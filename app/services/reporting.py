from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Iterable
import csv
import html
import re
import zipfile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from .. import db
from ..config import settings
from .normalize import normalize_key


OPERATION_LABELS: dict[str, str] = {
    "production": "Производство",
    "material_in": "Поступление сырья",
    "material_out": "Расход сырья",
    "energy": "Электроэнергия",
    "assembly": "Сборка",
    "shipment": "Отгрузка",
    "stock_in": "Склад: приход",
    "stock_out": "Склад: уход",
    "inventory_adjust": "Инвентаризация",
}

ENTITY_LABELS: dict[str, str] = {
    "component": "Комплектующие",
    "product": "Готовая продукция",
    "material": "Сырьё",
    "meter": "Прибор учёта",
    "stock_item": "Складская позиция",
}


@dataclass(frozen=True)
class PeriodFilter:
    title: str
    where_sql: str
    params: tuple[str, ...]
    error: str = ""


_DATE_PATTERN = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{2,4})(?!\d)")
_MAX_PERIOD_DAYS = 36525


def _format_ru_day(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _parse_ru_day(match: re.Match[str]) -> date | None:
    day = int(match.group(1))
    month = int(match.group(2))
    year_raw = match.group(3)
    year = int(year_raw)
    if len(year_raw) == 2:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _period_from_days(days: list[date]) -> PeriodFilter | None:
    if not days:
        return None
    start = days[0]
    end = days[1] if len(days) > 1 else days[0]
    if start > end:
        return PeriodFilter(
            "",
            "1=0",
            tuple(),
            "Дата начала не может быть позже даты конца. Укажите период так: 12.05.2022-20.06.2022.",
        )
    if (end - start).days > _MAX_PERIOD_DAYS:
        return PeriodFilter("", "1=0", tuple(), "Период не должен быть длиннее 100 лет.")
    end_exclusive = end + timedelta(days=1)
    if start == end:
        title = f"за {_format_ru_day(start)}"
    else:
        title = f"с {_format_ru_day(start)} по {_format_ru_day(end)}"
    return PeriodFilter(title, "o.created_at >= ? AND o.created_at < ?", (str(start), str(end_exclusive)))


def period_error_for_text(text: str) -> str:
    return _date_bounds_for_text(text).error


def looks_like_period_text(text: str) -> bool:
    return bool(_DATE_PATTERN.search(text or ""))


def _date_bounds_for_text(text: str) -> PeriodFilter:
    parsed_days: list[date] = []
    invalid_date_seen = False
    for match in _DATE_PATTERN.finditer(text or ""):
        parsed = _parse_ru_day(match)
        if parsed is None:
            invalid_date_seen = True
        else:
            parsed_days.append(parsed)
    if invalid_date_seen and not parsed_days:
        return PeriodFilter("", "1=0", tuple(), "Дата указана неверно. Используйте формат 12.05.2022.")
    explicit = _period_from_days(parsed_days[:2])
    if explicit is not None:
        return explicit

    key = normalize_key(text)
    today = datetime.now().date()
    if "вчера" in key:
        start = today - timedelta(days=1)
        end = today
        return PeriodFilter("за вчера", "o.created_at >= ? AND o.created_at < ?", (str(start), str(end)))
    if "недел" in key or "7" in key and "дн" in key:
        start = today - timedelta(days=7)
        return PeriodFilter("за 7 дней", "o.created_at >= ?", (str(start),))
    if "месяц" in key or "мес" in key:
        start = today.replace(day=1)
        return PeriodFilter("за текущий месяц", "o.created_at >= ?", (str(start),))
    if "сегодня" in key or not any(word in key for word in ("вчера", "недел", "месяц", "все", "всё", "общ")):
        return PeriodFilter("за сегодня", "o.created_at >= ?", (str(today),))
    return PeriodFilter("за всё время", "1=1", tuple())


def reports_dir() -> Path:
    path = settings.data_dir / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(value: str) -> str:
    clean = re.sub(r"[^0-9A-Za-zА-Яа-яёЁ_.-]+", "_", value).strip("_")
    return clean[:80] or "report"


def inventory_rows(chat_id: int) -> list[dict]:
    rows = db.fetchall(
        """
        SELECT i.quantity,i.unit,i.entity_type,e.name AS entity_name,a.name AS area_name
        FROM inventory i
        LEFT JOIN entities e ON e.id=i.entity_id
        LEFT JOIN areas a ON a.id=i.area_id
        WHERE i.chat_id=? AND ABS(i.quantity) > 0.000001
        ORDER BY i.entity_type, COALESCE(a.name,''), e.name, i.unit
        """,
        (chat_id,),
    )
    return [dict(r) for r in rows]


def operation_rows(chat_id: int, period: PeriodFilter) -> list[dict]:
    rows = db.fetchall(
        f"""
        SELECT o.operation_type,o.entity_type,e.name AS entity_name,a.name AS area_name,o.unit,
               SUM(o.quantity) AS total_quantity, COUNT(o.id) AS count_rows
        FROM operations o
        LEFT JOIN entities e ON e.id=o.entity_id
        LEFT JOIN areas a ON a.id=o.area_id
        WHERE o.chat_id=? AND {period.where_sql}
        GROUP BY o.operation_type,o.entity_type,o.entity_id,o.area_id,o.unit
        ORDER BY o.operation_type, COALESCE(a.name,''), e.name
        """,
        (chat_id, *period.params),
    )
    return [dict(r) for r in rows]


def raw_operation_rows(chat_id: int, period: PeriodFilter, limit: int = 5000) -> list[dict]:
    rows = db.fetchall(
        f"""
        SELECT o.created_at,o.operation_type,o.entity_type,e.name AS entity_name,a.name AS area_name,
               o.quantity,o.unit,o.user_id,o.group_chat_id,o.raw_text
        FROM operations o
        LEFT JOIN entities e ON e.id=o.entity_id
        LEFT JOIN areas a ON a.id=o.area_id
        WHERE o.chat_id=? AND {period.where_sql}
        ORDER BY o.created_at DESC
        LIMIT ?
        """,
        (chat_id, *period.params, limit),
    )
    return [dict(r) for r in rows]



def build_stock_text(chat_id: int) -> str:
    rows = inventory_rows(chat_id)
    if not rows:
        return "Склад пока пустой."
    lines = ["Склад"]
    current = None
    for row in rows[:80]:
        title = ENTITY_LABELS.get(row.get("entity_type") or "", row.get("entity_type") or "Позиции")
        if title != current:
            current = title
            lines.append(f"\n{title}:")
        name = row.get("entity_name") or "Позиция"
        area = f" · {row['area_name']}" if row.get("area_name") else ""
        qty = float(row.get("quantity") or 0)
        lines.append(f"• {name} — {qty:g} {row.get('unit') or ''}{area}")
    if len(rows) > 80:
        lines.append(f"\nЕщё строк: {len(rows) - 80}")
    return "\n".join(lines)

def build_text_report(chat_id: int, request_text: str, user_id: int | None = None) -> str:
    from . import repository as repo

    period = _date_bounds_for_text(request_text)
    if period.error:
        return period.error
    prefs = repo.get_export_preferences(chat_id, user_id)
    lines: list[str] = [f"Отчёт {period.title}"]

    if prefs.get("period_totals"):
        ops = operation_rows(chat_id, period)
        if not ops:
            lines.append("\nДвижения за период пока нет.")
        else:
            current = None
            for row in ops:
                title = OPERATION_LABELS.get(row.get("operation_type") or "", "Запись")
                if title != current:
                    current = title
                    lines.append(f"\n{title}:")
                name = row.get("entity_name") or ENTITY_LABELS.get(row.get("entity_type") or "", "Позиция")
                area = f" · {row['area_name']}" if row.get("area_name") else ""
                qty = float(row.get("total_quantity") or 0)
                lines.append(f"• {name} — {qty:g} {row.get('unit') or ''}{area}")

    if prefs.get("daily_matrix"):
        labels, matrix = movement_matrix_rows(chat_id, period)
        if labels and matrix:
            lines.append("\nПо датам:")
            shown = labels[-10:]
            for label in shown:
                total = sum(float(row["values"].get(label) or 0) for row in matrix)
                lines.append(f"• {label}: {total:g}")
            if len(labels) > len(shown):
                lines.append("• Полная таблица доступна в скачанном отчёте.")

    if prefs.get("capacity"):
        lines.append("\n" + build_all_assembly_capacity_report(chat_id))

    if prefs.get("inventory"):
        inv = inventory_rows(chat_id)
        if inv:
            lines.append("\nОстатки склада:")
            for row in inv[:25]:
                name = row.get("entity_name") or ENTITY_LABELS.get(row.get("entity_type") or "", "Позиция")
                area = f" · {row['area_name']}" if row.get("area_name") else ""
                qty = float(row.get("quantity") or 0)
                lines.append(f"• {name} — {qty:g} {row.get('unit') or ''}{area}")
            if len(inv) > 25:
                lines.append(f"• Ещё строк: {len(inv) - 25}")
        else:
            lines.append("\nСклад пока пустой.")

    if prefs.get("journal"):
        journal = raw_operation_rows(chat_id, period, limit=10)
        if journal:
            lines.append("\nПоследние записи:")
            for row in journal:
                title = OPERATION_LABELS.get(row.get("operation_type") or "", "Запись")
                name = row.get("entity_name") or ENTITY_LABELS.get(row.get("entity_type") or "", "Позиция")
                qty = float(row.get("quantity") or 0)
                lines.append(f"• {row.get('created_at') or ''} · {title}: {name} — {qty:g} {row.get('unit') or ''}")

    if len(lines) == 1:
        lines.append("\nВыберите хотя бы один раздел.")
    return "\n".join(lines)


def _style_sheet(ws) -> None:
    header_fill = PatternFill("solid", fgColor="D9EAF7")
    header_font = Font(bold=True)
    thin = Side(style="thin", color="CCCCCC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = border
            if cell.row == 1:
                cell.fill = header_fill
                cell.font = header_font
    for column_cells in ws.columns:
        col = get_column_letter(column_cells[0].column)
        max_len = 10
        for cell in column_cells:
            max_len = max(max_len, min(len(str(cell.value or "")), 45))
        ws.column_dimensions[col].width = max_len + 2
    ws.freeze_panes = "A2"


def create_xlsx_report(chat_id: int, request_text: str = "отчёт") -> Path:
    period = _date_bounds_for_text(request_text)
    if period.error:
        raise ValueError(period.error)
    wb = Workbook()
    ws = wb.active
    ws.title = "Склад"
    ws.append(["Тип", "Название", "Участок", "Количество", "Ед."])
    for row in inventory_rows(chat_id):
        ws.append([
            ENTITY_LABELS.get(row.get("entity_type") or "", row.get("entity_type") or ""),
            row.get("entity_name") or "",
            row.get("area_name") or "Общий склад",
            float(row.get("quantity") or 0),
            row.get("unit") or "",
        ])
    _style_sheet(ws)

    ws2 = wb.create_sheet("Итоги за период")
    ws2.append(["Период", period.title])
    ws2.append([])
    ws2.append(["Операция", "Тип", "Название", "Участок", "Количество", "Ед.", "Строк"])
    for row in operation_rows(chat_id, period):
        ws2.append([
            OPERATION_LABELS.get(row.get("operation_type") or "", row.get("operation_type") or ""),
            ENTITY_LABELS.get(row.get("entity_type") or "", row.get("entity_type") or ""),
            row.get("entity_name") or "",
            row.get("area_name") or "",
            float(row.get("total_quantity") or 0),
            row.get("unit") or "",
            int(row.get("count_rows") or 0),
        ])
    _style_sheet(ws2)

    ws3 = wb.create_sheet("Журнал")
    ws3.append(["Дата", "Операция", "Тип", "Название", "Участок", "Количество", "Ед.", "Работник", "Группа"])
    for row in raw_operation_rows(chat_id, period):
        ws3.append([
            row.get("created_at") or "",
            OPERATION_LABELS.get(row.get("operation_type") or "", row.get("operation_type") or ""),
            ENTITY_LABELS.get(row.get("entity_type") or "", row.get("entity_type") or ""),
            row.get("entity_name") or "",
            row.get("area_name") or "",
            float(row.get("quantity") or 0),
            row.get("unit") or "",
            row.get("user_id") or "",
            row.get("group_chat_id") or "",
        ])
    _style_sheet(ws3)

    filename = f"uchet_{_safe_name(period.title)}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    path = reports_dir() / filename
    wb.save(path)
    return path


def _register_pdf_font() -> str:
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]
    for font_path in candidates:
        if font_path.exists():
            try:
                pdfmetrics.registerFont(TTFont("BotSans", str(font_path)))
                return "BotSans"
            except Exception:
                continue
    return "Helvetica"


def create_pdf_report(chat_id: int, request_text: str = "отчёт") -> Path:
    period = _date_bounds_for_text(request_text)
    if period.error:
        raise ValueError(period.error)
    font_name = _register_pdf_font()
    filename = f"uchet_{_safe_name(period.title)}_{datetime.now():%Y%m%d_%H%M%S}.pdf"
    path = reports_dir() / filename
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), rightMargin=12*mm, leftMargin=12*mm, topMargin=12*mm, bottomMargin=12*mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleRu", parent=styles["Title"], fontName=font_name, fontSize=16, leading=20)
    normal_style = ParagraphStyle("NormalRu", parent=styles["Normal"], fontName=font_name, fontSize=9, leading=12)
    story = [Paragraph(f"Производственный отчёт {period.title}", title_style), Spacer(1, 6)]

    inv_data = [["Тип", "Название", "Участок", "Количество", "Ед."]]
    for row in inventory_rows(chat_id):
        inv_data.append([
            ENTITY_LABELS.get(row.get("entity_type") or "", row.get("entity_type") or ""),
            row.get("entity_name") or "",
            row.get("area_name") or "Общий склад",
            f"{float(row.get('quantity') or 0):g}",
            row.get("unit") or "",
        ])
    if len(inv_data) == 1:
        inv_data.append(["", "Склад пока пустой", "", "", ""])
    story.append(Paragraph("Склад", normal_style))
    story.append(_pdf_table(inv_data, font_name))
    story.append(Spacer(1, 8))

    ops_data = [["Операция", "Название", "Участок", "Количество", "Ед.", "Строк"]]
    for row in operation_rows(chat_id, period):
        ops_data.append([
            OPERATION_LABELS.get(row.get("operation_type") or "", row.get("operation_type") or ""),
            row.get("entity_name") or ENTITY_LABELS.get(row.get("entity_type") or "", ""),
            row.get("area_name") or "",
            f"{float(row.get('total_quantity') or 0):g}",
            row.get("unit") or "",
            str(row.get("count_rows") or 0),
        ])
    if len(ops_data) == 1:
        ops_data.append(["", "Движения за период пока нет", "", "", "", ""])
    story.append(Paragraph("Итоги за период", normal_style))
    story.append(_pdf_table(ops_data, font_name))
    doc.build(story)
    return path


