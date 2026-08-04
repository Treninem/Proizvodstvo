from __future__ import annotations

import base64
import hashlib
import json
import io
import shutil
import sqlite3
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
    "material_stock_settings": "chat_id",
    "operation_destinations": "chat_id",
    "area_section_access": "chat_id",
    "assembly_plan_targets": "chat_id",
    "report_presets": "chat_id",
    "inventory_sessions": "chat_id",
    "worker_shifts": "chat_id",
    "report_schedules": "chat_id",
    "inbox_items": "chat_id",
    "shift_plans": "chat_id",
    "report_delivery_history": "chat_id",
    "shift_templates": "chat_id",
    "notification_preferences": "chat_id",
    "inventory_approval_escalations": "chat_id",
    "departments": "chat_id",
    "stock_alert_rules": "chat_id",
    "stock_observations": "chat_id",
    "operational_events": "chat_id",
    "stock_alert_incidents": "chat_id",
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

    session_ids = [int(row["id"]) for row in tables_payload.get("inventory_sessions", [])]
    if session_ids:
        marks = ",".join("?" for _ in session_ids)
        tables_payload["inventory_session_items"] = _rows("inventory_session_items", f"session_id IN ({marks})", session_ids)
    else:
        tables_payload["inventory_session_items"] = []

    rule_ids = [int(row["id"]) for row in tables_payload.get("stock_alert_rules", [])]
    if rule_ids:
        marks = ",".join("?" for _ in rule_ids)
        tables_payload["stock_alert_snoozes"] = _rows("stock_alert_snoozes", f"rule_id IN ({marks})", rule_ids)
    else:
        tables_payload["stock_alert_snoozes"] = []

    department_ids = [int(row["id"]) for row in tables_payload.get("departments", [])]
    if department_ids:
        marks = ",".join("?" for _ in department_ids)
        tables_payload["department_operation_rules"] = _rows("department_operation_rules", f"department_id IN ({marks})", department_ids)
        tables_payload["department_entity_rules"] = _rows("department_entity_rules", f"department_id IN ({marks})", department_ids)
        tables_payload["department_members"] = _rows("department_members", f"department_id IN ({marks})", department_ids)
    else:
        tables_payload["department_operation_rules"] = []
        tables_payload["department_entity_rules"] = []
        tables_payload["department_members"] = []


    if account:
        tables_payload["account_user_access"] = _rows("account_user_access", "account_id=?", (account.id,))
        tables_payload["account_chat_access"] = _rows("account_chat_access", "account_id=?", (account.id,))

    payload["tables"] = tables_payload
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(json_path, arcname=json_path.name)
    try:
        json_path.unlink()
    except FileNotFoundError:
        pass
    return _encrypt_backup_if_needed(zip_path)


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
    return _encrypt_backup_if_needed(zip_path)


def _encrypt_backup_if_needed(path: Path) -> Path:
    key_text = settings.backup_encryption_key.strip()
    if not key_text:
        return path
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return path
    digest = hashlib.sha256(key_text.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(digest)
    encrypted = Fernet(fernet_key).encrypt(path.read_bytes())
    out = path.with_suffix(path.suffix + ".enc")
    out.write_bytes(encrypted)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return out


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


RESTORE_ALLOWED_TABLES = set(ACCOUNT_TABLES) | {
    "product_components", "meter_area_bindings", "stock_item_area_bindings",
    "chat_area_bindings", "inventory_session_items",
    "department_operation_rules", "department_entity_rules", "department_members",
    "account_user_access", "account_chat_access", "stock_alert_snoozes",
}


def _decrypt_restore_bytes(data: bytes, filename: str) -> bytes:
    if not filename.lower().endswith(".enc"):
        return data
    key_text = settings.backup_encryption_key.strip()
    if not key_text:
        raise ValueError("Для этой копии не задан ключ расшифровки.")
    try:
        from cryptography.fernet import Fernet
        digest = hashlib.sha256(key_text.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest)).decrypt(data)
    except Exception as exc:
        raise ValueError("Копия не расшифрована. Проверьте ключ.") from exc


def _read_account_backup_payload(data: bytes, filename: str) -> dict:
    if len(data) > 25 * 1024 * 1024:
        raise ValueError("Файл копии слишком большой.")
    plain = _decrypt_restore_bytes(data, filename)
    try:
        with zipfile.ZipFile(io.BytesIO(plain), "r") as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            if len(members) != 1 or not members[0].filename.lower().endswith(".json"):
                raise ValueError("В копии должен быть один файл данных JSON.")
            name = members[0].filename
            if Path(name).name != name or ".." in Path(name).parts:
                raise ValueError("Небезопасная структура архива.")
            raw = zf.read(members[0])
    except zipfile.BadZipFile as exc:
        raise ValueError("Файл не является резервной копией учёта.") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Данные копии повреждены.") from exc
    if payload.get("format") != BACKUP_LABEL or not isinstance(payload.get("tables"), dict):
        raise ValueError("Формат копии не поддерживается.")
    unknown = set(payload["tables"]) - RESTORE_ALLOWED_TABLES
    if unknown:
        raise ValueError("Копия содержит неподдерживаемые разделы.")
    return payload


