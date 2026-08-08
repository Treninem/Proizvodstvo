from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .. import db
from . import repository as repo

TASK_STATUSES = {"planned", "in_progress", "paused", "completed", "cancelled"}
TASK_PRIORITIES = {"low", "normal", "high", "urgent"}
REQUEST_STATUSES = {"requested", "approved", "issued", "partially_received", "received", "rejected", "cancelled"}
EQUIPMENT_STATUSES = {"active", "down", "maintenance", "inactive"}


def _scope(chat_id: int) -> int:
    return repo.resolve_scope_chat_id(int(chat_id))


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _department(department_id: int) -> dict[str, Any] | None:
    row = db.fetchone("SELECT * FROM departments WHERE id=? AND is_archived=0", (int(department_id),))
    return dict(row) if row else None


def _entity(scope: int, entity_id: int) -> dict[str, Any] | None:
    row = db.fetchone("SELECT * FROM entities WHERE chat_id=? AND id=? AND is_archived=0", (scope, int(entity_id)))
    return dict(row) if row else None


def _area(scope: int, area_id: int | None) -> dict[str, Any] | None:
    if area_id is None:
        return None
    row = db.fetchone("SELECT * FROM areas WHERE chat_id=? AND id=? AND is_archived=0", (scope, int(area_id)))
    return dict(row) if row else None


def _require_area(scope: int, area_id: int | None, label: str = "Площадка") -> None:
    if area_id is not None and not _area(scope, area_id):
        raise ValueError(f"{label} не найдена в этом учёте.")


def _member_level(department_id: int, user_id: int) -> int:
    return int(repo.department_actor_level(int(department_id), int(user_id)) or 0)


def _can_manage_department(scope: int, user_id: int, department_id: int) -> bool:
    if repo.is_system_admin_id(user_id):
        return True
    dep = _department(department_id)
    return bool(dep and int(dep["chat_id"]) == scope and _member_level(department_id, user_id) >= 50)


def _can_work_department(scope: int, user_id: int, department_id: int) -> bool:
    if repo.is_system_admin_id(user_id):
        return True
    dep = _department(department_id)
    return bool(dep and int(dep["chat_id"]) == scope and _member_level(department_id, user_id) >= 20)


def _department_allows_entity(department_id: int, operation_type: str, entity_id: int) -> bool:
    rule = db.fetchone(
        "SELECT can_submit FROM department_operation_rules WHERE department_id=? AND operation_key=?",
        (int(department_id), str(operation_type or "").strip()),
    )
    if not rule or not int(rule["can_submit"] or 0):
        return False
    entity_rule = db.fetchone(
        "SELECT can_submit FROM department_entity_rules WHERE department_id=? AND operation_key=? AND entity_id=?",
        (int(department_id), str(operation_type or "").strip(), int(entity_id)),
    )
    return bool(entity_rule and int(entity_rule["can_submit"] or 0))


def _entity_visible_to(scope: int, user_id: int, entity_id: int) -> bool:
    if repo.is_system_admin_id(user_id):
        return True
    visible = repo.visible_entity_ids_for_user(scope, user_id)
    return visible is None or int(entity_id) in {int(x) for x in visible}


def _department_manager_ids(scope: int, department_id: int) -> list[int]:
    ids = set(repo.list_system_admin_ids(include_owner=True))
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


def _notify(scope: int, recipients: list[int], kind: str, title: str, message: str, related_type: str, related_id: int, *, priority: str = "normal") -> None:
    for user_id in sorted(set(int(x) for x in recipients if x)):
        repo.create_inbox_item(
            scope, user_id, kind, title, message, related_type, int(related_id),
            deduplicate=False, priority=priority,
        )


def create_task(
    chat_id: int,
    actor_user_id: int,
    department_id: int,
    entity_id: int,
    *,
    operation_type: str = "production",
    target_quantity: float,
    unit: str = "шт",
    title: str = "",
    assignee_user_id: int | None = None,
    shift_plan_id: int | None = None,
    area_id: int | None = None,
    priority: str = "normal",
    due_at: str | None = None,
    note: str = "",
    output_lot_id: int | None = None,
) -> dict[str, Any]:
    scope = _scope(chat_id)
    if not _can_manage_department(scope, actor_user_id, department_id):
        raise PermissionError("Нет права создавать задания для этого отдела.")
    entity = _entity(scope, entity_id)
    if not entity:
        raise ValueError("Позиция не найдена.")
    if not repo.is_system_admin_id(actor_user_id) and not _department_allows_entity(department_id, operation_type, entity_id):
        raise PermissionError("Эта операция или позиция не разрешена выбранному отделу.")
    target_quantity = float(target_quantity)
    if target_quantity <= 0:
        raise ValueError("Плановое количество должно быть больше нуля.")
    _require_area(scope, area_id)
    if shift_plan_id:
        shift = db.fetchone("SELECT * FROM shift_plans WHERE chat_id=? AND id=? AND status IN ('planned','in_progress')", (scope, int(shift_plan_id)))
        if not shift:
            raise ValueError("Плановая смена не найдена или уже закрыта.")
        shift_user = int(shift["user_id"] or 0)
        if assignee_user_id and int(assignee_user_id) != shift_user:
            raise ValueError("Исполнитель задания не совпадает с сотрудником плановой смены.")
        assignee_user_id = shift_user
        if not area_id and shift["area_id"] is not None:
            area_id = int(shift["area_id"] )
        if not due_at:
            due_at = str(shift["planned_end"] or "")
    _require_area(scope, area_id)
    if assignee_user_id and not _can_work_department(scope, int(assignee_user_id), department_id):
        raise ValueError("Исполнитель не состоит в выбранном отделе.")
    priority = priority if priority in TASK_PRIORITIES else "normal"
    due_text = _parse_dt(due_at).strftime("%Y-%m-%d %H:%M:%S") if _parse_dt(due_at) else None
    if output_lot_id:
        lot = db.fetchone("SELECT id,entity_id FROM production_lots WHERE id=? AND chat_id=?", (int(output_lot_id), scope))
        if not lot:
            raise ValueError("Партия выпуска не найдена.")
        if int(lot["entity_id"] or 0) != int(entity_id):
            raise ValueError("Партия выпуска относится к другой позиции.")
        if not _entity_visible_to(scope, actor_user_id, entity_id):
            raise PermissionError("Нет доступа к выбранной партии выпуска.")
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO production_tasks(
                chat_id,department_id,assignee_user_id,shift_plan_id,area_id,operation_type,entity_type,entity_id,title,
                target_quantity,unit,priority,status,due_at,note,output_lot_id,created_by
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                scope, int(department_id), int(assignee_user_id) if assignee_user_id else None, int(shift_plan_id) if shift_plan_id else None,
                int(area_id) if area_id else None, str(operation_type or "production"), str(entity["entity_type"]), int(entity_id),
                str(title or entity["name"])[:180], target_quantity, str(unit or entity.get("default_unit") or "шт")[:30],
                priority, "planned", due_text, str(note or "")[:1000], int(output_lot_id) if output_lot_id else None, int(actor_user_id),
            ),
        )
        task_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO production_task_events(task_id,actor_user_id,event_type,to_status,note) VALUES(?,?,?,?,?)",
            (task_id, int(actor_user_id), "created", "planned", str(note or "")[:1000]),
        )
        conn.commit()
    recipients = [int(assignee_user_id)] if assignee_user_id else [x for x in _department_manager_ids(scope, department_id) if int(x) != int(actor_user_id)]
    if recipients:
        _notify(scope, recipients, "production_task", f"Новое задание №{task_id}", f"{entity['name']}: план {target_quantity:g} {unit}.", "production_task", task_id, priority="high" if priority in {"high", "urgent"} else "normal")
    return get_task(scope, task_id, actor_user_id) or {"id": task_id}