def _pdf_table(data: list[list[str]], font_name: str) -> Table:
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9EAF7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (3, 1), (3, -1), "RIGHT"),
    ]))
    return table


def build_assembly_capacity_report(chat_id: int, request_text: str) -> str:
    from .matcher import confident_match
    from . import repository as repo

    # Убираем служебные слова, чтобы остался запрос по изделию.
    key_text = request_text
    for word in ["сколько", "можно", "собрать", "изготовить", "сделать", "готово", "изделий", "изделие"]:
        key_text = re.sub(rf"\b{word}\b", " ", key_text, flags=re.IGNORECASE)
    match, _ = confident_match(chat_id, key_text.strip() or request_text, allowed_types={"product"})
    if not match:
        return "Не нашёл изделие. Уточните название."
    components = repo.list_product_components(match.target_id)
    if not components:
        return f"У изделия «{match.name}» пока не задан состав."
    lines = [f"Можно собрать: {match.name}"]
    possible_values: list[float] = []
    missing: list[str] = []
    for comp in components:
        row = db.fetchone(
            """
            SELECT COALESCE(SUM(quantity),0) AS qty
            FROM inventory
            WHERE chat_id=? AND entity_type='component' AND entity_id=?
            """,
            (chat_id, int(comp["component_id"])),
        )
        stock_qty = float(row["qty"] if row else 0)
        need = float(comp["quantity"] or 0)
        can_make = stock_qty // need if need > 0 else 0
        possible_values.append(can_make)
        lines.append(f"• {comp['name']}: есть {stock_qty:g}, нужно {need:g} на 1")
        if stock_qty < need:
            missing.append(f"• {comp['name']} — не хватает {need - stock_qty:g}")
    possible = int(min(possible_values)) if possible_values else 0
    lines.insert(1, f"Итого сейчас: {possible} шт.")
    if missing:
        lines.append("\nДля одной полной сборки не хватает:")
        lines.extend(missing)
    return "\n".join(lines)

