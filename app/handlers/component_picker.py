from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ._safe import safe_edit_text
from ..access import can_manage_accounting
from ..keyboards import product_components_action_keyboard, setup_menu
from ..services import repository as repo
from ..services.normalize import format_amount

router = Router()

_PAGE_SIZE = 18
_QUICK_QUANTITIES = (1, 2, 3, 4, 5, 10)


def _all_components(chat_id: int):
    return list(repo.list_entities(chat_id, {"component"}))


def _existing_map(product_id: int) -> dict[int, float]:
    out: dict[int, float] = {}
    for row in repo.list_product_components(product_id):
        out[int(row["component_id"])] = float(row.get("quantity") or 0)
    return out


def _picker_components(chat_id: int, product_id: int, mode: str):
    components = _all_components(chat_id)
    if mode == "add":
        existing = set(_existing_map(product_id))
        components = [item for item in components if int(item.id) not in existing]
    return components


def _selection_keyboard(components, selected_ids: set[int], page: int, current_quantities: dict[int, float]) -> InlineKeyboardMarkup:
    pages = max(1, (len(components) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    start = page * _PAGE_SIZE
    rows: list[list[InlineKeyboardButton]] = []
    for component in components[start:start + _PAGE_SIZE]:
        component_id = int(component.id)
        mark = "✅" if component_id in selected_ids else "⬜"
        suffix = ""
        current = float(current_quantities.get(component_id) or 0)
        if current > 0:
            suffix = f" · {format_amount(current)} {component.default_unit or 'шт'}"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {component.name}{suffix}"[:62],
                callback_data=f"components:picker:toggle:{component_id}",
            )
        ])
    if pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"components:picker:page:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="components:picker:noop"))
        if page + 1 < pages:
            nav.append(InlineKeyboardButton(text="Дальше ▶", callback_data=f"components:picker:page:{page + 1}"))
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(text="✅ Выбрать все", callback_data="components:picker:all"),
        InlineKeyboardButton(text="Снять все", callback_data="components:picker:none"),
    ])
    rows.append([InlineKeyboardButton(text=f"Дальше · выбрано {len(selected_ids)}", callback_data="components:picker:next")])
    rows.append([InlineKeyboardButton(text="Назад к составу", callback_data="components:picker:back")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="wizard:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _quantity_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=str(value), callback_data=f"components:picker:q:{value}") for value in _QUICK_QUANTITIES[:3]],
        [InlineKeyboardButton(text=str(value), callback_data=f"components:picker:q:{value}") for value in _QUICK_QUANTITIES[3:]],
        [InlineKeyboardButton(text="Назад к выбору", callback_data="components:picker:back_select")],
        [InlineKeyboardButton(text="Отмена", callback_data="wizard:cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _selection_text(data: dict, count: int) -> str:
    mode = str(data.get("mode") or "replace")
    title = "Заменить состав" if mode == "replace" else "Добавить комплектующие"
    note = (
        "Уже входящие в состав позиции отмечены автоматически. Их количество сохранится, если оставить их выбранными."
        if mode == "replace"
        else "Показаны созданные комплектующие, которых ещё нет в составе."
    )
    return (
        f"{title}: {data.get('product_name') or 'изделие'}\n\n"
        "Отметьте галочками нужные комплектующие. Можно выбрать одну, несколько или сразу все.\n"
        f"{note}\n\nВсего доступно: {count}. После выбора нажмите «Дальше»."
    )


def _selected_in_order(components, selected_ids: set[int]) -> list[int]:
    return [int(item.id) for item in components if int(item.id) in selected_ids]


def _component_by_id(chat_id: int, component_id: int):
    item = repo.get_entity(component_id)
    if not item or item.entity_type != "component" or item.chat_id != repo.resolve_scope_chat_id(chat_id):
        return None
    return item


def _quantity_prompt(chat_id: int, data: dict) -> str:
    pending_ids = [int(x) for x in data.get("pending_ids") or []]
    index = int(data.get("quantity_index") or 0)
    if index < 0 or index >= len(pending_ids):
        return "Введите количество."
    component = _component_by_id(chat_id, pending_ids[index])
    name = component.name if component else "Комплектующая"
    unit = component.default_unit if component else "шт"
    return (
        f"Количество для состава · {index + 1} из {len(pending_ids)}\n\n"
        f"{name}\n"
        f"Сколько {unit} нужно на 1 изделие?\n\n"
        "Можно нажать готовое число ниже или ввести своё."
    )


def _save_selection(chat_id: int, data: dict) -> tuple[bool, str]:
    product_id = int(data.get("product_id") or 0)
    mode = str(data.get("mode") or "replace")
    selected_ids = {int(x) for x in data.get("selected_ids") or []}
    quantities_raw = data.get("quantities") or {}
    quantities = {int(key): float(value) for key, value in quantities_raw.items() if float(value) > 0}
    components = _picker_components(chat_id, product_id, mode)
    ordered_ids = _selected_in_order(components, selected_ids)
    payload = [(component_id, quantities[component_id]) for component_id in ordered_ids if component_id in quantities]
    if len(payload) != len(ordered_ids):
        return False, "Не для всех выбранных комплектующих указано количество."
    if mode == "replace":
        repo.set_product_components(chat_id, product_id, payload)
        return True, "Состав изделия заменён."
    repo.add_or_update_product_components(chat_id, product_id, payload)
    return True, "Комплектующие добавлены в состав."


async def _open_picker(callback: CallbackQuery, mode: str) -> None:
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    session = repo.get_setup_session(chat_id, user_id)
    if not session or session.get("state") != "choose_product_components_action":
        await callback.answer("Откройте состав изделия заново.", show_alert=True)
        return
    data = dict(session.get("data") or {})
    product_id = int(data.get("product_id") or 0)
    product = repo.get_entity(product_id)
    if not product:
        await callback.answer("Изделие не найдено.", show_alert=True)
        return
    components = _picker_components(chat_id, product_id, mode)
    if not components:
        text = "Сначала создайте комплектующие." if mode == "replace" else "Все созданные комплектующие уже входят в состав."
        await safe_edit_text(callback.message, text, reply_markup=product_components_action_keyboard())
        await callback.answer()
        return
    existing = _existing_map(product_id)
    selected_ids = set(existing) if mode == "replace" else set()
    quantities = {str(key): value for key, value in existing.items()} if mode == "replace" else {}
    data.update({
        "mode": mode,
        "selected_ids": sorted(selected_ids),
        "quantities": quantities,
        "page": 0,
        "prompt_message_id": callback.message.message_id,
    })
    repo.set_setup_session(chat_id, user_id, "component_picker_select", data)
    await safe_edit_text(
        callback.message,
        _selection_text(data, len(components)),
        reply_markup=_selection_keyboard(components, selected_ids, 0, existing),
    )
    await callback.answer()


async def _finish_or_prompt_quantity(callback: CallbackQuery, data: dict) -> None:
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    mode = str(data.get("mode") or "replace")
    product_id = int(data.get("product_id") or 0)
    components = _picker_components(chat_id, product_id, mode)
    selected_ids = {int(x) for x in data.get("selected_ids") or []}
    if not selected_ids:
        await callback.answer("Отметьте хотя бы одну комплектующую.", show_alert=True)
        return
    quantities = {int(key): float(value) for key, value in (data.get("quantities") or {}).items() if float(value) > 0}
    ordered_ids = _selected_in_order(components, selected_ids)
    pending_ids = [component_id for component_id in ordered_ids if component_id not in quantities]
    data["pending_ids"] = pending_ids
    data["quantity_index"] = 0
    data["selected_ids"] = ordered_ids
    if not pending_ids:
        ok, text = _save_selection(chat_id, data)
        if ok:
            repo.set_setup_session(chat_id, user_id, "choose_product_components_action", {"product_id": product_id, "product_name": data.get("product_name")})
        await safe_edit_text(callback.message, text, reply_markup=product_components_action_keyboard())
        await callback.answer()
        return
    data["prompt_message_id"] = callback.message.message_id
    repo.set_setup_session(chat_id, user_id, "component_picker_quantity", data)
    await safe_edit_text(callback.message, _quantity_prompt(chat_id, data), reply_markup=_quantity_keyboard())
    await callback.answer()


async def _apply_quantity_callback(callback: CallbackQuery, quantity: float) -> None:
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    session = repo.get_setup_session(chat_id, user_id)
    if not session or session.get("state") != "component_picker_quantity":
        await callback.answer("Откройте выбор состава заново.", show_alert=True)
        return
    data = dict(session.get("data") or {})
    pending_ids = [int(x) for x in data.get("pending_ids") or []]
    index = int(data.get("quantity_index") or 0)
    if quantity <= 0 or index >= len(pending_ids):
        await callback.answer("Количество должно быть больше нуля.", show_alert=True)
        return
    quantities = dict(data.get("quantities") or {})
    quantities[str(pending_ids[index])] = float(quantity)
    data["quantities"] = quantities
    data["quantity_index"] = index + 1
    if data["quantity_index"] >= len(pending_ids):
        ok, text = _save_selection(chat_id, data)
        if ok:
            repo.set_setup_session(chat_id, user_id, "choose_product_components_action", {"product_id": int(data.get("product_id") or 0), "product_name": data.get("product_name")})
        await safe_edit_text(callback.message, text, reply_markup=product_components_action_keyboard())
        await callback.answer()
        return
    repo.set_setup_session(chat_id, user_id, "component_picker_quantity", data)
    await safe_edit_text(callback.message, _quantity_prompt(chat_id, data), reply_markup=_quantity_keyboard())
    await callback.answer()


@router.callback_query(F.data.in_({"components:replace", "components:add"}))
async def start_component_picker(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await _open_picker(callback, "replace" if callback.data == "components:replace" else "add")


@router.callback_query(F.data.startswith("components:picker:"))
async def component_picker_callback(callback: CallbackQuery) -> None:
    if not await can_manage_accounting(callback.bot, callback.message.chat, callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    session = repo.get_setup_session(chat_id, user_id)
    if not session or session.get("state") not in {"component_picker_select", "component_picker_quantity"}:
        await callback.answer("Откройте состав изделия заново.", show_alert=True)
        return
    data = dict(session.get("data") or {})
    action = (callback.data or "").split(":", 2)[2]

    if action == "noop":
        await callback.answer()
        return
    if action == "back":
        repo.set_setup_session(chat_id, user_id, "choose_product_components_action", {"product_id": int(data.get("product_id") or 0), "product_name": data.get("product_name")})
        await safe_edit_text(callback.message, "Выберите действие с составом изделия.", reply_markup=product_components_action_keyboard())
        await callback.answer()
        return
    if action == "back_select":
        components = _picker_components(chat_id, int(data.get("product_id") or 0), str(data.get("mode") or "replace"))
        selected_ids = {int(x) for x in data.get("selected_ids") or []}
        current = _existing_map(int(data.get("product_id") or 0))
        repo.set_setup_session(chat_id, user_id, "component_picker_select", data)
        await safe_edit_text(callback.message, _selection_text(data, len(components)), reply_markup=_selection_keyboard(components, selected_ids, int(data.get("page") or 0), current))
        await callback.answer()
        return
    if action.startswith("q:"):
        try:
            quantity = float(action.split(":", 1)[1])
        except ValueError:
            await callback.answer("Неверное количество.", show_alert=True)
            return
        await _apply_quantity_callback(callback, quantity)
        return
    if session.get("state") != "component_picker_select":
        await callback.answer("Сначала вернитесь к выбору комплектующих.", show_alert=True)
        return

    mode = str(data.get("mode") or "replace")
    product_id = int(data.get("product_id") or 0)
    components = _picker_components(chat_id, product_id, mode)
    available_ids = {int(item.id) for item in components}
    selected_ids = {int(x) for x in data.get("selected_ids") or [] if int(x) in available_ids}
    page = int(data.get("page") or 0)

    if action.startswith("toggle:"):
        component_id = int(action.rsplit(":", 1)[1])
        if component_id not in available_ids:
            await callback.answer("Комплектующая не найдена.", show_alert=True)
            return
        if component_id in selected_ids:
            selected_ids.remove(component_id)
        else:
            selected_ids.add(component_id)
    elif action == "all":
        selected_ids = set(available_ids)
    elif action == "none":
        selected_ids.clear()
    elif action.startswith("page:"):
        page = int(action.rsplit(":", 1)[1])
    elif action == "next":
        data["selected_ids"] = sorted(selected_ids)
        data["page"] = page
        await _finish_or_prompt_quantity(callback, data)
        return
    else:
        await callback.answer()
        return

    data["selected_ids"] = sorted(selected_ids)
    data["page"] = page
    repo.set_setup_session(chat_id, user_id, "component_picker_select", data)
    await safe_edit_text(callback.message, _selection_text(data, len(components)), reply_markup=_selection_keyboard(components, selected_ids, page, _existing_map(product_id)))
    await callback.answer()


async def try_handle_component_picker_message(message: Message) -> bool:
    if not message.text or not message.from_user:
        return False
    chat_id = message.chat.id
    user_id = message.from_user.id
    session = repo.get_setup_session(chat_id, user_id)
    if not session or session.get("state") != "component_picker_quantity":
        return False
    text = message.text.strip()
    if text.lower() in {"отмена", "стоп"}:
        repo.clear_setup_session(chat_id, user_id)
        await message.answer("Отменено.", reply_markup=setup_menu())
        return True
    match = re.search(r"\d+(?:[\.,]\d+)?", text)
    if not match:
        await message.answer("Введите количество числом, например 1, 2 или 0,5.", reply_markup=_quantity_keyboard())
        return True
    quantity = float(match.group(0).replace(",", "."))
    if quantity <= 0:
        await message.answer("Количество должно быть больше нуля.", reply_markup=_quantity_keyboard())
        return True

    data = dict(session.get("data") or {})
    pending_ids = [int(x) for x in data.get("pending_ids") or []]
    index = int(data.get("quantity_index") or 0)
    if index >= len(pending_ids):
        repo.clear_setup_session(chat_id, user_id)
        await message.answer("Выбор состава завершён. Откройте настройку заново.", reply_markup=setup_menu())
        return True
    quantities = dict(data.get("quantities") or {})
    quantities[str(pending_ids[index])] = quantity
    data["quantities"] = quantities
    data["quantity_index"] = index + 1

    try:
        await message.delete()
    except Exception:
        pass

    prompt_message_id = int(data.get("prompt_message_id") or 0)
    if data["quantity_index"] >= len(pending_ids):
        ok, result_text = _save_selection(chat_id, data)
        if ok:
            repo.set_setup_session(chat_id, user_id, "choose_product_components_action", {"product_id": int(data.get("product_id") or 0), "product_name": data.get("product_name")})
        else:
            repo.set_setup_session(chat_id, user_id, "component_picker_quantity", data)
        if prompt_message_id:
            try:
                await message.bot.edit_message_text(chat_id=chat_id, message_id=prompt_message_id, text=result_text, reply_markup=product_components_action_keyboard())
                return True
            except Exception:
                pass
        sent = await message.answer(result_text, reply_markup=product_components_action_keyboard())
        data["prompt_message_id"] = sent.message_id
        return True

    repo.set_setup_session(chat_id, user_id, "component_picker_quantity", data)
    prompt = _quantity_prompt(chat_id, data)
    if prompt_message_id:
        try:
            await message.bot.edit_message_text(chat_id=chat_id, message_id=prompt_message_id, text=prompt, reply_markup=_quantity_keyboard())
            return True
        except Exception:
            pass
    sent = await message.answer(prompt, reply_markup=_quantity_keyboard())
    data["prompt_message_id"] = sent.message_id
    repo.set_setup_session(chat_id, user_id, "component_picker_quantity", data)
    return True
