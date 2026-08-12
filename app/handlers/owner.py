from __future__ import annotations

from ._safe import safe_edit_text
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..services import repository as repo
from ..services import stock_risk
from ..config import settings

router = Router()


class OwnerAccessStates(StatesGroup):
    waiting_admin = State()


def _is_primary_owner(user_id: int | None) -> bool:
    return repo.is_primary_owner_id(user_id)


def _owner_menu(user_id: int | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="Организации", callback_data="owner:accounts")],
        [InlineKeyboardButton(text="Все чаты", callback_data="owner:chats")],
        [InlineKeyboardButton(text="Общая статистика", callback_data="owner:stats")],
        [InlineKeyboardButton(text="Состояние платформы", callback_data="owner:db")],
        [InlineKeyboardButton(text="Критические события", callback_data="owner:risks")],
        [InlineKeyboardButton(text="Версия", callback_data="owner:version")],
        [InlineKeyboardButton(text="Режим проверки", callback_data="owner:testmode")],
        [InlineKeyboardButton(text="Вернуться в обычное меню", callback_data="menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _access_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="Добавить администратора", callback_data="owner:addadmin")]
    ]
    for admin in repo.list_system_admins():
        user_id = int(admin["user_id"])
        label = str(admin.get("display_name") or user_id)
        rows.append([
            InlineKeyboardButton(
                text=f"Отключить: {label[:28]}",
                callback_data=f"owner:revokeadmin:{user_id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="owner:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _revoke_confirmation_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да, отключить", callback_data=f"owner:confirmrevoke:{user_id}")],
            [InlineKeyboardButton(text="Отмена", callback_data="owner:access")],
        ]
    )


def _format_access() -> str:
    admins = repo.list_system_admins()
    lines = [
        "Полный административный доступ",
        "",
        f"Владелец: {settings.primary_owner_id}",
        "Доступ владельца нельзя отключить или понизить.",
        "",
    ]
    if not admins:
        lines.append("Дополнительных администраторов нет.")
    else:
        lines.append("Дополнительные администраторы:")
        for admin in admins:
            name = str(admin.get("display_name") or "Без имени")
            lines.append(f"• {name} · ID {admin['user_id']}")
    lines.extend([
        "",
        "Администратор получает полный доступ к учёту, настройкам и разделам. "
        "Руководителей отделов и обычных сотрудников назначайте в настройках отделов.",
    ])
    return "\n".join(lines)


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


def _owner_account_keyboard(account_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="owner:accounts")],
        ]
    )


