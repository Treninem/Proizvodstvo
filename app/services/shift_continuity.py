from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from .. import db
from . import repository as repo


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def current_open_shift(chat_id: int, user_id: int) -> dict[str, Any] | None:
    scope = repo.resolve_scope_chat_id(chat_id)
    row = db.fetchone(
        """
        SELECT ws.*, a.name AS area_name
        FROM worker_shifts ws
        LEFT JOIN areas a ON a.id=ws.area_id
        WHERE ws.chat_id=? AND ws.user_id=? AND ws.status='open'
        ORDER BY ws.id DESC LIMIT 1
        """,
        (scope, int(user_id)),
    )
    return dict(row) if row else None


def upsert_shift_package(
    chat_id: int,
    user_id: int,
    client_package_id: str,
    *,
    shift_id: int | None = None,
    area_id: int | None = None,
    device_id: str = "",
    note: str = "",
) -> dict[str, Any]:
    scope = repo.resolve_scope_chat_id(chat_id)
    package_key = str(client_package_id or "").strip()[:120]
    if not package_key:
        raise ValueError("Не указан идентификатор пакета смены.")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO shift_sync_packages(
                chat_id,user_id,shift_id,area_id,client_package_id,device_id,status,note,submitted_at,updated_at
            ) VALUES(?,?,?,?,?,?, 'received', ?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id,user_id,client_package_id) DO UPDATE SET
                shift_id=COALESCE(excluded.shift_id,shift_sync_packages.shift_id),
                area_id=COALESCE(excluded.area_id,shift_sync_packages.area_id),
                device_id=CASE WHEN excluded.device_id<>'' THEN excluded.device_id ELSE shift_sync_packages.device_id END,
                note=CASE WHEN excluded.note<>'' THEN excluded.note ELSE shift_sync_packages.note END,
                submitted_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            """,
            (scope, int(user_id), shift_id, area_id, package_key, str(device_id or "")[:120], str(note or "")[:1000]),
        )
        row = conn.execute(
            "SELECT * FROM shift_sync_packages WHERE chat_id=? AND user_id=? AND client_package_id=?",
            (scope, int(user_id), package_key),
        ).fetchone()
        conn.commit()
    return dict(row) if row else {}


def upsert_shift_package_item(
    package_id: int,
    client_request_id: str,
    sequence_no: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    request_key = str(client_request_id or "").strip()[:120]
    if not request_key:
        raise ValueError("У записи отсутствует идентификатор защиты от дублей.")
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO shift_sync_items(package_id,client_request_id,sequence_no,payload_json,status,updated_at)
            VALUES(?,?,?,?, 'received',CURRENT_TIMESTAMP)
            ON CONFLICT(package_id,client_request_id) DO UPDATE SET
                sequence_no=excluded.sequence_no,
                payload_json=CASE
                    WHEN shift_sync_items.status IN ('accepted','duplicate') THEN shift_sync_items.payload_json
                    ELSE excluded.payload_json
                END,
                updated_at=CURRENT_TIMESTAMP
            """,
            (int(package_id), request_key, max(0, int(sequence_no)), raw),
        )
        row = conn.execute(
            "SELECT * FROM shift_sync_items WHERE package_id=? AND client_request_id=?",
            (int(package_id), request_key),
        ).fetchone()
        conn.commit()
    return dict(row) if row else {}


def update_package_item(
    item_id: int,
    status: str,
    *,
    message: str = "",
    warnings: Iterable[str] = (),
    operation_id: int | None = None,
) -> None:
    allowed = {"received", "accepted", "duplicate", "review", "rejected", "error"}
    value = status if status in allowed else "error"
    db.execute(
        """
        UPDATE shift_sync_items
        SET status=?,message=?,warnings_json=?,operation_id=COALESCE(?,operation_id),updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (value, str(message or "")[:1000], json.dumps(list(warnings), ensure_ascii=False), operation_id, int(item_id)),
    )


def get_package_item(item_id: int) -> dict[str, Any] | None:
    row = db.fetchone(
        """
        SELECT i.*,p.chat_id,p.user_id,p.client_package_id,p.status AS package_status,p.shift_id,p.area_id
        FROM shift_sync_items i JOIN shift_sync_packages p ON p.id=i.package_id
        WHERE i.id=?
        """,
        (int(item_id),),
    )
    if not row:
        return None
    item = dict(row)
    try:
        item["payload"] = json.loads(item.get("payload_json") or "{}")
    except Exception:
        item["payload"] = {}
    item["warnings"] = _json_list(item.get("warnings_json"))
    return item


def get_shift_package(package_id: int) -> dict[str, Any] | None:
    row = db.fetchone("SELECT * FROM shift_sync_packages WHERE id=?", (int(package_id),))
    if not row:
        return None
    result = dict(row)
    result["items"] = list_package_items(int(package_id))
    return result


def list_package_items(package_id: int) -> list[dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT i.*,o.created_at AS operation_created_at
        FROM shift_sync_items i
        LEFT JOIN operations o ON o.id=i.operation_id
        WHERE i.package_id=? ORDER BY i.sequence_no,i.id
        """,
        (int(package_id),),
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.get("payload_json") or "{}")
        except Exception:
            item["payload"] = {}
        item["warnings"] = _json_list(item.get("warnings_json"))
        result.append(item)
    return result


