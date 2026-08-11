from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .config import settings

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA wal_autocheckpoint=1000;
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

CREATE TABLE IF NOT EXISTS entity_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    normalized TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, normalized),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_entity_codes_entity
ON entity_codes(entity_id, is_primary DESC, id);

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

CREATE TABLE IF NOT EXISTS operation_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    quantity REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'шт',
    area_id INTEGER,
    from_area_id INTEGER,
    to_area_id INTEGER,
    destination_type TEXT NOT NULL DEFAULT '',
    storage_place TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    usage_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, user_id, name),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL,
    FOREIGN KEY(from_area_id) REFERENCES areas(id) ON DELETE SET NULL,
    FOREIGN KEY(to_area_id) REFERENCES areas(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_operation_presets_user
ON operation_presets(chat_id, user_id, usage_count DESC, updated_at DESC);

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



CREATE TABLE IF NOT EXISTS assembly_plan_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    target_qty REAL NOT NULL,
    is_archived INTEGER NOT NULL DEFAULT 0,
    is_notified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, product_id, target_qty),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(product_id) REFERENCES entities(id) ON DELETE CASCADE
);



CREATE TABLE IF NOT EXISTS material_stock_settings (
    chat_id INTEGER NOT NULL,
    material_id INTEGER NOT NULL,
    min_work_days REAL NOT NULL DEFAULT 5,
    average_days INTEGER NOT NULL DEFAULT 14,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(chat_id, material_id),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(material_id) REFERENCES entities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS operation_destinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    normalized TEXT NOT NULL,
    destination_type TEXT NOT NULL DEFAULT 'storage',
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, normalized),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS security_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    user_id INTEGER,
    event_type TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS area_section_access (
    chat_id INTEGER NOT NULL,
    job_title_id INTEGER NOT NULL,
    area_id INTEGER NOT NULL,
    section_key TEXT NOT NULL,
    can_view INTEGER NOT NULL DEFAULT 1,
    can_submit INTEGER NOT NULL DEFAULT 0,
    can_edit INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(chat_id, job_title_id, area_id, section_key),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(job_title_id) REFERENCES job_titles(id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS site_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    user_id INTEGER,
    action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS report_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    normalized TEXT NOT NULL,
    request_text TEXT NOT NULL DEFAULT 'отчёт за месяц',
    report_format TEXT NOT NULL DEFAULT 'xlsx',
    area_id INTEGER,
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, user_id, normalized),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL
);


CREATE TABLE IF NOT EXISTS inventory_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    area_id INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    note TEXT NOT NULL DEFAULT '',
    submitted_at TEXT,
    decided_by INTEGER,
    decided_at TEXT,
    decision_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS inventory_session_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    unit TEXT NOT NULL DEFAULT 'шт',
    system_quantity REAL NOT NULL DEFAULT 0,
    actual_quantity REAL NOT NULL DEFAULT 0,
    approved_system_quantity REAL,
    applied_delta REAL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, entity_type, entity_id, unit),
    FOREIGN KEY(session_id) REFERENCES inventory_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS worker_shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    area_id INTEGER,
    started_by INTEGER NOT NULL,
    ended_by INTEGER,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_shifts_open
ON worker_shifts(chat_id, user_id)
WHERE status='open';

CREATE TABLE IF NOT EXISTS report_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    preset_id INTEGER NOT NULL UNIQUE,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    delivery_chat_id INTEGER NOT NULL,
    frequency TEXT NOT NULL DEFAULT 'daily',
    hour INTEGER NOT NULL DEFAULT 8,
    minute INTEGER NOT NULL DEFAULT 0,
    weekday INTEGER NOT NULL DEFAULT 0,
    month_day INTEGER NOT NULL DEFAULT 1,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    next_run_at TEXT NOT NULL,
    last_run_at TEXT,
    last_status TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(preset_id) REFERENCES report_presets(id) ON DELETE CASCADE,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS inbox_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    recipient_user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    related_type TEXT NOT NULL DEFAULT '',
    related_id INTEGER,
    status TEXT NOT NULL DEFAULT 'unread',
    telegram_status TEXT NOT NULL DEFAULT 'queued',
    telegram_error TEXT NOT NULL DEFAULT '',
    telegram_attempts INTEGER NOT NULL DEFAULT 0,
    telegram_next_attempt_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at TEXT,
    resolved_at TEXT,
    sent_at TEXT,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_inbox_recipient_status
