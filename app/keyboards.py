from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


PERMISSION_LABELS: dict[str, str] = {
    "production": "Сдавать производство",
    "material": "Сдавать сырьё",
    "energy": "Сдавать счётчики",
    "assembly": "Сдавать сборку",
    "shipment": "Сдавать отгрузку",
    "reports": "Смотреть отчёты",
    "stock": "Смотреть склад",
    "edit": "Исправлять записи",
    "setup": "Настраивать учёт",
    "workers": "Работники и должности",
    "grant": "Назначать должности",
    "permissions": "Настраивать права должностей",
    "export": "Создавать файлы",
}


ENTITY_LABELS: dict[str, str] = {
    "product": "Изделие",
    "component": "Комплектующая",
    "material": "Сырьё",
    "stock_item": "Складская позиция",
    "meter": "Счётчик",
}


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Настроить учёт", callback_data="menu:setup")],
        [InlineKeyboardButton(text="Группы", callback_data="menu:chats")],
        [InlineKeyboardButton(text="Склад", callback_data="menu:stock"), InlineKeyboardButton(text="Отчёты", callback_data="menu:reports")],
        [InlineKeyboardButton(text="Работники", callback_data="menu:workers")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chat_list_keyboard(chats: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for chat in chats[:80]:
        title = str(chat.get("title") or chat.get("chat_id"))
        chat_id = int(chat["chat_id"])
        mark = "✅" if chat.get("is_connected") else "▫️"
        rows.append([InlineKeyboardButton(text=f"{mark} {title[:42]}", callback_data=f"chatpick:{chat_id}")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chat_action_keyboard(chat_id: int, connected: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if connected:
        rows.append([InlineKeyboardButton(text="Открыть настройку", callback_data=f"chatopen:{chat_id}:setup")])
    else:
        rows.append([InlineKeyboardButton(text="Подключить", callback_data=f"chatopen:{chat_id}:connect")])
    rows.append([InlineKeyboardButton(text="Выбрать участок", callback_data=f"chatopen:{chat_id}:area")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:chats"), InlineKeyboardButton(text="В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def setup_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Быстрая настройка", callback_data="setup:quick")],
        [InlineKeyboardButton(text="Создать участок", callback_data="wizard:area")],
        [InlineKeyboardButton(text="Создать должность", callback_data="wizard:job")],
        [InlineKeyboardButton(text="Добавить изделие", callback_data="wizard:entity:product")],
        [InlineKeyboardButton(text="Состав изделия", callback_data="wizard:product_components")],
        [InlineKeyboardButton(text="Добавить комплектующую", callback_data="wizard:entity:component")],
        [InlineKeyboardButton(text="Добавить сырьё", callback_data="wizard:entity:material")],
        [InlineKeyboardButton(text="Добавить позицию склада", callback_data="wizard:entity:stock_item")],
        [InlineKeyboardButton(text="Добавить счётчик", callback_data="wizard:entity:meter")],
        [InlineKeyboardButton(text="Назад", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def skip_alias_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="wizard:skip_aliases")],
        [InlineKeyboardButton(text="Отмена", callback_data="wizard:cancel")],
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отмена", callback_data="wizard:cancel")],
    ])


def permission_keyboard(selected: dict[str, bool]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, label in PERMISSION_LABELS.items():
        mark = "✅ " if selected.get(key) else "⬜ "
        rows.append([InlineKeyboardButton(text=mark + label, callback_data=f"perm:toggle:{key}")])
    rows.append([InlineKeyboardButton(text="Сохранить должность", callback_data="perm:save")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="wizard:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def area_choice_keyboard(areas: list[tuple[int, str]], prefix: str = "area_bind") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for area_id, name in areas[:50]:
        rows.append([InlineKeyboardButton(text=name, callback_data=f"{prefix}:{area_id}")])
    rows.append([InlineKeyboardButton(text="Без участка", callback_data=f"{prefix}:none")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="wizard:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def reports_quick_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сегодня", callback_data="reportquick:отчёт за сегодня"), InlineKeyboardButton(text="Неделя", callback_data="reportquick:отчёт за неделю")],
        [InlineKeyboardButton(text="Месяц", callback_data="reportquick:отчёт за месяц")],
        [InlineKeyboardButton(text="Назад", callback_data="menu:main")],
    ])


def workers_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Настроить учёт", callback_data="menu:setup")],
        [InlineKeyboardButton(text="Назад", callback_data="menu:main")],
    ])



def quick_step_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="quick:skip")],
        [InlineKeyboardButton(text="Отмена", callback_data="wizard:cancel")],
    ])




