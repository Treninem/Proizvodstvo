from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_DATA_DIR", "/tmp/prod_bot_step52_test")
shutil.rmtree(os.environ["BOT_DATA_DIR"], ignore_errors=True)

from app.db import init_db
from app.services import parser
from app.services import repository as repo

CHAT_ID = -100520052


def main() -> None:
    init_db()
    repo.upsert_chat(CHAT_ID, "Рабочая группа", "supergroup", connected=True)
    for entity_type, name in [
        ("component", "Деталь верхняя"),
        ("stock_item", "Деталь средняя"),
        ("component", "Деталь нижняя"),
        ("component", "Деталь боковая"),
        ("meter", "Счётчик"),
    ]:
        ok, msg = repo.create_entity(CHAT_ID, entity_type, name)
        assert ok, msg

    ignored = [
        "Да, помните, что говорили про прогрев.",
        "Думаю это касается и ЭВМ.",
        "Надо купить хорошие огнетушители на всякий случай.",
        "Что там со счётчиком?",
        "Показания надо будет вечером снять 123",
        "Счётчик вроде странно показывает 123",
        "Заработал7",
    ]
    for text in ignored:
        assert not parser.looks_like_accounting(text), text
        ops, errors = parser.parse_message(CHAT_ID, CHAT_ID, text)
        assert not ops, (text, ops, errors)

    text = "Сделал сегодня деталь среднюю 50шт\nПозицию 100шт\nДеталь нижняя 500 шт"
    assert parser.looks_like_accounting(text)
    ops, errors = parser.parse_message(CHAT_ID, CHAT_ID, text)
    assert len(ops) == 3, ops
    assert ops[0].operation_type == "production"
    assert ops[0].quantity == 50
    assert ops[0].needs_attention is True
    assert ops[0].variants, "short name should offer saved choices"
    assert ops[1].quantity == 100
    assert ops[1].needs_attention is True
    assert ops[2].entity_name == "Деталь нижняя"
    assert ops[2].quantity == 500

    meter_text = "Показание Счётчик 123456"
    assert parser.looks_like_accounting(meter_text)
    ops, _errors = parser.parse_message(CHAT_ID, CHAT_ID, meter_text)
    assert len(ops) == 1
    assert ops[0].operation_type == "energy"
    assert ops[0].quantity == 123456

    print("group_intake_filter_step52_test OK")


if __name__ == "__main__":
    main()
