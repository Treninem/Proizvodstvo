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
