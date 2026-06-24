from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="prod_bot_step44_compact_report_")

from app.db import init_db
from app.services import accounting, reporting, repository as repo
from app.services.parser import parse_message


def _save(chat_id: int, user_id: int, text: str) -> None:
    ops, errors = parse_message(chat_id, chat_id, text)
    assert not errors, (text, errors)
    saved = accounting.apply_operations(chat_id, chat_id, user_id, [op.to_dict() for op in ops], text)
    assert saved == 1, (text, saved, [op.to_dict() for op in ops])


def main() -> None:
    init_db()
    chat_id = -4401
    user_id = 4401
    repo.set_chat_connected(chat_id, "Группа", "supergroup", True)

    for name in ("Деталь А", "Деталь Б", "Деталь В", "Деталь Г"):
        ok, msg = repo.create_entity(chat_id, "component", name, "шт")
        assert ok, msg
    ok, msg = repo.create_entity(chat_id, "product", "Изделие А", "шт")
    assert ok, msg
    products = {item.name: item for item in repo.list_entities(chat_id, {"product"})}
    comps = {item.name: item for item in repo.list_entities(chat_id, {"component"})}
    repo.set_product_components(chat_id, products["Изделие А"].id, [
        (comps["Деталь А"].id, 1),
        (comps["Деталь Б"].id, 2),
        (comps["Деталь В"].id, 1),
        (comps["Деталь Г"].id, 40),
    ])

    _save(chat_id, user_id, "Производство Деталь А 13000")
    _save(chat_id, user_id, "Производство Деталь Б 22000")
    _save(chat_id, user_id, "Собрали Изделие А 1000")
    _save(chat_id, user_id, "Зафасовали Изделие А 500")

    text = reporting.build_text_report(chat_id, "отчёт за месяц", user_id=user_id)
    assert "Производство комплектующих:" in text, text
    assert "• Деталь А — 13 000 шт" in text, text
    assert "• Деталь Б — 22 000 шт" in text, text
    assert "Производство:\n" not in text, text
    assert "Производство: 35 000" not in text, text
    assert "Изделие А · 10 000" not in text, text
    assert "Изделие А · 50 000" not in text, text
    assert "Изделие А · 100 000" not in text, text
    assert "Полная таблица доступна в Excel/PDF" in text, text
    assert "можно собрать -" not in text, text

    plan_text = reporting.build_assembly_capacity_report(chat_id, "план сборки Изделие А 10000")
    assert "10 000" in plan_text, plan_text
    assert "Деталь А" not in plan_text or "не хватает" in plan_text, plan_text
    assert "4e+" not in plan_text.lower(), plan_text
    print("OK")


if __name__ == "__main__":
    main()
