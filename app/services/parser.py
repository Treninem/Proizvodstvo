from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

from .matcher import confident_match
from .normalize import normalize_key
from .repository import get_area, get_bound_area, list_areas, list_meter_area_ids, list_meters_for_area, list_stock_item_area_ids
from .vocabulary import (
    ALL_OPERATION_WORDS,
    ASSEMBLY_WORDS,
    ENERGY_WORDS,
    MATERIAL_IN_WORDS,
    MATERIAL_OUT_WORDS,
    NO_WORDS,
    PRODUCTION_WORDS,
    REPORT_WORDS,
    SHIPMENT_WORDS,
    STOCK_IN_WORDS,
    STOCK_OUT_WORDS,
    YES_WORDS,
)

NUMBER_RE = re.compile(r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>кг|г|т|шт|штук|квт|квтч|квт⋅ч)?", re.IGNORECASE)

# Слова, которые часто встречаются в обычной переписке. Если сообщение с такими
# словами не начинается с явного учётного действия, бот не должен влезать в чат.
DISCUSSION_HINTS = {
    "надо", "нужно", "нужна", "нужен", "помните", "помнить", "говорил",
    "думаю", "возможно", "наверное", "может", "можем", "будем", "будет", "если",
    "купить", "проверить", "посмотреть", "касается", "оставлять", "нельзя",
    "вроде", "странно", "показывает", "показал",
    "хорошие", "случай", "всякий", "готовым", "готовы", "готовыми",
}

LINE_FILLER_WORDS = {
    "сегодня", "вчера", "за", "смену", "смена", "день", "ночь", "утро",
    "вечер", "сейчас", "только", "ещё", "еще", "итого", "всего", "штук",
}

ACCOUNTING_PREFIXES = ("учет:", "учёт:", "учет ", "учёт ")


@dataclass
class ParsedOperation:
    operation_type: str
    entity_type: str | None
    entity_id: int | None
    entity_name: str | None
    quantity: float | None
    unit: str
    area_id: int | None
    area_name: str | None
    raw_line: str
    confidence: float = 1.0
    needs_attention: bool = False
    variants: list[dict[str, Any]] | None = None
    learning_phrase: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_yes(text: str) -> bool:
    return normalize_key(text) in YES_WORDS


def is_no(text: str) -> bool:
    return normalize_key(text) in NO_WORDS


def _has_any(text: str, words: set[str]) -> bool:
    key = normalize_key(text)
    return any(w in key.split() or w in key for w in words)


def _strip_accounting_prefix(text: str) -> tuple[str, bool]:
    clean = text.strip()
    lowered = clean.lower()
    for prefix in ACCOUNTING_PREFIXES:
        if lowered.startswith(prefix):
            return clean[len(prefix):].strip(), True
    return text, False


def _line_starts_with_action(line: str, words: set[str]) -> bool:
    key = normalize_key(line)
    if not key:
        return False
    tokens = key.split()
    for word in sorted(words, key=len, reverse=True):
        w_tokens = normalize_key(word).split()
        if not w_tokens:
            continue
        if tokens[:len(w_tokens)] == w_tokens:
            return True
    return False


def _has_strong_energy_action(line: str) -> bool:
    key = normalize_key(line)
    tokens = key.split()
    if not tokens:
        return False
    # Одного слова «счётчик» мало: в группах его часто обсуждают. Для ввода
    # нужны показание/электроэнергия/ээ или короткая форма с явным числом.
    strong_starts = {
        "показание", "показания", "электроэнергия", "электрика", "энергия",
        "ээ", "э э", "эл", "квт", "квтч", "свет",
    }
    key = normalize_key(line)
    if any(_line_starts_with_action(line, {w}) for w in strong_starts):
        return True
    if any(w in key.split() or w in key for w in strong_starts):
        return True
    # Точная короткая запись сохранённого счётчика часто выглядит как
    # «Счётчик 1 1356» или «Участок 2 Счётчик 1 1356».
    if _line_starts_with_action(line, {"счетчик", "счётчик"}) and bool(NUMBER_RE.search(line)):
        return True
    if ("счетчик" in key or "счётчик" in key) and len(list(NUMBER_RE.finditer(line))) >= 2:
        return True
    return False


