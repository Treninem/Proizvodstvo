from __future__ import annotations

from ._safe import safe_edit_text
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from ..access import can_manage_accounting, can_view_reports
from ..keyboards import main_menu, setup_menu, reports_quick_menu, workers_menu
from ..services.onboarding import build_readiness_text
from ..services import repository as repo
from ..services import reporting

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
    await safe_edit_text(callback.message, build_readiness_text(callback.message.chat.id), reply_markup=setup_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:stock")
async def stock_open(callback: CallbackQuery) -> None:
    if not await can_view_reports(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    scope_chat_id = repo.resolve_scope_chat_id(callback.message.chat.id)
    text = reporting.build_stock_text(scope_chat_id)
    await safe_edit_text(callback.message, text, reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:reports")
async def reports_open(callback: CallbackQuery) -> None:
    if not await can_view_reports(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await safe_edit_text(callback.message, "Выберите период отчёта.", reply_markup=reports_quick_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:workers")
async def workers_open(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    workers = repo.list_workers(callback.message.chat.id)
    if not workers:
        text = (
            "Работники пока не назначены.\n\n"
            "Как назначить должность:\n"
            "1. В рабочей группе ответьте на сообщение нужного человека.\n"
            "2. Напишите: назначить должность\n"
            "3. Выберите должность в личке и подтвердите."
        )
    else:
        lines = ["Работники"]
        for w in workers[:60]:
            lines.append(f"• {w.get('display_name') or w.get('user_id')} — {w.get('job_name') or 'без должности'}")
        lines.append("\nЧтобы назначить должность: ответьте в группе на сообщение человека и напишите: назначить должность")
        text = "\n".join(lines)
    await safe_edit_text(callback.message, text, reply_markup=workers_menu())
    await callback.answer()

