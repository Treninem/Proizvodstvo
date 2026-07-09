from __future__ import annotations

from aiogram import Router
from aiogram.types import FSInputFile, Message

from ..access import can_view_reports, is_global_owner
from ..services.command_intents import backup_request_kind
from ..services import backups

router = Router()


async def try_handle_backup(message: Message) -> bool:
    kind = backup_request_kind(message.text or "")
    if kind is None:
        return False

    if kind == "list":
        if not await can_view_reports(message.bot, message.chat, message.from_user, need_export=True):
            await message.answer("Этот раздел доступен только участнику с подходящей должностью.")
            return True
        await message.answer(backups.format_backup_list())
        return True

    if kind == "full":
        if not message.from_user or not is_global_owner(message.from_user.id):
            await message.answer("Команда не распознана.")
            return True
        path = backups.create_full_backup()
        await message.answer_document(FSInputFile(path), caption="Полная копия базы готова.")
        return True

    if not await can_view_reports(message.bot, message.chat, message.from_user, need_export=True):
        await message.answer("Этот раздел доступен только участнику с подходящей должностью.")
        return True
    path = backups.create_account_backup(message.chat.id, message.from_user.id if message.from_user else None)
    await message.answer_document(FSInputFile(path), caption="Копия текущего учёта готова.")
    return True
