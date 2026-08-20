from __future__ import annotations

from app import db
from app.services.normalize import normalize_key

_DEFAULT_UNITS: tuple[tuple[str, str], ...] = (
    ("Штуки", "шт"),
    ("Единицы", "ед"),
    ("Килограммы", "кг"),
    ("Граммы", "г"),
    ("Тонны", "т"),
    ("Метры", "м"),
    ("Сантиметры", "см"),
    ("Миллиметры", "мм"),
    ("Литры", "л"),
    ("Миллилитры", "мл"),
    ("Мешки", "мешок"),
    ("Упаковки", "упак"),
    ("Коробки", "короб"),
    ("Рулоны", "рулон"),
    ("Погонные метры", "пог.м"),
)

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS measurement_units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    normalized TEXT NOT NULL,
    is_archived INTEGER NOT NULL DEFAULT 0,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, normalized)
)
"""
_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_measurement_units_scope ON measurement_units(chat_id,is_archived,name)"


def ensure_schema() -> None:
    db.execute(_TABLE_SQL)
    db.execute(_INDEX_SQL)


def ensure_defaults(chat_id: int, created_by: int | None = None) -> None:
    ensure_schema()
    for name, symbol in _DEFAULT_UNITS:
        key = normalize_key(symbol)
        db.execute(
            """
            INSERT OR IGNORE INTO measurement_units(chat_id,name,symbol,normalized,is_default,created_by)
            VALUES(?,?,?,?,1,?)
            """,
            (int(chat_id), name, symbol, key, int(created_by) if created_by else None),
        )


def list_units(chat_id: int, created_by: int | None = None) -> list[dict]:
    ensure_defaults(chat_id, created_by)
    rows = db.fetchall(
        """
        SELECT id,name,symbol,is_default,created_at,updated_at
        FROM measurement_units
        WHERE chat_id=? AND is_archived=0
        ORDER BY is_default DESC, name COLLATE NOCASE, id
        """,
        (int(chat_id),),
    )
    return [dict(row) for row in rows]


def unit_exists(chat_id: int, symbol: str) -> bool:
    ensure_defaults(chat_id)
    key = normalize_key(symbol)
    if not key:
        return False
    return bool(
        db.fetchone(
            "SELECT id FROM measurement_units WHERE chat_id=? AND normalized=? AND is_archived=0",
            (int(chat_id), key),
        )
    )


def create_unit(chat_id: int, name: str, symbol: str, created_by: int | None = None) -> tuple[bool, str, int | None]:
    ensure_defaults(chat_id, created_by)
    clean_name = " ".join(str(name or "").split()).strip()[:120]
    clean_symbol = " ".join(str(symbol or "").split()).strip()[:40]
    key = normalize_key(clean_symbol)
    if not clean_name or not key:
        return False, "Укажите название и обозначение единицы измерения.", None
    existing = db.fetchone(
        "SELECT id FROM measurement_units WHERE chat_id=? AND normalized=? AND is_archived=0",
        (int(chat_id), key),
    )
    if existing:
        return False, "Такая единица измерения уже есть.", int(existing["id"])
    try:
        db.execute(
            """
            INSERT INTO measurement_units(chat_id,name,symbol,normalized,is_default,created_by)
            VALUES(?,?,?,?,0,?)
            """,
            (int(chat_id), clean_name, clean_symbol, key, int(created_by) if created_by else None),
        )
        created = db.fetchone(
            "SELECT id FROM measurement_units WHERE chat_id=? AND normalized=? AND is_archived=0",
            (int(chat_id), key),
        )
        if not created:
            return False, "Не удалось найти добавленную единицу измерения.", None
        return True, f"Единица измерения добавлена: {clean_name} ({clean_symbol})", int(created["id"])
    except Exception:
        return False, "Не удалось добавить единицу измерения.", None


def archive_unit(chat_id: int, unit_id: int) -> tuple[bool, str]:
    ensure_schema()
    row = db.fetchone(
        "SELECT id,name,is_default FROM measurement_units WHERE id=? AND chat_id=? AND is_archived=0",
        (int(unit_id), int(chat_id)),
    )
    if not row:
        return False, "Единица измерения не найдена."
    if bool(row["is_default"]):
        return False, "Базовые единицы измерения скрывать нельзя."
    db.execute(
        "UPDATE measurement_units SET is_archived=1,updated_at=CURRENT_TIMESTAMP WHERE id=? AND chat_id=?",
        (int(unit_id), int(chat_id)),
    )
    return True, f"Единица измерения скрыта: {row['name']}"
