from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_DATA_DIR", "/tmp/prod_bot_step57_test")
shutil.rmtree(os.environ["BOT_DATA_DIR"], ignore_errors=True)

from app.db import init_db
from app.services import parser
from app.services.command_intents import backup_request_kind
from app.services import inventory_adjustment
from app.services import repository as repo

CHAT_ID = -100570057


def main() -> None:
    init_db()
    repo.upsert_chat(CHAT_ID, "Рабочая группа", "supergroup", connected=True)
    for entity_type, name in [
        ("component", "Трубка"),
        ("component", "Деталь А"),
        ("component", "Деталь Б"),
        ("meter", "Счётчик"),
    ]:
        ok, msg = repo.create_entity(CHAT_ID, entity_type, name)
        assert ok, msg

    ordinary_messages = [
        "Здесь пока ничего не штробите, всё по воздуху или по полу не заглубляясь",
        "Резервный — это какой?",
        "Электричество используем наш резервный ввод",
        "Давайте подумаем про резервный счётчик",
        "Надо сделать копию чертежа",
        "Копия документа лежит в папке",
        "Тройник 32 мм - 4 шт.\nКран 32 - 2 шт.\nТруба 32 - 12 м",
        "467,107-453,091 = 14,016 = 280.32 кВт в день",
        "Показания надо будет снять вечером",
        "Остаток трубы примерно 5 метров, надо проверить",
    ]
    for text in ordinary_messages:
        assert backup_request_kind(text) is None, text
        assert not parser.looks_like_accounting(text), text
        assert not inventory_adjustment.looks_like_inventory_adjustment(text), text
        ops, errors = parser.parse_message(CHAT_ID, CHAT_ID, text)
        assert not ops, (text, ops, errors)

    explicit_backups = {
        "Копия учёта": "account",
        "Резервная копия учёта": "account",
        "/backup": "account",
        "Полная копия базы": "full",
        "/backup_full": "full",
        "Список копий": "list",
    }
    for text, expected in explicit_backups.items():
        assert backup_request_kind(text) == expected, (text, backup_request_kind(text), expected)

    accepted_operations = [
        "Сделали Трубка 50 шт",
        "Сделали сегодня:\nТрубка 50 шт\nДеталь Б 500 шт",
        "Показание Счётчик 123456",
        "Ээ 1500",
    ]
    for text in accepted_operations:
        assert parser.looks_like_accounting(text), text
        ops, _errors = parser.parse_message(CHAT_ID, CHAT_ID, text)
        assert ops, text

    # Инвентаризация должна оставаться отдельной явной операцией.
    assert inventory_adjustment.looks_like_inventory_adjustment("Инвентаризация Трубка 50 шт")

    print("group_conversation_filter_step57_test OK")


if __name__ == "__main__":
    main()
