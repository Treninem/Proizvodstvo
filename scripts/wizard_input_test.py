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
        def startswith(self, *_args, **_kwargs):
            return self
        def __eq__(self, _other):
            return self
    class _DummyF:
        data = _DummyFilter()
    class _DummyRouter:
        def callback_query(self, *_args, **_kwargs):
            def deco(func):
                return func
            return deco
        def message(self, *_args, **_kwargs):
            def deco(func):
                return func
            return deco
    class _DummyButton:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
    class _DummyMarkup:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
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
os.environ["BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="prod_bot_wizard_")
os.environ.setdefault("GLOBAL_OWNER_IDS", "1001")

from app.db import init_db
from app.handlers.setup import try_handle_wizard_message
from app.services import repository as repo


class FakeBot:
    def __init__(self) -> None:
        self.deleted: list[tuple[int, int]] = []

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))


class FakeUser:
    id = 1001
    first_name = "Участник"
    last_name = None
    username = None


class FakeChat:
    id = 5001
    type = "private"
    title = None
    full_name = "Личный чат"


class FakeSent:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id


class FakeMessage:
    def __init__(self, text: str, bot: FakeBot) -> None:
        self.text = text
        self.bot = bot
        self.chat = FakeChat()
        self.from_user = FakeUser()
        self.deleted = False
        self.answers: list[tuple[str, object]] = []

    async def delete(self) -> None:
        self.deleted = True

    async def answer(self, text: str, reply_markup=None):
        self.answers.append((text, reply_markup))
        return FakeSent(9000 + len(self.answers))


async def main_async() -> None:
    init_db()
    chat_id = FakeChat.id
    user_id = FakeUser.id
    repo.upsert_chat(chat_id, "Личный чат", "private", connected=None)
    repo.set_setup_session(chat_id, user_id, "await_area_name", {"prompt_message_id": 123})
    bot = FakeBot()
    message = FakeMessage("Участок 1", bot)
    handled = await try_handle_wizard_message(message)
    assert handled is True
    assert message.deleted is True
    assert (chat_id, 123) in bot.deleted
    assert repo.list_areas(chat_id)[0].name == "Участок 1"
    session = repo.get_setup_session(chat_id, user_id)
    assert session and session["state"] == "await_aliases"
    assert session["data"].get("prompt_message_id") == 9001
    assert message.answers and "Участок создан" in message.answers[0][0]

    ok, msg = repo.create_entity(chat_id, "product", "Изделие 1", "шт")
    assert ok, msg
    repo.set_setup_session(chat_id, user_id, "await_product_for_components", {"prompt_message_id": 200})
    message_product = FakeMessage("Изделие 1", bot)
    handled = await try_handle_wizard_message(message_product)
    assert handled is True
    session = repo.get_setup_session(chat_id, user_id)
    assert session and session["state"] == "choose_product_components_action", session
    repo.set_setup_session(chat_id, user_id, "await_product_components_replace", session["data"])

    message_components = FakeMessage("Комплектующая 1 2, Комплектующая 2 3", bot)
    handled = await try_handle_wizard_message(message_components)
    assert handled is True
    components = repo.list_entities(chat_id, {"component"})
    assert {c.name for c in components} == {"Комплектующая 1", "Комплектующая 2"}
    product = repo.get_entity_by_name(chat_id, "product", "Изделие 1")
    assert product is not None
    assert len(repo.list_product_components(product.id)) == 2
    session = repo.get_setup_session(chat_id, user_id)
    assert session and session["state"] == "await_component_aliases", session
    assert message_components.answers and "Сокращения для" in message_components.answers[0][0]


def main() -> None:
    asyncio.run(main_async())
    print("OK")


if __name__ == "__main__":
    main()
