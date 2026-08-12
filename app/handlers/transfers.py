from __future__ import annotations

import re
from aiogram.types import BufferedInputFile, Message

from ..services import repository as repo, stock_transfers, excel_bridge
from ..services.normalize import normalize_key


def _scope_for_message(message: Message) -> int | None:
    if message.chat.type == "private":
        account = repo.get_active_account(message.chat.id)
        if not account and message.from_user:
            accounts = repo.list_accounts_for_user(message.from_user.id, message.chat.id, include_accessible=True)
            account = accounts[0] if len(accounts) == 1 else None
        return int(account.scope_chat_id) if account else None
    return int(repo.resolve_scope_chat_id(message.chat.id))


def _find_area(scope: int, text: str):
    key = normalize_key(text)
    rows = repo.list_areas(scope)
    exact = [x for x in rows if normalize_key(x.name) == key]
    if len(exact) == 1:
        return exact[0]
    partial = [x for x in rows if key and key in normalize_key(x.name)]
    return partial[0] if len(partial) == 1 else None


def _find_department(scope: int, text: str):
    key = normalize_key(text)
    rows = repo.list_departments(scope)
    exact = [x for x in rows if normalize_key(str(x.get("name") or "")) == key]
    if len(exact) == 1:
        return exact[0]
    partial = [x for x in rows if key and key in normalize_key(str(x.get("name") or ""))]
    return partial[0] if len(partial) == 1 else None


def _find_entity(scope: int, text: str):
    key = normalize_key(text)
    rows = repo.list_entities(scope, {"material", "component", "product", "stock_item"})
    exact = [x for x in rows if normalize_key(x.name) == key]
    if len(exact) == 1:
        return exact[0]
    partial = [x for x in rows if key and key in normalize_key(x.name)]
    return partial[0] if len(partial) == 1 else None


def _format_transfers(scope: int, uid: int) -> str:
    rows = stock_transfers.list_transfers(scope, uid, limit=20)
    if not rows:
        return (
            "Передач пока нет.\n\n"
            "Создать одной строкой:\n"
            "передача: 100 | Позиция | Участок откуда | Участок куда | Отдел откуда | Отдел куда\n\n"
            "Принять: принять передачу 15\n"
            "При расхождении: принять передачу 15: 95 | причина"
        )
    lines = ["Передачи:"]
    for t in rows:
        status = {
            "sent": "В ПУТИ · ждёт приёмки",
            "accepted": "принято",
            "accepted_discrepancy": "принято с расхождением",
            "cancelled": "отменено",
        }.get(str(t.get("status") or ""), str(t.get("status") or ""))
        place = " → ".join(filter(None, [
            " / ".join(filter(None, [t.get("from_area_name"), t.get("from_department_name")])),
            " / ".join(filter(None, [t.get("to_area_name"), t.get("to_department_name")])),
        ]))
        items = ", ".join(f"{x.get('entity_name')} {float(x.get('sent_quantity') or 0):g} {x.get('unit') or ''}" for x in t.get("items") or [])
        lines.append(f"• №{t['id']} · {status}\n  {place}\n  {items}")
    return "\n".join(lines)


