from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import db
from ..config import settings
from . import repository as repo
from . import shift_continuity


def _scope(chat_id: int) -> int:
    return repo.resolve_scope_chat_id(chat_id)


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace('Z', '+00:00')
    try:
        value_dt = datetime.fromisoformat(text)
        if value_dt.tzinfo:
            value_dt = value_dt.astimezone().replace(tzinfo=None)
        return value_dt
    except Exception:
        return None


def _age_minutes(value: Any, now: datetime | None = None) -> int:
    started = _dt(value)
    if not started:
        return 0
    now = now or datetime.now()
    return max(0, int((now - started).total_seconds() // 60))


def get_sla_settings(chat_id: int) -> dict[str, Any]:
    scope = _scope(chat_id)
    row = db.fetchone('SELECT * FROM control_sla_settings WHERE chat_id=?', (scope,))
    if row:
        return dict(row)
    return {
        'chat_id': scope,
        'package_sla_minutes': 120,
        'handover_sla_minutes': 60,
        'critical_alert_sla_minutes': 30,
    }


def save_sla_settings(chat_id: int, actor_user_id: int, values: dict[str, Any]) -> dict[str, Any]:
    scope = _scope(chat_id)
    if not repo.is_system_admin_id(actor_user_id):
        raise PermissionError('Настраивать SLA может только владелец или полный администратор.')
    package = max(5, min(int(values.get('package_sla_minutes') or 120), 10080))
    handover = max(5, min(int(values.get('handover_sla_minutes') or 60), 10080))
    critical = max(5, min(int(values.get('critical_alert_sla_minutes') or 30), 10080))
    db.execute(
        '''INSERT INTO control_sla_settings(chat_id,package_sla_minutes,handover_sla_minutes,critical_alert_sla_minutes,updated_by,updated_at)
           VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(chat_id) DO UPDATE SET package_sla_minutes=excluded.package_sla_minutes,
             handover_sla_minutes=excluded.handover_sla_minutes,critical_alert_sla_minutes=excluded.critical_alert_sla_minutes,
             updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP''',
        (scope, package, handover, critical, int(actor_user_id)),
    )
    return get_sla_settings(scope)


def record_decision(
    chat_id: int,
    actor_user_id: int,
    *,
    worker_user_id: int | None,
    target_type: str,
    target_id: int,
    action: str,
    reason: str,
    before_status: str = '',
    after_status: str = '',
    metadata: dict[str, Any] | None = None,
) -> int:
    scope = _scope(chat_id)
    with db.connect() as conn:
        cur = conn.execute(
            '''INSERT INTO supervisor_decisions(chat_id,actor_user_id,worker_user_id,target_type,target_id,action,reason,before_status,after_status,metadata_json)
               VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (
                scope, int(actor_user_id), int(worker_user_id) if worker_user_id else None,
                str(target_type)[:80], int(target_id), str(action)[:40], str(reason or '')[:2000],
                str(before_status or '')[:80], str(after_status or '')[:80],
                json.dumps(metadata or {}, ensure_ascii=False, separators=(',', ':')),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_decisions(chat_id: int, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    rows = [dict(r) for r in db.fetchall(
        '''SELECT * FROM supervisor_decisions WHERE chat_id=? ORDER BY id DESC LIMIT ?''',
        (scope, max(1, min(int(limit), 500))),
    )]
    if repo.is_system_admin_id(user_id):
        return rows
    if not repo.user_can_manage_departments(scope, user_id):
        return []
    result = []
    for row in rows:
        worker = int(row.get('worker_user_id') or 0)
        if worker and shift_continuity.can_review_worker_packages(scope, user_id, worker):
            result.append(row)
        elif int(row.get('actor_user_id') or 0) == int(user_id):
            result.append(row)
    return result


def _managed_worker_ids(chat_id: int, user_id: int) -> set[int] | None:
    scope = _scope(chat_id)
    if repo.is_system_admin_id(user_id):
        return None
    rows = db.fetchall(
        '''SELECT DISTINCT worker.user_id
           FROM department_members head
           JOIN departments d ON d.id=head.department_id AND d.chat_id=? AND d.is_archived=0
           JOIN department_members worker ON worker.department_id=d.id AND worker.is_active=1
           WHERE head.user_id=? AND head.is_active=1 AND head.role_level>=50''',
        (scope, int(user_id)),
    )
    return {int(r['user_id']) for r in rows}


def workspace_profile(chat_id: int, user_id: int) -> dict[str, Any]:
    scope = _scope(chat_id)
    memberships = repo.user_department_memberships(scope, user_id)
    if repo.is_system_admin_id(user_id):
        return {
            'role': 'admin', 'label': 'Полный контроль', 'home_tab': 'control',
            'primary_tabs': ['control', 'work', 'overview', 'risks', 'shifts', 'inbox', 'departments', 'reports', 'security'],
        }
    if any(int(x.get('role_level') or 0) >= 50 for x in memberships):
        return {
            'role': 'manager', 'label': 'Руководитель отдела', 'home_tab': 'control',
            'primary_tabs': ['control', 'work', 'shifts', 'inbox', 'risks', 'departments'],
        }
    return {
        'role': 'worker', 'label': 'Рабочий режим', 'home_tab': 'work',
        'primary_tabs': ['work', 'shifts', 'inbox', 'risks'],
    }


def control_summary(chat_id: int, user_id: int, now: datetime | None = None) -> dict[str, Any]:
    scope = _scope(chat_id)
    now = now or datetime.now()
    settings_row = get_sla_settings(scope)
    managed = _managed_worker_ids(scope, user_id)
    if managed is not None and not managed:
        return {'sla': settings_row, 'counts': {}, 'open_shifts': [], 'packages': [], 'handovers': [], 'critical_alerts': [], 'decisions': []}

    shifts = [dict(r) for r in db.fetchall(
        '''SELECT ws.*,a.name AS area_name,
                  COALESCE(NULLIF(w.display_name,''),NULLIF(dm.display_name,''),CAST(ws.user_id AS TEXT)) AS worker_name
           FROM worker_shifts ws
           LEFT JOIN areas a ON a.id=ws.area_id
           LEFT JOIN workers w ON w.chat_id=ws.chat_id AND w.user_id=ws.user_id AND w.is_active=1
           LEFT JOIN department_members dm ON dm.user_id=ws.user_id AND dm.is_active=1
           LEFT JOIN departments d ON d.id=dm.department_id AND d.chat_id=ws.chat_id AND d.is_archived=0
           WHERE ws.chat_id=? AND ws.status='open'
           GROUP BY ws.id ORDER BY ws.started_at''', (scope,)
    )]
    if managed is not None:
        shifts = [x for x in shifts if int(x.get('user_id') or 0) in managed]

    packages = shift_continuity.list_shift_packages(scope, unresolved_only=True, limit=200)
    if managed is not None:
        packages = [x for x in packages if int(x.get('user_id') or 0) in managed]
    package_sla = int(settings_row['package_sla_minutes'])
    package_rows = []
    for p in packages:
        age = _age_minutes(p.get('submitted_at') or p.get('created_at'), now)
        package_rows.append({
            'id': int(p['id']), 'worker_user_id': int(p.get('user_id') or 0), 'worker_name': p.get('worker_name') or str(p.get('user_id')),
            'area_name': p.get('area_name') or '', 'status': p.get('status') or '', 'age_minutes': age,
            'sla_minutes': package_sla, 'overdue': age > package_sla, 'overdue_minutes': max(0, age-package_sla),
            'review_count': int(p.get('review_count') or 0), 'error_count': int(p.get('error_count') or 0)+int(p.get('rejected_count') or 0),
        })

    can_manage = repo.is_system_admin_id(user_id) or repo.user_can_manage_departments(scope, user_id)
    handovers = shift_continuity.list_handovers(scope, user_id, can_manage=can_manage, limit=200)
    if managed is not None:
        handovers = [x for x in handovers if int(x.get('from_user_id') or 0) in managed or int(x.get('to_user_id') or 0) in managed]
    handover_sla = int(settings_row['handover_sla_minutes'])
    handover_rows = []
    for h in handovers:
        if str(h.get('status')) != 'open':
            continue
        age = _age_minutes(h.get('created_at'), now)
        handover_rows.append({
            'id': int(h['id']), 'from_user_id': int(h.get('from_user_id') or 0), 'to_user_id': int(h.get('to_user_id') or 0),
            'from_name': h.get('from_name') or str(h.get('from_user_id')), 'to_name': h.get('to_name') or '',
            'area_name': h.get('area_name') or '', 'age_minutes': age, 'sla_minutes': handover_sla,
            'overdue': age > handover_sla, 'overdue_minutes': max(0, age-handover_sla),
            'unfinished_count': int(h.get('unfinished_count') or 0), 'issue_count': int(h.get('issue_count') or 0),
        })

    incident_rows = [dict(r) for r in db.fetchall(
        '''SELECT i.*,r.entity_id,r.entity_type,r.name AS rule_name,e.name AS entity_name,a.name AS area_name
           FROM stock_alert_incidents i JOIN stock_alert_rules r ON r.id=i.rule_id
           LEFT JOIN entities e ON e.id=r.entity_id LEFT JOIN areas a ON a.id=r.area_id
           WHERE i.chat_id=? AND i.status='open' AND i.severity IN ('critical','emergency')
           ORDER BY CASE i.severity WHEN 'emergency' THEN 0 ELSE 1 END,i.first_seen_at''', (scope,)
    )]
    if managed is not None:
        visible = repo.visible_entity_ids_for_user(scope, user_id) or set()
        visible = {int(x) for x in visible}
        incident_rows = [x for x in incident_rows if int(x.get('entity_id') or 0) in visible]
    alert_sla = int(settings_row['critical_alert_sla_minutes'])
    alerts = []
    for i in incident_rows:
        age = _age_minutes(i.get('first_seen_at'), now)
        alerts.append({
            'id': int(i['id']), 'severity': i.get('severity') or '', 'entity_name': i.get('entity_name') or i.get('rule_name') or '',
            'area_name': i.get('area_name') or '', 'message': i.get('message') or '', 'age_minutes': age,
            'sla_minutes': alert_sla, 'overdue': age > alert_sla, 'overdue_minutes': max(0, age-alert_sla),
        })

    overdue = sum(1 for x in package_rows+handover_rows+alerts if x.get('overdue'))
    return {
        'sla': settings_row,
        'counts': {
            'open_shifts': len(shifts), 'packages_waiting': len(package_rows), 'handovers_waiting': len(handover_rows),
            'critical_alerts': len(alerts), 'overdue': overdue,
        },
        'open_shifts': shifts[:80], 'packages': package_rows[:80], 'handovers': handover_rows[:80],
        'critical_alerts': alerts[:80], 'decisions': list_decisions(scope, user_id, 40),
    }


def _breach_recipients(scope: int, target_type: str, row: dict[str, Any]) -> set[int]:
    recipients = set(repo.list_system_admin_ids())
    worker = 0
    if target_type == 'package':
        worker = int(row.get('user_id') or 0)
    elif target_type == 'handover':
        worker = int(row.get('from_user_id') or 0)
    if worker:
        recipients.update(shift_continuity.package_review_recipient_ids(scope, worker))
    return {x for x in recipients if x > 0}


def queue_sla_breach_notifications(now: datetime | None = None) -> int:
    now = now or datetime.now()
    created = 0
    scopes = {int(r['chat_id']) for r in db.fetchall('SELECT chat_id FROM control_sla_settings UNION SELECT DISTINCT chat_id FROM shift_sync_packages UNION SELECT DISTINCT chat_id FROM shift_handovers UNION SELECT DISTINCT chat_id FROM stock_alert_incidents')}
    for scope in scopes:
        cfg = get_sla_settings(scope)
        targets: list[tuple[str, dict[str, Any], int, str]] = []
        for r in db.fetchall("SELECT * FROM shift_sync_packages WHERE chat_id=? AND status IN ('received','review','partial','rejected')", (scope,)):
            row = dict(r); targets.append(('package', row, int(cfg['package_sla_minutes']), str(row.get('submitted_at') or row.get('created_at'))))
        for r in db.fetchall("SELECT * FROM shift_handovers WHERE chat_id=? AND status='open'", (scope,)):
            row = dict(r); targets.append(('handover', row, int(cfg['handover_sla_minutes']), str(row.get('created_at'))))
        for r in db.fetchall("SELECT * FROM stock_alert_incidents WHERE chat_id=? AND status='open' AND severity IN ('critical','emergency')", (scope,)):
            row = dict(r); targets.append(('critical_alert', row, int(cfg['critical_alert_sla_minutes']), str(row.get('first_seen_at'))))
        for kind, row, sla, started in targets:
            if _age_minutes(started, now) <= sla:
                continue
            target_id = int(row['id'])
            recipients = _breach_recipients(scope, kind, row) if kind != 'critical_alert' else set(repo.list_system_admin_ids())
            for uid in recipients:
                exists = db.fetchone('SELECT id FROM sla_breach_notifications WHERE chat_id=? AND target_type=? AND target_id=? AND recipient_user_id=?', (scope, kind, target_id, uid))
                if exists:
                    continue
                labels = {'package':'Пакет смены просрочен','handover':'Передача смены просрочена','critical_alert':'Критическая тревога без реакции'}
                item_id = repo.create_inbox_item(scope, uid, 'sla_breach', labels[kind], f'Объект №{target_id} превысил допустимое время реакции {sla} мин.', kind, target_id, deduplicate=False, priority='urgent', force=True)
                try:
                    db.execute('INSERT INTO sla_breach_notifications(chat_id,target_type,target_id,recipient_user_id,inbox_item_id) VALUES(?,?,?,?,?)', (scope, kind, target_id, uid, item_id or None))
                    created += 1
                except Exception:
                    pass
    return created


def heartbeat(service_key: str, status: str = 'ok', details: str = '') -> None:
    try:
        db.execute(
            '''INSERT INTO system_heartbeats(service_key,status,details,last_seen_at) VALUES(?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(service_key) DO UPDATE SET status=excluded.status,details=excluded.details,last_seen_at=CURRENT_TIMESTAMP''',
            (str(service_key)[:80], str(status)[:30], str(details)[:1000]),
        )
    except Exception:
        pass


def diagnostics_snapshot(chat_id: int, user_id: int) -> dict[str, Any]:
    scope = _scope(chat_id)
    if not repo.is_system_admin_id(user_id):
        raise PermissionError('Диагностика доступна только владельцу или полному администратору.')
    db_status = 'ok'
    db_message = 'ok'
    try:
        with db.connect() as conn:
            row = conn.execute('PRAGMA quick_check').fetchone()
            db_message = str(row[0] if row else 'unknown')
            if db_message.lower() != 'ok':
                db_status = 'error'
    except Exception as exc:
        db_status, db_message = 'error', str(exc)
    data_dir = Path(settings.data_dir)
    usage = shutil.disk_usage(data_dir)
    db_path = Path(settings.database_path)
    hb = {str(r['service_key']): dict(r) for r in db.fetchall('SELECT * FROM system_heartbeats ORDER BY service_key')}
    pending_telegram = db.fetchone("SELECT COUNT(*) AS n FROM inbox_items WHERE telegram_status IN ('queued','error')")
    report_errors = db.fetchone("SELECT COUNT(*) AS n FROM report_delivery_history WHERE status='error'")
    queued_reports = db.fetchone("SELECT COUNT(*) AS n FROM report_delivery_history WHERE status='queued'")
    unresolved_packages = db.fetchone("SELECT COUNT(*) AS n FROM shift_sync_packages WHERE chat_id=? AND status IN ('received','review','partial','rejected')", (scope,))
    active_tasks = db.fetchone("SELECT COUNT(*) AS n FROM production_tasks WHERE chat_id=? AND status IN ('planned','in_progress','paused')", (scope,))
    open_requests = db.fetchone("SELECT COUNT(*) AS n FROM interdepartment_requests WHERE chat_id=? AND status IN ('requested','approved','issued','partially_received')", (scope,))
    open_downtimes = db.fetchone("SELECT COUNT(*) AS n FROM equipment_downtimes WHERE chat_id=? AND status='open'", (scope,))
    maintenance_due = db.fetchone("SELECT COUNT(*) AS n FROM equipment WHERE chat_id=? AND is_archived=0 AND next_service_at IS NOT NULL AND datetime(next_service_at)<=datetime('now','+3 day')", (scope,))
    quality_open = db.fetchone("SELECT COUNT(*) AS n FROM quality_inspections WHERE chat_id=? AND status IN ('open','waiting_rework','quarantined','rework')", (scope,))
    quarantine_lots = db.fetchone("SELECT COUNT(*) AS n FROM production_lots WHERE chat_id=? AND status IN ('quarantine','rejected')", (scope,))
    replenishment_open = db.fetchone("SELECT COUNT(*) AS n FROM replenishment_requests WHERE chat_id=? AND status IN ('requested','approved','ordered','partial')", (scope,))
    maintenance_work_overdue = db.fetchone("SELECT COUNT(*) AS n FROM maintenance_work_orders WHERE chat_id=? AND status IN ('planned','in_progress') AND datetime(due_at)<datetime('now')", (scope,))
    reliability_pending = db.fetchone("SELECT COUNT(*) AS n FROM reliability_journal WHERE chat_id=? AND status IN ('pending','error')", (scope,))
    backups_dir = data_dir / 'backups'
    backups = sorted((p for p in backups_dir.glob('*') if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True) if backups_dir.exists() else []
    latest_backup = None
    if backups:
        b = backups[0]
        latest_backup = {'name': b.name, 'size': b.stat().st_size, 'modified_at': datetime.fromtimestamp(b.stat().st_mtime).isoformat(timespec='seconds')}
    db_files = db.database_file_state()
    device_rows = [dict(r) for r in db.fetchall(
        "SELECT * FROM miniapp_devices WHERE last_chat_id=? OR user_id=? ORDER BY last_seen_at DESC LIMIT 200",
        (scope, int(settings.primary_owner_id)),
    )]
    now_dt = datetime.now()
    sync_devices = {"total": len(device_rows), "recent": 0, "stale": 0, "with_pending": 0, "pending_total": 0, "with_errors": 0}
    for item in device_rows:
        age = _age_minutes(item.get('last_seen_at'), now_dt)
        if age <= 5: sync_devices["recent"] += 1
        else: sync_devices["stale"] += 1
        pending = int(item.get('pending_queue_count') or 0)
        sync_devices["pending_total"] += pending
        if pending: sync_devices["with_pending"] += 1
        if str(item.get('last_sync_error') or '').strip(): sync_devices["with_errors"] += 1

    errors = [dict(r) for r in db.fetchall(
        '''SELECT 'telegram' AS source,id AS ref_id,telegram_error AS message,created_at FROM inbox_items WHERE telegram_status='error' AND telegram_error<>''
           UNION ALL SELECT 'report' AS source,id AS ref_id,error AS message,created_at FROM report_delivery_history WHERE status='error' AND error<>''
           ORDER BY created_at DESC LIMIT 12'''
    )]
    def hb_view(key: str) -> dict[str, Any]:
        item = hb.get(key) or {}
        age = _age_minutes(item.get('last_seen_at')) if item else 999999
        return {'status': item.get('status') or 'unknown', 'last_seen_at': item.get('last_seen_at'), 'age_minutes': age, 'stale': age > 3, 'details': item.get('details') or ''}
    return {
        'database': {'status': db_status, 'message': db_message, 'path': str(db_path), 'size_bytes': db_path.stat().st_size if db_path.exists() else 0, **db_files},
        'services': {'bot': hb_view('bot'), 'scheduler': hb_view('scheduler'), 'miniapp': hb_view('miniapp'), 'runtime': hb_view('runtime'), 'watchdog': hb_view('watchdog')},
        'disk': {'total_bytes': usage.total, 'used_bytes': usage.used, 'free_bytes': usage.free, 'free_percent': round(usage.free/max(1,usage.total)*100, 1)},
        'queues': {'telegram_pending': int(pending_telegram['n'] if pending_telegram else 0), 'report_queued': int(queued_reports['n'] if queued_reports else 0), 'report_errors': int(report_errors['n'] if report_errors else 0), 'shift_packages_unresolved': int(unresolved_packages['n'] if unresolved_packages else 0)},
        'workflow': {'active_tasks': int(active_tasks['n'] if active_tasks else 0), 'open_requests': int(open_requests['n'] if open_requests else 0), 'open_downtimes': int(open_downtimes['n'] if open_downtimes else 0), 'maintenance_due_soon': int(maintenance_due['n'] if maintenance_due else 0),
                     'quality_open': int(quality_open['n'] if quality_open else 0), 'quarantine_lots': int(quarantine_lots['n'] if quarantine_lots else 0),
                     'replenishment_open': int(replenishment_open['n'] if replenishment_open else 0), 'maintenance_work_overdue': int(maintenance_work_overdue['n'] if maintenance_work_overdue else 0),
                     'reliability_pending': int(reliability_pending['n'] if reliability_pending else 0)},
        'backup': {'count': len(backups), 'latest': latest_backup}, 'device_sync': sync_devices, 'recent_errors': errors,
        'checked_at': datetime.now().isoformat(timespec='seconds'),
    }
