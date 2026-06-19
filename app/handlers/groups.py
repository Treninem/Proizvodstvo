from __future__ import annotations

from ._safe import safe_edit_text
import re

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from ..access import can_manage_accounting
from ..keyboards import area_choice_keyboard
from ..services import repository as repo
from ..services.matcher import confident_match

router = Router()


async def try_handle_group_command(message: Message) -> bool:
    text = (message.text or "").strip()
    lowered = text.lower()
    if message.chat.type not in {"group", "supergroup"}:
        if lowered in {"привязать группу", "подключить группу"}:
            await message.answer("Эту команду нужно отправить в рабочей группе.")
            return True
        return False

    direct_area = re.match(r"^(?:привязать|подключить)\s+группу\s+(.+)$", text, flags=re.IGNORECASE)
    simple = lowered in {"привязать группу", "подключить группу"}
    if not direct_area and not simple:
        return False

    if not await can_manage_accounting(message.bot, message.chat, message.from_user):
        await message.answer("Подключить группу может владелец учёта.")
        return True

    repo.set_chat_connected(message.chat.id, message.chat.title or "", message.chat.type, True)

    if direct_area:
        area_text = direct_area.group(1).strip()
        match, variants = confident_match(message.chat.id, area_text, allowed_types={"area"})
        if match:
            repo.bind_chat_to_area(message.chat.id, match.target_id)
            await message.answer(f"Группа подключена и привязана: {match.name}")
            return True
        await message.answer("Группа подключена. Участок не найден. Создайте участок или выберите его позже.")
        return True

    areas = repo.list_areas(message.chat.id)
    if areas:
        await message.answer(
            "Группа подключена. Выберите участок для сырья и электричества.",
            reply_markup=area_choice_keyboard([(a.id, a.name) for a in areas]),
        )
    else:
        await message.answer("Группа подключена. Создайте участок или оставьте группу без участка.")
    return True


@router.callback_query(F.data.startswith("area_bind:"))
async def area_bind(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    raw = callback.data.split(":", 1)[1]
    if raw == "none":
        repo.bind_chat_to_area(callback.message.chat.id, None)
        await safe_edit_text(callback.message, "Группа подключена без участка. Для сырья и электричества бот будет уточнять участок.")
        await callback.answer()
        return
    try:
        area_id = int(raw)
    except ValueError:
        await callback.answer("Не удалось выбрать участок.", show_alert=True)
        return
    area = next((a for a in repo.list_areas(callback.message.chat.id) if a.id == area_id), None)
    if not area:
        await callback.answer("Участок не найден.", show_alert=True)
        return
    repo.bind_chat_to_area(callback.message.chat.id, area_id)
    await safe_edit_text(callback.message, f"Группа подключена и привязана: {area.name}")
    await callback.answer()
