from __future__ import annotations

from ._safe import safe_edit_text
from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from ..access import is_chat_creator, is_global_owner
from ..keyboards import chat_list_keyboard, help_topic_keyboard, main_menu, setup_menu
from ..services import repository as repo

router = Router()

HELP_PAGES: dict[str, str] = {
    "start": """Как пользоваться

Бот помогает вести учёт производства, комплектующих, склада, сборки, фасовки, отправки, продажи и отчётов.

С чего начать:
1. Добавьте бота в рабочую группу.
2. Откройте бота в личных сообщениях.
3. Нажмите «Группы».
4. Выберите нужную группу.
5. Нажмите «Открыть настройку».
6. Создайте должности, изделия, комплектующие, сырьё, складские позиции и счётчики.

После настройки сотрудники смогут писать данные прямо в группе. Перед сохранением бот покажет проверку и кнопки «Да», «Исправить», «Отмена».""",
    "what": """Что для чего нужно

Группа — отдельный учёт. У каждой группы свои сотрудники, должности, изделия, склад и отчёты. Данные разных групп не смешиваются.

Участок — место работы: линия, станок, цех или направление. Нужен, чтобы понимать, где сделали продукцию, куда пришло сырьё или где был расход.

Должность — доступ человека в учёте. Нужна, чтобы один сотрудник только вносил данные, другой смотрел отчёты, а ответственный настраивал учёт и назначал людей.

Изделие — готовая продукция, которую собирают из комплектующих.

Комплектующая — деталь изделия. По комплектующим бот считает остатки, сборку и нехватку.

Норма комплектации — сколько каждой комплектующей нужно на 1 изделие. Без нормы бот не сможет точно посчитать, сколько изделий можно собрать.

Сырьё — материал, который приходит и расходуется.

Позиция склада — любая вещь, которую нужно учитывать отдельно: коробки, пакеты, готовые партии.

Счётчик — показания электроэнергии, воды или другого расхода.

Отчёт — итоги за день, неделю, месяц или выбранный период. Полные таблицы доступны в Excel и PDF.""",
    "setup": """Настройка учёта

Настройка открывается в личных сообщениях через «Группы» → нужная группа → «Открыть настройку».

Что создать сначала:
1. Должности.
2. Изделия.
3. Комплектующие.
4. Состав изделия.
5. Сырьё и складские позиции.
6. Счётчики, если нужно учитывать показания.

Перед созданием новой позиции бот показывает, что уже есть. Это помогает не плодить одинаковые названия.

Пример настройки изделия:
Создать изделие: Изделие 1
Добавить комплектующие: Деталь А, Деталь Б
Состав изделия: Деталь А — 1 шт, Деталь Б — 2 шт

После этого бот сможет считать остатки, сборку и нехватку комплектующих.""",
    "jobs": """Должности

Должности нужны для доступа. Названия придумывает владелец учёта.

Как создать должность:
1. Откройте «Настроить учёт».
2. Нажмите «Создать должность».
3. Введите название.
4. Отметьте нужные права кнопками.
5. Сохраните.

Пример названия: Смена 1

Как назначить должность человеку в группе:
1. Найдите сообщение человека.
2. Ответьте на него фразой: назначить должность
3. Бот покажет список должностей.
4. Выберите должность кнопкой.
5. Подтвердите.

Можно сразу написать: назначить должность Смена 1

Если должность не видна, откройте в личке «Группы», выберите рабочую группу и проверьте, что должность создана именно для этого учёта.""",
    "items": """Изделия, комплектующие и склад

Изделие — готовая продукция.
Комплектующая — деталь, которая входит в изделие.
Складская позиция — отдельная вещь для учёта остатков.
Сырьё — материал для прихода и расхода.

Пример изделия:
Изделие 1

Пример комплектующих:
Деталь А
Деталь Б
Деталь В

Пример нормы комплектации:
Для 1 изделия нужно:
• Деталь А — 1 шт
• Деталь Б — 2 шт
• Деталь В — 4 шт

После этого бот покажет:
• сколько изделий можно собрать;
• чего не хватает;
• сколько нужно для выбранного количества.

Если название введено неполно, бот предложит выбрать сохранённую позицию кнопкой.""",
    "work": """Ввод данных в группе

Пишите рабочую запись коротко: действие, название и количество.

Производство комплектующих:
Производство Деталь А 5000
Сделали Деталь Б 12000

Можно отправить несколько строк одним сообщением:
Сделали сегодня:
Деталь А 5000
Деталь Б 12000
Деталь В 3000

Сборка готового изделия:
Собрали Изделие 1 1000
Сборка Изделие 1 500

Фасовка, отправка, продажа:
Зафасовали Изделие 1 1000
На отправку Изделие 1 500
К продаже Изделие 1 300
Продано Изделие 1 200

Сырьё:
Приход Пластик 500 кг
Расход Пластик 120 кг

Счётчик:
Показание Счётчик 12500
Показание Электроэнергия 12500

Если название написано коротко, бот предложит выбрать сохранённую позицию кнопкой. После подтверждения выбранное название будет пониматься в следующих записях.

Перед сохранением бот покажет проверку. Если всё верно — нажмите «Да». Если ошибка — «Исправить». Если запись не нужна — «Отмена».""",
    "reports": """Отчёты

Отчёт можно запросить в группе или в личке.

Примеры:
Отчёт за сегодня
Отчёт за неделю
Отчёт за месяц
Отчёт с 01.06.2026 по 20.06.2026

Excel отчёт за месяц
PDF отчёт за неделю

План сборки:
1. Откройте «Отчёты».
2. Нажмите «План сборки».
3. Выберите изделие.
4. Введите одно или несколько количеств.

Что видно в отчётах:
• сколько сделано комплектующих;
• остатки комплектующих;
• сколько изделий можно собрать;
• чего не хватает;
• сколько собрано;
• сколько зафасовано;
• сколько отправлено или продано;
• журнал записей.

Отчёт из нескольких групп:
1. Откройте бота в личке.
2. Нажмите «Отчёты».
3. Нажмите «Отчёт из нескольких групп».
4. Отметьте группы галочками.
5. Выберите «Показать», «Excel» или «PDF».

В общий отчёт попадают только те группы, где у вас есть доступ или где вы владелец.""",
    "examples": """Краткие примеры

Производство Деталь А 5000
Сделали Деталь Б 12000
Сделали сегодня:
Деталь А 5000
Деталь Б 12000
Собрали Изделие 1 1000
Зафасовали Изделие 1 500
На отправку Изделие 1 300
Продано Изделие 1 200
Приход Пластик 500 кг
Расход Пластик 120 кг
Отчёт за месяц
Excel отчёт за месяц
PDF отчёт за неделю
Показание Счётчик 12500
План сборки Изделие 1 50000
План сборки: Изделие 1 50000; Изделие 2 25000
Назначить должность

Если бот не понял название, он предложит выбрать подходящую позицию кнопкой.""",
}


