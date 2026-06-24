from __future__ import annotations

from ._safe import safe_edit_text
import re

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from ..access import can_manage_accounting
from ..keyboards import (
    ENTITY_LABELS,
    cancel_keyboard,
    component_alias_keyboard,
    component_choice_keyboard,
    job_assignment_confirm_keyboard,
    job_title_choice_keyboard,
    meter_area_keyboard,
    permission_keyboard,
    product_choice_keyboard,
    product_components_action_keyboard,
    quick_step_keyboard,
    setup_menu,
    skip_alias_keyboard,
    stock_item_area_keyboard,
)
from ..services import repository as repo
from ..services.normalize import normalize_key

router = Router()


def _chat_title(message: Message) -> str:
    return message.chat.title or message.chat.full_name or ""


async def _safe_delete_message(message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def _delete_saved_prompt(bot, chat_id: int, data: dict) -> None:
    prompt_id = data.get("prompt_message_id")
    if not prompt_id:
        return
    try:
        await bot.delete_message(chat_id, int(prompt_id))
    except Exception:
        pass


async def _send_step_message(message: Message, text: str, reply_markup=None):
    await _safe_delete_message(message)
    return await message.answer(text, reply_markup=reply_markup)


def _with_prompt(data: dict | None, message_id: int | None) -> dict:
    payload = dict(data or {})
    if message_id:
        payload["prompt_message_id"] = int(message_id)
    return payload


def _short_list(names: list[str], empty: str = "Пока ничего нет.") -> str:
    clean = [name.strip() for name in names if name and name.strip()]
    if not clean:
        return empty
    shown = clean[:12]
    text = ", ".join(shown)
    if len(clean) > len(shown):
        text += f" и ещё {len(clean) - len(shown)}"
    return text


def _existing_jobs_text(chat_id: int) -> str:
    jobs = repo.list_job_titles(chat_id)
    return "Уже есть: " + _short_list([str(job.get("name") or "") for job in jobs])


def _existing_areas_text(chat_id: int) -> str:
    areas = repo.list_areas(chat_id)
    return "Уже есть: " + _short_list([area.name for area in areas])


def _existing_entities_text(chat_id: int, entity_type: str) -> str:
    items = repo.list_entities(chat_id, {entity_type})
    return "Уже есть: " + _short_list([item.name for item in items])


def _neutral_example_for_entity(entity_type: str) -> str:
    return {
        "product": "Изделие 1",
        "component": "Комплектующая 1",
        "material": "Сырьё 1",
        "stock_item": "Позиция 1",
        "meter": "Счётчик 1",
    }.get(entity_type, "Название 1")


@router.callback_query(F.data.startswith("setup:"))
async def setup_section(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    section = callback.data.split(":", 1)[1]
    if section == "quick":
        repo.set_setup_session(callback.message.chat.id, callback.from_user.id, "quick_area_name", {"prompt_message_id": callback.message.message_id})
        await safe_edit_text(
            callback.message,
            "Быстрая настройка\n\nШаг 1 из 6\nВведите название участка.\n"
            + _existing_areas_text(callback.message.chat.id)
            + "\n\nПример: Участок 1",
            reply_markup=quick_step_keyboard(),
        )
        await callback.answer()
        return
    texts = {
        "areas": "Участки\n\nЗдесь создаются места для сырья и счётчиков.",
        "groups": "Группы\n\nДанные принимаются только из подключённых групп.",
        "jobs": "Должности\n\nСоздайте должность и отметьте нужные права.",
        "items": "Позиции\n\nДобавьте изделия, комплектующие или складские позиции.",
        "materials": "Сырьё\n\nСырьё можно учитывать по участкам.",
        "meters": "Счётчики\n\nСчётчик можно привязать к одному или нескольким участкам.",
    }
    await safe_edit_text(callback.message, texts.get(section, "Настройка учёта"), reply_markup=setup_menu())
    await callback.answer()


def _next_quick_state(state: str, chat_id: int | None = None) -> tuple[str | None, str]:
    existing_jobs = _existing_jobs_text(chat_id) if chat_id is not None else "Уже есть: Пока ничего нет."
    existing_products = _existing_entities_text(chat_id, "product") if chat_id is not None else "Уже есть: Пока ничего нет."
    existing_components = _existing_entities_text(chat_id, "component") if chat_id is not None else "Уже есть: Пока ничего нет."
    existing_materials = _existing_entities_text(chat_id, "material") if chat_id is not None else "Уже есть: Пока ничего нет."
    existing_meters = _existing_entities_text(chat_id, "meter") if chat_id is not None else "Уже есть: Пока ничего нет."
    steps = {
        "quick_area_name": (
            "quick_job_name",
            "Быстрая настройка\n\nШаг 2 из 6\nВведите название должности.\n"
            + existing_jobs
            + "\n\nПример: Смена 1",
        ),
        "quick_job_name": (
            "quick_product_name",
            "Быстрая настройка\n\nШаг 3 из 6\nВведите название изделия.\n"
            + existing_products
            + "\n\nПример: Изделие 1",
        ),
        "quick_product_name": (
            "quick_product_components",
            "Быстрая настройка\n\nШаг 4 из 6\nВведите комплектующие и количество через строки или запятую.\n"
            + existing_components
            + "\n\nПример:\nКомплектующая 1 — 2 шт\nКомплектующая 2 — 1 шт",
        ),
        "quick_product_components": (
            "quick_material_name",
            "Быстрая настройка\n\nШаг 5 из 6\nВведите название сырья.\n"
            + existing_materials
            + "\n\nПример: Сырьё 1",
        ),
        "quick_material_name": (
            "quick_meter_name",
            "Быстрая настройка\n\nШаг 6 из 6\nВведите название счётчика.\n"
            + existing_meters
            + "\n\nПример: Счётчик 1",
        ),
        "quick_meter_name": (None, "Быстрая настройка завершена."),
    }
    return steps.get(state, (None, "Быстрая настройка завершена."))


@router.callback_query(F.data == "quick:skip")
async def quick_skip_callback(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    session = repo.get_setup_session(callback.message.chat.id, callback.from_user.id)
    if not session or not str(session["state"]).startswith("quick_"):
        await callback.answer("Откройте настройку заново.", show_alert=True)
        return
    next_state, prompt = _next_quick_state(session["state"], callback.message.chat.id)
    data = dict(session["data"] or {})
    if next_state is None:
        repo.clear_setup_session(callback.message.chat.id, callback.from_user.id)
        await safe_edit_text(callback.message, prompt, reply_markup=setup_menu())
    else:
        data["prompt_message_id"] = callback.message.message_id
        repo.set_setup_session(callback.message.chat.id, callback.from_user.id, next_state, data)
        await safe_edit_text(callback.message, prompt, reply_markup=quick_step_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("wizard:"))
async def wizard_callback(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    data = callback.data
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    repo.upsert_chat(chat_id, callback.message.chat.title or "", callback.message.chat.type, connected=None)

    if data == "wizard:cancel":
        repo.clear_setup_session(chat_id, user_id)
        await safe_edit_text(callback.message, "Отменено.", reply_markup=setup_menu())
        await callback.answer()
        return

    if data == "wizard:skip_aliases":
        repo.clear_setup_session(chat_id, user_id)
        await safe_edit_text(callback.message, "Сохранено. Выберите следующий пункт.", reply_markup=setup_menu())
        await callback.answer()
        return

    if data == "wizard:area":
        repo.set_setup_session(chat_id, user_id, "await_area_name", {"prompt_message_id": callback.message.message_id})
        await safe_edit_text(
            callback.message,
            "Введите название участка.\n\n"
            "Участок нужен для сырья и счётчиков. Название можно написать любое.\n"
            + _existing_areas_text(chat_id)
            + "\n\nПример: Участок 1",
            reply_markup=cancel_keyboard(),
        )
        await callback.answer()
        return

    if data == "wizard:job":
        repo.set_setup_session(chat_id, user_id, "await_job_name", {"prompt_message_id": callback.message.message_id})
        await safe_edit_text(
            callback.message,
            "Введите название должности.\n\n"
            "После названия бот покажет список прав. Отметьте только нужное.\n"
            + _existing_jobs_text(chat_id)
            + "\n\nПример: Смена 1",
            reply_markup=cancel_keyboard(),
        )
        await callback.answer()
        return

    if data == "wizard:product_components":
        products = repo.list_entities(chat_id, {"product"})
        if not products:
            await safe_edit_text(callback.message, "Сначала добавьте изделие.", reply_markup=setup_menu())
            await callback.answer()
            return
        repo.set_setup_session(chat_id, user_id, "choose_product_for_components", {"prompt_message_id": callback.message.message_id})
        await safe_edit_text(
            callback.message,
            "Выберите изделие.",
            reply_markup=product_choice_keyboard([(product.id, product.name) for product in products]),
        )
        await callback.answer()
        return

    if data.startswith("wizard:entity:"):
        entity_type = data.rsplit(":", 1)[1]
        label = ENTITY_LABELS.get(entity_type, "Позиция")
        repo.set_setup_session(chat_id, user_id, "await_entity_name", {"entity_type": entity_type, "prompt_message_id": callback.message.message_id})
        await safe_edit_text(
            callback.message,
            f"Введите название.\n\nТип: {label}\n"
            "После сохранения можно добавить сокращения и рабочие названия.\n"
            + _existing_entities_text(chat_id, entity_type)
            + f"\n\nПример: {_neutral_example_for_entity(entity_type)}",
            reply_markup=cancel_keyboard(),
        )
        await callback.answer()
        return


def _assignment_job_by_id(group_chat_id: int, job_id: int) -> dict | None:
    for job in repo.list_job_titles(group_chat_id):
        if int(job.get("id") or 0) == int(job_id):
            return job
    return None


def _assignment_text(data: dict, page: int = 0) -> str:
    group_title = str(data.get("group_title") or "рабочая группа")
    target_name = str(data.get("target_name") or data.get("target_user_id") or "участник")
    return (
        "Назначение должности\n\n"
        f"Кому: {target_name}\n"
        f"Группа: {group_title}\n\n"
        "Выберите должность кнопкой. После выбора бот попросит подтвердить действие."
    )


async def _open_assignment_menu(message_or_callback_message, actor_user_id: int, group_chat_id: int, data: dict, page: int = 0) -> None:
    jobs = repo.list_job_titles(group_chat_id)
    if not jobs:
        repo.clear_setup_session(actor_user_id, actor_user_id)
        await safe_edit_text(
            message_or_callback_message,
            "Должностей пока нет. Сначала создайте должность в настройке учёта.",
            reply_markup=setup_menu(),
        )
        return
    data["page"] = int(page)
    repo.set_setup_session(actor_user_id, actor_user_id, "assign_job_select", data)
    await safe_edit_text(
        message_or_callback_message,
        _assignment_text(data, page),
        reply_markup=job_title_choice_keyboard(jobs, int(data["target_user_id"]), page),
    )


@router.callback_query(F.data.startswith("jobassign:"))
async def job_assignment_callback(callback: CallbackQuery) -> None:
    if not callback.from_user:
        await callback.answer("Откройте меню заново.", show_alert=True)
        return
    user_id = callback.from_user.id
    session = repo.get_setup_session(user_id, user_id)
    if not session or not str(session.get("state", "")).startswith("assign_job"):
        await callback.answer("Откройте назначение заново.", show_alert=True)
        return
    data = dict(session.get("data") or {})
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    target_user_id = int(data.get("target_user_id") or 0)
    group_chat_id = int(data.get("group_chat_id") or 0)
    if not target_user_id or not group_chat_id:
        repo.clear_setup_session(user_id, user_id)
        await callback.answer("Откройте назначение заново.", show_alert=True)
        return

    if action == "cancel":
        repo.clear_setup_session(user_id, user_id)
        await _safe_delete_message(callback.message)
        await callback.answer("Отменено.", show_alert=True)
        return

    if action == "page":
        page = int(parts[3]) if len(parts) > 3 else 0
        await _open_assignment_menu(callback.message, user_id, group_chat_id, data, page)
        await callback.answer()
        return

    if action == "change":
        await _open_assignment_menu(callback.message, user_id, group_chat_id, data, int(data.get("page") or 0))
        await callback.answer()
        return

    if action == "pick":
        if len(parts) < 5:
            await callback.answer("Не удалось выбрать должность.", show_alert=True)
            return
        job_id = int(parts[3])
        page = int(parts[4])
        job = _assignment_job_by_id(group_chat_id, job_id)
        if not job:
            await callback.answer("Должность не найдена.", show_alert=True)
            return
        data["selected_job_id"] = job_id
        data["selected_job_name"] = str(job.get("name") or "")
        data["page"] = page
        repo.set_setup_session(user_id, user_id, "assign_job_confirm", data)
        await safe_edit_text(
            callback.message,
            "Проверьте назначение\n\n"
            f"Кому: {data.get('target_name') or target_user_id}\n"
            f"Должность: {job.get('name')}\n\n"
            "Нажмите «Подтвердить», если всё верно.",
            reply_markup=job_assignment_confirm_keyboard(target_user_id, job_id),
        )
        await callback.answer()
        return

    if action == "confirm":
        if len(parts) < 4:
            await callback.answer("Не удалось подтвердить.", show_alert=True)
            return
        job_id = int(parts[3])
        job = _assignment_job_by_id(group_chat_id, job_id)
        if not job:
            await callback.answer("Должность не найдена.", show_alert=True)
            return
        repo.set_worker_job(group_chat_id, target_user_id, str(data.get("target_name") or target_user_id), job_id)
        repo.clear_setup_session(user_id, user_id)
        await _safe_delete_message(callback.message)
        await callback.answer(f"Готово: {job.get('name')}", show_alert=True)
        return

    await callback.answer("Откройте назначение заново.", show_alert=True)


@router.callback_query(F.data.startswith("perm:"))
async def permission_callback(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    session = repo.get_setup_session(chat_id, user_id)
    if not session or session["state"] != "choose_job_permissions":
        await callback.answer("Откройте настройку заново.", show_alert=True)
        return
    data = session["data"]
    permissions: dict[str, bool] = data.get("permissions", {})

    if callback.data.startswith("perm:toggle:"):
        key = callback.data.rsplit(":", 1)[1]
        permissions[key] = not permissions.get(key, False)
        data["permissions"] = permissions
        repo.set_setup_session(chat_id, user_id, "choose_job_permissions", data)
        await safe_edit_text(callback.message, 
            f"Должность: {data.get('name', '')}\n\nОтметьте, что разрешено.",
            reply_markup=permission_keyboard(permissions),
        )
        await callback.answer()
        return

    if callback.data == "perm:save":
        name = data.get("name", "").strip()
        ok, msg = repo.create_job_title(chat_id, name, permissions)
        repo.clear_setup_session(chat_id, user_id)
        await safe_edit_text(callback.message, msg, reply_markup=setup_menu())
        await callback.answer()
        return


def _product_components_text(product_id: int) -> str:
    product = repo.get_entity(product_id)
    if not product:
        return "Изделие не найдено."
    components = repo.list_product_components(product_id)
    lines = [f"Состав изделия: {product.name}"]
    if not components:
        lines.append("Состав пока не задан.")
    else:
        for comp in components:
            lines.append(f"• {comp['name']} — {float(comp['quantity']):g} {comp.get('default_unit') or 'шт'}")
    from ..services import reporting
    capacity = reporting.build_assembly_capacity_report(product.chat_id, f"сколько можно собрать {product.name}")
    lines.append("")
    lines.append(capacity)
    return "\n".join(lines)


@router.callback_query(F.data.startswith("components:"))
async def product_components_callback(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    session = repo.get_setup_session(chat_id, user_id)
    action = (callback.data or "").split(":", 1)[1]

    if action.startswith("product:"):
        if not session or session["state"] != "choose_product_for_components":
            await callback.answer("Откройте настройку заново.", show_alert=True)
            return
        product_id = int(action.rsplit(":", 1)[1])
        product = repo.get_entity(product_id)
        if not product or product.chat_id != repo.resolve_scope_chat_id(chat_id) or product.entity_type != "product":
            await callback.answer("Изделие не найдено.", show_alert=True)
            return
        sent_text = _product_components_text(product_id)
        repo.set_setup_session(chat_id, user_id, "choose_product_components_action", {"product_id": product_id, "product_name": product.name, "prompt_message_id": callback.message.message_id})
        await safe_edit_text(callback.message, sent_text, reply_markup=product_components_action_keyboard())
        await callback.answer()
        return

    if not session or session["state"] not in {"choose_product_components_action", "choose_component_for_quantity", "choose_component_for_remove"}:
        await callback.answer("Откройте настройку заново.", show_alert=True)
        return
    data = dict(session["data"] or {})
    product_id = int(data.get("product_id", 0))
    if action == "finish":
        repo.clear_setup_session(chat_id, user_id)
        await safe_edit_text(callback.message, "Сохранено.", reply_markup=setup_menu())
        await callback.answer()
        return
    if action == "show":
        await safe_edit_text(callback.message, _product_components_text(product_id), reply_markup=product_components_action_keyboard())
        await callback.answer()
        return
    if action == "back_actions":
        repo.set_setup_session(chat_id, user_id, "choose_product_components_action", data)
        await safe_edit_text(callback.message, _product_components_text(product_id), reply_markup=product_components_action_keyboard())
        await callback.answer()
        return

    if action == "replace":
        data["prompt_message_id"] = callback.message.message_id
        repo.set_setup_session(chat_id, user_id, "await_product_components_replace", data)
        await safe_edit_text(callback.message, "Введите полный состав изделия. Старый состав будет заменён.\n\nМожно писать через строки или через запятую.", reply_markup=cancel_keyboard())
        await callback.answer()
        return

    if action == "add":
        data["prompt_message_id"] = callback.message.message_id
        repo.set_setup_session(chat_id, user_id, "await_product_components_add", data)
        await safe_edit_text(callback.message, "Введите комплектующие, которые нужно добавить или обновить.\n\nМожно писать через строки или через запятую.", reply_markup=cancel_keyboard())
        await callback.answer()
        return

    if action == "qty":
        components = repo.list_product_components(product_id)
        if not components:
            await safe_edit_text(callback.message, "Состав пока не задан.", reply_markup=product_components_action_keyboard())
            await callback.answer()
            return
        data["prompt_message_id"] = callback.message.message_id
        repo.set_setup_session(chat_id, user_id, "choose_component_for_quantity", data)
        await safe_edit_text(callback.message, "Выберите комплектующую.", reply_markup=component_choice_keyboard(components, "selectqty"))
        await callback.answer()
        return

    if action == "remove":
        components = repo.list_product_components(product_id)
        if not components:
            await safe_edit_text(callback.message, "Состав пока не задан.", reply_markup=product_components_action_keyboard())
            await callback.answer()
            return
        data["prompt_message_id"] = callback.message.message_id
        repo.set_setup_session(chat_id, user_id, "choose_component_for_remove", data)
        await safe_edit_text(callback.message, "Выберите комплектующую, которую нужно убрать.", reply_markup=component_choice_keyboard(components, "selectremove"))
        await callback.answer()
        return

    if action.startswith("selectqty:"):
        if session["state"] != "choose_component_for_quantity":
            await callback.answer("Откройте настройку заново.", show_alert=True)
            return
        component_id = int(action.rsplit(":", 1)[1])
        component = repo.get_entity(component_id)
        if not component:
            await callback.answer("Комплектующая не найдена.", show_alert=True)
            return
        data["component_id"] = component_id
        data["component_name"] = component.name
        data["prompt_message_id"] = callback.message.message_id
        repo.set_setup_session(chat_id, user_id, "await_selected_component_quantity", data)
        await safe_edit_text(callback.message, f"Введите новое количество для: {component.name}", reply_markup=cancel_keyboard())
        await callback.answer()
        return

    if action.startswith("selectremove:"):
        if session["state"] != "choose_component_for_remove":
            await callback.answer("Откройте настройку заново.", show_alert=True)
            return
        component_id = int(action.rsplit(":", 1)[1])
        removed = repo.remove_product_components(chat_id, product_id, [component_id])
        components = repo.list_product_components(product_id)
        if components:
            repo.set_setup_session(chat_id, user_id, "choose_component_for_remove", data)
            text = "Убрано из состава." if removed else "Не удалось убрать."
            await safe_edit_text(callback.message, text + "\n\nМожно выбрать ещё одну комплектующую.", reply_markup=component_choice_keyboard(components, "selectremove"))
        else:
            repo.set_setup_session(chat_id, user_id, "choose_product_components_action", data)
            await safe_edit_text(callback.message, "Состав пустой.", reply_markup=product_components_action_keyboard())
        await callback.answer()
        return

    await callback.answer()


@router.callback_query(F.data.startswith("meterarea:"))
async def meter_area_callback(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    session = repo.get_setup_session(chat_id, user_id)
    if not session or session["state"] != "choose_meter_areas":
        await callback.answer("Откройте настройку заново.", show_alert=True)
        return
    data = session["data"]
    selected = set(int(x) for x in data.get("area_ids", []))
    areas = repo.list_areas(chat_id)

    if callback.data.startswith("meterarea:toggle:"):
        area_id = int(callback.data.rsplit(":", 1)[1])
        if area_id in selected:
            selected.remove(area_id)
        else:
            selected.add(area_id)
        data["area_ids"] = sorted(selected)
        repo.set_setup_session(chat_id, user_id, "choose_meter_areas", data)
        await safe_edit_text(callback.message, 
            "Выберите участки для счётчика. Можно выбрать один или несколько. Показания будут относиться к прибору, закреплённому за выбранным участком.",
            reply_markup=meter_area_keyboard([(a.id, a.name) for a in areas], selected),
        )
        await callback.answer()
        return

    if callback.data == "meterarea:save":
        meter_id = int(data.get("meter_id", 0))
        repo.bind_meter_to_areas(chat_id, meter_id, sorted(selected))
        repo.set_setup_session(chat_id, user_id, "await_aliases", {"target_type": "meter", "target_id": meter_id, "prompt_message_id": callback.message.message_id})
        names = repo.list_meter_area_names(meter_id)
        text = "Счётчик привязан."
        text += "\nУчастки: " + ", ".join(names) if names else "\nУчастки не выбраны."
        await safe_edit_text(callback.message, 
            text + "\n\nДобавьте сокращения через запятую или с новой строки. Например: короткое название, рабочее название. Можно пропустить.",
            reply_markup=skip_alias_keyboard(),
        )
        await callback.answer()
        return

    if callback.data == "meterarea:skip":
        meter_id = int(data.get("meter_id", 0))
        repo.set_setup_session(chat_id, user_id, "await_aliases", {"target_type": "meter", "target_id": meter_id, "prompt_message_id": callback.message.message_id})
        await safe_edit_text(callback.message, 
            "Счётчик создан без привязки. Его можно привязать к участкам позже.\n\nДобавьте сокращения через запятую или с новой строки. Можно пропустить.",
            reply_markup=skip_alias_keyboard(),
        )
        await callback.answer()
        return


@router.callback_query(F.data.startswith("stockarea:"))
async def stock_item_area_callback(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    session = repo.get_setup_session(chat_id, user_id)
    if not session or session["state"] != "choose_stock_item_areas":
        await callback.answer("Откройте настройку заново.", show_alert=True)
        return
    data = session["data"]
    selected = set(int(x) for x in data.get("area_ids", []))
    areas = repo.list_areas(chat_id)

    if callback.data.startswith("stockarea:toggle:"):
        area_id = int(callback.data.rsplit(":", 1)[1])
        if area_id in selected:
            selected.remove(area_id)
        else:
            selected.add(area_id)
        data["area_ids"] = sorted(selected)
        repo.set_setup_session(chat_id, user_id, "choose_stock_item_areas", data)
        await safe_edit_text(callback.message, 
            "Выберите участки для складской позиции. Можно выбрать один, несколько или оставить общей для всего учёта.",
            reply_markup=stock_item_area_keyboard([(a.id, a.name) for a in areas], selected),
        )
        await callback.answer()
        return

    if callback.data == "stockarea:save":
        stock_item_id = int(data.get("stock_item_id", 0))
        repo.bind_stock_item_to_areas(chat_id, stock_item_id, sorted(selected))
        repo.set_setup_session(chat_id, user_id, "await_aliases", {"target_type": "stock_item", "target_id": stock_item_id, "prompt_message_id": callback.message.message_id})
        names = repo.list_stock_item_area_names(stock_item_id)
        text = "Складская позиция привязана."
        text += "\nУчастки: " + ", ".join(names) if names else "\nПозиция оставлена общей."
        await safe_edit_text(callback.message, 
            text + "\n\nДобавьте сокращения через запятую или с новой строки. Например: короткое название, рабочее название. Можно пропустить.",
            reply_markup=skip_alias_keyboard(),
        )
        await callback.answer()
        return

    if callback.data == "stockarea:skip":
        stock_item_id = int(data.get("stock_item_id", 0))
        repo.bind_stock_item_to_areas(chat_id, stock_item_id, [])
        repo.set_setup_session(chat_id, user_id, "await_aliases", {"target_type": "stock_item", "target_id": stock_item_id, "prompt_message_id": callback.message.message_id})
        await safe_edit_text(callback.message, 
            "Складская позиция оставлена общей для этого учёта.\n\nДобавьте сокращения через запятую или с новой строки. Можно пропустить.",
            reply_markup=skip_alias_keyboard(),
        )
        await callback.answer()
        return


async def _prompt_component_alias_callback(callback: CallbackQuery, data: dict) -> None:
    queue = list(data.get("alias_queue") or [])
    if not queue:
        repo.clear_setup_session(callback.message.chat.id, callback.from_user.id)
        await safe_edit_text(callback.message, "Состав сохранён.", reply_markup=setup_menu())
        await callback.answer()
        return
    item = queue.pop(0)
    data["current"] = item
    data["alias_queue"] = queue
    data["prompt_message_id"] = callback.message.message_id
    repo.set_setup_session(callback.message.chat.id, callback.from_user.id, "await_component_aliases", data)
    await safe_edit_text(callback.message, 
        f"Сокращения для: {item['name']}\n\nМожно написать через запятую или с новой строки.",
        reply_markup=component_alias_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("componentalias:"))
async def component_alias_callback(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    session = repo.get_setup_session(callback.message.chat.id, callback.from_user.id)
    if not session or session["state"] != "await_component_aliases":
        await callback.answer("Откройте настройку заново.", show_alert=True)
        return
    data = session["data"]
    if callback.data == "componentalias:finish":
        repo.clear_setup_session(callback.message.chat.id, callback.from_user.id)
        await safe_edit_text(callback.message, "Состав сохранён.", reply_markup=setup_menu())
        await callback.answer()
        return
    await _prompt_component_alias_callback(callback, data)


async def try_handle_wizard_message(message: Message) -> bool:
    if not message.text or not message.from_user:
        return False
    chat_id = message.chat.id
    user_id = message.from_user.id
    session = repo.get_setup_session(chat_id, user_id)
    if not session:
        return False
    text = message.text.strip()
    state = session["state"]
    data = session["data"]
    repo.upsert_chat(chat_id, _chat_title(message), message.chat.type, connected=None)
    await _delete_saved_prompt(message.bot, chat_id, data)

    if text.lower() in {"отмена", "стоп"}:
        repo.clear_setup_session(chat_id, user_id)
        await _send_step_message(message, "Отменено.", reply_markup=setup_menu())
        return True

    if state.startswith("quick_"):
        skip_words = {"пропустить", "нет", "не надо", "дальше"}
        current = state
        if current == "quick_area_name" and text.lower() not in skip_words:
            if repo.count_active_areas(chat_id) < 999:
                repo.create_area(chat_id, text)
        elif current == "quick_job_name" and text.lower() not in skip_words:
            repo.create_job_title(chat_id, text, repo.full_permissions())
        elif current == "quick_product_name" and text.lower() not in skip_words:
            ok, _ = repo.create_entity(chat_id, "product", text, "шт")
            product = repo.get_entity_by_name(chat_id, "product", text)
            if product:
                data["quick_product_id"] = product.id
        elif current == "quick_product_components" and text.lower() not in skip_words:
            product_id = int(data.get("quick_product_id") or 0)
            if product_id:
                components, errors = _parse_component_lines(chat_id, text, create_missing=True)
                if errors or not components:
                    sent = await _send_step_message(message, "Не удалось сохранить состав. Введите список ещё раз или нажмите «Пропустить».", reply_markup=quick_step_keyboard())
                    data["prompt_message_id"] = sent.message_id
                    repo.set_setup_session(chat_id, user_id, current, data)
                    return True
                repo.set_product_components(chat_id, product_id, components)
        elif current == "quick_material_name" and text.lower() not in skip_words:
            repo.create_entity(chat_id, "material", text, "кг")
        elif current == "quick_meter_name" and text.lower() not in skip_words:
            repo.create_entity(chat_id, "meter", text, "кВт⋅ч")

        next_state, prompt = _next_quick_state(current, chat_id)
        if next_state is None:
            repo.clear_setup_session(chat_id, user_id)
            await _send_step_message(message, prompt, reply_markup=setup_menu())
        else:
            sent = await _send_step_message(message, prompt, reply_markup=quick_step_keyboard())
            data["prompt_message_id"] = sent.message_id
            repo.set_setup_session(chat_id, user_id, next_state, data)
        return True

    if state == "await_area_name":
        if repo.count_active_areas(chat_id) >= 999:
            repo.clear_setup_session(chat_id, user_id)
            await _send_step_message(message, "Достигнут предел: 999 участков.", reply_markup=setup_menu())
            return True
        ok, msg = repo.create_area(chat_id, text)
        if not ok:
            await _send_step_message(message, msg)
            return True
        from ..services.matcher import confident_match
        match, _ = confident_match(chat_id, text, allowed_types={"area"})
        if match:
            sent = await _send_step_message(message, msg + "\n\nДобавьте сокращения через запятую или с новой строки. Бот будет понимать эти варианты при вводе данных. Можно пропустить.", reply_markup=skip_alias_keyboard())
            repo.set_setup_session(chat_id, user_id, "await_aliases", {"target_type": "area", "target_id": match.target_id, "prompt_message_id": sent.message_id})
        else:
            repo.clear_setup_session(chat_id, user_id)
            await _send_step_message(message, msg, reply_markup=setup_menu())
        return True

    if state == "await_job_name":
        sent = await _send_step_message(
            message,
            f"Должность: {text}\n\nОтметьте, что разрешено. Потом нажмите «Сохранить должность».\n\n"
            "Подсказка: обычному работнику обычно хватает сдачи данных, мастеру — отчётов и исправлений.",
            reply_markup=permission_keyboard({}),
        )
        repo.set_setup_session(chat_id, user_id, "choose_job_permissions", {"name": text, "permissions": {}, "prompt_message_id": sent.message_id})
        return True

    if state == "await_product_for_components":
        from ..services.matcher import confident_match
        match, _ = confident_match(chat_id, text, allowed_types={"product"})
        if not match:
            await _send_step_message(message, "Изделие не найдено. Сначала добавьте изделие или уточните название.", reply_markup=setup_menu())
            repo.clear_setup_session(chat_id, user_id)
            return True
        sent = await _send_step_message(
            message,
            _product_components_text(match.target_id),
            reply_markup=product_components_action_keyboard(),
        )
        repo.set_setup_session(chat_id, user_id, "choose_product_components_action", {"product_id": match.target_id, "product_name": match.name, "prompt_message_id": sent.message_id})
        return True

    if state in {"await_product_components", "await_product_components_replace", "await_product_components_add", "await_product_component_quantity"}:
        product_id = int(data.get("product_id", 0))
        product = repo.get_entity(product_id)
        if not product:
            repo.clear_setup_session(chat_id, user_id)
            await _send_step_message(message, "Изделие не найдено. Откройте настройку заново.", reply_markup=setup_menu())
            return True
        components, errors = _parse_component_lines(chat_id, text, create_missing=True)
        if errors or not components:
            await _send_step_message(message, "Не удалось сохранить состав.\n" + "\n".join(f"• {e}" for e in errors), reply_markup=cancel_keyboard())
            sent = await message.answer("Введите список ещё раз.", reply_markup=cancel_keyboard())
            repo.set_setup_session(chat_id, user_id, state, {"product_id": product_id, "product_name": product.name, "prompt_message_id": sent.message_id})
            return True
        if state in {"await_product_components", "await_product_components_replace"}:
            repo.set_product_components(chat_id, product_id, components)
            prefix = "Состав заменён."
        else:
            repo.add_or_update_product_components(chat_id, product_id, components)
            prefix = "Состав обновлён."
        component_ids = [component_id for component_id, _ in components]
        await _start_component_alias_queue(message, component_ids, prefix=prefix)
        return True


    if state == "await_selected_component_quantity":
        product_id = int(data.get("product_id", 0))
        component_id = int(data.get("component_id", 0))
        component_name = str(data.get("component_name") or "комплектующей")
        match = re.search(r"\d+(?:[\.,]\d+)?", text)
        if not match:
            sent = await _send_step_message(message, "Введите только количество числом.", reply_markup=cancel_keyboard())
            data["prompt_message_id"] = sent.message_id
            repo.set_setup_session(chat_id, user_id, "await_selected_component_quantity", data)
            return True
        quantity = float(match.group(0).replace(",", "."))
        if quantity <= 0:
            sent = await _send_step_message(message, "Количество должно быть больше нуля.", reply_markup=cancel_keyboard())
            data["prompt_message_id"] = sent.message_id
            repo.set_setup_session(chat_id, user_id, "await_selected_component_quantity", data)
            return True
        ok = repo.update_product_component_quantity(chat_id, product_id, component_id, quantity)
        repo.set_setup_session(chat_id, user_id, "choose_product_components_action", {"product_id": product_id, "product_name": data.get("product_name")})
        text_out = f"Количество обновлено: {component_name} — {quantity:g}." if ok else "Не удалось обновить количество."
        await _send_step_message(message, text_out, reply_markup=product_components_action_keyboard())
        return True

    if state == "await_product_components_remove":
        product_id = int(data.get("product_id", 0))
        product = repo.get_entity(product_id)
        if not product:
            repo.clear_setup_session(chat_id, user_id)
            await _send_step_message(message, "Изделие не найдено. Откройте настройку заново.", reply_markup=setup_menu())
            return True
        component_ids, errors = _parse_component_names(chat_id, text)
        if errors or not component_ids:
            await _send_step_message(message, "Не удалось убрать комплектующие.\n" + "\n".join(f"• {e}" for e in errors), reply_markup=cancel_keyboard())
            sent = await message.answer("Введите список ещё раз.", reply_markup=cancel_keyboard())
            repo.set_setup_session(chat_id, user_id, state, {"product_id": product_id, "product_name": product.name, "prompt_message_id": sent.message_id})
            return True
        removed = repo.remove_product_components(chat_id, product_id, component_ids)
        repo.set_setup_session(chat_id, user_id, "choose_product_components_action", {"product_id": product_id, "product_name": product.name})
        await _send_step_message(message, f"Убрано из состава: {removed}.", reply_markup=product_components_action_keyboard())
        return True

    if state == "await_component_aliases":
        current = data.get("current") or {}
        if current and text.lower() not in {"пропустить", "нет", "не надо"}:
            repo.add_aliases(chat_id, "component", int(current["target_id"]), text)
            prefix = "Сокращения сохранены."
        else:
            prefix = "Пропущено."
        await _send_next_component_alias_prompt(message, data, prefix)
        return True

    if state == "await_entity_name":
        entity_type = data.get("entity_type", "component")
        unit = "кг" if entity_type == "material" else ("кВт⋅ч" if entity_type == "meter" else "шт")
        ok, msg = repo.create_entity(chat_id, entity_type, text, unit)
        if not ok:
            await _send_step_message(message, msg)
            return True
        from ..services.matcher import confident_match
        match, _ = confident_match(chat_id, text, allowed_types={entity_type})
        if match and entity_type == "meter":
            areas = repo.list_areas(chat_id)
            if areas:
                sent = await _send_step_message(message, msg + "\n\nВыберите участки для счётчика. Можно выбрать один или несколько. Если участок будет понятен из сообщения или группы, бот сам выберет закреплённый прибор.", reply_markup=meter_area_keyboard([(a.id, a.name) for a in areas], set()))
                repo.set_setup_session(chat_id, user_id, "choose_meter_areas", {"meter_id": match.target_id, "area_ids": [], "prompt_message_id": sent.message_id})
            else:
                sent = await _send_step_message(message, msg + "\n\nУчастков пока нет. Счётчик можно привязать позже. Добавьте сокращения через запятую или с новой строки. Можно пропустить.", reply_markup=skip_alias_keyboard())
                repo.set_setup_session(chat_id, user_id, "await_aliases", {"target_type": "meter", "target_id": match.target_id, "prompt_message_id": sent.message_id})
        elif match and entity_type == "stock_item":
            areas = repo.list_areas(chat_id)
            if areas:
                sent = await _send_step_message(message, msg + "\n\nПривязать складскую позицию к участкам? Можно выбрать один, несколько или оставить общей для всего учёта.", reply_markup=stock_item_area_keyboard([(a.id, a.name) for a in areas], set()))
                repo.set_setup_session(chat_id, user_id, "choose_stock_item_areas", {"stock_item_id": match.target_id, "area_ids": [], "prompt_message_id": sent.message_id})
            else:
                sent = await _send_step_message(message, msg + "\n\nУчастков пока нет. Позиция будет общей. Добавьте сокращения через запятую или с новой строки. Можно пропустить.", reply_markup=skip_alias_keyboard())
                repo.set_setup_session(chat_id, user_id, "await_aliases", {"target_type": "stock_item", "target_id": match.target_id, "prompt_message_id": sent.message_id})
        elif match:
            sent = await _send_step_message(message, msg + "\n\nДобавьте сокращения через запятую или с новой строки. Бот будет понимать эти варианты при вводе данных. Можно пропустить.", reply_markup=skip_alias_keyboard())
            repo.set_setup_session(chat_id, user_id, "await_aliases", {"target_type": match.target_type, "target_id": match.target_id, "prompt_message_id": sent.message_id})
        else:
            repo.clear_setup_session(chat_id, user_id)
            await _send_step_message(message, msg, reply_markup=setup_menu())
        return True

    if state == "await_aliases":
        if text.lower() in {"пропустить", "нет", "не надо"}:
            repo.clear_setup_session(chat_id, user_id)
            await _send_step_message(message, "Сохранено. Выберите следующий пункт.", reply_markup=setup_menu())
            return True
        added, conflicts = repo.add_aliases(chat_id, data["target_type"], int(data["target_id"]), text)
        repo.clear_setup_session(chat_id, user_id)
        answer = f"Сокращения сохранены: {added}"
        if conflicts:
            answer += "\nНе добавлены занятые названия: " + ", ".join(conflicts)
        await _send_step_message(message, answer, reply_markup=setup_menu())
        return True

    return False



def _display_name_from_user(user) -> str:
    if not user:
        return ""
    full = " ".join(x for x in [user.first_name, user.last_name] if x).strip()
    return full or user.username or str(user.id)


def _parse_component_lines(chat_id: int, text: str, create_missing: bool = False) -> tuple[list[tuple[int, float]], list[str]]:
    from ..services.matcher import confident_match
    from ..services.parser import NUMBER_RE

    scope_chat_id = repo.resolve_scope_chat_id(chat_id)
    parts: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for chunk in line.split(","):
            chunk = chunk.strip(" -•")
            if chunk:
                parts.append(chunk)
    components: list[tuple[int, float]] = []
    errors: list[str] = []
    for part in parts:
        matches = list(NUMBER_RE.finditer(part))
        if not matches:
            errors.append(f"Не найдено количество: {part}")
            continue
        m = matches[-1]
        try:
            qty = float(m.group("num").replace(",", "."))
        except ValueError:
            errors.append(f"Не понял количество: {part}")
            continue
        name = (part[:m.start()] + " " + part[m.end():]).strip(" ,.-")
        name = re.sub(r"\b(?:шт|штук|штуки|кг|г|т)\b", " ", name, flags=re.IGNORECASE).strip(" ,.-")
        if not name:
            errors.append(f"Не найдено название: {part}")
            continue
        exact = repo.get_entity_by_name(scope_chat_id, "component", name)
        component_id: int | None = exact.id if exact else None
        if component_id is None:
            match, variants = confident_match(chat_id, name, allowed_types={"component"})
            if match and match.score >= 0.98:
                component_id = match.target_id
        if component_id is None and create_missing:
            ok, _ = repo.create_entity(scope_chat_id, "component", name, "шт")
            entity = repo.get_entity_by_name(scope_chat_id, "component", name)
            if entity:
                component_id = entity.id
        if component_id is None:
            errors.append(f"Не найдена комплектующая: {name}")
            continue
        components.append((component_id, qty))
    return components, errors


def _parse_component_names(chat_id: int, text: str) -> tuple[list[int], list[str]]:
    from ..services.matcher import confident_match

    scope_chat_id = repo.resolve_scope_chat_id(chat_id)
    parts: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for chunk in line.split(","):
            chunk = chunk.strip(" -•")
            if chunk:
                parts.append(chunk)
    ids: list[int] = []
    errors: list[str] = []
    seen: set[int] = set()
    for part in parts:
        name = re.sub(r"\b(?:шт|штук|штуки|кг|г|т)\b", " ", part, flags=re.IGNORECASE).strip(" ,.-")
        name = re.sub(r"\d+(?:[,.]\d+)?", " ", name).strip(" ,.-")
        if not name:
            errors.append(f"Не найдено название: {part}")
            continue
        exact = repo.get_entity_by_name(scope_chat_id, "component", name)
        component_id: int | None = exact.id if exact else None
        if component_id is None:
            match, _ = confident_match(chat_id, name, allowed_types={"component"})
            if match and match.score >= 0.90:
                component_id = match.target_id
        if component_id is None:
            errors.append(f"Не найдена комплектующая: {name}")
            continue
        if component_id not in seen:
            ids.append(component_id)
            seen.add(component_id)
    return ids, errors


def _unique_component_queue(component_ids: list[int]) -> list[dict]:
    result: list[dict] = []
    seen: set[int] = set()
    for component_id in component_ids:
        if component_id in seen:
            continue
        seen.add(component_id)
        ent = repo.get_entity(component_id)
        if ent:
            result.append({"target_id": ent.id, "name": ent.name})
    return result


async def _send_next_component_alias_prompt(message: Message, data: dict, prefix: str = "") -> None:
    queue = list(data.get("alias_queue") or [])
    if not queue:
        repo.clear_setup_session(message.chat.id, message.from_user.id)
        await _send_step_message(message, (prefix + "\n" if prefix else "") + "Состав сохранён.", reply_markup=setup_menu())
        return
    current = queue.pop(0)
    data["current"] = current
    data["alias_queue"] = queue
    sent = await _send_step_message(
        message,
        (prefix + "\n\n" if prefix else "") + f"Сокращения для: {current['name']}\n\nМожно написать через запятую или с новой строки.",
        reply_markup=component_alias_keyboard(),
    )
    data["prompt_message_id"] = sent.message_id
    repo.set_setup_session(message.chat.id, message.from_user.id, "await_component_aliases", data)


async def _start_component_alias_queue(message: Message, component_ids: list[int], prefix: str = "Состав сохранён.") -> None:
    data = {"alias_queue": _unique_component_queue(component_ids)}
    await _send_next_component_alias_prompt(message, data, prefix)

async def try_handle_setup_command(message: Message) -> bool:
    text = (message.text or "").strip()
    if not text:
        return False
    if not await can_manage_accounting(message.bot, message.chat, message.from_user):
        return False

    chat_id = message.chat.id
    repo.upsert_chat(chat_id, message.chat.title or message.chat.full_name or "", message.chat.type, connected=None)

    # Владелец учёта может задать себе видимую должность при запуске учёта.
    self_job_match = re.match(r"^(?:моя\s+должность|назначить\s+себе\s+должность|поставить\s+себе\s+должность)\s+(.+)$", text, flags=re.IGNORECASE)
    if self_job_match:
        job_name = self_job_match.group(1).strip()
        ok, msg = repo.create_or_set_self_job(chat_id, message.from_user.id, _display_name_from_user(message.from_user), job_name)
        await message.answer(msg)
        return True

    assign_match = re.match(r"^(?:назначить|выдать|поставить)\s+должность(?:\s+(.+))?$", text, flags=re.IGNORECASE)
    if assign_match:
        if not message.reply_to_message or not message.reply_to_message.from_user:
            await message.answer("Ответьте этой фразой на сообщение нужного участника.\n\nПример: ответьте на сообщение работника и напишите: назначить должность")
            return True
        target = message.reply_to_message.from_user
        target_name = _display_name_from_user(target)
        if target.username:
            username_label = f"@{target.username}"
            target_name = username_label if target_name == target.username else f"{target_name} ({username_label})"
        job_name = (assign_match.group(1) or "").strip()
        if job_name and not job_name.startswith("@"):
            job = repo.find_job_title(chat_id, job_name)
            if not job:
                await message.answer("Должность не найдена. Откройте настройку учёта и создайте её.")
                return True
            repo.set_worker_job(chat_id, target.id, target_name, int(job["id"]))
            try:
                await message.bot.send_message(message.from_user.id, f"Готово. {target_name} — {job['name']}.")
                await _safe_delete_message(message)
            except Exception:
                await message.answer(f"Готово. {target_name} — {job['name']}.")
            return True

        jobs = repo.list_job_titles(chat_id)
        if not jobs:
            await message.answer("Должностей пока нет. Сначала создайте должность в настройке учёта.")
            return True
        data = {
            "group_chat_id": chat_id,
            "group_title": message.chat.title or "рабочая группа",
            "target_user_id": target.id,
            "target_name": target_name,
            "target_username": target.username or "",
            "page": 0,
        }
        repo.set_setup_session(message.from_user.id, message.from_user.id, "assign_job_select", data)
        try:
            await message.bot.send_message(
                message.from_user.id,
                _assignment_text(data, 0),
                reply_markup=job_title_choice_keyboard(jobs, target.id, 0),
            )
            await _safe_delete_message(message)
        except Exception:
            await message.answer("Откройте личку с ботом и нажмите Start. После этого повторите назначение должности.")
        return True

    show_comp_match = re.match(r"^состав\s+(.+)$", text, flags=re.IGNORECASE)
    if show_comp_match and ":" not in text:
        from ..services.matcher import confident_match
        product_name = show_comp_match.group(1).strip()
        product_match, _ = confident_match(chat_id, product_name, allowed_types={"product"})
        if not product_match:
            await message.answer("Изделие не найдено. Сначала создайте изделие.")
            return True
        await message.answer(_product_components_text(product_match.target_id))
        return True

    add_comp_match = re.match(r"^добавить\s+в\s+состав\s+(.+?)\s*:\s*(.+)$", text, flags=re.IGNORECASE | re.DOTALL)
    if add_comp_match:
        from ..services.matcher import confident_match
        product_name = add_comp_match.group(1).strip()
        body = add_comp_match.group(2).strip()
        product_match, _ = confident_match(chat_id, product_name, allowed_types={"product"})
        if not product_match:
            await message.answer("Изделие не найдено. Сначала создайте изделие.")
            return True
        components, errors = _parse_component_lines(chat_id, body, create_missing=True)
        if errors or not components:
            await message.answer("Не удалось обновить состав.\n" + "\n".join(f"• {e}" for e in errors))
            return True
        repo.add_or_update_product_components(chat_id, product_match.target_id, components)
        await message.answer("Состав обновлён.")
        return True

    remove_comp_match = re.match(r"^(?:убрать|удалить)\s+из\s+состава\s+(.+?)\s*:\s*(.+)$", text, flags=re.IGNORECASE | re.DOTALL)
    if remove_comp_match:
        from ..services.matcher import confident_match
        product_name = remove_comp_match.group(1).strip()
        body = remove_comp_match.group(2).strip()
        product_match, _ = confident_match(chat_id, product_name, allowed_types={"product"})
        if not product_match:
            await message.answer("Изделие не найдено. Сначала создайте изделие.")
            return True
        component_ids, errors = _parse_component_names(chat_id, body)
        if errors or not component_ids:
            await message.answer("Не удалось убрать комплектующие.\n" + "\n".join(f"• {e}" for e in errors))
            return True
        removed = repo.remove_product_components(chat_id, product_match.target_id, component_ids)
        await message.answer(f"Убрано из состава: {removed}.")
        return True

    qty_comp_match = re.match(r"^(?:изменить\s+количество\s+в\s+составе|количество\s+в\s+составе)\s+(.+?)\s*:\s*(.+)$", text, flags=re.IGNORECASE | re.DOTALL)
    if qty_comp_match:
        from ..services.matcher import confident_match
        product_name = qty_comp_match.group(1).strip()
        body = qty_comp_match.group(2).strip()
        product_match, _ = confident_match(chat_id, product_name, allowed_types={"product"})
        if not product_match:
            await message.answer("Изделие не найдено. Сначала создайте изделие.")
            return True
        components, errors = _parse_component_lines(chat_id, body, create_missing=True)
        if errors or not components:
            await message.answer("Не удалось изменить количество.\n" + "\n".join(f"• {e}" for e in errors))
            return True
        repo.add_or_update_product_components(chat_id, product_match.target_id, components)
        await message.answer("Количество обновлено.")
        return True

    # Состав изделия: первая часть до двоеточия — изделие, дальше комплектующие и количество.
    comp_match = re.match(r"^(?:состав|комплект|норма)\s+(.+?)\s*:\s*(.+)$", text, flags=re.IGNORECASE | re.DOTALL)
    if comp_match:
        from ..services.matcher import confident_match
        product_name = comp_match.group(1).strip()
        body = comp_match.group(2).strip()
        product_match, _ = confident_match(chat_id, product_name, allowed_types={"product"})
        if not product_match:
            await message.answer("Изделие не найдено. Сначала создайте изделие.")
            return True
        components, errors = _parse_component_lines(chat_id, body, create_missing=True)
        if errors or not components:
            await message.answer("Не удалось сохранить состав.\n" + "\n".join(f"• {e}" for e in errors))
            return True
        repo.set_product_components(chat_id, product_match.target_id, components)
        await _start_component_alias_queue(message, [component_id for component_id, _ in components])
        return True

    if normalize_key(text) in {"работники", "список работников"}:
        workers = repo.list_workers(chat_id)
        if not workers:
            await message.answer("Работники пока не назначены.")
            return True
        lines = ["Работники:"]
        for w in workers[:60]:
            lines.append(f"• {w.get('display_name') or w.get('user_id')} — {w.get('job_name') or 'без должности'}")
        await message.answer("\n".join(lines))
        return True

    patterns = [
        (r"^создать\s+участок\s+(.+)$", "area"),
        (r"^создать\s+должность\s+(.+)$", "job"),
        (r"^(?:добавить\s+сырье|добавить\s+сырьё|создать\s+сырье|создать\s+сырьё)\s+(.+)$", "material"),
        (r"^(?:добавить\s+деталь|добавить\s+комплектующую|создать\s+деталь)\s+(.+)$", "component"),
        (r"^(?:создать\s+изделие|добавить\s+изделие)\s+(.+)$", "product"),
        (r"^(?:добавить\s+позицию\s+склада|создать\s+позицию\s+склада|добавить\s+складскую\s+позицию|создать\s+складскую\s+позицию|добавить\s+на\s+склад)\s+(.+)$", "stock_item"),
        (r"^(?:добавить\s+счетчик|добавить\s+счётчик|создать\s+счетчик|создать\s+счётчик)\s+(.+)$", "meter"),
    ]
    lowered = text.lower()
    for pattern, kind in patterns:
        m = re.match(pattern, lowered, flags=re.IGNORECASE)
        if not m:
            continue
        name = text[m.start(1):].strip()
        if kind == "area":
            if repo.count_active_areas(chat_id) >= 999:
                await message.answer("Достигнут предел: 999 участков.")
                return True
            ok, msg = repo.create_area(chat_id, name)
        elif kind == "job":
            ok, msg = repo.create_job_title(chat_id, name)
        else:
            unit = "кг" if kind == "material" else ("кВт⋅ч" if kind == "meter" else "шт")
            ok, msg = repo.create_entity(chat_id, kind, name, unit)
            if ok and kind in {"meter", "stock_item"}:
                from ..services.matcher import confident_match
                match, _ = confident_match(chat_id, name, allowed_types={kind})
                if match and kind == "meter":
                    areas = repo.list_areas(chat_id)
                    repo.set_setup_session(chat_id, message.from_user.id, "choose_meter_areas", {"meter_id": match.target_id, "area_ids": []})
                    if areas:
                        await message.answer(msg + "\n\nВыберите участки для счётчика. Можно выбрать один или несколько. Если участок будет понятен из сообщения или группы, бот сам выберет закреплённый прибор.", reply_markup=meter_area_keyboard([(a.id, a.name) for a in areas], set()))
                    else:
                        await message.answer(msg + "\n\nУчастков пока нет. Счётчик можно привязать позже.")
                    return True
                if match and kind == "stock_item":
                    areas = repo.list_areas(chat_id)
                    repo.set_setup_session(chat_id, message.from_user.id, "choose_stock_item_areas", {"stock_item_id": match.target_id, "area_ids": []})
                    if areas:
                        await message.answer(msg + "\n\nПривязать складскую позицию к участкам? Можно выбрать один, несколько или оставить общей для всего учёта.", reply_markup=stock_item_area_keyboard([(a.id, a.name) for a in areas], set()))
                    else:
                        await message.answer(msg + "\n\nУчастков пока нет. Позиция будет общей.")
                    return True
        await message.answer(msg)
        return True

    alias_match = re.match(r"^синонимы\s+(.+?)\s*:\s*(.+)$", text, flags=re.IGNORECASE | re.DOTALL)
    if alias_match:
        target_name = alias_match.group(1).strip()
        aliases = alias_match.group(2).strip()
        from ..services.matcher import confident_match

        match, variants = confident_match(chat_id, target_name, allowed_types=None)
        if not match:
            await message.answer("Не нашёл основное название. Создайте позицию или уточните название.")
            return True
        added, conflicts = repo.add_aliases(chat_id, match.target_type, match.target_id, aliases)
        msg = f"Сокращения сохранены: {added}"
        if conflicts:
            msg += "\nНе добавлены занятые названия: " + ", ".join(conflicts)
        await message.answer(msg)
        return True

    return False