def recount_shift_package(package_id: int, *, reviewed_by: int | None = None) -> dict[str, Any] | None:
    rows = db.fetchall(
        "SELECT status,COUNT(*) AS n FROM shift_sync_items WHERE package_id=? GROUP BY status",
        (int(package_id),),
    )
    counts = {str(row["status"]): int(row["n"]) for row in rows}
    total = sum(counts.values())
    accepted = counts.get("accepted", 0) + counts.get("duplicate", 0)
    review = counts.get("review", 0) + counts.get("received", 0)
    rejected = counts.get("rejected", 0)
    errors = counts.get("error", 0)
    if total and accepted == total:
        status = "accepted"
    elif accepted and (review or rejected or errors):
        status = "partial"
    elif review:
        status = "review"
    elif rejected or errors:
        status = "rejected"
    else:
        status = "received"
    reviewed_sql = ""
    params: list[Any] = [status, total, accepted, review, rejected, errors]
    if reviewed_by is not None and not review:
        reviewed_sql = ",reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP"
        params.append(int(reviewed_by))
    params.append(int(package_id))
    db.execute(
        f"""
        UPDATE shift_sync_packages SET status=?,item_count=?,accepted_count=?,review_count=?,
            rejected_count=?,error_count=?,updated_at=CURRENT_TIMESTAMP{reviewed_sql}
        WHERE id=?
        """,
        params,
    )
    return get_shift_package(int(package_id))


def _display_name_sql(alias: str = "p") -> str:
    return f"""
    COALESCE(
      NULLIF((SELECT w.display_name FROM workers w WHERE w.chat_id={alias}.chat_id AND w.user_id={alias}.user_id AND w.is_active=1 LIMIT 1),''),
      NULLIF((SELECT dm.display_name FROM department_members dm JOIN departments d ON d.id=dm.department_id WHERE d.chat_id={alias}.chat_id AND dm.user_id={alias}.user_id AND dm.is_active=1 LIMIT 1),''),
      CAST({alias}.user_id AS TEXT)
    )
    """


