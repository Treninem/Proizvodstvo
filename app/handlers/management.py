from __future__ import annotations

import json

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .. import db
from ..access import can_manage_accounting
from ..services import repository as repo
from ..services.normalize import normalize_key
from ._safe import safe_edit_text

router = Router()


def _management_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать место хранения", callback_data="manage:destination:create")],
        [InlineKeyboardButton(text="Переименовать место хранения", callback_data="manage:destination:rename")],
        [InlineKeyboardButton(text="Переименовать должность", callback_data="manage:job:rename")],
        [InlineKeyboardButton(text="Переименовать текущий учёт", callback_data="manage:account:rename")],
        [InlineKeyboardButton(text="Назад", callback_data="menu:setup")],
    ])


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отмена", callback_data="manage:cancel")],
    ])


def _destination_keyboard(items: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items[:60]:
        rows.append([InlineKeyboardButton(
            text=str(item.get("name") or "Место хранения")[:50],
            callback_data=f"manage:destination:pick:{int(item['id'])}",
        )])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="wizard:destination")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _job_keyboard(items: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items[:60]:
        rows.append([InlineKeyboardButton(
            text=str(item.get("name") or "Должность")[:50],
            callback_data=f"manage:job:pick:{int(item['id'])}",
        )])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="wizard:destination")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _current_account(chat_id: int):
    account = repo.get_active_account(chat_id)
    if account:
        return account
    return repo.get_account_by_scope(repo.resolve_scope_chat_id(chat_id))


def _rename_account(chat_id: int, actor_user_id: int, account_id: int, name: str) -> tuple[bool, str]:
    account = repo.get_account_by_id(account_id)
    if not account:
        return False, "Учёт не найден."
    if not repo.is_tenant_admin(account.scope_chat_id, actor_user_id):
        return False, "Нет права переименовывать этот учёт."
    key = normalize_key(name)
    if not key:
        return False, "Укажите новое название учёта."
    conflict = db.fetchone(
        "SELECT id FROM accounting_accounts WHERE owner_user_id=? AND normalized=? AND id<>? AND is_archived=0",
        (int(account.owner_user_id), key, int(account.id)),
    )
    if conflict:
        return False, "Учёт с таким названием уже есть."
    clean = " ".join(str(name).split()).strip()[:180]
    try:
        with db.connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE accounting_accounts SET name=?,normalized=? WHERE id=? AND is_archived=0",
                (clean, key, int(account.id)),
            )
            conn.execute(
                "UPDATE chats SET title=? WHERE chat_id=?",
                (f"Учёт: {clean}", int(account.scope_chat_id)),
            )
            conn.commit()
        repo.log_site_action(account.scope_chat_id, actor_user_id, "account_rename", clean)
        return True, f"Учёт переименован: {clean}"
    except Exception:
        return False, "Не удалось переименовать учёт. Проверьте название и повторите."


