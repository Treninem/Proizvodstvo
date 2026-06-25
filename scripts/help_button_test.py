from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
start = (ROOT / "app" / "handlers" / "start.py").read_text(encoding="utf-8")
keyboards = (ROOT / "app" / "keyboards.py").read_text(encoding="utf-8")

checks = [
    'InlineKeyboardButton(text="Как пользоваться", callback_data="menu:help")',
    'HELP_PAGES: dict[str, str]',
    'Что для чего нужно',
    '@router.callback_query(F.data.startswith("help:"))',
    'help_topic_keyboard',
    'назначить должность',
    'Норма комплектации',
    'Отчёт из нескольких групп',
]
missing = [item for item in checks if item not in start + keyboards]
if missing:
    raise SystemExit("Не найдены элементы подробной инструкции: " + "; ".join(missing))
print("OK")
