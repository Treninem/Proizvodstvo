from __future__ import annotations

from ._safe import safe_edit_text
from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from ..keyboards import main_menu, setup_menu
from ..services import repository as repo

router = Router()


@router.message(CommandStart())
async def start(message: Message) -> None:
    repo.upsert_chat(message.chat.id, message.chat.title or message.chat.full_name or "", message.chat.type)
    if message.from_user:
        repo.clear_setup_session(message.chat.id, message.from_user.id)
    if message.chat.type == "private":
        await message.answer(
            "Производственный учёт\n\nУчёт пока пустой. Начните с настройки или подключите рабочую группу.",
            reply_markup=main_menu(),
        )
    else:
        await message.answer("Учёт можно подключить. Владелец учёта может написать: привязать группу")


@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery) -> None:
    await safe_edit_text(callback.message, "Главное меню", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:setup")
async def menu_setup(callback: CallbackQuery) -> None:
    await safe_edit_text(callback.message, "Настройка учёта", reply_markup=setup_menu())
    await callback.answer()
