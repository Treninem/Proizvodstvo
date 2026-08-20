from __future__ import annotations

from app import db


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS telegram_user_directory (
    user_id INTEGER PRIMARY KEY,
    username TEXT NOT NULL DEFAULT '',
    username_key TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    last_chat_id INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""
_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_telegram_user_directory_username ON telegram_user_directory(username_key, updated_at DESC)"


def ensure_table() -> None:
    db.execute(_TABLE_SQL)
    db.execute(_INDEX_SQL)


def normalize_username(value: str | None) -> str:
    text = str(value or "").strip()
    if text.startswith("https://t.me/"):
        text = text.split("https://t.me/", 1)[1]
    if text.startswith("t.me/"):
        text = text.split("t.me/", 1)[1]
    return text.lstrip("@").strip().lower()


def remember_user(
    user_id: int | None,
    username: str | None = None,
    display_name: str | None = None,
    chat_id: int | None = None,
) -> None:
    if not user_id or int(user_id) <= 0:
        return
    ensure_table()
    clean_username = str(username or "").strip().lstrip("@")[:64]
    key = normalize_username(clean_username)
    clean_name = " ".join(str(display_name or "").split()).strip()[:180]
    db.execute(
        """
        INSERT INTO telegram_user_directory(user_id,username,username_key,display_name,last_chat_id,updated_at)
        VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            username_key=excluded.username_key,
            display_name=CASE WHEN excluded.display_name<>'' THEN excluded.display_name ELSE telegram_user_directory.display_name END,
            last_chat_id=COALESCE(excluded.last_chat_id,telegram_user_directory.last_chat_id),
            updated_at=CURRENT_TIMESTAMP
        """,
        (int(user_id), clean_username, key, clean_name, int(chat_id) if chat_id is not None else None),
    )


def resolve_user_ref(value: str | int | None) -> dict | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.lstrip("+").isdigit():
        user_id = int(text.lstrip("+"))
        if user_id <= 0:
            return None
        row = None
        try:
            ensure_table()
            row = db.fetchone(
                "SELECT user_id,username,display_name,last_chat_id,updated_at FROM telegram_user_directory WHERE user_id=?",
                (user_id,),
            )
        except Exception:
            row = None
        if row:
            return dict(row)
        return {"user_id": user_id, "username": "", "display_name": "", "last_chat_id": None, "updated_at": None}

    key = normalize_username(text)
    if not key:
        return None
    ensure_table()
    row = db.fetchone(
        """
        SELECT user_id,username,display_name,last_chat_id,updated_at
        FROM telegram_user_directory
        WHERE username_key=? AND username<>''
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (key,),
    )
    return dict(row) if row else None


def list_recent_users(limit: int = 100) -> list[dict]:
    ensure_table()
    rows = db.fetchall(
        """
        SELECT user_id,username,display_name,last_chat_id,updated_at
        FROM telegram_user_directory
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, min(int(limit), 500)),),
    )
    return [dict(row) for row in rows]
