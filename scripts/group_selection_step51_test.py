from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="prod_bot_step51_")

from app.db import init_db
from app.services import repository as repo
from app.keyboards import chat_list_keyboard, report_multi_keyboard


def keyboard_text(markup) -> str:
    return str(markup.model_dump())


def main() -> None:
    init_db()
    repo.set_chat_connected(-1001, "Группа", "supergroup", True)
    repo.set_chat_connected(-1002, "Группа", "supergroup", True)
    repo.set_chat_connected(-1003, "Другая", "supergroup", True)
    chats = repo.list_known_group_chats()

    setup_plain = keyboard_text(chat_list_keyboard(chats))
    assert "chatpick:" not in setup_plain, setup_plain
    assert setup_plain.count("⚪") == 3, setup_plain
    assert "chatselect:-1001" in setup_plain and "chatselect:-1002" in setup_plain, setup_plain
    assert "chatsetup:selected" in setup_plain, setup_plain
    assert "reportmulti:start" in setup_plain, setup_plain

    setup_selected = keyboard_text(chat_list_keyboard(chats, selected_chat_id=-1002))
    assert setup_selected.count("🔘") == 1, setup_selected
    assert setup_selected.count("⚪") == 2, setup_selected
    assert "Группа · 1" in setup_selected and "Группа · 2" in setup_selected, setup_selected

    scopes = [
        {"scope_chat_id": -9001, "title": "Учёт"},
        {"scope_chat_id": -9002, "title": "Учёт"},
        {"scope_chat_id": -9003, "title": "Другой учёт"},
    ]
    multi_plain = keyboard_text(report_multi_keyboard("tok", scopes, set()))
    assert "✅" not in multi_plain, multi_plain
    assert multi_plain.count("⬜") == 3, multi_plain
    assert "reportmulti:toggle:tok:-9001" in multi_plain, multi_plain

    multi_selected = keyboard_text(report_multi_keyboard("tok", scopes, {-9001, -9003}))
    assert multi_selected.count("✅") == 2, multi_selected
    assert multi_selected.count("⬜") == 1, multi_selected
    print("OK")


if __name__ == "__main__":
    main()
