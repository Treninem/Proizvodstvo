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
        f"Версия бота: 84 · Backend 84b · Mini App 20260813b\n\n"
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
    await message.answer("Версия бота: 84\nBackend: 84b\nMini App: 20260813b\nАрхитектура: tenant-isolation v2")


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
        await safe_edit_text(callback.message, "Версия бота: 84\nBackend: 84b\nMini App: 20260813b\nАрхитектура: tenant-isolation v2", reply_markup=_owner_menu(user_id))
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
        accounts = repo.owner_list_a…247257 tokens truncated…аны</div>
        </article>
      </div>
    </section>

    <section class="tab-page" id="page-transfers">
      <section class="hub-head"><div><p class="eyebrow">склад</p><h2>Передачи между подразделениями</h2><p>Отправитель создаёт передачу. Получатель пересчитывает и подтверждает. До приёмки количество отмечено как «в пути».</p></div></section>
      <div class="grid">
        <article class="form-panel wide"><div>
          <h2>Новая передача</h2>
          <div class="two-col"><label>Откуда<select id="transferFromArea"></select></label><label>Куда<select id="transferToArea"></select></label></div>
          <div class="two-col"><label>Отдел отправителя<select id="transferFromDepartment"></select></label><label>Отдел получателя<select id="transferToDepartment"></select></label></div>
          <div class="two-col"><label>Место выдачи<select id="transferFromLocation"></select></label><label>Место приёмки<select id="transferToLocation"></select></label></div>
          <div class="two-col"><label>Позиция<select id="transferEntity"></select></label><label>Количество<input id="transferQuantity" inputmode="decimal" /></label></div>
          <label>Комментарий<input id="transferNote" maxlength="1000" placeholder="Необязательно" /></label>
          <button class="primary" data-action="create-transfer">Передать на подтверждение</button>
        </div></article>
        <article class="panel wide"><div class="section-title-row"><div><h2>Входящие и исходящие</h2><p>При расхождении приёмщик указывает фактическое количество и причину.</p></div><button data-action="refresh-transfers">Обновить</button></div><div id="transferList" class="manager-list empty">Передач пока нет</div></article>
      </div>
    </section>

    <section class="tab-page" id="page-organization" data-admin-only="1">
      <section class="hub-head"><div><p class="eyebrow">структура</p><h2>Где работает организация</h2><p>Создайте населённые пункты/площадки, привяжите участки и обозначьте конкретные места хранения.</p></div></section>
      <div class="grid">
        <article class="form-panel"><div><h2>Населённый пункт / площадка</h2><label>Населённый пункт<input id="siteSettlement" placeholder="Город, посёлок" /></label><label>Название площадки<input id="siteName" placeholder="Производство, склад…" /></label><label>Адрес<input id="siteAddress" /></label><button class="primary" data-action="create-site">Создать</button><div id="siteList" class="manager-list compact"></div></div></article>
        <article class="form-panel"><div><h2>Привязать участок</h2><label>Участок<select id="bindAreaSelect"></select></label><label>Площадка<select id="bindSiteSelect"></select></label><button data-action="bind-area-site">Сохранить</button></div></article>
        <article class="form-panel wide"><div><h2>Место хранения</h2><div class="two-col"><label>Название<input id="storageLocationName" placeholder="Стеллаж, зона, склад…" /></label><label>Код<input id="storageLocationCode" placeholder="необязательно" /></label></div><div class="three-col"><label>Площадка<select id="storageLocationSite"></select></label><label>Участок<select id="storageLocationArea"></select></label><label>Отдел<select id="storageLocationDepartment"></select></label></div><button data-action="create-storage-location">Создать место</button><div id="storageLocationList" class="manager-list compact"></div></div></article>
      </div>
    </section>

    <section class="tab-page" id="page-more">
      <section class="hub-head"><div><p class="eyebrow">ещё</p><h2>Управление и дополнительные разделы</h2><p>Каждая функция находится в своей группе. Вы увидите только то, к чему у вас есть доступ.</p></div></section>
      <div class="more-groups">
        <section><h3>Люди и доступ</h3><div class="more-grid"><button data-tab="team" data-section="workers"><span>👥</span><b>Сотрудники</b><small>Люди и должности</small></button><button data-tab="departments" data-department-manage="1"><span>🏢</span><b>Отделы</b><small>Руководители и рабочие действия</small></button><button data-tab="area-access" data-section="permissions"><span>🔐</span><b>Права</b><small>Что кому разрешено</small></button></div></section>
        <section><h3>Работа</h3><div class="more-grid"><button data-tab="workflow"><span>🧾</span><b>Задания</b><small>Задачи, заявки и план/факт</small></button><button data-tab="shifts"><span>🕐</span><b>Смены</b><small>График и передача смены</small></button><button data-tab="inbox"><span>🔔</span><b>Входящие</b><small>Что требует вашего внимания</small></button><button data-tab="control" data-control-only="1"><span>🎛️</span><b>Контроль смены</b><small>Сводка руководителя</small></button></div></section>
        <section><h3>Качество и техника</h3><div class="more-grid"><button data-tab="quality"><span>✅</span><b>Качество</b><small>Проверки, карантин, снабжение</small></button><button data-tab="workflow" data-focus="workflowEquipmentBlock" data-requires-equipment="1"><span>⚙️</span><b>Оборудование</b><small>Простои и обслуживание</small></button></div></section>
        <section data-admin-only="1"><h3>Настройка организации</h3><div class="more-grid"><button data-tab="organization"><span>📍</span><b>Площадки и места</b><small>Населённые пункты, участки, хранение</small></button><button data-tab="places"><span>🏷️</span><b>Справочники</b><small>Позиции, составы и назначения</small></button></div></section>
        <section data-admin-only="1"><h3>Безопасность и сервис</h3><div class="more-grid"><button data-tab="security"><span>🛡️</span><b>Безопасность</b><small>Резерв, устройства и аудит</small></button><button data-tab="control" data-focus="controlDiagnosticsBlock"><span>🩺</span><b>Диагностика</b><small>Бот, база, очереди и сервер</small></button></div></section>
      </div>
    </section>

    <section class="tab-page" id="page-security" data-section="site">
      <div class="grid">
        <article class="panel illustrated">
          <div>
            <h2>Mini App и синхронизация</h2>
            <div id="syncList" class="list empty">Нет данных</div>
          </div>
          <img src="/static/img/security.svg" alt="" />
        </article>
        <article class="panel illustrated">
          <div>
            <h2>Действия в Mini App</h2>
            <div id="miniAppLog" class="list empty">Нет данных</div>
          </div>
          <img src="/static/img/team.svg" alt="" />
        </article>
        <article class="panel illustrated">
          <div>
            <h2>Защита</h2>
            <div id="securityStatus" class="list empty">Нет данных</div>
          </div>
          <img src="/static/img/shield.svg" alt="" />
        </article>
        <article class="panel wide">
          <div class="section-title-row"><div><h2>Устройства Mini App и синхронизация</h2><p>Видно последнюю активность, наличие локального черновика и неотправленных записей. Здесь же можно отозвать потерянное устройство.</p></div></div>
          <div id="miniappDeviceList" class="manager-list empty">Устройства ещё не зарегистрированы</div>
        </article>
        <article class="form-panel wide" data-admin-only>
          <img src="/static/img/security.svg" alt="" class="side-img" />
          <div>
            <h2>Контроль незавершённых смен</h2>
            <p>Система сама напоминает о пакетах, которые не разобраны, и о передаче смены, которую не приняли.</p>
            <div class="filter-grid">
              <label>Первое напоминание о пакете, мин<input id="continuityPackageFirst" type="number" min="5" max="10080" value="60" /></label>
              <label>Повтор по пакету, мин<input id="continuityPackageRepeat" type="number" min="5" max="10080" value="120" /></label>
              <label>Первое по передаче, мин<input id="continuityHandoverFirst" type="number" min="5" max="10080" value="30" /></label>
              <label>Повтор по передаче, мин<input id="continuityHandoverRepeat" type="number" min="5" max="10080" value="60" /></label>
              <label>Максимум напоминаний<input id="continuityMaxReminders" type="number" min="0" max="10" value="3" /></label>
            </div>
            <button data-action="save-continuity-settings">Сохранить напоминания</button>
          </div>
        </article>
        <article class="form-panel wide" data-admin-only>
          <img src="/static/img/team.svg" alt="" class="side-img" />
          <div>
            <h2>Чек-лист передачи смены</h2>
            <p>Один пункт на строку. Поставьте <b>?</b> в начале строки, если пункт необязательный. Все остальные пункты нужно отметить перед передачей.</p>
            <label>Пункты<textarea id="handoverChecklistEditor" rows="6" placeholder="Проверить незавершённые операции&#10;Передать замечания&#10;? Указать дополнительную информацию"></textarea></label>
            <div class="actions"><button data-action="save-handover-checklist">Сохранить чек-лист</button><button data-action="download-continuity-audit">Скачать журнал аудита XLSX</button></div>
          </div>
        </article>
        <article class="panel wide">
          <h2>Резерв</h2>
          <p>Копия создаётся для выбранного учёта и скачивается через Mini App.</p>
          <div class="actions"><button class="primary" data-action="backup-account">Скачать копию учёта</button></div>
        </article>
        <article class="form-panel wide">
          <img src="/static/img/security.svg" alt="" class="side-img" />
          <div>
            <h2>Восстановление учёта</h2>
            <p>Доступно только владельцу. Перед восстановлением система автоматически создаёт страховочную копию. Подходит копия, созданная для этого же учёта.</p>
            <label>Файл копии<input id="restoreBackupFile" type="file" accept=".zip,.enc" /></label>
            <label>Подтверждение<input id="restoreConfirmation" placeholder="Введите ВОССТАНОВИТЬ" /></label>
            <button class="danger" data-action="restore-account">Восстановить из копии</button>
          </div>
        </article>
      </div>
    </section>
  </main>

  <dialog id="scannerDialog" class="confirm-dialog scanner-dialog">
    <form method="dialog">
      <h2>Сканирование кода</h2>
      <p>Наведите камеру на QR-код или штрихкод позиции.</p>
      <video id="scannerVideo" autoplay playsinline muted></video>
      <div id="scannerStatus" class="stock-hint">Запуск камеры…</div>
      <div class="actions"><button value="cancel">Закрыть</button></div>
    </form>
  </dialog>

  <dialog id="operationConfirmDialog" class="confirm-dialog">
    <form method="dialog">
      <h2>Проверьте запись</h2>
      <p id="confirmSummary" class="confirm-summary"></p>
      <div id="confirmBalances" class="list compact"></div>
      <div id="confirmComponents" class="list compact"></div>
      <div id="confirmWarnings" class="confirm-warnings hidden"></div>
      <div class="actions">
        <button value="cancel">Вернуться</button>
        <button id="confirmOperationButton" value="default" type="button" class="primary">Сохранить</button>
      </div>
    </form>
  </dialog>

  <nav class="mobile-nav primary-nav" aria-label="Основные разделы">
    <button data-tab="work" data-primary-nav="production"><span>🏭</span>Производство</button>
    <button data-tab="overview" data-primary-nav="stock"><span>📦</span>Склад</button>
    <button data-tab="plan" data-primary-nav="plan" data-section="assembly"><span>📋</span>План</button>
    <button data-tab="reports" data-primary-nav="reports" data-section="reports"><span>📊</span>Отчёты</button>
    <button data-tab="more" data-primary-nav="more"><span>☰</span>Ещё</button>
  </nav>
  <script src="/static/app-20260813b.js?v=84b-entity-code-hotfix"></script>
</body>
</html>
