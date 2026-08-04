from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN_RE = re.compile(r"^\d{6,14}:[A-Za-z0-9_-]{20,}$")


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _bool(value: str, default: bool) -> bool:
    if not str(value or "").strip():
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "да"}


def main() -> None:
    env_path = ROOT / ".env"
    values = _read_env(env_path)

    def get(name: str, default: str = "") -> str:
        return values.get(name) or os.getenv(name, default)

    bot_enabled = _bool(get("BOT_ENABLED", "true"), True)
    miniapp_enabled = _bool(get("MINIAPP_ENABLED", "true"), True)
    token = get("BOT_TOKEN")
    owner_id = get("OWNER_TELEGRAM_ID")
    data_dir = get("BOT_DATA_DIR", "./data")
    miniapp_secret = get("MINIAPP_API_TOKEN")

    problems: list[str] = []
    if not env_path.exists():
        problems.append("Создайте .env рядом с .env.example")
    if not bot_enabled and not miniapp_enabled:
        problems.append("Включите BOT_ENABLED или MINIAPP_ENABLED")
    if bot_enabled and not TOKEN_RE.match(token.strip()):
        problems.append("Проверьте BOT_TOKEN в .env")
    if not owner_id.strip().isdigit() or int(owner_id.strip()) <= 0:
        problems.append("Проверьте OWNER_TELEGRAM_ID в .env")
    if miniapp_enabled and not miniapp_secret.strip():
        problems.append("Укажите MINIAPP_API_TOKEN в .env")
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        probe = Path(data_dir) / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception:
        problems.append("BOT_DATA_DIR недоступен для записи")
    try:
        port = int(get("PORT", get("WEB_PORT", "8080")))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        problems.append("Проверьте PORT в .env")

    if problems:
        raise SystemExit("\n".join(problems))
    print(f"OK: bot={str(bot_enabled).lower()} miniapp={str(miniapp_enabled).lower()}")


if __name__ == "__main__":
    main()