# --- Дополнение: общие комплектующие, расчёт сборки по всем изделиям и настраиваемые файлы ---


def _period_start_end(period: PeriodFilter) -> tuple[date, date]:
    today = datetime.now().date()
    if len(period.params) >= 2:
        start = date.fromisoformat(str(period.params[0])[:10])
        exclusive_end = date.fromisoformat(str(period.params[1])[:10])
        return start, exclusive_end - timedelta(days=1)
    if len(period.params) == 1:
        start = date.fromisoformat(str(period.params[0])[:10])
        return start, today
    return today, today


def _bucket_labels_for_period(period: PeriodFilter) -> tuple[str, list[str]]:
    start, end = _period_start_end(period)
    day_count = (end - start).days + 1
    if day_count <= 62:
        labels: list[str] = []
        current = start
        while current <= end:
            labels.append(current.strftime("%d.%m.%Y"))
            current += timedelta(days=1)
        return "day", labels
    labels = []
    current = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while current <= last:
        labels.append(current.strftime("%m.%Y"))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return "month", labels


def movement_matrix_rows(chat_id: int, period: PeriodFilter) -> tuple[list[str], list[dict]]:
    bucket_type, labels = _bucket_labels_for_period(period)
    if bucket_type == "day":
        bucket_sql = "strftime('%d.%m.%Y', o.created_at)"
    else:
        bucket_sql = "strftime('%m.%Y', o.created_at)"
    rows = db.fetchall(
        f"""
        SELECT o.operation_type,o.entity_type,e.name AS entity_name,o.unit,{bucket_sql} AS bucket,
               SUM(o.quantity) AS total_quantity
        FROM operations o
        LEFT JOIN entities e ON e.id=o.entity_id
        WHERE o.chat_id=? AND {period.where_sql}
        GROUP BY o.operation_type,o.entity_type,o.entity_id,o.unit,bucket
        ORDER BY o.operation_type,e.name,bucket
        """,
        (chat_id, *period.params),
    )
    grouped: dict[tuple[str, str, str, str], dict] = {}
    for raw_row in rows:
        row = dict(raw_row)
        key = (
            str(row.get("operation_type") or ""),
            str(row.get("entity_type") or ""),
            str(row.get("entity_name") or ""),
            str(row.get("unit") or ""),
        )
        item = grouped.setdefault(key, {
            "operation_type": key[0],
            "entity_type": key[1],
            "entity_name": key[2],
            "unit": key[3],
            "values": {label: 0.0 for label in labels},
        })
        bucket = str(row.get("bucket") or "")
        if bucket in item["values"]:
            item["values"][bucket] = float(row.get("total_quantity") or 0)
    return labels, list(grouped.values())


