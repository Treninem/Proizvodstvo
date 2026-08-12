from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .. import db
from . import repository as repo

INSPECTION_TYPES = {"incoming", "in_process", "output", "recheck"}
INSPECTION_STATUSES = {"open", "waiting_rework", "passed", "quarantined", "rework", "written_off", "cancelled", "reworked_passed"}
DECISIONS = {"pass", "quarantine", "rework", "write_off", "cancel"}
DEFECT_SEVERITIES = {"minor", "major", "critical"}


def _scope(chat_id: int) -> int:
    return repo.resolve_scope_chat_id(int(chat_id))


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _entity(scope: int, entity_id: int) -> dict[str, Any] | None:
    row = db.fetchone("SELECT * FROM entities WHERE chat_id=? AND id=? AND is_archived=0", (scope, int(entity_id)))
    return dict(row) if row else None


def _member_level(department_id: int | None, user_id: int) -> int:
    if not department_id:
        return 0
    return int(repo.department_actor_level(int(department_id), int(user_id)) or 0)


def _visible_entity(scope: int, user_id: int, entity_id: int) -> bool:
    if repo.is_tenant_admin(scope, user_id):
        return True
    visible = repo.visible_entity_ids_for_user(scope, user_id)
    return visible is None or int(entity_id) in {int(x) for x in visible}


def _can_manage_department(scope: int, user_id: int, department_id: int | None) -> bool:
    if repo.is_tenant_admin(scope, user_id):
        return True
    if not department_id:
        return False
    row = db.fetchone("SELECT id FROM departments WHERE id=? AND chat_id=? AND is_archived=0", (int(department_id), scope))
    return bool(row and _member_level(department_id, user_id) >= 50)


def _manager_ids(scope: int, department_id: int | None) -> list[int]:
    ids = set(repo.tenant_admin_user_ids(scope))
    if department_id:
        rows = db.fetchall(
            """
            SELECT dm.user_id FROM department_members dm
            JOIN departments d ON d.id=dm.department_id
            WHERE d.chat_id=? AND dm.department_id=? AND dm.is_active=1 AND dm.role_level>=50
            """,
            (scope, int(department_id)),
        )
        ids.update(int(r["user_id"]) for r in rows)
    return sorted(ids)


def _notify(scope: int, recipients: list[int], title: str, message: str, inspection_id: int, *, priority: str = "high") -> None:
    for uid in sorted(set(int(x) for x in recipients if x)):
        repo.create_inbox_item(
            scope, uid, "quality", title, message, "quality_inspection", int(inspection_id),
            deduplicate=False, priority=priority,
        )


def _task(scope: int, task_id: int | None) -> dict[str, Any] | None:
    if not task_id:
        return None
    row = db.fetchone("SELECT * FROM production_tasks WHERE chat_id=? AND id=?", (scope, int(task_id)))
    return dict(row) if row else None


def _lot(scope: int, lot_id: int | None) -> dict[str, Any] | None:
    if not lot_id:
        return None
    row = db.fetchone("SELECT * FROM production_lots WHERE chat_id=? AND id=?", (scope, int(lot_id)))
    return dict(row) if row else None


def _equipment(scope: int, equipment_id: int | None) -> dict[str, Any] | None:
    if not equipment_id:
        return None
    row = db.fetchone("SELECT * FROM equipment WHERE chat_id=? AND id=? AND is_archived=0", (scope, int(equipment_id)))
    return dict(row) if row else None


def _matching_rule(scope: int, entity_id: int, department_id: int | None, operation_type: str | None, inspection_type: str) -> dict[str, Any] | None:
    rows = db.fetchall(
        """
        SELECT * FROM quality_rules
        WHERE chat_id=? AND entity_id=? AND is_enabled=1 AND inspection_type=?
          AND (department_id IS NULL OR department_id=?)
          AND (operation_type=? OR operation_type='*')
        ORDER BY CASE WHEN department_id IS NULL THEN 1 ELSE 0 END,id DESC
        """,
        (scope, int(entity_id), inspection_type, department_id, str(operation_type or "production")),
    )
    return dict(rows[0]) if rows else None


