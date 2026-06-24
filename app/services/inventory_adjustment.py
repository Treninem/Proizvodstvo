from __future__ import annotations

import re
from typing import Any

from . import repository as repo
from .normalize import format_amount
from .matcher import confident_match
from .normalize import normalize_key
from .parser import NUMBER_RE

_INVENTORY_WORDS = {
    "инвентаризация",
    "сверка",
    "пересчет",
    "пересчёт",
    "факт",
    "фактически",
    "установить остаток",
    "новый остаток",
    "остаток факт",
}

_ALLOWED_TYPES = {"component", "product", "material", "stock_item"}


def looks_like_inventory_adjustment(text: str) -> bool:
    key = normalize_key(text)
    if not key or not NUMBER_RE.search(text):
        return False
    if any(word in key for word in _INVENTORY_WORDS):
        return True
    return "остаток" in key and "остатки" not in key and bool(NUMBER_RE.search(text))


def _remove_inventory_words(text: str) -> str:
    cleaned = text
    for word in sorted(_INVENTORY_WORDS | {"остаток", "остатки", "склад"}, key=len, reverse=True):
        cleaned = re.sub(rf"\b{re.escape(word)}\b", " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip(" ,.-")


def _extract_quantity(line: str) -> tuple[float | None, str, str]:
    matches = list(NUMBER_RE.finditer(line))
    if not matches:
        return None, "шт", line
    m = matches[-1]
    try:
        value = float(m.group("num").replace(",", "."))
    except ValueError:
        return None, "шт", line
    unit = (m.group("unit") or "шт").lower().replace("квтч", "кВт⋅ч").replace("квт", "кВт⋅ч")
    rest = (line[:m.start()] + " " + line[m.end():]).strip(" ,.-")
    return value, unit, rest


def _area_from_line(scope_chat_id: int, group_chat_id: int, line: str) -> tuple[int | None, str | None, str]:
    key = normalize_key(line)
    for area in repo.list_areas(scope_chat_id):
        if area.normalized and area.normalized in key:
            cleaned = re.sub(re.escape(area.name), " ", line, flags=re.IGNORECASE).strip()
            return area.id, area.name, cleaned
    match, _ = confident_match(scope_chat_id, line, allowed_types={"area"})
    if match:
        area = repo.get_area(match.target_id)
        if area:
            cleaned = re.sub(re.escape(area.name), " ", line, flags=re.IGNORECASE).strip()
            return area.id, area.name, cleaned
    bound = repo.get_bound_area(group_chat_id)
    if bound:
        return bound.id, bound.name, line
    return None, None, line


def _target_area_for_entity(scope_chat_id: int, group_chat_id: int, entity_type: str, entity_id: int, area_id: int | None, area_name: str | None) -> tuple[int | None, str | None, str | None]:
    if entity_type == "stock_item":
        ids = repo.list_stock_item_area_ids(entity_id)
        if not ids:
            return None, None, None
        if len(ids) == 1:
            area = repo.get_area(ids[0])
            return (area.id, area.name, None) if area else (None, None, "Участок складской позиции не найден.")
        if area_id and area_id in ids:
            return area_id, area_name, None
        return None, None, "Нужно указать участок для складской позиции."
    if entity_type == "material":
        if area_id:
            return area_id, area_name, None
        return None, None, "Нужно указать участок для сырья."
    return None, None, None


def parse_inventory_lines(scope_chat_id: int, group_chat_id: int, text: str) -> tuple[list[dict[str, Any]], list[str]]:
    operations: list[dict[str, Any]] = []
    errors: list[str] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return [], []
    for line in lines:
        key = normalize_key(line)
        if not NUMBER_RE.search(line):
            continue
        if key in {"инвентаризация", "сверка", "пересчет", "пересчёт"}:
            continue
        qty, unit, rest = _extract_quantity(line)
        if qty is None:
            continue
        area_id, area_name, without_area = _area_from_line(scope_chat_id, group_chat_id, rest)
        name_part = _remove_inventory_words(without_area)
        match, variants = confident_match(scope_chat_id, name_part, allowed_types=_ALLOWED_TYPES)
        if not match:
            operations.append({
                "operation_type": "inventory_adjust",
                "entity_type": None,
                "entity_id": None,
                "entity_name": None,
                "quantity": None,
                "unit": unit,
                "area_id": area_id,
                "area_name": area_name,
                "raw_line": line,
                "needs_attention": True,
                "variants": [v.__dict__ for v in variants],
                "learning_phrase": name_part,
            })
            errors.append(f"Не удалось понять позицию: {line}")
            continue
        final_area_id, final_area_name, area_error = _target_area_for_entity(
            scope_chat_id, group_chat_id, match.target_type, match.target_id, area_id, area_name
        )
        current = repo.inventory_quantity(scope_chat_id, match.target_type, match.target_id, unit, final_area_id)
        operations.append({
            "operation_type": "inventory_adjust",
            "entity_type": match.target_type,
            "entity_id": match.target_id,
            "entity_name": match.name,
            "quantity": float(qty) - current,
            "unit": unit,
            "area_id": final_area_id,
            "area_name": final_area_name,
            "raw_line": line,
            "needs_attention": bool(area_error),
            "learning_phrase": name_part,
            "fact_quantity": float(qty),
            "old_quantity": current,
        })
        if area_error:
            errors.append(area_error)
    return operations, errors


def format_inventory_summary(operations: list[dict[str, Any]], errors: list[str]) -> str:
    lines = ["Проверьте инвентаризацию перед сохранением:"]
    for op in operations:
        name = op.get("entity_name") or "нужно уточнить"
        unit = op.get("unit") or "шт"
        area = f" · {op.get('area_name')}" if op.get("area_name") else ""
        if op.get("needs_attention"):
            lines.append(f"• {name}{area} · нужно уточнить")
            continue
        old = float(op.get("old_quantity") or 0)
        fact = float(op.get("fact_quantity") or 0)
        delta = float(op.get("quantity") or 0)
        sign = "+" if delta >= 0 else ""
        lines.append(f"• {name}{area}: было {format_amount(old)} {unit}, стало {format_amount(fact)} {unit}, правка {sign}{format_amount(abs(delta)) if delta < 0 else format_amount(delta)} {unit}")
    if errors:
        lines.append("\nНужно уточнить:")
        lines.extend(f"• {e}" for e in sorted(set(errors)))
    lines.append("\nВсё верно?")
    return "\n".join(lines)
