from __future__ import annotations

from ._safe import safe_edit_text
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..access import is_global_owner
from ..services import repository as repo

router = Router()


def _owner_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Все чаты", callback_data="owner:chats")],
            [InlineKeyboardButton(text="Все учёты", callback_data="owner:accounts")],
            [InlineKeyboardButton(text="Общая статистика", callback_data="owner:stats")],
            [InlineKeyboardButton(text="Состояние базы", callback_data="owner:db")],
            [InlineKeyboardButton(text="Режим проверки", callback_data="owner:testmode")],
            [InlineKeyboardButton(text="Обновить", callback_data="owner:panel")],
        ]
    )


def _chats_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for chat in repo.owner_list_chats(limit=20):
        title = chat.get("title") or str(chat.get("chat_id"))
        prefix = "✅" if chat.get("is_connected") else "▫️"
        rows.append([InlineKeyboardButton(text=f"{prefix} {title[:42]}", callback_data=f"owner:chat:{chat['chat_id']}")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="owner:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)




def _accounts_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for account in repo.owner_list_accounts(limit=50):
        prefix = "🌐" if account.is_general else "📘"
        rows.append([InlineKeyboardButton(text=f"{prefix} {account.name[:42]}", callback_data=f"owner:account:{account.id}")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="owner:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _format_panel(user_id: int | None = None) -> str:
    stats = repo.owner_global_stats()
    test_mode = "включён" if repo.is_user_test_mode_enabled(user_id) else "выключен"
    return (
        "Закрытый раздел\n\n"
        f"Подключённых групп: {stats['connected_chats']}\n"
        f"Всего чатов в базе: {stats['total_chats']}\n"
        f"Записей учёта: {stats['operations']}\n"
        f"Позиции склада: {stats['inventory_rows']}\n"
        f"Учётов: {stats.get('accounts', 0)}\n"
        f"Режим проверки: {test_mode}\n\n"
        "Выберите действие."
    )


def _format_stats() -> str:
    stats = repo.owner_global_stats()
    return (
        "Общая статистика\n\n"
        f"Всего чатов: {stats['total_chats']}\n"
        f"Подключённых групп: {stats['connected_chats']}\n"
        f"Личных чатов: {stats['private_chats']}\n"
        f"Групп и супергрупп: {stats['group_chats']}\n"
        f"Участков: {stats['areas']}\n"
        f"Должностей: {stats['job_titles']}\n"
        f"Позиций: {stats['entities']}\n"
        f"Сокращений: {stats['aliases']}\n"
        f"Локальных слов: {stats['lexicon']}\n"
        f"Операций: {stats['operations']}\n"
        f"Ожидают подтверждения: {stats['pending']}\n"
        f"Строк склада: {stats['inventory_rows']}\n"
        f"Учётов: {stats.get('accounts', 0)}\n"
        f"Привязок учётов к чатам: {stats.get('account_links', 0)}"
    )


def _format_db_status() -> str:
    stats = repo.owner_global_stats()
    return (
        "Состояние базы\n\n"
        f"Файл базы: {stats['database_path']}\n"
        f"Размер базы: {stats['database_size']}\n"
        f"Журнал ожидания: {stats['pending']}\n"
        f"Последняя активность: {stats['last_operation_at'] or 'нет данных'}"
    )


async def _show_panel(message: Message) -> None:
    await message.answer(_format_panel(message.from_user.id if message.from_user else None), reply_markup=_owner_menu())


@router.message(Command("owner"))
async def owner_command(message: Message) -> None:
    if not is_global_owner(message.from_user.id if message.from_user else None):
        return
    await _show_panel(message)


@router.message(F.text.lower().in_({"закрытый раздел", "панель владельца бота", "служебный доступ"}))
async def owner_text_command(message: Message) -> None:
    if not is_global_owner(message.from_user.id if message.from_user else None):
        return
    await _show_panel(message)




@router.message(F.text.lower().in_({"тестовый режим", "режим проверки", "тест вкл", "тест выкл"}))
async def owner_test_mode_text(message: Message) -> None:
    if not is_global_owner(message.from_user.id if message.from_user else None):
        return
    text = (message.text or "").lower().strip()
    if text == "тест вкл":
        repo.set_user_test_mode(message.from_user.id, True)
        await message.answer("Режим проверки включён. Ваши пробные записи не попадут в основной учёт.")
        return
    if text == "тест выкл":
        repo.set_user_test_mode(message.from_user.id, False)
        await message.answer("Режим проверки выключен.")
        return
    enabled = repo.toggle_user_test_mode(message.from_user.id)
    await message.answer("Режим проверки включён. Ваши пробные записи не попадут в основной учёт." if enabled else "Режим проверки выключен.")


@router.callback_query(F.data.startswith("owner:"))
async def owner_callbacks(callback: CallbackQuery) -> None:
    if not is_global_owner(callback.from_user.id if callback.from_user else None):
        await callback.answer()
        return
    action = callback.data.split(":", 1)[1]
    if action == "panel":
        await safe_edit_text(callback.message, _format_panel(callback.from_user.id if callback.from_user else None), reply_markup=_owner_menu())
        await callback.answer()
        return

    if action == "testmode":
        enabled = repo.toggle_user_test_mode(callback.from_user.id)
        text = "Режим проверки включён. Ваши пробные записи не попадут в основной учёт." if enabled else "Режим проверки выключен."
        await safe_edit_text(callback.message, text + "\n\n" + _format_panel(callback.from_user.id), reply_markup=_owner_menu())
        await callback.answer()
        return
    if action == "chats":
        chats = repo.owner_list_chats(limit=20)
        if not chats:
            await safe_edit_text(callback.message, "Чатов пока нет.", reply_markup=_owner_menu())
        else:
            await safe_edit_text(callback.message, "Все чаты\n\nВыберите чат для просмотра.", reply_markup=_chats_keyboard())
        await callback.answer()
        return
    if action == "accounts":
        accounts = repo.owner_list_accounts(limit=50)
        if not accounts:
            await safe_edit_text(callback.message, "Учётов пока нет.", reply_markup=_owner_menu())
        else:
            await safe_edit_text(callback.message, "Все учёты\n\nВыберите учёт для просмотра.", reply_markup=_accounts_keyboard())
        await callback.answer()
        return
    if action.startswith("account:"):
        raw_account_id = action.split(":", 1)[1]
        try:
            account_id = int(raw_account_id)
        except ValueError:
            await callback.answer("Учёт не найден.", show_alert=True)
            return
        report = repo.owner_account_report(account_id)
        await safe_edit_text(callback.message, report, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="owner:accounts")]]))
        await callback.answer()
        return
    if action == "stats":
        await safe_edit_text(callback.message, _format_stats(), reply_markup=_owner_menu())
        await callback.answer()
        return
    if action == "db":
        await safe_edit_text(callback.message, _format_db_status(), reply_markup=_owner_menu())
        await callback.answer()
        return
    if action.startswith("chat:"):
        raw_chat_id = action.split(":", 1)[1]
        try:
            chat_id = int(raw_chat_id)
        except ValueError:
            await callback.answer("Чат не найден.", show_alert=True)
            return
        report = repo.owner_chat_report(chat_id)
        await safe_edit_text(callback.message, report, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="owner:chats")]]))
        await callback.answer()
        return
    await callback.answer()
