from __future__ import annotations

import io
import textwrap
from typing import Iterable

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from . import reporting
from . import repository as repo


ENTITY_TYPE_NAMES = {
    "component": "Комплектующая",
    "product": "Изделие",
    "material": "Сырьё",
    "stock_item": "Складская позиция",
    "meter": "Счётчик",
}


def _fit_text(c: canvas.Canvas, text: str, font: str, max_width: float, *, start_size: float = 9, min_size: float = 6) -> float:
    size = start_size
    while size > min_size and c.stringWidth(text, font, size) > max_width:
        size -= 0.5
    return size


def build_entity_labels_pdf(
    chat_id: int,
    entity_ids: Iterable[int],
    *,
    code_type: str = "qr",
    copies: int = 1,
    template: dict | None = None,
) -> bytes:
    ids = sorted({int(value) for value in entity_ids if int(value) > 0})
    rows = repo.list_entity_codes(chat_id, ids)
    primary: dict[int, dict] = {}
    for row in rows:
        entity_id = int(row["entity_id"])
        if entity_id not in primary or int(row.get("is_primary") or 0):
            primary[entity_id] = row
    if not primary:
        raise ValueError("У выбранных позиций нет назначенных кодов.")

    labels: list[dict] = []
    for entity_id in ids:
        row = primary.get(entity_id)
        if row:
            labels.extend([row] * max(1, min(int(copies), 20)))
    if not labels:
        raise ValueError("Не найдены позиции с кодами.")

    font_name = reporting._register_pdf_font()
    output = io.BytesIO()
    template = dict(template or {})
    page_mode = str(template.get("page_mode") or "a4")
    if page_mode == "label":
        label_w = max(20.0, float(template.get("label_width_mm") or 63)) * mm
        label_h = max(15.0, float(template.get("label_height_mm") or 32)) * mm
        page_w, page_h = label_w, label_h
        cols = rows_per_page = 1
        margin_x = margin_y = gap_x = gap_y = 0
    else:
        page_w, page_h = A4
        cols = max(1, min(int(template.get("columns_count") or 3), 8))
        rows_per_page = max(1, min(int(template.get("rows_count") or 8), 20))
        margin_x = max(0.0, float(template.get("margin_x_mm") or 8)) * mm
        margin_y = max(0.0, float(template.get("margin_y_mm") or 8)) * mm
        gap_x = max(0.0, float(template.get("gap_x_mm") or 3)) * mm
        gap_y = max(0.0, float(template.get("gap_y_mm") or 3)) * mm
        requested_w = max(20.0, float(template.get("label_width_mm") or 0)) * mm if template.get("label_width_mm") else 0
        requested_h = max(15.0, float(template.get("label_height_mm") or 0)) * mm if template.get("label_height_mm") else 0
        max_w = (page_w - 2 * margin_x - gap_x * (cols - 1)) / cols
        max_h = (page_h - 2 * margin_y - gap_y * (rows_per_page - 1)) / rows_per_page
        label_w = requested_w or max_w
        label_h = requested_h or max_h
        if label_w > max_w + 0.1 or label_h > max_h + 0.1:
            raise ValueError("Размер этикетки не помещается в выбранную сетку A4.")
    c = canvas.Canvas(output, pagesize=(page_w, page_h))
    code_size = max(8.0, min(float(template.get("code_size_mm") or 21), min(label_w/mm, label_h/mm))) * mm
    if template.get("code_type") in {"qr", "code128"}:
        code_type = str(template["code_type"])

    for index, item in enumerate(labels):
        slot = index % (cols * rows_per_page)
        if index and slot == 0:
            c.showPage()
        row_no = slot // cols
        col_no = slot % cols
        x = margin_x + col_no * (label_w + gap_x)
        y = page_h - margin_y - (row_no + 1) * label_h - row_no * gap_y
        c.roundRect(x, y, label_w, label_h, 2 * mm, stroke=1, fill=0)

        code = str(item.get("code") or "").strip()
        barcode_kind = "QR" if code_type == "qr" else "Code128"
        try:
            if barcode_kind == "QR":
                qr_size = min(code_size, max(8 * mm, label_h - 6 * mm), max(8 * mm, label_w * 0.45))
                drawing = createBarcodeDrawing("QR", value=code, width=qr_size, height=qr_size, barBorder=0)
                renderPDF.draw(drawing, c, x + 3 * mm, y + (label_h - qr_size) / 2)
                text_x = x + qr_size + 6 * mm
                text_w = max(12 * mm, label_w - qr_size - 9 * mm)
            else:
                drawing = createBarcodeDrawing("Code128", value=code, width=label_w - 8 * mm, height=12 * mm, humanReadable=False)
                renderPDF.draw(drawing, c, x + 4 * mm, y + 4 * mm)
                text_x = x + 4 * mm
                text_w = label_w - 8 * mm
        except Exception:
            qr_size = min(code_size, max(8 * mm, label_h - 6 * mm), max(8 * mm, label_w * 0.45))
            drawing = createBarcodeDrawing("QR", value=code, width=qr_size, height=qr_size, barBorder=0)
            renderPDF.draw(drawing, c, x + 3 * mm, y + (label_h - qr_size) / 2)
            text_x = x + qr_size + 6 * mm
            text_w = max(12 * mm, label_w - qr_size - 9 * mm)

        name = str(item.get("entity_name") or "Позиция")
        lines = textwrap.wrap(name, width=24)[:3] or [name]
        cursor_y = y + label_h - 6 * mm
        for line in lines:
            size = _fit_text(c, line, font_name, text_w, start_size=9, min_size=6)
            c.setFont(font_name, size)
            c.drawString(text_x, cursor_y, line)
            cursor_y -= 4 * mm
        c.setFont(font_name, 6.5)
        c.drawString(text_x, max(y + 9 * mm, cursor_y - 1 * mm), ENTITY_TYPE_NAMES.get(str(item.get("entity_type") or ""), "Позиция"))
        c.setFont(font_name, 7.5)
        c.drawString(text_x, y + 4 * mm if barcode_kind == "QR" else y + 18 * mm, code[:42])

    c.save()
    return output.getvalue()