ON inbox_items(recipient_user_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_inbox_telegram_queue
ON inbox_items(telegram_status, id);

CREATE TABLE IF NOT EXISTS shift_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    area_id INTEGER,
    planned_start TEXT NOT NULL,
    planned_end TEXT NOT NULL,
    created_by INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_shift_plans_worker_time
ON shift_plans(chat_id, user_id, planned_start, status);

CREATE TABLE IF NOT EXISTS report_delivery_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    preset_id INTEGER,
    preset_name TEXT NOT NULL DEFAULT '',
    trigger_type TEXT NOT NULL DEFAULT 'scheduled',
    status TEXT NOT NULL DEFAULT 'queued',
    delivery_chat_id INTEGER NOT NULL,
    report_format TEXT NOT NULL DEFAULT 'xlsx',
    retry_of INTEGER,
    started_at TEXT,
    finished_at TEXT,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(schedule_id) REFERENCES report_schedules(id) ON DELETE SET NULL,
    FOREIGN KEY(retry_of) REFERENCES report_delivery_history(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_report_delivery_history_owner
ON report_delivery_history(chat_id, user_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_report_delivery_queue
ON report_delivery_history(status, id);

CREATE TABLE IF NOT EXISTS shift_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    area_id INTEGER,
    pattern_type TEXT NOT NULL DEFAULT 'weekly',
    weekdays_json TEXT NOT NULL DEFAULT '[]',
    cycle_work_days INTEGER NOT NULL DEFAULT 2,
    cycle_rest_days INTEGER NOT NULL DEFAULT 2,
    cycle_anchor_date TEXT,
    start_time TEXT NOT NULL DEFAULT '09:00',
    end_time TEXT NOT NULL DEFAULT '18:00',
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    last_generated_until TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_shift_templates_active
ON shift_templates(chat_id, is_enabled, valid_from);

CREATE TABLE IF NOT EXISTS notification_preferences (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    inbox_enabled INTEGER NOT NULL DEFAULT 1,
    telegram_enabled INTEGER NOT NULL DEFAULT 1,
    inventory_approval_enabled INTEGER NOT NULL DEFAULT 1,
    inventory_result_enabled INTEGER NOT NULL DEFAULT 1,
    shift_plan_enabled INTEGER NOT NULL DEFAULT 1,
    approval_reminders_enabled INTEGER NOT NULL DEFAULT 1,
    reminder_after_minutes INTEGER NOT NULL DEFAULT 60,
    repeat_every_minutes INTEGER NOT NULL DEFAULT 120,
    max_reminders INTEGER NOT NULL DEFAULT 3,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(chat_id, user_id),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS inventory_approval_escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    recipient_user_id INTEGER NOT NULL,
    escalation_level INTEGER NOT NULL,
    inbox_item_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, recipient_user_id, escalation_level),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(session_id) REFERENCES inventory_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(inbox_item_id) REFERENCES inbox_items(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_inventory_approval_escalations
ON inventory_approval_escalations(chat_id, session_id, recipient_user_id);


CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    normalized TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, normalized),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS department_operation_rules (
    department_id INTEGER NOT NULL,
    operation_key TEXT NOT NULL,
    can_view INTEGER NOT NULL DEFAULT 1,
    can_submit INTEGER NOT NULL DEFAULT 0,
    can_edit INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(department_id, operation_key),
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS department_entity_rules (
    department_id INTEGER NOT NULL,
    operation_key TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    can_view INTEGER NOT NULL DEFAULT 1,
    can_submit INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(department_id, operation_key, entity_type, entity_id),
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS department_members (
    department_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    role_level INTEGER NOT NULL DEFAULT 20,
    operation_keys_json TEXT NOT NULL DEFAULT '[]',
    is_active INTEGER NOT NULL DEFAULT 1,
    granted_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(department_id, user_id),
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_department_members_user
ON department_members(user_id, is_active, department_id);

CREATE INDEX IF NOT EXISTS idx_department_entity_rules_entity
ON department_entity_rules(entity_id, department_id, operation_key);

CREATE TABLE IF NOT EXISTS system_admins (
    user_id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    granted_by INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_system_admins_active
ON system_admins(is_active, user_id);


CREATE TABLE IF NOT EXISTS stock_alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    area_id INTEGER,
    name TEXT NOT NULL DEFAULT '',
    is_enabled INTEGER NOT NULL DEFAULT 1,
    calculation_mode TEXT NOT NULL DEFAULT 'hybrid',
    manual_consumption_qty REAL NOT NULL DEFAULT 0,
    manual_period TEXT NOT NULL DEFAULT 'shift',
    shifts_per_day REAL NOT NULL DEFAULT 1,
    work_days_per_week REAL NOT NULL DEFAULT 5,
    warning_shifts REAL NOT NULL DEFAULT 10,
    critical_shifts REAL NOT NULL DEFAULT 5,
    emergency_shifts REAL NOT NULL DEFAULT 1,
    absolute_warning_qty REAL,
    absolute_critical_qty REAL,
    safety_buffer_qty REAL NOT NULL DEFAULT 0,
    learning_window_days INTEGER NOT NULL DEFAULT 28,
    minimum_samples INTEGER NOT NULL DEFAULT 2,
    stale_after_hours INTEGER NOT NULL DEFAULT 168,
    anomaly_multiplier REAL NOT NULL DEFAULT 2,
    demand_multiplier REAL NOT NULL DEFAULT 1,
    yield_output_entity_id INTEGER,
    yield_input_qty REAL NOT NULL DEFAULT 0,
    yield_output_qty REAL NOT NULL DEFAULT 0,
    planned_output_entity_id INTEGER,
    planned_output_qty REAL NOT NULL DEFAULT 0,
    planned_output_period TEXT NOT NULL DEFAULT 'shift',
    notify_owner INTEGER NOT NULL DEFAULT 1,
    notify_system_admins INTEGER NOT NULL DEFAULT 1,
    notify_department_heads INTEGER NOT NULL DEFAULT 1,
    notify_work_chat INTEGER NOT NULL DEFAULT 0,
    notify_user_ids_json TEXT NOT NULL DEFAULT '[]',
    repeat_minutes INTEGER NOT NULL DEFAULT 180,
    alert_on_stale INTEGER NOT NULL DEFAULT 1,
    alert_on_negative INTEGER NOT NULL DEFAULT 1,
    alert_on_anomaly INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, entity_type, entity_id, area_id),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE CASCADE,
    FOREIGN KEY(yield_output_entity_id) REFERENCES entities(id) ON DELETE SET NULL,
    FOREIGN KEY(planned_output_entity_id) REFERENCES entities(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_stock_alert_rules_enabled
ON stock_alert_rules(chat_id, is_enabled, entity_id, area_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_alert_rule_global_unique
ON stock_alert_rules(chat_id, entity_type, entity_id) WHERE area_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_alert_rule_area_unique
ON stock_alert_rules(chat_id, entity_type, entity_id, area_id) WHERE area_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS stock_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    area_id INTEGER,
    user_id INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'bot',
    observation_type TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT 'шт',
    period_kind TEXT NOT NULL DEFAULT 'instant',
    period_count REAL NOT NULL DEFAULT 1,
    period_start TEXT,
    period_end TEXT,
    note TEXT NOT NULL DEFAULT '',
    operation_id INTEGER,
    dedupe_key TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dedupe_key),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL,
    FOREIGN KEY(operation_id) REFERENCES operations(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_stock_observations_lookup
ON stock_observations(chat_id, entity_id, area_id, observation_type, created_at DESC);

CREATE TABLE IF NOT EXISTS operational_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    area_id INTEGER,
    department_id INTEGER,
    entity_id INTEGER,
    severity TEXT NOT NULL DEFAULT 'warning',
    impact_kind TEXT NOT NULL DEFAULT 'info',
    impact_value REAL NOT NULL DEFAULT 0,
    unavailable_quantity REAL NOT NULL DEFAULT 0,
    starts_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ends_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    resolved_by INTEGER,
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE SET NULL,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_operational_events_active
ON operational_events(chat_id, status, starts_at, ends_at);

CREATE TABLE IF NOT EXISTS stock_alert_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    rule_id INTEGER NOT NULL,
    severity TEXT NOT NULL,
    reason_code TEXT NOT NULL DEFAULT '',
    reserve_shifts REAL,
    stock_quantity REAL NOT NULL DEFAULT 0,
    effective_stock REAL NOT NULL DEFAULT 0,
    consumption_per_shift REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_notified_at TEXT,
    notification_count INTEGER NOT NULL DEFAULT 0,
    acknowledged_by INTEGER,
    acknowledged_at TEXT,
    snoozed_until TEXT,
    resolved_at TEXT,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(rule_id) REFERENCES stock_alert_rules(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_alert_incident_one_open
ON stock_alert_incidents(rule_id) WHERE status='open';

CREATE INDEX IF NOT EXISTS idx_stock_alert_incidents_open
ON stock_alert_incidents(chat_id, status, severity, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS stock_alert_snoozes (
    rule_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    snoozed_until TEXT,
    acknowledged_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(rule_id, user_id),
    FOREIGN KEY(rule_id) REFERENCES stock_alert_rules(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_stock_alert_snoozes_due
ON stock_alert_snoozes(user_id, snoozed_until);


CREATE TABLE IF NOT EXISTS shift_sync_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    shift_id INTEGER,
    area_id INTEGER,
    client_package_id TEXT NOT NULL,
    device_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'received',
    item_count INTEGER NOT NULL DEFAULT 0,
    accepted_count INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_by INTEGER,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, user_id, client_package_id),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(shift_id) REFERENCES worker_shifts(id) ON DELETE SET NULL,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_shift_sync_packages_review
ON shift_sync_packages(chat_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS shift_sync_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id INTEGER NOT NULL,
    client_request_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    operation_id INTEGER,
    status TEXT NOT NULL DEFAULT 'received',
    message TEXT NOT NULL DEFAULT '',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(package_id, client_request_id),
    FOREIGN KEY(package_id) REFERENCES shift_sync_packages(id) ON DELETE CASCADE,
    FOREIGN KEY(operation_id) REFERENCES operations(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_shift_sync_items_package
ON shift_sync_items(package_id, sequence_no, id);

CREATE TABLE IF NOT EXISTS shift_handovers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    from_user_id INTEGER NOT NULL,
    to_user_id INTEGER,
    shift_id INTEGER,
    area_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open',
    summary TEXT NOT NULL DEFAULT '',
    unfinished_count INTEGER NOT NULL DEFAULT 0,
    issue_count INTEGER NOT NULL DEFAULT 0,
    package_ids_json TEXT NOT NULL DEFAULT '[]',
    created_by INTEGER NOT NULL,
    acknowledged_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    acknowledged_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(shift_id) REFERENCES worker_shifts(id) ON DELETE SET NULL,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_shift_handovers_open
ON shift_handovers(chat_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS miniapp_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    device_name TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_chat_id INTEGER,
    revoked_at TEXT,
    revoked_by INTEGER,
    revoke_reason TEXT NOT NULL DEFAULT '',
    UNIQUE(user_id, device_id)
);

CREATE INDEX IF NOT EXISTS idx_miniapp_devices_user
ON miniapp_devices(user_id, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS shift_continuity_settings (
    chat_id INTEGER PRIMARY KEY,
    package_reminder_after_minutes INTEGER NOT NULL DEFAULT 60,
    package_repeat_minutes INTEGER NOT NULL DEFAULT 120,
    handover_reminder_after_minutes INTEGER NOT NULL DEFAULT 30,
    handover_repeat_minutes INTEGER NOT NULL DEFAULT 60,
    max_reminders INTEGER NOT NULL DEFAULT 3,
    updated_by INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shift_continuity_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    reminder_kind TEXT NOT NULL,
    related_id INTEGER NOT NULL,
    recipient_user_id INTEGER NOT NULL,
    reminder_level INTEGER NOT NULL,
    inbox_item_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(reminder_kind, related_id, recipient_user_id, reminder_level),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(inbox_item_id) REFERENCES inbox_items(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_shift_continuity_reminders_lookup
ON shift_continuity_reminders(chat_id, reminder_kind, related_id, recipient_user_id);

CREATE TABLE IF NOT EXISTS shift_handover_checklist_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT 'Основной чек-лист',
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shift_handover_checklist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    is_required INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(template_id) REFERENCES shift_handover_checklist_templates(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shift_handover_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handover_id INTEGER NOT NULL,
    checklist_item_id INTEGER,
    label TEXT NOT NULL,
    is_required INTEGER NOT NULL DEFAULT 1,
    is_checked INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    checked_by INTEGER,
    checked_at TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(handover_id, checklist_item_id),
    FOREIGN KEY(handover_id) REFERENCES shift_handovers(id) ON DELETE CASCADE,
    FOREIGN KEY(checklist_item_id) REFERENCES shift_handover_checklist_items(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_shift_handover_checks_handover
ON shift_handover_checks(handover_id, sort_order, id);


CREATE TABLE IF NOT EXISTS supervisor_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    actor_user_id INTEGER NOT NULL,
    worker_user_id INTEGER,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    before_status TEXT NOT NULL DEFAULT '',
    after_status TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_supervisor_decisions_chat
ON supervisor_decisions(chat_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_supervisor_decisions_worker
ON supervisor_decisions(chat_id, worker_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS control_sla_settings (
    chat_id INTEGER PRIMARY KEY,
    package_sla_minutes INTEGER NOT NULL DEFAULT 120,
    handover_sla_minutes INTEGER NOT NULL DEFAULT 60,
    critical_alert_sla_minutes INTEGER NOT NULL DEFAULT 30,
    updated_by INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sla_breach_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    recipient_user_id INTEGER NOT NULL,
    breached_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    inbox_item_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, target_type, target_id, recipient_user_id),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(inbox_item_id) REFERENCES inbox_items(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_sla_breach_notifications_chat
ON sla_breach_notifications(chat_id, target_type, target_id);

CREATE TABLE IF NOT EXISTS system_heartbeats (
    service_key TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'ok',
    details TEXT NOT NULL DEFAULT '',
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS label_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    page_mode TEXT NOT NULL DEFAULT 'a4',
    label_width_mm REAL NOT NULL DEFAULT 63,
    label_height_mm REAL NOT NULL DEFAULT 32,
    columns_count INTEGER NOT NULL DEFAULT 3,
    rows_count INTEGER NOT NULL DEFAULT 8,
    margin_x_mm REAL NOT NULL DEFAULT 8,
    margin_y_mm REAL NOT NULL DEFAULT 8,
    gap_x_mm REAL NOT NULL DEFAULT 3,
    gap_y_mm REAL NOT NULL DEFAULT 3,
    code_size_mm REAL NOT NULL DEFAULT 21,
    code_type TEXT NOT NULL DEFAULT 'qr',
    is_default INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, name),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_label_templates_chat
ON label_templates(chat_id, is_default DESC, id);


CREATE TABLE IF NOT EXISTS production_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    department_id INTEGER NOT NULL,
    assignee_user_id INTEGER,
    shift_plan_id INTEGER,
    area_id INTEGER,
    operation_type TEXT NOT NULL DEFAULT 'production',
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    target_quantity REAL NOT NULL DEFAULT 0,
    actual_quantity REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'шт',
    priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'planned',
    due_at TEXT,
    started_at TEXT,
    paused_at TEXT,
    completed_at TEXT,
    cancelled_at TEXT,
    deviation_reason TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    output_lot_id INTEGER,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE CASCADE,
    FOREIGN KEY(shift_plan_id) REFERENCES shift_plans(id) ON DELETE SET NULL,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(output_lot_id) REFERENCES production_lots(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_production_tasks_scope
ON production_tasks(chat_id, status, department_id, assignee_user_id, due_at);

CREATE TABLE IF NOT EXISTS production_task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    actor_user_id INTEGER NOT NULL,
    operation_id INTEGER,
    event_type TEXT NOT NULL,
    from_status TEXT NOT NULL DEFAULT '',
    to_status TEXT NOT NULL DEFAULT '',
    quantity REAL,
    reason TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES production_tasks(id) ON DELETE CASCADE,
    FOREIGN KEY(operation_id) REFERENCES operations(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_task_events_task
ON production_task_events(task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS interdepartment_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    requester_department_id INTEGER NOT NULL,
    supplier_department_id INTEGER NOT NULL,
    requester_user_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    requested_quantity REAL NOT NULL,
    approved_quantity REAL,
    issued_quantity REAL NOT NULL DEFAULT 0,
    received_quantity REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'шт',
    from_area_id INTEGER,
    to_area_id INTEGER,
    priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'requested',
    needed_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    decision_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at TEXT,
    issued_at TEXT,
    received_at TEXT,
    closed_at TEXT,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(requester_department_id) REFERENCES departments(id) ON DELETE CASCADE,
    FOREIGN KEY(supplier_department_id) REFERENCES departments(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(from_area_id) REFERENCES areas(id) ON DELETE SET NULL,
    FOREIGN KEY(to_area_id) REFERENCES areas(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_interdepartment_requests_scope
ON interdepartment_requests(chat_id, status, requester_department_id, supplier_department_id, needed_at);

CREATE TABLE IF NOT EXISTS interdepartment_request_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    actor_user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    quantity REAL,
    reason TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(request_id) REFERENCES interdepartment_requests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS production_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    lot_code TEXT NOT NULL,
    supplier_code TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    manufacture_date TEXT,
    expiry_date TEXT,
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, lot_code),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_production_lots_entity
ON production_lots(chat_id, entity_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS lot_inventory (
    lot_id INTEGER NOT NULL,
    area_id INTEGER,
    quantity REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'шт',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(lot_id, area_id, unit),
    FOREIGN KEY(lot_id) REFERENCES production_lots(id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS lot_operation_links (
    operation_id INTEGER NOT NULL,
    lot_id INTEGER NOT NULL,
    link_role TEXT NOT NULL DEFAULT 'output',
    quantity REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(operation_id, lot_id, link_role),
    FOREIGN KEY(operation_id) REFERENCES operations(id) ON DELETE CASCADE,
    FOREIGN KEY(lot_id) REFERENCES production_lots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS lot_relations (
    parent_lot_id INTEGER NOT NULL,
    component_lot_id INTEGER NOT NULL,
    quantity_used REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'шт',
    task_id INTEGER,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(parent_lot_id, component_lot_id, task_id),
    FOREIGN KEY(parent_lot_id) REFERENCES production_lots(id) ON DELETE CASCADE,
    FOREIGN KEY(component_lot_id) REFERENCES production_lots(id) ON DELETE CASCADE,
    FOREIGN KEY(task_id) REFERENCES production_tasks(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS equipment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    department_id INTEGER,
    area_id INTEGER,
    name TEXT NOT NULL,
    code TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    service_interval_days INTEGER NOT NULL DEFAULT 0,
    warning_before_days INTEGER NOT NULL DEFAULT 3,
    last_service_at TEXT,
    next_service_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE SET NULL,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL,
    UNIQUE(chat_id, code)
);

CREATE INDEX IF NOT EXISTS idx_equipment_scope
ON equipment(chat_id, is_archived, department_id, area_id, status);

CREATE TABLE IF NOT EXISTS equipment_downtimes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    task_id INTEGER,
    reported_by INTEGER NOT NULL,
    reason_type TEXT NOT NULL DEFAULT 'other',
    reason TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    resolution TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
    FOREIGN KEY(task_id) REFERENCES production_tasks(id) ON DELETE SET NULL,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_equipment_downtime
ON equipment_downtimes(chat_id, status, equipment_id, started_at DESC);

CREATE TABLE IF NOT EXISTS maintenance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    actor_user_id INTEGER NOT NULL,
    maintenance_type TEXT NOT NULL DEFAULT 'planned',
    status TEXT NOT NULL DEFAULT 'completed',
    performed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    next_due_at TEXT,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS quality_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    department_id INTEGER,
    entity_id INTEGER NOT NULL,
    operation_type TEXT NOT NULL DEFAULT 'production',
    inspection_type TEXT NOT NULL DEFAULT 'output',
    is_enabled INTEGER NOT NULL DEFAULT 1,
    sample_quantity REAL NOT NULL DEFAULT 0,
    max_defect_percent REAL NOT NULL DEFAULT 0,
    require_before_task_complete INTEGER NOT NULL DEFAULT 0,
    auto_quarantine_on_fail INTEGER NOT NULL DEFAULT 1,
    create_rework_task INTEGER NOT NULL DEFAULT 1,
    rework_department_id INTEGER,
    rework_operation_type TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(rework_department_id) REFERENCES departments(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_quality_rules_unique
ON quality_rules(chat_id,entity_id,operation_type,inspection_type,COALESCE(department_id,0));

CREATE INDEX IF NOT EXISTS idx_quality_rules_enabled
ON quality_rules(chat_id,is_enabled,entity_id,department_id);

CREATE TABLE IF NOT EXISTS quality_inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    inspection_type TEXT NOT NULL DEFAULT 'output',
    department_id INTEGER,
    area_id INTEGER,
    entity_id INTEGER NOT NULL,
    lot_id INTEGER,
    task_id INTEGER,
    equipment_id INTEGER,
    shift_plan_id INTEGER,
    worker_user_id INTEGER,
    inspector_user_id INTEGER NOT NULL,
    checked_quantity REAL NOT NULL DEFAULT 0,
    defect_quantity REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'шт',
    status TEXT NOT NULL DEFAULT 'open',
    decision_reason TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    parent_inspection_id INTEGER,
    rework_task_id INTEGER,
    operational_event_id INTEGER,
    decided_by INTEGER,
    decided_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE SET NULL,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(lot_id) REFERENCES production_lots(id) ON DELETE SET NULL,
    FOREIGN KEY(task_id) REFERENCES production_tasks(id) ON DELETE SET NULL,
    FOREIGN KEY(equipment_id) REFERENCES equipment(id) ON DELETE SET NULL,
    FOREIGN KEY(shift_plan_id) REFERENCES shift_plans(id) ON DELETE SET NULL,
    FOREIGN KEY(parent_inspection_id) REFERENCES quality_inspections(id) ON DELETE SET NULL,
    FOREIGN KEY(rework_task_id) REFERENCES production_tasks(id) ON DELETE SET NULL,
    FOREIGN KEY(operational_event_id) REFERENCES operational_events(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_quality_inspections_scope
ON quality_inspections(chat_id,status,inspection_type,department_id,created_at DESC);

CREATE INDEX IF NOT EXISTS idx_quality_inspections_links
ON quality_inspections(task_id,lot_id,equipment_id,rework_task_id);

CREATE TABLE IF NOT EXISTS quality_defects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inspection_id INTEGER NOT NULL,
    defect_code TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'other',
    severity TEXT NOT NULL DEFAULT 'minor',
    quantity REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(inspection_id) REFERENCES quality_inspections(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_quality_defects_inspection
ON quality_defects(inspection_id,id);

CREATE TABLE IF NOT EXISTS quality_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inspection_id INTEGER NOT NULL,
    actor_user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    generated_task_id INTEGER,
    generated_inspection_id INTEGER,
    write_off_operation_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(inspection_id) REFERENCES quality_inspections(id) ON DELETE CASCADE,
    FOREIGN KEY(generated_task_id) REFERENCES production_tasks(id) ON DELETE SET NULL,
    FOREIGN KEY(generated_inspection_id) REFERENCES quality_inspections(id) ON DELETE SET NULL,
    FOREIGN KEY(write_off_operation_id) REFERENCES operations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS replenishment_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    area_id INTEGER,
    lead_time_days REAL NOT NULL DEFAULT 0,
    target_cover_shifts REAL NOT NULL DEFAULT 10,
    minimum_order_quantity REAL NOT NULL DEFAULT 0,
    pack_quantity REAL NOT NULL DEFAULT 0,
    preferred_supplier TEXT NOT NULL DEFAULT '',
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_replenishment_settings_unique
ON replenishment_settings(chat_id,entity_id,COALESCE(area_id,0));

CREATE TABLE IF NOT EXISTS replenishment_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    area_id INTEGER,
    requested_by INTEGER NOT NULL,
    requested_quantity REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT 'шт',
    status TEXT NOT NULL DEFAULT 'requested',
    source TEXT NOT NULL DEFAULT 'manual',
    source_rule_id INTEGER,
    source_incident_id INTEGER,
    available_quantity REAL NOT NULL DEFAULT 0,
    consumption_per_shift REAL NOT NULL DEFAULT 0,
    reserve_shifts REAL,
    recommended_quantity REAL NOT NULL DEFAULT 0,
    lead_time_days REAL NOT NULL DEFAULT 0,
    needed_at TEXT,
    supplier_note TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    approved_by INTEGER,
    approved_at TEXT,
    ordered_by INTEGER,
    ordered_at TEXT,
    received_by INTEGER,
    received_at TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL,
    FOREIGN KEY(source_rule_id) REFERENCES stock_alert_rules(id) ON DELETE SET NULL,
    FOREIGN KEY(source_incident_id) REFERENCES stock_alert_incidents(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_replenishment_requests_scope
ON replenishment_requests(chat_id,status,entity_id,needed_at);

CREATE TABLE IF NOT EXISTS replenishment_request_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    actor_user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    quantity REAL,
    reason TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(request_id) REFERENCES replenishment_requests(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS maintenance_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    equipment_id INTEGER NOT NULL UNIQUE,
    responsible_user_id INTEGER,
    interval_days INTEGER NOT NULL DEFAULT 0,
    warning_before_days INTEGER NOT NULL DEFAULT 3,
    next_due_at TEXT,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(equipment_id) REFERENCES equipment(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_maintenance_plans_due
ON maintenance_plans(chat_id,is_enabled,next_due_at);

CREATE TABLE IF NOT EXISTS maintenance_checklist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    is_required INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(plan_id) REFERENCES maintenance_plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS maintenance_spare_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    area_id INTEGER,
    planned_quantity REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'шт',
    FOREIGN KEY(plan_id) REFERENCES maintenance_plans(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS maintenance_work_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    plan_id INTEGER NOT NULL,
    equipment_id INTEGER NOT NULL,
    responsible_user_id INTEGER,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    started_at TEXT,
    completed_at TEXT,
    cancelled_at TEXT,
    result TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    maintenance_record_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(plan_id,due_at),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(plan_id) REFERENCES maintenance_plans(id) ON DELETE CASCADE,
    FOREIGN KEY(equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
    FOREIGN KEY(maintenance_record_id) REFERENCES maintenance_records(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_maintenance_work_orders_due
ON maintenance_work_orders(chat_id,status,due_at,responsible_user_id);

CREATE TABLE IF NOT EXISTS maintenance_work_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_order_id INTEGER NOT NULL,
    checklist_item_id INTEGER,
    label TEXT NOT NULL,
    is_required INTEGER NOT NULL DEFAULT 1,
    is_checked INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    checked_by INTEGER,
    checked_at TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(work_order_id) REFERENCES maintenance_work_orders(id) ON DELETE CASCADE,
    FOREIGN KEY(checklist_item_id) REFERENCES maintenance_checklist_items(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS maintenance_work_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_order_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    area_id INTEGER,
    planned_quantity REAL NOT NULL DEFAULT 0,
    actual_quantity REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'шт',
    operation_id INTEGER,
    FOREIGN KEY(work_order_id) REFERENCES maintenance_work_orders(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE SET NULL,
    FOREIGN KEY(operation_id) REFERENCES operations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS reliability_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    object_type TEXT NOT NULL,
    object_id INTEGER NOT NULL,
    step_key TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    next_retry_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(object_type,object_id,step_key),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reliability_journal_pending
ON reliability_journal(status,next_retry_at,id);

CREATE TABLE IF NOT EXISTS workflow_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    object_type TEXT NOT NULL,
    object_id INTEGER NOT NULL,
    notification_key TEXT NOT NULL,
    recipient_user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, object_type, object_id, notification_key, recipient_user_id),
    FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
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
    conn = sqlite3.connect(settings.database_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_test_modes (
                user_id INTEGER PRIMARY KEY,
                is_enabled INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _ensure_column(conn, "pending_confirmations", "is_test", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "operations", "from_area_id", "INTEGER")
        _ensure_column(conn, "operations", "to_area_id", "INTEGER")
        _ensure_column(conn, "operations", "destination_type", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "operations", "storage_place", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "operations", "client_request_id", "TEXT")
        _ensure_column(conn, "operations", "source_channel", "TEXT NOT NULL DEFAULT 'bot'")
        _ensure_column(conn, "operations", "task_id", "INTEGER")
        _ensure_column(conn, "operations", "lot_id", "INTEGER")
        _ensure_column(conn, "miniapp_devices", "app_version", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miniapp_devices", "sync_status", "TEXT NOT NULL DEFAULT 'unknown'")
        _ensure_column(conn, "miniapp_devices", "pending_queue_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "miniapp_devices", "draft_present", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "miniapp_devices", "last_sync_at", "TEXT")
        _ensure_column(conn, "miniapp_devices", "last_sync_error", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miniapp_devices", "last_health_at", "TEXT")
        _ensure_column(conn, "production_tasks", "shift_plan_id", "INTEGER")
        _ensure_column(conn, "production_task_events", "operation_id", "INTEGER")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_task_events_operation_unique ON production_task_events(operation_id) WHERE operation_id IS NOT NULL")
        _ensure_column(conn, "worker_shifts", "plan_id", "INTEGER")
        _ensure_column(conn, "worker_shifts", "start_deviation_minutes", "REAL")
        _ensure_column(conn, "worker_shifts", "end_deviation_minutes", "REAL")
        _ensure_column(conn, "report_schedules", "timezone_name", "TEXT NOT NULL DEFAULT 'server'")
        _ensure_column(conn, "shift_plans", "template_id", "INTEGER")
        _ensure_column(conn, "shift_plans", "occurrence_date", "TEXT")
        _ensure_column(conn, "inbox_items", "priority", "TEXT NOT NULL DEFAULT 'normal'")
        _ensure_column(conn, "inbox_items", "site_visible", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "inbox_items", "telegram_attempts", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "inbox_items", "telegram_next_attempt_at", "TEXT")
        _ensure_column(conn, "stock_alert_rules", "planned_output_entity_id", "INTEGER")
        _ensure_column(conn, "stock_alert_rules", "planned_output_qty", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "stock_alert_rules", "planned_output_period", "TEXT NOT NULL DEFAULT 'shift'")
        _ensure_column(conn, "stock_alert_rules", "notify_work_chat", "INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_shift_plan_template_occurrence ON shift_plans(template_id, occurrence_date) WHERE template_id IS NOT NULL AND occurrence_date IS NOT NULL")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_operations_client_request ON operations(chat_id, user_id, client_request_id) WHERE client_request_id IS NOT NULL AND client_request_id<>''")
        conn.commit()


def database_probe(timeout_ms: int = 1000) -> dict[str, Any]:
    """Короткая проверка готовности БД без длинного ожидания блокировки."""
    ensure_data_dir()
    started = __import__("time").monotonic()
    try:
        conn = sqlite3.connect(settings.database_path, timeout=max(0.1, float(timeout_ms) / 1000.0))
        try:
            conn.execute(f"PRAGMA busy_timeout={max(100, int(timeout_ms))}")
            row = conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return {"ok": bool(row and row[0] == 1), "latency_ms": round((__import__("time").monotonic()-started)*1000, 1), "error": ""}
    except Exception as exc:
        return {"ok": False, "latency_ms": round((__import__("time").monotonic()-started)*1000, 1), "error": str(exc)[:500]}


def checkpoint_wal() -> dict[str, Any]:
    """Безопасный PASSIVE-checkpoint: не мешает текущим записям."""
    try:
        with connect() as conn:
            row = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        values = list(row or (0, 0, 0))
        while len(values) < 3:
            values.append(0)
        return {"ok": int(values[0] or 0) == 0, "busy": int(values[0] or 0), "log_frames": int(values[1] or 0), "checkpointed_frames": int(values[2] or 0), "error": ""}
    except Exception as exc:
        return {"ok": False, "busy": 0, "log_frames": 0, "checkpointed_frames": 0, "error": str(exc)[:500]}


def database_file_state() -> dict[str, Any]:
    path = Path(settings.database_path)
    wal = Path(str(path) + "-wal")
    shm = Path(str(path) + "-shm")
    return {
        "database_bytes": path.stat().st_size if path.exists() else 0,
        "wal_bytes": wal.stat().st_size if wal.exists() else 0,
        "shm_bytes": shm.stat().st_size if shm.exists() else 0,
    }


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
