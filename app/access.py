from __future__ import annotations

from aiogram import Bot
from aiogram.types import Chat, User

from .config import settings


def is_global_owner(user_id: int | None) -> bool:
    return bool(user_id and user_id in settings.global_owner_ids)


async def is_chat_creator(bot: Bot, chat_id: int, user_id: int | None) -> bool:
    if not user_id:
        return False
    if is_global_owner(user_id):
        return True
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except Exception:
        return False
    for member in admins:
        if member.user.id == user_id and member.status == "creator":
            return True
    return False


async def can_manage_accounting(bot: Bot, chat: Chat, user: User | None) -> bool:
    if not user:
        return False
    if is_global_owner(user.id):
        return True
    try:
        from .services import repository as repo
        if repo.user_can_manage_current_context(chat.id, user.id):
            return True
    except Exception:
        pass
    if chat.type in {"group", "supergroup"}:
        return await is_chat_creator(bot, chat.id, user.id)
    # В личке создать новый учёт может сам пользователь, но настройки уже выбранного
    # учёта открываются только при наличии прав в этом учёте.
    return False


OPERATION_PERMISSION = {
    "production": "production",
    "material_in": "material",
    "material_out": "material",
    "energy": "energy",
    "assembly": "assembly",
    "shipment": "shipment",
    "stock_in": "stock",
    "stock_out": "stock",
    "inventory_adjust": "stock",
}


async def can_submit_operations(bot: Bot, chat: Chat, user: User | None, operation_types: set[str]) -> bool:
    if not user:
        return False
    if is_global_owner(user.id):
        return True
    if await can_manage_accounting(bot, chat, user):
        return True
    try:
        from .services import repository as repo
        permissions = repo.user_permissions_current_context(chat.id, user.id)
    except Exception:
        permissions = {}
    if not permissions:
        return False
    for op in operation_types:
        key = OPERATION_PERMISSION.get(op)
        if key and not permissions.get(key):
            return False
    return True


async def can_view_reports(bot: Bot, chat: Chat, user: User | None, need_export: bool = False) -> bool:
    if not user:
        return False
    if is_global_owner(user.id):
        return True
    if await can_manage_accounting(bot, chat, user):
        return True
    try:
        from .services import repository as repo
        permissions = repo.user_permissions_current_context(chat.id, user.id)
    except Exception:
        permissions = {}
    if need_export:
        return bool(permissions.get("export") or permissions.get("reports"))
    return bool(permissions.get("reports") or permissions.get("stock") or permissions.get("export"))
