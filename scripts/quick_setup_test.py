from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
import types

try:
    import aiogram  # type: ignore
except ModuleNotFoundError:
    class _DummyFilter:
        def startswith(self, *_args, **_kwargs): return self
        def __eq__(self, _other): return self
    class _DummyF: data = _DummyFilter()
    class _DummyRouter:
        def callback_query(self, *_args, **_kwargs):
            def deco(func): return func
            return deco
        def message(self, *_args, **_kwargs):
            def deco(func): return func
            return deco
    class _DummyButton:
        def __init__(self, **kwargs): self.kwargs = kwargs
    class _DummyMarkup:
        def __init__(self, **kwargs): self.kwargs = kwargs
    aiogram_mod = types.ModuleType("aiogram")
    aiogram_mod.F = _DummyF()
    aiogram_mod.Router = _DummyRouter
    aiogram_mod.Bot = object
    types_mod = types.ModuleType("aiogram.types")
    types_mod.CallbackQuery = object
    types_mod.Message = object
    types_mod.Chat = object
    types_mod.User = object
    types_mod.InlineKeyboardButton = _DummyButton
    types_mod.InlineKeyboardMarkup = _DummyMarkup
    sys.modules["aiogram"] = aiogram_mod
    sys.modules["aiogram.types"] = types_mod

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="prod_bot_quick_")
os.environ.setdefault("GLOBAL_OWNER_IDS", "1001")

from app.db import init_db
from app.handlers.setup import try_handle_wizard_message
from app.services import repository as repo

class FakeBot:
    async def delete_message(self, chat_id: int, message_id: int) -> None: pass
class FakeUser:
    id = 1001; first_name = "User"; last_name = None; username = None
class FakeChat:
    id = 62001; type = "private"; title = None; full_name = "Личный чат"
class FakeSent:
    def __init__(self, message_id: int) -> None: self.message_id = message_id
class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text; self.bot = FakeBot(); self.chat = FakeChat(); self.from_user = FakeUser(); self.answers=[]
    async def delete(self) -> None: pass
    async def answer(self, text: str, reply_markup=None):
        self.answers.append((text, reply_markup)); return FakeSent(9000 + len(self.answers))

async def main_async() -> None:
    init_db()
    chat_id = FakeChat.id; user_id = FakeUser.id
    repo.upsert_chat(chat_id, "Личный чат", "private", connected=None)
    repo.set_setup_session(chat_id, user_id, "quick_area_name", {"prompt_message_id": 1})
    for text in ["Участок 1", "Должность 1", "Изделие 1", "Комплектующая 1 2, Комплектующая 2 1", "Сырьё 1", "Счётчик 1"]:
        handled = await try_handle_wizard_message(FakeMessage(text))
        assert handled is True
    assert repo.list_areas(chat_id)
    assert repo.find_job_title(chat_id, "Должность 1")
    product = repo.get_entity_by_name(chat_id, "product", "Изделие 1")
    assert product and len(repo.list_product_components(product.id)) == 2
    assert repo.get_entity_by_name(chat_id, "material", "Сырьё 1")
    assert repo.get_entity_by_name(chat_id, "meter", "Счётчик 1")
    assert repo.get_setup_session(chat_id, user_id) is None


def main() -> None:
    asyncio.run(main_async())
    print("OK")

if __name__ == "__main__":
    main()