def _component_stock(chat_id: int, component_id: int) -> float:
    row = db.fetchone(
        """
        SELECT COALESCE(SUM(quantity),0) AS qty
        FROM inventory
        WHERE chat_id=? AND entity_type='component' AND entity_id=?
        """,
        (chat_id, component_id),
    )
    return float(row["qty"] if row else 0)


def product_capacity_rows(chat_id: int) -> list[dict]:
    from . import repository as repo

    rows: list[dict] = []
    for product_pack in repo.all_products_with_components(chat_id):
        product = product_pack["product"]
        components = product_pack["components"]
        if not components:
            rows.append({
                "product_name": product.name,
                "possible": "состав не задан",
                "component_name": "",
                "stock": "",
                "need": "",
                "missing_next": "",
                "unit": "",
            })
            continue
        possible_values: list[float] = []
        component_info: list[dict] = []
        for comp in components:
            stock_qty = _component_stock(chat_id, int(comp["component_id"]))
            need = float(comp["quantity"] or 0)
            can_make = stock_qty // need if need > 0 else 0
            possible_values.append(can_make)
            component_info.append({
                "name": str(comp.get("name") or ""),
                "stock": stock_qty,
                "need": need,
                "unit": str(comp.get("default_unit") or "шт"),
            })
        possible = int(min(possible_values)) if possible_values else 0
        for comp in component_info:
            missing_next = max(0.0, comp["need"] * (possible + 1) - comp["stock"])
            rows.append({
                "product_name": product.name,
                "possible": possible,
                "component_name": comp["name"],
                "stock": comp["stock"],
                "need": comp["need"],
                "missing_next": missing_next,
                "unit": comp["unit"],
            })
    return rows


