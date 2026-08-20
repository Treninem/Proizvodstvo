from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ._safe import safe_edit_text
from ..access import can_manage_accounting
from ..services import repository as repo
from ..services import worker_places


router = Router()

_TEXT_COMMANDS = {
    "должность",
    "назначить",
    "назначить должность",
    "выдать должность",
    "поставить должность",
}
_PAGE_SIZE = 12


def _display_name(user) -> str:
    full = " ".join(
        part
        for part in (getattr(user, "first_name", None), getattr(user, "last_name", None))
        if part
    ).strip()
    username = str(getattr(user, "username", None) or "").strip()
    if full and username:
        return f"{full} (@{username})"
    if full:
        return full
    if username:
        return f"@{username}"
    return str(getattr(user, "id", "сотрудник"))


def normalized_assignment_command(text: str) -> str:
    clean = " ".join(str(text or "").strip().split())
    clean = re.sub(r"^@\w+\s+", "", clean, flags=re.IGNORECASE)
    clean = clean.lower().rstrip(".!?:;")
    return " ".join(clean.split())


def _job_keyboard(jobs: list[dict], page: int = 0) -> InlineKeyboardMarkup:
    page = max(0, int(page))
    start = page * _PAGE_SIZE
    rows: list[list[InlineKeyboardButton]] = []
    for job in jobs[start : start + _PAGE_SIZE]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=str(job.get("name") or "Должность")[:50],
                    callback_data=f"role2:job:{int(job['id'])}:{page}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="← Назад", callback_data=f"role2:page:{page - 1}"))
    if start + _PAGE_SIZE < len(jobs):
        nav.append(InlineKeyboardButton(text="Дальше →", callback_data=f"role2:page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="role2:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _place_keyboard(places: list[dict], selected: set[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for place in places[:60]:
        key = str(place.get("key") or "")
        mark = "✅" if key in selected else "⬜"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {str(place.get('label') or 'Рабочее место')[:48]}",
                    callback_data=f"role2:place:{key}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="Выбрать все", callback_data="role2:places_all"),
            InlineKeyboardButton(text="Снять все", callback_data="role2:places_none"),
        ]
    )
    rows.append([InlineKeyboardButton(text=f"Сохранить · выбрано {len(selected)}", callback_data="role2:save")])
    rows.append([InlineKeyboardButton(text="← К должностям", callback_data="role2:change_job")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="role2:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _session(actor_user_id: int) -> dict | None:
    return repo.get_setup_session(int(actor_user_id), int(actor_user_id))


def _save_session(actor_user_id: int, state: str, data: dict) -> None:
    repo.set_setup_session(int(actor_user_id), int(actor_user_id), state, data)


def _clear_session(actor_user_id: int) -> None:
    repo.clear_setup_session(int(actor_user_id), int(actor_user_id))


async def _start_assignment(message: Message) -> bool:
    if not message.from_user or message.chat.type not in {"group", "supergroup"}:
        if message.chat.type == "private":
            await message.answer(
                "Назначение должности делается в рабочей группе: ответьте на сообщение сотрудника командой /role."
            )
            return True
        return False

    reply = message.reply_to_message
    if not reply or not reply.from_user:
        await message.answer(
            "Ответьте именно на сообщение сотрудника и отправьте /role.\n"
            "Также работает фраза «назначить должность», если бот получает обычные сообщения группы."
        )
        return True
    if getattr(reply.from_user, "is_bot", False):
        await message.answer("Должность можно назначить сотруднику, а не боту.")
        return True
    if not await can_manage_accounting(message.bot, message.chat, message.from_user):
        await message.answer("У вас нет права назначать должности в этом учёте.")
        return True

    group_chat_id = int(message.chat.id)
    actor_user_id = int(message.from_user.id)
    jobs = repo.list_job_titles(group_chat_id)
    if not jobs:
        copied = repo.copy_job_titles_between_contexts(actor_user_id, group_chat_id)
        jobs = repo.list_job_titles(group_chat_id)
        if copied and jobs:
            pass
    if not jobs:
        await message.answer("Сначала создайте хотя бы одну должность для этой группы.")
        return True

    target = reply.from_user
    target_name = _display_name(target)
    data = {
        "group_chat_id": group_chat_id,
        "group_title": message.chat.title or "рабочая группа",
        "target_user_id": int(target.id),
        "target_name": target_name,
        "target_username": str(target.username or ""),
        "selected_job_id": 0,
        "selected_job_name": "",
        "selected_places": [],
        "page": 0,
    }
    _save_session(actor_user_id, "role2_job", data)
    await message.answer(
        "Назначение сотрудника\n\n"
        f"Сотрудник: {target_name}\n"
        "Шаг 1 из 2 — выберите должность.",
        reply_markup=_job_keyboard(jobs, 0),
    )
    return True


async def try_handle_reply_job_assignment_v2(message: Message) -> bool:
    if not message.text:
        return False
    if normalized_assignment_command(message.text) not in _TEXT_COMMANDS:
        return False
    return await _start_assignment(message)


@router.message(Command("role", "job"))
async def role_command(message: Message) -> None:
    await _start_assignment(message)


async def _render_jobs(callback: CallbackQuery, data: dict, page: int) -> None:
    jobs = repo.list_job_titles(int(data["group_chat_id"]))
    if not jobs:
        await callback.answer("Должности не найдены.", show_alert=True)
        return
    data["page"] = max(0, int(page))
    _save_session(int(callback.from_user.id), "role2_job", data)
    await safe_edit_text(
        callback.message,
        "Назначение сотрудника\n\n"
        f"Сотрудник: {data.get('target_name')}\n"
        "Шаг 1 из 2 — выберите должность.",
        reply_markup=_job_keyboard(jobs, data["page"]),
    )


async def _render_places(callback: CallbackQuery, data: dict) -> None:
    group_chat_id = int(data["group_chat_id"])
    places = worker_places.list_available_workplaces(group_chat_id)
    if not places:
        await safe_edit_text(
            callback.message,
            "Для этой группы ещё нет рабочих мест.\n\n"
            "Сначала создайте участок и место хранения в организации, затем повторите назначение сотрудника.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="← К должностям", callback_data="role2:change_job")],
                    [InlineKeyboardButton(text="Отмена", callback_data="role2:cancel")],
                ]
            ),
        )
        return
    available = {str(item["key"]) for item in places}
    selected = {str(x) for x in data.get("selected_places") or [] if str(x) in available}
    data["selected_places"] = sorted(selected)
    _save_session(int(callback.from_user.id), "role2_places", data)
    await safe_edit_text(
        callback.message,
        "Назначение сотрудника\n\n"
        f"Сотрудник: {data.get('target_name')}\n"
        f"Должность: {data.get('selected_job_name')}\n\n"
        "Шаг 2 из 2 — выберите одно или несколько рабочих мест.\n"
        "Если место одно, записи сотрудника будут относиться туда автоматически. "
        "Если мест несколько, при рабочей записи бот даст выбор.",
        reply_markup=_place_keyboard(places, selected),
    )


@router.callback_query(F.data.startswith("role2:"))
async def role_assignment_callback(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        return
    actor_user_id = int(callback.from_user.id)
    session = _session(actor_user_id)
    if not session or str(session.get("state") or "") not in {"role2_job", "role2_places"}:
        await callback.answer("Назначение устарело. Ответьте на сообщение сотрудника командой /role ещё раз.", show_alert=True)
        return
    data = dict(session.get("data") or {})
    group_chat_id = int(data.get("group_chat_id") or 0)
    if not group_chat_id:
        _clear_session(actor_user_id)
        await callback.answer("Группа не найдена. Начните назначение заново.", show_alert=True)
        return
    if callback.message.chat.type not in {"group", "supergroup"} or int(callback.message.chat.id) != group_chat_id:
        _clear_session(actor_user_id)
        await callback.answer("Назначение нужно завершить в исходной рабочей группе.", show_alert=True)
        return
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        _clear_session(actor_user_id)
        await callback.answer("Право назначения больше недоступно.", show_alert=True)
        return

    parts = str(callback.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "cancel":
        _clear_session(actor_user_id)
        await safe_edit_text(callback.message, "Назначение отменено.")
        await callback.answer()
        return

    if action == "page":
        page = int(parts[2]) if len(parts) > 2 else 0
        await _render_jobs(callback, data, page)
        await callback.answer()
        return

    if action == "change_job":
        data["selected_job_id"] = 0
        data["selected_job_name"] = ""
        data["selected_places"] = []
        await _render_jobs(callback, data, int(data.get("page") or 0))
        await callback.answer()
        return

    if action == "job":
        if len(parts) < 3:
            await callback.answer("Должность не выбрана.", show_alert=True)
            return
        job_id = int(parts[2])
        job = next((x for x in repo.list_job_titles(group_chat_id) if int(x.get("id") or 0) == job_id), None)
        if not job:
            await callback.answer("Должность не найдена.", show_alert=True)
            return
        data["selected_job_id"] = job_id
        data["selected_job_name"] = str(job.get("name") or "")
        data["selected_places"] = []
        await _render_places(callback, data)
        await callback.answer()
        return

    if session.get("state") != "role2_places":
        await callback.answer("Сначала выберите должность.", show_alert=True)
        return

    places = worker_places.list_available_workplaces(group_chat_id)
    available = {str(item["key"]): item for item in places}
    selected = {str(x) for x in data.get("selected_places") or [] if str(x) in available}

    if action == "place":
        key = parts[2] if len(parts) > 2 else ""
        if key not in available:
            await callback.answer("Рабочее место больше недоступно.", show_alert=True)
            return
        if key in selected:
            selected.remove(key)
        else:
            selected.add(key)
        data["selected_places"] = sorted(selected)
        _save_session(actor_user_id, "role2_places", data)
        await safe_edit_text(
            callback.message,
            "Назначение сотрудника\n\n"
            f"Сотрудник: {data.get('target_name')}\n"
            f"Должность: {data.get('selected_job_name')}\n\n"
            "Выберите одно или несколько рабочих мест.",
            reply_markup=_place_keyboard(places, selected),
        )
        await callback.answer("Выбор изменён")
        return

    if action == "places_all":
        selected = set(available)
        data["selected_places"] = sorted(selected)
        _save_session(actor_user_id, "role2_places", data)
        await safe_edit_text(
            callback.message,
            "Назначение сотрудника\n\n"
            f"Сотрудник: {data.get('target_name')}\n"
            f"Должность: {data.get('selected_job_name')}\n\n"
            "Выбраны все доступные рабочие места.",
            reply_markup=_place_keyboard(places, selected),
        )
        await callback.answer()
        return

    if action == "places_none":
        selected = set()
        data["selected_places"] = []
        _save_session(actor_user_id, "role2_places", data)
        await safe_edit_text(
            callback.message,
            "Назначение сотрудника\n\n"
            f"Сотрудник: {data.get('target_name')}\n"
            f"Должность: {data.get('selected_job_name')}\n\n"
            "Выберите хотя бы одно рабочее место.",
            reply_markup=_place_keyboard(places, selected),
        )
        await callback.answer()
        return

    if action == "save":
        if not selected:
            await callback.answer("Выберите хотя бы одно рабочее место.", show_alert=True)
            return
        target_user_id = int(data.get("target_user_id") or 0)
        job_id = int(data.get("selected_job_id") or 0)
        job = next((x for x in repo.list_job_titles(group_chat_id) if int(x.get("id") or 0) == job_id), None)
        if not target_user_id or not job:
            await callback.answer("Данные назначения устарели. Начните заново.", show_alert=True)
            return

        repo.set_worker_job(
            group_chat_id,
            target_user_id,
            str(data.get("target_name") or target_user_id),
            job_id,
        )
        ok, message = worker_places.set_worker_workplaces(
            group_chat_id,
            target_user_id,
            sorted(selected),
            actor_user_id,
        )
        if not ok:
            await callback.answer(message, show_alert=True)
            return
        assigned = worker_places.list_worker_workplaces(group_chat_id, target_user_id)
        labels = [str(item.get("label") or "Рабочее место") for item in assigned]
        _clear_session(actor_user_id)
        mode_text = (
            "Рабочие записи будут относиться сюда автоматически."
            if len(labels) == 1
            else "При рабочей записи бот будет спрашивать, к какому из этих мест её отнести."
        )
        await safe_edit_text(
            callback.message,
            "Сотрудник назначен\n\n"
            f"Кому: {data.get('target_name')}\n"
            f"Должность: {job.get('name')}\n"
            "Рабочие места:\n• "
            + "\n• ".join(labels)
            + "\n\n"
            + mode_text,
        )
        await callback.answer("Сохранено")
        return

    await callback.answer("Неизвестное действие.", show_alert=True)
