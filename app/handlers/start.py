from __future__ import annotations

from ._safe import safe_edit_text
from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from ..keyboards import main_menu, setup_menu
from ..services import repository as repo

router = Router()

HELP_TEXT = """Как пользоваться ботом

Главное меню

Настроить учёт
Открывает создание участков, должностей, изделий, комплектующих, сырья, складских позиций и счётчиков.
В каждом шаге бот показывает, что уже создано, и просит ввести только нужное название.
Пример названия: Изделие 1

Группы
Показывает рабочие группы, где есть бот. Здесь можно подключить группу, выбрать участок для сырья и счётчиков или открыть настройку выбранного учёта.

Склад
Показывает остатки по изделиям, комплектующим, сырью и складским позициям.

Отчёты
Показывает итоги за день, неделю, месяц или выбранный период. В отчётах можно выбрать, какие разделы показать и какой файл скачать.

Работники
Показывает назначенных людей и их должности.
Чтобы назначить должность:
1. В рабочей группе ответьте на сообщение нужного человека.
2. Напишите: назначить должность
3. Бот пришлёт в личку список созданных должностей.
4. Выберите должность кнопкой и подтвердите.

Как пользоваться
Открывает эту памятку.

Короткие команды в группе

Подключить группу:
подключить группу

Назначить должность ответом на сообщение:
назначить должность

Задать себе видимую должность:
моя должность Смена 1

Создать участок:
создать участок Участок 1

Создать изделие:
создать изделие Изделие 1

Создать комплектующую:
добавить комплектующую Комплектующая 1

Создать сырьё:
добавить сырьё Сырьё 1

Создать счётчик:
добавить счётчик Счётчик 1

Задать состав изделия:
состав Изделие 1: Комплектующая 1 2 шт, Комплектующая 2 1 шт

Добавить сокращения:
синонимы Изделие 1: короткое название, рабочее название

Сдать производство:
произвели Участок 1 Комплектующая 1 100 шт

Сдать расход сырья:
израсходовали Участок 1 Сырьё 1 25 кг

Сдать приход сырья:
привезли Участок 1 Сырьё 1 100 кг

Сдать показания счётчика:
счётчик Участок 1 12500

Отчёт:
отчёт за сегодня
отчёт за неделю
отчёт с 01.06.2026 по 15.06.2026

Как бот понимает сообщения
Бот берёт только явные учётные фразы. Обычную переписку он не трогает.
Если не хватает участка, количества или название похоже на несколько вариантов, бот попросит подтвердить кнопками.

Совет
Сначала создайте участки, должности, изделия, комплектующие, сырьё и счётчики. Потом назначьте людям должности и подключите рабочие группы."""


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
    await safe_edit_text(callback.message, HELP_TEXT, reply_markup=main_menu())
    await callback.answer()


@router.message(F.text.lower().in_({"как пользоваться", "помощь", "инструкция"}))
async def help_text(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu())


@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery) -> None:
    await safe_edit_text(callback.message, "Главное меню", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:setup")
async def menu_setup(callback: CallbackQuery) -> None:
    await safe_edit_text(callback.message, "Настройка учёта", reply_markup=setup_menu())
    await callback.answer()
