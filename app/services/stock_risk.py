from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .. import db
from ..config import settings
from . import repository as repo
from .normalize import format_amount

SEVERITY_ORDER = {"ok": 0, "unknown": 1, "warning": 2, "critical": 3, "emergency": 4}
PERIODS = {"shift", "day", "week", "custom", "instant"}
CALCULATION_MODES = {"manual", "observed", "historical", "planned", "hybrid"}
EVENT_STATUSES = {"active", "resolved", "cancelled"}

# 40 универсальных событий. Названия производства не зашиты: владелец выбирает тип
# и пишет своё название/описание. impact_kind задаёт влияние на расчёт риска.
EVENT_CATALOG: list[dict[str, Any]] = [
    {"key": "machine_breakdown", "label": "Поломка оборудования", "impact_kind": "capacity_loss"},
    {"key": "tooling_failure", "label": "Поломка оснастки", "impact_kind": "capacity_loss"},
    {"key": "planned_maintenance", "label": "Плановое обслуживание", "impact_kind": "capacity_loss"},
    {"key": "power_outage", "label": "Отключение электричества", "impact_kind": "capacity_loss"},
    {"key": "voltage_instability", "label": "Нестабильное напряжение", "impact_kind": "capacity_loss"},
    {"key": "compressed_air_failure", "label": "Отказ сжатого воздуха", "impact_kind": "capacity_loss"},
    {"key": "cooling_failure", "label": "Отказ охлаждения", "impact_kind": "capacity_loss"},
    {"key": "heating_failure", "label": "Отказ нагрева", "impact_kind": "capacity_loss"},
    {"key": "raw_material_delay", "label": "Задержка сырья", "impact_kind": "lead_time_days"},
    {"key": "supplier_shortage", "label": "Дефицит у поставщика", "impact_kind": "lead_time_days"},
    {"key": "transport_delay", "label": "Задержка транспорта", "impact_kind": "lead_time_days"},
    {"key": "customs_delay", "label": "Задержка на таможне", "impact_kind": "lead_time_days"},
    {"key": "warehouse_block", "label": "Недоступен склад", "impact_kind": "unavailable_stock"},
    {"key": "inventory_discrepancy", "label": "Расхождение инвентаризации", "impact_kind": "unavailable_stock"},
    {"key": "stock_damage", "label": "Повреждение запаса", "impact_kind": "unavailable_stock"},
    {"key": "contamination", "label": "Загрязнение материала", "impact_kind": "unavailable_stock"},
    {"key": "moisture_issue", "label": "Проблема с влажностью", "impact_kind": "unavailable_stock"},
    {"key": "wrong_material", "label": "Неверный материал", "impact_kind": "unavailable_stock"},
    {"key": "quality_hold", "label": "Карантин качества", "impact_kind": "unavailable_stock"},
    {"key": "defect_spike", "label": "Рост брака", "impact_kind": "demand_multiplier"},
    {"key": "rework", "label": "Повторная переработка", "impact_kind": "demand_multiplier"},
    {"key": "accident", "label": "Несчастный случай", "impact_kind": "capacity_loss"},
    {"key": "injury", "label": "Травма сотрудника", "impact_kind": "capacity_loss"},
    {"key": "sick_leave", "label": "Больничный", "impact_kind": "capacity_loss"},
    {"key": "staff_shortage", "label": "Нехватка сотрудников", "impact_kind": "capacity_loss"},
    {"key": "no_show", "label": "Сотрудник не вышел", "impact_kind": "capacity_loss"},
    {"key": "partial_shift", "label": "Неполная смена", "impact_kind": "capacity_loss"},
    {"key": "overtime_limit", "label": "Ограничение переработки", "impact_kind": "capacity_loss"},
    {"key": "training", "label": "Обучение персонала", "impact_kind": "capacity_loss"},
    {"key": "demand_spike", "label": "Резкий рост спроса", "impact_kind": "demand_multiplier"},
    {"key": "urgent_order", "label": "Срочный заказ", "impact_kind": "demand_multiplier"},
    {"key": "order_cancellation", "label": "Отмена крупного заказа", "impact_kind": "demand_multiplier"},
    {"key": "shipment_delay", "label": "Задержка отгрузки", "impact_kind": "info"},
    {"key": "packaging_shortage", "label": "Нехватка упаковки", "impact_kind": "unavailable_stock"},
    {"key": "label_shortage", "label": "Нехватка маркировки", "impact_kind": "unavailable_stock"},
    {"key": "it_outage", "label": "Сбой информационной системы", "impact_kind": "info"},
    {"key": "internet_outage", "label": "Нет интернета", "impact_kind": "info"},
    {"key": "telegram_outage", "label": "Недоступен Telegram", "impact_kind": "info"},
    {"key": "database_recovery", "label": "Восстановление базы", "impact_kind": "info"},
    {"key": "force_majeure", "label": "Прочий форс-мажор", "impact_kind": "info"},
]
EVENT_BY_KEY = {item["key"]: item for item in EVENT_CATALOG}


@dataclass
class RiskSnapshot:
    rule_id: int
    severity: str
    reason_code: str
    stock_quantity: float
    effective_stock: float
    consumption_per_shift: float
    reserve_shifts: float | None
    warning_shifts: float
    critical_shifts: float
    emergency_shifts: float
    latest_data_at: str | None
    samples: int
    message: str
    output_capacity: float | None = None


def _scope(chat_id: int) -> int:
    return repo.resolve_scope_chat_id(chat_id)


def _json_ids(value: str | list[int] | None) -> list[int]:
    if isinstance(value, list):
        raw = value
    else:
        try:
            raw = json.loads(value or "[]")
        except Exception:
            raw = []
    result: list[int] = []
    for item in raw:
        try:
            uid = int(item)
        except (TypeError, ValueError):
            continue
        if uid > 0 and uid not in result:
            result.append(uid)
    return result


def period_from_text(text: str) -> tuple[str, float]:
    key = (text or "").lower().replace("ё", "е")
    match = re.search(r"за\s+(\d+(?:[.,]\d+)?)\s*(смен|смены|смену|смен)", key)
    if match:
        return "shift", max(0.01, float(match.group(1).replace(",", ".")))
    match = re.search(r"за\s+(\d+(?:[.,]\d+)?)\s*(дн|дня|дней|день|суток|сутки)", key)
    if match:
        return "day", max(0.01, float(match.group(1).replace(",", ".")))
    match = re.search(r"за\s+(\d+(?:[.,]\d+)?)\s*(недел|недели|недель)", key)
    if match:
        return "week", max(0.01, float(match.group(1).replace(",", ".")))
    if any(x in key for x in ("за смену", "сменный расход", "в смену")):
        return "shift", 1.0
    if any(x in key for x in ("за неделю", "недельный расход", "в неделю")):
        return "week", 1.0
    if any(x in key for x in ("за день", "суточный расход", "в день")):
        return "day", 1.0
    return "instant", 1.0


def _to_per_shift(quantity: float, period_kind: str, period_count: float, shifts_per_day: float, work_days_per_week: float) -> float:
    qty = max(0.0, float(quantity or 0))
    count = max(0.01, float(period_count or 1))
    shifts_day = max(0.01, float(shifts_per_day or 1))
    work_days = max(0.01, float(work_days_per_week or 5))
    if period_kind == "shift":
        return qty / count
    if period_kind == "day":
        return qty / (count * shifts_day)
    if period_kind == "week":
        return qty / (count * work_days * shifts_day)
    return qty


