from __future__ import annotations

import re
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ..services import production_flow
from ..services import quality_control
from ..services import replenishment
from ..services import maintenance_planning
from ..services import repository as repo
from ..services.normalize import normalize_key

router = Router()

_TASK_STATUS = {
    "planned": "план",
    "in_progress": "в работе",
    "paused": "пауза",
    "completed": "готово",
    "cancelled": "отменено",
}
_REQUEST_STATUS = {
    "requested": "ожидает подтверждения",
    "approved": "подтверждена",
    "issued": "выдано",
    "partially_received": "получено частично",
    "received": "получено",
    "rejected": "отклонена",
    "cancelled": "отменена",
}
_TASK_ACTIONS = {
    "начать": "start", "старт": "start", "в работу": "start",
    "пауза": "pause", "приостановить": "pause", "остановить": "pause",
    "готово": "complete", "завершить": "complete", "выполнено": "complete",
    "отменить": "cancel", "отмена": "cancel",
    "возобновить": "reopen", "вернуть": "reopen", "переоткрыть": "reopen",
}
_REQUEST_ACTIONS = {
    "подтвердить": "approve", "одобрить": "approve",
    "выдать": "issue", "выдача": "issue",
    "получить": "receive", "принять": "receive", "получено": "receive",
    "отклонить": "reject", "отказать": "reject",
    "отменить": "cancel", "отмена": "cancel",
}


def _scope(message: Message) -> int:
    return repo.resolve_scope_chat_id(message.chat.id)


def _user(message: Message) -> int:
    return int(message.from_user.id) if message.from_user else 0


def _fmt_qty(value: Any) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "0"


def _visible_workflow(scope: int, user_id: int) -> bool:
    if repo.is_system_admin_id(user_id):
        return True
    return bool(repo.user_has_department_membership(scope, user_id))


def _tasks_text(scope: int, user_id: int) -> str:
    tasks = production_flow.list_tasks(scope, user_id, limit=30)
    active = [x for x in tasks if x.get("status") not in {"completed", "cancelled"}]
    lines = ["Задания"]
    if not active:
        lines.append("Активных заданий нет.")
    for item in active[:20]:
        actual = _fmt_qty(item.get("actual_quantity"))
        target = _fmt_qty(item.get("target_quantity"))
        due = str(item.get("due_at") or "")[:16]
        due_text = f" · срок {due}" if due else ""
        lines.append(
            f"№{item['id']} · {_TASK_STATUS.get(str(item.get('status')), str(item.get('status')))} · "
            f"{item.get('entity_name')} · {actual}/{target} {item.get('unit') or 'шт'} · {item.get('department_name')}{due_text}"
        )
    lines += ["", "Действие: задание <номер> начать | пауза | готово [причина] | отменить <причина>"]
    return "\n".join(lines)


def _requests_text(scope: int, user_id: int) -> str:
    requests = production_flow.list_requests(scope, user_id, limit=30)
    open_items = [x for x in requests if x.get("status") not in {"received", "rejected", "cancelled"}]
    lines = ["Внутренние заявки"]
    if not open_items:
        lines.append("Открытых заявок нет.")
    for item in open_items[:20]:
        qty = _fmt_qty(item.get("requested_quantity"))
        issued = _fmt_qty(item.get("issued_quantity"))
        received = _fmt_qty(item.get("received_quantity"))
        lines.append(
            f"№{item['id']} · {_REQUEST_STATUS.get(str(item.get('status')), str(item.get('status')))} · "
            f"{item.get('entity_name')} {qty} {item.get('unit') or 'шт'} · "
            f"{item.get('requester_department_name')} → {item.get('supplier_department_name')} · выдано {issued}, получено {received}"
        )
    lines += ["", "Действие: заявка <номер> подтвердить [кол-во] | выдать <кол-во> | получить <кол-во> | отклонить <причина> | отменить <причина>"]
    return "\n".join(lines)


def _equipment_text(scope: int, user_id: int) -> str:
    items = production_flow.list_equipment(scope, user_id)
    lines = ["Оборудование"]
    if not items:
        lines.append("Доступного оборудования нет.")
    for item in items[:30]:
        state = "🔴 простой" if int(item.get("open_downtimes") or 0) else ("ТО" if item.get("status") == "maintenance" else "работает")
        next_service = str(item.get("next_service_at") or "")[:10]
        service = f" · ТО до {next_service}" if next_service else ""
        lines.append(f"№{item['id']} · {item.get('name')} · {state}{service}")
    lines += ["", "Сообщить: простой оборудование <номер> <причина>", "Закрыть: закрыть простой <номер простоя> <результат>", "ТО: обслуживание оборудование <номер> <заметка>"]
    return "\n".join(lines)


