from __future__ import annotations

from . import accounting
from . import repository as repo


def approve_session(chat_id: int, session_id: int, actor_user_id: int, note: str = "") -> tuple[bool, str, int]:
    scope = repo.resolve_scope_chat_id(chat_id)
    session = repo.get_inventory_session(scope, session_id)
    if not session:
        return False, "Инвентаризация не найдена.", 0
    if session.get("status") != "submitted":
        return False, "Инвентаризация не ожидает подтверждения.", 0
    if int(session.get("created_by") or 0) == int(actor_user_id) and not repo.is_tenant_admin(chat_id, actor_user_id):
        return False, "Подтвердить пересчёт должен другой ответственный сотрудник.", 0

    operations: list[dict] = []
    applications: list[tuple[int, float, float]] = []
    for item in session.get("items") or []:
        current = repo.inventory_quantity(
            scope,
            str(item["entity_type"]),
            int(item["entity_id"]),
            str(item.get("unit") or "шт"),
            int(session["area_id"]),
        )
        delta = float(item.get("actual_quantity") or 0) - float(current)
        applications.append((int(item["id"]), float(current), float(delta)))
        if abs(delta) <= 1e-9:
            continue
        operations.append({
            "operation_type": "inventory_adjust",
            "entity_type": str(item["entity_type"]),
            "entity_id": int(item["entity_id"]),
            "entity_name": str(item.get("entity_name") or "Позиция"),
            "quantity": delta,
            "unit": str(item.get("unit") or "шт"),
            "area_id": int(session["area_id"]),
        })

    saved = accounting.apply_operations(
        scope,
        scope,
        actor_user_id,
        operations,
        raw_text=f"Инвентаризация №{session_id}: {(note or session.get('note') or '').strip()}".strip(),
    )
    for item_id, current, delta in applications:
        repo.record_inventory_session_application(session_id, item_id, current, delta)
    ok, message = repo.decide_inventory_session(scope, session_id, actor_user_id, "approved", note)
    if not ok:
        return False, message, saved
    return True, message, saved
