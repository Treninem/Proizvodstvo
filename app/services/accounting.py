from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from .. import db
from .normalize import normalize_key
from . import repository as repo


def create_pending(chat_id: int, group_chat_id: int, user_id: int, payload: dict[str, Any], minutes: int = 30) -> str:
    pending_id = uuid.uuid4().hex
    expires = (datetime.utcnow() + timedelta(minutes=minutes)).isoformat()
    db.execute(
        "INSERT INTO pending_confirmations(id,chat_id,group_chat_id,user_id,data_json,expires_at) VALUES(?,?,?,?,?,?)",
        (pending_id, chat_id, group_chat_id, user_id, json.dumps(payload, ensure_ascii=False), expires),
    )
    return pending_id


def get_pending(chat_id: int, group_chat_id: int, user_id: int) -> tuple[str, dict[str, Any]] | None:
    now = datetime.utcnow().isoformat()
    rows = db.fetchall(
        "SELECT * FROM pending_confirmations WHERE chat_id=? AND group_chat_id=? AND user_id=? ORDER BY created_at DESC",
        (chat_id, group_chat_id, user_id),
    )
    for r in rows:
        if r["expires_at"] >= now:
            return r["id"], json.loads(r["data_json"])
    return None


def clear_pending(pending_id: str) -> None:
    db.execute("DELETE FROM pending_confirmations WHERE id=?", (pending_id,))


def _inventory_delta(chat_id: int, area_id: int | None, entity_type: str, entity_id: int, unit: str, delta: float) -> None:
    row = db.fetchone(
        "SELECT quantity FROM inventory WHERE chat_id=? AND ((area_id IS NULL AND ? IS NULL) OR area_id=?) AND entity_type=? AND entity_id=? AND unit=?",
        (chat_id, area_id, area_id, entity_type, entity_id, unit),
    )
    if row:
        db.execute(
            "UPDATE inventory SET quantity=quantity+? WHERE chat_id=? AND ((area_id IS NULL AND ? IS NULL) OR area_id=?) AND entity_type=? AND entity_id=? AND unit=?",
            (delta, chat_id, area_id, area_id, entity_type, entity_id, unit),
        )
    else:
        db.execute(
            "INSERT INTO inventory(chat_id,area_id,entity_type,entity_id,unit,quantity) VALUES(?,?,?,?,?,?)",
            (chat_id, area_id, entity_type, entity_id, unit, delta),
        )



def _remember_confirmed_phrase(chat_id: int, op: dict[str, Any]) -> None:
    phrase = str(op.get("learning_phrase") or "").strip()
    target_type = op.get("entity_type")
    target_id = op.get("entity_id")
    target_name = str(op.get("entity_name") or "").strip()
    if not phrase or not target_type or not target_id or not target_name:
        return
    phrase_key = normalize_key(phrase)
    name_key = normalize_key(target_name)
    if not phrase_key or phrase_key == name_key:
        return
    # Слишком короткие одиночные слова вроде "на" или "по" не запоминаем.
    if len(phrase_key) < 2:
        return
    repo.remember_lexicon(chat_id, phrase, str(target_type), int(target_id))

def _apply_inventory_effect(chat_id: int, op: dict[str, Any]) -> None:
    operation_type = op.get("operation_type")
    entity_type = op.get("entity_type")
    entity_id = op.get("entity_id")
    quantity = op.get("quantity")
    unit = op.get("unit") or "шт"
    area_id = op.get("area_id")
    if quantity is None:
        return
    qty = float(quantity)
    if operation_type == "production" and entity_type and entity_id:
        _inventory_delta(chat_id, None, entity_type, int(entity_id), unit, qty)
    elif operation_type == "material_in" and entity_type == "material" and entity_id:
        _inventory_delta(chat_id, area_id, "material", int(entity_id), unit, qty)
    elif operation_type == "material_out" and entity_type == "material" and entity_id:
        _inventory_delta(chat_id, area_id, "material", int(entity_id), unit, -qty)
    elif operation_type == "assembly" and entity_type == "product" and entity_id:
        _inventory_delta(chat_id, None, "product", int(entity_id), unit, qty)
        for comp in repo.list_product_components(int(entity_id)):
            comp_id = int(comp["component_id"])
            need = float(comp["quantity"] or 0) * qty
            comp_unit = comp.get("default_unit") or "шт"
            _inventory_delta(chat_id, None, "component", comp_id, comp_unit, -need)
    elif operation_type == "shipment" and entity_type == "product" and entity_id:
        _inventory_delta(chat_id, None, "product", int(entity_id), unit, -qty)
    elif operation_type == "stock_in" and entity_type == "stock_item" and entity_id:
        _inventory_delta(chat_id, area_id, "stock_item", int(entity_id), unit, qty)
    elif operation_type == "stock_out" and entity_type == "stock_item" and entity_id:
        _inventory_delta(chat_id, area_id, "stock_item", int(entity_id), unit, -qty)
    elif operation_type == "inventory_adjust" and entity_type and entity_id:
        # Количество в такой записи — это разница между фактическим остатком
        # и тем, что было в базе до сверки.
        _inventory_delta(chat_id, area_id, str(entity_type), int(entity_id), unit, qty)