def _help_text(topic: str) -> str:
    return HELP_PAGES.get(topic, HELP_PAGES["start"])



async def _manageable_group_chats(bot, user_id: int | None) -> list[dict]:
    if not user_id:
        return []
    result: list[dict] = []
    for chat in repo.list_known_group_chats(limit=200):
        chat_id = int(chat["chat_id"])
        if is_global_owner(user_id) or repo.user_has_manage_access_to_chat(chat_id, user_id):
            result.append(chat)
            continue
        if await is_chat_creator(bot, chat_id, user_id):
            result.append(chat)
    return result


def _private_title(callback: CallbackQuery) -> str:
    chat = callback.message.chat
    return getattr(chat, "full_name", None) or getattr(chat, "title", None) or "Личный чат"




def _selected_group_chat_id(private_chat_id: int) -> int | None:
    account = repo.get_active_account(private_chat_id)
    if not account:
        return None
    chat = repo.get_chat_info(account.owner_chat_id)
    if chat and str(chat.get("chat_type") or "") in {"group", "supergroup"}:
        return int(account.owner_chat_id)
    return None

@router.message(CommandStart())
async def start(message: Message) -> None:
    repo.upsert_chat(message.chat.id, message.chat.title or message.chat.full_name or "", message.chat.type)
    if message.from_user:
        repo.clear_setup_session(message.chat.id, message.from_user.id)
    if message.chat.type == "private":
        await message.answer(
            "Производственный учёт\n\nНачните с настройки или подключите рабочую группу. Подсказки есть в разделе «Как пользоваться».",
            reply_markup=main_menu(),
        )
    else:
        await message.answer("Группа видна в личке бота у владельца и людей с правом управления. Откройте бота в личке и нажмите «Группы».")


@router.callback_query(F.data == "menu:help")
async def menu_help(callback: CallbackQuery) -> None:
    await safe_edit_text(callback.message, _help_text("start"), reply_markup=help_topic_keyboard("start"))
    await callback.answer()


@router.callback_query(F.data.startswith("help:"))
async def help_topic(callback: CallbackQuery) -> None:
    topic = (callback.data or "help:start").split(":", 1)[1] or "start"
    await safe_edit_text(callback.message, _help_text(topic), reply_markup=help_topic_keyboard(topic))
    await callback.answer()


@router.message(F.text.lower().in_({"как пользоваться", "помощь", "инструкция"}))
async def help_text(message: Message) -> None:
    await message.answer(_help_text("start"), reply_markup=help_topic_keyboard("start"))


@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery) -> None:
    await safe_edit_text(callback.message, "Главное меню", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:setup")
async def menu_setup(callback: CallbackQuery) -> None:
    if callback.message.chat.type == "private" and callback.from_user:
        groups = await _manageable_group_chats(callback.bot, callback.from_user.id)
        if len(groups) == 1:
            group = groups[0]
            group_chat_id = int(group["chat_id"])
            title = str(group.get("title") or group_chat_id)
            chat_type = str(group.get("chat_type") or "supergroup")
            repo.ensure_group_account_context(
                group_chat_id,
                title,
                chat_type,
                callback.from_user.id,
                private_chat_id=callback.message.chat.id,
                private_title=_private_title(callback),
            )
            await safe_edit_text(callback.message, f"Настройка учёта\n\nГруппа: {title}", reply_markup=setup_menu())
            await callback.answer()
            return
        if len(groups) > 1:
            selected_chat_id = _selected_group_chat_id(callback.message.chat.id)
            await safe_edit_text(
                callback.message,
                "Ваши группы\n\nДля настройки отметьте одну группу. Нажатие по группе только ставит или убирает галочку. После выбора нажмите «Открыть настройку».\n\nДля общего отчёта используйте кнопку «Отчёт из нескольких групп».",
                reply_markup=chat_list_keyboard(groups, selected_chat_id=selected_chat_id),
            )
            await callback.answer()
            return
        repo.ensure_private_account_context(
            callback.from_user.id,
            callback.message.chat.id,
            callback.message.chat.full_name or callback.message.chat.title or "Личный чат",
        )
    await safe_edit_text(callback.message, "Настройка учёта", reply_markup=setup_menu())
    await callback.answer()
