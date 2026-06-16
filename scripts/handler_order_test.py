from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    problems: list[str] = []
    for path in (ROOT / "app" / "handlers").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "@router.message()" in text:
            problems.append(str(path.relative_to(ROOT)))
    if problems:
        raise SystemExit("Лишний общий обработчик сообщений: " + ", ".join(problems))
    print("OK")


if __name__ == "__main__":
    main()
