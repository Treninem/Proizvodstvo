from __future__ import annotations

from aiogram.types import Message

from ..access import can_submit_operations
from ..keyboards import confirm_keyboard
from ..services import accounting
from ..services import repository as repo
from ..services import inventory_adjustment


async def try_handle_inventory_adjustment(message: Message) -> bool:
    text = message.text or ""
    if not inventory_adjustment.looks_like_inventory_adjustment(text):
        return False
    if message.chat.type in {"group", "supergroup"} and not repo.is_connected_chat(message.chat.id):
        return False
    user_id = message.from_user.id if message.from_user else 0
    if not await can_submit_operations(message.bot, message.chat, message.from_user, {"inventory_adjust"}):
        await message.answer("Инвентаризацию может сохранять только участник с подходящей должностью.")
        return True
    repo.upsert_chat(message.chat.id, message.chat.title or message.chat.full_name or "", message.chat.type, connected=None)
    scope_chat_id = repo.resolve_scope_chat_id(message.chat.id)
    ops, errors = inventory_adjustment.parse_inventory_lines(scope_chat_id, message.chat.id, text)
    if not ops:
        return False
    payload = {"operations": ops, "raw_text": text}
    pending_id = accounting.create_pending(scope_chat_id, message.chat.id, user_id, payload)
    await message.answer(inventory_adjustment.format_inventory_summary(ops, errors), reply_markup=confirm_keyboard(pending_id))
    return True
