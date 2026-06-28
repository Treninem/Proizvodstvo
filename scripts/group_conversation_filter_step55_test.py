from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_DATA_DIR", "/tmp/prod_bot_step55_test")
shutil.rmtree(os.environ["BOT_DATA_DIR"], ignore_errors=True)

from app.db import init_db
from app.services import parser
from app.services import repository as repo
from app.services import accounting

CHAT_ID = -100550055
USER_ID = 5501


def main() -> None:
    init_db()
    repo.upsert_chat(CHAT_ID, "Рабочая группа", "supergroup", connected=True)
    ok, _ = repo.create_entity(CHAT_ID, "component", "Трубка", "шт")
    assert ok
    ok, _ = repo.create_entity(CHAT_ID, "component", "Деталь Б", "шт")
    assert ok
    ok, _ = repo.create_entity(CHAT_ID, "meter", "Счётчик", "кВт⋅ч")
    assert ok

    ignored = [
        "320 кВт за 8 часов. Т.е. 40 кВт в час",
        "Показания надо будет вечером снять 123",
        "Счётчик вроде странно показывает 123",
        "Думаю это касается и ЭВМ",
        "Надо проверить счётчик",
        "Электроэнергия дорогая стала 320 кВт за смену",
        "Трубка 50 шт",
        "Ромашка 100 шт",
    ]
    for text in ignored:
        assert not parser.looks_like_accounting(text), text
        ops, errors = parser.parse_message(CHAT_ID, CHAT_ID, text)
        assert not ops, (text, ops, errors)

    accepted = [
        "Сделали Трубка 50 шт",
        "Сделали сегодня:\nТрубка 50 шт\nРомашка 100 шт\nДеталь Б 500 шт",
        "Показание Счётчик 123456",
        "Ээ 1500",
        "Расход электроэнергии 320 кВт⋅ч",
    ]
    for text in accepted:
        assert parser.looks_like_accounting(text), text
        ops, _errors = parser.parse_message(CHAT_ID, CHAT_ID, text)
        assert ops, text

    ops, _errors = parser.parse_message(CHAT_ID, CHAT_ID, "Сделали сегодня:\nРомашка 100 шт")
    assert len(ops) == 1 and ops[0].needs_attention and ops[0].learning_phrase == "Ромашка", ops
    first_component = repo.list_entities(CHAT_ID, {"component"})[0]
    op_dict = ops[0].to_dict()
    op_dict.update({"entity_type": first_component.entity_type, "entity_id": first_component.id, "entity_name": first_component.name, "needs_attention": False})
    saved = accounting.apply_operations(CHAT_ID, CHAT_ID, USER_ID, [op_dict], "Сделали сегодня:\nРомашка 100 шт")
    assert saved == 1
    ops2, _errors2 = parser.parse_message(CHAT_ID, CHAT_ID, "Сделали Ромашка 50 шт")
    assert len(ops2) == 1 and ops2[0].entity_id == first_component.id and not ops2[0].needs_attention, ops2

    print("group_conversation_filter_step55_test OK")


if __name__ == "__main__":
    main()
