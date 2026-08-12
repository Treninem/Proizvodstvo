from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .. import db
from . import repository as repo


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _fmt(value: object) -> str:
    number = _num(value)
    if abs(number - int(number)) < 0.000001:
        return f"{int(number):,}".replace(",", " ")
    return f"{number:,.2f}".replace(",", " ").replace(".", ",")


def _scope(chat_id: int) -> int:
    return repo.resolve_scope_chat_id(int(chat_id))


def inventory_by_type(chat_id: int) -> dict[str, list[dict[str, Any]]]:
    scope = _scope(chat_id)
    rows = db.fetchall(
        """
        SELECT i.entity_type,i.entity_id,e.name,e.default_unit,COALESCE(SUM(i.quantity),0) AS qty
        FROM inventory i
        LEFT JOIN entities e ON e.id=i.entity_id
        WHERE i.chat_id=? AND e.is_archived=0
        GROUP BY i.entity_type,i.entity_id,e.name,e.default_unit
        ORDER BY i.entity_type,e.name
        """,
        (scope,),
    )
    result: dict[str, list[dict[str, Any]]] = {"component": [], "material": [], "product": [], "stock_item": [], "meter": []}
    for row in rows:
        key = str(row["entity_type"] or "stock_item")
        result.setdefault(key, []).append(
            {
                "id": int(row["entity_id"]),
                "name": str(row["name"] or row["entity_id"]),
                "qty": _num(row["qty"]),
                "qty_text": _fmt(row["qty"]),
                "unit": str(row["default_unit"] or "шт"),
            }
        )
    return result


def inventory_by_area(chat_id: int) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    rows = db.fetchall(
        """
        SELECT i.area_id,COALESCE(a.name,'Без площадки') AS area_name,
               i.entity_type,i.entity_id,e.name,e.default_unit,COALESCE(SUM(i.quantity),0) AS qty
        FROM inventory i
        LEFT JOIN areas a ON a.id=i.area_id
        LEFT JOIN entities e ON e.id=i.entity_id
        WHERE i.chat_id=? AND e.is_archived=0
        GROUP BY i.area_id,area_name,i.entity_type,i.entity_id,e.name,e.default_unit
        HAVING ABS(COALESCE(SUM(i.quantity),0))>0.000001
        ORDER BY area_name,i.entity_type,e.name
        """,
        (scope,),
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "area_id": row["area_id"],
                "area_name": str(row["area_name"] or "Без площадки"),
                "entity_type": str(row["entity_type"] or ""),
                "entity_id": int(row["entity_id"]),
                "name": str(row["name"] or row["entity_id"]),
                "qty": _num(row["qty"]),
                "qty_text": _fmt(row["qty"]),
                "unit": str(row["default_unit"] or "шт"),
            }
        )
    return result


def area_summary(chat_id: int) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    rows = db.fetchall(
        """
        SELECT COALESCE(a.name,'Без площадки') AS area_name,
               SUM(CASE WHEN i.entity_type='component' THEN i.quantity ELSE 0 END) AS components,
               SUM(CASE WHEN i.entity_type='material' THEN i.quantity ELSE 0 END) AS materials,
               SUM(CASE WHEN i.entity_type='product' THEN i.quantity ELSE 0 END) AS products
        FROM inventory i
        LEFT JOIN areas a ON a.id=i.area_id
        WHERE i.chat_id=?
        GROUP BY i.area_id,area_name
        ORDER BY area_name
        """,
        (scope,),
    )
    return [
        {
            "area_name": str(r["area_name"] or "Без площадки"),
            "components": _num(r["components"]),
            "components_text": _fmt(r["components"]),
            "materials": _num(r["materials"]),
            "materials_text": _fmt(r["materials"]),
            "products": _num(r["products"]),
            "products_text": _fmt(r["products"]),
        }
        for r in rows
    ]


