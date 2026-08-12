from __future__ import annotations

from aiogram import Bot
from aiogram.types import Chat, User

from .config import settings


def is_global_owner(user_id: int | None) -> bool:
    if not user_id:
        return False
    try:
        from .services import repository as repo
        return repo.is_global_owner_id(user_id)
    except Exception:
        return bool(settings.primary_owner_id and int(user_id) == int(settings.primary_owner_id))


async def is_chat_creator(bot: Bot, chat_id: int, user_id: int | None) -> bool:
    if not user_id:
        return False
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except Exception:
        return False
    for member in admins:
        if member.user.id == user_id and member.status == "creator":
            return True
    return False


async def can_manage_accounting(bot: Bot, chat: Chat, user: User | None) -> bool:
    """Tenant-level administration only.

    The platform owner does not receive implicit access here. Telegram group creators
    do receive full rights for the accounting tenant attached to *their* group.
    """
    if not user:
        return False
    try:
        from .services import repository as repo
        if repo.user_can_manage_current_context(chat.id, user.id):
            return True
        if chat.type in {"group", "supergroup"} and await is_chat_creator(bot, chat.id, user.id):
            account = repo.ensure_group_account_context(
                chat.id, chat.title or "Рабочая группа", chat.type, user.id
            )
            return bool(account and repo.user_can_manage_current_context(chat.id, user.id))
    except Exception:
        return False
    return False


OPERATION_PERMISSION = {
    "production": "production",
    "material_in": "material",
    "material_out": "material",
    "energy": "energy",
    "assembly": "assembly",
    "shipment": "shipment",
    "shipment_client": "shipment",
    "shipment_fulfillment": "fulfillment",
    "return": "returns",
    "movement": "movement",
    "transfer_to_assembly": "movement",
    "stock_in": "stock",
    "stock_out": "stock",
    "write_off": "stock",
    "inventory_adjust": "stock",
}


async def can_submit_operations(bot: Bot, chat: Chat, user: User | None, operation_types: set[str]) -> bool:
    if not user:
        return False
    if await can_manage_accounting(bot, chat, user):
        return True
    try:
        from .services import repository as repo
        permissions = repo.user_permissions_current_context(chat.id, user.id)
    except Exception:
        permissions = {}
    if not permissions:
        return False
    from .services import repository as repo
    for op in operation_types:
        department_allowed = repo.department_operation_allowed(chat.id, user.id, op, "submit")
        if department_allowed is False:
            return False
        if department_allowed is True:
            continue
        key = OPERATION_PERMISSION.get(op)
        if key and not permissions.get(key):
            return False
    return True


async def can_view_reports(bot: Bot, chat: Chat, user: User | None, need_export: bool = False) -> bool:
    if not user:
        return False
    if await can_manage_accounting(bot, chat, user):
        return True
    try:
        from .services import repository as repo
        if repo.user_has_department_membership(chat.id, user.id):
            return False
        permissions = repo.user_permissions_current_context(chat.id, user.id)
    except Exception:
        permissions = {}
    if need_export:
        return bool(permissions.get("export") or permissions.get("reports"))
    return bool(permissions.get("reports") or permissions.get("stock") or permissions.get("export"))
