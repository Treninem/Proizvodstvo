from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOVE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", "data", "backups", "exports"}
REMOVE_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3"}
REMOVE_NAMES = {".env"}


def main() -> None:
    removed = 0
    for path in sorted(ROOT.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        rel = path.relative_to(ROOT)
        if path.is_dir() and path.name in REMOVE_DIRS:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
            continue
        if path.is_file() and (path.name in REMOVE_NAMES or path.suffix.lower() in REMOVE_SUFFIXES):
            path.unlink(missing_ok=True)
            removed += 1
    print(f"OK: {removed}")


if __name__ == "__main__":
    main()
