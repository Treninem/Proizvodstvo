from __future__ import annotations

from ._safe import safe_edit_text
import secrets
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message

from ..services.normalize import normalize_key
from ..services import reporting
from ..services import repository as repo
from ..access import can_view_reports, is_chat_creator, is_global_owner
from ..keyboards import report_sections_keyboard, report_download_keyboard, report_multi_keyboard, EXPORT_SECTION_LABELS, reports_quick_menu

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

_FILE_WORDS = {"файл", "скачать", "excel", "xlsx", "эксель", "ексель", "pdf", "пдф", "таблица", "выгрузка"}
_UNSUPPORTED_FILE_WORDS = {"csv", "цсв", "html", "хтмл", "txt", "текстовый", "zip", "архив", "универсальный", "универсал", "браузер"}
_REPORT_WORDS = {"отчет", "отчёт", "склад", "остатки", "сводка", "итоги", "движение"}


@dataclass
class ReportState:
    chat_id: int
    scope_chat_id: int
    user_id: int
    request_text: str
    selected: dict[str, bool]
    mode: str = "show"
    selected_scope_ids: set[int] = field(default_factory=set)
    available_scopes: list[dict] = field(default_factory=list)
    scope_titles: dict[int, str] = field(default_factory=dict)


_REPORT_STATES: dict[str, ReportState] = {}


def _token() -> str:
    return secrets.token_urlsafe(6).replace("-", "_")[:8]


def _state_for(token: str) -> ReportState | None:
    return _REPORT_STATES.get(token)


def _looks_like_capacity_request(text: str) -> bool:
    key = normalize_key(text)
    if any(word in key for word in ("собрать", "сбор", "сборки", "сбора", "комплект", "не хватает", "нужно")) and any(
        marker in key for marker in ("сколько", "план", "цель", "цели", "расчет", "расчёт", "нужно", "хватает")
    ):
        return True
    return False


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


def _file_type_from_text(text: str) -> str | None:
    key = normalize_key(text)
    if any(word in key for word in {"excel", "xlsx", "ексель", "эксель", "таблица"}):
        return "xlsx"
    if any(word in key for word in {"pdf", "пдф"}):
        return "pdf"
    return None


def _looks_like_unsupported_file_request(text: str) -> bool:
    key = normalize_key(text)
    return any(word in key for word in _UNSUPPORTED_FILE_WORDS)



def _looks_like_multi_report_request(text: str) -> bool:
    key = normalize_key(text)
    markers = ("по групп", "по чат", "из групп", "из чатов", "нескольк", "общий по", "сводный")
    return any(marker in key for marker in markers) and any(word in key for word in ("отчет", "отчёт", "excel", "pdf", "эксель", "пдф"))


async def _user_can_report_chat(bot, chat_id: int, user_id: int | None) -> bool:
    if not user_id:
        return False
    if is_global_owner(user_id):
        return True
    account = repo.get_active_account(chat_id)
    if account and repo.user_has_account_access(account.id, user_id):
        return True
    if repo.user_has_manage_access_to_chat(chat_id, user_id):
        return True
    return await is_chat_creator(bot, chat_id, user_id)


async def _reportable_scopes(bot, user_id: int | None) -> list[dict]:
    result: list[dict] = []
    seen: set[int] = set()
    for chat in repo.list_known_group_chats(limit=300):
        chat_id = int(chat["chat_id"])
        if not await _user_can_report_chat(bot, chat_id, user_id):
            continue
        account = repo.get_active_account(chat_id)
        scope_chat_id = int(account.scope_chat_id) if account else chat_id
        if scope_chat_id in seen:
            continue
        seen.add(scope_chat_id)
        title = str(account.name if account else (chat.get("title") or chat_id))
        result.append({
            "scope_chat_id": scope_chat_id,
            "source_chat_id": chat_id,
            "title": title,
        })
    return result


