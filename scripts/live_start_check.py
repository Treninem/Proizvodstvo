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


def main() -> None:
    env_path = ROOT / ".env"
    values = _read_env(env_path)
    token = values.get("BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
    owners = values.get("GLOBAL_OWNER_IDS") or os.getenv("GLOBAL_OWNER_IDS", "")
    data_dir = values.get("BOT_DATA_DIR") or os.getenv("BOT_DATA_DIR", "./data")

    problems: list[str] = []
    if not env_path.exists():
        problems.append("Создайте .env рядом с .env.example")
    if not TOKEN_RE.match(token.strip()):
        problems.append("Проверьте BOT_TOKEN в .env")
    if not any(part.strip().isdigit() for part in owners.replace(";", ",").split(",")):
        problems.append("Проверьте GLOBAL_OWNER_IDS в .env")
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
    except Exception:
        problems.append("BOT_DATA_DIR недоступен для записи")

    if problems:
        raise SystemExit("\n".join(problems))
    print("OK")


if __name__ == "__main__":
    main()
