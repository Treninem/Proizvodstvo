from __future__ import annotations

from typing import Any

from .. import db
from . import repository as repo


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS worker_workplaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    workplace_key TEXT NOT NULL,
    site_id INTEGER,
    area_id INTEGER,
    location_id INTEGER,
    department_id INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1,
    assigned_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, user_id, workplace_key),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(site_id) REFERENCES company_sites(id) ON DELETE SET NULL,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL,
    FOREIGN KEY(location_id) REFERENCES storage_locations(id) ON DELETE SET NULL,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE SET NULL
)
"""
_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_worker_workplaces_user "
    "ON worker_workplaces(chat_id,user_id,is_active,area_id,location_id)"
)


def ensure_schema() -> None:
    db.execute(_TABLE_SQL)
    db.execute(_INDEX_SQL)


def _clean_parts(*parts: object) -> list[str]:
    out: list[str] = []
    for value in parts:
        text = " ".join(str(value or "").split()).strip()
        if text and text not in out:
            out.append(text)
    return out


def _label(*parts: object) -> str:
    values = _clean_parts(*parts)
    return " · ".join(values) if values else "Рабочее место"


def list_available_workplaces(chat_id: int) -> list[dict[str, Any]]:
    """Return physical places available for worker assignment.

    Storage locations are preferred because they identify both the area and the
    exact stock/work location. Areas without any storage location remain
    selectable as area-only workplaces, so an incomplete organisation setup does
    not block assigning staff.
    """
    ensure_schema()
    scope = repo.resolve_scope_chat_id(int(chat_id))
    locations = repo.list_storage_locations(scope)
    result: list[dict[str, Any]] = []
    covered_areas: set[int] = set()

    for row in locations:
        location_id = int(row.get("id") or 0)
        if not location_id:
            continue
        area_id = int(row.get("area_id") or 0) or None
        site_id = int(row.get("site_id") or 0) or None
        department_id = int(row.get("department_id") or 0) or None
        if area_id:
            covered_areas.add(area_id)
        label = _label(
            row.get("settlement"),
            row.get("site_name"),
            row.get("area_name"),
            row.get("name"),
        )
        result.append(
            {
                "key": f"l{location_id}",
                "site_id": site_id,
                "area_id": area_id,
                "location_id": location_id,
                "department_id": department_id,
                "label": label,
                "kind": "location",
            }
        )

    rows = db.fetchall(
        """
        SELECT a.id AS area_id,a.name AS area_name,a.site_id,
               s.name AS site_name,s.settlement
        FROM areas a
        LEFT JOIN company_sites s ON s.id=a.site_id AND s.chat_id=a.chat_id AND s.is_archived=0
        WHERE a.chat_id=? AND a.is_archived=0
        ORDER BY COALESCE(s.settlement,''),COALESCE(s.name,''),a.name
        """,
        (scope,),
    )
    for row in rows:
        area_id = int(row["area_id"])
        if area_id in covered_areas:
            continue
        result.append(
            {
                "key": f"a{area_id}",
                "site_id": int(row["site_id"]) if row["site_id"] else None,
                "area_id": area_id,
                "location_id": None,
                "department_id": None,
                "label": _label(row["settlement"], row["site_name"], row["area_name"]),
                "kind": "area",
            }
        )
    return result


def available_workplace_map(chat_id: int) -> dict[str, dict[str, Any]]:
    return {str(item["key"]): item for item in list_available_workplaces(chat_id)}


def set_worker_workplaces(
    chat_id: int,
    user_id: int,
    workplace_keys: list[str] | set[str] | tuple[str, ...],
    assigned_by: int,
) -> tuple[bool, str]:
    ensure_schema()
    scope = repo.resolve_scope_chat_id(int(chat_id))
    worker = repo.get_worker(scope, int(user_id))
    if not worker:
        return False, "Сначала назначьте сотруднику должность."
    available = available_workplace_map(scope)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_key in workplace_keys:
        key = str(raw_key or "").strip()
        if not key or key in seen:
            continue
        place = available.get(key)
        if not place:
            return False, "Одно из выбранных рабочих мест больше недоступно."
        seen.add(key)
        selected.append(place)
    if not selected:
        return False, "Выберите хотя бы одно рабочее место."

    with db.connect() as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE worker_workplaces SET is_active=0,updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND user_id=?",
            (scope, int(user_id)),
        )
        for place in selected:
            conn.execute(
                """
                INSERT INTO worker_workplaces(
                    chat_id,user_id,workplace_key,site_id,area_id,location_id,department_id,
                    is_active,assigned_by,updated_at
                ) VALUES(?,?,?,?,?,?,?,1,?,CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id,user_id,workplace_key) DO UPDATE SET
                    site_id=excluded.site_id,
                    area_id=excluded.area_id,
                    location_id=excluded.location_id,
                    department_id=excluded.department_id,
                    is_active=1,
                    assigned_by=excluded.assigned_by,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    scope,
                    int(user_id),
                    str(place["key"]),
                    place.get("site_id"),
                    place.get("area_id"),
                    place.get("location_id"),
                    place.get("department_id"),
                    int(assigned_by),
                ),
            )
        conn.commit()
    return True, f"Рабочих мест назначено: {len(selected)}"


