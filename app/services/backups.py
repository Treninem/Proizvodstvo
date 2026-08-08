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
    "entity_codes": "chat_id",
    "aliases": "chat_id",
    "inventory": "chat_id",
    "operations": "chat_id",
    "operation_presets": "chat_id",
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
    "shift_sync_packages": "chat_id",
    "shift_handovers": "chat_id",
    "shift_continuity_settings": "chat_id",
    "shift_continuity_reminders": "chat_id",
    "supervisor_decisions": "chat_id",
    "control_sla_settings": "chat_id",
    "sla_breach_notifications": "chat_id",
    "shift_handover_checklist_templates": "chat_id",
    "label_templates": "chat_id",
    "notification_preferences": "chat_id",
    "inventory_approval_escalations": "chat_id",
    "departments": "chat_id",
    "stock_alert_rules": "chat_id",
    "stock_observations": "chat_id",
    "operational_events": "chat_id",
    "stock_alert_incidents": "chat_id",
    "production_tasks": "chat_id",
    "interdepartment_requests": "chat_id",
    "production_lots": "chat_id",
    "equipment": "chat_id",
    "equipment_downtimes": "chat_id",
    "maintenance_records": "chat_id",
    "workflow_notifications": "chat_id",
    "quality_rules": "chat_id",
    "quality_inspections": "chat_id",
    "replenishment_settings": "chat_id",
    "replenishment_requests": "chat_id",
    "maintenance_plans": "chat_id",
    "maintenance_work_orders": "chat_id",
    "reliability_journal": "chat_id",
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

    package_ids = [int(row["id"]) for row in tables_payload.get("shift_sync_packages", [])]
    if package_ids:
        marks = ",".join("?" for _ in package_ids)
        tables_payload["shift_sync_items"] = _rows("shift_sync_items", f"package_id IN ({marks})", package_ids)
    else:
        tables_payload["shift_sync_items"] = []

    handover_ids = [int(row["id"]) for row in tables_payload.get("shift_handovers", [])]
    if handover_ids:
        marks = ",".join("?" for _ in handover_ids)
        tables_payload["shift_handover_checks"] = _rows("shift_handover_checks", f"handover_id IN ({marks})", handover_ids)
    else:
        tables_payload["shift_handover_checks"] = []

    checklist_template_ids = [int(row["id"]) for row in tables_payload.get("shift_handover_checklist_templates", [])]
    if checklist_template_ids:
        marks = ",".join("?" for _ in checklist_template_ids)
        tables_payload["shift_handover_checklist_items"] = _rows("shift_handover_checklist_items", f"template_id IN ({marks})", checklist_template_ids)
    else:
        tables_payload["shift_handover_checklist_items"] = []

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

    task_ids = [int(row["id"]) for row in tables_payload.get("production_tasks", [])]
    if task_ids:
        marks = ",".join("?" for _ in task_ids)
        tables_payload["production_task_events"] = _rows("production_task_events", f"task_id IN ({marks})", task_ids)
    else:
        tables_payload["production_task_events"] = []

    request_ids = [int(row["id"]) for row in tables_payload.get("interdepartment_requests", [])]
    if request_ids:
        marks = ",".join("?" for _ in request_ids)
        tables_payload["interdepartment_request_events"] = _rows("interdepartment_request_events", f"request_id IN ({marks})", request_ids)
    else:
        tables_payload["interdepartment_request_events"] = []

    lot_ids = [int(row["id"]) for row in tables_payload.get("production_lots", [])]
    if lot_ids:
        marks = ",".join("?" for _ in lot_ids)
        tables_payload["lot_inventory"] = _rows("lot_inventory", f"lot_id IN ({marks})", lot_ids)
        tables_payload["lot_relations"] = _rows("lot_relations", f"parent_lot_id IN ({marks}) OR component_lot_id IN ({marks})", lot_ids + lot_ids)
        operation_ids = [int(row["id"]) for row in tables_payload.get("operations", [])]
        if operation_ids:
            op_marks = ",".join("?" for _ in operation_ids)
            tables_payload["lot_operation_links"] = _rows("lot_operation_links", f"operation_id IN ({op_marks})", operation_ids)
        else:
            tables_payload["lot_operation_links"] = []
    else:
        tables_payload["lot_inventory"] = []
        tables_payload["lot_relations"] = []
        tables_payload["lot_operation_links"] = []


    inspection_ids = [int(row["id"]) for row in tables_payload.get("quality_inspections", [])]
    if inspection_ids:
        marks = ",".join("?" for _ in inspection_ids)
        tables_payload["quality_defects"] = _rows("quality_defects", f"inspection_id IN ({marks})", inspection_ids)
        tables_payload["quality_actions"] = _rows("quality_actions", f"inspection_id IN ({marks})", inspection_ids)
    else:
        tables_payload["quality_defects"] = []
        tables_payload["quality_actions"] = []

    replenishment_ids = [int(row["id"]) for row in tables_payload.get("replenishment_requests", [])]
    if replenishment_ids:
        marks = ",".join("?" for _ in replenishment_ids)
        tables_payload["replenishment_request_events"] = _rows("replenishment_request_events", f"request_id IN ({marks})", replenishment_ids)
    else:
        tables_payload["replenishment_request_events"] = []

    maintenance_plan_ids = [int(row["id"]) for row in tables_payload.get("maintenance_plans", [])]
    if maintenance_plan_ids:
        marks = ",".join("?" for _ in maintenance_plan_ids)
        tables_payload["maintenance_checklist_items"] = _rows("maintenance_checklist_items", f"plan_id IN ({marks})", maintenance_plan_ids)
        tables_payload["maintenance_spare_parts"] = _rows("maintenance_spare_parts", f"plan_id IN ({marks})", maintenance_plan_ids)
    else:
        tables_payload["maintenance_checklist_items"] = []
        tables_payload["maintenance_spare_parts"] = []

    maintenance_work_ids = [int(row["id"]) for row in tables_payload.get("maintenance_work_orders", [])]
    if maintenance_work_ids:
        marks = ",".join("?" for _ in maintenance_work_ids)
        tables_payload["maintenance_work_checks"] = _rows("maintenance_work_checks", f"work_order_id IN ({marks})", maintenance_work_ids)
        tables_payload["maintenance_work_parts"] = _rows("maintenance_work_parts", f"work_order_id IN ({marks})", maintenance_work_ids)
    else:
        tables_payload["maintenance_work_checks"] = []
        tables_payload["maintenance_work_parts"] = []

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
    "chat_area_bindings", "inventory_session_items", "shift_sync_items", "shift_handover_checks",
    "shift_handover_checklist_items", "department_operation_rules", "department_entity_rules", "department_members",
    "account_user_access", "account_chat_access", "stock_alert_snoozes",
    "production_task_events", "interdepartment_request_events", "lot_inventory", "lot_operation_links", "lot_relations",
    "quality_defects", "quality_actions", "replenishment_request_events",
    "maintenance_checklist_items", "maintenance_spare_parts", "maintenance_work_checks", "maintenance_work_parts",
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
        package_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM shift_sync_packages WHERE chat_id=?",(scope,)).fetchall()]
        handover_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM shift_handovers WHERE chat_id=?",(scope,)).fetchall()]
        checklist_template_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM shift_handover_checklist_templates WHERE chat_id=?",(scope,)).fetchall()]
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
        if package_ids:
            marks=','.join('?' for _ in package_ids); conn.execute(f"DELETE FROM shift_sync_items WHERE package_id IN ({marks})",package_ids)
        if handover_ids:
            marks=','.join('?' for _ in handover_ids); conn.execute(f"DELETE FROM shift_handover_checks WHERE handover_id IN ({marks})",handover_ids)
        if checklist_template_ids:
            marks=','.join('?' for _ in checklist_template_ids); conn.execute(f"DELETE FROM shift_handover_checklist_items WHERE template_id IN ({marks})",checklist_template_ids)
        if entity_ids:
            marks=','.join('?' for _ in entity_ids)
            conn.execute(f"DELETE FROM product_components WHERE product_id IN ({marks}) OR component_id IN ({marks})", entity_ids+entity_ids)
            conn.execute(f"DELETE FROM meter_area_bindings WHERE meter_id IN ({marks})",entity_ids)
            conn.execute(f"DELETE FROM stock_item_area_bindings WHERE stock_item_id IN ({marks})",entity_ids)
        if area_ids:
            marks=','.join('?' for _ in area_ids); conn.execute(f"DELETE FROM chat_area_bindings WHERE area_id IN ({marks})",area_ids)
        task_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM production_tasks WHERE chat_id=?",(scope,)).fetchall()]
        request_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM interdepartment_requests WHERE chat_id=?",(scope,)).fetchall()]
        lot_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM production_lots WHERE chat_id=?",(scope,)).fetchall()]
        operation_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM operations WHERE chat_id=?",(scope,)).fetchall()]
        quality_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM quality_inspections WHERE chat_id=?",(scope,)).fetchall()]
        repl_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM replenishment_requests WHERE chat_id=?",(scope,)).fetchall()]
        maint_plan_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM maintenance_plans WHERE chat_id=?",(scope,)).fetchall()]
        maint_work_ids=[int(r["id"]) for r in conn.execute("SELECT id FROM maintenance_work_orders WHERE chat_id=?",(scope,)).fetchall()]
        if quality_ids:
            marks=','.join('?' for _ in quality_ids); conn.execute(f"DELETE FROM quality_defects WHERE inspection_id IN ({marks})",quality_ids); conn.execute(f"DELETE FROM quality_actions WHERE inspection_id IN ({marks})",quality_ids)
        if repl_ids:
            marks=','.join('?' for _ in repl_ids); conn.execute(f"DELETE FROM replenishment_request_events WHERE request_id IN ({marks})",repl_ids)
        if maint_work_ids:
            marks=','.join('?' for _ in maint_work_ids); conn.execute(f"DELETE FROM maintenance_work_checks WHERE work_order_id IN ({marks})",maint_work_ids); conn.execute(f"DELETE FROM maintenance_work_parts WHERE work_order_id IN ({marks})",maint_work_ids)
        if maint_plan_ids:
            marks=','.join('?' for _ in maint_plan_ids); conn.execute(f"DELETE FROM maintenance_checklist_items WHERE plan_id IN ({marks})",maint_plan_ids); conn.execute(f"DELETE FROM maintenance_spare_parts WHERE plan_id IN ({marks})",maint_plan_ids)
        if task_ids:
            marks=','.join('?' for _ in task_ids); conn.execute(f"DELETE FROM production_task_events WHERE task_id IN ({marks})",task_ids)
        if request_ids:
            marks=','.join('?' for _ in request_ids); conn.execute(f"DELETE FROM interdepartment_request_events WHERE request_id IN ({marks})",request_ids)
        if lot_ids:
            marks=','.join('?' for _ in lot_ids)
            conn.execute(f"DELETE FROM lot_inventory WHERE lot_id IN ({marks})",lot_ids)
            conn.execute(f"DELETE FROM lot_relations WHERE parent_lot_id IN ({marks}) OR component_lot_id IN ({marks})",lot_ids+lot_ids)
        if operation_ids:
            marks=','.join('?' for _ in operation_ids); conn.execute(f"DELETE FROM lot_operation_links WHERE operation_id IN ({marks})",operation_ids)
        delete_order=[
            "workflow_notifications","reliability_journal",
            "maintenance_work_orders","maintenance_plans","quality_inspections","quality_rules","replenishment_requests","replenishment_settings",
            "maintenance_records","equipment_downtimes","equipment",
            "interdepartment_requests","production_tasks","production_lots",
            "stock_alert_incidents","stock_observations","operational_events","stock_alert_rules",
            "inventory_approval_escalations","report_delivery_history","report_schedules","inbox_items",
            "sla_breach_notifications","supervisor_decisions","control_sla_settings",
            "shift_continuity_reminders","shift_continuity_settings","label_templates",
            "shift_handovers","shift_sync_packages","shift_handover_checklist_templates",
            "worker_shifts","shift_plans","shift_templates","notification_preferences","inventory_sessions",
            "report_presets","assembly_plan_targets","area_section_access",
            "operation_presets","operations","inventory","entity_codes","aliases",
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
            "material_stock_settings","area_section_access","aliases","entity_codes","inventory",
            "production_lots","production_tasks","production_task_events","interdepartment_requests","interdepartment_request_events","equipment","equipment_downtimes","maintenance_records",
            "quality_rules","quality_inspections","quality_defects","quality_actions","replenishment_settings","replenishment_requests","replenishment_request_events",
            "maintenance_plans","maintenance_checklist_items","maintenance_spare_parts","maintenance_work_orders","maintenance_work_checks","maintenance_work_parts","workflow_notifications",
            "operations","lot_operation_links","lot_inventory","lot_relations","reliability_journal","operation_presets",
            "stock_alert_rules","stock_alert_snoozes","stock_observations","operational_events","stock_alert_incidents",
            "assembly_plan_targets","report_presets","inventory_sessions","shift_templates","shift_plans",
            "worker_shifts","shift_sync_packages","shift_sync_items","shift_handover_checklist_templates","shift_handover_checklist_items","shift_handovers","shift_handover_checks",
            "shift_continuity_settings","shift_continuity_reminders","control_sla_settings","supervisor_decisions","sla_breach_notifications",
            "label_templates","report_schedules","inbox_items","report_delivery_history",
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
