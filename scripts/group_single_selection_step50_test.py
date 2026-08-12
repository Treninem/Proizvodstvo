from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

aiogram = types.ModuleType("aiogram")
aiogram_types = types.ModuleType("aiogram.types")

class WebAppInfo:
    def __init__(self, url: str):
        self.url = url

    def model_dump(self):
        return {"url": self.url}

class InlineKeyboardButton:
    def __init__(self, text: str, callback_data: str | None = None, web_app=None):
        self.text = text
        self.callback_data = callback_data
        self.web_app = web_app

    def model_dump(self):
        return {"text": self.text, "callback_data": self.callback_data, "web_app": self.web_app.model_dump() if self.web_app else None}

class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard

    def model_dump(self):
        return {"inline_keyboard": [[btn.model_dump() for btn in row] for row in self.inline_keyboard]}

aiogram_types.InlineKeyboardButton = InlineKeyboardButton
aiogram_types.WebAppInfo = WebAppInfo
aiogram_types.InlineKeyboardMarkup = InlineKeyboardMarkup
sys.modules.setdefault("aiogram", aiogram)
sys.modules.setdefault("aiogram.types", aiogram_types)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="prod_bot_step50_")

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

    plain = keyboard_text(chat_list_keyboard(chats))
    assert "🔘" not in plain, plain
    assert plain.count("⚪") == 3, plain

    selected = keyboard_text(chat_list_keyboard(chats, selected_chat_id=-1002))
    assert selected.count("🔘") == 1, selected
    assert selected.count("⚪") == 2, selected
    assert "Группа · 1" in selected and "Группа · 2" in selected, selected
    assert "chatselect:-1002" in selected, selected
    assert "chatsetup:selected" in selected, selected
    assert "reportmulti:start" in selected, selected
    assert "Выбрать группы для отчёта" in selected, selected

    scopes = [
        {"scope_chat_id": -9001, "title": "Учёт"},
        {"scope_chat_id": -9002, "title": "Учёт"},
        {"scope_chat_id": -9003, "title": "Другой учёт"},
    ]
    multi_plain = keyboard_text(report_multi_keyboard("tok", scopes, set()))
    assert "✅" not in multi_plain, multi_plain
    assert multi_plain.count("⬜") == 3, multi_plain
    assert "Учёт · 1" in multi_plain and "Учёт · 2" in multi_plain, multi_plain
    multi_selected = keyboard_text(report_multi_keyboard("tok", scopes, {-9002}))
    assert multi_selected.count("✅") == 1, multi_selected
    assert multi_selected.count("⬜") == 2, multi_selected
    print("OK")


if __name__ == "__main__":
    main()