def list_shift_packages(
    chat_id: int,
    *,
    user_id: int | None = None,
    worker_user_id: int | None = None,
    department_id: int | None = None,
    area_id: int | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    unresolved_only: bool = False,
    limit: int = 60,
) -> list[dict[str, Any]]:
    scope = repo.resolve_scope_chat_id(chat_id)
    where = ["p.chat_id=?"]
    params: list[Any] = [scope]
    effective_user = worker_user_id if worker_user_id is not None else user_id
    if effective_user is not None:
        where.append("p.user_id=?")
        params.append(int(effective_user))
    if department_id is not None:
        where.append("EXISTS (SELECT 1 FROM department_members dm JOIN departments d ON d.id=dm.department_id WHERE dm.department_id=? AND dm.user_id=p.user_id AND dm.is_active=1 AND d.chat_id=p.chat_id AND d.is_archived=0)")
        params.append(int(department_id))
    if area_id is not None:
        where.append("p.area_id=?")
        params.append(int(area_id))
    allowed_statuses = {"received", "review", "partial", "accepted", "rejected"}
    if status in allowed_statuses:
        where.append("p.status=?")
        params.append(str(status))
    elif unresolved_only:
        where.append("p.status IN ('received','review','partial','rejected')")
    if date_from:
        where.append("p.created_at>=?")
        params.append(str(date_from)[:10] + " 00:00:00")
    if date_to:
        where.append("p.created_at<?")
        try:
            from datetime import date, timedelta
            end = date.fromisoformat(str(date_to)[:10]) + timedelta(days=1)
            params.append(f"{end.isoformat()} 00:00:00")
        except ValueError:
            params.append(str(date_to)[:10] + " 23:59:59")
    params.append(max(1, min(int(limit), 300)))
    rows = db.fetchall(
        f"""
        SELECT p.*,a.name AS area_name,{_display_name_sql('p')} AS worker_name
        FROM shift_sync_packages p
        LEFT JOIN areas a ON a.id=p.area_id
        WHERE {' AND '.join(where)}
        ORDER BY CASE p.status WHEN 'review' THEN 0 WHEN 'partial' THEN 1 WHEN 'received' THEN 2 ELSE 3 END,
                 p.updated_at DESC,p.id DESC
        LIMIT ?
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        package = dict(row)
        package["items"] = list_package_items(int(package["id"]))
        result.append(package)
    return result


def package_unfinished_count(chat_id: int, user_id: int | None = None) -> int:
    scope = repo.resolve_scope_chat_id(chat_id)
    where = ["chat_id=?", "status IN ('received','review','partial','rejected')"]
    params: list[Any] = [scope]
    if user_id is not None:
        where.append("user_id=?")
        params.append(int(user_id))
    row = db.fetchone(f"SELECT COUNT(*) AS n FROM shift_sync_packages WHERE {' AND '.join(where)}", params)
    return int(row["n"] if row else 0)


def create_handover(
    chat_id: int,
    from_user_id: int,
    created_by: int,
    *,
    to_user_id: int | None = None,
    shift_id: int | None = None,
    area_id: int | None = None,
    summary: str = "",
    package_ids: Iterable[int] = (),
    checklist: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    scope = repo.resolve_scope_chat_id(chat_id)
    incoming_checks = list(checklist or [])
    active_checks = active_handover_checklist(scope)
    if active_checks:
        response_map = {int(x.get("item_id") or 0): x for x in incoming_checks if int(x.get("item_id") or 0) > 0}
        checklist_rows: list[dict[str, Any]] = []
        for source in active_checks:
            item_id = int(source.get("item_id") or 0)
            response = response_map.get(item_id, {})
            checklist_rows.append({
                "item_id": item_id, "label": str(source.get("label") or ""),
                "required": bool(source.get("required", True)),
                "checked": bool(response.get("checked", False)),
                "note": str(response.get("note") or "")[:1000],
            })
    else:
        checklist_rows = incoming_checks
    ids = sorted({int(x) for x in package_ids if int(x) > 0})
    unfinished = 0
    issues = 0
    if ids:
        marks = ",".join("?" for _ in ids)
        rows = db.fetchall(
            f"SELECT id,status,review_count,rejected_count,error_count FROM shift_sync_packages WHERE chat_id=? AND id IN ({marks})",
            (scope, *ids),
        )
        valid = {int(r["id"]): dict(r) for r in rows}
        ids = [x for x in ids if x in valid]
        unfinished = sum(1 for x in ids if valid[x]["status"] != "accepted")
        issues = sum(int(valid[x].get("review_count") or 0) + int(valid[x].get("rejected_count") or 0) + int(valid[x].get("error_count") or 0) for x in ids)
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO shift_handovers(
                chat_id,from_user_id,to_user_id,shift_id,area_id,status,summary,
                unfinished_count,issue_count,package_ids_json,created_by
            ) VALUES(?,?,?,?,?,'open',?,?,?,?,?)
            """,
            (
                scope, int(from_user_id), int(to_user_id) if to_user_id else None,
                shift_id, area_id, str(summary or "")[:2000], unfinished, issues,
                json.dumps(ids, ensure_ascii=False), int(created_by),
            ),
        )
        handover_id = int(cur.lastrowid)
        for order, check in enumerate(checklist_rows):
            item_id = int(check.get("item_id") or 0) or None
            label = str(check.get("label") or "")[:500].strip()
            required = bool(check.get("required", True))
            checked = bool(check.get("checked", False))
            note_value = str(check.get("note") or "")[:1000]
            if item_id:
                source = conn.execute("SELECT label,is_required,sort_order FROM shift_handover_checklist_items WHERE id=?", (item_id,)).fetchone()
                if source:
                    label = str(source["label"] or "")
                    required = bool(source["is_required"])
                    order = int(source["sort_order"] or order)
            if required and not checked:
                raise ValueError(f"Не отмечен обязательный пункт: {label or 'чек-лист передачи'}")
            if not label:
                continue
            conn.execute(
                """INSERT INTO shift_handover_checks(
                    handover_id,checklist_item_id,label,is_required,is_checked,note,checked_by,checked_at,sort_order
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (handover_id,item_id,label,int(required),int(checked),note_value,int(created_by) if checked else None,_now() if checked else None,order),
            )
        conn.commit()
    if to_user_id:
        repo.create_inbox_item(
            scope,
            int(to_user_id),
            "shift_handover",
            "Передача смены",
            str(summary or "Проверьте переданные незавершённые записи.")[:1000],
            "shift_handover",
            handover_id,
            deduplicate=False,
            priority="high" if unfinished or issues else "normal",
            force=True,
        )
    return get_handover(handover_id) or {}


def get_handover(handover_id: int) -> dict[str, Any] | None:
    row = db.fetchone(
        """
        SELECT h.*,a.name AS area_name,
          COALESCE(NULLIF(wf.display_name,''),CAST(h.from_user_id AS TEXT)) AS from_name,
          COALESCE(NULLIF(wt.display_name,''),CAST(h.to_user_id AS TEXT)) AS to_name
        FROM shift_handovers h
        LEFT JOIN areas a ON a.id=h.area_id
        LEFT JOIN workers wf ON wf.chat_id=h.chat_id AND wf.user_id=h.from_user_id
        LEFT JOIN workers wt ON wt.chat_id=h.chat_id AND wt.user_id=h.to_user_id
        WHERE h.id=?
        """,
        (int(handover_id),),
    )
    if not row:
        return None
    result = dict(row)
    result["package_ids"] = [int(x) for x in _json_list(result.get("package_ids_json")) if str(x).isdigit()]
    checks = [dict(x) for x in db.fetchall("SELECT * FROM shift_handover_checks WHERE handover_id=? ORDER BY sort_order,id", (int(handover_id),))]
    result["checklist"] = checks
    result["checklist_total"] = len(checks)
    result["checklist_done"] = sum(1 for x in checks if int(x.get("is_checked") or 0))
    result["checklist_required_missing"] = sum(1 for x in checks if int(x.get("is_required") or 0) and not int(x.get("is_checked") or 0))
    return result


def list_handovers(chat_id: int, user_id: int, *, can_manage: bool = False, limit: int = 80) -> list[dict[str, Any]]:
    scope = repo.resolve_scope_chat_id(chat_id)
    where = ["h.chat_id=?"]
    params: list[Any] = [scope]
    if not can_manage:
        where.append(
            """(h.from_user_id=? OR h.to_user_id=? OR (h.to_user_id IS NULL AND EXISTS (
                SELECT 1 FROM department_members mine
                JOIN department_members sender ON sender.department_id=mine.department_id AND sender.is_active=1
                JOIN departments d ON d.id=mine.department_id AND d.chat_id=h.chat_id AND d.is_archived=0
                WHERE mine.user_id=? AND mine.is_active=1 AND sender.user_id=h.from_user_id
            )))"""
        )
        params.extend([int(user_id), int(user_id), int(user_id)])
    params.append(max(1, min(int(limit), 200)))
    rows = db.fetchall(
        f"""
        SELECT h.*,a.name AS area_name,
          COALESCE(NULLIF(wf.display_name,''),CAST(h.from_user_id AS TEXT)) AS from_name,
          COALESCE(NULLIF(wt.display_name,''),CAST(h.to_user_id AS TEXT)) AS to_name
        FROM shift_handovers h
        LEFT JOIN areas a ON a.id=h.area_id
        LEFT JOIN workers wf ON wf.chat_id=h.chat_id AND wf.user_id=h.from_user_id
        LEFT JOIN workers wt ON wt.chat_id=h.chat_id AND wt.user_id=h.to_user_id
        WHERE {' AND '.join(where)}
        ORDER BY CASE h.status WHEN 'open' THEN 0 ELSE 1 END,h.created_at DESC,h.id DESC
        LIMIT ?
        """,
        params,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["package_ids"] = [int(x) for x in _json_list(item.get("package_ids_json")) if str(x).isdigit()]
        checks = [dict(x) for x in db.fetchall("SELECT * FROM shift_handover_checks WHERE handover_id=? ORDER BY sort_order,id", (int(item["id"]),))]
        item["checklist"] = checks
        item["checklist_total"] = len(checks)
        item["checklist_done"] = sum(1 for x in checks if int(x.get("is_checked") or 0))
        item["checklist_required_missing"] = sum(1 for x in checks if int(x.get("is_required") or 0) and not int(x.get("is_checked") or 0))
        result.append(item)
    return result


def acknowledge_handover(chat_id: int, handover_id: int, user_id: int, *, can_manage: bool = False) -> bool:
    scope = repo.resolve_scope_chat_id(chat_id)
    row = db.fetchone("SELECT * FROM shift_handovers WHERE chat_id=? AND id=?", (scope, int(handover_id)))
    if not row:
        return False
    if not can_manage and row["to_user_id"] is not None and int(row["to_user_id"]) != int(user_id):
        return False
    db.execute(
        """
        UPDATE shift_handovers SET status='acknowledged',acknowledged_by=?,acknowledged_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
        WHERE chat_id=? AND id=?
        """,
        (int(user_id), scope, int(handover_id)),
    )
    return True


def touch_device(
    user_id: int,
    device_id: str,
    *,
    device_name: str = "",
    platform: str = "",
    user_agent: str = "",
    chat_id: int | None = None,
) -> dict[str, Any]:
    key = str(device_id or "").strip()[:120]
    if not key:
        return {"allowed": True, "registered": False}
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO miniapp_devices(user_id,device_id,device_name,platform,user_agent,last_chat_id,last_seen_at)
            VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(user_id,device_id) DO UPDATE SET
                device_name=CASE WHEN excluded.device_name<>'' THEN excluded.device_name ELSE miniapp_devices.device_name END,
                platform=CASE WHEN excluded.platform<>'' THEN excluded.platform ELSE miniapp_devices.platform END,
                user_agent=CASE WHEN excluded.user_agent<>'' THEN excluded.user_agent ELSE miniapp_devices.user_agent END,
                last_chat_id=COALESCE(excluded.last_chat_id,miniapp_devices.last_chat_id),
                last_seen_at=CURRENT_TIMESTAMP
            """,
            (
                int(user_id), key, str(device_name or "")[:160], str(platform or "")[:80],
                str(user_agent or "")[:500], int(chat_id) if chat_id else None,
            ),
        )
        row = conn.execute("SELECT * FROM miniapp_devices WHERE user_id=? AND device_id=?", (int(user_id), key)).fetchone()
        conn.commit()
    result = dict(row) if row else {}
    result["allowed"] = not bool(result.get("revoked_at"))
    result["registered"] = True
    return result


