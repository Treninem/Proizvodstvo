from __future__ import annotations

from .. import db
from . import repository as repo


def _count(query: str, params: tuple = ()) -> int:
    row = db.fetchone(query, params)
    return int(row[0] or 0) if row else 0


def readiness_counts(chat_id: int) -> dict[str, int | str]:
    account = repo.get_active_account(chat_id)
    scope_chat_id = account.scope_chat_id if account else chat_id
    return {
        "account_name": account.name if account else "Текущий учёт",
        "areas": _count("SELECT COUNT(*) FROM areas WHERE chat_id=? AND is_archived=0", (scope_chat_id,)),
        "jobs": _count("SELECT COUNT(*) FROM job_titles WHERE chat_id=? AND is_archived=0", (scope_chat_id,)),
        "products": _count("SELECT COUNT(*) FROM entities WHERE chat_id=? AND entity_type='product' AND is_archived=0", (scope_chat_id,)),
        "components": _count("SELECT COUNT(*) FROM entities WHERE chat_id=? AND entity_type='component' AND is_archived=0", (scope_chat_id,)),
        "materials": _count("SELECT COUNT(*) FROM entities WHERE chat_id=? AND entity_type='material' AND is_archived=0", (scope_chat_id,)),
        "stock_items": _count("SELECT COUNT(*) FROM entities WHERE chat_id=? AND entity_type='stock_item' AND is_archived=0", (scope_chat_id,)),
        "meters": _count("SELECT COUNT(*) FROM entities WHERE chat_id=? AND entity_type='meter' AND is_archived=0", (scope_chat_id,)),
        "connected_chats": _count(
            """
            SELECT COUNT(*) FROM account_chat_access aca
            JOIN accounting_accounts aa ON aa.id=aca.account_id
            WHERE aa.scope_chat_id=?
            """,
            (scope_chat_id,),
        ) if account else _count("SELECT COUNT(*) FROM chats WHERE is_connected=1"),
        "workers": _count("SELECT COUNT(*) FROM workers WHERE chat_id=? AND is_active=1", (scope_chat_id,)),
        "aliases": _count("SELECT COUNT(*) FROM aliases WHERE chat_id=?", (scope_chat_id,)),
        "operations": _count("SELECT COUNT(*) FROM operations WHERE chat_id=?", (scope_chat_id,)),
    }


def _state_label(value: int | str | bool) -> str:
    return "настроено" if value else "нужно настроить"


def build_readiness_text(chat_id: int) -> str:
    c = readiness_counts(chat_id)
    has_positions = any(c[k] for k in ("products", "components", "materials", "stock_items", "meters"))
    ready_basic = bool(c["areas"] and c["jobs"] and has_positions)
    ready_work = bool(c["connected_chats"] and c["workers"])

    status = "можно принимать данные" if ready_basic and ready_work else "нужно закончить настройку"
    lines = [
        "Состояние учёта",
        "",
        f"Учёт: {c['account_name']}",
        f"Сейчас: {status}",
        "",
        "Проверка:",
        f"• участки — {_state_label(c['areas'])}",
        f"• должности — {_state_label(c['jobs'])}",
        f"• позиции для учёта — {_state_label(has_positions)}",
        f"• рабочие чаты — {_state_label(c['connected_chats'])}",
        f"• работники — {_state_label(c['workers'])}",
    ]

    todo: list[str] = []
    if not c["areas"]:
        todo.append("создайте участок")
    if not c["jobs"]:
        todo.append("создайте должность и отметьте права")
    if not has_positions:
        todo.append("добавьте изделия, комплектующие, сырьё, складские позиции или счётчики")
    if not c["connected_chats"]:
        todo.append("подключите рабочий чат")
    if not c["workers"]:
        todo.append("назначьте работников или должности")
    if todo:
        lines += ["", "Следующий шаг:"]
        lines += [f"• {item}" for item in todo]
    else:
        lines += ["", "Учёт готов. Можно вводить данные и смотреть отчёты."]

    lines += ["", "Нажмите «Настроить учёт» и выберите нужный пункт — бот подскажет, что написать дальше."]
    return "\n".join(lines)
