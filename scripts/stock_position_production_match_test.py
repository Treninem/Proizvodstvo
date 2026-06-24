from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["BOT_DATA_DIR"] = "/tmp/production_bot_stock_position_match_test"
shutil.rmtree(Path(os.environ["BOT_DATA_DIR"]), ignore_errors=True)

from app import db
from app.services import parser
from app.services import repository as repo


def main() -> None:
    db.init_db()
    chat_id = -100777
    repo.upsert_chat(chat_id, "Тест", "supergroup", connected=True)
    repo.create_area(chat_id, "Участок 1")
    repo.create_area(chat_id, "Участок 2")
    repo.create_entity(chat_id, "stock_item", "Позиция 1", "шт")
    item = repo.get_entity_by_name(chat_id, "stock_item", "Позиция 1")
    assert item is not None
    repo.bind_stock_item_to_areas(chat_id, item.id, [area.id for area in repo.list_areas(chat_id)])

    operations, errors = parser.parse_message(chat_id, chat_id, "Производство позиция 1 5000")
    assert not errors, errors
    assert len(operations) == 1
    op = operations[0]
    assert op.operation_type == "production"
    assert op.entity_type == "stock_item"
    assert op.entity_id == item.id
    assert op.entity_name == "Позиция 1"
    assert op.quantity == 5000.0
    assert op.unit == "шт"
    assert op.needs_attention is False

    operations, _ = parser.parse_message(chat_id, chat_id, "Производство совсем новое название 5000")
    assert len(operations) == 1
    assert operations[0].needs_attention is True
    assert operations[0].entity_id is None

    print("stock_position_production_match_test: OK")


if __name__ == "__main__":
    main()