def build_all_assembly_capacity_report(chat_id: int) -> str:
    rows = product_capacity_rows(chat_id)
    if not rows:
        return "Изделия пока не созданы."
    lines = ["Расчёт сборки по изделиям"]
    current = None
    for row in rows:
        product_name = str(row["product_name"])
        if product_name != current:
            current = product_name
            lines.append(f"\n{product_name}: можно собрать {row['possible']}")
        if row.get("component_name"):
            missing = float(row.get("missing_next") or 0)
            extra = f", для ещё 1 не хватает {missing:g} {row.get('unit') or ''}" if missing > 0 else ""
            lines.append(f"• {row['component_name']}: есть {float(row['stock']):g}, нужно {float(row['need']):g}{extra}")
        else:
            lines.append("• Состав пока не задан.")
    return "\n".join(lines)


def build_assembly_capacity_report(chat_id: int, request_text: str) -> str:
    from .matcher import confident_match
    from . import repository as repo

    key = normalize_key(request_text)
    if any(word in key for word in ("все", "всё", "кажд", "общ")) or key.strip() in {"сколько можно собрать", "расчет сборки", "расчёт сборки"}:
        return build_all_assembly_capacity_report(chat_id)
    key_text = request_text
    for word in ["сколько", "можно", "собрать", "изготовить", "сделать", "готово", "изделий", "изделие", "расчет", "расчёт"]:
        key_text = re.sub(rf"\b{word}\b", " ", key_text, flags=re.IGNORECASE)
    match, _ = confident_match(chat_id, key_text.strip() or request_text, allowed_types={"product"})
    if not match:
        return build_all_assembly_capacity_report(chat_id)
    components = repo.list_product_components(match.target_id)
    if not components:
        return f"У изделия «{match.name}» пока не задан состав."
    possible_values: list[float] = []
    lines = [f"Можно собрать: {match.name}"]
    for comp in components:
        stock_qty = _component_stock(chat_id, int(comp["component_id"]))
        need = float(comp["quantity"] or 0)
        can_make = stock_qty // need if need > 0 else 0
        possible_values.append(can_make)
        lines.append(f"• {comp['name']}: есть {stock_qty:g}, нужно {need:g} на 1")
    possible = int(min(possible_values)) if possible_values else 0
    lines.insert(1, f"Итого сейчас: {possible} шт.")
    missing_next: list[str] = []
    for comp in components:
        stock_qty = _component_stock(chat_id, int(comp["component_id"]))
        need = float(comp["quantity"] or 0)
        missing = max(0.0, need * (possible + 1) - stock_qty)
        if missing > 0:
            missing_next.append(f"• {comp['name']} — {missing:g} {comp.get('default_unit') or 'шт'}")
    if missing_next:
        lines.append("\nДля ещё 1 изделия не хватает:")
        lines.extend(missing_next)
    return "\n".join(lines)


def create_xlsx_report(chat_id: int, request_text: str = "отчёт", user_id: int | None = None) -> Path:
    from . import repository as repo

    period = _date_bounds_for_text(request_text)
    if period.error:
        raise ValueError(period.error)
    prefs = repo.get_export_preferences(chat_id, user_id)
    wb = Workbook()
    first_sheet = True

    def get_sheet(title: str):
        nonlocal first_sheet
        if first_sheet:
            ws = wb.active
            ws.title = title
            first_sheet = False
        else:
            ws = wb.create_sheet(title)
        return ws

    if prefs.get("inventory"):
        ws = get_sheet("Склад")
        ws.append(["Тип", "Название", "Участок", "Количество", "Ед."])
        for row in inventory_rows(chat_id):
            ws.append([
                ENTITY_LABELS.get(row.get("entity_type") or "", row.get("entity_type") or ""),
                row.get("entity_name") or "",
                row.get("area_name") or "Общий склад",
                float(row.get("quantity") or 0),
                row.get("unit") or "",
            ])
        _style_sheet(ws)

    if prefs.get("period_totals"):
        ws = get_sheet("Итоги за период")
        ws.append(["Период", period.title])
        ws.append([])
        ws.append(["Операция", "Тип", "Название", "Участок", "Количество", "Ед.", "Строк"])
        for row in operation_rows(chat_id, period):
            ws.append([
                OPERATION_LABELS.get(row.get("operation_type") or "", row.get("operation_type") or ""),
                ENTITY_LABELS.get(row.get("entity_type") or "", row.get("entity_type") or ""),
                row.get("entity_name") or "",
                row.get("area_name") or "",
                float(row.get("total_quantity") or 0),
                row.get("unit") or "",
                int(row.get("count_rows") or 0),
            ])
        _style_sheet(ws)

    if prefs.get("daily_matrix"):
        ws = get_sheet("По датам")
        labels, matrix = movement_matrix_rows(chat_id, period)
        ws.append(["Операция", "Название", "Ед.", *labels, "Итого"])
        for row in matrix:
            values = [float(row["values"].get(label) or 0) for label in labels]
            ws.append([
                OPERATION_LABELS.get(row.get("operation_type") or "", row.get("operation_type") or ""),
                row.get("entity_name") or ENTITY_LABELS.get(row.get("entity_type") or "", ""),
                row.get("unit") or "",
                *values,
                sum(values),
            ])
        _style_sheet(ws)

    if prefs.get("capacity"):
        ws = get_sheet("Расчёт сборки")
        ws.append(["Изделие", "Можно собрать", "Комплектующая", "Есть", "Нужно на 1", "Не хватает для ещё 1", "Ед."])
        for row in product_capacity_rows(chat_id):
            ws.append([
                row.get("product_name") or "",
                row.get("possible") if row.get("possible") != "" else "",
                row.get("component_name") or "",
                row.get("stock") if row.get("stock") != "" else "",
                row.get("need") if row.get("need") != "" else "",
                row.get("missing_next") if row.get("missing_next") != "" else "",
                row.get("unit") or "",
            ])
        _style_sheet(ws)

    if prefs.get("journal"):
        ws = get_sheet("Журнал")
        ws.append(["Дата", "Операция", "Тип", "Название", "Участок", "Количество", "Ед.", "Работник", "Группа"])
        for row in raw_operation_rows(chat_id, period):
            ws.append([
                row.get("created_at") or "",
                OPERATION_LABELS.get(row.get("operation_type") or "", row.get("operation_type") or ""),
                ENTITY_LABELS.get(row.get("entity_type") or "", row.get("entity_type") or ""),
                row.get("entity_name") or "",
                row.get("area_name") or "",
                float(row.get("quantity") or 0),
                row.get("unit") or "",
                row.get("user_id") or "",
                row.get("group_chat_id") or "",
            ])
        _style_sheet(ws)

    if first_sheet:
        ws = wb.active
        ws.title = "Отчёт"
        ws.append(["Файл пустой", "Не выбран ни один раздел."])
        _style_sheet(ws)
    filename = f"uchet_{_safe_name(period.title)}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    path = reports_dir() / filename
    wb.save(path)
    return path


