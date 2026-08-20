from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ._safe import safe_edit_text
from . import intake
from ..access import can_submit_operations
from ..keyboards import confirm_keyboard, resolve_operation_keyboard
from ..services import accounting, parser
from ..services import repository as repo
from ..services import worker_places, workplace_pending


router = Router()


def _workplace_keyboard(token: str, places: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for place in places[:50]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=str(place.get("label") or "Рабочее место")[:52],
                    callback_data=f"workplaceop:{token}:{int(place['id'])}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"workplaceop_cancel:{token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _workplace_prompt(places: list[dict]) -> str:
    return (
        "Куда отнести эту рабочую запись?\n\n"
        "У вас назначено несколько рабочих мест. Выберите место для этой записи."
    )


async def _show_after_workplace(message, user_id: int, pending_id: str, payload: dict) -> None:
    unresolved = accounting.first_unresolved_index(payload.get("operations", []))
    if unresolved is None:
        await safe_edit_text(
            message,
            intake._summary_for_payload(payload),
            reply_markup=confirm_keyboard(pending_id),
        )
        return
    choices = intake._choices_for_operation(
        int(payload.get("chat_id") or 0),
        payload.get("operations", [])[unresolved],
        int(user_id),
    )
    if choices:
        await safe_edit_text(
            message,
            intake._choice_text(payload["operations"][unresolved], choices),
            reply_markup=resolve_operation_keyboard(pending_id, unresolved, choices),
        )
        return
    await safe_edit_text(
        message,
        intake._choice_text(payload["operations"][unresolved], choices),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Исправить сообщением", callback_data=f"edit:{pending_id}")],
                [InlineKeyboardButton(text="Отмена", callback_data=f"cancel:{pending_id}")],
            ]
        ),
    )


async def try_handle_workplace_intake(message: Message) -> bool:
    """Handle group work for staff bound to one or more physical workplaces.

    Workers without an explicit workplace keep the legacy intake flow. Workers
    with one workplace are routed there automatically. Workers with multiple
    workplaces choose one before the normal accounting confirmation is created.
    """
    if not message.text or not message.from_user:
        return False
    if message.chat.type not in {"group", "supergroup"}:
        return False

    raw_text = message.text or ""
    text, is_test = intake._test_input(raw_text, int(message.from_user.id))
    if not parser.looks_like_accounting(text):
        return False
    if not repo.is_connected_chat(message.chat.id):
        return False

    repo.upsert_chat(
        message.chat.id,
        message.chat.title or message.chat.full_name or "",
        message.chat.type,
        connected=None,
    )
    scope_chat_id = repo.resolve_scope_chat_id(message.chat.id)
    user_id = int(message.from_user.id)
    places = worker_places.list_worker_workplaces(scope_chat_id, user_id)
    if not places:
        return False

    ops, errors = parser.parse_message(scope_chat_id, message.chat.id, text)
    if not ops:
        return False
    operation_types = {op.operation_type for op in ops if op.operation_type}
    if not await can_submit_operations(message.bot, message.chat, message.from_user, operation_types):
        await message.answer("У вас нет доступа к этому действию.")
        return True
    for op in ops:
        if op.entity_id and repo.department_operation_allowed(
            scope_chat_id,
            user_id,
            op.operation_type,
            "submit",
            op.entity_type,
            op.entity_id,
        ) is False:
            await message.answer("Эта позиция не назначена вашему отделу.")
            return True

    operation_dicts = [op.to_dict() for op in ops]
    payload = {
        "chat_id": scope_chat_id,
        "operations": operation_dicts,
        "raw_text": raw_text,
        "is_test": is_test,
        "workplace_routed": True,
    }

    if len(places) == 1:
        payload["operations"] = worker_places.apply_workplace_to_operations(operation_dicts, places[0])
        payload["workplace_id"] = int(places[0]["id"])
        payload["workplace_label"] = str(places[0].get("label") or "")
        pending_id = accounting.create_pending(
            scope_chat_id,
            message.chat.id,
            user_id,
            payload,
        )
        unresolved = accounting.first_unresolved_index(payload["operations"])
        if unresolved is not None:
            choices = intake._choices_for_operation(scope_chat_id, payload["operations"][unresolved], user_id)
            if choices:
                await message.answer(
                    intake._choice_text(payload["operations"][unresolved], choices),
                    reply_markup=resolve_operation_keyboard(pending_id, unresolved, choices),
                )
            else:
                await message.answer(
                    intake._choice_text(payload["operations"][unresolved], choices),
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="Исправить сообщением", callback_data=f"edit:{pending_id}")],
                            [InlineKeyboardButton(text="Отмена", callback_data=f"cancel:{pending_id}")],
                        ]
                    ),
                )
            return True
        await message.answer(
            intake._summary_for_payload(payload, errors),
            reply_markup=confirm_keyboard(pending_id),
        )
        return True

    token = workplace_pending.create(
        scope_chat_id,
        message.chat.id,
        user_id,
        payload,
    )
    await message.answer(
        _workplace_prompt(places),
        reply_markup=_workplace_keyboard(token, places),
    )
    return True


@router.callback_query(F.data.startswith("workplaceop_cancel:"))
async def cancel_workplace_choice(callback: CallbackQuery) -> None:
    token = str(callback.data or "").split(":", 1)[1]
    workplace_pending.clear(token)
    await safe_edit_text(callback.message, "Рабочая запись отменена.")
    await callback.answer()


@router.callback_query(F.data.startswith("workplaceop:"))
async def choose_workplace(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        return
    parts = str(callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Выбор устарел. Отправьте запись заново.", show_alert=True)
        return
    token = parts[1]
    try:
        workplace_id = int(parts[2])
    except ValueError:
        await callback.answer("Рабочее место не найдено.", show_alert=True)
        return

    group_chat_id = int(callback.message.chat.id)
    scope_chat_id = repo.resolve_scope_chat_id(group_chat_id)
    user_id = int(callback.from_user.id)
    payload = workplace_pending.get(token, scope_chat_id, group_chat_id, user_id)
    if not payload:
        await callback.answer("Запись устарела. Отправьте её заново.", show_alert=True)
        return
    place = worker_places.worker_workplace_by_id(scope_chat_id, user_id, workplace_id)
    if not place:
        workplace_pending.clear(token)
        await callback.answer("Это рабочее место вам больше не назначено.", show_alert=True)
        return

    payload["operations"] = worker_places.apply_workplace_to_operations(
        list(payload.get("operations") or []),
        place,
    )
    payload["workplace_id"] = workplace_id
    payload["workplace_label"] = str(place.get("label") or "")
    pending_id = accounting.create_pending(
        scope_chat_id,
        group_chat_id,
        user_id,
        payload,
    )
    workplace_pending.clear(token)
    await _show_after_workplace(callback.message, user_id, pending_id, payload)
    await callback.answer("Место выбрано")
