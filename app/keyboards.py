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
        [InlineKeyboardButton(text="Как пользоваться", callback_data="menu:help")],
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



def job_title_choice_keyboard(jobs: list[dict], target_user_id: int, page: int = 0) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    start = max(0, page) * 12
    shown = jobs[start:start + 12]
    for job in shown:
        rows.append([InlineKeyboardButton(text=str(job.get("name") or "Должность"), callback_data=f"jobassign:pick:{target_user_id}:{int(job['id'])}:{page}")])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="Назад", callback_data=f"jobassign:page:{target_user_id}:{page - 1}"))
    if start + 12 < len(jobs):
        nav.append(InlineKeyboardButton(text="Дальше", callback_data=f"jobassign:page:{target_user_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"jobassign:cancel:{target_user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def job_assignment_confirm_keyboard(target_user_id: int, job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подтвердить", callback_data=f"jobassign:confirm:{target_user_id}:{job_id}")],
        [InlineKeyboardButton(text="Изменить", callback_data=f"jobassign:change:{target_user_id}")],
        [InlineKeyboardButton(text="Отмена", callback_data=f"jobassign:cancel:{target_user_id}")],
    ])


def reports_quick_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сегодня", callback_data="reportquick:отчёт за сегодня"), InlineKeyboardButton(text="Неделя", callback_data="reportquick:отчёт за неделю")],
        [InlineKeyboardButton(text="Месяц", callback_data="reportquick:отчёт за месяц")],
        [InlineKeyboardButton(text="Отчёт из нескольких групп", callback_data="reportmulti:start")],
        [InlineKeyboardButton(text="Назад", callback_data="menu:main")],
    ])


HELP_TOPIC_LABELS: dict[str, str] = {
    "start": "С чего начать",
    "what": "Что для чего нужно",
    "setup": "Настройка",
    "jobs": "Должности",
    "items": "Изделия и склад",
    "work": "Ввод данных",
    "reports": "Отчёты",
    "examples": "Примеры",
}


def help_topic_keyboard(current: str = "start") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for key, label in HELP_TOPIC_LABELS.items():
        mark = "✅ " if key == current else ""
        row.append(InlineKeyboardButton(text=mark + label, callback_data=f"help:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="В меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def report_multi_keyboard(token: str, chats: list[dict], selected_scope_ids: set[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in chats[:60]:
        scope_id = int(item["scope_chat_id"])
        title = str(item.get("title") or scope_id)[:42]
        mark = "✅ " if scope_id in selected_scope_ids else "⬜ "
        rows.append([InlineKeyboardButton(text=mark + title, callback_data=f"reportmulti:toggle:{token}:{scope_id}")])
    rows.append([
        InlineKeyboardButton(text="Сегодня", callback_data=f"reportmulti:period:{token}:today"),
        InlineKeyboardButton(text="Неделя", callback_data=f"reportmulti:period:{token}:week"),
        InlineKeyboardButton(text="Месяц", callback_data=f"reportmulti:period:{token}:month"),
    ])
    rows.append([
        InlineKeyboardButton(text="Показать", callback_data=f"reportmulti:show:{token}"),
        InlineKeyboardButton(text="Excel", callback_data=f"reportmulti:file:{token}:xlsx"),
        InlineKeyboardButton(text="PDF", callback_data=f"reportmulti:file:{token}:pdf"),
    ])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:reports"), InlineKeyboardButton(text="В меню", callback_data="menu:main")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"reportmulti:cancel:{token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        rows.append([InlineKeyboardButton(text=f"{name} — {format_amount(qty)} {unit}", callback_data=f"components:{mode}:{component_id}")])
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
        [InlineKeyboardButton(text="Назад", callback_data=f"report:back:{token}"), InlineKeyboardButton(text="В меню", callback_data="menu:main")],
    ])


def resolve_operation_keyboard(pending_id: str, op_index: int, choices: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in choices[:40]:
        target_type = str(item.get("target_type") or "")
        target_id = int(item.get("target_id") or 0)
        name = str(item.get("name") or "Позиция")[:48]
        if not target_type or not target_id:
            continue
        rows.append([InlineKeyboardButton(text=name, callback_data=f"resolveop:{pending_id}:{op_index}:{target_type}:{target_id}")])
    rows.append([InlineKeyboardButton(text="Исправить сообщением", callback_data=f"edit:{pending_id}")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"cancel:{pending_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
