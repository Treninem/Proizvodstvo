from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", "data", "backups", "exports"}
PARTS = [
    ["дир", "ектор"],
    ["ро", "ль"],
    ["что ", "сделано"],
    ["маш", "инный"],
    ["тех", "нический раздел"],
]
FORBIDDEN = ["".join(x) for x in PARTS]
CHECK_SUFFIXES = {".py", ".md", ".txt", ".example"}


def main() -> None:
    problems: list[str] = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if not path.is_file() or path.suffix.lower() not in CHECK_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for word in FORBIDDEN:
            if word in text:
                problems.append(f"Нежелательный текст: {rel}")
                break
    if problems:
        raise SystemExit("\n".join(problems))
    print("OK")


if __name__ == "__main__":
    main()