def _line_has_accounting_action(line: str) -> bool:
    if _line_starts_with_action(line, PRODUCTION_WORDS | MATERIAL_IN_WORDS | MATERIAL_OUT_WORDS | STOCK_IN_WORDS | STOCK_OUT_WORDS | ASSEMBLY_WORDS | SHIPMENT_WORDS):
        return True
    return _has_strong_energy_action(line)


def _is_discussion_line(line: str) -> bool:
    key = normalize_key(line)
    if not key:
        return False
    tokens = set(key.split())
    if "?" in line:
        return True
    if tokens.intersection(DISCUSSION_HINTS):
        # Для слов про счётчики особенно важно не путать обсуждение с вводом
        # показаний. Рабочие записи обычно начинаются с производственного действия.
        if not _line_starts_with_action(line, PRODUCTION_WORDS | MATERIAL_IN_WORDS | MATERIAL_OUT_WORDS | STOCK_IN_WORDS | STOCK_OUT_WORDS | ASSEMBLY_WORDS | SHIPMENT_WORDS):
            return True
    return False


def looks_like_accounting(text: str) -> bool:
    clean, forced = _strip_accounting_prefix(text)
    key = normalize_key(clean)
    if not key:
        return False
    lines = [l.strip() for l in clean.splitlines() if l.strip()]
    if not lines:
        return False

    # Явный префикс разрешает короткий ввод, но всё равно нужна цифра или команда отчёта.
    if forced:
        return bool(NUMBER_RE.search(clean) or _has_any(key, REPORT_WORDS))

    first_line = lines[0]
    if _is_discussion_line(first_line):
        return False

    # Отчёты обрабатывает отдельный модуль. Здесь пропускаем только рабочие факты.
    if _line_has_accounting_action(first_line) and NUMBER_RE.search(clean):
        return True

    # Многострочный ввод принимается только если первая строка задаёт действие
    # вроде «Сделали сегодня:», а следующие строки содержат количества.
    if len(lines) >= 2 and _line_has_accounting_action(first_line):
        return any(NUMBER_RE.search(line) for line in lines[1:])

    return False


def detect_header_context(line: str) -> str | None:
    key = normalize_key(line)
    if _has_strong_energy_action(line):
        return "energy"
    if _has_any(key, PRODUCTION_WORDS):
        return "production"
    if _has_any(key, MATERIAL_IN_WORDS):
        return "material_in"
    if _has_any(key, MATERIAL_OUT_WORDS):
        return "material_out"
    if _has_any(key, STOCK_IN_WORDS):
        return "stock_in"
    if _has_any(key, STOCK_OUT_WORDS):
        return "stock_out"
    if _has_any(key, ASSEMBLY_WORDS):
        return "assembly"
    if _has_any(key, SHIPMENT_WORDS):
        return "shipment"
    return None


def _extract_number_unit(line: str) -> tuple[float | None, str, str]:
    matches = list(NUMBER_RE.finditer(line))
    if not matches:
        return None, "шт", line
    m = matches[-1]
    raw = m.group("num").replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        value = None
    unit = (m.group("unit") or "шт").lower().replace("штук", "шт").replace("квтч", "кВт⋅ч").replace("квт", "кВт⋅ч")
    rest = (line[:m.start()] + " " + line[m.end():]).strip(" ,.-")
    return value, unit, rest


