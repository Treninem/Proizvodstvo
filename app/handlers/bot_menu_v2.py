from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from ._safe import safe_edit_text
from ..config import settings
from ..services import repository as repo


router = Router()


def _mini_url() -> str:
    base = str(settings.public_base_url or "").rstrip("/")
    return f"{base}/mini?v=20260821a" if base else ""


def bot_main_menu(user_id: int | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    url = _mini_url()
    if url:
        rows.append([InlineKeyboardButton(text="Открыть Mini App", web_app=WebAppInfo(url=url))])
    rows.append([InlineKeyboardButton(text="Рабочие группы", callback_data="menu:chats")])
    rows.append(
        [
            InlineKeyboardButton(text="Мои записи", callback_data="menu:recent"),
            InlineKeyboardButton(text="Отчёты", callback_data="menu:reports"),
        ]
    )
    rows.append([InlineKeyboardButton(text="Как пользоваться", callback_data="menu:help")])
    if repo.is_primary_owner_id(user_id):
        rows.append([InlineKeyboardButton(text="Владелец", callback_data="owner:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _main_text(user_id: int | None) -> str:
    if repo.is_primary_owner_id(user_id):
        return (
            "Производственный учёт\n\n"
            "Работа выполняется в Mini App или через сообщения рабочей группы. "
            "Настройка конкретной организации открывается через «Рабочие группы»."
        )
    return (
        "Производственный учёт\n\n"
        "Откройте Mini App для работы или выберите доступный раздел. "
        "В меню показано только то, что относится к обычной работе."
    )


def _help_text() -> str:
    return (
        "Как пользоваться\n\n"
        "1. Для настройки организации откройте «Рабочие группы», выберите группу и затем её настройку.\n"
        "2. Для ежедневной работы используйте Mini App или пишите производственные записи в рабочей группе.\n"
        "3. Если сотруднику назначено одно рабочее место, его записи автоматически относятся туда.\n"
        "4. Если назначено несколько мест, бот спросит, куда отнести конкретную запись.\n\n"
        "Назначение сотрудника:\n"
        "• ответьте на его сообщение в рабочей группе командой /role;\n"
        "• либо ответьте: @ProChckbot назначить должность;\n"
        "• выберите должность;\n"
        "• выберите одно или несколько рабочих мест;\n"
        "• сохраните.\n\n"
        "Обычная фраза «назначить должность» без /role или упоминания тоже поддерживается, "
        "но Telegram передаёт её боту только когда для бота отключён режим приватности группы. "
        "Команда /role работает при стандартном режиме приватности Telegram."
    )


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="menu:main")]]
    )


@router.message(CommandStart())
async def start_v2(message: Message) -> None:
    repo.upsert_chat(
        message.chat.id,
        message.chat.title or message.chat.full_name or "",
        message.chat.type,
        connected=None,
    )
    if message.chat.type != "private":
        await message.answer(
            "Для назначения сотрудника ответьте на его сообщение командой /role. "
            "Настройка организации открывается в личке бота через «Рабочие группы»."
        )
        return
    user_id = int(message.from_user.id) if message.from_user else None
    await message.answer(_main_text(user_id), reply_markup=bot_main_menu(user_id))


@router.message(Command("help"))
async def help_command_v2(message: Message) -> None:
    await message.answer(_help_text(), reply_markup=_back_keyboard() if message.chat.type == "private" else None)


@router.message(F.text.lower().in_({"меню", "главное меню"}))
async def menu_text_v2(message: Message) -> None:
    if message.chat.type != "private":
        return
    user_id = int(message.from_user.id) if message.from_user else None
    await message.answer(_main_text(user_id), reply_markup=bot_main_menu(user_id))


@router.callback_query(F.data == "menu:main")
async def main_callback_v2(callback: CallbackQuery) -> None:
    user_id = int(callback.from_user.id) if callback.from_user else None
    await safe_edit_text(callback.message, _main_text(user_id), reply_markup=bot_main_menu(user_id))
    await callback.answer()


@router.callback_query(F.data == "menu:help")
async def help_callback_v2(callback: CallbackQuery) -> None:
    await safe_edit_text(callback.message, _help_text(), reply_markup=_back_keyboard())
    await callback.answer()


@router.message(F.text.lower().in_({"как пользоваться", "помощь", "инструкция"}))
async def help_text_v2(message: Message) -> None:
    await message.answer(_help_text(), reply_markup=_back_keyboard() if message.chat.type == "private" else None)
