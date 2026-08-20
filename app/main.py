from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, Message

from .config import settings
from .db import init_db
from .services import control_center, report_scheduler

# Install the compact menu before importing legacy handlers. Many old screens use
# ``from app.keyboards import main_menu``; importing them only after this patch
# makes every Back/Menu button return to the same cleaned-up menu.
from . import keyboards
from .handlers import bot_menu_v2

keyboards.main_menu = bot_menu_v2.bot_main_menu

from .handlers import (  # noqa: E402
    accounts,
    backups,
    chats,
    component_picker,
    corrections,
    excel_import,
    groups,
    help_guide,
    intake,
    job_assignment_v2,
    management,
    onboarding,
    owner,
    reports,
    risks,
    setup,
    start,
    workplace_intake,
    workflow,
)
from .handlers.groups import try_handle_group_command  # noqa: E402
from .handlers.accounts import try_handle_account_command  # noqa: E402
from .handlers.management import try_handle_management_message  # noqa: E402
from .handlers.component_picker import try_handle_component_picker_message  # noqa: E402
from .handlers.job_assignment_v2 import try_handle_reply_job_assignment_v2  # noqa: E402
from .handlers.setup import try_handle_wizard_message, try_handle_setup_command  # noqa: E402
from .handlers.intake import try_handle_confirmation_text, try_handle_intake  # noqa: E402
from .handlers.workplace_intake import try_handle_workplace_intake  # noqa: E402
from .handlers.reports import try_handle_report  # noqa: E402
from .handlers.corrections import try_handle_correction_command  # noqa: E402
from .handlers.backups import try_handle_backup  # noqa: E402
from .handlers.inventory import try_handle_inventory_adjustment  # noqa: E402
from .handlers.risks import try_handle_risk_command  # noqa: E402
from .handlers.workflow import try_handle_workflow_command  # noqa: E402
from .handlers.onboarding import try_handle_onboarding  # noqa: E402
from .handlers.transfers import try_handle_transfer_command  # noqa: E402
from .user_directory_middleware import UserDirectoryMiddleware  # noqa: E402

# Legacy owner.py still contains historical version labels. Keep the owner-only
# system panel, but replace its banner at runtime so a fresh deploy is obvious.
_STEP92_OLD_RELEASE = "Версия бота: 84 · Backend 85 · Mini App 20260816a"
_STEP92_RELEASE = "Версия бота: 92 · Backend 92 · Mini App 20260821a"
_owner_format_panel_legacy = owner._format_panel


def _owner_format_panel_step92(user_id: int | None = None) -> str:
    return _owner_format_panel_legacy(user_id).replace(_STEP92_OLD_RELEASE, _STEP92_RELEASE)


owner._format_panel = _owner_format_panel_step92

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
        try_handle_reply_job_assignment_v2,
        try_handle_group_command,
        try_handle_management_message,
        try_handle_component_picker_message,
        try_handle_wizard_message,
        try_handle_setup_command,
        try_handle_correction_command,
        try_handle_inventory_adjustment,
        try_handle_workflow_command,
        try_handle_transfer_command,
        try_handle_risk_command,
        try_handle_report,
        try_handle_backup,
        try_handle_workplace_intake,
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
    dp.message.outer_middleware(UserDirectoryMiddleware())
    dp.callback_query.outer_middleware(UserDirectoryMiddleware())
    # The new routers intentionally go first. They replace the old overloaded
    # menu and old role-assignment flow while legacy business callbacks remain.
    dp.include_router(job_assignment_v2.router)
    dp.include_router(bot_menu_v2.router)
    dp.include_router(help_guide.router)
    dp.include_router(start.router)
    dp.include_router(management.router)
    dp.include_router(component_picker.router)
    dp.include_router(setup.router)
    dp.include_router(groups.router)
    dp.include_router(chats.router)
    dp.include_router(workplace_intake.router)
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


async def _configure_bot_commands(bot: Bot) -> None:
    """Expose privacy-mode-safe reply commands in group chats."""
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="role", description="Назначить должность ответом"),
                BotCommand(command="job", description="Назначить должность ответом"),
            ],
            scope=BotCommandScopeAllGroupChats(),
        )
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="help", description="Как пользоваться"),
            ],
            scope=BotCommandScopeAllPrivateChats(),
        )
    except Exception as exc:
        # Command-menu setup must never stop accounting polling.
        log.warning("Не удалось обновить команды Telegram: %s", exc)


async def _bot_heartbeat_loop() -> None:
    while True:
        control_center.heartbeat("bot", "ok", "polling active")
        await asyncio.sleep(60)


async def run_bot() -> None:
    bot = Bot(settings.bot_token)
    dp = build_dispatcher()
    await _configure_bot_commands(bot)
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