def restore_account_backup(chat_id: int, user_id: int, data: bytes, filename: str) -> dict:
    scope = repo.resolve_scope_chat_id(chat_id)
    account = repo.get_account_by_scope(scope)
    if not account:
        raise ValueError("Выбранный учёт не найден.")
    payload = _read_account_backup_payload(data, filename)
    if int(payload.get("scope_chat_id") or 0) != int(scope):
        raise ValueError("Эта копия создана для другого учёта.")
    backup_account = payload.get("account") or {}
    if backup_account.get("id") and int(backup_account["id"]) != int(account.id):
        raise ValueError("Идентификатор учёта в копии не совпадает.")
    safety_copy = create_account_backup(scope, user_id)
    tables: dict[str, list[dict]] = payload["tables"]
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        entity_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM entities WHERE chat_id=?",(scope,)).fetchall()]
        area_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM areas WHERE chat_id=?",(scope,)).fetchall()]
        session_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM inventory_sessions WHERE chat_id=?",(scope,)).fetchall()]
        department_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM departments WHERE chat_id=?",(scope,)).fetchall()]
        stock_rule_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM stock_alert_rules WHERE chat_id=?",(scope,)).fetchall()]
        if stock_rule_ids:
            marks=','.join('?' for _ in stock_rule_ids)
            conn.execute(f"DELETE FROM stock_alert_snoozes WHERE rule_id IN ({marks})",stock_rule_ids)
        if department_ids:
            marks=','.join('?' for _ in department_ids)
            conn.execute(f"DELETE FROM department_members WHERE department_id IN ({marks})",department_ids)
            conn.execute(f"DELETE FROM department_entity_rules WHERE department_id IN ({marks})",department_ids)
            conn.execute(f"DELETE FROM department_operation_rules WHERE department_id IN ({marks})",department_ids)
        if session_ids:
            marks=','.join('?' for _ in session_ids); conn.execute(f"DELETE FROM inventory_session_items WHERE session_id IN ({marks})",session_ids)
        if entity_ids:
            marks=','.join('?' for _ in entity_ids)
            conn.execute(f"DELETE FROM product_components WHERE product_id IN ({marks}) OR component_id IN ({marks})", entity_ids+entity_ids)
            conn.execute(f"DELETE FROM meter_area_bindings WHERE meter_id IN ({marks})",entity_ids)
            conn.execute(f"DELETE FROM stock_item_area_bindings WHERE stock_item_id IN ({marks})",entity_ids)
        if area_ids:
            marks=','.join('?' for _ in area_ids); conn.execute(f"DELETE FROM chat_area_bindings WHERE area_id IN ({marks})",area_ids)
        delete_order=[
            "stock_alert_incidents","stock_observations","operational_events","stock_alert_rules",
            "inventory_approval_escalations","report_delivery_history","report_schedules","inbox_items",
            "worker_shifts","shift_plans","shift_templates","notification_preferences","inventory_sessions",
            "report_presets","assembly_plan_targets","area_section_access",
            "operations","inventory","aliases",
            "material_stock_settings","export_preferences","setup_sessions","operation_destinations","workers",
            "job_titles","departments","entities","areas",
        ]
        for table in delete_order:
            conn.execute(f"DELETE FROM {table} WHERE chat_id=?",(scope,))
        if "account_user_access" in tables:
            conn.execute("DELETE FROM account_user_access WHERE account_id=?",(account.id,))
        if "account_chat_access" in tables:
            conn.execute("DELETE FROM account_chat_access WHERE account_id=?",(account.id,))
        insert_order=[
            "areas","job_titles","entities","departments","department_operation_rules","department_entity_rules","department_members","workers","operation_destinations","export_preferences",
            "material_stock_settings","area_section_access","aliases","inventory","operations",
            "stock_alert_rules","stock_alert_snoozes","stock_observations","operational_events","stock_alert_incidents",
            "assembly_plan_targets","report_presets","inventory_sessions","shift_templates","shift_plans",
            "worker_shifts","report_schedules","inbox_items","report_delivery_history",
            "notification_preferences","inventory_approval_escalations","product_components",
            "meter_area_bindings","stock_item_area_bindings","chat_area_bindings","inventory_session_items",
            "setup_sessions","account_user_access","account_chat_access",
        ]
        inserted=0
        for table in insert_order:
            rows=tables.get(table) or []
            columns_available={str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for row in rows:
                clean={k:v for k,v in row.items() if k in columns_available}
                if table in ACCOUNT_TABLES: clean[ACCOUNT_TABLES[table]]=scope
                if table in {"account_user_access","account_chat_access"}: clean["account_id"]=account.id
                if not clean: continue
                columns=list(clean); marks=','.join('?' for _ in columns)
                conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({marks})",tuple(clean[c] for c in columns))
                inserted+=1
        violations=conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValueError("В копии нарушены связи данных.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    db.init_db()
    return {"inserted_rows": inserted, "safety_backup": safety_copy.name, "created_at": payload.get("created_at") or ""}
