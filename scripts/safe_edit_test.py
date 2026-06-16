from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
from pathlib import Path

try:
    import aiogram  # type: ignore
except ModuleNotFoundError:
    aiogram_mod = types.ModuleType("aiogram")
    types_mod = types.ModuleType("aiogram.types")
    types_mod.Message = object
    sys.modules["aiogram"] = aiogram_mod
    sys.modules["aiogram.types"] = types_mod

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="prod_bot_safe_edit_")

from app.handlers._safe import safe_edit_text


class SameMessage:
    async def edit_text(self, *_args, **_kwargs):
        raise Exception("Telegram server says - Bad Request: message is not modified: specified new message content and reply markup are exactly the same as a current content and reply markup of the message")


class OtherError:
    async def edit_text(self, *_args, **_kwargs):
        raise Exception("other error")


async def main() -> None:
    await safe_edit_text(SameMessage(), "test")
    try:
        await safe_edit_text(OtherError(), "test")
    except Exception as exc:
        assert "other error" in str(exc)
    else:
        raise AssertionError("other errors must not be hidden")
    print("OK")


asyncio.run(main())
