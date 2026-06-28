from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_DATA_DIR", "/tmp/prod_bot_step56_test")
shutil.rmtree(os.environ["BOT_DATA_DIR"], ignore_errors=True)

from app.db import init_db
from app.services import parser
from app.services import repository as repo

CHAT_ID = -100560056


def main() -> None:
    init_db()
    repo.upsert_chat(CHAT_ID, "Рабочая группа", "supergroup", connected=True)
    for entity_type, name in [
        ("component", "Трубка"),
        ("component", "Деталь Б"),
        ("meter", "Счётчик"),
    ]:
        ok, msg = repo.create_entity(CHAT_ID, entity_type, name)
        assert ok, msg

    ignored = [
        "467,107-453,091 = 14,016 = 280.32\nкВт в день.\n10,42494 рубля за кВт без НДС.\n12,718 с НДС.\nЭлектричество 1,4612 рубля\nна тарелку безндсных, 1,7826\nндсных",
        "467,107-453,091 = 14,016 = 280.32 кВт в день",
        "10,42494 рубля за кВт без НДС",
        "12,718 с НДС",
        "Электричество 1,4612 рубля на единицу",
        "320 кВт за 8 часов. Т.е. 40 кВт в час",
        "кВт в день 280.32",
        "467 кВт в день",
        "счётчик 467 показывает странно",
    ]
    for text in ignored:
        assert not parser.looks_like_accounting(text), text
        ops, errors = parser.parse_message(CHAT_ID, CHAT_ID, text)
        assert not ops, (text, ops, errors)

    accepted = [
        "Сделали Трубка 50 шт",
        "Сделали сегодня:\nТрубка 50 шт\nДеталь Б 500 шт",
        "Показание Счётчик 123456",
        "Ээ 1500",
        "Расход электроэнергии 320 кВт⋅ч",
    ]
    for text in accepted:
        assert parser.looks_like_accounting(text), text
        ops, _errors = parser.parse_message(CHAT_ID, CHAT_ID, text)
        assert ops, text

    print("group_conversation_filter_step56_test OK")


if __name__ == "__main__":
    main()