def _task_row(scope: int, task_id: int) -> dict[str, Any] | None:
    row = db.fetchone(
        """
        SELECT t.*,d.name AS department_name,e.name AS entity_name,a.name AS area_name,
               l.lot_code AS output_lot_code,sp.planned_start AS shift_planned_start,sp.planned_end AS shift_planned_end,
               sp.user_id AS shift_user_id,COALESCE(NULLIF(w.display_name,''),CAST(sp.user_id AS TEXT)) AS shift_worker_name
        FROM production_tasks t
        JOIN departments d ON d.id=t.department_id
        JOIN entities e ON e.id=t.entity_id
        LEFT JOIN areas a ON a.id=t.area_id
        LEFT JOIN production_lots l ON l.id=t.output_lot_id
        LEFT JOIN shift_plans sp ON sp.id=t.shift_plan_id
        LEFT JOIN workers w ON w.chat_id=t.chat_id AND w.user_id=sp.user_id
        WHERE t.chat_id=? AND t.id=?
        """,
        (scope, int(task_id)),
    )
    return dict(row) if row else None


def can_view_task(scope: int, user_id: int, task: dict[str, Any]) -> bool:
    if repo.is_system_admin_id(user_id):
        return True
    if int(task.get("assignee_user_id") or 0) == int(user_id):
        return True
    return _member_level(int(task["department_id"]), int(user_id)) >= 10


def _task_capabilities(scope: int, user_id: int, task: dict[str, Any]) -> dict[str, bool]:
    manager = _can_manage_department(scope, user_id, int(task["department_id"]))
    worker = _can_work_department(scope, user_id, int(task["department_id"])) and (not task.get("assignee_user_id") or int(task.get("assignee_user_id") or 0) == int(user_id))
    status = str(task.get("status") or "")
    return {
        "can_work": bool(manager or worker),
        "can_start": bool((manager or worker) and status in {"planned", "paused"}),
        "can_pause": bool((manager or worker) and status == "in_progress"),
        "can_complete": bool((manager or worker) and status in {"planned", "in_progress", "paused"}),
        "can_cancel": bool(manager and status in {"planned", "in_progress", "paused"}),
        "can_reopen": bool(manager and status in {"completed", "cancelled"}),
    }


def _decorate_task(scope: int, user_id: int, task: dict[str, Any]) -> dict[str, Any]:
    task.update(_task_capabilities(scope, user_id, task))
    return task


def get_task(chat_id: int, task_id: int, user_id: int) -> dict[str, Any] | None:
    scope = _scope(chat_id)
    task = _task_row(scope, task_id)
    if not task or not can_view_task(scope, user_id, task):
        return None
    _decorate_task(scope, user_id, task)
    task["events"] = [dict(r) for r in db.fetchall("SELECT * FROM production_task_events WHERE task_id=? ORDER BY id DESC LIMIT 30", (int(task_id),))]
    task["consumption"] = task_consumption(scope, int(task_id))
    return task