def set_device_chat(user_id: int, device_id: str, chat_id: int) -> None:
    if not str(device_id or "").strip():
        return
    db.execute(
        "UPDATE miniapp_devices SET last_chat_id=?,last_seen_at=CURRENT_TIMESTAMP WHERE user_id=? AND device_id=?",
        (repo.resolve_scope_chat_id(chat_id), int(user_id), str(device_id)[:120]),
    )


def list_devices(user_ids: Iterable[int], *, limit: int = 200) -> list[dict[str, Any]]:
    ids = sorted({int(x) for x in user_ids if int(x) > 0})
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    rows = db.fetchall(
        f"SELECT * FROM miniapp_devices WHERE user_id IN ({marks}) ORDER BY revoked_at IS NOT NULL,last_seen_at DESC LIMIT ?",
        (*ids, max(1, min(int(limit), 500))),
    )
    return [dict(r) for r in rows]


def account_user_ids(chat_id: int) -> list[int]:
    scope = repo.resolve_scope_chat_id(chat_id)
    ids = {int(repo.settings.primary_owner_id)} if getattr(repo, "settings", None) else set()
    account = repo.get_account_by_scope(scope)
    if account:
        rows = db.fetchall("SELECT user_id FROM account_user_access WHERE account_id=?", (account.id,))
        ids.update(int(r["user_id"]) for r in rows)
    ids.update(int(r["user_id"]) for r in db.fetchall("SELECT user_id FROM workers WHERE chat_id=? AND is_active=1", (scope,)))
    ids.update(int(r["user_id"]) for r in db.fetchall(
        "SELECT dm.user_id FROM department_members dm JOIN departments d ON d.id=dm.department_id WHERE d.chat_id=? AND dm.is_active=1",
        (scope,),
    ))
    return sorted(x for x in ids if x > 0)



