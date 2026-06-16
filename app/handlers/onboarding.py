from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from ..access import can_manage_accounting
from ..keyboards import main_menu, setup_menu
from ..services.onboarding import build_readiness_text
from ..services import repository as repo

router = Router()

TRIGGERS = {
    "проверить учёт",
    "проверка учёта",
    "готовность учёта",
    "что настроить",
    "запуск учёта",
    "помощник запуска",
}


async def try_handle_onboarding(message: Message) -> bool:
    text = (message.text or "").strip().lower()
    if text not in TRIGGERS:
        return False
    if message.chat.type in {"group", "supergroup"} and not repo.is_connected_chat(message.chat.id):
        return False
    if not await can_manage_accounting(message.bot, message.chat, message.from_user):
        await message.answer("Проверку может открыть участник с правом настройки учёта.")
        return True
    await message.answer(build_readiness_text(message.chat.id), reply_markup=setup_menu())
    return True


@router.callback_query(F.data == "menu:readiness")
async def readiness_callback(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.message.edit_text(build_readiness_text(callback.message.chat.id), reply_markup=setup_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:stock")
async def stock_stub(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Склад\n\nНапишите «склад» или «остатки», чтобы получить текущий список.\nДля настройки позиций откройте «Настроить учёт».",
        reply_markup=main_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:reports")
async def reports_stub(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Отчёты\n\nМожно написать: «отчёт за сегодня», «отчёт за неделю» или период в формате 12.05.2022-20.06.2022.",
        reply_markup=main_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:workers")
async def workers_stub(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Работники\n\nСоздайте должность, отметьте права и назначьте её нужным участникам.\nДля начала откройте «Настроить учёт».",
        reply_markup=main_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:export")
async def export_stub(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Файл для печати\n\nНапишите «файл для всех устройств за сегодня» или выберите разделы командой «настройка файла».",
        reply_markup=main_menu(),
    )
    await callback.answer()
