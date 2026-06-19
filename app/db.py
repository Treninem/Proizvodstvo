from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .config import settings

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    chat_type TEXT NOT NULL DEFAULT '',
    is_connected INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS areas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    normalized TEXT NOT NULL,
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, normalized),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_area_bindings (
    chat_id INTEGER PRIMARY KEY,
    area_id INTEGER,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS group_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_chat_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    normalized TEXT NOT NULL,
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_chat_id, normalized)
);

CREATE TABLE IF NOT EXISTS group_set_items (
    group_set_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    PRIMARY KEY(group_set_id, chat_id),
    FOREIGN KEY(group_set_id) REFERENCES group_sets(id) ON DELETE CASCADE,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS job_titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    normalized TEXT NOT NULL,
    permissions_json TEXT NOT NULL DEFAULT '{}',
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, normalized),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workers (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    job_title_id INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(chat_id, user_id),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(job_title_id) REFERENCES job_titles(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    name TEXT NOT NULL,
    normalized TEXT NOT NULL,
    default_unit TEXT NOT NULL DEFAULT 'шт',
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, entity_type, normalized),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    normalized TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, normalized),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS product_components (
    product_id INTEGER NOT NULL,
    component_id INTEGER NOT NULL,
    quantity REAL NOT NULL,
    PRIMARY KEY(product_id, component_id),
    FOREIGN KEY(product_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(component_id) REFERENCES entities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meter_area_bindings (
    meter_id INTEGER NOT NULL,
    area_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(meter_id, area_id),
    FOREIGN KEY(meter_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS stock_item_area_bindings (
    stock_item_id INTEGER NOT NULL,
    area_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(stock_item_id, area_id),
    FOREIGN KEY(stock_item_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS inventory (
    chat_id INTEGER NOT NULL,
    area_id INTEGER,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    unit TEXT NOT NULL DEFAULT 'шт',
    quantity REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(chat_id, area_id, entity_type, entity_id, unit),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    group_chat_id INTEGER NOT NULL,
    area_id INTEGER,
    user_id INTEGER NOT NULL,
    operation_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id INTEGER,
    quantity REAL,
    unit TEXT NOT NULL DEFAULT 'шт',
    raw_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL
);


CREATE TABLE IF NOT EXISTS operation_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_operation_id INTEGER NOT NULL UNIQUE,
    reversal_operation_id INTEGER,
    replacement_operation_id INTEGER,
    actor_user_id INTEGER NOT NULL,
    correction_type TEXT NOT NULL,
    old_quantity REAL,
    new_quantity REAL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(original_operation_id) REFERENCES operations(id) ON DELETE CASCADE,
    FOREIGN KEY(reversal_operation_id) REFERENCES operations(id) ON DELETE SET NULL,
    FOREIGN KEY(replacement_operation_id) REFERENCES operations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS pending_confirmations (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    group_chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS local_lexicon (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    phrase TEXT NOT NULL,
    normalized TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'confirmation',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, normalized),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS accounting_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    owner_chat_id INTEGER NOT NULL,
    scope_chat_id INTEGER NOT NULL UNIQUE,
    name TEXT NOT NULL,
    normalized TEXT NOT NULL,
    is_general INTEGER NOT NULL DEFAULT 0,
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_user_id, normalized)
);

CREATE TABLE IF NOT EXISTS account_chat_access (
    account_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    can_manage INTEGER NOT NULL DEFAULT 0,
    can_view INTEGER NOT NULL DEFAULT 1,
    can_submit INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(account_id, chat_id),
    FOREIGN KEY(account_id) REFERENCES accounting_accounts(id) ON DELETE CASCADE,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_active_account (
    chat_id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(account_id) REFERENCES accounting_accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS account_user_access (
    account_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    job_title_id INTEGER,
    can_manage INTEGER NOT NULL DEFAULT 0,
    can_view INTEGER NOT NULL DEFAULT 1,
    can_submit INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(account_id, user_id),
    FOREIGN KEY(account_id) REFERENCES accounting_accounts(id) ON DELETE CASCADE,
    FOREIGN KEY(job_title_id) REFERENCES job_titles(id) ON DELETE SET NULL
);



CREATE TABLE IF NOT EXISTS export_preferences (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    include_inventory INTEGER NOT NULL DEFAULT 1,
    include_period_totals INTEGER NOT NULL DEFAULT 1,
    include_daily_matrix INTEGER NOT NULL DEFAULT 1,
    include_capacity INTEGER NOT NULL DEFAULT 1,
    include_journal INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS setup_sessions (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    state TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(chat_id, user_id)
);
"""


def ensure_data_dir() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def execute(query: str, params: Iterable[Any] = ()) -> None:
    with connect() as conn:
        conn.execute(query, tuple(params))
        conn.commit()


def fetchone(query: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(query, tuple(params)).fetchone()


def fetchall(query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(query, tuple(params)).fetchall()