def list_rules(chat_id: int, *, include_disabled: bool = True) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    where = "" if include_disabled else "AND r.is_enabled=1"
    rows = db.fetchall(
        f"""
        SELECT r.*,e.name AS entity_name,e.default_unit,a.name AS area_name,yo.name AS yield_output_name,po.name AS planned_output_name
        FROM stock_alert_rules r
        JOIN entities e ON e.id=r.entity_id AND e.chat_id=r.chat_id AND e.is_archived=0
        LEFT JOIN areas a ON a.id=r.area_id AND a.chat_id=r.chat_id
        LEFT JOIN entities yo ON yo.id=r.yield_output_entity_id AND yo.chat_id=r.chat_id
        LEFT JOIN entities po ON po.id=r.planned_output_entity_id AND po.chat_id=r.chat_id
        WHERE r.chat_id=? {where}
        ORDER BY CASE WHEN r.is_enabled=1 THEN 0 ELSE 1 END,e.name,a.name
        """,
        (scope,),
    )
    result = []
    for row in rows:
        item = dict(row)
        item["is_enabled"] = bool(item["is_enabled"])
        item["notify_user_ids"] = _json_ids(item.get("notify_user_ids_json"))
        result.append(item)
    return result


def get_rule(rule_id: int) -> dict[str, Any] | None:
    row = db.fetchone(
        """
        SELECT r.*,e.name AS entity_name,e.default_unit,a.name AS area_name,yo.name AS yield_output_name,po.name AS planned_output_name
        FROM stock_alert_rules r JOIN entities e ON e.id=r.entity_id AND e.chat_id=r.chat_id
        LEFT JOIN areas a ON a.id=r.area_id AND a.chat_id=r.chat_id LEFT JOIN entities yo ON yo.id=r.yield_output_entity_id AND yo.chat_id=r.chat_id
        LEFT JOIN entities po ON po.id=r.planned_output_entity_id AND po.chat_id=r.chat_id
        WHERE r.id=?
        """,
        (int(rule_id),),
    )
    if not row:
        return None
    item = dict(row)
    item["notify_user_ids"] = _json_ids(item.get("notify_user_ids_json"))
    return item


