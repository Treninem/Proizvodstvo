from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_BAD_PARTS = [
    ["дир", "ектор"],
    ["ро", "ль"],
    ["ро", "ли"],
    ["ро", "лей"],
    ["Б", "Г"],
    ["б", "г"],
    ["Нов", "лян"],
    ["У", "жур"],
    ["фон", "тан"],
    ["Фон", "тан"],
    ["роз", "етка"],
    ["соед", "инитель"],
    ["П", "П"],
    ["дроб", "л"],
]
FORBIDDEN = ["".join(parts) for parts in _BAD_PARTS]
BANNED_FILES = {".pyc", ".db", ".sqlite", ".sqlite3", ".env"}
BANNED_DIRS = {"__" + "pycache__", ".pytest_cache", ".mypy_cache", "data", "backups", "exports"}


def main() -> None:
    problems: list[str] = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if any(part in BANNED_DIRS for part in rel.parts):
            problems.append(f"Лишняя папка: {rel}")
        if path.is_file() and path.suffix in BANNED_FILES:
            problems.append(f"Лишний файл: {rel}")
        if path.is_file() and path.suffix.lower() in {".py", ".md", ".txt", ".example"}:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for bad in FORBIDDEN:
                if bad in text:
                    problems.append(f"Запрещённый текст в {rel}")
    if problems:
        raise SystemExit("\n".join(problems))
    print("OK")


if __name__ == "__main__":
    main()
