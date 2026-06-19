from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from ._safe import safe_edit_text
from ..access import is_global_owner
from ..keyboards import area_choice_keyboard, chat_action_keyboard, chat_list_keyboard, main_menu, setup_menu
from ..services import repository as repo

router = Router()
_VISIBLE_MEMBER_STATUSES = {"creator", "administrator"}


async def _can_see_chat(bot, chat_id: int, user_id: int | None) -> bool:
    if not user_id:
        return False
    if is_global_owner(user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if str(member.status) in _VISIBLE_MEMBER_STATUSES:
            return True
    except Exception:
        pass
    return repo.user_has_manage_access_to_chat(chat_id, user_id)


async def _visible_chats(bot, user_id: int | None) -> list[dict]:
    result: list[dict] = []
    for chat in repo.list_known_group_chats(limit=200):
        chat_id = int(chat["chat_id"])
        if await _can_see_chat(bot, chat_id, user_id):
            result.append(chat)
    return result


async def _send_chats(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    chats = await _visible_chats(message.bot, user_id)
    if not chats:
        await message.answer(
            "Групп пока нет. Добавьте бота в нужную группу и откройте этот раздел снова.",
            reply_markup=main_menu(),
        )
        return
    await message.answer(
        "Ваши группы\n\nВыберите группу для настройки.",
        reply_markup=chat_list_keyboard(chats),
    )


@router.my_chat_member()
async def bot_chat_member(update: ChatMemberUpdated) -> None:
    chat = update.chat
    if chat.type not in {"group", "supergroup"}:
        return
    new_status = str(update.new_chat_member.status)
    connected = new_status not in {"left", "kicked"}
    repo.upsert_chat(chat.id, chat.title or "", chat.type, connected=connected)
    if connected:
        try:
            await update.bot.send_message(
                chat.id,
                "Бот добавлен. Для настройки откройте бота в личке и нажмите «Группы».",
            )
        except Exception:
            pass


@router.message(F.text.lower().in_({"мои группы", "группы", "мои чаты", "чаты"}))
async def my_chats_text(message: Message) -> None:
    if message.chat.type != "private":
        await message.answer("Список групп открывается в личке бота.")
        return
    await _send_chats(message)


@router.callback_query(F.data == "menu:chats")
async def my_chats_callback(callback: CallbackQuery) -> None:
    if callback.message.chat.type != "private":
        await callback.answer("Откройте этот раздел в личке бота.", show_alert=True)
        return
    user_id = callback.from_user.id if callback.from_user else None
    chats = await _visible_chats(callback.bot, user_id)
    if not chats:
        await safe_edit_text(
            callback.message,
            "Групп пока нет. Добавьте бота в нужную группу и откройте этот раздел снова.",
            reply_markup=main_menu(),
        )
    else:
        await safe_edit_text(
            callback.message,
            "Ваши группы\n\nВыберите группу для настройки.",
            reply_markup=chat_list_keyboard(chats),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("chatpick:"))
async def chat_pick(callback: CallbackQuery) -> None:
    raw_chat_id = callback.data.split(":", 1)[1]
    try:
        chat_id = int(raw_chat_id)
    except ValueError:
        await callback.answer("Группа не найдена.", show_alert=True)
        return
    if not await _can_see_chat(callback.bot, chat_id, callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    chat = repo.get_chat_info(chat_id)
    if not chat:
        await callback.answer("Группа не найдена.", show_alert=True)
        return
    title = chat.get("title") or str(chat_id)
    connected = bool(chat.get("is_connected"))
    account = repo.get_active_account(chat_id)
    bound = repo.get_bound_area(chat_id)
    text = (
        f"Группа: {title}\n\n"
        f"Состояние: {'подключена' if connected else 'не подключена'}\n"
        f"Учёт: {(account.name if account else 'не выбран')}\n"
        f"Участок: {(bound.name if bound else 'не выбран')}"
    )
    await safe_edit_text(callback.message, text, reply_markup=chat_action_keyboard(chat_id, connected))
    await callback.answer()


@router.callback_query(F.data.startswith("chatopen:"))
async def chat_action(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Не удалось открыть действие.", show_alert=True)
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer("Группа не найдена.", show_alert=True)
        return
    action = parts[2]
    if not await _can_see_chat(callback.bot, chat_id, callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    chat = repo.get_chat_info(chat_id) or {"title": str(chat_id), "chat_type": "supergroup"}
    if action == "connect":
        repo.set_chat_connected(chat_id, str(chat.get("title") or chat_id), str(chat.get("chat_type") or "supergroup"), True)
        await safe_edit_text(
            callback.message,
            "Группа подключена. Выберите участок для сырья и счётчиков или оставьте без участка.",
            reply_markup=area_choice_keyboard([(a.id, a.name) for a in repo.list_areas(chat_id)], prefix=f"remotearea:{chat_id}"),
        )
        await callback.answer()
        return
    if action == "area":
        areas = repo.list_areas(chat_id)
        if not areas:
            await safe_edit_text(callback.message, "Сначала создайте участок.", reply_markup=setup_menu())
        else:
            await safe_edit_text(
                callback.message,
                "Выберите участок для сырья и счётчиков.",
                reply_markup=area_choice_keyboard([(a.id, a.name) for a in areas], prefix=f"remotearea:{chat_id}"),
            )
        await callback.answer()
        return
    if action == "setup":
        await safe_edit_text(callback.message, "Настройка учёта", reply_markup=setup_menu())
        await callback.answer()
        return
    await callback.answer("Не удалось открыть действие.", show_alert=True)


@router.callback_query(F.data.startswith("remotearea:"))
async def remote_area_bind(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Не удалось выбрать участок.", show_alert=True)
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer("Группа не найдена.", show_alert=True)
        return
    raw_area = parts[2]
    if not await _can_see_chat(callback.bot, chat_id, callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    if raw_area == "none":
        repo.bind_chat_to_area(chat_id, None)
        await safe_edit_text(callback.message, "Группа подключена без участка.", reply_markup=chat_action_keyboard(chat_id, True))
        await callback.answer()
        return
    try:
        area_id = int(raw_area)
    except ValueError:
        await callback.answer("Не удалось выбрать участок.", show_alert=True)
        return
    area = next((a for a in repo.list_areas(chat_id) if a.id == area_id), None)
    if not area:
        await callback.answer("Участок не найден.", show_alert=True)
        return
    repo.bind_chat_to_area(chat_id, area_id)
    await safe_edit_text(callback.message, f"Участок выбран: {area.name}", reply_markup=chat_action_keyboard(chat_id, True))
    await callback.answer()
