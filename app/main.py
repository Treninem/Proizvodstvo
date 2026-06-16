from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message

from .config import settings
from .db import init_db
from .handlers import start, intake, setup, groups, owner, accounts, corrections, reports, backups, inventory, onboarding
from .handlers.groups import try_handle_group_command
from .handlers.accounts import try_handle_account_command
from .handlers.setup import try_handle_wizard_message, try_handle_setup_command
from .handlers.intake import try_handle_confirmation_text, try_handle_intake
from .handlers.reports import try_handle_report
from .handlers.corrections import try_handle_correction_command
from .handlers.backups import try_handle_backup
from .handlers.inventory import try_handle_inventory_adjustment
from .handlers.onboarding import try_handle_onboarding

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
        try_handle_wizard_message,
        try_handle_setup_command,
        try_handle_correction_command,
        try_handle_inventory_adjustment,
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


async def main() -> None:
    settings.require_ready()
    init_db()
    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(setup.router)
    dp.include_router(groups.router)
    dp.include_router(intake.router)
    dp.include_router(owner.router)
    dp.include_router(accounts.router)
    dp.include_router(corrections.router)
    dp.include_router(reports.router)
    dp.include_router(backups.router)
    dp.include_router(onboarding.router)
    dp.include_router(router)
    log.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