def handover_recipients(chat_id: int, user_id: int) -> list[dict[str, Any]]:
    """Return only people the actor may safely hand a shift to."""
    scope = repo.resolve_scope_chat_id(chat_id)
    uid = int(user_id)
    if repo.is_system_admin_id(uid):
        candidate_ids = account_user_ids(scope)
    else:
        rows = db.fetchall(
            """
            SELECT DISTINCT peer.user_id
            FROM department_members mine
            JOIN departments d ON d.id=mine.department_id AND d.chat_id=? AND d.is_archived=0
            JOIN department_members peer ON peer.department_id=d.id AND peer.is_active=1
            WHERE mine.user_id=? AND mine.is_active=1
            """,
            (scope, uid),
        )
        candidate_ids = [int(row["user_id"]) for row in rows]
        if not candidate_ids:
            candidate_ids = [uid]
    result: list[dict[str, Any]] = []
    for candidate in sorted(set(candidate_ids)):
        if candidate <= 0 or candidate == uid:
            continue
        row = db.fetchone(
            """
            SELECT COALESCE(
              NULLIF((SELECT display_name FROM workers WHERE chat_id=? AND user_id=? AND is_active=1 LIMIT 1),''),
              NULLIF((SELECT dm.display_name FROM department_members dm JOIN departments d ON d.id=dm.department_id WHERE d.chat_id=? AND dm.user_id=? AND dm.is_active=1 LIMIT 1),''),
              CAST(? AS TEXT)
            ) AS display_name
            """,
            (scope, candidate, scope, candidate, candidate),
        )
        result.append({"id": candidate, "name": str(row["display_name"] if row else candidate)})
    return result