def list_tasks(chat_id: int, user_id: int, *, status: str | None = None, department_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    where = ["t.chat_id=?"]
    params: list[Any] = [scope]
    if status and status in TASK_STATUSES:
        where.append("t.status=?")
        params.append(status)
    if department_id:
        where.append("t.department_id=?")
        params.append(int(department_id))
    params.append(max(1, min(int(limit), 500)))
    rows = db.fetchall(
        f"""
        SELECT t.*,d.name AS department_name,e.name AS entity_name,a.name AS area_name,l.lot_code AS output_lot_code,
               sp.planned_start AS shift_planned_start,sp.planned_end AS shift_planned_end,sp.user_id AS shift_user_id,
               COALESCE(NULLIF(w.display_name,''),CAST(sp.user_id AS TEXT)) AS shift_worker_name
        FROM production_tasks t
        JOIN departments d ON d.id=t.department_id
        JOIN entities e ON e.id=t.entity_id
        LEFT JOIN areas a ON a.id=t.area_id
        LEFT JOIN production_lots l ON l.id=t.output_lot_id
        LEFT JOIN shift_plans sp ON sp.id=t.shift_plan_id
        LEFT JOIN workers w ON w.chat_id=t.chat_id AND w.user_id=sp.user_id
        WHERE {' AND '.join(where)}
        ORDER BY CASE t.status WHEN 'in_progress' THEN 0 WHEN 'paused' THEN 1 WHEN 'planned' THEN 2 ELSE 3 END,
                 CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
                 COALESCE(t.due_at,'9999-12-31'),t.id DESC LIMIT ?
        """,
        tuple(params),
    )
    result=[]
    for r in rows:
        item=dict(r)
        if can_view_task(scope,user_id,item):
            result.append(_decorate_task(scope,user_id,item))
    return result


def task_action(chat_id: int, actor_user_id: int, task_id: int, action: str, *, reason: str = "", note: str = "") -> dict[str, Any]:
    scope = _scope(chat_id)
    task = _task_row(scope, task_id)
    if not task:
        raise ValueError("Задание не найдено.")
    manager = _can_manage_department(scope, actor_user_id, int(task["department_id"]))
    worker = _can_work_department(scope, actor_user_id, int(task["department_id"])) and (not task.get("assignee_user_id") or int(task.get("assignee_user_id") or 0) == int(actor_user_id))
    transitions = {
        "start": ({"planned", "paused"}, "in_progress"),
        "pause": ({"in_progress"}, "paused"),
        "complete": ({"in_progress", "paused", "planned"}, "completed"),
        "cancel": ({"planned", "in_progress", "paused"}, "cancelled"),
        "reopen": ({"completed", "cancelled"}, "planned"),
    }
    if action not in transitions:
        raise ValueError("Неизвестное действие.")
    if action in {"cancel", "reopen"} and not manager:
        raise PermissionError("Это действие доступно только руководителю.")
    if action not in {"cancel", "reopen"} and not (manager or worker):
        raise PermissionError("Нет доступа к этому заданию.")
    allowed_from, target = transitions[action]
    if str(task["status"]) not in allowed_from:
        raise ValueError("Текущий статус задания не позволяет это действие.")
    if action == "cancel" and not str(reason or "").strip():
        raise ValueError("Укажите причину отмены.")
    if action == "complete":
        target_qty = float(task.get("target_quantity") or 0)
        actual_qty = float(task.get("actual_quantity") or 0)
        tolerance = max(1e-9, abs(target_qty) * 0.001)
        if abs(actual_qty - target_qty) > tolerance and not str(reason or "").strip():
            raise ValueError("Факт отличается от плана. Укажите причину отклонения.")
        try:
            from . import quality_control
            quality_control.validate_task_completion(scope, int(task_id))
        except ImportError:
            pass
    fields = ["status=?", "updated_at=CURRENT_TIMESTAMP"]
    values: list[Any] = [target]
    timestamp_field = {"start": "started_at", "pause": "paused_at", "complete": "completed_at", "cancel": "cancelled_at"}.get(action)
    if timestamp_field:
        fields.append(f"{timestamp_field}=CURRENT_TIMESTAMP")
    if action == "start" and not task.get("assignee_user_id") and not manager:
        fields.append("assignee_user_id=?")
        values.append(int(actor_user_id))
    if action in {"complete", "cancel"} and reason:
        fields.append("deviation_reason=?")
        values.append(str(reason)[:1000])
    values.append(int(task_id))
    with db.connect() as conn:
        conn.execute(f"UPDATE production_tasks SET {','.join(fields)} WHERE id=?", tuple(values))
        conn.execute(
            "INSERT INTO production_task_events(task_id,actor_user_id,event_type,from_status,to_status,reason,note) VALUES(?,?,?,?,?,?,?)",
            (int(task_id), int(actor_user_id), action, str(task["status"]), target, str(reason or "")[:1000], str(note or "")[:1000]),
        )
        conn.commit()
    if target == "completed":
        try:
            from . import quality_control
            quality_control.activate_rechecks_for_task(scope, int(task_id))
        except Exception:
            pass
    if target in {"completed", "cancelled"}:
        recipients = [int(task.get("created_by") or 0)] + _department_manager_ids(scope, int(task["department_id"]))
        _notify(scope, recipients, "production_task_result", f"Задание №{task_id}: {target}", str(reason or note or task.get("entity_name") or ""), "production_task", int(task_id))
    return get_task(scope, task_id, actor_user_id) or {"id": task_id}


def task_consumption(chat_id: int, task_id: int) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    rows = db.fetchall(
        """
        SELECT o.entity_type,o.entity_id,e.name AS entity_name,o.unit,
               SUM(CASE WHEN o.operation_type IN ('material_out','stock_out','write_off') THEN ABS(o.quantity) ELSE 0 END) AS consumed,
               SUM(CASE WHEN o.operation_type IN ('material_in','stock_in','return') THEN ABS(o.quantity) ELSE 0 END) AS received
        FROM operations o LEFT JOIN entities e ON e.id=o.entity_id
        WHERE o.chat_id=? AND o.task_id=?
        GROUP BY o.entity_type,o.entity_id,e.name,o.unit
        HAVING consumed>0 OR received>0
        ORDER BY e.name
        """,
        (scope, int(task_id)),
    )
    return [dict(r) for r in rows]


def _auto_task_for_operation(scope: int, user_id: int, op: dict[str, Any]) -> int | None:
    task_id = op.get("task_id")
    if task_id:
        task = _task_row(scope, int(task_id))
        if not task or not can_view_task(scope, user_id, task):
            return None
        return int(task_id)
    # Автопривязка только при однозначном активном задании на ту же позицию и действие.
    rows = db.fetchall(
        """
        SELECT t.* FROM production_tasks t
        WHERE t.chat_id=? AND t.status IN ('planned','in_progress','paused')
          AND t.operation_type=? AND t.entity_id=?
          AND (t.assignee_user_id=? OR t.assignee_user_id IS NULL)
        ORDER BY CASE WHEN t.assignee_user_id=? THEN 0 ELSE 1 END,t.id DESC
        """,
        (scope, str(op.get("operation_type") or ""), int(op.get("entity_id") or 0), int(user_id), int(user_id)),
    )
    candidates = [dict(r) for r in rows if _can_work_department(scope, user_id, int(r["department_id"]))]
    if not candidates:
        return None
    exact = [x for x in candidates if int(x.get("assignee_user_id") or 0) == int(user_id)]
    pool = exact or candidates
    return int(pool[0]["id"]) if len(pool) == 1 else None


def attach_operation(chat_id: int, user_id: int, operation_id: int, op: dict[str, Any]) -> None:
    """Идемпотентно связывает уже сохранённую операцию с заданием и партией."""
    scope = _scope(chat_id)
    task_id = _auto_task_for_operation(scope, user_id, op)
    lot_id = int(op.get("lot_id") or 0) or None
    if task_id:
        task = _task_row(scope, task_id)
        if task and not lot_id and task.get("output_lot_id") and str(op.get("operation_type") or "") == str(task.get("operation_type") or "") and int(op.get("entity_id") or 0) == int(task.get("entity_id") or 0):
            lot_id = int(task["output_lot_id"])
    if not task_id and not lot_id:
        return
    qty = float(op.get("quantity") or 0)
    with db.connect() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE operations SET task_id=?,lot_id=? WHERE id=? AND chat_id=?", (task_id, lot_id, int(operation_id), scope))
            if task_id:
                task = _task_row(scope, task_id)
                matching = bool(task and str(op.get("operation_type") or "") == str(task.get("operation_type") or "") and int(op.get("entity_id") or 0) == int(task.get("entity_id") or 0))
                if matching:
                    existing = conn.execute("SELECT id FROM production_task_events WHERE operation_id=?", (int(operation_id),)).fetchone()
                    if not existing:
                        conn.execute("UPDATE production_tasks SET actual_quantity=actual_quantity+?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (qty, task_id))
                        conn.execute(
                            "INSERT INTO production_task_events(task_id,actor_user_id,operation_id,event_type,quantity,metadata_json) VALUES(?,?,?,?,?,?)",
                            (task_id, int(user_id), int(operation_id), "operation", qty, json.dumps({"operation_id": int(operation_id)}, ensure_ascii=False)),
                        )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if lot_id:
        link_operation_to_lot(scope, user_id, int(operation_id), lot_id, op)
        if task_id:
            task = _task_row(scope, task_id)
            output_lot_id = int(task.get("output_lot_id") or 0) if task else 0
            if output_lot_id and output_lot_id != int(lot_id) and str(op.get("operation_type") or "") in {"material_out","stock_out","write_off"}:
                db.execute(
                    "INSERT OR REPLACE INTO lot_relations(parent_lot_id,component_lot_id,quantity_used,unit,task_id,created_by,created_at) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                    (output_lot_id, int(lot_id), abs(float(op.get("quantity") or 0)), str(op.get("unit") or "шт")[:30], int(task_id), int(user_id)),
                )


def create_request(
    chat_id: int,
    actor_user_id: int,
    requester_department_id: int,
    supplier_department_id: int,
    entity_id: int,
    quantity: float,
    *, unit: str = "шт", from_area_id: int | None = None, to_area_id: int | None = None,
    priority: str = "normal", needed_at: str | None = None, note: str = "",
) -> dict[str, Any]:
    scope = _scope(chat_id)
    if int(requester_department_id) == int(supplier_department_id):
        raise ValueError("Отдел-источник и отдел-получатель должны отличаться.")
    if not _can_work_department(scope, actor_user_id, requester_department_id) and not repo.is_system_admin_id(actor_user_id):
        raise PermissionError("Нет права создавать заявку от этого отдела.")
    if not _department(supplier_department_id) or int(_department(supplier_department_id)["chat_id"]) != scope:
        raise ValueError("Отдел-поставщик не найден.")
    entity = _entity(scope, entity_id)
    if not entity:
        raise ValueError("Позиция не найдена.")
    if not _entity_visible_to(scope, actor_user_id, entity_id):
        raise PermissionError("Эта позиция недоступна вашему рабочему контуру.")
    _require_area(scope, from_area_id, "Склад выдачи")
    _require_area(scope, to_area_id, "Склад получения")
    quantity = float(quantity)
    if quantity <= 0:
        raise ValueError("Количество должно быть больше нуля.")
    priority = priority if priority in TASK_PRIORITIES else "normal"
    needed_text = _parse_dt(needed_at).strftime("%Y-%m-%d %H:%M:%S") if _parse_dt(needed_at) else None
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO interdepartment_requests(
                chat_id,requester_department_id,supplier_department_id,requester_user_id,entity_type,entity_id,
                requested_quantity,unit,from_area_id,to_area_id,priority,status,needed_at,note
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (scope,int(requester_department_id),int(supplier_department_id),int(actor_user_id),str(entity["entity_type"]),int(entity_id),quantity,str(unit or entity.get("default_unit") or "шт")[:30],from_area_id,to_area_id,priority,"requested",needed_text,str(note or "")[:1000]),
        )
        request_id=int(cur.lastrowid)
        conn.execute("INSERT INTO interdepartment_request_events(request_id,actor_user_id,action,quantity,note) VALUES(?,?,?,?,?)",(request_id,int(actor_user_id),"requested",quantity,str(note or "")[:1000]))
        conn.commit()
    _notify(scope,_department_manager_ids(scope,supplier_department_id),"department_request",f"Новая заявка №{request_id}",f"{entity['name']}: {quantity:g} {unit}","department_request",request_id,priority="high" if priority in {"high","urgent"} else "normal")
    return get_request(scope, request_id, actor_user_id) or {"id":request_id}


def _request_row(scope:int, request_id:int)->dict[str,Any]|None:
    row=db.fetchone("""
        SELECT r.*,rd.name AS requester_department_name,sd.name AS supplier_department_name,e.name AS entity_name,
               fa.name AS from_area_name,ta.name AS to_area_name
        FROM interdepartment_requests r
        JOIN departments rd ON rd.id=r.requester_department_id
        JOIN departments sd ON sd.id=r.supplier_department_id
        JOIN entities e ON e.id=r.entity_id
        LEFT JOIN areas fa ON fa.id=r.from_area_id LEFT JOIN areas ta ON ta.id=r.to_area_id
        WHERE r.chat_id=? AND r.id=?
    """,(scope,int(request_id)))
    return dict(row) if row else None


def can_view_request(scope:int,user_id:int,item:dict[str,Any])->bool:
    if repo.is_system_admin_id(user_id): return True
    return _member_level(int(item["requester_department_id"]),user_id)>=10 or _member_level(int(item["supplier_department_id"]),user_id)>=10


def _decorate_request(scope:int,user_id:int,item:dict[str,Any])->dict[str,Any]:
    req_level=_member_level(int(item["requester_department_id"]),user_id)
    sup_level=_member_level(int(item["supplier_department_id"]),user_id)
    admin=repo.is_system_admin_id(user_id); status=str(item.get("status") or "")
    issued=float(item.get("issued_quantity") or 0)
    item.update({
        "can_approve": bool((admin or sup_level>=50) and status=="requested"),
        "can_reject": bool((admin or sup_level>=50) and status=="requested"),
        "can_issue": bool((admin or sup_level>=30) and status in {"approved","issued","partially_received"}),
        "can_receive": bool((admin or req_level>=20) and status in {"issued","partially_received"}),
        "can_cancel": bool((admin or req_level>=50 or int(item.get("requester_user_id") or 0)==int(user_id)) and status not in {"received","rejected","cancelled"} and issued<=0),
    })
    return item


def get_request(chat_id:int,request_id:int,user_id:int)->dict[str,Any]|None:
    scope=_scope(chat_id); item=_request_row(scope,request_id)
    if not item or not can_view_request(scope,user_id,item): return None
    _decorate_request(scope,user_id,item)
    item["events"]=[dict(r) for r in db.fetchall("SELECT * FROM interdepartment_request_events WHERE request_id=? ORDER BY id DESC LIMIT 30",(int(request_id),))]
    return item


def list_requests(chat_id:int,user_id:int,*,status:str|None=None,limit:int=200)->list[dict[str,Any]]:
    scope=_scope(chat_id); where=["r.chat_id=?"]; params:[Any]=[scope]
    if status and status in REQUEST_STATUSES: where.append("r.status=?"); params.append(status)
    params.append(max(1,min(int(limit),500)))
    rows=db.fetchall(f"""
        SELECT r.*,rd.name AS requester_department_name,sd.name AS supplier_department_name,e.name AS entity_name,
               fa.name AS from_area_name,ta.name AS to_area_name
        FROM interdepartment_requests r
        JOIN departments rd ON rd.id=r.requester_department_id JOIN departments sd ON sd.id=r.supplier_department_id
        JOIN entities e ON e.id=r.entity_id LEFT JOIN areas fa ON fa.id=r.from_area_id LEFT JOIN areas ta ON ta.id=r.to_area_id
        WHERE {' AND '.join(where)} ORDER BY CASE r.status WHEN 'requested' THEN 0 WHEN 'approved' THEN 1 WHEN 'issued' THEN 2 WHEN 'partially_received' THEN 3 ELSE 4 END,
        CASE r.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,COALESCE(r.needed_at,'9999-12-31'),r.id DESC LIMIT ?
    """,tuple(params))
    result=[]
    for r in rows:
        item=dict(r)
        if can_view_request(scope,user_id,item): result.append(_decorate_request(scope,user_id,item))
    return result


def request_action(chat_id:int,actor_user_id:int,request_id:int,action:str,*,quantity:float|None=None,reason:str="",note:str="")->dict[str,Any]:
    scope=_scope(chat_id); item=_request_row(scope,request_id)
    if not item: raise ValueError("Заявка не найдена.")
    req_level=_member_level(int(item["requester_department_id"]),actor_user_id)
    sup_level=_member_level(int(item["supplier_department_id"]),actor_user_id)
    admin=repo.is_system_admin_id(actor_user_id)
    status=str(item["status"])
    updates=[]; values=[]; target=status; event_qty=quantity
    if action=="approve":
        if not (admin or sup_level>=50): raise PermissionError("Подтвердить заявку может руководитель отдела-поставщика.")
        if status!="requested": raise ValueError("Заявка уже обработана.")
        q=float(quantity if quantity is not None else item["requested_quantity"])
        if q<=0: raise ValueError("Количество должно быть больше нуля.")
        target="approved"; updates += ["approved_quantity=?","approved_at=CURRENT_TIMESTAMP"]; values.append(q); event_qty=q
    elif action=="reject":
        if not (admin or sup_level>=50): raise PermissionError("Отклонить заявку может руководитель отдела-поставщика.")
        if status!="requested": raise ValueError("Заявка уже обработана.")
        if not str(reason).strip(): raise ValueError("Укажите причину отклонения.")
        target="rejected"; updates += ["decision_reason=?","closed_at=CURRENT_TIMESTAMP"]; values.append(str(reason)[:1000])
    elif action=="issue":
        if not (admin or sup_level>=30): raise PermissionError("Выдать позицию может ответственный сотрудник отдела-поставщика.")
        if status not in {"approved","issued","partially_received"}: raise ValueError("Сначала заявку нужно подтвердить.")
        max_q=float(item.get("approved_quantity") or item["requested_quantity"]); current=float(item.get("issued_quantity") or 0); q=float(quantity or 0)
        if q<=0 or current+q>max_q+1e-9: raise ValueError("Проверьте количество выдачи.")
        available = repo.inventory_quantity(scope, str(item["entity_type"]), int(item["entity_id"]), str(item.get("unit") or "шт"), item.get("from_area_id"))
        if available + 1e-9 < q:
            raise ValueError(f"Недостаточно на выдачу: доступно {available:g} {item.get('unit') or 'шт'}.")
        target="issued"; updates += ["issued_quantity=issued_quantity+?","issued_at=CURRENT_TIMESTAMP"]; values.append(q); event_qty=q
    elif action=="receive":
        if not (admin or req_level>=20): raise PermissionError("Получение подтверждает отдел-получатель.")
        if status not in {"issued","partially_received"}: raise ValueError("Позиция ещё не выдана.")
        issued=float(item.get("issued_quantity") or 0); received=float(item.get("received_quantity") or 0); q=float(quantity or 0)
        if q<=0 or received+q>issued+1e-9: raise ValueError("Нельзя получить больше, чем выдано.")
        new_received=received+q; target="received" if new_received>=issued-1e-9 and issued>=float(item.get("approved_quantity") or item["requested_quantity"])-1e-9 else "partially_received"
        updates += ["received_quantity=received_quantity+?","received_at=CURRENT_TIMESTAMP"]; values.append(q); event_qty=q
        if target=="received": updates.append("closed_at=CURRENT_TIMESTAMP")
    elif action=="cancel":
        if not (admin or req_level>=50 or int(item.get("requester_user_id") or 0)==int(actor_user_id)): raise PermissionError("Нет права отменить заявку.")
        if status in {"received","rejected","cancelled"}: raise ValueError("Заявка уже закрыта.")
        if float(item.get("issued_quantity") or 0)>0: raise ValueError("После выдачи заявка не отменяется — закройте получение или оформите обратное движение.")
        target="cancelled"; updates += ["decision_reason=?","closed_at=CURRENT_TIMESTAMP"]; values.append(str(reason or note or "Отменено")[:1000])
    else: raise ValueError("Неизвестное действие.")
    updates += ["status=?","updated_at=CURRENT_TIMESTAMP"]; values.append(target); values.append(int(request_id))
    with db.connect() as conn:
        conn.execute(f"UPDATE interdepartment_requests SET {','.join(updates)} WHERE id=?",tuple(values))
        conn.execute("INSERT INTO interdepartment_request_events(request_id,actor_user_id,action,quantity,reason,note) VALUES(?,?,?,?,?,?)",(int(request_id),int(actor_user_id),action,event_qty,str(reason or "")[:1000],str(note or "")[:1000]))
        conn.commit()
    if action in {"issue", "receive"} and event_qty and float(event_qty) > 0:
        try:
            from . import accounting
            auto_op = {
                "operation_type": "stock_out" if action == "issue" else "stock_in",
                "entity_type": str(item["entity_type"]), "entity_id": int(item["entity_id"]),
                "quantity": float(event_qty), "unit": str(item.get("unit") or "шт"),
                "area_id": item.get("from_area_id") if action == "issue" else item.get("to_area_id"),
                "source_channel": "workflow", "skip_risk_observation": False,
            }
            accounting._insert_operation(scope, scope, int(actor_user_id), auto_op, f"Заявка №{request_id}: {'выдача' if action == 'issue' else 'получение'}")
        except Exception:
            # Статус заявки уже сохранён; основной планировщик/инвентаризация выявят расхождение.
            pass
    recipients=_department_manager_ids(scope,int(item["requester_department_id"]))+_department_manager_ids(scope,int(item["supplier_department_id"]))+[int(item["requester_user_id"])]
    _notify(scope,recipients,"department_request_update",f"Заявка №{request_id}: {target}",str(note or reason or item.get("entity_name") or ""),"department_request",int(request_id))
    return get_request(scope,request_id,actor_user_id) or {"id":request_id}


def create_lot(chat_id:int,actor_user_id:int,entity_id:int,lot_code:str,*,supplier_code:str="",manufacture_date:str|None=None,expiry_date:str|None=None,note:str="")->dict[str,Any]:
    scope=_scope(chat_id)
    if not (repo.is_system_admin_id(actor_user_id) or repo.user_can_manage_departments(scope,actor_user_id)):
        raise PermissionError("Создание партий доступно ответственным сотрудникам.")
    entity=_entity(scope,entity_id)
    if not entity: raise ValueError("Позиция не найдена.")
    if not _entity_visible_to(scope,actor_user_id,entity_id): raise PermissionError("Эта позиция недоступна вашему рабочему контуру.")
    code=str(lot_code or "").strip()
    if not code: raise ValueError("Укажите код партии.")
    try:
        with db.connect() as conn:
            cur=conn.execute("INSERT INTO production_lots(chat_id,entity_type,entity_id,lot_code,supplier_code,manufacture_date,expiry_date,note,created_by) VALUES(?,?,?,?,?,?,?,?,?)",(scope,str(entity["entity_type"]),int(entity_id),code[:120],str(supplier_code or "")[:120],manufacture_date or None,expiry_date or None,str(note or "")[:1000],int(actor_user_id)))
            conn.commit(); lot_id=int(cur.lastrowid)
    except Exception as exc:
        if "UNIQUE" in str(exc).upper(): raise ValueError("Такой код партии уже существует.") from exc
        raise
    return get_lot(scope,lot_id,actor_user_id) or {"id":lot_id}


def list_lots(chat_id:int,user_id:int,limit:int=200)->list[dict[str,Any]]:
    scope=_scope(chat_id); visible=repo.visible_entity_ids_for_user(scope,user_id)
    rows=db.fetchall("""
      SELECT l.*,e.name AS entity_name,COALESCE(SUM(li.quantity),0) AS tracked_quantity,
             GROUP_CONCAT(DISTINCT a.name) AS area_names
      FROM production_lots l JOIN entities e ON e.id=l.entity_id
      LEFT JOIN lot_inventory li ON li.lot_id=l.id LEFT JOIN areas a ON a.id=li.area_id
      WHERE l.chat_id=? GROUP BY l.id ORDER BY l.id DESC LIMIT ?
    """,(scope,max(1,min(int(limit),500))))
    return [dict(r) for r in rows if visible is None or int(r["entity_id"]) in visible]


def get_lot(chat_id:int,lot_id:int,user_id:int)->dict[str,Any]|None:
    scope=_scope(chat_id); row=db.fetchone("SELECT l.*,e.name AS entity_name FROM production_lots l JOIN entities e ON e.id=l.entity_id WHERE l.chat_id=? AND l.id=?",(scope,int(lot_id)))
    if not row: return None
    item=dict(row); visible=repo.visible_entity_ids_for_user(scope,user_id)
    if visible is not None and int(item["entity_id"]) not in visible: return None
    item["inventory"]=[dict(r) for r in db.fetchall("SELECT li.*,a.name AS area_name FROM lot_inventory li LEFT JOIN areas a ON a.id=li.area_id WHERE li.lot_id=?",(int(lot_id),))]
    item["components"]=[dict(r) for r in db.fetchall("SELECT lr.*,l.lot_code,e.name AS entity_name FROM lot_relations lr JOIN production_lots l ON l.id=lr.component_lot_id JOIN entities e ON e.id=l.entity_id WHERE lr.parent_lot_id=?",(int(lot_id),))]
    item["used_in"]=[dict(r) for r in db.fetchall("SELECT lr.*,l.lot_code,e.name AS entity_name FROM lot_relations lr JOIN production_lots l ON l.id=lr.parent_lot_id JOIN entities e ON e.id=l.entity_id WHERE lr.component_lot_id=?",(int(lot_id),))]
    return item


def link_lots(chat_id:int,actor_user_id:int,parent_lot_id:int,component_lot_id:int,quantity:float,unit:str="шт",task_id:int|None=None)->None:
    scope=_scope(chat_id)
    if not (repo.is_system_admin_id(actor_user_id) or repo.user_can_manage_departments(scope,actor_user_id)): raise PermissionError("Нет права связывать партии.")
    if int(parent_lot_id)==int(component_lot_id): raise ValueError("Партия не может содержать саму себя.")
    for lot_id in (parent_lot_id,component_lot_id):
        if not db.fetchone("SELECT id FROM production_lots WHERE chat_id=? AND id=?",(scope,int(lot_id))): raise ValueError("Партия не найдена.")
        if not get_lot(scope,int(lot_id),actor_user_id): raise PermissionError("Нет доступа к одной из выбранных партий.")
    q=float(quantity)
    if q<=0: raise ValueError("Количество должно быть больше нуля.")
    db.execute("INSERT OR REPLACE INTO lot_relations(parent_lot_id,component_lot_id,quantity_used,unit,task_id,created_by,created_at) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)",(int(parent_lot_id),int(component_lot_id),q,str(unit or "шт")[:30],int(task_id) if task_id else None,int(actor_user_id)))


def _lot_inventory_delta(lot_id:int,area_id:int|None,unit:str,delta:float,*,conn=None)->None:
    own = conn is None
    connection = conn or db.connect()
    try:
        row=connection.execute("SELECT quantity FROM lot_inventory WHERE lot_id=? AND ((area_id IS NULL AND ? IS NULL) OR area_id=?) AND unit=?",(int(lot_id),area_id,area_id,unit)).fetchone()
        if row:
            connection.execute("UPDATE lot_inventory SET quantity=quantity+?,updated_at=CURRENT_TIMESTAMP WHERE lot_id=? AND ((area_id IS NULL AND ? IS NULL) OR area_id=?) AND unit=?",(float(delta),int(lot_id),area_id,area_id,unit))
        else:
            connection.execute("INSERT INTO lot_inventory(lot_id,area_id,quantity,unit) VALUES(?,?,?,?)",(int(lot_id),area_id,float(delta),unit))
        if own: connection.commit()
    finally:
        if own: connection.close()


def link_operation_to_lot(scope:int,user_id:int,operation_id:int,lot_id:int,op:dict[str,Any])->None:
    lot=db.fetchone("SELECT * FROM production_lots WHERE chat_id=? AND id=?",(scope,int(lot_id)))
    if not lot or int(lot["entity_id"])!=int(op.get("entity_id") or 0):
        return
    operation_type=str(op.get("operation_type") or ""); qty=float(op.get("quantity") or 0); unit=str(op.get("unit") or "шт"); area_id=op.get("area_id")
    incoming={"production","assembly","material_in","stock_in","return"}; outgoing={"material_out","stock_out","write_off","shipment","shipment_client","shipment_fulfillment"}
    role="output" if operation_type in incoming else "input" if operation_type in outgoing else "trace"
    with db.connect() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur=conn.execute("INSERT OR IGNORE INTO lot_operation_links(operation_id,lot_id,link_role,quantity) VALUES(?,?,?,?)",(int(operation_id),int(lot_id),role,qty))
            inserted = int(cur.rowcount or 0) > 0
            if inserted:
                if operation_type in incoming: _lot_inventory_delta(lot_id,area_id,unit,qty,conn=conn)
                elif operation_type in outgoing: _lot_inventory_delta(lot_id,area_id,unit,-qty,conn=conn)
                elif operation_type in {"movement","transfer_to_assembly"}:
                    if op.get("from_area_id") is not None: _lot_inventory_delta(lot_id,int(op["from_area_id"]),unit,-qty,conn=conn)
                    if op.get("to_area_id") is not None: _lot_inventory_delta(lot_id,int(op["to_area_id"]),unit,qty,conn=conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def save_equipment(chat_id:int,actor_user_id:int,name:str,*,equipment_id:int|None=None,department_id:int|None=None,area_id:int|None=None,code:str="",status:str="active",service_interval_days:int=0,warning_before_days:int=3,note:str="")->dict[str,Any]:
    scope=_scope(chat_id)
    _require_area(scope,area_id)
    if department_id and not _can_manage_department(scope,actor_user_id,int(department_id)):
        raise PermissionError("Нет права управлять оборудованием этого отдела.")
    if not department_id and not repo.is_system_admin_id(actor_user_id):
        raise PermissionError("Оборудование без отдела может создавать только владелец/администратор.")
    if not str(name or "").strip(): raise ValueError("Укажите название оборудования.")
    status=status if status in EQUIPMENT_STATUSES else "active"; interval=max(0,min(int(service_interval_days),3650)); warning=max(0,min(int(warning_before_days),365))
    next_due=(datetime.now()+timedelta(days=interval)).strftime("%Y-%m-%d %H:%M:%S") if interval else None
    if equipment_id:
        current=db.fetchone("SELECT * FROM equipment WHERE chat_id=? AND id=?",(scope,int(equipment_id)))
        if not current: raise ValueError("Оборудование не найдено.")
        if current["department_id"] and not _can_manage_department(scope,actor_user_id,int(current["department_id"])): raise PermissionError("Нет права редактировать это оборудование.")
        if current["last_service_at"] and interval:
            base=_parse_dt(str(current["last_service_at"])) or datetime.now(); next_due=(base+timedelta(days=interval)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute("UPDATE equipment SET department_id=?,area_id=?,name=?,code=?,status=?,service_interval_days=?,warning_before_days=?,next_service_at=?,note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(department_id,area_id,str(name)[:180],str(code or "")[:100],status,interval,warning,next_due,str(note or "")[:1000],int(equipment_id)))
        eid=int(equipment_id)
    else:
        with db.connect() as conn:
            cur=conn.execute("INSERT INTO equipment(chat_id,department_id,area_id,name,code,status,service_interval_days,warning_before_days,next_service_at,note,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(scope,department_id,area_id,str(name)[:180],str(code or "")[:100],status,interval,warning,next_due,str(note or "")[:1000],int(actor_user_id)));conn.commit();eid=int(cur.lastrowid)
    return get_equipment(scope,eid,actor_user_id) or {"id":eid}


def _equipment_row(scope:int,equipment_id:int)->dict[str,Any]|None:
    row=db.fetchone("SELECT eq.*,d.name AS department_name,a.name AS area_name FROM equipment eq LEFT JOIN departments d ON d.id=eq.department_id LEFT JOIN areas a ON a.id=eq.area_id WHERE eq.chat_id=? AND eq.id=? AND eq.is_archived=0",(scope,int(equipment_id)))
    return dict(row) if row else None


def can_view_equipment(scope:int,user_id:int,item:dict[str,Any])->bool:
    if repo.is_system_admin_id(user_id): return True
    dep=item.get("department_id")
    return bool(dep and _member_level(int(dep),user_id)>=10)


def get_equipment(chat_id:int,equipment_id:int,user_id:int)->dict[str,Any]|None:
    scope=_scope(chat_id); item=_equipment_row(scope,equipment_id)
    if not item or not can_view_equipment(scope,user_id,item): return None
    item["downtimes"]=[dict(r) for r in db.fetchall("SELECT * FROM equipment_downtimes WHERE equipment_id=? ORDER BY id DESC LIMIT 20",(int(equipment_id),))]
    item["maintenance"]=[dict(r) for r in db.fetchall("SELECT * FROM maintenance_records WHERE equipment_id=? ORDER BY id DESC LIMIT 20",(int(equipment_id),))]
    return item


def list_equipment(chat_id:int,user_id:int)->list[dict[str,Any]]:
    scope=_scope(chat_id); rows=db.fetchall("SELECT eq.*,d.name AS department_name,a.name AS area_name,(SELECT COUNT(*) FROM equipment_downtimes ed WHERE ed.equipment_id=eq.id AND ed.status='open') AS open_downtimes,(SELECT ed.id FROM equipment_downtimes ed WHERE ed.equipment_id=eq.id AND ed.status='open' ORDER BY ed.id DESC LIMIT 1) AS open_downtime_id FROM equipment eq LEFT JOIN departments d ON d.id=eq.department_id LEFT JOIN areas a ON a.id=eq.area_id WHERE eq.chat_id=? AND eq.is_archived=0 ORDER BY eq.name",(scope,))
    result=[]
    for r in rows:
        item=dict(r)
        if not can_view_equipment(scope,user_id,item): continue
        level=_member_level(int(item["department_id"]),user_id) if item.get("department_id") else 0; admin=repo.is_system_admin_id(user_id)
        item["can_report_downtime"]=True
        item["can_close_downtime"]=bool(admin or level>=30)
        item["can_maintain"]=bool(admin or level>=30)
        result.append(item)
    return result


def open_downtime(chat_id:int,actor_user_id:int,equipment_id:int,*,reason_type:str="other",reason:str="",task_id:int|None=None)->dict[str,Any]:
    scope=_scope(chat_id); item=_equipment_row(scope,equipment_id)
    if not item or not can_view_equipment(scope,actor_user_id,item): raise PermissionError("Нет доступа к оборудованию.")
    if not str(reason or "").strip(): raise ValueError("Укажите причину простоя.")
    if task_id:
        task=_task_row(scope,int(task_id))
        if not task or not can_view_task(scope,actor_user_id,task): raise PermissionError("Нет доступа к выбранному заданию.")
        if item.get("department_id") and int(task.get("department_id") or 0)!=int(item.get("department_id") or 0): raise ValueError("Задание относится к другому отделу.")
    existing=db.fetchone("SELECT id FROM equipment_downtimes WHERE equipment_id=? AND status='open'",(int(equipment_id),))
    if existing: raise ValueError("Для оборудования уже открыт простой.")
    with db.connect() as conn:
        cur=conn.execute("INSERT INTO equipment_downtimes(equipment_id,chat_id,task_id,reported_by,reason_type,reason) VALUES(?,?,?,?,?,?)",(int(equipment_id),scope,int(task_id) if task_id else None,int(actor_user_id),str(reason_type or "other")[:80],str(reason)[:1000]));did=int(cur.lastrowid)
        conn.execute("UPDATE equipment SET status='down',updated_at=CURRENT_TIMESTAMP WHERE id=?",(int(equipment_id),));conn.commit()
    if item.get("department_id"):_notify(scope,_department_manager_ids(scope,int(item["department_id"])),"equipment_downtime",f"Простой: {item['name']}",str(reason),"equipment_downtime",did,priority="urgent")
    return dict(db.fetchone("SELECT * FROM equipment_downtimes WHERE id=?",(did,)))


def close_downtime(chat_id:int,actor_user_id:int,downtime_id:int,resolution:str)->dict[str,Any]:
    scope=_scope(chat_id); row=db.fetchone("SELECT ed.*,eq.department_id,eq.name FROM equipment_downtimes ed JOIN equipment eq ON eq.id=ed.equipment_id WHERE ed.chat_id=? AND ed.id=?",(scope,int(downtime_id)))
    if not row: raise ValueError("Простой не найден.")
    item=dict(row)
    if not (repo.is_system_admin_id(actor_user_id) or (item.get("department_id") and _member_level(int(item["department_id"]),actor_user_id)>=30)):
        raise PermissionError("Закрыть простой может ответственный сотрудник.")
    if item["status"]!="open": raise ValueError("Простой уже закрыт.")
    if not str(resolution or "").strip(): raise ValueError("Укажите результат устранения причины.")
    db.execute("UPDATE equipment_downtimes SET status='closed',ended_at=CURRENT_TIMESTAMP,resolution=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(str(resolution)[:1000],int(downtime_id)))
    db.execute("UPDATE equipment SET status='active',updated_at=CURRENT_TIMESTAMP WHERE id=?",(int(item["equipment_id"]),))
    return dict(db.fetchone("SELECT * FROM equipment_downtimes WHERE id=?",(int(downtime_id),)))


def record_maintenance(chat_id:int,actor_user_id:int,equipment_id:int,*,maintenance_type:str="planned",note:str="")->dict[str,Any]:
    scope=_scope(chat_id); item=_equipment_row(scope,equipment_id)
    if not item: raise ValueError("Оборудование не найдено.")
    if not (repo.is_system_admin_id(actor_user_id) or (item.get("department_id") and _member_level(int(item["department_id"]),actor_user_id)>=30)): raise PermissionError("Нет права отмечать обслуживание.")
    interval=int(item.get("service_interval_days") or 0); next_due=(datetime.now()+timedelta(days=interval)).strftime("%Y-%m-%d %H:%M:%S") if interval else None
    with db.connect() as conn:
        cur=conn.execute("INSERT INTO maintenance_records(equipment_id,chat_id,actor_user_id,maintenance_type,status,next_due_at,note) VALUES(?,?,?,?,?,?,?)",(int(equipment_id),scope,int(actor_user_id),str(maintenance_type or "planned")[:80],"completed",next_due,str(note or "")[:1000]));rid=int(cur.lastrowid)
        conn.execute("UPDATE equipment SET last_service_at=CURRENT_TIMESTAMP,next_service_at=?,status='active',updated_at=CURRENT_TIMESTAMP WHERE id=?",(next_due,int(equipment_id)));conn.commit()
    return dict(db.fetchone("SELECT * FROM maintenance_records WHERE id=?",(rid,)))


def plan_fact_summary(chat_id:int,user_id:int,*,start_date:str|None=None,end_date:str|None=None,department_id:int|None=None)->dict[str,Any]:
    scope=_scope(chat_id); start=start_date or (datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d"); end=end_date or datetime.now().strftime("%Y-%m-%d")
    tasks=list_tasks(scope,user_id,department_id=department_id,limit=500)
    selected=[]
    for t in tasks:
        stamp=str(t.get("shift_planned_start") or t.get("started_at") or t.get("created_at") or "")[:10]
        if start<=stamp<=end: selected.append(t)
    by_dep:dict[int,dict[str,Any]]={}
    for t in selected:
        dep_id=int(t["department_id"]); d=by_dep.setdefault(dep_id,{"department_id":dep_id,"department_name":t.get("department_name") or "Отдел","tasks":0,"target":0.0,"actual":0.0,"completed":0,"overdue":0})
        d["tasks"]+=1; d["target"]+=float(t.get("target_quantity") or 0); d["actual"]+=float(t.get("actual_quantity") or 0); d["completed"]+=1 if t.get("status")=="completed" else 0
        due=_parse_dt(t.get("due_at")); d["overdue"]+=1 if due and due<datetime.now() and t.get("status") not in {"completed","cancelled"} else 0
    downtime_rows=db.fetchall("""
      SELECT eq.department_id,SUM((julianday(COALESCE(ed.ended_at,CURRENT_TIMESTAMP))-julianday(ed.started_at))*1440.0) AS minutes
      FROM equipment_downtimes ed JOIN equipment eq ON eq.id=ed.equipment_id
      WHERE ed.chat_id=? AND date(ed.started_at) BETWEEN date(?) AND date(?) GROUP BY eq.department_id
    """,(scope,start,end))
    downtime={int(r["department_id"]):round(float(r["minutes"] or 0),1) for r in downtime_rows if r["department_id"] is not None}
    for dep_id in downtime:
        if dep_id not in by_dep and (repo.is_system_admin_id(user_id) or _member_level(dep_id,user_id)>=10):
            dep=_department(dep_id)
            if dep: by_dep[dep_id]={"department_id":dep_id,"department_name":dep.get("name") or "Отдел","tasks":0,"target":0.0,"actual":0.0,"completed":0,"overdue":0}
    rows=[]
    for dep_id,d in by_dep.items():
        d["completion_percent"]=round((d["actual"]/d["target"]*100) if d["target"] else 0,1); d["deviation"]=round(d["actual"]-d["target"],3); d["downtime_minutes"]=downtime.get(dep_id,0); rows.append(d)
    by_shift:dict[int,dict[str,Any]]={}
    for t in selected:
        shift_id=int(t.get("shift_plan_id") or 0)
        if not shift_id:
            continue
        item=by_shift.setdefault(shift_id,{"shift_plan_id":shift_id,"worker_name":t.get("shift_worker_name") or str(t.get("assignee_user_id") or ""),"planned_start":t.get("shift_planned_start"),"planned_end":t.get("shift_planned_end"),"department_name":t.get("department_name") or "Отдел","tasks":0,"target":0.0,"actual":0.0,"completed":0,"overdue":0})
        item["tasks"]+=1; item["target"]+=float(t.get("target_quantity") or 0); item["actual"]+=float(t.get("actual_quantity") or 0); item["completed"]+=1 if t.get("status")=="completed" else 0
        due=_parse_dt(t.get("due_at")); item["overdue"]+=1 if due and due<datetime.now() and t.get("status") not in {"completed","cancelled"} else 0
    shift_rows=[]
    for item in by_shift.values():
        item["completion_percent"]=round((item["actual"]/item["target"]*100) if item["target"] else 0,1); item["deviation"]=round(item["actual"]-item["target"],3); shift_rows.append(item)
    return {"start_date":start,"end_date":end,"departments":sorted(rows,key=lambda x:str(x["department_name"]).lower()),"shifts":sorted(shift_rows,key=lambda x:str(x.get("planned_start") or ""),reverse=True),"tasks":selected}


def workflow_snapshot(chat_id:int,user_id:int)->dict[str,Any]:
    scope=_scope(chat_id)
    tasks=list_tasks(scope,user_id,limit=100); requests=list_requests(scope,user_id,limit=100); equipment=list_equipment(scope,user_id); lots=list_lots(scope,user_id,100)
    return {
        "tasks":tasks,"requests":requests,"equipment":equipment,"lots":lots,
        "plan_fact":plan_fact_summary(scope,user_id),
        "counts":{
            "active_tasks":sum(1 for x in tasks if x.get("status") in {"planned","in_progress","paused"}),
            "open_requests":sum(1 for x in requests if x.get("status") not in {"received","rejected","cancelled"}),
            "downtimes":sum(int(x.get("open_downtimes") or 0) for x in equipment),
            "maintenance_due":sum(1 for x in equipment if x.get("next_service_at") and (_parse_dt(x.get("next_service_at")) or datetime.max)<=datetime.now()+timedelta(days=int(x.get("warning_before_days") or 0))),
        },
    }


def queue_workflow_reminders(now:datetime|None=None)->int:
    now=now or datetime.now(); created=0
    scopes=[int(r["chat_id"]) for r in db.fetchall("SELECT DISTINCT chat_id FROM production_tasks UNION SELECT DISTINCT chat_id FROM interdepartment_requests UNION SELECT DISTINCT chat_id FROM equipment")]
    day_key=now.strftime("%Y%m%d")
    for scope in scopes:
        # Просроченные задания.
        tasks=db.fetchall("SELECT * FROM production_tasks WHERE chat_id=? AND due_at IS NOT NULL AND due_at<? AND status NOT IN ('completed','cancelled')",(scope,now.strftime("%Y-%m-%d %H:%M:%S")))
        for row in tasks:
            task=dict(row); recipients=_department_manager_ids(scope,int(task["department_id"]));
            if task.get("assignee_user_id"): recipients.append(int(task["assignee_user_id"]))
            for uid in set(recipients):
                if _workflow_once(scope,"task",int(task["id"]),f"overdue:{day_key}",uid):
                    repo.create_inbox_item(scope,uid,"task_overdue",f"Просрочено задание №{task['id']}","Проверьте план, факт и причину отклонения.","production_task",int(task["id"]),deduplicate=False,priority="urgent"); created+=1
        reqs=db.fetchall("SELECT * FROM interdepartment_requests WHERE chat_id=? AND needed_at IS NOT NULL AND needed_at<? AND status NOT IN ('received','rejected','cancelled')",(scope,now.strftime("%Y-%m-%d %H:%M:%S")))
        for row in reqs:
            r=dict(row); recipients=_department_manager_ids(scope,int(r["requester_department_id"]))+_department_manager_ids(scope,int(r["supplier_department_id"]))
            for uid in set(recipients):
                if _workflow_once(scope,"request",int(r["id"]),f"overdue:{day_key}",uid):
                    repo.create_inbox_item(scope,uid,"request_overdue",f"Просрочена заявка №{r['id']}","Срок потребности прошёл, заявка не закрыта.","department_request",int(r["id"]),deduplicate=False,priority="high"); created+=1
        eqs=db.fetchall("SELECT * FROM equipment WHERE chat_id=? AND is_archived=0 AND next_service_at IS NOT NULL",(scope,))
        for row in eqs:
            eq=dict(row); due=_parse_dt(eq.get("next_service_at")); warning=int(eq.get("warning_before_days") or 0)
            if not due or due>now+timedelta(days=warning): continue
            recipients=_department_manager_ids(scope,int(eq["department_id"])) if eq.get("department_id") else repo.list_system_admin_ids(include_owner=True)
            for uid in set(recipients):
                if _workflow_once(scope,"equipment",int(eq["id"]),f"maintenance:{day_key}",uid):
                    priority="urgent" if due<now else "high"
                    repo.create_inbox_item(scope,uid,"maintenance_due",f"Обслуживание: {eq['name']}",f"Срок: {eq['next_service_at']}","equipment",int(eq["id"]),deduplicate=False,priority=priority); created+=1
    return created


def _workflow_once(scope:int,object_type:str,object_id:int,key:str,user_id:int)->bool:
    try:
        db.execute("INSERT INTO workflow_notifications(chat_id,object_type,object_id,notification_key,recipient_user_id) VALUES(?,?,?,?,?)",(scope,object_type,int(object_id),str(key)[:120],int(user_id)))
        return True
    except Exception:
        return False


def validate_operation_context(chat_id:int,user_id:int,*,task_id:int|None=None,lot_id:int|None=None,entity_id:int|None=None,operation_type:str|None=None)->None:
    scope=_scope(chat_id)
    if task_id:
        task=_task_row(scope,int(task_id))
        if not task or not can_view_task(scope,user_id,task) or not _can_work_department(scope,user_id,int(task["department_id"])):
            raise PermissionError("Нет доступа к выбранному заданию.")
        if str(task.get("status") or "") in {"completed","cancelled"}:
            raise ValueError("Задание уже закрыто.")
    if lot_id:
        lot=get_lot(scope,int(lot_id),user_id)
        if not lot:
            raise PermissionError("Нет доступа к выбранной партии.")
        if entity_id and int(lot.get("entity_id") or 0)!=int(entity_id):
            raise ValueError("Выбранная партия относится к другой позиции.")
        lot_status = str(lot.get("status") or "active")
        if lot_status in {"quarantine", "rejected"} and str(operation_type or "") in {"material_out","stock_out","write_off","shipment","shipment_client","shipment_fulfillment","assembly","production"}:
            raise ValueError("Эта партия находится в карантине/заблокирована контролем качества и не может использоваться в этой операции.")


def workflow_options(chat_id:int,user_id:int)->dict[str,Any]:
    scope=_scope(chat_id)
    memberships=repo.user_department_memberships(scope,user_id)
    own=[{"id":int(x["department_id"]),"name":str(x.get("department_name") or "Отдел"),"role_level":int(x.get("role_level") or 0)} for x in memberships]
    destinations=[dict(r) for r in db.fetchall("SELECT id,name FROM departments WHERE chat_id=? AND is_archived=0 ORDER BY name",(scope,))]
    manageable=[]
    for dep in destinations:
        dep_id=int(dep["id"])
        if _can_manage_department(scope,user_id,dep_id):
            members=[dict(r) for r in db.fetchall("SELECT user_id,display_name,role_level FROM department_members WHERE department_id=? AND is_active=1 ORDER BY display_name,user_id",(dep_id,))]
            manageable.append({"id":dep_id,"name":dep["name"],"members":members})
    return {"own_departments":own,"departments":[{"id":int(x["id"]),"name":str(x["name"])} for x in destinations],"manageable_departments":manageable}
