from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
setup = (ROOT / "app" / "handlers" / "setup.py").read_text(encoding="utf-8")
keyboards = (ROOT / "app" / "keyboards.py").read_text(encoding="utf-8")
source = setup + keyboards

checks = [
    're.match(r"^(?:назначить|выдать|поставить)\\s+должность(?:\\s+(.+))?$"',
    'repo.copy_job_titles_between_contexts(message.from_user.id, chat_id)',
    'prefix + _assignment_text(data, 0)',
    'job_title_choice_keyboard(jobs, target.id, 0)',
    'job_assignment_confirm_keyboard(target_user_id, job_id)',
    'repo.set_worker_job(group_chat_id, target_user_id',
    'await _safe_delete_message(callback.message)',
    'callback_data=f"jobassign:pick:{target_user_id}',
    'callback_data=f"jobassign:confirm:{target_user_id}',
    'callback_data=f"jobassign:change:{target_user_id}',
]
missing = [item for item in checks if item not in source]
if missing:
    raise SystemExit("Не найдены элементы сценария назначения должности: " + "; ".join(missing))
print("OK")
