from __future__ import annotations

from aiogram import Router
from aiogram.types import FSInputFile, Message

from ..access import can_view_reports, is_global_owner
from ..services.normalize import normalize_key
from ..services import backups

router = Router()

_BACKUP_WORDS = {"копия", "копию", "бэкап", "backup", "резерв"}
_FULL_WORDS = {"полная", "полный", "полностью", "база", "базы", "вся"}
_LIST_WORDS = {"список", "последние", "история", "показать"}


def _looks_like_backup(text: str) -> bool:
    key = normalize_key(text)
    return any(word in key for word in _BACKUP_WORDS)


def _wants_full(text: str) -> bool:
    key = normalize_key(text)
    return any(word in key for word in _FULL_WORDS)


def _wants_list(text: str) -> bool:
    key = normalize_key(text)
    return any(word in key for word in _LIST_WORDS)


async def try_handle_backup(message: Message) -> bool:
    text = message.text or ""
    if not _looks_like_backup(text):
        return False
    if _wants_list(text):
        if not await can_view_reports(message.bot, message.chat, message.from_user, need_export=True):
            await message.answer("Этот раздел доступен только участнику с подходящей должностью.")
            return True
        await message.answer(backups.format_backup_list())
        return True
    if _wants_full(text):
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
