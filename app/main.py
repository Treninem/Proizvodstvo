from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message

from .config import settings
from .db import init_db
from .services import report_scheduler
from .services import control_center
from .handlers import start, intake, setup, management, groups, owner, accounts, corrections, reports, backups, onboarding, chats, risks, workflow, excel_import
from .handlers.groups import try_handle_group_command
from .handlers.accounts import try_handle_account_command
from .handlers.management import try_handle_management_message
from .handlers.setup import try_handle_wizard_message, try_handle_setup_command
from .handlers.intake import try_handle_confirmation_text, try_handle_intake
from .handlers.reports import try_handle_report
from .handlers.corrections import try_handle_correction_command
from .handlers.backups import try_handle_backup
from .handlers.inventory import try_handle_inventory_adjustment
from .handlers.risks import try_handle_risk_command
from .handlers.workflow import try_handle_workflow_command
from .handlers.onboarding import try_handle_onboarding
from .handlers.transfers import try_handle_transfer_command

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("production_account_bot")

router = Router()


@router.message()
async def all_text(message: Message) -> None:
    if not message.text:
        return
    for handler in (
        try_handle_confirmation_text,
        try_handle_onboarding,
        try_handle_account_command,
        try_handle_group_command,
        try_handle_management_message,
        try_handle_wizard_message,
        try_handle_setup_command,
        try_handle_correction_command,
        try_handle_inventory_adjustment,
        try_handle_workflow_command,
        try_handle_transfer_command,
        try_handle_risk_command,
        try_handle_report,
        try_handle_backup,
        try_handle_intake,
    ):
        try:
            handled = await handler(message)
        except Exception:
            log.exception("Не удалось обработать сообщение")
            return
        if handled:
            return


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(management.router)
    dp.include_router(setup.router)
    dp.include_router(groups.router)
    dp.include_router(chats.router)
    dp.include_router(intake.router)
    dp.include_router(owner.router)
    dp.include_router(accounts.router)
    dp.include_router(corrections.router)
    dp.include_router(reports.router)
    dp.include_router(backups.router)
    dp.include_router(onboarding.router)
    dp.include_router(risks.router)
    dp.include_router(workflow.router)
    dp.include_router(excel_import.router)
    dp.include_router(router)
    return dp


async def _bot_heartbeat_loop() -> None:
    while True:
        control_center.heartbeat("bot", "ok", "polling active")
        await asyncio.sleep(60)


async def run_bot() -> None:
    bot = Bot(settings.bot_token)
    dp = build_dispatcher()
    log.info("Бот запущен")
    control_center.heartbeat("bot", "ok", "polling started")
    scheduler_task = asyncio.create_task(report_scheduler.schedule_loop(bot))
    heartbeat_task = asyncio.create_task(_bot_heartbeat_loop())
    try:
        await dp.start_polling(bot)
    except Exception as exc:
        control_center.heartbeat("bot", "error", str(exc))
        raise
    finally:
        heartbeat_task.cancel()
        scheduler_task.cancel()
        for task in (heartbeat_task, scheduler_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        await bot.session.close()


async def main() -> None:
    settings.require_ready()
    init_db()
    if not settings.bot_enabled:
        raise RuntimeError("BOT_ENABLED=false: используйте общий запуск app.runtime для Mini App без бота.")
    await run_bot()


if __name__ == "__main__":
    asyncio.run(main())