def _quality_text(scope:int,user_id:int)->str:
    snap=quality_control.quality_snapshot(scope,user_id);lines=["Контроль качества"]
    active=[x for x in snap.get("inspections",[]) if x.get("status") in {"open","waiting_rework","quarantined","rework"}]
    if not active:lines.append("Открытых проверок нет.")
    for x in active[:20]:lines.append(f"№{x['id']} · {x.get('entity_name')} · {x.get('status')} · проверено {_fmt_qty(x.get('checked_quantity'))}, несоответствие {_fmt_qty(x.get('defect_quantity'))} {x.get('unit') or 'шт'}"+(f" · партия {x.get('lot_code')}" if x.get('lot_code') else ""))
    lines += ["", "Решение: контроль <номер> годно | карантин <причина> | доработка <причина> | списать <причина>", "Новая проверка: контроль; позиция=...; отдел=...; проверено=...; брак=...; партия=<номер>"]
    return "\n".join(lines)

def _replenishment_text(scope:int,user_id:int)->str:
    snap=replenishment.snapshot(scope,user_id);lines=["Пополнение запасов"]
    suggestions=[x for x in snap.get("suggestions",[]) if float(x.get("recommended_quantity") or 0)>0]
    if not suggestions:lines.append("Срочных рекомендаций по пополнению нет.")
    for x in suggestions[:15]:lines.append(f"• {x.get('entity_name')} · {x.get('severity')} · рекомендуем {_fmt_qty(x.get('recommended_quantity'))} {x.get('unit') or ''} · запас {_fmt_qty(x.get('reserve_shifts')) if x.get('reserve_shifts') is not None else '—'} смен")
    opened=[x for x in snap.get("requests",[]) if x.get("status") in replenishment.OPEN_STATUSES]
    if opened:lines.append("\nОткрытые заявки:")
    for x in opened[:15]:lines.append(f"№{x['id']} · {x.get('entity_name')} · {_fmt_qty(x.get('requested_quantity'))} {x.get('unit') or ''} · {x.get('status')}")
    lines += ["", "Создать: пополнение; позиция=...; количество=...; склад=...", "Решение: пополнение <номер> подтвердить | заказано | получено | отклонить <причина>"]
    return "\n".join(lines)

def _maintenance_plan_text(scope:int,user_id:int)->str:
    snap=maintenance_planning.snapshot(scope,user_id);lines=["Плановое обслуживание"]
    work=[x for x in snap.get("work_orders",[]) if x.get("status") in {"planned","in_progress"}]
    if not work:lines.append("Открытых заданий ТО нет.")
    for x in work[:20]:lines.append(f"№{x['id']} · {x.get('equipment_name')} · {x.get('status')} · срок {str(x.get('due_at') or '')[:16]}")
    lines += ["", "Действие: то задание <номер> начать | готово <результат>"]
    return "\n".join(lines)

def _number_and_tail(text: str, prefix: str) -> tuple[int, str] | None:
    m = re.match(rf"^{prefix}\s+[№#]?(\d+)\s+(.+)$", text, re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1)), m.group(2).strip()


def _parse_action_tail(tail: str, mapping: dict[str, str]) -> tuple[str, str] | None:
    norm = normalize_key(tail)
    for phrase in sorted(mapping, key=len, reverse=True):
        key = normalize_key(phrase)
        if norm == key or norm.startswith(key + " "):
            raw_rest = tail[len(phrase):].strip() if tail.lower().startswith(phrase.lower()) else ""
            if not raw_rest and norm != key:
                # normalisation may have changed ё/spacing; take remaining words by count.
                raw_rest = " ".join(tail.split()[len(phrase.split()):]).strip()
            return mapping[phrase], raw_rest
    return None


def _qty_reason(rest: str) -> tuple[float | None, str]:
    rest = str(rest or "").strip()
    if not rest:
        return None, ""
    m = re.match(r"^(\d+(?:[.,]\d+)?)(?:\s+(.+))?$", rest)
    if not m:
        return None, rest
    qty = float(m.group(1).replace(",", "."))
    return qty, str(m.group(2) or "").strip()