@router.callback_query(F.data == "wizard:destination")
async def open_names_management(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    repo.clear_setup_session(callback.message.chat.id, callback.from_user.id)
    await safe_edit_text(
        callback.message,
        "Места хранения и названия\n\n"
        "Здесь можно создать место хранения или переименовать уже существующее место, должность и текущий учёт.",
        reply_markup=_management_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "manage:cancel")
async def cancel_management(callback: CallbackQuery) -> None:
    repo.clear_setup_session(callback.message.chat.id, callback.from_user.id)
    await safe_edit_text(callback.message, "Действие отменено.", reply_markup=_management_keyboard())
    await callback.answer()


@router.callback_query(F.data == "manage:destination:create")
async def start_destination_create(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    repo.set_setup_session(callback.message.chat.id, callback.from_user.id, "manage_destination_create", {})
    await safe_edit_text(
        callback.message,
        "Введите название нового места хранения.\n\nПример: Склад готовой продукции",
        reply_markup=_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "manage:destination:rename")
async def choose_destination_rename(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    items = repo.list_destinations(callback.message.chat.id, {"storage"})
    if not items:
        await callback.answer("Мест хранения пока нет.", show_alert=True)
        return
    await safe_edit_text(callback.message, "Выберите место хранения для переименования.", reply_markup=_destination_keyboard(items))
    await callback.answer()


@router.callback_query(F.data.startswith("manage:destination:pick:"))
async def start_destination_rename(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    try:
        destination_id = int(callback.data.rsplit(":", 1)[1])
    except Exception:
        await callback.answer("Место не найдено.", show_alert=True)
        return
    item = repo.get_destination(callback.message.chat.id, destination_id)
    if not item:
        await callback.answer("Место не найдено.", show_alert=True)
        return
    repo.set_setup_session(
        callback.message.chat.id,
        callback.from_user.id,
        "manage_destination_rename",
        {"destination_id": destination_id, "destination_type": str(item.get("destination_type") or "storage")},
    )
    await safe_edit_text(
        callback.message,
        f"Текущее название: {item.get('name') or '—'}\n\nВведите новое название места хранения.",
        reply_markup=_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "manage:job:rename")
async def choose_job_rename(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    jobs = repo.list_job_titles(callback.message.chat.id)
    if not jobs:
        await callback.answer("Должностей пока нет.", show_alert=True)
        return
    await safe_edit_text(callback.message, "Выберите должность для переименования.", reply_markup=_job_keyboard(jobs))
    await callback.answer()


@router.callback_query(F.data.startswith("manage:job:pick:"))
async def start_job_rename(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    try:
        job_id = int(callback.data.rsplit(":", 1)[1])
    except Exception:
        await callback.answer("Должность не найдена.", show_alert=True)
        return
    job = next((x for x in repo.list_job_titles(callback.message.chat.id) if int(x.get("id") or 0) == job_id), None)
    if not job:
        await callback.answer("Должность не найдена.", show_alert=True)
        return
    repo.set_setup_session(callback.message.chat.id, callback.from_user.id, "manage_job_rename", {"job_id": job_id})
    await safe_edit_text(
        callback.message,
        f"Текущее название: {job.get('name') or '—'}\n\nВведите новое название должности.",
        reply_markup=_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "manage:account:rename")
async def start_account_rename(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    account = _current_account(callback.message.chat.id)
    if not account:
        await callback.answer("Сначала выберите учёт.", show_alert=True)
        return
    repo.set_setup_session(callback.message.chat.id, callback.from_user.id, "manage_account_rename", {"account_id": int(account.id)})
    await safe_edit_text(
        callback.message,
        f"Текущее название: {account.name}\n\nВведите новое название учёта.",
        reply_markup=_cancel_keyboard(),
    )
    await callback.answer()


async def try_handle_management_message(message: Message) -> bool:
    if not message.text or not message.from_user:
        return False
    chat_id = message.chat.id
    user_id = message.from_user.id
    session = repo.get_setup_session(chat_id, user_id)
    if not session:
        return False
    state = str(session.get("state") or "")
    if not state.startswith("manage_"):
        return False
    if not await can_manage_accounting(message.bot, message.chat, message.from_user):
        repo.clear_setup_session(chat_id, user_id)
        await message.answer("Нет доступа.")
        return True

    text = " ".join(message.text.split()).strip()
    if not text:
        await message.answer("Введите непустое название.", reply_markup=_cancel_keyboard())
        return True
    data = dict(session.get("data") or {})

    if state == "manage_destination_create":
        ok, msg = repo.create_destination(chat_id, text, "storage")
    elif state == "manage_destination_rename":
        ok, msg = repo.update_destination(
            chat_id,
            int(data.get("destination_id") or 0),
            text,
            str(data.get("destination_type") or "storage"),
        )
    elif state == "manage_job_rename":
        job_id = int(data.get("job_id") or 0)
        job = next((x for x in repo.list_job_titles(chat_id) if int(x.get("id") or 0) == job_id), None)
        if not job:
            ok, msg = False, "Должность не найдена."
        else:
            try:
                permissions = json.loads(str(job.get("permissions_json") or "{}"))
            except Exception:
                permissions = {}
            ok, msg = repo.update_job_title_record(chat_id, job_id, text, permissions)
    elif state == "manage_account_rename":
        ok, msg = _rename_account(chat_id, user_id, int(data.get("account_id") or 0), text)
    else:
        return False

    if ok:
        repo.clear_setup_session(chat_id, user_id)
        await message.answer(msg, reply_markup=_management_keyboard())
    else:
        await message.answer(msg, reply_markup=_cancel_keyboard())
    return True
