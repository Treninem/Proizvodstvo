from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "app" / "handlers" / "setup.py").read_text(encoding="utf-8")
required = [
    'def _next_quick_state(state: str, chat_id: int | None = None)',
    '_existing_jobs_text(chat_id)',
    '_existing_entities_text(chat_id, "product")',
    '_existing_entities_text(chat_id, "component")',
    '_existing_entities_text(chat_id, "material")',
    '_existing_entities_text(chat_id, "meter")',
    '_next_quick_state(session["state"], callback.message.chat.id)',
    '_next_quick_state(current, chat_id)',
]
missing = [item for item in required if item not in source]
if missing:
    raise SystemExit("Не хватает списка уже созданных пунктов в быстрой настройке: " + "; ".join(missing))
print("OK")