def _fields(text: str) -> dict[str, str]:
    parts = [x.strip() for x in text.split(";") if x.strip()]
    result: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
        elif ":" in part:
            key, value = part.split(":", 1)
        else:
            continue
        result[normalize_key(key)] = value.strip()
    return result


def _pick(fields: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = fields.get(normalize_key(name))
        if value is not None:
            return value
    return default


def _resolve_department(scope: int, user_id: int, value: str) -> int:
    raw = str(value or "").strip().lstrip("№#")
    options = production_flow.workflow_options(scope, user_id).get("departments") or []
    if raw.isdigit():
        dep_id = int(raw)
        if any(int(x.get("id") or 0) == dep_id for x in options):
            return dep_id
    key = normalize_key(raw)
    matches = [x for x in options if normalize_key(str(x.get("name") or "")) == key]
    if len(matches) == 1:
        return int(matches[0]["id"])
    raise ValueError("Отдел не найден или название неоднозначно. Укажите точное название или номер отдела.")


def _resolve_entity(scope: int, user_id: int, value: str) -> int:
    raw = str(value or "").strip().lstrip("№#")
    visible = repo.visible_entity_ids_for_user(scope, user_id)
    items = repo.list_entities(scope)
    if visible is not None:
        items = [x for x in items if int(x.id) in {int(v) for v in visible}]
    if raw.isdigit():
        entity_id = int(raw)
        if any(int(x.id) == entity_id for x in items):
            return entity_id
    key = normalize_key(raw)
    matches = [x for x in items if x.normalized == key]
    if len(matches) == 1:
        return int(matches[0].id)
    raise ValueError("Позиция не найдена или название неоднозначно. Укажите точное название или номер позиции.")


def _resolve_area(scope: int, value: str) -> int | None:
    raw = str(value or "").strip().lstrip("№#")
    if not raw:
        return None
    items = repo.list_areas(scope)
    if raw.isdigit():
        area_id = int(raw)
        if any(int(x.id) == area_id for x in items):
            return area_id
    key = normalize_key(raw)
    matches = [x for x in items if x.normalized == key]
    if len(matches) == 1:
        return int(matches[0].id)
    raise ValueError("Площадка/склад не найден. Укажите точное название или номер.")


def _priority(value: str) -> str:
    key = normalize_key(value)
    return {
        "низкий": "low", "low": "low", "обычный": "normal", "нормальный": "normal", "normal": "normal",
        "высокий": "high", "high": "high", "срочно": "urgent", "срочный": "urgent", "urgent": "urgent",
    }.get(key, "normal")


def _operation(value: str) -> str:
    key = normalize_key(value)
    return {
        "изготовление": "production", "производство": "production", "production": "production",
        "сборка": "assembly", "assembly": "assembly", "fulfillment": "fulfillment",
        "отгрузка": "shipment", "shipment": "shipment", "приход": "material_in", "расход": "material_out",
        "списание": "write_off", "write_off": "write_off",
    }.get(key, str(value or "production").strip() or "production")


async def _create_from_chat(message: Message, scope: int, user_id: int, text: str) -> bool:
    key = normalize_key(text.split(";", 1)[0])
    if key in {"создать задание", "новое задание"}:
        f = _fields(text)
        try:
            dep = _resolve_department(scope, user_id, _pick(f, "отдел"))
            entity = _resolve_entity(scope, user_id, _pick(f, "позиция", "изделие"))
            target_raw = _pick(f, "план", "количество")
            if not target_raw:
                raise ValueError("Укажите план. Например: план=100")
            task = production_flow.create_task(
                scope, user_id, dep, entity,
                operation_type=_operation(_pick(f, "операция", "действие", default="production")),
                target_quantity=float(target_raw.replace(",", ".")),
                unit=_pick(f, "единица", "ед", default="шт"),
                title=_pick(f, "название"),
                assignee_user_id=int(_pick(f, "исполнитель")) if _pick(f, "исполнитель").isdigit() else None,
                shift_plan_id=int(_pick(f, "смена")) if _pick(f, "смена").isdigit() else None,
                area_id=_resolve_area(scope, _pick(f, "площадка", "склад")),
                priority=_priority(_pick(f, "приоритет", default="normal")),
                due_at=_pick(f, "срок") or None,
                note=_pick(f, "примечание", "комментарий"),
            )
            await message.answer(f"Задание №{task['id']} создано: {task.get('entity_name')} · план {_fmt_qty(task.get('target_quantity'))} {task.get('unit') or 'шт'}.")
        except (ValueError, PermissionError) as exc:
            await message.answer(str(exc))
        return True
    if key in {"создать заявку", "новая заявка"}:
        f = _fields(text)
        try:
            requester = _resolve_department(scope, user_id, _pick(f, "от", "отдел от", "получатель"))
            supplier = _resolve_department(scope, user_id, _pick(f, "в", "отдел в", "поставщик"))
            entity = _resolve_entity(scope, user_id, _pick(f, "позиция"))
            qty_raw = _pick(f, "количество", "кол-во")
            if not qty_raw:
                raise ValueError("Укажите количество.")
            item = production_flow.create_request(
                scope, user_id, requester, supplier, entity, float(qty_raw.replace(",", ".")),
                unit=_pick(f, "единица", "ед", default="шт"),
                from_area_id=_resolve_area(scope, _pick(f, "откуда", "склад от")),
                to_area_id=_resolve_area(scope, _pick(f, "куда", "склад в")),
                priority=_priority(_pick(f, "приоритет", default="normal")), needed_at=_pick(f, "срок") or None,
                note=_pick(f, "примечание", "комментарий"),
            )
            await message.answer(f"Заявка №{item['id']} создана: {item.get('entity_name')} {_fmt_qty(item.get('requested_quantity'))} {item.get('unit') or 'шт'}.")
        except (ValueError, PermissionError) as exc:
            await message.answer(str(exc))
        return True
    if key in {"добавить оборудование", "создать оборудование"}:
        f = _fields(text)
        try:
            name = _pick(f, "название", "имя")
            if not name:
                raise ValueError("Укажите название оборудования.")
            dep_text = _pick(f, "отдел")
            dep = _resolve_department(scope, user_id, dep_text) if dep_text else None
            interval = int(float((_pick(f, "то", "интервал то", default="0") or "0").replace(",", ".")))
            warning = int(float((_pick(f, "предупреждение", default="3") or "3").replace(",", ".")))
            item = production_flow.save_equipment(
                scope, user_id, name, department_id=dep,
                area_id=_resolve_area(scope, _pick(f, "площадка", "склад")), code=_pick(f, "код"),
                service_interval_days=interval, warning_before_days=warning, note=_pick(f, "примечание", "комментарий"),
            )
            await message.answer(f"Оборудование №{item['id']} создано: {item.get('name')}.")
        except (ValueError, PermissionError) as exc:
            await message.answer(str(exc))
        return True
    if key in {"контроль", "контроль качества", "проверка качества"}:
        f=_fields(text)
        try:
            entity_id=_resolve_entity(scope,user_id,_pick(f,"позиция","изделие"));dep_text=_pick(f,"отдел");dep=_resolve_department(scope,user_id,dep_text) if dep_text else None
            checked_raw=_pick(f,"проверено","количество");defect_raw=_pick(f,"брак","несоответствие",default="0")
            if not checked_raw:raise ValueError("Укажите проверенное количество.")
            item=quality_control.create_inspection(scope,user_id,entity_id,inspection_type={"входной":"incoming","межоперационный":"in_process","выходной":"output","повторный":"recheck"}.get(normalize_key(_pick(f,"вид",default="выходной")),"output"),department_id=dep,area_id=_resolve_area(scope,_pick(f,"площадка","склад")),lot_id=int(_pick(f,"партия")) if _pick(f,"партия").isdigit() else None,task_id=int(_pick(f,"задание")) if _pick(f,"задание").isdigit() else None,equipment_id=int(_pick(f,"оборудование")) if _pick(f,"оборудование").isdigit() else None,checked_quantity=float(checked_raw.replace(",",".")),defect_quantity=float(defect_raw.replace(",",".")),unit=_pick(f,"единица","ед",default="шт"),note=_pick(f,"причина","примечание","комментарий"))
            await message.answer(f"Контроль качества №{item['id']} сохранён. Статус: {item.get('status')}.")
        except (ValueError,PermissionError) as exc:await message.answer(str(exc))
        return True
    if key in {"пополнение", "заявка пополнение", "создать пополнение"}:
        f=_fields(text)
        try:
            entity_id=_resolve_entity(scope,user_id,_pick(f,"позиция"));qty_raw=_pick(f,"количество","кол-во")
            if not qty_raw:raise ValueError("Укажите количество.")
            ent=repo.get_entity(entity_id);item=replenishment.create_request(scope,user_id,{"entity_id":entity_id,"area_id":_resolve_area(scope,_pick(f,"склад","площадка")),"requested_quantity":float(qty_raw.replace(",",".")),"unit":_pick(f,"единица","ед",default=(ent.default_unit if ent else "шт")),"reason":_pick(f,"причина"),"note":_pick(f,"примечание","комментарий")})
            await message.answer(f"Заявка на пополнение №{item['id']} создана.")
        except (ValueError,PermissionError) as exc:await message.answer(str(exc))
        return True
    return False


async def try_handle_workflow_command(message: Message) -> bool:
    text = (message.text or "").strip()
    key = normalize_key(text)
    user_id = _user(message)
    scope = _scope(message)

    if ";" in text and await _create_from_chat(message, scope, user_id, text):
        return True

    if key in {"мои задания", "задания", "задание", "tasks"}:
        if not _visible_workflow(scope, user_id):
            return False
        await message.answer(_tasks_text(scope, user_id))
        return True

    if key in {"заявки", "мои заявки", "внутренние заявки", "requests"}:
        if not _visible_workflow(scope, user_id):
            return False
        await message.answer(_requests_text(scope, user_id))
        return True

    if key in {"оборудование", "простои оборудования", "equipment"}:
        if not _visible_workflow(scope, user_id):
            return False
        await message.answer(_equipment_text(scope, user_id))
        return True

    if key in {"качество","контроль качества","проверки качества","quality"}:
        if not _visible_workflow(scope,user_id):return False
        await message.answer(_quality_text(scope,user_id));return True
    if key in {"пополнение","закупки","снабжение","replenishment"}:
        if not _visible_workflow(scope,user_id):return False
        await message.answer(_replenishment_text(scope,user_id));return True
    if key in {"то календарь","план то","обслуживание план","maintenance"}:
        if not _visible_workflow(scope,user_id):return False
        await message.answer(_maintenance_plan_text(scope,user_id));return True

    m=re.match(r"^контроль\s+[№#]?(\d+)\s+(годно|карантин|доработка|списать)(?:\s+(.+))?$",text,re.IGNORECASE)
    if m:
        action={"годно":"pass","карантин":"quarantine","доработка":"rework","списать":"write_off"}[normalize_key(m.group(2))];reason=str(m.group(3) or "").strip()
        try:item=quality_control.decide_inspection(scope,user_id,int(m.group(1)),action,reason=reason);await message.answer(f"Контроль №{m.group(1)}: {item.get('status')}.")
        except (ValueError,PermissionError) as exc:await message.answer(str(exc))
        return True
    m=re.match(r"^пополнение\s+[№#]?(\d+)\s+(подтвердить|заказано|получено|отклонить|отменить)(?:\s+(.+))?$",text,re.IGNORECASE)
    if m:
        action={"подтвердить":"approve","заказано":"order","получено":"receive","отклонить":"reject","отменить":"cancel"}[normalize_key(m.group(2))];reason=str(m.group(3) or "").strip()
        try:item=replenishment.request_action(scope,user_id,int(m.group(1)),action,reason=reason);await message.answer(f"Пополнение №{m.group(1)}: {item.get('status')}.")
        except (ValueError,PermissionError) as exc:await message.answer(str(exc))
        return True
    m=re.match(r"^то\s+задание\s+[№#]?(\d+)\s+(начать|готово|отменить)(?:\s+(.+))?$",text,re.IGNORECASE)
    if m:
        action={"начать":"start","готово":"complete","отменить":"cancel"}[normalize_key(m.group(2))];result=str(m.group(3) or "").strip()
        try:item=maintenance_planning.work_action(scope,user_id,int(m.group(1)),action,result=result);await message.answer(f"ТО №{m.group(1)}: {item.get('status')}.")
        except (ValueError,PermissionError) as exc:await message.answer(str(exc))
        return True

    parsed = _number_and_tail(text, r"задани[ея]")
    if parsed:
        task_id, tail = parsed
        action_info = _parse_action_tail(tail, _TASK_ACTIONS)
        if not action_info:
            await message.answer("Не понял действие. Пример: задание 12 начать")
            return True
        action, reason = action_info
        try:
            item = production_flow.task_action(scope, user_id, task_id, action, reason=reason)
            await message.answer(
                f"Задание №{task_id}: {_TASK_STATUS.get(str(item.get('status')), str(item.get('status')))}. "
                f"Факт {_fmt_qty(item.get('actual_quantity'))}/{_fmt_qty(item.get('target_quantity'))} {item.get('unit') or 'шт'}."
            )
        except (ValueError, PermissionError) as exc:
            await message.answer(str(exc))
        return True

    parsed = _number_and_tail(text, r"заявк[аиу]")
    if parsed:
        request_id, tail = parsed
        action_info = _parse_action_tail(tail, _REQUEST_ACTIONS)
        if not action_info:
            await message.answer("Не понял действие. Пример: заявка 5 выдать 20")
            return True
        action, rest = action_info
        qty, reason = _qty_reason(rest)
        try:
            item = production_flow.request_action(scope, user_id, request_id, action, quantity=qty, reason=reason)
            await message.answer(
                f"Заявка №{request_id}: {_REQUEST_STATUS.get(str(item.get('status')), str(item.get('status')))}. "
                f"Выдано {_fmt_qty(item.get('issued_quantity'))}, получено {_fmt_qty(item.get('received_quantity'))} {item.get('unit') or 'шт'}."
            )
        except (ValueError, PermissionError) as exc:
            await message.answer(str(exc))
        return True

    m = re.match(r"^простой\s+(?:оборудовани[ея]|оборудование)\s+[№#]?(\d+)\s+(.+)$", text, re.IGNORECASE)
    if m:
        try:
            row = production_flow.open_downtime(scope, user_id, int(m.group(1)), reason=m.group(2).strip())
            await message.answer(f"Простой зарегистрирован. Номер простоя: №{row['id']}. Ответственные уведомлены.")
        except (ValueError, PermissionError) as exc:
            await message.answer(str(exc))
        return True

    m = re.match(r"^закрыть\s+простой\s+[№#]?(\d+)\s+(.+)$", text, re.IGNORECASE)
    if m:
        try:
            production_flow.close_downtime(scope, user_id, int(m.group(1)), m.group(2).strip())
            await message.answer(f"Простой №{m.group(1)} закрыт.")
        except (ValueError, PermissionError) as exc:
            await message.answer(str(exc))
        return True

    m = re.match(r"^(?:обслуживание|то)\s+оборудовани[ея]\s+[№#]?(\d+)(?:\s+(.+))?$", text, re.IGNORECASE)
    if m:
        try:
            row = production_flow.record_maintenance(scope, user_id, int(m.group(1)), note=str(m.group(2) or "").strip())
            next_due = str(row.get("next_due_at") or "")[:10]
            await message.answer("Обслуживание записано." + (f" Следующее: {next_due}." if next_due else ""))
        except (ValueError, PermissionError) as exc:
            await message.answer(str(exc))
        return True

    return False


@router.message(Command("tasks"))
async def tasks_command(message: Message) -> None:
    scope = _scope(message)
    user_id = _user(message)
    if _visible_workflow(scope, user_id):
        await message.answer(_tasks_text(scope, user_id))


@router.message(Command("requests"))
async def requests_command(message: Message) -> None:
    scope = _scope(message)
    user_id = _user(message)
    if _visible_workflow(scope, user_id):
        await message.answer(_requests_text(scope, user_id))


@router.message(Command("equipment"))
async def equipment_command(message: Message) -> None:
    scope = _scope(message)
    user_id = _user(message)
    if _visible_workflow(scope, user_id):
        await message.answer(_equipment_text(scope, user_id))


@router.message(Command("quality"))
async def quality_command(message: Message) -> None:
    scope=_scope(message);user_id=_user(message)
    if _visible_workflow(scope,user_id):await message.answer(_quality_text(scope,user_id))

@router.message(Command("replenishment"))
async def replenishment_command(message: Message) -> None:
    scope=_scope(message);user_id=_user(message)
    if _visible_workflow(scope,user_id):await message.answer(_replenishment_text(scope,user_id))

@router.message(Command("maintenance"))
async def maintenance_command(message: Message) -> None:
    scope=_scope(message);user_id=_user(message)
    if _visible_workflow(scope,user_id):await message.answer(_maintenance_plan_text(scope,user_id))
