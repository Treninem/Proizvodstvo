from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .. import db
from ..config import settings
from .normalize import normalize_key
from . import repository as repo


ACCOUNT_TABLES: dict[str, str] = {
    "areas": "chat_id",
    "job_titles": "chat_id",
    "workers": "chat_id",
    "entities": "chat_id",
    "aliases": "chat_id",
    "inventory": "chat_id",
    "operations": "chat_id",
    "setup_sessions": "chat_id",
    "export_preferences": "chat_id",
}

GLOBAL_ACCOUNT_TABLES = {
    "accounting_accounts",
    "account_chat_access",
    "chat_active_account",
    "account_user_access",
}


BACKUP_LABEL = "production_account_bot"


def backups_dir() -> Path:
    path = settings.data_dir / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(value: str) -> str:
    cleaned = []
    for ch in value.strip():
        if ch.isalnum() or ch in {"_", "-", "."}:
            cleaned.append(ch)
        else:
            cleaned.append("_")
    result = "".join(cleaned).strip("_")
    return result[:80] or "uchet"


def _rows(table: str, where: str = "1=1", params: Iterable[object] = ()) -> list[dict]:
    return [dict(row) for row in db.fetchall(f"SELECT * FROM {table} WHERE {where}", tuple(params))]


def _schema_info() -> list[dict]:
    tables = db.fetchall("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name")
    return [{"name": str(row["name"]), "sql": str(row["sql"] or "")} for row in tables]


def create_account_backup(chat_id: int, user_id: int | None = None) -> Path:
    scope_chat_id = repo.resolve_scope_chat_id(chat_id)
    account = repo.get_account_by_scope(scope_chat_id)
    name = account.name if account else "uchet"
    created_at = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"kopiya_ucheta_{_safe_name(name)}_{created_at}"
    folder = backups_dir()
    json_path = folder / f"{base}.json"
    zip_path = folder / f"{base}.zip"

    payload: dict[str, object] = {
        "format": BACKUP_LABEL,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "scope_chat_id": scope_chat_id,
        "account": dict(account.__dict__) if account else None,
        "tables": {},
    }
    tables_payload: dict[str, list[dict]] = {}
    for table, column in ACCOUNT_TABLES.items():
        tables_payload[table] = _rows(table, f"{column}=?", (scope_chat_id,))

    product_ids = [int(row["id"]) for row in tables_payload.get("entities", []) if row.get("entity_type") == "product"]
    component_ids = [int(row["id"]) for row in tables_payload.get("entities", [])]
    if product_ids:
        marks = ",".join("?" for _ in product_ids)
        tables_payload["product_components"] = _rows("product_components", f"product_id IN ({marks})", product_ids)
    else:
        tables_payload["product_components"] = []
    if component_ids:
        marks = ",".join("?" for _ in component_ids)
        tables_payload["meter_area_bindings"] = _rows("meter_area_bindings", f"meter_id IN ({marks})", component_ids)
        tables_payload["stock_item_area_bindings"] = _rows("stock_item_area_bindings", f"stock_item_id IN ({marks})", component_ids)
    else:
        tables_payload["meter_area_bindings"] = []
        tables_payload["stock_item_area_bindings"] = []

    area_ids = [int(row["id"]) for row in tables_payload.get("areas", [])]
    if area_ids:
        marks = ",".join("?" for _ in area_ids)
        tables_payload["chat_area_bindings"] = _rows("chat_area_bindings", f"area_id IN ({marks})", area_ids)
    else:
        tables_payload["chat_area_bindings"] = []

    payload["tables"] = tables_payload
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(json_path, arcname=json_path.name)
    try:
        json_path.unlink()
    except FileNotFoundError:
        pass
    return zip_path


def create_full_backup() -> Path:
    created_at = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = backups_dir()
    zip_path = folder / f"polnaya_kopiya_bazy_{created_at}.zip"
    manifest_path = folder / f"manifest_{created_at}.json"
    manifest = {
        "format": BACKUP_LABEL,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "schema": _schema_info(),
        "counts": {},
    }
    for row in db.fetchall("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        table = str(row["name"])
        try:
            count_row = db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")
            manifest["counts"][table] = int(count_row["n"] if count_row else 0)
        except Exception:
            manifest["counts"][table] = 0
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if settings.database_path.exists():
            zf.write(settings.database_path, arcname=settings.database_path.name)
        wal = settings.database_path.with_suffix(settings.database_path.suffix + "-wal")
        shm = settings.database_path.with_suffix(settings.database_path.suffix + "-shm")
        if wal.exists():
            zf.write(wal, arcname=wal.name)
        if shm.exists():
            zf.write(shm, arcname=shm.name)
        zf.write(manifest_path, arcname=manifest_path.name)
    try:
        manifest_path.unlink()
    except FileNotFoundError:
        pass
    return zip_path


def list_backup_files(limit: int = 10) -> list[Path]:
    files = sorted(backups_dir().glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def format_backup_list(limit: int = 10) -> str:
    files = list_backup_files(limit)
    if not files:
        return "Копий пока нет."
    lines = ["Последние копии"]
    for file in files:
        size_kb = file.stat().st_size / 1024
        stamp = datetime.fromtimestamp(file.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
        lines.append(f"• {file.name} · {size_kb:.1f} КБ · {stamp}")
    return "\n".join(lines)
