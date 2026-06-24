from __future__ import annotations

from ._safe import safe_edit_text
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery

from ..keyboards import confirm_keyboard, resolve_operation_keyboard
from ..access import can_submit_operations
from ..services import accounting
from ..services import parser
from ..services import repository as repo
from ..services.normalize import format_amount

router = Router()


def _allowed_entity_types(operation_type: str | None) -> set[str]:
    if operation_type == "production":
        return {"component", "product", "stock_item"}
    if operation_type in {"material_in", "material_out", "stock_in", "stock_out"}:
        return {"material", "stock_item"}
    if operation_type == "energy":
        return {"meter"}
    if operation_type in {"assembly", "shipment"}:
        return {"product"}
    return {"component", "product", "stock_item", "material", "meter"}


def _choices_for_operation(chat_id: int, operation: dict) -> list[dict]:
    allowed = _allowed_entity_types(operation.get("operation_type"))
    result: list[dict] = []
    seen: set[tuple[str, int]] = set()

    for item in operation.get("variants") or []:
        target_type = str(item.get("target_type") or "")
        target_id = int(item.get("target_id") or 0)
        if target_type not in allowed or not target_id:
            continue
        key = (target_type, target_id)
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "target_type": target_type,
            "target_id": target_id,
            "name": str(item.get("name") or "Позиция"),
        })

    if len(result) < 3:
        for entity in repo.list_entities(chat_id, allowed):
            key = (entity.entity_type, entity.id)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "target_type": entity.entity_type,
                "target_id": entity.id,
                "name": entity.name,
            })
    return result[:40]


def _choice_text(operation: dict, choices: list[dict]) -> str:
    raw_line = str(operation.get("raw_line") or "запись").strip()
    qty = operation.get("quantity")
    unit = operation.get("unit") or "шт"
    qty_line = f"Количество: {format_amount(qty)} {unit}" if isinstance(qty, (int, float)) else "Количество нужно уточнить"
    lines = [
        "Нужно уточнить позицию",
        "",
        f"Строка: {raw_line}",
        qty_line,
        "",
    ]
    if choices:
        lines.append("Выберите подходящее сохранённое название.")
    else:
        lines.append("Подходящих сохранённых названий пока нет. Исправьте сообщение или сначала добавьте позицию в настройке учёта.")
    return "\n".join(lines)


def _apply_selected_entity(chat_id: int, payload: dict, op_index: int, target_type: str, target_id: int) -> bool:
    operations = payload.get("operations") or []
    if op_index < 0 or op_index >= len(operations):
        return False
    entity = repo.get_entity(target_id)
    if not entity or entity.chat_id != repo.resolve_scope_chat_id(chat_id) or entity.entity_type != target_type:
        return False
    op = dict(operations[op_index])
    op_type = str(op.get("operation_type") or "")

    if target_type == "stock_item" and op_type in {"material_in", "stock_in"}:
        op_type = "stock_in"
    elif target_type == "stock_item" and op_type in {"material_out", "stock_out"}:
        op_type = "stock_out"
    elif target_type == "material" and op_type == "stock_in":
        op_type = "material_in"
    elif target_type == "material" and op_type == "stock_out":
        op_type = "material_out"

    op["operation_type"] = op_type
    op["entity_type"] = entity.entity_type
    op["entity_id"] = entity.id
    op["entity_name"] = entity.name
    op["needs_attention"] = False
    op["variants"] = None

    # Для складского прихода/ухода участок нужен только если позиция закреплена за участками.
    if entity.entity_type == "stock_item" and op_type in {"stock_in", "stock_out"}:
        area_ids = repo.list_stock_item_area_ids(entity.id)
        if not area_ids:
            op["area_id"] = None
            op["area_name"] = None
        elif len(area_ids) == 1:
            area = repo.get_area(area_ids[0])
            if area:
                op["area_id"] = area.id
                op["area_name"] = area.name
        elif not op.get("area_id") or int(op.get("area_id")) not in area_ids:
            op["needs_attention"] = True
    operations[op_index] = op
    payload["operations"] = operations
    return True


async def _show_unresolved_or_confirm(message, pending_id: str, payload: dict) -> None:
    operations = payload.get("operations", [])
    unresolved = accounting.first_unresolved_index(operations)
    if unresolved is None:
        await safe_edit_text(message, accounting.format_summary(operations), reply_markup=confirm_keyboard(pending_id))
        return
    choices = _choices_for_operation(payload.get("chat_id") or repo.resolve_scope_chat_id(message.chat.id), operations[unresolved])
    markup = resolve_operation_keyboard(pending_id, unresolved, choices) if choices else confirm_keyboard(pending_id)
    if not choices:
        # Без вариантов кнопку «Да» не показываем, чтобы не сохранялось 0 записей.
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Исправить сообщением", callback_data=f"edit:{pending_id}")],
            [InlineKeyboardButton(text="Отмена", callback_data=f"cancel:{pending_id}")],
        ])
    await safe_edit_text(message, _choice_text(operations[unresolved], choices), reply_markup=markup)