def save_rule(chat_id: int, actor_user_id: int, values: dict[str, Any]) -> tuple[bool, str, int | None]:
    scope = _scope(chat_id)
    if not repo.is_tenant_admin(scope, int(actor_user_id)):
        return False, "Настраивать критические остатки может только владелец или полный администратор.", None
    entity_id = int(values.get("entity_id") or 0)
    entity_type = str(values.get("entity_type") or "")
    entity = repo.get_entity(entity_id)
    if not entity or entity.chat_id != scope or entity.entity_type != entity_type:
        return False, "Позиция не найдена.", None
    area_id = int(values["area_id"]) if values.get("area_id") not in (None, "", 0, "0") else None
    if area_id:
        area = repo.get_area(area_id)
        if not area or area.chat_id != scope:
            return False, "Площадка не найдена.", None
    mode = str(values.get("calculation_mode") or "hybrid")
    if mode not in CALCULATION_MODES:
        mode = "hybrid"
    manual_period = str(values.get("manual_period") or "shift")
    if manual_period not in {"shift", "day", "week"}:
        manual_period = "shift"
    warning = max(0.0, float(values.get("warning_shifts") or 0))
    critical = max(0.0, float(values.get("critical_shifts") or 0))
    emergency = max(0.0, float(values.get("emergency_shifts") or 0))
    if not (warning >= critical >= emergency):
        return False, "Пороги должны идти по убыванию: предупреждение ≥ тревога ≥ авария.", None
    planned_output_entity_id = int(values["planned_output_entity_id"]) if values.get("planned_output_entity_id") not in (None, "", 0, "0") else None
    if planned_output_entity_id:
        planned_entity = repo.get_entity(planned_output_entity_id)
        if not planned_entity or int(planned_entity.chat_id) != int(scope):
            return False, "Плановая выходная позиция не найдена.", None
    planned_output_period = str(values.get("planned_output_period") or "shift")
    if planned_output_period not in {"shift", "day", "week"}:
        planned_output_period = "shift"
    rule_id = int(values.get("rule_id") or 0)
    notify_ids = _json_ids(values.get("notify_user_ids"))
    payload = (
        scope, entity_type, entity_id, area_id, str(values.get("name") or "")[:120],
        int(bool(values.get("is_enabled", True))), mode,
        max(0.0, float(values.get("manual_consumption_qty") or 0)), manual_period,
        max(0.1, float(values.get("shifts_per_day") or 1)), max(1.0, float(values.get("work_days_per_week") or 5)),
        warning, critical, emergency,
        float(values["absolute_warning_qty"]) if values.get("absolute_warning_qty") not in (None, "") else None,
        float(values["absolute_critical_qty"]) if values.get("absolute_critical_qty") not in (None, "") else None,
        max(0.0, float(values.get("safety_buffer_qty") or 0)),
        max(1, min(int(values.get("learning_window_days") or 28), 365)),
        max(1, min(int(values.get("minimum_samples") or 2), 100)),
        max(1, min(int(values.get("stale_after_hours") or 168), 8760)),
        max(1.1, min(float(values.get("anomaly_multiplier") or 2), 20)),
        max(0.01, min(float(values.get("demand_multiplier") or 1), 20)),
        int(values["yield_output_entity_id"]) if values.get("yield_output_entity_id") not in (None, "", 0, "0") else None,
        max(0.0, float(values.get("yield_input_qty") or 0)), max(0.0, float(values.get("yield_output_qty") or 0)),
        planned_output_entity_id, max(0.0, float(values.get("planned_output_qty") or 0)), planned_output_period,
        int(bool(values.get("notify_owner", True))), int(bool(values.get("notify_system_admins", True))),
        int(bool(values.get("notify_department_heads", True))), int(bool(values.get("notify_work_chat", False))),
        json.dumps(notify_ids, ensure_ascii=False),
        max(5, min(int(values.get("repeat_minutes") or 180), 10080)),
        int(bool(values.get("alert_on_stale", True))), int(bool(values.get("alert_on_negative", True))),
        int(bool(values.get("alert_on_anomaly", True))), int(actor_user_id),
    )
    with db.connect() as conn:
        if rule_id:
            exists = conn.execute("SELECT id FROM stock_alert_rules WHERE id=? AND chat_id=?", (rule_id, scope)).fetchone()
            if not exists:
                return False, "Правило не найдено.", None
            conn.execute(
                """
                UPDATE stock_alert_rules SET entity_type=?,entity_id=?,area_id=?,name=?,is_enabled=?,calculation_mode=?,
                  manual_consumption_qty=?,manual_period=?,shifts_per_day=?,work_days_per_week=?,warning_shifts=?,critical_shifts=?,
                  emergency_shifts=?,absolute_warning_qty=?,absolute_critical_qty=?,safety_buffer_qty=?,learning_window_days=?,
                  minimum_samples=?,stale_after_hours=?,anomaly_multiplier=?,demand_multiplier=?,yield_output_entity_id=?,
                  yield_input_qty=?,yield_output_qty=?,planned_output_entity_id=?,planned_output_qty=?,planned_output_period=?,
                  notify_owner=?,notify_system_admins=?,notify_department_heads=?,notify_work_chat=?,
                  notify_user_ids_json=?,repeat_minutes=?,alert_on_stale=?,alert_on_negative=?,alert_on_anomaly=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND chat_id=?
                """,
                payload[1:-1] + (rule_id, scope),
            )
            conn.commit()
            return True, "Правило тревоги обновлено.", rule_id
        try:
            cur = conn.execute(
                """
                INSERT INTO stock_alert_rules(
                  chat_id,entity_type,entity_id,area_id,name,is_enabled,calculation_mode,manual_consumption_qty,manual_period,
                  shifts_per_day,work_days_per_week,warning_shifts,critical_shifts,emergency_shifts,absolute_warning_qty,
                  absolute_critical_qty,safety_buffer_qty,learning_window_days,minimum_samples,stale_after_hours,anomaly_multiplier,
                  demand_multiplier,yield_output_entity_id,yield_input_qty,yield_output_qty,planned_output_entity_id,planned_output_qty,
                  planned_output_period,notify_owner,notify_system_admins,notify_department_heads,notify_work_chat,notify_user_ids_json,repeat_minutes,
                  alert_on_stale,alert_on_negative,alert_on_anomaly,created_by
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                payload,
            )
            conn.commit()
            return True, "Правило тревоги создано.", int(cur.lastrowid)
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                return False, "Для этой позиции и площадки правило уже создано.", None
            raise


def delete_rule(chat_id: int, rule_id: int) -> bool:
    scope = _scope(chat_id)
    row = db.fetchone("SELECT id FROM stock_alert_rules WHERE id=? AND chat_id=?", (int(rule_id), scope))
    if not row:
        return False
    db.execute("DELETE FROM stock_alert_rules WHERE id=?", (int(rule_id),))
    return True


def record_observation(
    chat_id: int, entity_type: str, entity_id: int, area_id: int | None, user_id: int,
    source: str, observation_type: str, quantity: float, unit: str,
    period_kind: str = "instant", period_count: float = 1, note: str = "",
    operation_id: int | None = None, dedupe_key: str | None = None,
) -> int:
    scope = _scope(chat_id)
    if period_kind not in PERIODS:
        period_kind = "instant"
    with db.connect() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO stock_observations(chat_id,entity_type,entity_id,area_id,user_id,source,observation_type,
                  quantity,unit,period_kind,period_count,note,operation_id,dedupe_key)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (scope, entity_type, int(entity_id), area_id, int(user_id), source[:20], observation_type[:30],
                 float(quantity), unit or "шт", period_kind, max(0.01, float(period_count or 1)), note[:500], operation_id, dedupe_key),
            )
            conn.commit()
            return int(cur.lastrowid)
        except Exception as exc:
            if dedupe_key and "UNIQUE" in str(exc).upper():
                row = conn.execute("SELECT id FROM stock_observations WHERE dedupe_key=?", (dedupe_key,)).fetchone()
                return int(row["id"]) if row else 0
            raise


def record_operation_observation(chat_id: int, user_id: int, op: dict[str, Any], operation_id: int, raw_text: str, source: str = "bot") -> None:
    op_type = str(op.get("operation_type") or "")
    entity_id = int(op.get("entity_id") or 0)
    entity_type = str(op.get("entity_type") or "")
    if not entity_id or not entity_type:
        return
    qty = float(op.get("quantity") or 0)
    if op_type == "inventory_adjust" and op.get("fact_quantity") is not None:
        record_observation(chat_id, entity_type, entity_id, op.get("area_id"), user_id, source, "balance",
                           float(op.get("fact_quantity") or 0), str(op.get("unit") or "шт"), "instant", 1,
                           raw_text, operation_id, f"operation:{operation_id}:balance")
    elif op_type in {"material_out", "stock_out", "write_off", "shipment", "shipment_client", "shipment_fulfillment"} and qty > 0:
        period_kind, period_count = period_from_text(raw_text)
        record_observation(chat_id, entity_type, entity_id, op.get("area_id"), user_id, source, "consumption",
                           qty, str(op.get("unit") or "шт"), period_kind, period_count,
                           raw_text, operation_id, f"operation:{operation_id}:consumption")


def list_observations(chat_id: int, rule_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    params: list[Any] = [scope]
    where = ["o.chat_id=?"]
    if rule_id:
        rule = get_rule(rule_id)
        if not rule or int(rule["chat_id"]) != scope:
            return []
        where.append("o.entity_id=?")
        params.append(int(rule["entity_id"]))
        if rule.get("area_id") is not None:
            where.append("o.area_id=?")
            params.append(int(rule["area_id"]))
    params.append(max(1, min(int(limit), 500)))
    rows = db.fetchall(
        f"""
        SELECT o.*,e.name AS entity_name,a.name AS area_name
        FROM stock_observations o JOIN entities e ON e.id=o.entity_id AND e.chat_id=o.chat_id LEFT JOIN areas a ON a.id=o.area_id AND a.chat_id=o.chat_id
        WHERE {' AND '.join(where)} ORDER BY o.id DESC LIMIT ?
        """,
        params,
    )
    return [dict(r) for r in rows]



def _membership_context(chat_id: int, user_id: int) -> tuple[list[dict[str, Any]], set[int], set[int], int]:
    memberships = repo.user_department_memberships(chat_id, user_id)
    department_ids = {int(item["department_id"]) for item in memberships}
    head_ids = {int(item["department_id"]) for item in memberships if int(item.get("role_level") or 0) >= 50}
    max_level = max((int(item.get("role_level") or 0) for item in memberships), default=0)
    return memberships, department_ids, head_ids, max_level


def _validate_event_scope(scope: int, area_id: int | None, department_id: int | None, entity_id: int | None) -> str | None:
    if area_id:
        area = repo.get_area(area_id)
        if not area or int(area.chat_id) != int(scope):
            return "Площадка не найдена."
    if department_id:
        department = db.fetchone("SELECT id FROM departments WHERE id=? AND chat_id=? AND is_archived=0", (int(department_id), int(scope)))
        if not department:
            return "Отдел не найден."
    if entity_id:
        entity = repo.get_entity(entity_id)
        if not entity or int(entity.chat_id) != int(scope):
            return "Позиция не найдена."
    return None


def _event_accessible_to_user(event: dict[str, Any], chat_id: int, user_id: int, *, manage: bool = False) -> bool:
    if repo.is_tenant_admin(chat_id, user_id):
        return True
    memberships, department_ids, head_ids, _ = _membership_context(chat_id, user_id)
    if not memberships:
        return False
    if int(event.get("created_by") or 0) == int(user_id):
        return True
    department_id = int(event.get("department_id") or 0)
    entity_id = int(event.get("entity_id") or 0)
    if manage:
        if department_id:
            return department_id in head_ids
        if entity_id and head_ids:
            visible = repo.visible_entity_ids_for_user(chat_id, user_id) or set()
            return entity_id in {int(x) for x in visible}
        return False
    if department_id and department_id in department_ids:
        return True
    if entity_id:
        visible = repo.visible_entity_ids_for_user(chat_id, user_id) or set()
        return entity_id in {int(x) for x in visible}
    return False


def can_resolve_event(chat_id: int, event_id: int, user_id: int) -> bool:
    scope = _scope(chat_id)
    row = db.fetchone("SELECT * FROM operational_events WHERE id=? AND chat_id=?", (int(event_id), scope))
    return bool(row and _event_accessible_to_user(dict(row), scope, int(user_id), manage=True))


def _sanitize_event_values(scope: int, actor_user_id: int, values: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    clean = dict(values)
    area_id = int(clean["area_id"]) if clean.get("area_id") not in (None, "", 0, "0") else None
    department_id = int(clean["department_id"]) if clean.get("department_id") not in (None, "", 0, "0") else None
    entity_id = int(clean["entity_id"]) if clean.get("entity_id") not in (None, "", 0, "0") else None
    error = _validate_event_scope(scope, area_id, department_id, entity_id)
    if error:
        return False, error, clean
    clean["area_id"], clean["department_id"], clean["entity_id"] = area_id, department_id, entity_id
    if repo.is_tenant_admin(scope, actor_user_id):
        return True, "", clean
    memberships, department_ids, head_ids, max_level = _membership_context(scope, actor_user_id)
    if not memberships:
        return False, "У вас нет доступа к производственному учёту.", clean
    if department_id and department_id not in department_ids:
        return False, "Нет доступа к выбранному отделу.", clean
    visible = repo.visible_entity_ids_for_user(scope, actor_user_id) or set()
    visible_ids = {int(x) for x in visible}
    if entity_id and entity_id not in visible_ids:
        return False, "Нет доступа к выбранной позиции.", clean
    can_set_effect = bool(
        max_level >= 50
        and ((department_id and department_id in head_ids) or (entity_id and bool(head_ids)))
    )
    if not can_set_effect:
        # Обычный сотрудник сообщает факт. Он не может сам изменить прогноз склада.
        clean["impact_kind"] = "info"
        clean["impact_value"] = 0.0
        clean["unavailable_quantity"] = 0.0
    return True, "", clean

def save_event(chat_id: int, actor_user_id: int, values: dict[str, Any]) -> tuple[bool, str, int | None]:
    scope = _scope(chat_id)
    ok, error, values = _sanitize_event_values(scope, int(actor_user_id), values)
    if not ok:
        return False, error, None
    event_type = str(values.get("event_type") or "force_majeure")
    catalog = EVENT_BY_KEY.get(event_type, EVENT_BY_KEY["force_majeure"])
    impact_kind = str(values.get("impact_kind") or catalog["impact_kind"])
    if impact_kind not in {"info", "capacity_loss", "lead_time_days", "unavailable_stock", "demand_multiplier"}:
        impact_kind = "info"
    severity = str(values.get("severity") or "warning")
    if severity not in {"warning", "critical", "emergency"}:
        severity = "warning"
    event_id = int(values.get("event_id") or 0)
    title = str(values.get("title") or catalog["label"]).strip()[:180]
    if not title:
        return False, "Укажите название события.", None
    area_id = int(values["area_id"]) if values.get("area_id") not in (None, "", 0, "0") else None
    department_id = int(values["department_id"]) if values.get("department_id") not in (None, "", 0, "0") else None
    entity_id = int(values["entity_id"]) if values.get("entity_id") not in (None, "", 0, "0") else None
    starts_at = str(values.get("starts_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    ends_at = str(values.get("ends_at") or "") or None
    payload = (scope, event_type, title, area_id, department_id, entity_id, severity, impact_kind,
               float(values.get("impact_value") or 0), max(0.0, float(values.get("unavailable_quantity") or 0)),
               starts_at, ends_at, str(values.get("status") or "active"), str(values.get("note") or "")[:1000], int(actor_user_id))
    with db.connect() as conn:
        if event_id:
            exists = conn.execute("SELECT * FROM operational_events WHERE id=? AND chat_id=?", (event_id, scope)).fetchone()
            if not exists:
                return False, "Событие не найдено.", None
            if not _event_accessible_to_user(dict(exists), scope, int(actor_user_id), manage=True):
                return False, "Изменять это событие может только его автор, руководитель соответствующего отдела или администратор.", None
            conn.execute(
                """UPDATE operational_events SET event_type=?,title=?,area_id=?,department_id=?,entity_id=?,severity=?,impact_kind=?,
                   impact_value=?,unavailable_quantity=?,starts_at=?,ends_at=?,status=?,note=?,updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND chat_id=?""",
                payload[1:-1] + (event_id, scope),
            )
            conn.commit()
            notify_operational_event(scope, event_id)
            evaluate_all(scope)
            return True, "Событие обновлено.", event_id
        cur = conn.execute(
            """INSERT INTO operational_events(chat_id,event_type,title,area_id,department_id,entity_id,severity,impact_kind,
               impact_value,unavailable_quantity,starts_at,ends_at,status,note,created_by)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload,
        )
        conn.commit()
        event_id = int(cur.lastrowid)
    notify_operational_event(scope, event_id)
    evaluate_all(scope)
    return True, "Событие зарегистрировано.", event_id



