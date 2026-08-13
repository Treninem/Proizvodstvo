from __future__ import annotations

import re
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ..services import repository as repo
from ..services import stock_risk
from ..services.matcher import confident_match
from ..services.normalize import normalize_key

router = Router()

_STATUS_WORDS = {
    "критические остатки", "красные флаги", "тревоги склада", "остатки тревога",
    "риск склада", "риски склада", "покажи тревоги", "аварийные остатки",
}
_CONFIG_PREFIXES = ("настроить тревогу", "настрой тревогу", "правило тревоги")
_EVENT_PREFIXES = (
    "событие", "форс мажор", "форс-мажор", "поломка", "больничный", "авария",
    "несчастный случай", "задержка поставки", "нехватка сотрудников", "простой",
    "карантин", "рост брака", "нет электричества", "нет интернета",
)


def _scope(message: Message) -> int:
    return repo.resolve_scope_chat_id(message.chat.id)


def _can_view(scope: int, user_id: int) -> bool:
    if repo.is_tenant_admin(scope, user_id):
        return True
    if repo.user_has_department_membership(scope, user_id):
        return True
    perms = repo.user_permissions_current_context(scope, user_id)
    return bool(perms.get("stock") or perms.get("reports") or perms.get("view"))


def _can_report_event(scope: int, user_id: int) -> bool:
    return repo.is_tenant_admin(scope, user_id) or repo.user_has_department_membership(scope, user_id) or repo.user_can_manage_current_context(scope, user_id)


def _fmt_status(scope: int, user_id: int) -> str:
    data = stock_risk.dashboard_for_user(scope, user_id)
    rules = data.get("rules") or []
    incidents = data.get("incidents") or []
    events = data.get("events") or []
    lines = ["Контроль критических остатков"]
    if not rules:
        lines.extend(["", "Правила ещё не настроены.", "Владелец может настроить их в Mini App или командой:", "настроить тревогу <позиция> расход 100 за смену предупреждение 10 тревога 5 авария 1"])
        return "\n".join(lines)
    if incidents:
        lines.append("")
        lines.append("Активные тревоги:")
        for item in incidents[:15]:
            icon = {"warning": "⚠️", "critical": "🔴", "emergency": "🚨"}.get(str(item.get("severity")), "•")
            reserve = item.get("reserve_shifts")
            reserve_text = "не рассчитан" if reserve is None else f"{float(reserve):.1f} смен"
            area = f" · {item.get('area_name')}" if item.get("area_name") else ""
            lines.append(f"{icon} {item.get('entity_name')}{area}: запас {reserve_text}")
    else:
        lines.extend(["", "Активных красных флагов нет."])
    unknown = [r for r in rules if r.get("severity") == "unknown"]
    if unknown:
        lines.append(f"⚪ Без нормы расхода: {len(unknown)}")
    if events:
        lines.append(f"Активных событий/форс-мажоров: {len(events)}")
    return "\n".join(lines)


def _extract_threshold(text: str, words: tuple[str, ...], default: float) -> float:
    key = text.lower().replace("ё", "е")
    for word in words:
        match = re.search(rf"{re.escape(word)}\s*(?:на|меньше|до|=|:)??\s*(\d+(?:[.,]\d+)?)", key)
        if match:
            return float(match.group(1).replace(",", "."))
    return default


def _find_area(scope: int, text: str):
    key = normalize_key(text)
    found = None
    for area in repo.list_areas(scope):
        if area.normalized and area.normalized in key:
            if found is None or len(area.normalized) > len(found.normalized):
                found = area
    return found


def _parse_rule_command(scope: int, user_id: int, text: str) -> tuple[bool, str]:
    if not repo.is_tenant_admin(scope, user_id):
        return True, "Настраивать тревоги может только владелец или полный администратор."
    key = text.lower().replace("ё", "е")
    consumption_match = re.search(r"(?:расход|потребление|норма)\s*(\d+(?:[.,]\d+)?)", key)
    if not consumption_match:
        return True, "Не вижу норму расхода. Пример: настроить тревогу Позиция расход 250 за смену предупреждение 10 тревога 5 авария 1"
    consumption = float(consumption_match.group(1).replace(",", "."))
    period_kind, _ = stock_risk.period_from_text(text)
    if period_kind not in {"shift", "day", "week"}:
        period_kind = "shift"
    warning = _extract_threshold(key, ("предупреждение", "желтый", "желтая", "жёлтый", "жёлтая"), 10)
    critical = _extract_threshold(key, ("тревога", "красный", "красная", "критический"), 5)
    emergency = _extract_threshold(key, ("авария", "аварийный", "срочно"), 1)
    cleaned = text
    for prefix in _CONFIG_PREFIXES:
        cleaned = re.sub(re.escape(prefix), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?:расход|потребление|норма).*", " ", cleaned, flags=re.IGNORECASE).strip(" :-")
    area = _find_area(scope, cleaned)
    if area:
        cleaned = re.sub(re.escape(area.name), " ", cleaned, flags=re.IGNORECASE).strip()
    match, variants = confident_match(scope, cleaned, allowed_types={"component", "product", "material", "stock_item"})
    if not match:
        options = ", ".join(v.name for v in variants[:5]) if variants else ""
        return True, "Не понял позицию." + (f" Возможно: {options}" if options else " Сначала создайте её в учёте.")
    ok, message, rule_id = stock_risk.save_rule(scope, user_id, {
        "entity_type": match.target_type,
        "entity_id": match.target_id,
        "area_id": area.id if area else None,
        "calculation_mode": "hybrid",
        "manual_consumption_qty": consumption,
        "manual_period": period_kind,
        "warning_shifts": warning,
        "critical_shifts": critical,
        "emergency_shifts": emergency,
        "notify_owner": True,
        "notify_system_admins": True,
        "notify_department_heads": True,
        "repeat_minutes": 180,
        "alert_on_stale": True,
        "alert_on_negative": True,
        "alert_on_anomaly": True,
    })
    if ok and rule_id:
        snapshot = stock_risk.evaluate_rule(rule_id)
        if snapshot:
            stock_risk.persist_snapshot(snapshot)
            message += "\n" + snapshot.message
    return True, message


