from __future__ import annotations

from aiogram.types import Message

from ..access import can_manage_accounting
from ..keyboards import job_title_choice_keyboard
from ..services import repository as repo


_COMMANDS = {
    "должность",
    "назначить",
    "назначить должность",
    "выдать должность",
    "поставить должность",
}


def _display_name(user) -> str:
    full = " ".join(part for part in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if part).strip()
    username = getattr(user, "username", None)
    if full and username:
        return f"{full} (@{username})"
    if full:
        return full
    if username:
        return f"@{username}"
    return str(getattr(user, "id", "сотрудник"))


def _normalized_command(text: str) -> str:
    return " ".join((text or "").strip().lower().rstrip(".!?:;").split())


async def try_handle_reply_job_assignment(message: Message) -> bool:
    """Open created job titles when a manager replies to a worker's group message."""
    if not message.text or not message.from_user:
        return False
    if message.chat.type not in {"group", "supergroup"}:
        return False
    if _normalized_command(message.text) not in _COMMANDS:
        return False
    reply = message.reply_to_message
    if not reply or not reply.from_user:
        await message.answer("Ответьте на сообщение сотрудника и напишите «должность». Потом выберите нужную должность кнопкой.")
        return True
    if not await can_manage_accounting(message.bot, message.chat, message.from_user):
        await message.answer("У вас нет права назначать должности в этом учёте.")
        return True

    target = reply.from_user
    if getattr(target, "is_bot", False):
        await message.answer("Должность можно назначить сотруднику, а не боту.")
        return True

    chat_id = int(message.chat.id)
    actor_id = int(message.from_user.id)
    jobs = repo.list_job_titles(chat_id)
    copied = 0
    if not jobs:
        copied = repo.copy_job_titles_between_contexts(actor_id, chat_id)
        jobs = repo.list_job_titles(chat_id)
    if not jobs:
        await message.answer("Сначала создайте хотя бы одну должность в настройке учёта.")
        return True

    target_name = _display_name(target)
    data = {
        "group_chat_id": chat_id,
        "group_title": message.chat.title or "рабочая группа",
        "target_user_id": int(target.id),
        "target_name": target_name,
        "target_username": target.username or "",
        "page": 0,
    }
    repo.set_setup_session(actor_id, actor_id, "assign_job_select", data)

    prefix = "Созданные должности перенесены в этот учёт.\n\n" if copied else ""
    await message.answer(
        prefix
        + f"Назначить должность\n\nСотрудник: {target_name}\n\n"
        + "Выберите одну из уже созданных должностей. Вводить название вручную не нужно.",
        reply_markup=job_title_choice_keyboard(jobs, int(target.id), 0),
    )
    return True