def _remove_intent_words(text: str, words: set[str]) -> str:
    key = text
    for w in sorted(words, key=len, reverse=True):
        key = re.sub(rf"\b{re.escape(w)}\b", " ", key, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", key).strip()


def _clean_entity_name_part(text: str) -> str:
    key = _remove_intent_words(text, LINE_FILLER_WORDS)
    key = re.sub(r"\b(?:шт|штук|кг|г|т|квт|квтч)\b", " ", key, flags=re.IGNORECASE)
    key = re.sub(r"\s+", " ", key).strip(" ,.-:")
    return key


def _detect_area(chat_id: int, line: str, current_area_id: int | None, current_area_name: str | None, group_chat_id: int | None) -> tuple[int | None, str | None, str]:
    areas = list_areas(chat_id)
    best = None
    for area in areas:
        if area.normalized and area.normalized in normalize_key(line):
            best = area
            break
    if not best:
        match, _ = confident_match(chat_id, line, allowed_types={"area"})
        if match:
            best = next((a for a in areas if a.id == match.target_id), None)
    if best:
        cleaned = re.sub(re.escape(best.name), " ", line, flags=re.IGNORECASE).strip()
        return best.id, best.name, cleaned
    if current_area_id:
        return current_area_id, current_area_name, line
    if group_chat_id:
        bound = get_bound_area(group_chat_id)
        if bound:
            return bound.id, bound.name, line
    return None, None, line


def _parse_entity_quantity(chat_id: int, line: str, operation_type: str, area_id: int | None, area_name: str | None) -> ParsedOperation:
    quantity, unit, name_part = _extract_number_unit(line)
    allowed = {"component", "product", "stock_item"}
    if operation_type in {"material_in", "material_out", "stock_in", "stock_out"}:
        allowed = {"material", "stock_item"}
    elif operation_type == "energy":
        allowed = {"meter"}
    elif operation_type == "assembly":
        allowed = {"product"}
    elif operation_type == "shipment":
        allowed = {"product"}
    name_part = _remove_intent_words(name_part, PRODUCTION_WORDS | MATERIAL_IN_WORDS | MATERIAL_OUT_WORDS | STOCK_IN_WORDS | STOCK_OUT_WORDS | ENERGY_WORDS | ASSEMBLY_WORDS | SHIPMENT_WORDS)
    name_part = _clean_entity_name_part(name_part)
    if operation_type == "energy" and not name_part:
        return ParsedOperation(operation_type, None, None, "Электроэнергия", quantity, unit if unit != "шт" else "кВт⋅ч", area_id, area_name, line, 0.8)
    match, variants = confident_match(chat_id, name_part, allowed_types=allowed)
    if match:
        final_operation = operation_type
        if match.target_type == "stock_item" and operation_type in {"material_in", "stock_in"}:
            final_operation = "stock_in"
        elif match.target_type == "stock_item" and operation_type in {"material_out", "stock_out"}:
            final_operation = "stock_out"
        elif match.target_type == "material" and operation_type == "stock_in":
            final_operation = "material_in"
        elif match.target_type == "material" and operation_type == "stock_out":
            final_operation = "material_out"
        return ParsedOperation(
            final_operation,
            match.target_type,
            match.target_id,
            match.name,
            quantity,
            unit,
            area_id,
            area_name,
            line,
            match.score,
            False,
            None,
            name_part,
        )
    return ParsedOperation(
        operation_type,
        None,
        None,
        None,
        quantity,
        unit,
        area_id,
        area_name,
        line,
        0.2,
        True,
        [v.__dict__ for v in variants],
        name_part,
    )


def parse_message(chat_id: int, group_chat_id: int, text: str) -> tuple[list[ParsedOperation], list[str]]:
    errors: list[str] = []
    operations: list[ParsedOperation] = []
    text, _forced = _strip_accounting_prefix(text)
    raw_lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not raw_lines:
        return [], []
    if not _forced and _is_discussion_line(raw_lines[0]):
        return [], []

    context: str | None = None
    current_area_id: int | None = None
    current_area_name: str | None = None

    for raw_line in raw_lines:
        line = raw_line.strip().strip(":")
        if not line:
            continue
        header = detect_header_context(line)
        has_number = bool(NUMBER_RE.search(line))
        if header:
            # Строка с действием задаёт контекст для следующих строк. Так работает
            # ввод вроде «Сделали сегодня:» и список позиций ниже.
            context = header
            a_id, a_name, clean_for_header = _detect_area(chat_id, line, current_area_id, current_area_name, group_chat_id)
            clean_for_header = _remove_intent_words(
                clean_for_header,
                PRODUCTION_WORDS | MATERIAL_IN_WORDS | MATERIAL_OUT_WORDS | ENERGY_WORDS | ASSEMBLY_WORDS | SHIPMENT_WORDS,
            )
            clean_for_header = _clean_entity_name_part(clean_for_header)
            header_quantity, _header_unit, header_name = _extract_number_unit(clean_for_header)
            # «Производство Участок 1» или «Сделали сегодня:» — только заголовок.
            # «Сделали Деталь 100» — полноценная запись.
            if not has_number or header_quantity is None or (header != "energy" and not _clean_entity_name_part(header_name)):
                if a_id:
                    current_area_id, current_area_name = a_id, a_name
                continue

        # line may contain several material items separated by commas
        chunks = [c.strip() for c in line.split(",") if c.strip()] if "," in line else [line]
        for chunk in chunks:
            chunk_header = detect_header_context(chunk)
            local_context = chunk_header or context
            if chunk_header:
                context = chunk_header
            if not local_context:
                # Без явного действия строка принимается только внутри ранее заданного
                # контекста. Это защищает обычную переписку от случайных ответов бота.
                continue
            if local_context == "energy" and not _has_strong_energy_action(chunk) and not context == "energy":
                continue
            if not NUMBER_RE.search(chunk):
                continue
            a_id, a_name, cleaned = _detect_area(chat_id, chunk, current_area_id, current_area_name, group_chat_id)
            if a_id:
                current_area_id, current_area_name = a_id, a_name
            op = _parse_entity_quantity(chat_id, cleaned, local_context, a_id, a_name)

            if op.operation_type == "energy":
                if op.entity_id and op.area_id:
                    meter_area_ids = list_meter_area_ids(int(op.entity_id))
                    if meter_area_ids and int(op.area_id) not in meter_area_ids:
                        op.needs_attention = True
                        errors.append("Этот счётчик не привязан к выбранному участку.")
                elif op.entity_id and not op.area_id:
                    meter_area_ids = list_meter_area_ids(int(op.entity_id))
                    if len(meter_area_ids) == 1:
                        area = get_area(meter_area_ids[0])
                        if area:
                            op.area_id = area.id
                            op.area_name = area.name
                    elif len(meter_area_ids) > 1:
                        op.needs_attention = True
                        errors.append("Нужно выбрать участок для показания счётчика.")
                elif op.area_id and not op.entity_id:
                    meters = list_meters_for_area(chat_id, int(op.area_id))
                    if len(meters) == 1:
                        meter = meters[0]
                        op.entity_type = "meter"
                        op.entity_id = meter.id
                        op.entity_name = meter.name
                    elif len(meters) > 1:
                        op.needs_attention = True
                        errors.append("К участку привязано несколько счётчиков. Нужно выбрать прибор учёта.")
                    else:
                        op.needs_attention = True
                        errors.append("К участку не привязан прибор учёта.")

            if op.entity_type == "stock_item" and op.entity_id and op.operation_type in {"stock_in", "stock_out"}:
                stock_area_ids = list_stock_item_area_ids(int(op.entity_id))
                if not stock_area_ids:
                    op.area_id = None
                    op.area_name = None
                elif len(stock_area_ids) == 1:
                    area = get_area(stock_area_ids[0])
                    if area:
                        op.area_id = area.id
                        op.area_name = area.name
                elif op.area_id and int(op.area_id) in stock_area_ids:
                    pass
                else:
                    op.needs_attention = True
                    errors.append("Нужно выбрать участок для складской позиции.")

            if op.operation_type in {"material_in", "material_out", "energy"} and not op.area_id:
                # Only material and energy must be tied directly to an area.
                op.needs_attention = True
                errors.append("Нужно выбрать участок для сырья или электричества.")
            operations.append(op)

    return operations, errors