def month_totals(chat_id: int) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(sep=" ")
    rows = db.fetchall(
        """
        SELECT o.operation_type,o.entity_type,o.entity_id,e.name,COALESCE(SUM(o.quantity),0) AS qty,o.unit
        FROM operations o
        LEFT JOIN entities e ON e.id=o.entity_id
        WHERE o.chat_id=? AND o.created_at>=?
        GROUP BY o.operation_type,o.entity_type,o.entity_id,e.name,o.unit
        ORDER BY o.operation_type,e.name
        """,
        (scope, start),
    )
    return [
        {
            "type": str(r["operation_type"] or ""),
            "entity_type": str(r["entity_type"] or ""),
            "name": str(r["name"] or ""),
            "qty": _num(r["qty"]),
            "qty_text": _fmt(r["qty"]),
            "unit": str(r["unit"] or "шт"),
        }
        for r in rows
    ]


def material_days(chat_id: int) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    materials = repo.list_entities(scope, {"material"})
    result: list[dict[str, Any]] = []
    for item in materials:
        settings = repo.get_material_stock_settings(scope, item.id)
        average_days = int(settings.get("average_days") or 14)
        min_work_days = float(settings.get("min_work_days") or 5)
        since = (datetime.now() - timedelta(days=max(average_days + 7, 21))).isoformat(sep=" ")
        stock = repo.inventory_quantity(scope, "material", item.id, item.default_unit)
        row = db.fetchone(
            """
            SELECT COALESCE(SUM(quantity),0) AS qty,COUNT(DISTINCT DATE(created_at)) AS days
            FROM operations
            WHERE chat_id=? AND operation_type='material_out' AND entity_id=? AND created_at>=?
            """,
            (scope, item.id, since),
        )
        used = abs(_num(row["qty"] if row else 0))
        days = max(1, min(average_days, int(row["days"] if row and row["days"] else average_days)))
        avg = used / days if used > 0 else 0
        left_days = stock / avg if avg > 0 else None
        result.append(
            {
                "id": item.id,
                "name": item.name,
                "stock": stock,
                "stock_text": _fmt(stock),
                "unit": item.default_unit,
                "avg": avg,
                "avg_text": _fmt(avg),
                "days_left": left_days,
                "days_left_text": "—" if left_days is None else _fmt(left_days),
                "flag": bool(left_days is not None and left_days < min_work_days),
                "min_work_days": min_work_days,
                "average_days": average_days,
            }
        )
    return result


def material_days_by_area(chat_id: int) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    materials = repo.list_entities(scope, {"material"})
    areas = [None] + [a.id for a in repo.list_areas(scope)]
    area_names = {None: "Без площадки"}
    for area in repo.list_areas(scope):
        area_names[area.id] = area.name
    result: list[dict[str, Any]] = []
    for item in materials:
        settings = repo.get_material_stock_settings(scope, item.id)
        average_days = int(settings.get("average_days") or 14)
        min_work_days = float(settings.get("min_work_days") or 5)
        since = (datetime.now() - timedelta(days=max(average_days + 7, 21))).isoformat(sep=" ")
        for area_id in areas:
            stock = repo.inventory_quantity(scope, "material", item.id, item.default_unit, area_id=area_id)
            row = db.fetchone(
                """
                SELECT COALESCE(SUM(quantity),0) AS qty,COUNT(DISTINCT DATE(created_at)) AS days
                FROM operations
                WHERE chat_id=? AND operation_type='material_out' AND entity_id=? AND created_at>=?
                  AND ((area_id IS NULL AND ? IS NULL) OR area_id=?)
                """,
                (scope, item.id, since, area_id, area_id),
            )
            used = abs(_num(row["qty"] if row else 0))
            if abs(stock) < 0.000001 and used < 0.000001:
                continue
            days = max(1, min(average_days, int(row["days"] if row and row["days"] else average_days)))
            avg = used / days if used > 0 else 0
            left_days = stock / avg if avg > 0 else None
            result.append(
                {
                    "area_id": area_id,
                    "area_name": area_names.get(area_id, "Без площадки"),
                    "id": item.id,
                    "name": item.name,
                    "stock": stock,
                    "stock_text": _fmt(stock),
                    "unit": item.default_unit,
                    "avg": avg,
                    "avg_text": _fmt(avg),
                    "days_left": left_days,
                    "days_left_text": "—" if left_days is None else _fmt(left_days),
                    "flag": bool(left_days is not None and left_days < min_work_days),
                    "min_work_days": min_work_days,
                    "average_days": average_days,
                }
            )
    return result