def _pdf_table(data: list[list[str]], font_name: str) -> Table:
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9EAF7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
    ]))
    return table


def create_pdf_report(chat_id: int, request_text: str = "отчёт", user_id: int | None = None) -> Path:
    from . import repository as repo

    period = _date_bounds_for_text(request_text)
    if period.error:
        raise ValueError(period.error)
    prefs = repo.get_export_preferences(chat_id, user_id)
    font_name = _register_pdf_font()
    filename = f"uchet_{_safe_name(period.title)}_{datetime.now():%Y%m%d_%H%M%S}.pdf"
    path = reports_dir() / filename
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), rightMargin=10*mm, leftMargin=10*mm, topMargin=10*mm, bottomMargin=10*mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleRu2", parent=styles["Title"], fontName=font_name, fontSize=15, leading=18)
    normal_style = ParagraphStyle("NormalRu2", parent=styles["Normal"], fontName=font_name, fontSize=9, leading=12)
    story = [Paragraph(f"Производственный отчёт {period.title}", title_style), Spacer(1, 6)]

    if prefs.get("inventory"):
        inv_data = [["Тип", "Название", "Участок", "Количество", "Ед."]]
        for row in inventory_rows(chat_id):
            inv_data.append([
                ENTITY_LABELS.get(row.get("entity_type") or "", row.get("entity_type") or ""),
                row.get("entity_name") or "",
                row.get("area_name") or "Общий склад",
                f"{float(row.get('quantity') or 0):g}",
                row.get("unit") or "",
            ])
        if len(inv_data) == 1:
            inv_data.append(["", "Склад пока пустой", "", "", ""])
        story.append(Paragraph("Склад", normal_style))
        story.append(_pdf_table(inv_data[:80], font_name))
        story.append(Spacer(1, 8))

    if prefs.get("period_totals"):
        ops_data = [["Операция", "Название", "Участок", "Количество", "Ед.", "Строк"]]
        for row in operation_rows(chat_id, period):
            ops_data.append([
                OPERATION_LABELS.get(row.get("operation_type") or "", row.get("operation_type") or ""),
                row.get("entity_name") or ENTITY_LABELS.get(row.get("entity_type") or "", ""),
                row.get("area_name") or "",
                f"{float(row.get('total_quantity') or 0):g}",
                row.get("unit") or "",
                str(row.get("count_rows") or 0),
            ])
        if len(ops_data) == 1:
            ops_data.append(["", "Движения за период пока нет", "", "", "", ""])
        story.append(Paragraph("Итоги за период", normal_style))
        story.append(_pdf_table(ops_data[:80], font_name))
        story.append(Spacer(1, 8))

    if prefs.get("daily_matrix"):
        labels, matrix = movement_matrix_rows(chat_id, period)
        shown_labels = labels[-12:]
        data = [["Операция", "Название", *shown_labels, "Итого"]]
        for row in matrix[:60]:
            values = [float(row["values"].get(label) or 0) for label in shown_labels]
            total = sum(float(row["values"].get(label) or 0) for label in labels)
            data.append([
                OPERATION_LABELS.get(row.get("operation_type") or "", row.get("operation_type") or ""),
                row.get("entity_name") or ENTITY_LABELS.get(row.get("entity_type") or "", ""),
                *[f"{v:g}" for v in values],
                f"{total:g}",
            ])
        if len(data) == 1:
            data.append(["", "Движения по датам пока нет", *["" for _ in shown_labels], ""])
        story.append(Paragraph("По датам", normal_style))
        if len(labels) > len(shown_labels):
            story.append(Paragraph("В PDF показаны последние столбцы периода. Полная таблица доступна в Excel.", normal_style))
        story.append(_pdf_table(data, font_name))
        story.append(Spacer(1, 8))

    if prefs.get("capacity"):
        cap_data = [["Изделие", "Можно собрать", "Комплектующая", "Есть", "Нужно", "Не хватает", "Ед."]]
        for row in product_capacity_rows(chat_id):
            cap_data.append([
                str(row.get("product_name") or ""),
                str(row.get("possible") or ""),
                str(row.get("component_name") or ""),
                f"{float(row.get('stock') or 0):g}" if row.get("stock") != "" else "",
                f"{float(row.get('need') or 0):g}" if row.get("need") != "" else "",
                f"{float(row.get('missing_next') or 0):g}" if row.get("missing_next") != "" else "",
                str(row.get("unit") or ""),
            ])
        if len(cap_data) == 1:
            cap_data.append(["", "Изделия пока не созданы", "", "", "", "", ""])
        story.append(Paragraph("Расчёт сборки", normal_style))
        story.append(_pdf_table(cap_data[:100], font_name))
        story.append(Spacer(1, 8))

    if prefs.get("journal"):
        journal_data = [["Дата", "Операция", "Название", "Участок", "Количество", "Ед."]]
        for row in raw_operation_rows(chat_id, period, limit=120):
            journal_data.append([
                row.get("created_at") or "",
                OPERATION_LABELS.get(row.get("operation_type") or "", row.get("operation_type") or ""),
                row.get("entity_name") or "",
                row.get("area_name") or "",
                f"{float(row.get('quantity') or 0):g}",
                row.get("unit") or "",
            ])
        if len(journal_data) == 1:
            journal_data.append(["", "Записей пока нет", "", "", "", ""])
        story.append(Paragraph("Журнал", normal_style))
        story.append(_pdf_table(journal_data, font_name))

    doc.build(story)
    return path


