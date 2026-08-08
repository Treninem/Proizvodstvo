from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
# Переменные панели хостинга имеют наивысший приоритет.
# Локальный .env используется при наличии, runtime.defaults.env — запасные значения.
load_dotenv(_ROOT / ".env", override=False)
load_dotenv(_ROOT / "runtime.defaults.env", override=False)

_TOKEN_PLACEHOLDERS = {"", "PASTE_NEW_TOKEN_HERE", "PUT_TELEGRAM_BOT_TOKEN_HERE"}
_TOKEN_RE = re.compile(r"^\d{6,14}:[A-Za-z0-9_-]{20,}$")


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "да"}


def _parse_int(raw: str | None, default: int = 0, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(str(raw or default).strip())
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _parse_csv(raw: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(raw or "").replace(";", ",").split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    bot_token: str
    bot_enabled: bool
    miniapp_enabled: bool
    primary_owner_id: int
    data_dir: Path
    database_path: Path
    miniapp_api_token: str
    backup_encryption_key: str
    public_base_url: str
    host: str
    port: int
    proxy_headers: bool
    forwarded_allow_ips: str
    trusted_hosts: tuple[str, ...]
    cors_allowed_origins: tuple[str, ...]

    def require_ready(self) -> None:
        if not self.bot_enabled and not self.miniapp_enabled:
            raise RuntimeError("Отключены и бот, и Mini App.")
        token = self.bot_token.strip()
        if token in _TOKEN_PLACEHOLDERS:
            raise RuntimeError("В переменных хостинга или .env не указан BOT_TOKEN из BotFather.")
        if not _TOKEN_RE.match(token):
            raise RuntimeError("BOT_TOKEN выглядит неверно.")
        if not self.primary_owner_id:
            raise RuntimeError("В переменных хостинга или .env не указан OWNER_TELEGRAM_ID.")
        self.data_dir.mkdir(parents=True, exist_ok=True)


_data_dir = Path(os.getenv("BOT_DATA_DIR", "./data")).resolve()
settings = Settings(
    bot_token=os.getenv("BOT_TOKEN", "").strip(),
    bot_enabled=_parse_bool(os.getenv("BOT_ENABLED"), True),
    miniapp_enabled=_parse_bool(os.getenv("MINIAPP_ENABLED"), True),
    primary_owner_id=_parse_int(os.getenv("OWNER_TELEGRAM_ID"), 0),
    data_dir=_data_dir,
    database_path=_data_dir / "production_account.sqlite3",
    miniapp_api_token=os.getenv("MINIAPP_API_TOKEN", "").strip(),
    backup_encryption_key=os.getenv("BACKUP_ENCRYPTION_KEY", "").strip(),
    public_base_url=os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/"),
    host=os.getenv("HOST", "0.0.0.0").strip() or "0.0.0.0",
    port=_parse_int(os.getenv("PORT"), 3000, minimum=1, maximum=65535),
    proxy_headers=_parse_bool(os.getenv("PROXY_HEADERS"), True),
    forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "*").strip() or "*",
    trusted_hosts=_parse_csv(os.getenv("TRUSTED_HOSTS")),
    cors_allowed_origins=_parse_csv(os.getenv("CORS_ALLOWED_ORIGINS")),
)
