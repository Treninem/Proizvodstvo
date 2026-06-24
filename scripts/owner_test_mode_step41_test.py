from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="prod_bot_step41_test_mode_")
os.environ["GLOBAL_OWNER_IDS"] = "2097006037"

from app import db
from app.db import init_db
from app.services import accounting, repository as repo
from app.services.parser import parse_message


def _save(chat_id: int, user_id: int, text: str, dry_run: bool = False) -> int:
    ops, errors = parse_message(chat_id, chat_id, text)
    assert not errors, (text, errors)
    assert ops, text
    return accounting.apply_operations(chat_id, chat_id, user_id, [op.to_dict() for op in ops], text, dry_run=dry_run)


def main() -> None:
    init_db()
    owner = 2097006037
    other_user = 3003
    chat_a = -4101
    chat_b = -4102
    repo.set_chat_connected(chat_a, "Группа А", "supergroup", True)
    repo.set_chat_connected(chat_b, "Группа Б", "supergroup", True)
    ok, msg = repo.create_entity(chat_a, "component", "Комплектующая 1", "шт")
    assert ok, msg
    ok, msg = repo.create_entity(chat_b, "component", "Комплектующая 1", "шт")
    assert ok, msg

    # Проверочный режим доступен владельцу и не включается обычному пользователю.
    assert not repo.is_user_test_mode_enabled(owner)
    assert repo.toggle_user_test_mode(owner) is True
    assert repo.is_user_test_mode_enabled(owner)
    repo.set_user_test_mode(other_user, True)
    assert not repo.is_user_test_mode_enabled(other_user)

    # Пробная запись считается как проверенная, но не попадает в операции и склад.
    saved = _save(chat_a, owner, "Производство Комплектующая 1 5000", dry_run=repo.is_user_test_mode_enabled(owner))
    assert saved == 1
    assert db.fetchone("SELECT COUNT(*) AS n FROM operations WHERE chat_id=?", (chat_a,))["n"] == 0
    ent_a = repo.get_entity_by_name(chat_a, "component", "Комплектующая 1")
    assert ent_a is not None
    assert repo.inventory_quantity(chat_a, "component", ent_a.id, "шт") == 0

    # Настоящая запись другого пользователя сохраняется только в своём учёте.
    saved_real = _save(chat_b, other_user, "Производство Комплектующая 1 7000", dry_run=repo.is_user_test_mode_enabled(other_user))
    assert saved_real == 1
    assert db.fetchone("SELECT COUNT(*) AS n FROM operations WHERE chat_id=?", (chat_b,))["n"] == 1
    ent_b = repo.get_entity_by_name(chat_b, "component", "Комплектующая 1")
    assert ent_b is not None
    assert repo.inventory_quantity(chat_b, "component", ent_b.id, "шт") == 7000
    assert db.fetchone("SELECT COUNT(*) AS n FROM operations WHERE chat_id=?", (chat_a,))["n"] == 0
    assert repo.inventory_quantity(chat_a, "component", ent_a.id, "шт") == 0

    # Ожидающая проверочная запись помечается отдельно, чтобы подтверждение тоже не стало настоящей записью.
    pending_id = accounting.create_pending(chat_a, chat_a, owner, {"operations": [], "raw_text": "проверка", "is_test": True})
    row = db.fetchone("SELECT is_test FROM pending_confirmations WHERE id=?", (pending_id,))
    assert row and row["is_test"] == 1
    found = accounting.get_pending(chat_a, chat_a, owner)
    assert found and found[1].get("is_test") is True

    print("OK")


if __name__ == "__main__":
    main()