def notify_operational_event(chat_id: int, event_id: int) -> int:
    scope = _scope(chat_id)
    row = db.fetchone(
        """SELECT ev.*,a.name AS area_name,d.name AS department_name,e.name AS entity_name
           FROM operational_events ev LEFT JOIN areas a ON a.id=ev.area_id
           LEFT JOIN departments d ON d.id=ev.department_id LEFT JOIN entities e ON e.id=ev.entity_id
           WHERE ev.id=? AND ev.chat_id=?""", (int(event_id), scope),
    )
    if not row:
        return 0
    event = dict(row)
    recipients: set[int] = set(repo.tenant_admin_user_ids(scope))
    if event.get("department_id"):
        heads = db.fetchall("SELECT user_id FROM department_members WHERE department_id=? AND is_active=1 AND role_level>=50", (int(event["department_id"]),))
        recipients.update(int(x["user_id"]) for x in heads)
    elif event.get("entity_id"):
        heads = db.fetchall(
            """SELECT DISTINCT dm.user_id FROM department_members dm
               JOIN department_entity_rules der ON der.department_id=dm.department_id
               JOIN departments d ON d.id=dm.department_id AND d.chat_id=? AND d.is_archived=0
               WHERE der.entity_id=? AND dm.is_active=1 AND dm.role_level>=50""",
            (scope, int(event["entity_id"])),
        )
        recipients.update(int(x["user_id"]) for x in heads)
    details = []
    if event.get("area_name"):
        details.append(str(event["area_name"]))
    if event.get("department_name"):
        details.append(str(event["department_name"]))
    if event.get("entity_name"):
        details.append(str(event["entity_name"]))
    message = (" · ".join(details) + ("\n" if details else "")) + str(event.get("note") or "Без подробностей")
    priority = "urgent" if event.get("severity") == "emergency" else "high"
    count = 0
    for uid in recipients:
        if uid <= 0:
            continue
        repo.create_inbox_item(scope, uid, "operational_event", f"Событие: {event.get('title')}", message,
                               "operational_event", int(event_id), deduplicate=False, priority=priority, force=True)
        count += 1
    return count