def _format_panel(user_id: int | None = None) -> str:
    stats = repo.owner_global_stats()
    test_mode = "включён" if repo.is_user_test_mode_enabled(user_id) else "выключен"
    role = "Владелец платформы"
    return (
        f"Системное меню · {role}\n"
        f"Версия бота: 83 · Mini App 20260812f\n\n"
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


def _format_risk_status(chat_id: int) -> str:
    scope = repo.resolve_scope_chat_id(chat_id)
    data = stock_risk.dashboard(scope)
    summary = data.get("summary") or {}
    incidents = data.get("incidents") or []
    lines = [
        "Критические остатки",
        "",
        f"Аварийных: {summary.get('emergency', 0)}",
        f"Критических: {summary.get('critical', 0)}",
        f"Предупреждений: {summary.get('warning', 0)}",
        f"Без нормы расхода: {summary.get('unknown', 0)}",
    ]
    if incidents:
        lines.append("")
        for item in incidents[:15]:
            reserve = item.get("reserve_shifts")
            reserve_text = "не рассчитан" if reserve is None else f"{float(reserve):.1f} смен"
            lines.append(f"• {item.get('entity_name')}: {reserve_text}")
    else:
        lines.extend(["", "Активных тревог нет."])
    lines.extend(["", "Подробная настройка доступна в Mini App. В чате или боте можно написать: красные флаги"])
    return "\n".join(lines)


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
    if message.chat.type != "private":
        try:
            await message.delete()
        except Exception:
            pass
        return
    user_id = message.from_user.id if message.from_user else None
    await message.answer(_format_panel(user_id), reply_markup=_owner_menu(user_id))


@router.message(Command("version"))
async def owner_version_command(message: Message) -> None:
    if not _is_primary_owner(message.from_user.id if message.from_user else None):
        return
    if message.chat.type != "private":
        return
    await message.answer("Версия бота: 83\nMini App: 20260812f\nАрхитектура: tenant-isolation v2")


@router.message(Command("owner"))
async def owner_command(message: Message) -> None:
    if not _is_primary_owner(message.from_user.id if message.from_user else None):
        return
    await _show_panel(message)


@router.message(F.text.lower().in_({"закрытый раздел", "панель владельца бота", "служебный доступ"}))
async def owner_text_command(message: Message) -> None:
    if not _is_primary_owner(message.from_user.id if message.from_user else None):
        return
    await _show_panel(message)


@router.message(OwnerAccessStates.waiting_admin)
async def owner_add_admin_message(message: Message, state: FSMContext) -> None:
    actor_id = message.from_user.id if message.from_user else None
    if not _is_primary_owner(actor_id):
        await state.clear()
        return
    text = (message.text or "").strip()
    if text.lower() in {"отмена", "cancel", "/cancel"}:
        await state.clear()
        await message.answer(_format_access(), reply_markup=_access_keyboard())
        return
    parts = text.split(maxsplit=1)
    try:
        target_id = int(parts[0])
    except (ValueError, IndexError):
        await message.answer("Отправьте Telegram ID цифрами. После ID можно указать имя.\nНапример: 123456789 Имя")
        return
    display_name = parts[1].strip() if len(parts) > 1 else ""
    ok, result = repo.grant_system_admin(int(actor_id), target_id, display_name)
    if not ok:
        await message.answer(result)
        return
    await state.clear()
    try:
        await message.bot.send_message(
            target_id,
            "Владелец выдал вам полный административный доступ к производственному учёту.",
        )
    except Exception:
        pass
    await message.answer(result + "\n\n" + _format_access(), reply_markup=_access_keyboard())


@router.message(F.text.lower().in_({"тестовый режим", "режим проверки", "тест вкл", "тест выкл"}))
async def owner_test_mode_text(message: Message) -> None:
    if not _is_primary_owner(message.from_user.id if message.from_user else None):
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
async def owner_callbacks(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id if callback.from_user else None
    if not _is_primary_owner(user_id):
        await callback.answer()
        return
    if callback.message and callback.message.chat.type != "private":
        await callback.answer("Откройте личные сообщения бота.", show_alert=True)
        return
    action = callback.data.split(":", 1)[1]

    if action == "panel":
        await state.clear()
        await safe_edit_text(callback.message, _format_panel(user_id), reply_markup=_owner_menu(user_id))
        await callback.answer()
        return

    if action == "access":
        if not _is_primary_owner(user_id):
            await callback.answer("Управление полным доступом доступно только владельцу.", show_alert=True)
            return
        await state.clear()
        await safe_edit_text(callback.message, _format_access(), reply_markup=_access_keyboard())
        await callback.answer()
        return

    if action == "addadmin":
        if not _is_primary_owner(user_id):
            await callback.answer("Доступно только владельцу.", show_alert=True)
            return
        await state.set_state(OwnerAccessStates.waiting_admin)
        await safe_edit_text(
            callback.message,
            "Отправьте Telegram ID нового администратора. После ID можно указать понятное имя.\n\n"
            "Пример: 123456789 Имя\n\n"
            "Этот уровень даёт полный доступ. Для ограниченного доступа используйте отделы.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="owner:access")]]
            ),
        )
        await callback.answer()
        return

    if action.startswith("revokeadmin:"):
        if not _is_primary_owner(user_id):
            await callback.answer("Доступно только владельцу.", show_alert=True)
            return
        try:
            target_id = int(action.split(":", 1)[1])
        except ValueError:
            await callback.answer("Пользователь не найден.", show_alert=True)
            return
        await safe_edit_text(
            callback.message,
            f"Отключить полный административный доступ для ID {target_id}?",
            reply_markup=_revoke_confirmation_keyboard(target_id),
        )
        await callback.answer()
        return

    if action.startswith("confirmrevoke:"):
        if not _is_primary_owner(user_id):
            await callback.answer("Доступно только владельцу.", show_alert=True)
            return
        try:
            target_id = int(action.split(":", 1)[1])
        except ValueError:
            await callback.answer("Пользователь не найден.", show_alert=True)
            return
        ok, result = repo.revoke_system_admin(int(user_id), target_id)
        if ok:
            try:
                await callback.bot.send_message(target_id, "Владелец отключил ваш полный административный доступ.")
            except Exception:
                pass
        await safe_edit_text(callback.message, result + "\n\n" + _format_access(), reply_markup=_access_keyboard())
        await callback.answer(result, show_alert=not ok)
        return

    if action == "version":
        await safe_edit_text(callback.message, "Версия бота: 83\nMini App: 20260812f\nАрхитектура: tenant-isolation v2", reply_markup=_owner_menu(user_id))
        await callback.answer()
        return

    if action == "testmode":
        enabled = repo.toggle_user_test_mode(callback.from_user.id)
        text = "Режим проверки включён. Ваши пробные записи не попадут в основной учёт." if enabled else "Режим проверки выключен."
        await safe_edit_text(callback.message, text + "\n\n" + _format_panel(user_id), reply_markup=_owner_menu(user_id))
        await callback.answer()
        return
    if action == "chats":
        chats = repo.owner_list_chats(limit=20)
        if not chats:
            await safe_edit_text(callback.message, "Чатов пока нет.", reply_markup=_owner_menu(user_id))
        else:
            await safe_edit_text(callback.message, "Все чаты\n\nВыберите чат для просмотра.", reply_markup=_chats_keyboard())
        await callback.answer()
        return
    if action == "accounts":
        accounts = repo.owner_list_accounts(limit=50)
        if not accounts:
            await safe_edit_text(callback.message, "Учётов пока нет.", reply_markup=_owner_menu(user_id))
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
        report = repo.owner_company_report(account_id)
        await safe_edit_text(callback.message, report, reply_markup=_owner_account_keyboard(account_id))
        await callback.answer()
        return
    if action == "stats":
        await safe_edit_text(callback.message, _format_stats(), reply_markup=_owner_menu(user_id))
        await callback.answer()
        return
    if action == "risks":
        await safe_edit_text(callback.message, _format_risk_status(callback.message.chat.id), reply_markup=_owner_menu(user_id))
        await callback.answer()
        return

    if action == "db":
        await safe_edit_text(callback.message, _format_db_status(), reply_markup=_owner_menu(user_id))
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
        await safe_edit_text(
            callback.message,
            report,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="owner:chats")]]
            ),
        )
        await callback.answer()
        return
    await callback.answer()
