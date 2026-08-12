from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    start = ROOT / 'README_START.txt'
    memory = ROOT / 'STEP83_MEMORY.md'
    assert start.is_file() and start.stat().st_size > 1000, start
    assert memory.is_file() and 'Шаг 83' in memory.read_text(encoding='utf-8'), memory
    # Реальный .env и runtime-база не должны храниться в репозитории.
    assert not (ROOT / '.env').exists()
    assert not (ROOT / 'production_account.sqlite3').exists()
    print('repository_docs_test OK')


if __name__ == '__main__':
    main()