def product_choice_keyboard(products: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for product_id, name in products[:80]:
        rows.append([InlineKeyboardButton(text=name, callback_data=f"components:product:{product_id}")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:setup")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="wizard:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def product_components_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Показать состав", callback_data="components:show")],
        [InlineKeyboardButton(text="Заменить состав", callback_data="components:replace")],
        [InlineKeyboardButton(text="Добавить комплектующие", callback_data="components:add")],
        [InlineKeyboardButton(text="Изменить количество", callback_data="components:qty")],
        [InlineKeyboardButton(text="Удалить комплектующие", callback_data="components:remove")],
        [InlineKeyboardButton(text="Готово", callback_data="components:finish")],
        [InlineKeyboardButton(text="Отмена", callback_data="wizard:cancel")],
    ])



def component_choice_keyboard(components: list[dict], mode: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for comp in components[:80]:
        component_id = int(comp["component_id"])
        name = str(comp["name"])
        qty = float(comp.get("quantity") or 0)
        unit = str(comp.get("default_unit") or "шт")
        rows.append([InlineKeyboardButton(text=f"{name} — {qty:g} {unit}", callback_data=f"components:{mode}:{component_id}")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="components:back_actions")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="wizard:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def component_alias_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="componentalias:skip")],
        [InlineKeyboardButton(text="Готово", callback_data="componentalias:finish")],
    ])

def confirm_keyboard(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да", callback_data=f"confirm:{pending_id}"), InlineKeyboardButton(text="Исправить", callback_data=f"edit:{pending_id}")],
        [InlineKeyboardButton(text="Отмена", callback_data=f"cancel:{pending_id}")],
    ])


def meter_area_keyboard(areas: list[tuple[int, str]], selected_ids: set[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for area_id, name in areas[:80]:
        mark = "✅ " if area_id in selected_ids else "⬜ "
        rows.append([InlineKeyboardButton(text=mark + name, callback_data=f"meterarea:toggle:{area_id}")])
    rows.append([InlineKeyboardButton(text="Сохранить привязку", callback_data="meterarea:save")])
    rows.append([InlineKeyboardButton(text="Не привязывать сейчас", callback_data="meterarea:skip")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="wizard:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stock_item_area_keyboard(areas: list[tuple[int, str]], selected_ids: set[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for area_id, name in areas[:80]:
        mark = "✅ " if area_id in selected_ids else "⬜ "
        rows.append([InlineKeyboardButton(text=mark + name, callback_data=f"stockarea:toggle:{area_id}")])
    rows.append([InlineKeyboardButton(text="Сохранить привязку", callback_data="stockarea:save")])
    rows.append([InlineKeyboardButton(text="Не привязывать", callback_data="stockarea:skip")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="wizard:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

EXPORT_SECTION_LABELS: dict[str, str] = {
    "inventory": "Склад",
    "period_totals": "Итоги за период",
    "daily_matrix": "По датам",
    "capacity": "Расчёт сборки",
    "journal": "Журнал",
}


def export_preferences_keyboard(selected: dict[str, bool]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, label in EXPORT_SECTION_LABELS.items():
        mark = "✅ " if selected.get(key) else "⬜ "
        rows.append([InlineKeyboardButton(text=mark + label, callback_data=f"export:toggle:{key}")])
    rows.append([InlineKeyboardButton(text="Сохранить", callback_data="export:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def report_sections_keyboard(token: str, selected: dict[str, bool], action_text: str = "Показать отчёт") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, label in EXPORT_SECTION_LABELS.items():
        mark = "✅ " if selected.get(key) else "⬜ "
        rows.append([InlineKeyboardButton(text=mark + label, callback_data=f"report:toggle:{token}:{key}")])
    rows.append([InlineKeyboardButton(text=action_text, callback_data=f"report:show:{token}")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:reports"), InlineKeyboardButton(text="В меню", callback_data="menu:main")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"report:cancel:{token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def report_download_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Excel", callback_data=f"report:file:{token}:xlsx"), InlineKeyboardButton(text="PDF", callback_data=f"report:file:{token}:pdf")],
        [InlineKeyboardButton(text="CSV", callback_data=f"report:file:{token}:csv"), InlineKeyboardButton(text="HTML", callback_data=f"report:file:{token}:html")],
        [InlineKeyboardButton(text="TXT", callback_data=f"report:file:{token}:txt")],
        [InlineKeyboardButton(text="Назад", callback_data=f"report:back:{token}"), InlineKeyboardButton(text="В меню", callback_data="menu:main")],
    ])