async def try_handle_confirmation_text(message: Message) -> bool:
    text = message.text or ""
    if not (parser.is_yes(text) or parser.is_no(text)):
        return False
    scope_chat_id = repo.resolve_scope_chat_id(message.chat.id)
    found = accounting.get_pending(scope_chat_id, message.chat.id, message.from_user.id if message.from_user else 0)
    if not found:
        return False
    pending_id, payload = found
    if parser.is_no(text):
        accounting.clear_pending(pending_id)
        await message.answer("Запись отменена.")
        return True
    unresolved = accounting.first_unresolved_index(payload.get("operations", []))
    if unresolved is not None:
        choices = _choices_for_operation(scope_chat_id, payload.get("operations", [])[unresolved])
        if choices:
            await message.answer(_choice_text(payload["operations"][unresolved], choices), reply_markup=resolve_operation_keyboard(pending_id, unresolved, choices))
        else:
            await message.answer("Сначала уточните название позиции или добавьте её в настройке учёта.")
        return True
    saved = accounting.apply_operations(scope_chat_id, message.chat.id, message.from_user.id, payload.get("operations", []), payload.get("raw_text", ""))
    accounting.clear_pending(pending_id)
    if saved <= 0:
        await message.answer("Запись не сохранена. Уточните название позиции или отправьте данные заново.")
        return True
    await message.answer(f"Сохранено записей: {saved}")
    return True


async def try_handle_intake(message: Message) -> bool:
    text = message.text or ""
    if not parser.looks_like_accounting(text):
        return False
    if message.chat.type in {"group", "supergroup"} and not repo.is_connected_chat(message.chat.id):
        return False
    chat_id = message.chat.id
    repo.upsert_chat(chat_id, message.chat.title or message.chat.full_name or "", message.chat.type, connected=None)
    scope_chat_id = repo.resolve_scope_chat_id(chat_id)
    ops, errors = parser.parse_message(scope_chat_id, message.chat.id, text)
    if not ops:
        return False
    operation_types = {op.operation_type for op in ops if op.operation_type}
    if not await can_submit_operations(message.bot, message.chat, message.from_user, operation_types):
        await message.answer("Эти данные может сдавать только участник с подходящей должностью.")
        return True
    payload = {"chat_id": scope_chat_id, "operations": [op.to_dict() for op in ops], "raw_text": text}
    pending_id = accounting.create_pending(scope_chat_id, message.chat.id, message.from_user.id if message.from_user else 0, payload)
    unresolved = accounting.first_unresolved_index(payload["operations"])
    if unresolved is not None:
        choices = _choices_for_operation(scope_chat_id, payload["operations"][unresolved])
        if choices:
            await message.answer(_choice_text(payload["operations"][unresolved], choices), reply_markup=resolve_operation_keyboard(pending_id, unresolved, choices))
        else:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            await message.answer(
                _choice_text(payload["operations"][unresolved], choices),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Исправить сообщением", callback_data=f"edit:{pending_id}")],
                    [InlineKeyboardButton(text="Отмена", callback_data=f"cancel:{pending_id}")],
                ]),
            )
        return True
    await message.answer(accounting.format_summary(payload["operations"], errors), reply_markup=confirm_keyboard(pending_id))
    return True


@router.callback_query(F.data.startswith("resolveop:"))
async def resolve_operation(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 5:
        await callback.answer("Откройте запись заново.", show_alert=True)
        return
    pending_id = parts[1]
    op_index = int(parts[2])
    target_type = parts[3]
    target_id = int(parts[4])
    scope_chat_id = repo.resolve_scope_chat_id(callback.message.chat.id)
    found = accounting.get_pending(scope_chat_id, callback.message.chat.id, callback.from_user.id)
    if not found or found[0] != pending_id:
        await callback.answer("Запись не найдена или устарела.", show_alert=True)
        return
    _, payload = found
    payload["chat_id"] = scope_chat_id
    if not _apply_selected_entity(scope_chat_id, payload, op_index, target_type, target_id):
        await callback.answer("Позиция не найдена.", show_alert=True)
        return
    accounting.update_pending(pending_id, payload)
    await _show_unresolved_or_confirm(callback.message, pending_id, payload)
    await callback.answer("Выбрано")


@router.callback_query(F.data.startswith("confirm:"))
async def confirm_pending(callback: CallbackQuery) -> None:
    pending_id = callback.data.split(":", 1)[1]
    scope_chat_id = repo.resolve_scope_chat_id(callback.message.chat.id)
    found = accounting.get_pending(scope_chat_id, callback.message.chat.id, callback.from_user.id)
    if not found or found[0] != pending_id:
        await callback.answer("Запись не найдена или устарела.", show_alert=True)
        return
    _, payload = found
    unresolved = accounting.first_unresolved_index(payload.get("operations", []))
    if unresolved is not None:
        payload["chat_id"] = scope_chat_id
        await _show_unresolved_or_confirm(callback.message, pending_id, payload)
        await callback.answer("Нужно уточнить позицию", show_alert=True)
        return
    saved = accounting.apply_operations(scope_chat_id, callback.message.chat.id, callback.from_user.id, payload.get("operations", []), payload.get("raw_text", ""))
    accounting.clear_pending(pending_id)
    if saved <= 0:
        await safe_edit_text(callback.message, "Запись не сохранена. Уточните название позиции или отправьте данные заново.")
        await callback.answer("Нужно уточнить запись", show_alert=True)
        return
    await safe_edit_text(callback.message, f"Сохранено записей: {saved}")
    await callback.answer()


@router.callback_query(F.data.startswith("cancel:"))
async def cancel_pending(callback: CallbackQuery) -> None:
    pending_id = callback.data.split(":", 1)[1]
    accounting.clear_pending(pending_id)
    await safe_edit_text(callback.message, "Запись отменена.")
    await callback.answer()


@router.callback_query(F.data.startswith("edit:"))
async def edit_pending(callback: CallbackQuery) -> None:
    pending_id = callback.data.split(":", 1)[1] if ":" in (callback.data or "") else ""
    if pending_id:
        accounting.clear_pending(pending_id)
    await callback.answer("Отправьте данные заново одним сообщением.", show_alert=True)
