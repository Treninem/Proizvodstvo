from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {'.pyc', '.db', '.sqlite', '.sqlite3'}
FORBIDDEN_DIRS = {'data', 'backups', 'exports', '__pycache__'}
TOKEN_RE = re.compile(r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b')
CHECK_SUFFIXES = {'.py', '.md', '.txt', '.env', '.example', '.yml', '.yaml'}


def tracked_files() -> list[Path]:
    raw = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT)
    return [ROOT / item.decode() for item in raw.split(b'\0') if item]


def main() -> None:
    problems: list[str] = []
    for path in tracked_files():
        rel = path.relative_to(ROOT)
        if path.name == '.env' or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            problems.append(f'Запрещённый runtime-файл: {rel}')
        if any(part in FORBIDDEN_DIRS for part in rel.parts):
            problems.append(f'Запрещённый runtime-каталог: {rel}')
        if path.is_file() and path.suffix.lower() in CHECK_SUFFIXES:
            try:
                text = path.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                continue
            if TOKEN_RE.search(text):
                problems.append(f'Похожий на Telegram BOT_TOKEN секрет: {rel}')

    defaults = (ROOT / 'runtime.defaults.env').read_text(encoding='utf-8')
    for key in ('MINIAPP_API_TOKEN', 'BACKUP_ENCRYPTION_KEY'):
        line = next((x.strip() for x in defaults.splitlines() if x.startswith(key + '=')), None)
        if line != key + '=':
            problems.append(f'{key} должен быть пустым в runtime.defaults.env')

    if problems:
        raise SystemExit('\n'.join(sorted(set(problems))))
    print('final_step83_audit OK')


if __name__ == '__main__':
    main()
