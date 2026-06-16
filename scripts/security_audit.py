from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_RE = re.compile(r"\b\d{6,14}:[A-Za-z0-9_-]{20,}\b")
SKIP_DIRS = {"data", "backups", "exports", "__pycache__", ".pytest_cache", ".mypy_cache"}
ALLOW_FILES = {".env.example"}


def main() -> None:
    problems: list[str] = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name == ".env":
            problems.append(f"Нельзя вкладывать .env в архив: {rel}")
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".py", ".md", ".txt", ".example", ""}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if path.name not in ALLOW_FILES and SECRET_RE.search(text):
            problems.append(f"Похожий на секрет ключ найден в файле: {rel}")
    if problems:
        raise SystemExit("\n".join(problems))
    print("OK")


if __name__ == "__main__":
    main()