def save_rule(chat_id: int, actor_user_id: int, values: dict[str, Any]) -> dict[str, Any]:
    scope = _scope(chat_id)
    entity_id = int(values.get("entity_id") or 0)
    entity = _entity(scope, entity_id)
    if not entity:
        raise ValueError("Позиция не найдена.")
    department_id = int(values["department_id"]) if values.get("department_id") else None
    if not repo.is_tenant_admin(scope, actor_user_id) and not _can_manage_department(scope, actor_user_id, department_id):
        raise PermissionError("Настраивать контроль качества может владелец, администратор или руководитель своего отдела.")
    if not _visible_entity(scope, actor_user_id, entity_id):
        raise PermissionError("Позиция недоступна вашему рабочему контуру.")
    inspection_type = str(values.get("inspection_type") or "output")
    if inspection_type not in INSPECTION_TYPES:
        raise ValueError("Неизвестный вид контроля.")
    operation_type = str(values.get("operation_type") or "production")[:80]
    rework_department_id = int(values["rework_department_id"]) if values.get("rework_department_id") else None
    if rework_department_id:
        dep = db.fetchone("SELECT id FROM departments WHERE id=? AND chat_id=? AND is_archived=0", (rework_department_id, scope))
        if not dep:
            raise ValueError("Отдел доработки не найден.")
        if not repo.is_tenant_admin(scope, actor_user_id) and not _can_manage_department(scope, actor_user_id, rework_department_id):
            raise PermissionError("Нельзя назначить автоматическую доработку в отдел, которым вы не управляете.")
    payload = (
        scope, department_id, entity_id, operation_type, inspection_type,
        int(bool(values.get("is_enabled", True))), max(0.0, float(values.get("sample_quantity") or 0)),
        max(0.0, min(float(values.get("max_defect_percent") or 0), 100.0)),
        int(bool(values.get("require_before_task_complete", False))), int(bool(values.get("auto_quarantine_on_fail", True))),
        int(bool(values.get("create_rework_task", True))), rework_department_id,
        str(values.get("rework_operation_type") or "")[:80], int(actor_user_id),
    )
    rule_id = int(values.get("rule_id") or 0)
    with db.connect() as conn:
        if rule_id:
            row = conn.execute("SELECT * FROM quality_rules WHERE id=? AND chat_id=?", (rule_id, scope)).fetchone()
            if not row:
                raise ValueError("Правило контроля не найдено.")
            conn.execute(
                """
                UPDATE quality_rules SET department_id=?,entity_id=?,operation_type=?,inspection_type=?,is_enabled=?,sample_quantity=?,
                    max_defect_percent=?,require_before_task_complete=?,auto_quarantine_on_fail=?,create_rework_task=?,
                    rework_department_id=?,rework_operation_type=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND chat_id=?
                """,
                payload[1:13] + (rule_id, scope),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO quality_rules(chat_id,department_id,entity_id,operation_type,inspection_type,is_enabled,sample_quantity,
                    max_defect_percent,require_before_task_complete,auto_quarantine_on_fail,create_rework_task,rework_department_id,
                    rework_operation_type,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                payload,
            )
            rule_id = int(cur.lastrowid)
        conn.commit()
    return dict(db.fetchone("SELECT * FROM quality_rules WHERE id=?", (rule_id,)))


def list_rules(chat_id: int, user_id: int) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    rows = db.fetchall(
        """
        SELECT q.*,e.name AS entity_name,d.name AS department_name,rd.name AS rework_department_name
        FROM quality_rules q JOIN entities e ON e.id=q.entity_id AND e.chat_id=q.chat_id
        LEFT JOIN departments d ON d.id=q.department_id AND d.chat_id=q.chat_id LEFT JOIN departments rd ON rd.id=q.rework_department_id AND rd.chat_id=q.chat_id
        WHERE q.chat_id=? ORDER BY q.is_enabled DESC,e.name,q.id DESC
        """,
        (scope,),
    )
    result = []
    for row in rows:
        item = dict(row)
        if repo.is_tenant_admin(scope, user_id) or (not item.get("department_id") and _visible_entity(scope, user_id, int(item["entity_id"]))) or _member_level(item.get("department_id"), user_id) >= 10:
            item["can_manage"] = bool(repo.is_tenant_admin(scope, user_id) or _can_manage_department(scope, user_id, item.get("department_id")))
            result.append(item)
    return result


def _inspection_row(scope: int, inspection_id: int) -> dict[str, Any] | None:
    row = db.fetchone(
        """
        SELECT qi.*,e.name AS entity_name,d.name AS department_name,a.name AS area_name,l.lot_code,
               eq.name AS equipment_name,t.title AS task_title,t.status AS task_status
        FROM quality_inspections qi JOIN entities e ON e.id=qi.entity_id
        LEFT JOIN departments d ON d.id=qi.department_id LEFT JOIN areas a ON a.id=qi.area_id
        LEFT JOIN production_lots l ON l.id=qi.lot_id LEFT JOIN equipment eq ON eq.id=qi.equipment_id
        LEFT JOIN production_tasks t ON t.id=qi.task_id
        WHERE qi.chat_id=? AND qi.id=?
        """,
        (scope, int(inspection_id)),
    )
    return dict(row) if row else None


def _can_view(scope: int, user_id: int, item: dict[str, Any]) -> bool:
    if repo.is_tenant_admin(scope, user_id):
        return True
    if int(item.get("inspector_user_id") or 0) == int(user_id) or int(item.get("worker_user_id") or 0) == int(user_id):
        return True
    dep = item.get("department_id")
    return bool(dep and _member_level(int(dep), user_id) >= 10)


def _can_decide(scope: int, user_id: int, item: dict[str, Any]) -> bool:
    return bool(repo.is_tenant_admin(scope, user_id) or _can_manage_department(scope, user_id, item.get("department_id")))


def _decorate(scope: int, user_id: int, item: dict[str, Any]) -> dict[str, Any]:
    item["can_decide"] = _can_decide(scope, user_id, item)
    item["defects"] = [dict(r) for r in db.fetchall("SELECT * FROM quality_defects WHERE inspection_id=? ORDER BY id", (int(item["id"]),))]
    item["actions"] = [dict(r) for r in db.fetchall("SELECT * FROM quality_actions WHERE inspection_id=? ORDER BY id DESC LIMIT 30", (int(item["id"]),))]
    return item


def get_inspection(chat_id: int, inspection_id: int, user_id: int) -> dict[str, Any] | None:
    scope = _scope(chat_id)
    item = _inspection_row(scope, inspection_id)
    if not item or not _can_view(scope, user_id, item):
        return None
    return _decorate(scope, user_id, item)


def list_inspections(chat_id: int, user_id: int, *, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    params: list[Any] = [scope]
    where = ["qi.chat_id=?"]
    if status:
        where.append("qi.status=?")
        params.append(str(status))
    params.append(max(1, min(int(limit), 500)))
    rows = db.fetchall(
        f"""
        SELECT qi.*,e.name AS entity_name,d.name AS department_name,a.name AS area_name,l.lot_code,
               eq.name AS equipment_name,t.title AS task_title,t.status AS task_status
        FROM quality_inspections qi JOIN entities e ON e.id=qi.entity_id
        LEFT JOIN departments d ON d.id=qi.department_id LEFT JOIN areas a ON a.id=qi.area_id
        LEFT JOIN production_lots l ON l.id=qi.lot_id LEFT JOIN equipment eq ON eq.id=qi.equipment_id
        LEFT JOIN production_tasks t ON t.id=qi.task_id
        WHERE {' AND '.join(where)}
        ORDER BY CASE qi.status WHEN 'open' THEN 0 WHEN 'waiting_rework' THEN 1 WHEN 'quarantined' THEN 2 ELSE 3 END,qi.id DESC
        LIMIT ?
        """,
        tuple(params),
    )
    result = []
    for row in rows:
        item = dict(row)
        if _can_view(scope, user_id, item):
            result.append(_decorate(scope, user_id, item))
    return result


def _quarantine_quantity(scope: int, lot_id: int, area_id: int | None, fallback: float) -> float:
    if area_id is not None:
        row = db.fetchone("SELECT COALESCE(SUM(CASE WHEN quantity>0 THEN quantity ELSE 0 END),0) AS q FROM lot_inventory WHERE lot_id=? AND area_id=?", (int(lot_id), int(area_id)))
    else:
        row = db.fetchone("SELECT COALESCE(SUM(CASE WHEN quantity>0 THEN quantity ELSE 0 END),0) AS q FROM lot_inventory WHERE lot_id=?", (int(lot_id),))
    return max(float(row["q"] or 0) if row else 0.0, max(0.0, float(fallback or 0)))


def _quarantine_lot(scope: int, inspection_id: int, lot_id: int, entity_id: int, area_id: int | None, actor_user_id: int, quantity: float, reason: str) -> int | None:
    lot = _lot(scope, lot_id)
    if not lot:
        return None
    db.execute("UPDATE production_lots SET status='quarantine',updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(lot_id),))
    existing = db.fetchone("SELECT operational_event_id FROM quality_inspections WHERE id=?", (int(inspection_id),))
    if existing and existing["operational_event_id"]:
        return int(existing["operational_event_id"])
    unavailable = _quarantine_quantity(scope, lot_id, area_id, quantity)
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO operational_events(chat_id,event_type,title,area_id,entity_id,severity,impact_kind,impact_value,unavailable_quantity,
                status,note,created_by)
            VALUES(?,?,?,?,?,'critical','unavailable_stock',0,?,'active',?,?)
            """,
            (scope, "quality_hold", f"Карантин партии {lot['lot_code']}", area_id, int(entity_id), unavailable, str(reason or "Контроль качества")[:1000], int(actor_user_id)),
        )
        event_id = int(cur.lastrowid)
        conn.execute("UPDATE quality_inspections SET operational_event_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (event_id, int(inspection_id)))
        conn.commit()
    return event_id


def _resolve_hold(inspection: dict[str, Any], actor_user_id: int) -> None:
    event_id = int(inspection.get("operational_event_id") or 0)
    if event_id:
        db.execute(
            "UPDATE operational_events SET status='resolved',resolved_by=?,resolved_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'",
            (int(actor_user_id), event_id),
        )


def create_inspection(
    chat_id: int,
    actor_user_id: int,
    entity_id: int,
    *,
    inspection_type: str = "output",
    department_id: int | None = None,
    area_id: int | None = None,
    lot_id: int | None = None,
    task_id: int | None = None,
    equipment_id: int | None = None,
    shift_plan_id: int | None = None,
    worker_user_id: int | None = None,
    checked_quantity: float = 0,
    defect_quantity: float = 0,
    unit: str = "шт",
    note: str = "",
    defects: list[dict[str, Any]] | None = None,
    parent_inspection_id: int | None = None,
    rework_task_id: int | None = None,
    initial_status: str = "open",
) -> dict[str, Any]:
    scope = _scope(chat_id)
    inspection_type = str(inspection_type or "output")
    if inspection_type not in INSPECTION_TYPES:
        raise ValueError("Неизвестный вид контроля.")
    entity = _entity(scope, entity_id)
    if not entity:
        raise ValueError("Позиция не найдена.")
    if not _visible_entity(scope, actor_user_id, entity_id):
        raise PermissionError("Эта позиция недоступна вашему рабочему контуру.")
    task = _task(scope, task_id)
    lot = _lot(scope, lot_id)
    eq = _equipment(scope, equipment_id)
    if task_id and not task:
        raise ValueError("Задание не найдено в этой организации.")
    if lot_id and not lot:
        raise ValueError("Партия не найдена в этой организации.")
    if equipment_id and not eq:
        raise ValueError("Оборудование не найдено в этой организации.")
    if shift_plan_id and not db.fetchone("SELECT id FROM shift_plans WHERE id=? AND chat_id=?", (int(shift_plan_id), scope)):
        raise ValueError("План смены не найден в этой организации.")
    if parent_inspection_id and not db.fetchone("SELECT id FROM quality_inspections WHERE id=? AND chat_id=?", (int(parent_inspection_id), scope)):
        raise ValueError("Родительский контроль не найден в этой организации.")
    if rework_task_id and not _task(scope, int(rework_task_id)):
        raise ValueError("Задание на доработку не найдено в этой организации.")
    if task:
        if int(task["entity_id"]) != int(entity_id):
            raise ValueError("Задание относится к другой позиции.")
        department_id = department_id or int(task["department_id"])
        area_id = area_id if area_id is not None else task.get("area_id")
        shift_plan_id = shift_plan_id or task.get("shift_plan_id")
        worker_user_id = worker_user_id or task.get("assignee_user_id")
    if lot and int(lot["entity_id"]) != int(entity_id):
        raise ValueError("Партия относится к другой позиции.")
    if eq:
        if department_id and eq.get("department_id") and int(eq["department_id"]) != int(department_id):
            raise ValueError("Оборудование относится к другому отделу.")
        department_id = department_id or eq.get("department_id")
        area_id = area_id if area_id is not None else eq.get("area_id")
    if department_id:
        dep = db.fetchone("SELECT id FROM departments WHERE id=? AND chat_id=? AND is_archived=0", (int(department_id), scope))
        if not dep:
            raise ValueError("Отдел не найден.")
        if not repo.is_tenant_admin(scope, actor_user_id) and _member_level(department_id, actor_user_id) < 20:
            raise PermissionError("Нет права проводить контроль в этом отделе.")
    elif not repo.is_tenant_admin(scope, actor_user_id):
        raise PermissionError("Для контроля без отдела нужен полный административный доступ.")
    if area_id is not None:
        area = db.fetchone("SELECT id FROM areas WHERE id=? AND chat_id=? AND is_archived=0", (int(area_id), scope))
        if not area:
            raise ValueError("Площадка не найдена.")
    checked = max(0.0, float(checked_quantity or 0))
    defective = max(0.0, float(defect_quantity or 0))
    if checked > 0 and defective > checked + 1e-9:
        raise ValueError("Количество несоответствий не может быть больше проверенного количества.")
    if initial_status not in {"open", "waiting_rework"}:
        initial_status = "open"
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO quality_inspections(chat_id,inspection_type,department_id,area_id,entity_id,lot_id,task_id,equipment_id,
                shift_plan_id,worker_user_id,inspector_user_id,checked_quantity,defect_quantity,unit,status,note,parent_inspection_id,rework_task_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (scope, inspection_type, department_id, area_id, int(entity_id), lot_id, task_id, equipment_id, shift_plan_id,
             worker_user_id, int(actor_user_id), checked, defective, str(unit or entity.get("default_unit") or "шт")[:30],
             initial_status, str(note or "")[:1000], parent_inspection_id, rework_task_id),
        )
        inspection_id = int(cur.lastrowid)
        for defect in defects or []:
            qty = max(0.0, float(defect.get("quantity") or 0))
            if qty <= 0 and not str(defect.get("reason") or "").strip() and not str(defect.get("category") or "").strip():
                continue
            severity = str(defect.get("severity") or "minor")
            if severity not in DEFECT_SEVERITIES:
                severity = "minor"
            conn.execute(
                """
                INSERT INTO quality_defects(inspection_id,defect_code,category,severity,quantity,reason,note)
                VALUES(?,?,?,?,?,?,?)
                """,
                (inspection_id, str(defect.get("defect_code") or "")[:80], str(defect.get("category") or "other")[:120], severity,
                 qty, str(defect.get("reason") or "")[:500], str(defect.get("note") or "")[:1000]),
            )
        if defective > 0 and not defects:
            conn.execute(
                "INSERT INTO quality_defects(inspection_id,category,severity,quantity,reason) VALUES(?,'other','major',?,?)",
                (inspection_id, defective, str(note or "Несоответствие")[:500]),
            )
        conn.commit()
    operation_type = str(task.get("operation_type") or "production") if task else "production"
    rule = _matching_rule(scope, int(entity_id), int(department_id) if department_id else None, operation_type, inspection_type)
    defect_percent = (defective / checked * 100.0) if checked > 0 else (100.0 if defective > 0 else 0.0)
    if lot_id and defective > 0 and rule and int(rule.get("auto_quarantine_on_fail") or 0) and defect_percent > float(rule.get("max_defect_percent") or 0):
        _quarantine_lot(scope, inspection_id, int(lot_id), int(entity_id), int(area_id) if area_id is not None else None, actor_user_id, defective, "Несоответствие по настроенному правилу качества")
    item = get_inspection(scope, inspection_id, actor_user_id) or {"id": inspection_id}
    _notify(scope, _manager_ids(scope, department_id), f"Контроль качества №{inspection_id}", f"{entity['name']}: проверено {checked:g}, несоответствий {defective:g} {unit}.", inspection_id, priority="urgent" if defective > 0 else "normal")
    return item


def _create_rework(scope: int, actor_user_id: int, inspection: dict[str, Any], reason: str) -> tuple[int | None, int | None]:
    from . import production_flow
    task = _task(scope, inspection.get("task_id"))
    operation_type = str(task.get("operation_type") or "production") if task else "production"
    rule = _matching_rule(scope, int(inspection["entity_id"]), inspection.get("department_id"), operation_type, str(inspection.get("inspection_type") or "output"))
    if rule and not int(rule.get("create_rework_task") or 0):
        return None, None
    department_id = int((rule or {}).get("rework_department_id") or inspection.get("department_id") or 0)
    if not department_id:
        return None, None
    rework_operation = str((rule or {}).get("rework_operation_type") or operation_type or "production")
    qty = float(inspection.get("defect_quantity") or inspection.get("checked_quantity") or 0)
    if qty <= 0:
        qty = 1.0
    rework_task = production_flow.create_task(
        scope, actor_user_id, department_id, int(inspection["entity_id"]), operation_type=rework_operation,
        target_quantity=qty, unit=str(inspection.get("unit") or "шт"),
        title=f"Доработка после контроля №{inspection['id']}", assignee_user_id=None,
        area_id=int(inspection["area_id"]) if inspection.get("area_id") is not None else None,
        priority="high", note=f"Причина: {reason}", output_lot_id=int(inspection["lot_id"]) if inspection.get("lot_id") else None,
    )
    recheck = create_inspection(
        scope, actor_user_id, int(inspection["entity_id"]), inspection_type="recheck",
        department_id=department_id, area_id=inspection.get("area_id"), lot_id=inspection.get("lot_id"),
        equipment_id=inspection.get("equipment_id"), worker_user_id=inspection.get("worker_user_id"),
        checked_quantity=qty, defect_quantity=0, unit=str(inspection.get("unit") or "шт"),
        note=f"Повторная проверка после доработки по контролю №{inspection['id']}",
        parent_inspection_id=int(inspection["id"]), rework_task_id=int(rework_task["id"]), initial_status="waiting_rework",
    )
    return int(rework_task["id"]), int(recheck["id"])


def decide_inspection(chat_id: int, actor_user_id: int, inspection_id: int, action: str, *, reason: str = "") -> dict[str, Any]:
    scope = _scope(chat_id)
    item = _inspection_row(scope, inspection_id)
    if not item:
        raise ValueError("Запись контроля не найдена.")
    if not _can_decide(scope, actor_user_id, item):
        raise PermissionError("Решение по контролю доступно руководителю соответствующего отдела или администратору.")
    action = str(action or "")
    if action not in DECISIONS:
        raise ValueError("Неизвестное решение.")
    if action in {"quarantine", "rework", "write_off", "cancel"} and not str(reason or "").strip():
        raise ValueError("Укажите причину решения.")
    if str(item.get("status") or "") == "waiting_rework" and action != "cancel":
        raise ValueError("Сначала завершите задание на доработку — после этого повторная проверка станет доступна.")
    generated_task_id = None
    generated_inspection_id = None
    write_off_operation_id = None
    target_status = {
        "pass": "passed", "quarantine": "quarantined", "rework": "rework", "write_off": "written_off", "cancel": "cancelled"
    }[action]
    if action == "quarantine" and item.get("lot_id"):
        _quarantine_lot(scope, inspection_id, int(item["lot_id"]), int(item["entity_id"]), item.get("area_id"), actor_user_id, float(item.get("defect_quantity") or item.get("checked_quantity") or 0), reason)
    elif action == "rework":
        if item.get("lot_id"):
            _quarantine_lot(scope, inspection_id, int(item["lot_id"]), int(item["entity_id"]), item.get("area_id"), actor_user_id, float(item.get("defect_quantity") or item.get("checked_quantity") or 0), reason)
        generated_task_id, generated_inspection_id = _create_rework(scope, actor_user_id, item, reason)
    elif action == "write_off":
        qty = float(item.get("defect_quantity") or item.get("checked_quantity") or 0)
        if qty <= 0:
            raise ValueError("Для списания нужно указать количество несоответствия/проверенное количество.")
        if item.get("area_id") is None:
            raise ValueError("Для списания укажите площадку/склад в записи контроля.")
        from . import accounting
        entity = _entity(scope, int(item["entity_id"]))
        write_off_operation_id = accounting.record_internal_operation(
            scope, scope, actor_user_id,
            {
                "operation_type": "write_off", "entity_type": str(entity["entity_type"]), "entity_id": int(item["entity_id"]),
                "quantity": qty, "unit": str(item.get("unit") or entity.get("default_unit") or "шт"), "area_id": int(item["area_id"]),
                "lot_id": int(item["lot_id"]) if item.get("lot_id") else None, "source_channel": "quality",
            },
            raw_text=f"Контроль качества №{inspection_id}: списание. {reason}",
        )
        if item.get("lot_id"):
            db.execute("UPDATE production_lots SET status='rejected',updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(item["lot_id"]),))
            _resolve_hold(item, actor_user_id)
    elif action == "pass":
        # При успешной повторной проверке сначала закрываем родительское несоответствие.
        # Иначе оно само попадает в список открытых блокировок и партия остаётся в карантине.
        parent = None
        if item.get("parent_inspection_id"):
            parent = _inspection_row(scope, int(item["parent_inspection_id"]))
            if parent:
                db.execute(
                    "UPDATE quality_inspections SET status='reworked_passed',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (int(parent["id"]),),
                )
                _resolve_hold(parent, actor_user_id)
        if item.get("lot_id"):
            other = db.fetchone(
                """
                SELECT id FROM quality_inspections
                WHERE chat_id=? AND lot_id=? AND id<>? AND status IN ('open','waiting_rework','quarantined','rework')
                LIMIT 1
                """,
                (scope, int(item["lot_id"]), int(inspection_id)),
            )
            if not other:
                db.execute(
                    "UPDATE production_lots SET status='active',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='quarantine'",
                    (int(item["lot_id"]),),
                )
        _resolve_hold(item, actor_user_id)
    with db.connect() as conn:
        conn.execute(
            "UPDATE quality_inspections SET status=?,decision_reason=?,decided_by=?,decided_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (target_status, str(reason or "")[:1000], int(actor_user_id), int(inspection_id)),
        )
        conn.execute(
            """
            INSERT INTO quality_actions(inspection_id,actor_user_id,action,reason,generated_task_id,generated_inspection_id,write_off_operation_id)
            VALUES(?,?,?,?,?,?,?)
            """,
            (int(inspection_id), int(actor_user_id), action, str(reason or "")[:1000], generated_task_id, generated_inspection_id, write_off_operation_id),
        )
        conn.commit()
    updated = get_inspection(scope, inspection_id, actor_user_id) or {"id": inspection_id, "status": target_status}
    recipients = _manager_ids(scope, item.get("department_id")) + [int(item.get("inspector_user_id") or 0), int(item.get("worker_user_id") or 0)]
    _notify(scope, recipients, f"Решение по контролю №{inspection_id}", f"Результат: {target_status}. {reason}", inspection_id, priority="urgent" if action in {"quarantine","write_off"} else "high")
    return updated


def validate_task_completion(chat_id: int, task_id: int) -> None:
    scope = _scope(chat_id)
    task = _task(scope, task_id)
    if not task:
        return
    rules = db.fetchall(
        """
        SELECT * FROM quality_rules
        WHERE chat_id=? AND is_enabled=1 AND entity_id=? AND require_before_task_complete=1
          AND (department_id IS NULL OR department_id=?) AND (operation_type=? OR operation_type='*')
        """,
        (scope, int(task["entity_id"]), int(task["department_id"]), str(task.get("operation_type") or "production")),
    )
    if not rules:
        return
    lot_id = int(task.get("output_lot_id") or 0)
    if lot_id:
        lot = _lot(scope, lot_id)
        if lot and str(lot.get("status") or "") in {"quarantine", "rejected"}:
            raise ValueError("Партия задания заблокирована контролем качества. Завершение задания недоступно до решения.")
    passed = db.fetchone(
        """
        SELECT id FROM quality_inspections
        WHERE chat_id=? AND task_id=? AND status IN ('passed','reworked_passed')
        ORDER BY id DESC LIMIT 1
        """,
        (scope, int(task_id)),
    )
    if not passed:
        raise ValueError("Для этого задания настроен обязательный контроль качества. Сначала проведите и подтвердите проверку.")


def activate_rechecks_for_task(chat_id: int, task_id: int) -> int:
    scope = _scope(chat_id)
    rows = db.fetchall("SELECT id,department_id,entity_id FROM quality_inspections WHERE chat_id=? AND rework_task_id=? AND status='waiting_rework'", (scope, int(task_id)))
    count = 0
    for row in rows:
        db.execute("UPDATE quality_inspections SET status='open',updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(row["id"]),))
        entity = _entity(scope, int(row["entity_id"]))
        _notify(scope, _manager_ids(scope, row["department_id"]), f"Нужна повторная проверка №{row['id']}", f"Доработка завершена. Проверьте {entity['name'] if entity else 'позицию'}.", int(row["id"]), priority="high")
        count += 1
    return count


def quality_snapshot(chat_id: int, user_id: int) -> dict[str, Any]:
    scope = _scope(chat_id)
    inspections = list_inspections(scope, user_id, limit=150)
    rules = list_rules(scope, user_id)
    visible = repo.visible_entity_ids_for_user(scope, user_id)
    quarantine_rows = db.fetchall(
        """
        SELECT l.id,l.lot_code,l.entity_id,e.name AS entity_name,l.status,COALESCE(SUM(li.quantity),0) AS quantity
        FROM production_lots l JOIN entities e ON e.id=l.entity_id LEFT JOIN lot_inventory li ON li.lot_id=l.id
        WHERE l.chat_id=? AND l.status IN ('quarantine','rejected')
        GROUP BY l.id ORDER BY l.id DESC
        """,
        (scope,),
    )
    quarantined = [dict(r) for r in quarantine_rows if visible is None or int(r["entity_id"]) in {int(x) for x in visible}]
    return {
        "inspections": inspections,
        "rules": rules,
        "quarantined_lots": quarantined,
        "counts": {
            "open": sum(1 for x in inspections if x.get("status") in {"open","waiting_rework"}),
            "quarantine": len(quarantined),
            "rework": sum(1 for x in inspections if x.get("status") == "rework"),
        },
    }
