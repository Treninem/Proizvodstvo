from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    keyboard_source = (ROOT / "app" / "keyboards.py").read_text(encoding="utf-8")
    assert "Проверить учёт" not in keyboard_source
    report_source = (ROOT / "app" / "handlers" / "reports.py").read_text(encoding="utf-8")
    assert "user_id=callback.from_user.id" in report_source
    assert "def _start_selection(message: Message, scope_chat_id: int, text: str, mode: str, user_id: int | None = None)" in report_source
    print("OK")


if __name__ == "__main__":
    main()