# --- Дополнение: универсальные форматы файлов для разных устройств ---


def _period_for_export(request_text: str) -> PeriodFilter:
    period = _date_bounds_for_text(request_text)
    if period.error:
        raise ValueError(period.error)
    return period


def _write_section_csv(writer: csv.writer, title: str, header: list[str], rows: list[list[object]]) -> None:
    writer.writerow([title])
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    writer.writerow([])


def _inventory_table(chat_id: int) -> list[list[object]]:
    return [[
        ENTITY_LABELS.get(row.get("entity_type") or "", row.get("entity_type") or ""),
        row.get("entity_name") or "",
        row.get("area_name") or "Общий склад",
        float(row.get("quantity") or 0),
        row.get("unit") or "",
    ] for row in inventory_rows(chat_id)]


def _period_totals_table(chat_id: int, period: PeriodFilter) -> list[list[object]]:
    return [[
        OPERATION_LABELS.get(row.get("operation_type") or "", row.get("operation_type") or ""),
        ENTITY_LABELS.get(row.get("entity_type") or "", row.get("entity_type") or ""),
        row.get("entity_name") or "",
        row.get("area_name") or "",
        float(row.get("total_quantity") or 0),
        row.get("unit") or "",
        int(row.get("count_rows") or 0),
    ] for row in operation_rows(chat_id, period)]


def _capacity_table(chat_id: int) -> list[list[object]]:
    return [[
        row.get("product_name") or "",
        row.get("possible") if row.get("possible") != "" else "",
        row.get("component_name") or "",
        row.get("stock") if row.get("stock") != "" else "",
        row.get("need") if row.get("need") != "" else "",
        row.get("missing_next") if row.get("missing_next") != "" else "",
        row.get("unit") or "",
    ] for row in product_capacity_rows(chat_id)]


def _journal_table(chat_id: int, period: PeriodFilter, limit: int = 5000) -> list[list[object]]:
    return [[
        row.get("created_at") or "",
        OPERATION_LABELS.get(row.get("operation_type") or "", row.get("operation_type") or ""),
        ENTITY_LABELS.get(row.get("entity_type") or "", row.get("entity_type") or ""),
        row.get("entity_name") or "",
        row.get("area_name") or "",
        float(row.get("quantity") or 0),
        row.get("unit") or "",
        row.get("user_id") or "",
        row.get("group_chat_id") or "",
    ] for row in raw_operation_rows(chat_id, period, limit=limit)]


def _daily_matrix_table(chat_id: int, period: PeriodFilter) -> tuple[list[str], list[list[object]]]:
    labels, matrix = movement_matrix_rows(chat_id, period)
    rows: list[list[object]] = []
    for row in matrix:
        values = [float(row["values"].get(label) or 0) for label in labels]
        rows.append([
            OPERATION_LABELS.get(row.get("operation_type") or "", row.get("operation_type") or ""),
            row.get("entity_name") or ENTITY_LABELS.get(row.get("entity_type") or "", ""),
            row.get("unit") or "",
            *values,
            sum(values),
        ])
    return labels, rows


