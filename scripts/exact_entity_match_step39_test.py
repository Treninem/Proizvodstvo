from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="prod_bot_exact_match_")

from app.db import init_db
from app.services import accounting, repository as repo
from app.services.parser import parse_message


def main() -> None:
    init_db()
    chat_id = -3901
    user_id = 3901
    repo.set_chat_connected(chat_id, "Группа", "supergroup", True)
    for name in ("Комплектующая 1", "Комплектующая 2"):
        ok, msg = repo.create_entity(chat_id, "component", name, "шт")
        assert ok, msg

    ops, errors = parse_message(chat_id, chat_id, "Производство Комплектующая 1 5000")
    assert not errors, errors
    assert len(ops) == 1, [op.to_dict() for op in ops]
    op = ops[0].to_dict()
    assert not op["needs_attention"], op
    assert op["entity_name"] == "Комплектующая 1", op
    assert op["quantity"] == 5000.0, op
    saved = accounting.apply_operations(chat_id, chat_id, user_id, [op], "Производство Комплектующая 1 5000")
    assert saved == 1, saved

    ops2, errors2 = parse_message(chat_id, chat_id, "Производство Комплектующая 5000")
    assert not errors2, errors2
    assert len(ops2) == 1, [op.to_dict() for op in ops2]
    op2 = ops2[0].to_dict()
    assert op2["needs_attention"], op2
    assert len(op2.get("variants") or []) >= 2, op2
    print("OK")


if __name__ == "__main__":
    main()
