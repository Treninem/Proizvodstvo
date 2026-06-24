from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "app" / "handlers" / "intake.py").read_text(encoding="utf-8")
required = [
    'if saved <= 0:',
    'Запись не сохранена. Уточните название позиции или отправьте данные заново.',
    'first_unresolved_index(payload.get("operations", []))',
]
missing = [item for item in required if item not in source]
if missing:
    raise SystemExit("Не хватает защиты от пустого сохранения: " + "; ".join(missing))
print("OK")
