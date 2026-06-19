from __future__ import annotations

try:
    from aiogram.exceptions import TelegramBadRequest
except Exception:  # тестовая среда может подставлять лёгкую заглушку aiogram
    class TelegramBadRequest(Exception):
        pass

from aiogram.types import Message


async def safe_edit_text(message: Message, text: str, **kwargs) -> None:
    """Редактирует сообщение без падения, если Телеграм вернул, что текст не изменился."""
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise
    except Exception as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise
