from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from .services import telegram_users


def _remember(user: Any, chat: Any = None) -> None:
    if not user:
        return
    full_name = " ".join(
        part for part in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if part
    ).strip()
    telegram_users.remember_user(
        getattr(user, "id", None),
        getattr(user, "username", None),
        full_name,
        getattr(chat, "id", None) if chat is not None else None,
    )


class UserDirectoryMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        _remember(user, chat)

        reply = getattr(event, "reply_to_message", None)
        if reply is not None:
            _remember(getattr(reply, "from_user", None), getattr(reply, "chat", chat))

        message = getattr(event, "message", None)
        if message is not None:
            _remember(getattr(message, "from_user", None), getattr(message, "chat", chat))
            nested_reply = getattr(message, "reply_to_message", None)
            if nested_reply is not None:
                _remember(getattr(nested_reply, "from_user", None), getattr(nested_reply, "chat", chat))

        return await handler(event, data)
