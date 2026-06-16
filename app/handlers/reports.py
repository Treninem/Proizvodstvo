from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message

from .. import db
from ..services.normalize import normalize_key
from ..services import reporting
from ..services import repository as repo
from ..access import can_view_reports
from ..keyboards import export_preferences_keyboard

router = Router()


_REPORT_TRIGGERS = {
    "склад",
    "остатки",
    "склад сырья",
    "склад готовой продукции",
    "отчет",
    "отчёт",
    "отчет за сегодня",
    "отчёт за сегодня",
    "отчет за неделю",
    "отчёт за неделю",
    "отчет за месяц",
    "отчёт за месяц",
    "общий отчет",
    "общий отчёт",
}

_FILE_WORDS = {"файл", "печать", "распечатать", "excel", "xlsx", "pdf", "пдф", "таблица", "выгрузка", "csv", "цсв", "html", "хтмл", "txt", "текстовый", "zip", "архив", "универсальный", "универсал", "телефон", "планшет", "компьютер", "андроид", "android", "айос", "ios", "iphone", "windows", "виндовс", "браузер"}
_REPORT_WORDS = {"отчет", "отчёт", "склад", "остатки", "сводка", "итоги", "движение"}


def _looks_like_capacity_request(text: str) -> bool:
    key = normalize_key(text)
    return "сколько" in key and "собрать" in key


def _looks_like_report_request(text: str) -> bool:
    key = normalize_key(text)
    if key in _REPORT_TRIGGERS:
        return True
    if reporting.looks_like_period_text(text):
        return True
    if _looks_like_capacity_request(text):
        return True
    if any(word in key for word in _FILE_WORDS):
        return True
    return any(word in key for word in _REPORT_WORDS)


def _looks_like_file_request(text: str) -> bool:
    key = normalize_key(text)
    return any(word in key for word in _FILE_WORDS)



@router.callback_query(F.data.startswith("export:"))
async def export_preferences_callback(callback: CallbackQuery) -> None:
    if not await can_view_reports(callback.bot, callback.message.chat, callback.from_user, need_export=True):
        await callback.answer("Недостаточно прав для настройки файла.", show_alert=True)
        return
    if callback.data == "export:done":
        await callback.message.edit_text("Настройка файла сохранена.")
        await callback.answer()
        return
    if callback.data.startswith("export:toggle:"):
        key = callback.data.rsplit(":", 1)[1]
        prefs = repo.get_export_preferences(callback.message.chat.id, callback.from_user.id)
        repo.set_export_preference(callback.message.chat.id, callback.from_user.id, key, not prefs.get(key, True))
        new_prefs = repo.get_export_preferences(callback.message.chat.id, callback.from_user.id)
        await callback.message.edit_text(repo.format_export_preferences(callback.message.chat.id, callback.from_user.id), reply_markup=export_preferences_keyboard(new_prefs))
        await callback.answer()
        return

async def try_handle_report(message: Message) -> bool:
    text = message.text or ""
    key_text = normalize_key(text)
    is_file_settings = key_text in {"настройка файла", "настройки файла", "что включать в файл", "выбор файла", "настроить файл"}
    if not is_file_settings and not _looks_like_report_request(text):
        return False
    if message.chat.type in {"group", "supergroup"} and not repo.is_connected_chat(message.chat.id):
        return False
    need_export = is_file_settings or _looks_like_file_request(text)
    if not await can_view_reports(message.bot, message.chat, message.from_user, need_export=need_export):
        await message.answer("Этот раздел доступен только участнику с подходящей должностью.")
        return True
    if is_file_settings:
        prefs = repo.get_export_preferences(message.chat.id, message.from_user.id if message.from_user else None)
        await message.answer(repo.format_export_preferences(message.chat.id, message.from_user.id if message.from_user else None), reply_markup=export_preferences_keyboard(prefs))
        return True

    scope_chat_id = repo.resolve_scope_chat_id(message.chat.id)

    if _looks_like_capacity_request(text):
        await message.answer(reporting.build_assembly_capacity_report(scope_chat_id, text))
        return True

    period_error = reporting.period_error_for_text(text)
    if period_error:
        await message.answer(period_error)
        return True

    if _looks_like_file_request(text):
        key = normalize_key(text)
        user_id = message.from_user.id if message.from_user else None
        try:
            if any(word in key for word in ("универсал", "все форматы", "все устройства", "для телефона", "телефон", "планшет", "компьютер", "андроид", "android", "айос", "ios", "iphone", "windows", "виндовс", "zip", "архив")):
                path = reporting.create_universal_report_zip(scope_chat_id, text, user_id=user_id)
                await message.answer_document(FSInputFile(path), caption="Универсальный архив готов: Excel, PDF, CSV, HTML и TXT.")
                return True
            if "csv" in key or "цсв" in key:
                path = reporting.create_csv_report(scope_chat_id, text, user_id=user_id)
                await message.answer_document(FSInputFile(path), caption="CSV-файл готов.")
                return True
            if "html" in key or "хтмл" in key or "браузер" in key:
                path = reporting.create_html_report(scope_chat_id, text, user_id=user_id)
                await message.answer_document(FSInputFile(path), caption="HTML-файл готов.")
                return True
            if "txt" in key or "текстовый" in key:
                path = reporting.create_txt_report(scope_chat_id, text, user_id=user_id)
                await message.answer_document(FSInputFile(path), caption="Текстовый файл готов.")
                return True
            if "pdf" in key or "пдф" in key or "печать" in key or "распечатать" in key:
                path = reporting.create_pdf_report(scope_chat_id, text, user_id=user_id)
                await message.answer_document(FSInputFile(path), caption="Файл для печати готов.")
                return True
            path = reporting.create_xlsx_report(scope_chat_id, text, user_id=user_id)
            await message.answer_document(FSInputFile(path), caption="Excel-файл готов.")
            return True
        except ValueError as exc:
            await message.answer(str(exc))
            return True

    report_text = reporting.build_text_report(scope_chat_id, text)
    if len(report_text) > 3900:
        report_text = report_text[:3800].rstrip() + "\n\nОтчёт длинный. Для полного списка запросите файл."
    await message.answer(report_text)
    return True
