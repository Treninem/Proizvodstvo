from __future__ import annotations

from ._safe import safe_edit_text
import secrets
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message

from ..services.normalize import normalize_key
from ..services import reporting
from ..services import repository as repo
from ..access import can_view_reports
from ..keyboards import report_sections_keyboard, report_download_keyboard, EXPORT_SECTION_LABELS, reports_quick_menu

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

_FILE_WORDS = {"файл", "скачать", "excel", "xlsx", "pdf", "пдф", "таблица", "выгрузка", "csv", "цсв", "html", "хтмл", "txt", "текстовый", "zip", "архив", "универсальный", "универсал", "телефон", "планшет", "компьютер", "андроид", "android", "айос", "ios", "iphone", "windows", "виндовс", "браузер"}
_REPORT_WORDS = {"отчет", "отчёт", "склад", "остатки", "сводка", "итоги", "движение"}


@dataclass
class ReportState:
    chat_id: int
    scope_chat_id: int
    user_id: int
    request_text: str
    selected: dict[str, bool]
    mode: str = "show"


_REPORT_STATES: dict[str, ReportState] = {}


def _token() -> str:
    return secrets.token_urlsafe(6).replace("-", "_")[:8]


def _state_for(token: str) -> ReportState | None:
    return _REPORT_STATES.get(token)


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


def _format_selection_text(state: ReportState) -> str:
    lines = ["Что показать в отчёте?", ""]
    for key, label in EXPORT_SECTION_LABELS.items():
        mark = "✅" if state.selected.get(key) else "⬜"
        lines.append(f"{mark} {label}")
    lines.append("")
    lines.append("Отметьте нужные разделы.")
    return "\n".join(lines)


def _save_selected(state: ReportState) -> None:
    repo.set_export_preferences(state.scope_chat_id, state.user_id, state.selected)


def _download_path(state: ReportState, file_type: str):
    if file_type == "csv":
        return reporting.create_csv_report(state.scope_chat_id, state.request_text, user_id=state.user_id)
    if file_type == "html":
        return reporting.create_html_report(state.scope_chat_id, state.request_text, user_id=state.user_id)
    if file_type == "txt":
        return reporting.create_txt_report(state.scope_chat_id, state.request_text, user_id=state.user_id)
    if file_type == "pdf":
        return reporting.create_pdf_report(state.scope_chat_id, state.request_text, user_id=state.user_id)
    return reporting.create_xlsx_report(state.scope_chat_id, state.request_text, user_id=state.user_id)


def _start_selection(message: Message, scope_chat_id: int, text: str, mode: str, user_id: int | None = None) -> tuple[str, ReportState]:
    if user_id is None:
        user_id = message.from_user.id if message.from_user else 0
    selected = repo.get_export_preferences(scope_chat_id, user_id)
    token = _token()
    state = ReportState(
        chat_id=message.chat.id,
        scope_chat_id=scope_chat_id,
        user_id=user_id,
        request_text=text,
        selected=selected,
        mode=mode,
    )
    _REPORT_STATES[token] = state
    return token, state



@router.callback_query(F.data.startswith("reportquick:"))
async def report_quick_callback(callback: CallbackQuery) -> None:
    request_text = (callback.data or "").split(":", 1)[1] or "отчёт за сегодня"
    if not await can_view_reports(callback.bot, callback.message.chat, callback.from_user, need_export=False):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    scope_chat_id = repo.resolve_scope_chat_id(callback.message.chat.id)
    token, state = _start_selection(callback.message, scope_chat_id, request_text, "show", user_id=callback.from_user.id)
    await safe_edit_text(callback.message, 
        _format_selection_text(state),
        reply_markup=report_sections_keyboard(token, state.selected, "Показать отчёт"),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("report:"))
async def report_selection_callback(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        await callback.answer()
        return
    action = parts[1]
    token = parts[2]
    state = _state_for(token)
    if not state:
        await callback.answer("Запрос устарел. Запросите отчёт заново.", show_alert=True)
        return
    if callback.from_user.id != state.user_id:
        await callback.answer("Это не ваш запрос.", show_alert=True)
        return
    if action == "toggle" and len(parts) >= 4:
        key = parts[3]
        if key in state.selected:
            state.selected[key] = not state.selected.get(key)
        await safe_edit_text(callback.message, 
            _format_selection_text(state),
            reply_markup=report_sections_keyboard(token, state.selected, "Выбрать формат" if state.mode == "download" else "Показать отчёт"),
        )
        await callback.answer()
        return
    if action == "cancel":
        _REPORT_STATES.pop(token, None)
        await safe_edit_text(callback.message, "Отчёт отменён.", reply_markup=reports_quick_menu())
        await callback.answer()
        return
    if action == "show":
        _save_selected(state)
        if state.mode == "download":
            await safe_edit_text(callback.message, "Выберите формат отчёта.", reply_markup=report_download_keyboard(token))
            await callback.answer()
            return
        report_text = reporting.build_text_report(state.scope_chat_id, state.request_text, user_id=state.user_id)
        if len(report_text) > 3900:
            report_text = report_text[:3800].rstrip() + "\n\nПолный отчёт можно скачать."
        await safe_edit_text(callback.message, "Отчёт сформирован.")
        await callback.message.answer(report_text, reply_markup=report_download_keyboard(token))
        await callback.answer()
        return
    if action == "download":
        await safe_edit_text(callback.message, "Выберите формат отчёта.", reply_markup=report_download_keyboard(token))
        await callback.answer()
        return
    if action == "back":
        await safe_edit_text(
            callback.message,
            _format_selection_text(state),
            reply_markup=report_sections_keyboard(token, state.selected, "Выбрать формат" if state.mode == "download" else "Показать отчёт"),
        )
        await callback.answer()
        return
    if action == "file" and len(parts) >= 4:
        _save_selected(state)
        file_type = parts[3]
        try:
            path = _download_path(state, file_type)
        except ValueError as exc:
            await callback.message.answer(str(exc))
            await callback.answer()
            return
        await callback.message.answer_document(FSInputFile(path), caption="Отчёт готов.")
        await callback.answer()
        return
    await callback.answer()


async def try_handle_report(message: Message) -> bool:
    text = message.text or ""
    if not _looks_like_report_request(text):
        return False
    if message.chat.type in {"group", "supergroup"} and not repo.is_connected_chat(message.chat.id):
        return False
    need_export = _looks_like_file_request(text)
    if not await can_view_reports(message.bot, message.chat, message.from_user, need_export=need_export):
        await message.answer("Этот раздел доступен только участнику с подходящей должностью.")
        return True

    scope_chat_id = repo.resolve_scope_chat_id(message.chat.id)

    if _looks_like_capacity_request(text) and not _looks_like_file_request(text):
        await message.answer(reporting.build_assembly_capacity_report(scope_chat_id, text))
        return True

    period_error = reporting.period_error_for_text(text)
    if period_error:
        await message.answer(period_error)
        return True

    mode = "download" if _looks_like_file_request(text) else "show"
    token, state = _start_selection(message, scope_chat_id, text, mode)
    await message.answer(
        _format_selection_text(state),
        reply_markup=report_sections_keyboard(token, state.selected, "Выбрать формат" if mode == "download" else "Показать отчёт"),
    )
    return True