def _insert_operation(chat_id: int, group_chat_id: int, user_id: int, op: dict[str, Any], raw_text: str) -> int:
    with db.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO operations(chat_id,group_chat_id,area_id,user_id,operation_type,entity_type,entity_id,quantity,unit,raw_text)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                chat_id,
                group_chat_id,
                op.get("area_id"),
                user_id,
                op.get("operation_type"),
                op.get("entity_type"),
                op.get("entity_id"),
                op.get("quantity"),
                op.get("unit") or "шт",
                raw_text,
            ),
        )
        conn.commit()
        operation_id = int(cur.lastrowid)
    _apply_inventory_effect(chat_id, op)
    return operation_id


def apply_operations(chat_id: int, group_chat_id: int, user_id: int, operations: list[dict[str, Any]], raw_text: str = "") -> int:
    saved = 0
    for op in operations:
        if op.get("needs_attention") or op.get("quantity") is None:
            continue
        _insert_operation(chat_id, group_chat_id, user_id, op, raw_text)
        _remember_confirmed_phrase(group_chat_id, op)
        saved += 1
    return saved


def format_summary(operations: list[dict[str, Any]], errors: list[str] | None = None) -> str:
    errors = errors or []
    groups: dict[str, list[str]] = {}
    titles = {
        "production": "Производство",
        "material_in": "Поступление сырья",
        "material_out": "Расход сырья",
        "energy": "Электроэнергия",
        "assembly": "Сборка",
        "shipment": "Отгрузка",
        "stock_in": "Склад: приход",
        "stock_out": "Склад: уход",
        "inventory_adjust": "Инвентаризация",
    }
    for op in operations:
        title = titles.get(op.get("operation_type"), "Запись")
        name = op.get("entity_name") or "нужно уточнить"
        qty = op.get("quantity")
        unit = op.get("unit") or ""
        area = op.get("area_name")
        line = f"• {name} — {qty:g} {unit}" if isinstance(qty, (int, float)) else f"• {name}"
        if area and op.get("operation_type") in {"material_in", "material_out", "energy", "stock_in", "stock_out"}:
            line += f" · {area}"
        if op.get("needs_attention"):
            line += " · нужно уточнить"
        groups.setdefault(title, []).append(line)
    parts = ["Проверьте данные перед сохранением:"]
    for title, lines in groups.items():
        parts.append(f"\n{title}:")
        parts.extend(lines)
    if errors:
        parts.append("\nНужно уточнить:")
        parts.extend(f"• {e}" for e in sorted(set(errors)))
    parts.append("\nВсё верно?")
    return "\n".join(parts)


def _operation_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation_type": row.get("operation_type"),
        "entity_type": row.get("entity_type"),
        "entity_id": row.get("entity_id"),
        "quantity": row.get("quantity"),
        "unit": row.get("unit") or "шт",
        "area_id": row.get("area_id"),
    }


