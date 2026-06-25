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


def dump(markup) -> str:
    return str(markup.model_dump())


def main() -> None:
    init_db()
    repo.set_chat_connected(-101, "Цех", "supergroup", True)
    repo.set_chat_connected(-102, "Цех", "supergroup", True)
    repo.set_chat_connected(-103, "Склад", "supergroup", True)
    chats = repo.list_known_group_chats()

    setup_plain = dump(chat_list_keyboard(chats, selected_chat_id=None))
    assert "🔘" not in setup_plain, setup_plain
    assert setup_plain.count("⚪") == 3, setup_plain
    assert "chatselect:-101" in setup_plain, setup_plain
    assert "chatsetup:selected" in setup_plain, setup_plain
    assert "Выбрать группы для отчёта" in setup_plain, setup_plain
    assert "reportmulti:start" in setup_plain, setup_plain

    setup_selected = dump(chat_list_keyboard(chats, selected_chat_id=-102))
    assert setup_selected.count("🔘") == 1, setup_selected
    assert setup_selected.count("⚪") == 2, setup_selected
    assert "Цех · 1" in setup_selected and "Цех · 2" in setup_selected, setup_selected

    scopes = [
        {"scope_chat_id": -101, "title": "Цех"},
        {"scope_chat_id": -102, "title": "Цех"},
        {"scope_chat_id": -103, "title": "Склад"},
    ]
    report_plain = dump(report_multi_keyboard("tok", scopes, set()))
    assert "✅" not in report_plain, report_plain
    assert report_plain.count("⬜") == 3, report_plain
    report_two = dump(report_multi_keyboard("tok", scopes, {-101, -103}))
    assert report_two.count("✅") == 2, report_two
    assert report_two.count("⬜") == 1, report_two
    assert "Показать" in report_two and "Excel" in report_two and "PDF" in report_two, report_two
    print("OK")


if __name__ == "__main__":
    main()