def revoke_device(actor_user_id: int, target_user_id: int, device_id: str, *, reason: str = "") -> bool:
    row = db.fetchone("SELECT id FROM miniapp_devices WHERE user_id=? AND device_id=?", (int(target_user_id), str(device_id)[:120]))
    if not row:
        return False
    db.execute(
        """
        UPDATE miniapp_devices SET revoked_at=CURRENT_TIMESTAMP,revoked_by=?,revoke_reason=?
        WHERE user_id=? AND device_id=?
        """,
        (int(actor_user_id), str(reason or "")[:500], int(target_user_id), str(device_id)[:120]),
    )
    return True


def restore_device(actor_user_id: int, target_user_id: int, device_id: str) -> bool:
    row = db.fetchone("SELECT id FROM miniapp_devices WHERE user_id=? AND device_id=?", (int(target_user_id), str(device_id)[:120]))
    if not row:
        return False
    db.execute(
        """
        UPDATE miniapp_devices SET revoked_at=NULL,revoked_by=NULL,revoke_reason='',last_seen_at=CURRENT_TIMESTAMP
        WHERE user_id=? AND device_id=?
        """,
        (int(target_user_id), str(device_id)[:120]),
    )
    return True


def can_review_worker_packages(chat_id: int, actor_user_id: int, worker_user_id: int) -> bool:
    scope = repo.resolve_scope_chat_id(chat_id)
    if repo.is_system_admin_id(actor_user_id):
        return True
    row = db.fetchone(
        """
        SELECT 1 AS ok
        FROM department_members actor
        JOIN departments d ON d.id=actor.department_id AND d.chat_id=? AND d.is_archived=0
        JOIN department_members worker ON worker.department_id=d.id
        WHERE actor.user_id=? AND actor.is_active=1 AND actor.role_level>=50
          AND worker.user_id=? AND worker.is_active=1
        LIMIT 1
        """,
        (scope, int(actor_user_id), int(worker_user_id)),
    )
    return bool(row)


# --- Шаг 75: пакетная проверка, напоминания, чек-листы, шаблоны этикеток ---

def get_continuity_settings(chat_id: int) -> dict[str, Any]:
    scope = repo.resolve_scope_chat_id(chat_id)
    row = db.fetchone("SELECT * FROM shift_continuity_settings WHERE chat_id=?", (scope,))
    if row:
        return dict(row)
    return {
        "chat_id": scope,
        "package_reminder_after_minutes": 60,
        "package_repeat_minutes": 120,
        "handover_reminder_after_minutes": 30,
        "handover_repeat_minutes": 60,
        "max_reminders": 3,
    }