def _format_multi_selection_text(state: ReportState) -> str:
    checked = len(state.selected_scope_ids)
    lines = [
        "Отчёт из нескольких групп",
        "",
        "Отметьте только те группы, которые нужны в этом отчёте.",
        f"Выбрано: {checked}",
        f"Период: {state.request_text}",
        "",
        "Ничего не выбрано заранее. После выбора нажмите «Показать», «Excel» или «PDF».",
    ]
    return "\n".join(lines)


def _set_multi_period(state: ReportState, period_key: str) -> None:
    if period_key == "today":
        state.request_text = "отчёт за сегодня"
    elif period_key == "week":
        state.request_text = "отчёт за неделю"
    elif period_key == "month":
        state.request_text = "отчёт за месяц"


async def _start_multi_report_selection(message: Message, request_text: str, user_id: int | None = None) -> tuple[str | None, ReportState | None]:
    if user_id is None:
        user_id = message.from_user.id if message.from_user else 0
    scopes = await _reportable_scopes(message.bot, user_id)
    if not scopes:
        await message.answer("Нет доступных групп для общего отчёта.", reply_markup=reports_quick_menu())
        return None, None
    token = _token()
    selected_scope_ids: set[int] = set()
    titles = {int(item["scope_chat_id"]): str(item.get("title") or item["scope_chat_id"]) for item in scopes}
    state = ReportState(
        chat_id=message.chat.id,
        scope_chat_id=int(scopes[0]["scope_chat_id"]),
        user_id=int(user_id or 0),
        request_text=request_text or "отчёт за месяц",
        selected={key: True for key in EXPORT_SECTION_LABELS},
        mode="multi",
        selected_scope_ids=selected_scope_ids,
        available_scopes=scopes,
        scope_titles=titles,
    )
    _REPORT_STATES[token] = state
    await message.answer(
        _format_multi_selection_text(state),
        reply_markup=report_multi_keyboard(token, scopes, state.selected_scope_ids),
    )
    return token, state

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
    if state.mode == "multi" and state.selected_scope_ids:
        scope_ids = tuple(sorted(state.selected_scope_ids))
        titles = {scope_id: state.scope_titles.get(scope_id, str(scope_id)) for scope_id in scope_ids}
        if file_type == "pdf":
            return reporting.create_multi_pdf_report(scope_ids, state.request_text, titles=titles, user_id=state.user_id)
        return reporting.create_multi_xlsx_report(scope_ids, state.request_text, titles=titles, user_id=state.user_id)
    if file_type == "pdf":
        return reporting.create_pdf_report(state.scope_chat_id, state.request_text, user_id=state.user_id)
    return reporting.create_xlsx_report(state.scope_chat_id, state.request_text, user_id=state.user_id)


def _start_selection(message: Message, scope_chat_id: int, text: str, mode: str, user_id: int | None = None) -> tuple[str, ReportState]:
    if user_id is None:
        user_id = message.from_user.id if message.from_user else 0
    if mode == "download":
        selected = {key: True for key in EXPORT_SECTION_LABELS}
    else:
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




@router.callback_query(F.data == "reportmulti:start")
async def report_multi_start_callback(callback: CallbackQuery) -> None:
    if callback.message.chat.type != "private":
        await callback.answer("Откройте общий отчёт в личке бота.", show_alert=True)
        return
    if not callback.from_user:
        await callback.answer("Не удалось определить пользователя.", show_alert=True)
        return
    token, state = await _start_multi_report_selection(callback.message, "отчёт за месяц", user_id=callback.from_user.id)
    if token and state:
        try:
            await callback.message.delete()
        except Exception:
            pass
    await callback.answer()


