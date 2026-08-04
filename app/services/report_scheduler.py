from __future__ import annotations

import asyncio
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import reporting
from . import repository as repo
from . import stock_risk


def _server_timezone():
    return datetime.now().astimezone().tzinfo


def normalize_timezone_name(value: str | None) -> str:
    name = (value or "server").strip()
    if not name or name == "server":
        return "server"
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Часовой пояс не поддерживается.") from exc
    return name


def _local_now(now: datetime, timezone_name: str) -> tuple[datetime, object]:
    timezone_name = normalize_timezone_name(timezone_name)
    server_tz = _server_timezone()
    if timezone_name == "server":
        if now.tzinfo is not None:
            return now.astimezone(server_tz).replace(tzinfo=None), server_tz
        return now, server_tz
    server_now = now if now.tzinfo is not None else now.replace(tzinfo=server_tz)
    return server_now.astimezone(ZoneInfo(timezone_name)), server_tz


def calculate_next_run(
    frequency: str,
    hour: int,
    minute: int,
    weekday: int = 0,
    month_day: int = 1,
    now: datetime | None = None,
    timezone_name: str = "server",
) -> datetime:
    now = now or datetime.now()
    local_now, server_tz = _local_now(now, timezone_name)
    hour = max(0, min(int(hour), 23))
    minute = max(0, min(int(minute), 59))
    if frequency == "weekly":
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        candidate += timedelta(days=(int(weekday) - candidate.weekday()) % 7)
        if candidate <= local_now:
            candidate += timedelta(days=7)
    elif frequency == "monthly":
        year, month = local_now.year, local_now.month
        day = min(max(1, int(month_day)), monthrange(year, month)[1])
        candidate = local_now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
            day = min(max(1, int(month_day)), monthrange(year, month)[1])
            candidate = candidate.replace(year=year, month=month, day=day)
    else:
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(days=1)
    if normalize_timezone_name(timezone_name) == "server":
        return candidate.replace(tzinfo=None)
    return candidate.astimezone(server_tz).replace(tzinfo=None)


def next_run_text(
    frequency: str,
    hour: int,
    minute: int,
    weekday: int = 0,
    month_day: int = 1,
    now: datetime | None = None,
    timezone_name: str = "server",
) -> str:
    return calculate_next_run(frequency, hour, minute, weekday, month_day, now, timezone_name).strftime("%Y-%m-%d %H:%M:%S")


def _area_ids_for_schedule(schedule: dict) -> set[int] | None:
    scope = int(schedule["chat_id"])
    user_id = int(schedule["user_id"])
    access = repo.area_section_access_for_user(scope, user_id, "reports")
    selected = schedule.get("area_id")
    if selected is not None:
        area_id = int(selected)
        if access.get("restricted") and area_id not in set(access.get("view") or []):
            raise PermissionError("Нет доступа к площадке шаблона.")
        return {area_id}
    if access.get("restricted"):
        allowed = set(access.get("view") or [])
        if not allowed:
            raise PermissionError("Нет доступных площадок для отчёта.")
        return allowed
    return None


async def _send_report(bot, schedule: dict) -> None:
    try:
        from aiogram.types import FSInputFile
    except ModuleNotFoundError:
        FSInputFile = lambda path: Path(path)
    path: Path | None = None
    try:
        scope = int(schedule["chat_id"])
        user_id = int(schedule["user_id"])
        account = repo.get_account_by_scope(scope)
        if account and not repo.is_global_owner_id(user_id) and not repo.user_has_account_access(account.id, user_id):
            raise PermissionError("Доступ сотрудника к учёту отключён.")
        area_ids = _area_ids_for_schedule(schedule)
        if str(schedule.get("report_format") or "xlsx").lower() == "pdf":
            path = reporting.create_pdf_report(scope, str(schedule.get("request_text") or "отчёт за месяц"), user_id=user_id, area_ids=area_ids)
        else:
            path = reporting.create_xlsx_report(scope, str(schedule.get("request_text") or "отчёт за месяц"), user_id=user_id, area_ids=area_ids)
        await bot.send_document(
            int(schedule["delivery_chat_id"]),
            FSInputFile(path),
            caption=f"Автоматический отчёт: {schedule.get('preset_name') or 'сохранённый шаблон'}",
        )
    finally:
        if path:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


async def deliver_due_reports(bot, now: datetime | None = None) -> int:
    now = now or datetime.now()
    due = repo.list_due_report_schedules(now.strftime("%Y-%m-%d %H:%M:%S"))
    delivered = 0
    for schedule in due:
        timezone_name = str(schedule.get("timezone_name") or "server")
        next_text = next_run_text(
            str(schedule.get("frequency") or "daily"),
            int(schedule.get("hour") or 0),
            int(schedule.get("minute") or 0),
            int(schedule.get("weekday") or 0),
            int(schedule.get("month_day") or 1),
            now,
            timezone_name,
        )
        history_id = repo.create_report_delivery_history(schedule, "scheduled", "running")
        repo.mark_report_schedule_running(int(schedule["id"]), next_text)
        try:
            await _send_report(bot, schedule)
            repo.mark_report_schedule_result(int(schedule["id"]), True)
            repo.mark_report_delivery_result(history_id, True)
            delivered += 1
        except Exception as exc:
            repo.mark_report_schedule_result(int(schedule["id"]), False, str(exc))
            repo.mark_report_delivery_result(history_id, False, str(exc))
    return delivered


async def deliver_queued_report_retries(bot) -> int:
    delivered = 0
    for schedule in repo.list_queued_report_deliveries():
        history_id = int(schedule["id"])
        repo.mark_report_delivery_running(history_id)
        try:
            await _send_report(bot, schedule)
            repo.mark_report_delivery_result(history_id, True)
            delivered += 1
        except Exception as exc:
            repo.mark_report_delivery_result(history_id, False, str(exc))
    return delivered


async def deliver_inbox_notifications(bot) -> int:
    sent = 0
    for item in repo.list_pending_inbox_telegram():
        text = str(item.get("title") or "Уведомление")
        if item.get("message"):
            text += f"\n\n{item['message']}"
        try:
            await bot.send_message(int(item["recipient_user_id"]), text)
            repo.mark_inbox_telegram_result(int(item["id"]), True)
            sent += 1
        except Exception as exc:
            repo.mark_inbox_telegram_result(int(item["id"]), False, str(exc))
    return sent


async def schedule_loop(bot, interval_seconds: int = 60) -> None:
    while True:
        try:
            repo.generate_shift_plans_from_templates(datetime.now())
            repo.queue_overdue_inventory_approval_escalations(datetime.now())
            stock_risk.evaluate_all(now=datetime.now())
            await deliver_inbox_notifications(bot)
            await deliver_queued_report_retries(bot)
            await deliver_due_reports(bot)
        except Exception:
            pass
        await asyncio.sleep(max(30, int(interval_seconds)))
