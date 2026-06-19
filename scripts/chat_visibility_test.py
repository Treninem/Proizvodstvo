from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

# Лёгкая заглушка для проверки клавиатур без установленного aiogram.
aiogram = types.ModuleType("aiogram")
aiogram_types = types.ModuleType("aiogram.types")

class InlineKeyboardButton:
    def __init__(self, text: str, callback_data: str | None = None):
        self.text = text
        self.callback_data = callback_data

    def model_dump(self):
        return {"text": self.text, "callback_data": self.callback_data}

class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard

    def model_dump(self):
        return {"inline_keyboard": [[btn.model_dump() for btn in row] for row in self.inline_keyboard]}

aiogram_types.InlineKeyboardButton = InlineKeyboardButton
aiogram_types.InlineKeyboardMarkup = InlineKeyboardMarkup
sys.modules.setdefault("aiogram", aiogram)
sys.modules.setdefault("aiogram.types", aiogram_types)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="prod_bot_chat_visibility_")

from app.db import init_db
from app.services import repository as repo
from app.keyboards import main_menu, chat_list_keyboard


def main() -> None:
    init_db()
    repo.set_chat_connected(-1001, "Группа 1", "supergroup", True)
    repo.set_chat_connected(-1002, "Группа 2", "group", False)
    assert len(repo.list_known_group_chats()) == 2
    assert repo.get_chat_info(-1001)["title"] == "Группа 1"
    assert repo.user_has_manage_access_to_chat(-1001, 999) is False
    repo.upsert_chat(777, "Личный чат", "private", connected=True)
    ok, msg, account_id = repo.create_account(777, 777, "Учёт 1")
    assert ok and account_id, msg
    repo.attach_chat_to_account(account_id, -1001, can_manage=True)
    repo.grant_account_user_access(account_id, 999, None, display_manage=True)
    assert repo.user_has_manage_access_to_chat(-1001, 999) is True
    kb_text = str(main_menu().model_dump())
    assert "Группы" in kb_text
    chat_kb = str(chat_list_keyboard(repo.list_known_group_chats()).model_dump())
    assert "Группа 1" in chat_kb and "Группа 2" in chat_kb
    print("OK")


if __name__ == "__main__":
    main()
