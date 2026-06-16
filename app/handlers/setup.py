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
    meter_area_keyboard,
    permission_keyboard,
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


@router.callback_query(F.data.startswith("setup:"))
async def setup_section(callback: CallbackQuery) -> None:
    section = callback.data.split(":", 1)[1]
    texts = {
        "quick": (
            "Быстрая настройка\n\n"
            "1. Создайте участок.\n"
            "2. Создайте нужные должности.\n"
            "3. Добавьте изделия, комплектующие, сырьё, складские позиции и счётчики.\n"
            "4. Добавьте сокращения, если работники пишут коротко.\n"
            "5. Подключите рабочую группу командой: привязать группу."
        ),
        "areas": "Участки\n\nСоздавайте только нужные участки. Максимум — 999.",
        "groups": "Группы\n\nБот принимает данные только в подключённых группах.",
        "jobs": "Должности\n\nВладелец учёта сам создаёт названия должностей и выбирает права.",
        "items": "Позиции\n\nДобавьте свои изделия, комплектующие или складские позиции. Названия можно писать любые.",
        "materials": "Сырьё\n\nСырьё можно учитывать по участкам.",
        "meters": "Счётчики\n\nСчётчик можно привязать к одному или нескольким участкам.",
    }
    await safe_edit_text(callback.message, texts.get(section, "Настройка учёта"), reply_markup=setup_menu())
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
        await safe_edit_text(callback.message, "Введите название участка.\n\nУчасток нужен для учёта сырья и счётчиков. Название можно написать любое. После сохранения бот предложит добавить сокращения.", reply_markup=cancel_keyboard())
        await callback.answer()
        return

    if data == "wizard:job":
        repo.set_setup_session(chat_id, user_id, "await_job_name", {"prompt_message_id": callback.message.message_id})
        await safe_edit_text(callback.message, "Введите название должности.\n\nПосле названия бот покажет список прав. Отметьте галочками, что будет разрешено этой должности.", reply_markup=cancel_keyboard())
        await callback.answer()
        return

    if data == "wizard:product_components":
        repo.set_setup_session(chat_id, user_id, "await_product_for_components", {"prompt_message_id": callback.message.message_id})
        await safe_edit_text(callback.message, "Введите название изделия.", reply_markup=cancel_keyboard())
        await callback.answer()
        return

    if data.startswith("wizard:entity:"):
        entity_type = data.rsplit(":", 1)[1]
        label = ENTITY_LABELS.get(entity_type, "Позиция")
        repo.set_setup_session(chat_id, user_id, "await_entity_name", {"entity_type": entity_type, "prompt_message_id": callback.message.message_id})
        await safe_edit_text(callback.message, f"Введите название.\n\nТип: {label}\nНазвание можно написать любое. После сохранения можно добавить сокращения и рабочие названия.", reply_markup=cancel_keyboard())
        await callback.answer()
        return


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
        sent = await _send_step_message(message, f"Должность: {text}\n\nОтметьте, что разрешено. Потом нажмите «Сохранить должность».", reply_markup=permission_keyboard({}))
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
            "Введите комплектующие и количество.\n\nМожно списком через строки или через запятую. Если комплектующей ещё нет, бот создаст её сам.",
            reply_markup=cancel_keyboard(),
        )
        repo.set_setup_session(chat_id, user_id, "await_product_components", {"product_id": match.target_id, "product_name": match.name, "prompt_message_id": sent.message_id})
        return True

    if state == "await_product_components":
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
            repo.set_setup_session(chat_id, user_id, "await_product_components", {"product_id": product_id, "product_name": product.name, "prompt_message_id": sent.message_id})
            return True
        repo.set_product_components(chat_id, product_id, components)
        component_ids = [component_id for component_id, _ in components]
        await _start_component_alias_queue(message, component_ids)
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


async def _start_component_alias_queue(message: Message, component_ids: list[int]) -> None:
    data = {"alias_queue": _unique_component_queue(component_ids)}
    await _send_next_component_alias_prompt(message, data, "Состав сохранён.")

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

    # Назначение должности делается ответом на сообщение работника.
    assign_match = re.match(r"^(?:назначить|выдать|поставить)\s+должность\s+(.+)$", text, flags=re.IGNORECASE)
    if assign_match:
        if not message.reply_to_message or not message.reply_to_message.from_user:
            await message.answer("Ответьте этой командой на сообщение нужного участника.")
            return True
        job_name = assign_match.group(1).strip()
        job = repo.find_job_title(chat_id, job_name)
        if not job:
            await message.answer("Должность не найдена. Сначала создайте должность.")
            return True
        user = message.reply_to_message.from_user
        repo.set_worker_job(chat_id, user.id, _display_name_from_user(user), int(job["id"]))
        await message.answer(f"Должность назначена: {job['name']}.")
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