def resolve_event(chat_id: int, event_id: int, actor_user_id: int) -> bool:
    scope = _scope(chat_id)
    row = db.fetchone("SELECT * FROM operational_events WHERE id=? AND chat_id=? AND status='active'", (int(event_id), scope))
    if not row or not _event_accessible_to_user(dict(row), scope, int(actor_user_id), manage=True):
        return False
    db.execute("UPDATE operational_events SET status='resolved',resolved_by=?,resolved_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(actor_user_id), int(event_id)))
    db.execute("UPDATE inbox_items SET status='resolved',resolved_at=CURRENT_TIMESTAMP WHERE related_type='operational_event' AND related_id=? AND status!='resolved'", (int(event_id),))
    evaluate_all(scope)
    return True


def list_events(chat_id: int, include_resolved: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    status = "" if include_resolved else "AND ev.status='active'"
    rows = db.fetchall(
        f"""
        SELECT ev.*,a.name AS area_name,d.name AS department_name,e.name AS entity_name
        FROM operational_events ev LEFT JOIN areas a ON a.id=ev.area_id
        LEFT JOIN departments d ON d.id=ev.department_id LEFT JOIN entities e ON e.id=ev.entity_id
        WHERE ev.chat_id=? {status} ORDER BY CASE ev.severity WHEN 'emergency' THEN 0 WHEN 'critical' THEN 1 ELSE 2 END,ev.id DESC LIMIT ?
        """, (scope, max(1, min(int(limit), 500))),
    )
    return [dict(r) for r in rows]


def _stock_quantity(rule: dict[str, Any]) -> float:
    if rule.get("area_id") is not None:
        return repo.inventory_quantity(int(rule["chat_id"]), str(rule["entity_type"]), int(rule["entity_id"]), str(rule.get("default_unit") or "шт"), int(rule["area_id"]))
    row = db.fetchone(
        "SELECT COALESCE(SUM(quantity),0) AS qty FROM inventory WHERE chat_id=? AND entity_type=? AND entity_id=? AND unit=?",
        (int(rule["chat_id"]), str(rule["entity_type"]), int(rule["entity_id"]), str(rule.get("default_unit") or "шт")),
    )
    return float(row["qty"] or 0) if row else 0.0


def _active_event_effects(rule: dict[str, Any], now: datetime) -> dict[str, float]:
    where = ["chat_id=?", "status='active'", "starts_at<=?", "(ends_at IS NULL OR ends_at='' OR ends_at>=?)"]
    params: list[Any] = [int(rule["chat_id"]), now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S")]
    if rule.get("area_id") is not None:
        where.append("(area_id IS NULL OR area_id=?)")
        params.append(int(rule["area_id"]))
    if rule.get("entity_id") is not None:
        where.append("(entity_id IS NULL OR entity_id=?)")
        params.append(int(rule["entity_id"]))
    rows = db.fetchall(f"SELECT * FROM operational_events WHERE {' AND '.join(where)}", params)
    result = {"unavailable": 0.0, "demand_multiplier": 1.0, "lead_time_days": 0.0, "capacity_loss": 0.0}
    for row in rows:
        kind = str(row["impact_kind"] or "info")
        value = float(row["impact_value"] or 0)
        if kind == "unavailable_stock":
            result["unavailable"] += max(float(row["unavailable_quantity"] or 0), value)
        elif kind == "demand_multiplier":
            result["demand_multiplier"] *= max(0.0, value or 1.0)
        elif kind == "lead_time_days":
            result["lead_time_days"] = max(result["lead_time_days"], max(0.0, value))
        elif kind == "capacity_loss":
            result["capacity_loss"] = min(100.0, result["capacity_loss"] + max(0.0, value))
    return result


def _observed_rate(rule: dict[str, Any], since: datetime) -> tuple[float, int, str | None, float | None]:
    params: list[Any] = [int(rule["chat_id"]), int(rule["entity_id"]), since.strftime("%Y-%m-%d %H:%M:%S")]
    area = ""
    if rule.get("area_id") is not None:
        area = "AND area_id=?"
        params.append(int(rule["area_id"]))
    rows = db.fetchall(
        f"""SELECT quantity,period_kind,period_count,created_at FROM stock_observations
             WHERE chat_id=? AND entity_id=? AND observation_type='consumption' AND created_at>=? {area}
             ORDER BY id DESC""", params,
    )
    rates = [_to_per_shift(float(r["quantity"] or 0), str(r["period_kind"] or "instant"), float(r["period_count"] or 1), float(rule["shifts_per_day"]), float(rule["work_days_per_week"])) for r in rows]
    latest = str(rows[0]["created_at"]) if rows else None
    latest_rate = rates[0] if rates else None
    if not rates:
        return 0.0, 0, latest, latest_rate
    # Усечённое среднее снижает влияние одиночной ошибки ввода.
    ordered = sorted(rates)
    if len(ordered) >= 5:
        trim = max(1, len(ordered) // 10)
        ordered = ordered[trim:-trim] or ordered
    return sum(ordered) / len(ordered), len(rates), latest, latest_rate


def _historical_rate(rule: dict[str, Any], since: datetime, now: datetime) -> float:
    where_area = ""
    params: list[Any] = [int(rule["chat_id"]), int(rule["entity_id"]), since.strftime("%Y-%m-%d %H:%M:%S")]
    if rule.get("area_id") is not None:
        where_area = "AND area_id=?"
        params.append(int(rule["area_id"]))
    row = db.fetchone(
        f"""SELECT COALESCE(SUM(ABS(quantity)),0) AS qty FROM operations
             WHERE chat_id=? AND entity_id=? AND created_at>=?
               AND operation_type IN ('material_out','stock_out','write_off','shipment','shipment_client','shipment_fulfillment') {where_area}""",
        params,
    )
    qty = float(row["qty"] or 0) if row else 0.0
    # Комплектующие списываются автоматически при сборке и не получают отдельную операцию.
    if str(rule["entity_type"]) == "component":
        component_params: list[Any] = [int(rule["entity_id"]), int(rule["chat_id"]), since.strftime("%Y-%m-%d %H:%M:%S")]
        area_clause = ""
        if rule.get("area_id") is not None:
            area_clause = "AND o.area_id=?"
            component_params.append(int(rule["area_id"]))
        comp = db.fetchone(
            f"""SELECT COALESCE(SUM(ABS(o.quantity)*pc.quantity),0) AS qty
                FROM operations o JOIN product_components pc ON pc.product_id=o.entity_id
                WHERE pc.component_id=? AND o.chat_id=? AND o.created_at>=? AND o.operation_type='assembly' {area_clause}""",
            component_params,
        )
        qty += float(comp["qty"] or 0) if comp else 0.0
    days = max(1 / 24, (now - since).total_seconds() / 86400)
    shifts = days * float(rule["shifts_per_day"] or 1) * float(rule["work_days_per_week"] or 5) / 7.0
    return qty / max(0.01, shifts)



def _planned_rate(rule: dict[str, Any]) -> float:
    planned_id = int(rule.get("planned_output_entity_id") or 0)
    planned_qty = max(0.0, float(rule.get("planned_output_qty") or 0))
    if not planned_id or planned_qty <= 0:
        return 0.0
    output_per_shift = _to_per_shift(
        planned_qty,
        str(rule.get("planned_output_period") or "shift"),
        1,
        float(rule.get("shifts_per_day") or 1),
        float(rule.get("work_days_per_week") or 5),
    )
    if int(rule.get("entity_id") or 0) == planned_id:
        return output_per_shift
    component = db.fetchone(
        "SELECT quantity FROM product_components WHERE product_id=? AND component_id=?",
        (planned_id, int(rule["entity_id"])),
    )
    if component:
        return output_per_shift * max(0.0, float(component["quantity"] or 0))
    # Для сырья/упаковки можно задать технологический выход: из X входа получается Y выхода.
    if int(rule.get("yield_output_entity_id") or 0) == planned_id:
        input_qty = max(0.0, float(rule.get("yield_input_qty") or 0))
        output_qty = max(0.0, float(rule.get("yield_output_qty") or 0))
        if input_qty > 0 and output_qty > 0:
            return output_per_shift / output_qty * input_qty
    return 0.0

def evaluate_rule(rule_id: int, now: datetime | None = None) -> RiskSnapshot | None:
    rule = get_rule(rule_id)
    if not rule or not bool(rule.get("is_enabled")):
        return None
    now = now or datetime.now()
    since = now - timedelta(days=max(1, int(rule.get("learning_window_days") or 28)))
    manual = _to_per_shift(float(rule.get("manual_consumption_qty") or 0), str(rule.get("manual_period") or "shift"), 1, float(rule.get("shifts_per_day") or 1), float(rule.get("work_days_per_week") or 5))
    observed, samples, latest_data_at, latest_rate = _observed_rate(rule, since)
    if samples < int(rule.get("minimum_samples") or 1):
        observed_for_baseline = 0.0
    else:
        observed_for_baseline = observed
    historical = _historical_rate(rule, since, now)
    planned = _planned_rate(rule)
    mode = str(rule.get("calculation_mode") or "hybrid")
    if mode == "manual":
        baseline = manual
    elif mode == "observed":
        baseline = observed_for_baseline
    elif mode == "historical":
        baseline = historical
    elif mode == "planned":
        baseline = planned
    else:
        available = [x for x in (manual, observed_for_baseline, historical, planned) if x > 0]
        # Берём максимум: для закупок безопаснее не занижать потребность.
        baseline = max(available) if available else 0.0
    effects = _active_event_effects(rule, now)
    consumption = baseline * max(0.01, float(rule.get("demand_multiplier") or 1)) * max(0.01, effects["demand_multiplier"])
    stock = _stock_quantity(rule)
    effective = stock - max(0.0, float(rule.get("safety_buffer_qty") or 0)) - effects["unavailable"]
    reserve = effective / consumption if consumption > 0 else None
    warning = max(float(rule.get("warning_shifts") or 0), effects["lead_time_days"] * float(rule.get("shifts_per_day") or 1))
    critical = float(rule.get("critical_shifts") or 0)
    emergency = float(rule.get("emergency_shifts") or 0)
    severity = "ok"
    reason = "enough_stock"
    balance_where = ""
    balance_params: list[Any] = [int(rule["chat_id"]), int(rule["entity_id"])]
    if rule.get("area_id") is not None:
        balance_where = " AND area_id=?"
        balance_params.append(int(rule["area_id"]))
    latest_balance = db.fetchone(
        f"SELECT MAX(created_at) AS at FROM stock_observations WHERE chat_id=? AND entity_id=? AND observation_type='balance'{balance_where}",
        balance_params,
    )
    latest_balance_at = str(latest_balance["at"]) if latest_balance and latest_balance["at"] else None
    stale = False
    if latest_balance_at:
        try:
            stale = now - datetime.fromisoformat(latest_balance_at.replace("Z", "+00:00").replace("+00:00", "")) > timedelta(hours=int(rule.get("stale_after_hours") or 168))
        except Exception:
            stale = True
    else:
        stale = True
    if latest_data_at is None:
        latest_data_at = latest_balance_at
    anomaly = bool(latest_rate and baseline > 0 and latest_rate >= baseline * float(rule.get("anomaly_multiplier") or 2))
    if bool(rule.get("alert_on_negative")) and stock < 0:
        severity, reason = "emergency", "negative_stock"
    elif effective <= 0 and consumption > 0:
        severity, reason = "emergency", "no_available_stock"
    elif reserve is not None and reserve <= emergency:
        severity, reason = "emergency", "reserve_emergency"
    elif rule.get("absolute_critical_qty") is not None and effective <= float(rule["absolute_critical_qty"]):
        severity, reason = "critical", "absolute_critical"
    elif reserve is not None and reserve <= critical:
        severity, reason = "critical", "reserve_critical"
    elif rule.get("absolute_warning_qty") is not None and effective <= float(rule["absolute_warning_qty"]):
        severity, reason = "warning", "absolute_warning"
    elif reserve is not None and reserve <= warning:
        severity, reason = "warning", "reserve_warning"
    elif bool(rule.get("alert_on_anomaly")) and anomaly:
        severity, reason = "warning", "consumption_anomaly"
    elif bool(rule.get("alert_on_stale")) and stale:
        severity, reason = "warning", "stale_data"
    elif consumption <= 0:
        severity, reason = "unknown", "no_consumption_baseline"
    output_capacity = None
    if float(rule.get("yield_input_qty") or 0) > 0 and float(rule.get("yield_output_qty") or 0) > 0:
        output_capacity = max(0.0, effective) / float(rule["yield_input_qty"]) * float(rule["yield_output_qty"])
    reserve_text = "не рассчитан" if reserve is None or not math.isfinite(reserve) else f"{reserve:.1f} смен"
    message = (
        f"{rule.get('entity_name')}"
        + (f" · {rule.get('area_name')}" if rule.get("area_name") else "")
        + f": остаток {format_amount(stock)} {rule.get('default_unit') or 'шт'}, доступно {format_amount(effective)}; "
        + f"расход {format_amount(consumption)} за смену; запас {reserve_text}."
    )
    if output_capacity is not None:
        message += f" Возможный выпуск: около {format_amount(output_capacity)} {rule.get('yield_output_name') or 'ед.'}."
    if effects["lead_time_days"] > 0:
        message += f" Учтена задержка поставки {format_amount(effects['lead_time_days'])} дн."
    if effects["unavailable"] > 0:
        message += f" Недоступно по событиям: {format_amount(effects['unavailable'])}."
    reason_messages = {
        "negative_stock": "В учёте получился отрицательный остаток — требуется проверка.",
        "no_available_stock": "Доступный запас исчерпан.",
        "reserve_emergency": "Запаса меньше аварийного порога.",
        "reserve_critical": "Запаса меньше красного порога.",
        "reserve_warning": "Запаса меньше жёлтого порога.",
        "absolute_critical": "Количество ниже абсолютного красного порога.",
        "absolute_warning": "Количество ниже абсолютного жёлтого порога.",
        "consumption_anomaly": "Зафиксирован резкий скачок фактического расхода.",
        "stale_data": "Физическая инвентаризация давно не обновлялась.",
        "no_consumption_baseline": "Норма расхода ещё не определена.",
    }
    if reason in reason_messages:
        message += " " + reason_messages[reason]
    return RiskSnapshot(int(rule["id"]), severity, reason, stock, effective, consumption, reserve, warning, critical, emergency, latest_data_at, samples, message, output_capacity)


def _recipient_ids(rule: dict[str, Any]) -> set[int]:
    scope = int(rule["chat_id"])
    ids: set[int] = set(_json_ids(rule.get("notify_user_ids_json")))
    if bool(rule.get("notify_owner")):
        account = repo.get_account_by_scope(scope)
        if account:
            ids.add(int(account.owner_user_id))
    if bool(rule.get("notify_system_admins")):
        # Historical field name: in tenant mode this means organization admins,
        # never the platform owner/system menu.
        ids.update(repo.tenant_admin_user_ids(scope))
    if bool(rule.get("notify_department_heads")):
        rows = db.fetchall(
            """
            SELECT DISTINCT dm.user_id FROM department_members dm
            JOIN departments d ON d.id=dm.department_id AND d.is_archived=0
            JOIN department_entity_rules der ON der.department_id=d.id
            WHERE d.chat_id=? AND dm.is_active=1 AND dm.role_level>=50 AND der.entity_id=?
            """, (int(rule["chat_id"]), int(rule["entity_id"])),
        )
        ids.update(int(r["user_id"]) for r in rows)
    return {uid for uid in ids if uid > 0}


def _work_chat_id(rule: dict[str, Any]) -> int | None:
    if not bool(rule.get("notify_work_chat")):
        return None
    account = repo.get_account_by_scope(int(rule["chat_id"]))
    candidate = int(account.owner_chat_id) if account else int(rule["chat_id"])
    # Telegram-группы обычно имеют отрицательный ID. Для положительного служебного scope
    # автоматическую отправку в чат не делаем, чтобы не отправить сообщение чужому пользователю.
    return candidate if candidate < 0 else None


def _resolve_notifications(rule_id: int) -> None:
    db.execute(
        """UPDATE inbox_items SET status='resolved',resolved_at=CURRENT_TIMESTAMP
           WHERE related_type='stock_alert_rule' AND related_id=? AND status!='resolved'""", (int(rule_id),),
    )
    db.execute("DELETE FROM stock_alert_snoozes WHERE rule_id=?", (int(rule_id),))


def _recipient_snoozed(rule_id: int, user_id: int, now: datetime) -> bool:
    row = db.fetchone("SELECT snoozed_until FROM stock_alert_snoozes WHERE rule_id=? AND user_id=?", (int(rule_id), int(user_id)))
    if not row or not row["snoozed_until"]:
        return False
    try:
        return now < datetime.fromisoformat(str(row["snoozed_until"]))
    except Exception:
        return False


def persist_snapshot(snapshot: RiskSnapshot, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    rule = get_rule(snapshot.rule_id)
    if not rule:
        return {}
    open_row = db.fetchone("SELECT * FROM stock_alert_incidents WHERE rule_id=? AND status='open'", (snapshot.rule_id,))
    if snapshot.severity in {"ok", "unknown"}:
        if open_row:
            db.execute("UPDATE stock_alert_incidents SET status='resolved',resolved_at=CURRENT_TIMESTAMP,last_seen_at=CURRENT_TIMESTAMP WHERE id=?", (int(open_row["id"]),))
            _resolve_notifications(snapshot.rule_id)
        return {"severity": snapshot.severity, "notified": False}
    if open_row:
        previous = str(open_row["severity"])
        db.execute(
            """UPDATE stock_alert_incidents SET severity=?,reason_code=?,reserve_shifts=?,stock_quantity=?,effective_stock=?,
               consumption_per_shift=?,message=?,last_seen_at=CURRENT_TIMESTAMP WHERE id=?""",
            (snapshot.severity, snapshot.reason_code, snapshot.reserve_shifts, snapshot.stock_quantity, snapshot.effective_stock,
             snapshot.consumption_per_shift, snapshot.message, int(open_row["id"])),
        )
        incident_id = int(open_row["id"])
        last_notified = str(open_row["last_notified_at"] or "")
        due = not last_notified
        if last_notified:
            try:
                due = now - datetime.fromisoformat(last_notified) >= timedelta(minutes=int(rule.get("repeat_minutes") or 180))
            except Exception:
                due = True
        worsened = SEVERITY_ORDER.get(snapshot.severity, 0) > SEVERITY_ORDER.get(previous, 0)
        snoozed = False
        if open_row["snoozed_until"]:
            try:
                snoozed = now < datetime.fromisoformat(str(open_row["snoozed_until"]))
            except Exception:
                snoozed = False
        should_notify = worsened or (due and not snoozed)
    else:
        try:
            with db.connect() as conn:
                cur = conn.execute(
                    """INSERT INTO stock_alert_incidents(chat_id,rule_id,severity,reason_code,reserve_shifts,stock_quantity,effective_stock,
                       consumption_per_shift,message,status) VALUES(?,?,?,?,?,?,?,?,?,'open')""",
                    (int(rule["chat_id"]), snapshot.rule_id, snapshot.severity, snapshot.reason_code, snapshot.reserve_shifts,
                     snapshot.stock_quantity, snapshot.effective_stock, snapshot.consumption_per_shift, snapshot.message),
                )
                conn.commit()
                incident_id = int(cur.lastrowid)
            should_notify = True
        except Exception:
            # Параллельный пересчёт мог уже открыть единственную активную тревогу.
            concurrent = db.fetchone("SELECT id FROM stock_alert_incidents WHERE rule_id=? AND status='open'", (snapshot.rule_id,))
            if not concurrent:
                raise
            incident_id = int(concurrent["id"])
            should_notify = False
    notified = False
    if should_notify:
        labels = {"warning": "⚠️ Предупреждение", "critical": "🔴 Критический запас", "emergency": "🚨 АВАРИЙНЫЙ ЗАПАС"}
        priority = "urgent" if snapshot.severity == "emergency" else "high"
        for user_id in _recipient_ids(rule):
            if _recipient_snoozed(snapshot.rule_id, user_id, now):
                continue
            try:
                repo.create_inbox_item(
                    int(rule["chat_id"]), user_id, "stock_risk", labels[snapshot.severity], snapshot.message,
                    "stock_alert_rule", snapshot.rule_id, deduplicate=False, priority=priority, force=True,
                )
                notified = True
            except Exception:
                # Один недоступный получатель не должен остановить остальные уведомления.
                continue
        work_chat_id = _work_chat_id(rule)
        if work_chat_id is not None:
            try:
                repo.create_inbox_item(
                    int(rule["chat_id"]), work_chat_id, "stock_risk_chat", labels[snapshot.severity], snapshot.message,
                    "stock_alert_rule_chat", snapshot.rule_id, deduplicate=False, priority=priority, force=True,
                )
                notified = True
            except Exception:
                # Недоступный рабочий чат не мешает личным уведомлениям и пересчёту.
                pass
        db.execute("UPDATE stock_alert_incidents SET last_notified_at=CURRENT_TIMESTAMP,notification_count=notification_count+1 WHERE id=?", (incident_id,))
    return {"severity": snapshot.severity, "notified": notified, "incident_id": incident_id}


def evaluate_all(chat_id: int | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    params: tuple[Any, ...] = ()
    where = "WHERE is_enabled=1"
    if chat_id is not None:
        where += " AND chat_id=?"
        params = (_scope(chat_id),)
    rows = db.fetchall(f"SELECT id FROM stock_alert_rules {where} ORDER BY id", params)
    result = []
    for row in rows:
        rule_id = int(row["id"])
        try:
            snapshot = evaluate_rule(rule_id, now)
            if not snapshot:
                continue
            persisted = persist_snapshot(snapshot, now)
            result.append({**snapshot.__dict__, **persisted})
        except Exception as exc:
            # Ошибка одного правила не останавливает контроль остальных позиций.
            result.append({"rule_id": rule_id, "severity": "unknown", "error": str(exc)[:300]})
    return result


def evaluate_related_rules(chat_id: int, entity_type: str | None, entity_id: int | None, area_id: int | None = None, _seen: set[tuple[str, int, int | None]] | None = None) -> list[dict[str, Any]]:
    if not entity_id:
        return []
    scope = _scope(chat_id)
    seen = _seen if _seen is not None else set()
    key = (str(entity_type or ""), int(entity_id), area_id)
    if key in seen:
        return []
    seen.add(key)
    rows = db.fetchall(
        """SELECT id FROM stock_alert_rules WHERE chat_id=? AND is_enabled=1 AND entity_id=?
           AND (area_id IS NULL OR area_id=?)""", (scope, int(entity_id), area_id),
    )
    result = []
    for row in rows:
        try:
            snap = evaluate_rule(int(row["id"]))
            if snap:
                result.append({**snap.__dict__, **persist_snapshot(snap)})
        except Exception as exc:
            result.append({"rule_id": int(row["id"]), "severity": "unknown", "error": str(exc)[:300]})
    # Сборка влияет на связанные комплектующие, поэтому пересчитываем и их правила.
    if entity_type == "product":
        for comp in repo.list_product_components(int(entity_id)):
            result.extend(evaluate_related_rules(scope, "component", int(comp["component_id"]), area_id, seen))
    return result


def list_incidents(chat_id: int, include_resolved: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    status = "" if include_resolved else "AND i.status='open'"
    rows = db.fetchall(
        f"""SELECT i.*,r.entity_id,r.entity_type,r.area_id,e.name AS entity_name,e.default_unit,a.name AS area_name
            FROM stock_alert_incidents i JOIN stock_alert_rules r ON r.id=i.rule_id
            JOIN entities e ON e.id=r.entity_id LEFT JOIN areas a ON a.id=r.area_id AND a.chat_id=r.chat_id
            WHERE i.chat_id=? {status}
            ORDER BY CASE i.severity WHEN 'emergency' THEN 0 WHEN 'critical' THEN 1 ELSE 2 END,i.last_seen_at DESC LIMIT ?""",
        (scope, max(1, min(int(limit), 500))),
    )
    return [dict(r) for r in rows]


def acknowledge_incident(chat_id: int, incident_id: int, user_id: int, snooze_minutes: int = 0) -> bool:
    scope = _scope(chat_id)
    row = db.fetchone(
        """SELECT i.id,i.rule_id,r.entity_id FROM stock_alert_incidents i
           JOIN stock_alert_rules r ON r.id=i.rule_id
           WHERE i.id=? AND i.chat_id=? AND i.status='open'""",
        (int(incident_id), scope),
    )
    if not row:
        return False
    if not repo.is_tenant_admin(scope, user_id):
        memberships = repo.user_department_memberships(scope, user_id)
        if memberships:
            visible = repo.visible_entity_ids_for_user(scope, user_id) or set()
            if int(row["entity_id"]) not in {int(x) for x in visible}:
                return False
    snooze = None
    if snooze_minutes > 0:
        snooze = (datetime.now() + timedelta(minutes=max(5, min(int(snooze_minutes), 10080)))).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE stock_alert_incidents SET acknowledged_by=?,acknowledged_at=CURRENT_TIMESTAMP WHERE id=?", (int(user_id), int(incident_id)))
    db.execute(
        """INSERT INTO stock_alert_snoozes(rule_id,user_id,snoozed_until,acknowledged_at,updated_at)
           VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
           ON CONFLICT(rule_id,user_id) DO UPDATE SET snoozed_until=excluded.snoozed_until,
             acknowledged_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP""",
        (int(row["rule_id"]), int(user_id), snooze),
    )
    db.execute("UPDATE inbox_items SET status='resolved',resolved_at=CURRENT_TIMESTAMP WHERE recipient_user_id=? AND related_type='stock_alert_rule' AND related_id=? AND status!='resolved'", (int(user_id), int(row["rule_id"])))
    return True



def dashboard_for_user(chat_id: int, user_id: int) -> dict[str, Any]:
    scope = _scope(chat_id)
    data = dashboard(scope)
    if repo.is_tenant_admin(scope, user_id):
        return data
    memberships = repo.user_department_memberships(scope, user_id)
    visible = repo.visible_entity_ids_for_user(scope, user_id)
    # Старые роли без отделов сохраняют прежний полный просмотр в рамках своего учёта.
    if not memberships or visible is None:
        return data
    visible_ids = {int(x) for x in visible}
    department_ids = {int(x["department_id"]) for x in memberships}
    data["rules"] = [x for x in data.get("rules", []) if int(x.get("entity_id") or 0) in visible_ids]
    data["incidents"] = [x for x in data.get("incidents", []) if int(x.get("entity_id") or 0) in visible_ids]
    data["observations"] = [x for x in data.get("observations", []) if int(x.get("entity_id") or 0) in visible_ids]
    filtered_events = []
    for event in data.get("events", []):
        if int(event.get("created_by") or 0) == int(user_id):
            filtered_events.append(event)
            continue
        entity_id = int(event.get("entity_id") or 0)
        department_id = int(event.get("department_id") or 0)
        if entity_id and entity_id in visible_ids:
            filtered_events.append(event)
        elif department_id and department_id in department_ids:
            filtered_events.append(event)
    data["events"] = filtered_events
    data["summary"] = {
        "emergency": sum(1 for x in data["rules"] if x.get("severity") == "emergency"),
        "critical": sum(1 for x in data["rules"] if x.get("severity") == "critical"),
        "warning": sum(1 for x in data["rules"] if x.get("severity") == "warning"),
        "unknown": sum(1 for x in data["rules"] if x.get("severity") == "unknown"),
    }
    return data

def dashboard(chat_id: int) -> dict[str, Any]:
    scope = _scope(chat_id)
    rules = list_rules(scope)
    snapshots = []
    for rule in rules:
        item = dict(rule)
        try:
            snap = evaluate_rule(int(rule["id"])) if rule.get("is_enabled") else None
            if snap:
                item.update(snap.__dict__)
        except Exception as exc:
            item.update({"severity": "unknown", "reason_code": "calculation_error", "error": str(exc)[:300]})
        snapshots.append(item)
    return {
        "rules": snapshots,
        "incidents": list_incidents(scope),
        "events": list_events(scope),
        "event_catalog": EVENT_CATALOG,
        "observations": list_observations(scope, limit=100),
        "summary": {
            "emergency": sum(1 for x in snapshots if x.get("severity") == "emergency"),
            "critical": sum(1 for x in snapshots if x.get("severity") == "critical"),
            "warning": sum(1 for x in snapshots if x.get("severity") == "warning"),
            "unknown": sum(1 for x in snapshots if x.get("severity") == "unknown"),
        },
    }
