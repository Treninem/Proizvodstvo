from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
chats = (ROOT / "app" / "handlers" / "chats.py").read_text(encoding="utf-8")
main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

if "Бот добавлен" in chats:
    raise SystemExit("В групповой обработчик вернулась шумная фраза 'Бот добавлен'.")

start = chats.index("@router.my_chat_member()")
end = chats.index("@router.message", start)
block = chats[start:end]
if "send_message" in block or ".answer(" in block:
    raise SystemExit("Обработчик добавления/статуса бота не должен писать в группу.")

required = [
    "try_handle_confirmation_text",
    "try_handle_onboarding",
    "try_handle_account_command",
    "try_handle_group_command",
    "try_handle_wizard_message",
    "try_handle_setup_command",
    "try_handle_correction_command",
    "try_handle_inventory_adjustment",
    "try_handle_report",
    "try_handle_backup",
    "try_handle_intake",
]
missing = [item for item in required if item not in main]
if missing:
    raise SystemExit("Не найден порядок явных обработчиков: " + ", ".join(missing))

if "if handled:\n            return" not in main:
    raise SystemExit("Сообщение должно останавливаться после первого явного обработчика.")

print("OK")