@router.callback_query(F.data.startswith("reportmulti:"))
async def report_multi_callback(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        await callback.answer()
        return
    action = parts[1]
    token = parts[2]
    state = _state_for(token)
    if not state or state.mode != "multi":
        await callback.answer("Запрос устарел. Откройте отчёт заново.", show_alert=True)
        return
    if callback.from_user.id != state.user_id:
        await callback.answer("Это не ваш запрос.", show_alert=True)
        return
    if action == "toggle" and len(parts) >= 4:
        try:
            scope_id = int(parts[3])
        except ValueError:
            await callback.answer("Группа не найдена.", show_alert=True)
            return
        allowed = {int(item["scope_chat_id"]) for item in state.available_scopes}
        if scope_id not in allowed:
            await callback.answer("Нет доступа к этой группе.", show_alert=True)
            return
        if scope_id in state.selected_scope_ids:
            state.selected_scope_ids.remove(scope_id)
        else:
            state.selected_scope_ids.add(scope_id)
        await safe_edit_text(
            callback.message,
            _format_multi_selection_text(state),
            reply_markup=report_multi_keyboard(token, state.available_scopes, state.selected_scope_ids),
        )
        await callback.answer()
        return
    if action == "period" and len(parts) >= 4:
        _set_multi_period(state, parts[3])
        await safe_edit_text(
            callback.message,
            _format_multi_selection_text(state),
            reply_markup=report_multi_keyboard(token, state.available_scopes, state.selected_scope_ids),
        )
        await callback.answer()
        return
    if action == "cancel":
        _REPORT_STATES.pop(token, None)
        await safe_edit_text(callback.message, "Отчёт отменён.", reply_markup=reports_quick_menu())
        await callback.answer()
        return
    if action in {"show", "file"}:
        if not state.selected_scope_ids:
            await callback.answer("Отметьте хотя бы одну группу.", show_alert=True)
            return
        if action == "show":
            report_text = reporting.build_multi_text_report(
                tuple(sorted(state.selected_scope_ids)),
                state.request_text,
                titles={scope_id: state.scope_titles.get(scope_id, str(scope_id)) for scope_id in state.selected_scope_ids},
                user_id=state.user_id,
            )
            if len(report_text) > 3900:
                report_text = report_text[:3800].rstrip() + "\n\nПолный отчёт можно скачать в Excel/PDF."
            await safe_edit_text(callback.message, "Отчёт сформирован.")
            await callback.message.answer(report_text, reply_markup=report_multi_keyboard(token, state.available_scopes, state.selected_scope_ids))
            await callback.answer()
            return
        if len(parts) < 4:
            await callback.answer()
            return
        file_type = parts[3]
        try:
            path = _download_path(state, file_type)
        except ValueError as exc:
            await callback.message.answer(str(exc))
            await callback.answer()
            return
        await callback.message.answer_document(FSInputFile(path), caption="Общий отчёт готов.")
        await callback.answer()
        return
    await callback.answer()

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
    if message.chat.type == "private" and _looks_like_multi_report_request(text):
        await _start_multi_report_selection(message, text, user_id=message.from_user.id if message.from_user else 0)
        return True
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

    if _looks_like_unsupported_file_request(text):
        await message.answer("Сейчас доступны только два формата отчёта: Excel и PDF.")
        return True

    requested_file_type = _file_type_from_text(text)
    if requested_file_type:
        state = ReportState(
            chat_id=message.chat.id,
            scope_chat_id=scope_chat_id,
            user_id=message.from_user.id if message.from_user else 0,
            request_text=text,
            selected={key: True for key in EXPORT_SECTION_LABELS},
            mode="download",
        )
        try:
            path = _download_path(state, requested_file_type)
        except ValueError as exc:
            await message.answer(str(exc))
            return True
        await message.answer_document(FSInputFile(path), caption="Отчёт готов.")
        return True

    mode = "download" if _looks_like_file_request(text) else "show"
    token, state = _start_selection(message, scope_chat_id, text, mode)
    if mode == "download":
        await message.answer("Выберите формат отчёта.", reply_markup=report_download_keyboard(token))
    else:
        await message.answer(
            _format_selection_text(state),
            reply_markup=report_sections_keyboard(token, state.selected, "Показать отчёт"),
        )
    return True