async def try_handle_transfer_command(message: Message) -> bool:
    text = (message.text or "").strip()
    key = normalize_key(text)
    if not text or not message.from_user:
        return False
    relevant = (
        key in {"передачи", "мои передачи", "входящие передачи", "transfers"}
        or key.startswith("принять передачу ")
        or key.startswith("передача ")
        or key.startswith("передача:")
        or key.startswith("ведомость ")
    )
    if not relevant:
        return False
    scope = _scope_for_message(message)
    if not scope:
        await message.answer("Сначала выберите рабочий учёт в боте.")
        return True
    uid = int(message.from_user.id)
    account = repo.get_account_by_scope(scope)
    if not account or not repo.user_has_account_access(account.id, uid):
        await message.answer("Нет доступа к выбранной организации.")
        return True

    if key in {"передачи", "мои передачи", "входящие передачи", "transfers"}:
        try:
            await message.answer(_format_transfers(scope, uid))
        except Exception as exc:
            await message.answer(f"Передачи не загружены: {exc}")
        return True

    if key.startswith("ведомость "):
        kind_text = key.removeprefix("ведомость ").strip()
        entity_type = {
            "детали": "component", "деталь": "component", "комплектующие": "component",
            "сырье": "material", "сырьё": "material", "материалы": "material",
            "изделия": "product", "готовая продукция": "product", "продукция": "product",
            "склад": "stock_item", "складские позиции": "stock_item",
        }.get(kind_text)
        if not entity_type:
            await message.answer("Доступно: ведомость детали / сырьё / изделия / склад")
            return True
        try:
            data = excel_bridge.build_location_ledger_xlsx(scope, uid, entity_type)
            await message.answer_document(
                BufferedInputFile(data, filename=f"vedomost_{entity_type}.xlsx"),
                caption="Готово. Таблица построена из текущего учёта по местам хранения.",
            )
        except Exception as exc:
            await message.answer(f"Ведомость не сформирована: {exc}")
        return True

    m = re.match(r"^принять\s+передачу\s+(\d+)(?:\s*:\s*([^|]+)(?:\|(.+))?)?$", text, re.I)
    if m:
        transfer_id = int(m.group(1))
        try:
            transfer = stock_transfers.get_transfer(scope, transfer_id)
            if not transfer:
                raise ValueError("Передача не найдена.")
            actual = None
            note = (m.group(3) or "").strip()
            if m.group(2):
                if len(transfer.get("items") or []) != 1:
                    raise ValueError("Для передачи из нескольких позиций пересчёт выполните в Mini App.")
                actual_qty = float(m.group(2).strip().replace(",", "."))
                item = transfer["items"][0]
                actual = [{"item_id": int(item["id"]), "quantity": actual_qty}]
            result = stock_transfers.accept_transfer(scope, uid, transfer_id, actual, note)
            await message.answer(f"Передача №{transfer_id} принята. Статус: {result.get('status')}.")
        except Exception as exc:
            await message.answer(f"Передача не принята: {exc}")
        return True

    if key.startswith("передача"):
        payload = re.sub(r"^передача\s*:\s*", "", text, flags=re.I).strip()
        parts = [x.strip() for x in payload.split("|")]
        if len(parts) < 4:
            await message.answer(
                "Формат:\nпередача: 100 | Позиция | Участок откуда | Участок куда | Отдел откуда | Отдел куда"
            )
            return True
        try:
            amount = float(parts[0].replace(" ", "").replace(",", "."))
            entity = _find_entity(scope, parts[1])
            from_area = _find_area(scope, parts[2])
            to_area = _find_area(scope, parts[3])
            from_dep = _find_department(scope, parts[4]) if len(parts) > 4 and parts[4] else None
            to_dep = _find_department(scope, parts[5]) if len(parts) > 5 and parts[5] else None
            if not entity:
                raise ValueError("Позиция не найдена однозначно.")
            if not from_area or not to_area:
                raise ValueError("Участок отправления или получения не найден однозначно.")
            if not from_dep:
                own = repo.user_department_memberships(scope, uid)
                if len(own) == 1:
                    from_dep = {"id": own[0]["department_id"], "name": own[0]["department_name"]}
            if not from_dep:
                raise ValueError("Укажите отдел отправителя или выберите его в Mini App.")
            if not to_dep:
                raise ValueError("Укажите отдел получателя.")
            result = stock_transfers.create_transfer(
                scope, uid,
                from_area_id=int(from_area.id), to_area_id=int(to_area.id),
                from_department_id=int(from_dep["id"]), to_department_id=int(to_dep["id"]),
                items=[{"entity_id": int(entity.id), "quantity": amount, "unit": entity.default_unit or "шт"}],
            )
            await message.answer(
                f"Передача №{result['id']} создана. Остаток получателя изменится только после подтверждения приёмки."
            )
        except Exception as exc:
            await message.answer(f"Передача не создана: {exc}")
        return True
    return False