def list_recent_operations(chat_id: int, group_chat_id: int | None = None, user_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
    where = ["o.chat_id=?"]
    params: list[Any] = [chat_id]
    if group_chat_id is not None:
        where.append("o.group_chat_id=?")
        params.append(group_chat_id)
    if user_id is not None:
        where.append("o.user_id=?")
        params.append(user_id)
    where.append("oc.id IS NULL")
    params.append(limit)
    rows = db.fetchall(
        f"""
        SELECT o.id,o.created_at,o.group_chat_id,o.user_id,o.operation_type,o.entity_type,
               e.name AS entity_name,a.name AS area_name,o.quantity,o.unit,o.raw_text
        FROM operations o
        LEFT JOIN operation_corrections oc ON oc.original_operation_id=o.id
        LEFT JOIN entities e ON e.id=o.entity_id
        LEFT JOIN areas a ON a.id=o.area_id
        WHERE {' AND '.join(where)}
        ORDER BY o.created_at DESC
        LIMIT ?
        """,
        tuple(params),
    )
    return [dict(r) for r in rows]


def format_recent_operations(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "Записей пока нет."
    titles = {
        "production": "Производство",
        "material_in": "Поступление сырья",
        "material_out": "Расход сырья",
        "energy": "Электроэнергия",
        "assembly": "Сборка",
        "shipment": "Отгрузка",
        "stock_in": "Склад: приход",
        "stock_out": "Склад: уход",
        "inventory_adjust": "Инвентаризация",
    }
    lines = ["Последние записи:"]
    for row in rows:
        title = titles.get(str(row.get("operation_type") or ""), "Запись")
        name = row.get("entity_name") or "позиция"
        qty = float(row.get("quantity") or 0)
        unit = row.get("unit") or ""
        area = f" · {row['area_name']}" if row.get("area_name") else ""
        lines.append(f"• №{row['id']} · {title}: {name} — {qty:g} {unit}{area}")
    lines.append("\nМожно написать: отменить запись 12 или исправить запись 12 250")
    return "\n".join(lines)


def _find_operation(chat_id: int, operation_id: int) -> dict[str, Any] | None:
    row = db.fetchone("SELECT * FROM operations WHERE chat_id=? AND id=?", (chat_id, operation_id))
    return dict(row) if row else None


def _already_changed(operation_id: int) -> bool:
    row = db.fetchone("SELECT id FROM operation_corrections WHERE original_operation_id=?", (operation_id,))
    return bool(row)


def _can_change_operation(chat_id: int, actor_user_id: int, operation: dict[str, Any]) -> bool:
    if repo.is_global_owner_id(actor_user_id):
        return True
    permissions = repo.user_permissions_current_context(chat_id, actor_user_id)
    if permissions.get("edit"):
        return True
    return int(operation.get("user_id") or 0) == int(actor_user_id)


def cancel_operation(chat_id: int, group_chat_id: int, actor_user_id: int, operation_id: int) -> tuple[bool, str]:
    operation = _find_operation(chat_id, operation_id)
    if not operation:
        return False, "Запись не найдена."
    if _already_changed(operation_id):
        return False, "Эта запись уже исправлена или отменена."
    if not _can_change_operation(chat_id, actor_user_id, operation):
        return False, "Нет права исправлять эту запись."
    reversal = _operation_to_dict(operation)
    reversal["quantity"] = -float(operation.get("quantity") or 0)
    reversal_id = _insert_operation(chat_id, group_chat_id, actor_user_id, reversal, f"Отмена записи №{operation_id}")
    db.execute(
        """
        INSERT INTO operation_corrections(original_operation_id,reversal_operation_id,actor_user_id,correction_type,old_quantity,note)
        VALUES(?,?,?,?,?,?)
        """,
        (operation_id, reversal_id, actor_user_id, "cancel", operation.get("quantity"), "отмена"),
    )
    return True, f"Запись №{operation_id} отменена."


def change_operation_quantity(chat_id: int, group_chat_id: int, actor_user_id: int, operation_id: int, new_quantity: float) -> tuple[bool, str]:
    operation = _find_operation(chat_id, operation_id)
    if not operation:
        return False, "Запись не найдена."
    if _already_changed(operation_id):
        return False, "Эта запись уже исправлена или отменена."
    if not _can_change_operation(chat_id, actor_user_id, operation):
        return False, "Нет права исправлять эту запись."
    if new_quantity < 0:
        return False, "Количество не может быть меньше нуля."
    reversal = _operation_to_dict(operation)
    reversal["quantity"] = -float(operation.get("quantity") or 0)
    replacement = _operation_to_dict(operation)
    replacement["quantity"] = float(new_quantity)
    reversal_id = _insert_operation(chat_id, group_chat_id, actor_user_id, reversal, f"Исправление записи №{operation_id}")
    replacement_id = _insert_operation(chat_id, group_chat_id, actor_user_id, replacement, f"Новая сумма для записи №{operation_id}")
    db.execute(
        """
        INSERT INTO operation_corrections(original_operation_id,reversal_operation_id,replacement_operation_id,actor_user_id,correction_type,old_quantity,new_quantity,note)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (operation_id, reversal_id, replacement_id, actor_user_id, "quantity", operation.get("quantity"), new_quantity, "исправление количества"),
    )
    return True, f"Запись №{operation_id} исправлена: {float(operation.get('quantity') or 0):g} → {new_quantity:g}."


def last_editable_operation_id(chat_id: int, group_chat_id: int, user_id: int) -> int | None:
    rows = list_recent_operations(chat_id, group_chat_id=group_chat_id, user_id=user_id, limit=1)
    if rows:
        return int(rows[0]["id"])
    return None
