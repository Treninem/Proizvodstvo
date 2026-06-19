from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_TOKEN_PLACEHOLDERS = {
    "",
    "PUT_TELEGRAM_BOT_TOKEN_HERE",
    "PASTE_NEW_TOKEN_HERE",
    "ВСТАВЬТЕ_НОВЫЙ_КЛЮЧ_СЮДА",
}
_TOKEN_RE = re.compile(r"^\d{6,14}:[A-Za-z0-9_-]{20,}$")


def _parse_owner_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.add(int(part))
        except ValueError:
            continue
    return result


def _looks_like_token(value: str) -> bool:
    return bool(_TOKEN_RE.match(value.strip()))


@dataclass(frozen=True)
class Settings:
    bot_token: str
    global_owner_ids: set[int]
    data_dir: Path
    database_path: Path

    def require_ready(self) -> None:
        token = self.bot_token.strip()
        if token in _TOKEN_PLACEHOLDERS:
            raise RuntimeError("В .env не указан ключ бота. Создайте новый ключ в BotFather и вставьте его только в .env.")
        if not _looks_like_token(token):
            raise RuntimeError("Ключ бота в .env выглядит неверно. Проверьте значение BOT_TOKEN.")
        if not self.global_owner_ids:
            raise RuntimeError("В .env не указан GLOBAL_OWNER_IDS.")
        self.data_dir.mkdir(parents=True, exist_ok=True)


_data_dir = Path(os.getenv("BOT_DATA_DIR", "./data")).resolve()

settings = Settings(
    bot_token=os.getenv("BOT_TOKEN", "").strip(),
    global_owner_ids=_parse_owner_ids(os.getenv("GLOBAL_OWNER_IDS", "2097006037")),
    data_dir=_data_dir,
    database_path=_data_dir / "production_account.sqlite3",
)