def create_csv_report(chat_id: int, request_text: str = "отчёт", user_id: int | None = None) -> Path:
    from . import repository as repo

    period = _period_for_export(request_text)
    prefs = repo.get_export_preferences(chat_id, user_id)
    filename = f"uchet_{_safe_name(period.title)}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    path = reports_dir() / filename
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow([f"Производственный отчёт {period.title}"])
        writer.writerow([])
        if prefs.get("inventory"):
            _write_section_csv(writer, "Склад", ["Тип", "Название", "Участок", "Количество", "Ед."], _inventory_table(chat_id))
        if prefs.get("period_totals"):
            _write_section_csv(writer, "Итоги за период", ["Операция", "Тип", "Название", "Участок", "Количество", "Ед.", "Строк"], _period_totals_table(chat_id, period))
        if prefs.get("daily_matrix"):
            labels, rows = _daily_matrix_table(chat_id, period)
            _write_section_csv(writer, "По датам", ["Операция", "Название", "Ед.", *labels, "Итого"], rows)
        if prefs.get("capacity"):
            _write_section_csv(writer, "Расчёт сборки", ["Изделие", "Можно собрать", "Комплектующая", "Есть", "Нужно на 1", "Не хватает для ещё 1", "Ед."], _capacity_table(chat_id))
        if prefs.get("journal"):
            _write_section_csv(writer, "Журнал", ["Дата", "Операция", "Тип", "Название", "Участок", "Количество", "Ед.", "Работник", "Группа"], _journal_table(chat_id, period))
    return path


def create_txt_report(chat_id: int, request_text: str = "отчёт", user_id: int | None = None) -> Path:
    period = _period_for_export(request_text)
    filename = f"uchet_{_safe_name(period.title)}_{datetime.now():%Y%m%d_%H%M%S}.txt"
    path = reports_dir() / filename
    text = build_text_report(chat_id, request_text, user_id=user_id)
    path.write_text(text, encoding="utf-8")
    return path


def _html_table(title: str, header: list[str], rows: list[list[object]]) -> str:
    parts = [f"<h2>{html.escape(title)}</h2>", "<div class='table-wrap'><table><thead><tr>"]
    for cell in header:
        parts.append(f"<th>{html.escape(str(cell))}</th>")
    parts.append("</tr></thead><tbody>")
    if not rows:
        parts.append(f"<tr><td colspan='{len(header)}'>Нет данных</td></tr>")
    else:
        for row in rows:
            parts.append("<tr>")
            for cell in row:
                parts.append(f"<td>{html.escape(str(cell))}</td>")
            parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def create_html_report(chat_id: int, request_text: str = "отчёт", user_id: int | None = None) -> Path:
    from . import repository as repo

    period = _period_for_export(request_text)
    prefs = repo.get_export_preferences(chat_id, user_id)
    filename = f"uchet_{_safe_name(period.title)}_{datetime.now():%Y%m%d_%H%M%S}.html"
    path = reports_dir() / filename
    sections: list[str] = []
    if prefs.get("inventory"):
        sections.append(_html_table("Склад", ["Тип", "Название", "Участок", "Количество", "Ед."], _inventory_table(chat_id)))
    if prefs.get("period_totals"):
        sections.append(_html_table("Итоги за период", ["Операция", "Тип", "Название", "Участок", "Количество", "Ед.", "Строк"], _period_totals_table(chat_id, period)))
    if prefs.get("daily_matrix"):
        labels, rows = _daily_matrix_table(chat_id, period)
        sections.append(_html_table("По датам", ["Операция", "Название", "Ед.", *labels, "Итого"], rows))
    if prefs.get("capacity"):
        sections.append(_html_table("Расчёт сборки", ["Изделие", "Можно собрать", "Комплектующая", "Есть", "Нужно на 1", "Не хватает для ещё 1", "Ед."], _capacity_table(chat_id)))
    if prefs.get("journal"):
        sections.append(_html_table("Журнал", ["Дата", "Операция", "Тип", "Название", "Участок", "Количество", "Ед.", "Работник", "Группа"], _journal_table(chat_id, period, limit=1000)))
    doc = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Производственный отчёт {html.escape(period.title)}</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 16px; color: #111; }}
h1 {{ font-size: 22px; margin-bottom: 8px; }}
h2 {{ font-size: 17px; margin-top: 22px; }}
.table-wrap {{ overflow-x: auto; margin-bottom: 18px; }}
table {{ border-collapse: collapse; width: 100%; min-width: 720px; }}
th, td {{ border: 1px solid #cfcfcf; padding: 6px 8px; vertical-align: top; }}
th {{ background: #e8f1fb; text-align: left; }}
td:nth-child(4), td:nth-child(5), td:nth-child(6), td:nth-child(7) {{ text-align: right; }}
@media print {{ body {{ margin: 8mm; }} .table-wrap {{ overflow: visible; }} table {{ font-size: 10px; }} }}
</style>
</head>
<body>
<h1>Производственный отчёт {html.escape(period.title)}</h1>
<p>Открывается в браузере на телефоне, планшете и компьютере.</p>
{''.join(sections) if sections else '<p>Не выбран ни один раздел.</p>'}
</body>
</html>"""
    path.write_text(doc, encoding="utf-8")
    return path


def create_universal_report_zip(chat_id: int, request_text: str = "отчёт", user_id: int | None = None) -> Path:
    period = _period_for_export(request_text)
    files = [
        create_xlsx_report(chat_id, request_text, user_id=user_id),
        create_pdf_report(chat_id, request_text, user_id=user_id),
        create_csv_report(chat_id, request_text, user_id=user_id),
        create_html_report(chat_id, request_text, user_id=user_id),
        create_txt_report(chat_id, request_text, user_id=user_id),
    ]
    filename = f"uchet_universal_{_safe_name(period.title)}_{datetime.now():%Y%m%d_%H%M%S}.zip"
    path = reports_dir() / filename
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in files:
            zf.write(file_path, arcname=file_path.name)
    return path
