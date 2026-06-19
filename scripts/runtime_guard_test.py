from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    text = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    needed = [
        "try:",
        "except Exception",
        "log.exception",
        "return",
    ]
    missing = [item for item in needed if item not in text]
    if missing:
        raise SystemExit("Проверьте защиту обработки сообщений")
    print("OK")


if __name__ == "__main__":
    main()
