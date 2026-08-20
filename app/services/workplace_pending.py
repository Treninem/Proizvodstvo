from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from .. import db


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS workplace_pending (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    group_chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""
_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_workplace_pending_user "
    "ON workplace_pending(chat_id,group_chat_id,user_id,created_at DESC)"
)


def ensure_schema() -> None:
    db.execute(_TABLE_SQL)
    db.execute(_INDEX_SQL)


def create(chat_id: int, group_chat_id: int, user_id: int, payload: dict[str, Any], minutes: int = 30) -> str:
    ensure_schema()
    token = uuid.uuid4().hex[:20]
    expires = (datetime.utcnow() + timedelta(minutes=max(1, int(minutes)))).isoformat()
    db.execute(
        "DELETE FROM workplace_pending WHERE chat_id=? AND group_chat_id=? AND user_id=?",
        (int(chat_id), int(group_chat_id), int(user_id)),
    )
    db.execute(
        "INSERT INTO workplace_pending(id,chat_id,group_chat_id,user_id,payload_json,expires_at) VALUES(?,?,?,?,?,?)",
        (
            token,
            int(chat_id),
            int(group_chat_id),
            int(user_id),
            json.dumps(payload, ensure_ascii=False),
            expires,
        ),
    )
    return token


def get(token: str, chat_id: int, group_chat_id: int, user_id: int) -> dict[str, Any] | None:
    ensure_schema()
    row = db.fetchone(
        """
        SELECT payload_json,expires_at FROM workplace_pending
        WHERE id=? AND chat_id=? AND group_chat_id=? AND user_id=?
        """,
        (str(token), int(chat_id), int(group_chat_id), int(user_id)),
    )
    if not row:
        return None
    if str(row["expires_at"]) < datetime.utcnow().isoformat():
        clear(token)
        return None
    try:
        return json.loads(row["payload_json"] or "{}")
    except Exception:
        clear(token)
        return None


def clear(token: str) -> None:
    ensure_schema()
    db.execute("DELETE FROM workplace_pending WHERE id=?", (str(token),))
