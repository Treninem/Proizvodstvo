from __future__ import annotations

import re

from aiogram import Router
from aiogram.types import Message

from ..access import can_manage_accounting, is_global_owner
from ..services import repository as repo

router = Router()

_CREATE_RE = re.compile(r"^(?:создать|добавить)\s+(общий\s+)?уч[её]т\s+(.+)$", re.IGNORECASE)
_SELECT_RE = re.compile(r"^(?:выбрать|переключить|открыть)\s+уч[её]т\s+(.+)$", re.IGNORECASE)
_ATTACH_RE = re.compile(r"^(?:подключить|привязать)\s+чат\s+к\s+уч[её]ту\s+(.+)$", re.IGNORECASE)


def _extract_account_id(text: str) -> int | None:
    value = text.strip().lstrip("#№").strip()
    return int(value) if value.isdigit() else None


def _find_account_by_name(accounts, name: str):
    from ..services.normalize import normalize_key
    key = normalize_key(name)
    matched = [acc for acc in accounts if acc.normalized == key]
    if len(matched) == 1:
        return matched[0]
    return None


def _account_choices_text(accounts, name: str) -> str | None:
    from ..services.normalize import normalize_key
    key = normalize_key(name)
    matched = [acc for acc in accounts if acc.normalized == key]
    if len(matched) <= 1:
        return None
    lines = ["Найдено несколько учётов с таким названием.", "Выберите нужный по номеру:"]
    for acc in matched[:10]:
        lines.append(f"• №{acc.id} — {acc.name}")
    lines.append("\nНапример: выбрать учёт №1")
    return "\n".join(lines)


def _find_account_for_owner(accounts, name: str):
    account_id = _extract_account_id(name)
    if account_id is not None:
        return repo.get_account_by_id(account_id)
    return _find_account_by_name(accounts, name)


async def try_handle_account_command(message: Message) -> bool:
    text = (message.text or "").strip()
    lowered = text.lower().replace("ё", "е")
    if lowered not in {"учеты", "учет", "мои учеты", "список учетов"} and not (_CREATE_RE.match(text) or _SELECT_RE.match(text) or _ATTACH_RE.match(text)):
        return False

    user_id = message.from_user.id if message.from_user else 0
    repo.upsert_chat(message.chat.id, message.chat.title or message.chat.full_name or "", message.chat.type, connected=None)

    if lowered in {"учеты", "учет", "мои учеты", "список учетов"}:
        await message.answer(repo.account_summary_for_chat(message.chat.id, user_id))
        return True

    create_match = _CREATE_RE.match(text)
    if create_match:
        # В личке новый учёт может создать сам пользователь. В группе — владелец учёта
        # или человек с правом настройки выбранного учёта.
        if message.chat.type in {"group", "supergroup"} and not await can_manage_accounting(message.bot, message.chat, message.from_user):
            await message.answer("Создать учёт может владелец учёта.")
            return True
        is_general = bool(create_match.group(1))
        name = create_match.group(2).strip()
        ok, msg, account_id = repo.create_account(user_id, message.chat.id, name, is_general=is_general)
        if ok and account_id:
            repo.attach_chat_to_account(account_id, message.chat.id, can_manage=True, set_active=True)
        await message.answer(msg + ("\nОн выбран активным для этого чата." if ok else ""))
        return True

    attach_match = _ATTACH_RE.match(text)
    if attach_match:
        if message.chat.type not in {"group", "supergroup"}:
            await message.answer("Эту команду нужно отправить в группе, которую нужно подключить.")
            return True
        if not await can_manage_accounting(message.bot, message.chat, message.from_user):
            await message.answer("Подключить чат к учёту может владелец учёта.")
            return True
        name = attach_match.group(1).strip()
        accounts = repo.list_accounts_for_user(user_id, message.chat.id)
        if is_global_owner(user_id):
            accounts = repo.owner_list_accounts()
            choices_text = _account_choices_text(accounts, name)
            if choices_text:
                await message.answer(choices_text)
                return True
            account = _find_account_for_owner(accounts, name)
        else:
            account = _find_account_by_name(accounts, name)
        if not account:
            await message.answer("Учёт не найден. Сначала создайте его или проверьте название.")
            return True
        repo.set_chat_connected(message.chat.id, message.chat.title or "", message.chat.type, True)
        repo.attach_chat_to_account(account.id, message.chat.id, can_manage=True, set_active=True)
        await message.answer(f"Чат подключён к учёту: {account.name}")
        return True

    select_match = _SELECT_RE.match(text)
    if select_match:
        name = select_match.group(1).strip()
        accounts = repo.list_accounts_for_user(user_id, message.chat.id)
        if is_global_owner(user_id):
            accounts = repo.owner_list_accounts()
            choices_text = _account_choices_text(accounts, name)
            if choices_text:
                await message.answer(choices_text)
                return True
            account = _find_account_for_owner(accounts, name)
        else:
            account = _find_account_by_name(accounts, name)
        if not account:
            await message.answer("Учёт не подключён к этому чату или не найден.")
            return True
        # В группе учёт можно выбрать, если чат уже подключён к нему или у пользователя
        # есть право управлять этим учётом. В личке достаточно личного доступа.
        if message.chat.type in {"group", "supergroup"} and not repo.chat_has_account_access(message.chat.id, account.id):
            if await can_manage_accounting(message.bot, message.chat, message.from_user):
                repo.attach_chat_to_account(account.id, message.chat.id, can_manage=True, set_active=True)
                await message.answer(f"Учёт подключён и выбран: {account.name}")
                return True
            await message.answer("Учёт не подключён к этому чату или не найден.")
            return True
        ok, msg = repo.set_active_account(message.chat.id, account.id, user_id=user_id)
        await message.answer(msg)
        return True

    return False