def _event_type(text: str) -> str:
    key = normalize_key(text)
    mapping = [
        ("несчастн", "accident"), ("травм", "injury"), ("больнич", "sick_leave"),
        ("поломк", "machine_breakdown"), ("оснаст", "tooling_failure"),
        ("электр", "power_outage"), ("интернет", "internet_outage"),
        ("телеграм", "telegram_outage"), ("задержк сыр", "raw_material_delay"),
        ("задержк постав", "supplier_shortage"), ("транспорт", "transport_delay"),
        ("нехватк сотруд", "staff_shortage"), ("не выш", "no_show"),
        ("неполн смен", "partial_shift"), ("карантин", "quality_hold"),
        ("брак", "defect_spike"), ("поврежден", "stock_damage"),
        ("срочн заказ", "urgent_order"), ("спрос", "demand_spike"),
        ("упаков", "packaging_shortage"), ("маркиров", "label_shortage"),
        ("охлажден", "cooling_failure"), ("сжат воздух", "compressed_air_failure"),
        ("простой", "planned_maintenance"),
    ]
    for part, event in mapping:
        if part in key:
            return event
    return "force_majeure"


def _parse_event(scope: int, user_id: int, text: str) -> tuple[bool, str]:
    if not _can_report_event(scope, user_id):
        return True, "У вас нет доступа к производственному учёту."
    event_type = _event_type(text)
    catalog = stock_risk.EVENT_BY_KEY[event_type]
    key = text.lower().replace("ё", "е")
    severity = "warning"
    if any(x in key for x in ("авария", "срочно", "опасно", "остановлено полностью", "несчастный случай")):
        severity = "emergency"
    elif any(x in key for x in ("критично", "серьезно", "остановлено", "поломка")):
        severity = "critical"
    impact_kind = catalog["impact_kind"]
    impact_value = 0.0
    unavailable = 0.0
    number = re.search(r"(\d+(?:[.,]\d+)?)\s*(%|процент|дн|дня|дней|смен|кг|г|шт)?", key)
    if number:
        value = float(number.group(1).replace(",", "."))
        unit = number.group(2) or ""
        if impact_kind == "lead_time_days":
            impact_value = value
        elif impact_kind == "demand_multiplier":
            impact_value = 1 + value / 100 if "%" in unit or "процент" in unit else max(1.0, value)
        elif impact_kind == "capacity_loss":
            impact_value = min(100.0, value if "%" in unit or "процент" in unit else 100.0)
        elif impact_kind == "unavailable_stock":
            unavailable = value
    area = _find_area(scope, text)
    entity = None
    entity_match, _ = confident_match(scope, text, allowed_types={"component", "product", "material", "stock_item"})
    if entity_match and entity_match.score >= 0.72:
        visible = repo.visible_entity_ids_for_user(scope, user_id)
        if visible is None or int(entity_match.target_id) in {int(x) for x in visible}:
            entity = entity_match
    ok, message, event_id = stock_risk.save_event(scope, user_id, {
        "event_type": event_type,
        "title": catalog["label"],
        "area_id": area.id if area else None,
        "entity_id": entity.target_id if entity else None,
        "severity": severity,
        "impact_kind": impact_kind,
        "impact_value": impact_value,
        "unavailable_quantity": unavailable,
        "starts_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": text,
    })
    if ok:
        return True, f"{message} Номер события: {event_id}. Ответственные уведомлены. После устранения напишите: закрыть событие {event_id}"
    return True, message


async def try_handle_risk_command(message: Message) -> bool:
    text = (message.text or "").strip()
    key = normalize_key(text)
    user_id = message.from_user.id if message.from_user else 0
    scope = _scope(message)
    if key in _STATUS_WORDS or key in {"risks", "risk"}:
        if not _can_view(scope, user_id):
            return False
        await message.answer(_fmt_status(scope, user_id))
        return True
    if any(key.startswith(normalize_key(prefix)) for prefix in _CONFIG_PREFIXES):
        handled, response = _parse_rule_command(scope, user_id, text)
        if handled:
            await message.answer(response)
        return handled
    close = re.match(r"^(?:закрыть|устранить|решено)\s+событие\s+(\d+)", key)
    if close:
        if not _can_report_event(scope, user_id):
            await message.answer("Нет доступа.")
            return True
        ok = stock_risk.resolve_event(scope, int(close.group(1)), user_id)
        await message.answer("Событие закрыто, риски пересчитаны." if ok else "Активное событие не найдено.")
        return True
    if any(key.startswith(normalize_key(prefix)) for prefix in _EVENT_PREFIXES):
        handled, response = _parse_event(scope, user_id, text)
        if handled:
            await message.answer(response)
        return handled
    return False


@router.message(Command("risks"))
async def risks_command(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    scope = _scope(message)
    if not _can_view(scope, user_id):
        return
    await message.answer(_fmt_status(scope, user_id))
