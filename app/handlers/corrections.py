from __future__ import annotations

import re

from aiogram import Router
from aiogram.types import Message

from ..services import accounting
from ..services.normalize import normalize_key
from ..services import repository as repo

router = Router()


_RECENT_WORDS = {"мои записи", "последние записи", "журнал записей", "записи за сегодня"}
_CANCEL_WORDS = {"отменить", "удалить", "убрать", "сторнировать"}
_EDIT_WORDS = {"исправить", "изменить", "поправить"}


def _has_any(key: str, words: set[str]) -> bool:
    return any(word in key for word in words)


def _extract_id(text: str) -> int | None:
    match = re.search(r"(?:№|#)?\s*(\d+)", text)
    return int(match.group(1)) if match else None


def _extract_last_number(text: str) -> float | None:
    matches = re.findall(r"\d+(?:[\.,]\d+)?", text)
    if not matches:
        return None
    return float(matches[-1].replace(",", "."))


def _split_reason(text: str) -> tuple[str, str]:
    match = re.search(r"(?:\bпричина\b|\bпочему\b)\s*[:=-]?\s*(.+)$", text, flags=re.IGNORECASE)
    if not match:
        return text, ""
    return text[:match.start()].strip(), match.group(1).strip()


async def try_handle_correction_command(message: Message) -> bool:
    text = message.text or ""
    key = normalize_key(text)
    if not key:
        return False
    scope_chat_id = repo.resolve_scope_chat_id(message.chat.id)
    user_id = message.from_user.id if message.from_user else 0
    command_text, reason = _split_reason(text)

    if any(word == key for word in _RECENT_WORDS) or ("мои" in key and "запис" in key):
        rows = accounting.list_recent_operations(scope_chat_id, group_chat_id=message.chat.id, user_id=user_id, limit=10)
        await message.answer(accounting.format_recent_operations(rows))
        return True

    if "последн" in key and _has_any(key, _CANCEL_WORDS):
        operation_id = accounting.last_editable_operation_id(scope_chat_id, message.chat.id, user_id)
        if not operation_id:
            await message.answer("Не нашёл вашу последнюю запись для отмены.")
            return True
        if not reason:
            await message.answer("Укажите причину. Например: отменить последнюю причина: ошибочно внесено")
            return True
        ok, msg = accounting.cancel_operation(scope_chat_id, message.chat.id, user_id, operation_id, reason)
        await message.answer(msg)
        return True

    if "последн" in key and _has_any(key, _EDIT_WORDS):
        new_quantity = _extract_last_number(command_text)
        if new_quantity is None:
            await message.answer("Напишите новое количество. Например: исправить последнюю 120")
            return True
        operation_id = accounting.last_editable_operation_id(scope_chat_id, message.chat.id, user_id)
        if not operation_id:
            await message.answer("Не нашёл вашу последнюю запись для исправления.")
            return True
        if not reason:
            await message.answer("Укажите причину. Например: исправить последнюю 120 причина: ошибочный ввод")
            return True
        ok, msg = accounting.change_operation_quantity(scope_chat_id, message.chat.id, user_id, operation_id, new_quantity, reason)
        await message.answer(msg)
        return True

    if "запис" in key and _has_any(key, _CANCEL_WORDS):
        operation_id = _extract_id(command_text)
        if not operation_id:
            await message.answer("Напишите номер записи. Например: отменить запись 12")
            return True
        if not reason:
            await message.answer("Укажите причину. Например: отменить последнюю причина: ошибочно внесено")
            return True
        ok, msg = accounting.cancel_operation(scope_chat_id, message.chat.id, user_id, operation_id, reason)
        await message.answer(msg)
        return True

    if "запис" in key and _has_any(key, _EDIT_WORDS):
        numbers = re.findall(r"\d+(?:[\.,]\d+)?", command_text)
        if len(numbers) < 2:
            await message.answer("Напишите номер записи и новое количество. Например: исправить запись 12 250")
            return True
        operation_id = int(float(numbers[0].replace(",", ".")))
        new_quantity = float(numbers[-1].replace(",", "."))
        if not reason:
            await message.answer("Укажите причину. Например: исправить последнюю 120 причина: ошибочный ввод")
            return True
        ok, msg = accounting.change_operation_quantity(scope_chat_id, message.chat.id, user_id, operation_id, new_quantity, reason)
        await message.answer(msg)
        return True

    return False