def recent_operations(chat_id: int, limit: int = 12) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    rows = db.fetchall(
        """
        SELECT o.id,o.operation_type,o.quantity,o.unit,o.created_at,e.name AS entity_name,c.title AS group_title,w.display_name,a.name AS area_name
        FROM operations o
        LEFT JOIN entities e ON e.id=o.entity_id
        LEFT JOIN chats c ON c.chat_id=o.group_chat_id
        LEFT JOIN workers w ON w.chat_id=o.chat_id AND w.user_id=o.user_id
        LEFT JOIN areas a ON a.id=o.area_id
        WHERE o.chat_id=?
        ORDER BY o.id DESC
        LIMIT ?
        """,
        (scope, int(limit)),
    )
    return [
        {
            "id": int(r["id"]),
            "type": str(r["operation_type"] or ""),
            "name": str(r["entity_name"] or ""),
            "qty": _num(r["quantity"]),
            "qty_text": _fmt(r["quantity"]),
            "unit": str(r["unit"] or "шт"),
            "group": str(r["group_title"] or ""),
            "worker": str(r["display_name"] or ""),
            "area": str(r["area_name"] or ""),
            "created_at": str(r["created_at"] or ""),
        }
        for r in rows
    ]



# --- Фильтрация панели по доступным площадкам step64 ---

def _area_clause(column: str, area_ids: set[int] | None) -> tuple[str, tuple[object, ...]]:
    if area_ids is None:
        return "", ()
    if not area_ids:
        return " AND 1=0", ()
    marks = ",".join("?" for _ in area_ids)
    return f" AND {column} IN ({marks})", tuple(sorted(int(value) for value in area_ids))


def _inventory_groups_from_area_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[tuple[str, int, str, str], float] = {}
    for row in rows:
        key = (
            str(row.get("entity_type") or "stock_item"),
            int(row.get("entity_id") or 0),
            str(row.get("name") or ""),
            str(row.get("unit") or "шт"),
        )
        grouped[key] = grouped.get(key, 0.0) + _num(row.get("qty"))
    result: dict[str, list[dict[str, Any]]] = {"component": [], "material": [], "product": [], "stock_item": [], "meter": []}
    for (entity_type, entity_id, name, unit), quantity in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][2])):
        result.setdefault(entity_type, []).append({
            "id": entity_id,
            "name": name,
            "qty": quantity,
            "qty_text": _fmt(quantity),
            "unit": unit,
        })
    return result


def _area_summary_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[object, str], dict[str, float]] = {}
    for row in rows:
        key = (row.get("area_id"), str(row.get("area_name") or "Без площадки"))
        bucket = grouped.setdefault(key, {"components": 0.0, "materials": 0.0, "products": 0.0})
        entity_type = str(row.get("entity_type") or "")
        if entity_type == "component":
            bucket["components"] += _num(row.get("qty"))
        elif entity_type == "material":
            bucket["materials"] += _num(row.get("qty"))
        elif entity_type == "product":
            bucket["products"] += _num(row.get("qty"))
    result: list[dict[str, Any]] = []
    for (_area_id, area_name), bucket in sorted(grouped.items(), key=lambda item: item[0][1]):
        result.append({
            "area_name": area_name,
            "components": bucket["components"],
            "components_text": _fmt(bucket["components"]),
            "materials": bucket["materials"],
            "materials_text": _fmt(bucket["materials"]),
            "products": bucket["products"],
            "products_text": _fmt(bucket["products"]),
        })
    return result


