from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["BOT_DATA_DIR"] = "/tmp/production_bot_stock_position_exact_step37"
shutil.rmtree(Path(os.environ["BOT_DATA_DIR"]), ignore_errors=True)

from app import db
from app.services import accounting
from app.services import parser
from app.services import repository as repo


def main() -> None:
    db.init_db()
    chat_id = -1003737
    user_id = 3737
    repo.upsert_chat(chat_id, "Рабочая группа", "supergroup", connected=True)
    ok, _ = repo.create_entity(chat_id, "stock_item", "Маленькая трубка", "шт")
    assert ok
    item = repo.get_entity_by_name(chat_id, "stock_item", "маленькая трубка")
    assert item is not None

    operations, errors = parser.parse_message(chat_id, chat_id, "Производство маленькая трубка 5000")
    assert not errors, errors
    assert len(operations) == 1
    op = operations[0]
    assert op.operation_type == "production", op
    assert op.entity_type == "stock_item", op
    assert op.entity_id == item.id, op
    assert op.entity_name == "Маленькая трубка", op
    assert op.quantity == 5000.0, op
    assert op.needs_attention is False, op

    saved = accounting.apply_operations(chat_id, chat_id, user_id, [op.to_dict()], "Производство маленькая трубка 5000")
    assert saved == 1
    rows = accounting.db.fetchall("SELECT * FROM inventory WHERE chat_id=? AND entity_type='stock_item' AND entity_id=?", (chat_id, item.id))
    assert len(rows) == 1
    assert float(rows[0]["quantity"]) == 5000.0
    print("OK")


if __name__ == "__main__":
    main()