def save_continuity_settings(chat_id: int, actor_user_id: int, values: dict[str, Any]) -> dict[str, Any]:
    scope = repo.resolve_scope_chat_id(chat_id)
    package_first = max(5, min(int(values.get("package_reminder_after_minutes") or 60), 10080))
    package_repeat = max(5, min(int(values.get("package_repeat_minutes") or 120), 10080))
    handover_first = max(5, min(int(values.get("handover_reminder_after_minutes") or 30), 10080))
    handover_repeat = max(5, min(int(values.get("handover_repeat_minutes") or 60), 10080))
    max_reminders = max(0, min(int(values.get("max_reminders") or 3), 10))
    db.execute(
        """INSERT INTO shift_continuity_settings(
            chat_id,package_reminder_after_minutes,package_repeat_minutes,handover_reminder_after_minutes,
            handover_repeat_minutes,max_reminders,updated_by,updated_at
        ) VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(chat_id) DO UPDATE SET
          package_reminder_after_minutes=excluded.package_reminder_after_minutes,
          package_repeat_minutes=excluded.package_repeat_minutes,
          handover_reminder_after_minutes=excluded.handover_reminder_after_minutes,
          handover_repeat_minutes=excluded.handover_repeat_minutes,
          max_reminders=excluded.max_reminders,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
        (scope, package_first, package_repeat, handover_first, handover_repeat, max_reminders, int(actor_user_id)),
    )
    return get_continuity_settings(scope)


def package_review_recipient_ids(chat_id: int, worker_user_id: int) -> list[int]:
    scope = repo.resolve_scope_chat_id(chat_id)
    recipients = set(repo.list_system_admin_ids())
    rows = db.fetchall(
        """SELECT DISTINCT head.user_id FROM department_members worker
        JOIN departments d ON d.id=worker.department_id AND d.chat_id=? AND d.is_archived=0
        JOIN department_members head ON head.department_id=d.id AND head.is_active=1 AND head.role_level>=50
        WHERE worker.user_id=? AND worker.is_active=1""",
        (scope, int(worker_user_id)),
    )
    recipients.update(int(row["user_id"]) for row in rows)
    return sorted(x for x in recipients if x > 0)


def _reminder_level(kind: str, related_id: int, recipient: int) -> int:
    row = db.fetchone(
        "SELECT COALESCE(MAX(reminder_level),0) AS level FROM shift_continuity_reminders WHERE reminder_kind=? AND related_id=? AND recipient_user_id=?",
        (kind, int(related_id), int(recipient)),
    )
    return int(row["level"] if row else 0)


def _insert_continuity_reminder(chat_id: int, kind: str, related_id: int, recipient: int, level: int, inbox_item_id: int | None) -> bool:
    with db.connect() as conn:
        try:
            conn.execute(
                "INSERT INTO shift_continuity_reminders(chat_id,reminder_kind,related_id,recipient_user_id,reminder_level,inbox_item_id) VALUES(?,?,?,?,?,?)",
                (int(chat_id), kind, int(related_id), int(recipient), int(level), inbox_item_id),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False


def queue_continuity_reminders(now: datetime | None = None) -> int:
    now = now or datetime.now()
    created = 0
    scopes = [int(r["chat_id"]) for r in db.fetchall(
        "SELECT DISTINCT chat_id FROM shift_sync_packages WHERE status IN ('received','review','partial','rejected') UNION SELECT DISTINCT chat_id FROM shift_handovers WHERE status='open'"
    )]
    for scope in scopes:
        settings = get_continuity_settings(scope)
        max_level = int(settings.get("max_reminders") or 0)
        if max_level <= 0:
            continue
        packages = [dict(r) for r in db.fetchall(
            "SELECT * FROM shift_sync_packages WHERE chat_id=? AND status IN ('received','review','partial','rejected')",
            (scope,),
        )]
        for package in packages:
            try:
                started = datetime.fromisoformat(str(package.get("submitted_at") or package.get("created_at")))
            except Exception:
                continue
            elapsed = max(0.0, (now - started).total_seconds() / 60.0)
            recipients = {int(package["user_id"])} | set(package_review_recipient_ids(scope, int(package["user_id"])))
            for recipient in recipients:
                current = _reminder_level("package", int(package["id"]), recipient)
                next_level = current + 1
                if next_level > max_level:
                    continue
                due = int(settings["package_reminder_after_minutes"]) + (next_level - 1) * int(settings["package_repeat_minutes"])
                if elapsed < due:
                    continue
                urgent = next_level >= max_level or str(package.get("status")) in {"rejected", "partial"}
                item_id = repo.create_inbox_item(
                    scope, recipient, "shift_package_reminder",
                    ("Срочно: " if urgent else "Напоминание: ") + f"пакет смены №{package['id']} не завершён",
                    f"Статус: {package.get('status')}. Ожидает {int(elapsed)} мин. На проверке: {int(package.get('review_count') or 0)}, ошибок/отклонений: {int(package.get('error_count') or 0)+int(package.get('rejected_count') or 0)}.",
                    "shift_sync_package", int(package["id"]), deduplicate=False, priority="urgent" if urgent else "high", force=True,
                )
                if _insert_continuity_reminder(scope, "package", int(package["id"]), recipient, next_level, item_id or None):
                    created += 1
        handovers = [dict(r) for r in db.fetchall("SELECT * FROM shift_handovers WHERE chat_id=? AND status='open'", (scope,))]
        for handover in handovers:
            try:
                started = datetime.fromisoformat(str(handover.get("created_at")))
            except Exception:
                continue
            elapsed = max(0.0, (now - started).total_seconds() / 60.0)
            recipients: set[int] = set(repo.list_system_admin_ids())
            if handover.get("to_user_id"):
                recipients.add(int(handover["to_user_id"]))
            else:
                recipients.update(package_review_recipient_ids(scope, int(handover["from_user_id"])))
            for recipient in recipients:
                current = _reminder_level("handover", int(handover["id"]), recipient)
                next_level = current + 1
                if next_level > max_level:
                    continue
                due = int(settings["handover_reminder_after_minutes"]) + (next_level - 1) * int(settings["handover_repeat_minutes"])
                if elapsed < due:
                    continue
                urgent = next_level >= max_level or int(handover.get("issue_count") or 0) > 0
                item_id = repo.create_inbox_item(
                    scope, recipient, "shift_handover_reminder",
                    ("Срочно: " if urgent else "Напоминание: ") + f"передача смены №{handover['id']} не принята",
                    f"Ожидает {int(elapsed)} мин. Незавершённых пакетов: {int(handover.get('unfinished_count') or 0)}, проблем: {int(handover.get('issue_count') or 0)}.",
                    "shift_handover", int(handover["id"]), deduplicate=False, priority="urgent" if urgent else "high", force=True,
                )
                if _insert_continuity_reminder(scope, "handover", int(handover["id"]), recipient, next_level, item_id or None):
                    created += 1
    return created


def active_handover_checklist(chat_id: int) -> list[dict[str, Any]]:
    scope = repo.resolve_scope_chat_id(chat_id)
    template = db.fetchone(
        "SELECT * FROM shift_handover_checklist_templates WHERE chat_id=? AND is_enabled=1 ORDER BY id DESC LIMIT 1",
        (scope,),
    )
    if not template:
        return []
    rows = db.fetchall(
        "SELECT id AS item_id,label,is_required AS required,sort_order FROM shift_handover_checklist_items WHERE template_id=? ORDER BY sort_order,id",
        (int(template["id"]),),
    )
    return [dict(r) for r in rows]


def save_handover_checklist(chat_id: int, actor_user_id: int, items: Iterable[dict[str, Any]], name: str = "Основной чек-лист") -> list[dict[str, Any]]:
    scope = repo.resolve_scope_chat_id(chat_id)
    cleaned: list[tuple[str, int]] = []
    for raw in list(items or [])[:40]:
        label = str(raw.get("label") or "").strip()[:300]
        if label:
            cleaned.append((label, int(bool(raw.get("required", True)))))
    with db.connect() as conn:
        conn.execute("UPDATE shift_handover_checklist_templates SET is_enabled=0,updated_at=CURRENT_TIMESTAMP WHERE chat_id=?", (scope,))
        if cleaned:
            cur = conn.execute(
                "INSERT INTO shift_handover_checklist_templates(chat_id,name,is_enabled,created_by) VALUES(?,?,1,?)",
                (scope, str(name or "Основной чек-лист")[:120], int(actor_user_id)),
            )
            tid = int(cur.lastrowid)
            for order, (label, required) in enumerate(cleaned):
                conn.execute(
                    "INSERT INTO shift_handover_checklist_items(template_id,label,is_required,sort_order) VALUES(?,?,?,?)",
                    (tid, label, required, order),
                )
        conn.commit()
    return active_handover_checklist(scope)


def list_label_templates(chat_id: int) -> list[dict[str, Any]]:
    scope = repo.resolve_scope_chat_id(chat_id)
    return [dict(r) for r in db.fetchall("SELECT * FROM label_templates WHERE chat_id=? ORDER BY is_default DESC,name,id", (scope,))]


def save_label_template(chat_id: int, actor_user_id: int, values: dict[str, Any]) -> dict[str, Any]:
    scope = repo.resolve_scope_chat_id(chat_id)
    name = str(values.get("name") or "Шаблон")[:120].strip() or "Шаблон"
    page_mode = "label" if str(values.get("page_mode")) == "label" else "a4"
    width = max(20.0, min(float(values.get("label_width_mm") or 63), 210.0))
    height = max(15.0, min(float(values.get("label_height_mm") or 32), 297.0))
    cols = max(1, min(int(values.get("columns_count") or 1), 8))
    rows = max(1, min(int(values.get("rows_count") or 1), 20))
    margin_x = max(0.0, min(float(values.get("margin_x_mm") or 0), 50.0))
    margin_y = max(0.0, min(float(values.get("margin_y_mm") or 0), 50.0))
    gap_x = max(0.0, min(float(values.get("gap_x_mm") or 0), 30.0))
    gap_y = max(0.0, min(float(values.get("gap_y_mm") or 0), 30.0))
    code_size = max(8.0, min(float(values.get("code_size_mm") or 21), min(width, height)))
    code_type = "code128" if str(values.get("code_type")) == "code128" else "qr"
    is_default = int(bool(values.get("is_default", False)))
    with db.connect() as conn:
        if is_default:
            conn.execute("UPDATE label_templates SET is_default=0 WHERE chat_id=?", (scope,))
        conn.execute(
            """INSERT INTO label_templates(chat_id,name,page_mode,label_width_mm,label_height_mm,columns_count,rows_count,
            margin_x_mm,margin_y_mm,gap_x_mm,gap_y_mm,code_size_mm,code_type,is_default,created_by,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id,name) DO UPDATE SET page_mode=excluded.page_mode,label_width_mm=excluded.label_width_mm,
            label_height_mm=excluded.label_height_mm,columns_count=excluded.columns_count,rows_count=excluded.rows_count,
            margin_x_mm=excluded.margin_x_mm,margin_y_mm=excluded.margin_y_mm,gap_x_mm=excluded.gap_x_mm,gap_y_mm=excluded.gap_y_mm,
            code_size_mm=excluded.code_size_mm,code_type=excluded.code_type,is_default=excluded.is_default,updated_at=CURRENT_TIMESTAMP""",
            (scope,name,page_mode,width,height,cols,rows,margin_x,margin_y,gap_x,gap_y,code_size,code_type,is_default,int(actor_user_id)),
        )
        conn.commit()
    row = db.fetchone("SELECT * FROM label_templates WHERE chat_id=? AND name=?", (scope,name))
    return dict(row) if row else {}