def clear_worker_workplaces(chat_id: int, user_id: int) -> None:
    ensure_schema()
    scope = repo.resolve_scope_chat_id(int(chat_id))
    db.execute(
        "UPDATE worker_workplaces SET is_active=0,updated_at=CURRENT_TIMESTAMP WHERE chat_id=? AND user_id=?",
        (scope, int(user_id)),
    )


def list_worker_workplaces(chat_id: int, user_id: int) -> list[dict[str, Any]]:
    ensure_schema()
    scope = repo.resolve_scope_chat_id(int(chat_id))
    rows = db.fetchall(
        """
        SELECT ww.id,ww.chat_id,ww.user_id,ww.workplace_key,ww.site_id,ww.area_id,
               ww.location_id,ww.department_id,ww.assigned_by,ww.created_at,ww.updated_at,
               a.name AS area_name,
               COALESCE(s1.name,s2.name) AS site_name,
               COALESCE(s1.settlement,s2.settlement) AS settlement,
               l.name AS location_name,
               d.name AS department_name
        FROM worker_workplaces ww
        LEFT JOIN areas a ON a.id=ww.area_id AND a.chat_id=ww.chat_id AND a.is_archived=0
        LEFT JOIN company_sites s1 ON s1.id=ww.site_id AND s1.chat_id=ww.chat_id AND s1.is_archived=0
        LEFT JOIN company_sites s2 ON s2.id=a.site_id AND s2.chat_id=ww.chat_id AND s2.is_archived=0
        LEFT JOIN storage_locations l ON l.id=ww.location_id AND l.chat_id=ww.chat_id AND l.is_archived=0
        LEFT JOIN departments d ON d.id=ww.department_id AND d.chat_id=ww.chat_id AND d.is_archived=0
        WHERE ww.chat_id=? AND ww.user_id=? AND ww.is_active=1
          AND (ww.area_id IS NULL OR a.id IS NOT NULL)
          AND (ww.location_id IS NULL OR l.id IS NOT NULL)
        ORDER BY COALESCE(s1.settlement,s2.settlement,''),COALESCE(s1.name,s2.name,''),
                 COALESCE(a.name,''),COALESCE(l.name,''),ww.id
        """,
        (scope, int(user_id)),
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["label"] = _label(
            item.get("settlement"),
            item.get("site_name"),
            item.get("area_name"),
            item.get("location_name"),
        )
        result.append(item)
    return result


def worker_workplace_by_id(chat_id: int, user_id: int, workplace_id: int) -> dict[str, Any] | None:
    for item in list_worker_workplaces(chat_id, user_id):
        if int(item.get("id") or 0) == int(workplace_id):
            return item
    return None


def apply_workplace_to_operations(operations: list[dict[str, Any]], workplace: dict[str, Any]) -> list[dict[str, Any]]:
    """Stamp chat-originated work with the employee's selected physical place."""
    out: list[dict[str, Any]] = []
    area_id = int(workplace.get("area_id") or 0) or None
    location_id = int(workplace.get("location_id") or 0) or None
    department_id = int(workplace.get("department_id") or 0) or None
    label = str(workplace.get("label") or "Рабочее место")
    location_name = str(workplace.get("location_name") or "")

    for source in operations:
        op = dict(source)
        op_type = str(op.get("operation_type") or "")
        # The worker workplace is authoritative for ordinary group-chat input.
        # Transfers retain their explicit destination, but default their origin to
        # the selected workplace when it was not stated separately.
        if op_type in {"movement", "transfer_to_assembly"}:
            if area_id and not op.get("from_area_id"):
                op["from_area_id"] = area_id
            if location_id and not op.get("from_location_id"):
                op["from_location_id"] = location_id
            if department_id and not op.get("from_department_id"):
                op["from_department_id"] = department_id
        else:
            op["area_id"] = area_id
            # The human confirmation must show the whole physical path, not just
            # a possibly duplicated area name such as "Экструзия".
            op["area_name"] = label
            op["storage_location_id"] = location_id
            op["storage_place"] = location_name or label
            if department_id:
                op["department_id"] = department_id
        op["worker_workplace_id"] = int(workplace.get("id") or 0) or None
        op["worker_workplace_label"] = label
        out.append(op)
    return out
