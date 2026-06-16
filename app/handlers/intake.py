from __future__ import annotations

from ._safe import safe_edit_text
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery

from ..keyboards import confirm_keyboard
from ..access import can_submit_operations
from ..services import accounting
from ..services import parser
from ..services import repository as repo

router = Router()


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
    scope_chat_id = repo.resolve_scope_chat_id(message.chat.id)
    saved = accounting.apply_operations(scope_chat_id, message.chat.id, message.from_user.id, payload.get("operations", []), payload.get("raw_text", ""))
    accounting.clear_pending(pending_id)
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
        # Чтобы не засорять чат, отвечаем только на явно похожие записи.
        await message.answer("Эти данные может сдавать только участник с подходящей должностью.")
        return True
    payload = {"operations": [op.to_dict() for op in ops], "raw_text": text}
    pending_id = accounting.create_pending(scope_chat_id, message.chat.id, message.from_user.id if message.from_user else 0, payload)
    await message.answer(accounting.format_summary(payload["operations"], errors), reply_markup=confirm_keyboard(pending_id))
    return True


@router.callback_query(F.data.startswith("confirm:"))
async def confirm_pending(callback: CallbackQuery) -> None:
    pending_id = callback.data.split(":", 1)[1]
    scope_chat_id = repo.resolve_scope_chat_id(callback.message.chat.id)
    found = accounting.get_pending(scope_chat_id, callback.message.chat.id, callback.from_user.id)
    if not found or found[0] != pending_id:
        await callback.answer("Запись не найдена или устарела.", show_alert=True)
        return
    _, payload = found
    scope_chat_id = repo.resolve_scope_chat_id(callback.message.chat.id)
    saved = accounting.apply_operations(scope_chat_id, callback.message.chat.id, callback.from_user.id, payload.get("operations", []), payload.get("raw_text", ""))
    accounting.clear_pending(pending_id)
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
    await callback.answer("Отправьте данные заново одним сообщением.", show_alert=True)