def _month_totals_for_areas(chat_id: int, area_ids: set[int] | None) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(sep=" ")
    clause, params = _area_clause("COALESCE(o.area_id,o.to_area_id,o.from_area_id)", area_ids)
    rows = db.fetchall(
        f"""
        SELECT o.operation_type,o.entity_type,o.entity_id,e.name,COALESCE(SUM(o.quantity),0) AS qty,o.unit
        FROM operations o
        LEFT JOIN entities e ON e.id=o.entity_id
        WHERE o.chat_id=? AND o.created_at>=? {clause}
        GROUP BY o.operation_type,o.entity_type,o.entity_id,e.name,o.unit
        ORDER BY o.operation_type,e.name
        """,
        (scope, start, *params),
    )
    return [{
        "type": str(row["operation_type"] or ""),
        "entity_type": str(row["entity_type"] or ""),
        "name": str(row["name"] or ""),
        "qty": _num(row["qty"]),
        "qty_text": _fmt(row["qty"]),
        "unit": str(row["unit"] or "шт"),
    } for row in rows]


def _recent_for_areas(chat_id: int, area_ids: set[int] | None, limit: int = 12) -> list[dict[str, Any]]:
    scope = _scope(chat_id)
    clause, params = _area_clause("COALESCE(o.area_id,o.to_area_id,o.from_area_id)", area_ids)
    rows = db.fetchall(
        f"""
        SELECT o.id,o.operation_type,o.quantity,o.unit,o.created_at,o.storage_place,
               e.name AS entity_name,c.title AS group_title,w.display_name,
               a.name AS area_name,fa.name AS from_area_name,ta.name AS to_area_name
        FROM operations o
        LEFT JOIN entities e ON e.id=o.entity_id
        LEFT JOIN chats c ON c.chat_id=o.group_chat_id
        LEFT JOIN workers w ON w.chat_id=o.chat_id AND w.user_id=o.user_id
        LEFT JOIN areas a ON a.id=o.area_id
        LEFT JOIN areas fa ON fa.id=o.from_area_id
        LEFT JOIN areas ta ON ta.id=o.to_area_id
        WHERE o.chat_id=? {clause}
        ORDER BY o.id DESC
        LIMIT ?
        """,
        (scope, *params, int(limit)),
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        area_name = str(row["area_name"] or "")
        if not area_name and (row["from_area_name"] or row["to_area_name"]):
            area_name = f"{row['from_area_name'] or '—'} → {row['to_area_name'] or '—'}"
        result.append({
            "id": int(row["id"]),
            "type": str(row["operation_type"] or ""),
            "name": str(row["entity_name"] or ""),
            "qty": _num(row["quantity"]),
            "qty_text": _fmt(row["quantity"]),
            "unit": str(row["unit"] or "шт"),
            "group": str(row["group_title"] or ""),
            "worker": str(row["display_name"] or ""),
            "area": area_name,
            "storage_place": str(row["storage_place"] or ""),
            "created_at": str(row["created_at"] or ""),
        })
    return result


def dashboard(chat_id: int, area_ids: set[int] | None = None) -> dict[str, Any]:
    scope = _scope(chat_id)
    account = repo.get_account_by_scope(scope)
    all_area_rows = inventory_by_area(scope)
    area_rows = all_area_rows if area_ids is None else [row for row in all_area_rows if row.get("area_id") in area_ids]
    materials_by_area = material_days_by_area(scope)
    if area_ids is not None:
        materials_by_area = [row for row in materials_by_area if row.get("area_id") in area_ids]
    materials = material_days(scope) if area_ids is None else []
    return {
        "scope_chat_id": scope,
        "account_name": account.name if account else "Учёт",
        "inventory": _inventory_groups_from_area_rows(area_rows),
        "inventory_by_area": area_rows,
        "area_summary": _area_summary_from_rows(area_rows),
        "month_totals": _month_totals_for_areas(scope, area_ids),
        "material_days": materials,
        "material_days_by_area": materials_by_area,
        "alerts": [item for item in materials_by_area if item.get("flag")] or [item for item in materials if item.get("flag")],
        "recent": _recent_for_areas(scope, area_ids),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
