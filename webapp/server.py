from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import time
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.db import init_db
from app import db
from app.services import accounting
from app.services import backups
from app.services import dashboard as dash
from app.services import reporting
from app.services import inventory_sessions as inventory_session_service
from app.services import report_scheduler
from app.services import repository as repo
from app.services.site_security import validate_telegram_init_data
from app.services import stock_risk
from app.services import shift_continuity
from app.services import labels as label_service
from app.services import continuity_audit
from app.services import control_center
from app.services import production_flow
from app.services import quality_control
from app.services import replenishment
from app.services import maintenance_planning
from app.services import production_needs_report
from app.services import stock_transfers, excel_bridge

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"

_OPERATION_SAVE_LOCK = RLock()
_REQUEST_DEVICE: ContextVar[dict[str, str]] = ContextVar("miniapp_request_device", default={})
_PROCESS_STARTED_AT = time.time()

app = FastAPI(title="Производственный учёт — Mini App", docs_url=None, redoc_url=None)
if settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Access-Token", "X-Telegram-Init-Data", "X-Device-Id", "X-Device-Name", "X-Device-Platform"],
    )
if settings.trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))
app.mount("/static", StaticFiles(directory=STATIC), name="static")



@app.middleware("http")
async def _security_headers(request: Request, call_next):
    token = _REQUEST_DEVICE.set({
        "device_id": str(request.headers.get("X-Device-Id") or "")[:120],
        "device_name": str(request.headers.get("X-Device-Name") or "")[:160],
        "platform": str(request.headers.get("X-Device-Platform") or "")[:80],
        "user_agent": str(request.headers.get("User-Agent") or "")[:500],
    })
    try:
        response = await call_next(request)
    finally:
        _REQUEST_DEVICE.reset(token)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; connect-src 'self' https:; frame-ancestors 'self' https:; base-uri 'self'; form-action 'self'",
    )
    if request.url.path.startswith("/api/") or request.url.path == "/mini":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif request.url.path.startswith("/static/"):
        # Versioned filenames allow long caching without serving stale UI after a deploy.
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    return response




class AccountSelectPayload(BaseModel):
    user_id: int
    account_id: int


class CompanySitePayload(BaseModel):
    chat_id: int
    user_id: int
    settlement: str = ""
    name: str
    address: str = ""

class StorageLocationPayload(BaseModel):
    chat_id: int
    user_id: int
    name: str
    site_id: int | None = None
    area_id: int | None = None
    department_id: int | None = None
    code: str = ""

class AreaSitePayload(BaseModel):
    chat_id: int
    user_id: int
    area_id: int
    site_id: int | None = None

class TransferItemPayload(BaseModel):
    entity_id: int
    quantity: float
    unit: str = "шт"

class TransferCreatePayload(BaseModel):
    chat_id: int
    user_id: int
    from_area_id: int
    to_area_id: int
    from_department_id: int | None = None
    to_department_id: int | None = None
    from_location_id: int | None = None
    to_location_id: int | None = None
    note: str = ""
    items: list[TransferItemPayload]

class TransferAcceptItemPayload(BaseModel):
    item_id: int
    quantity: float

class TransferAcceptPayload(BaseModel):
    chat_id: int
    user_id: int
    transfer_id: int
    note: str = ""
    items: list[TransferAcceptItemPayload] = Field(default_factory=list)

class ExcelConfirmPayload(BaseModel):
    chat_id: int
    user_id: int
    batch_id: str
    create_missing: bool = True

class ClientSyncPayload(BaseModel):
    chat_id: int
    user_id: int
    app_version: str = ""
    sync_status: str = "ok"
    pending_queue_count: int = 0
    draft_present: bool = False
    last_error: str = ""


class OperationPayload(BaseModel):
    chat_id: int
    user_id: int
    operation_type: str
    entity_type: str
    entity_id: int
    quantity: float
    unit: str = "шт"
    area_id: int | None = None
    from_area_id: int | None = None
    to_area_id: int | None = None
    destination_type: str = ""
    storage_place: str = ""
    note: str = ""
    client_request_id: str = ""
    confirm_warnings: bool = False
    preset_id: int | None = None
    preview_fingerprint: str = ""
    task_id: int | None = None
    lot_id: int | None = None
    department_id: int | None = None
    from_department_id: int | None = None
    to_department_id: int | None = None
    storage_location_id: int | None = None
    from_location_id: int | None = None
    to_location_id: int | None = None


class EntityCodePayload(BaseModel):
    chat_id: int
    user_id: int
    entity_id: int
    code: str


class OperationPresetPayload(BaseModel):
    chat_id: int
    user_id: int
    name: str
    operation_type: str
    entity_type: str
    entity_id: int
    quantity: float = 0
    unit: str = "шт"
    area_id: int | None = None
    from_area_id: int | None = None
    to_area_id: int | None = None
    destination_type: str = ""
    storage_place: str = ""
    note: str = ""


class PlanPayload(BaseModel):
    chat_id: int
    user_id: int
    product_id: int
    targets: list[float] = Field(default_factory=list)


class ReportPayload(BaseModel):
    chat_id: int
    user_id: int
    request_text: str = "отчёт за месяц"
    format: str = "xlsx"
    area_id: int | None = None


class DestinationPayload(BaseModel):
    chat_id: int
    user_id: int
    destination_id: int | None = None
    name: str
    destination_type: str = "storage"


class AreaAccessPayload(BaseModel):
    chat_id: int
    user_id: int
    job_title_id: int
    area_id: int
    section_key: str
    can_view: bool = True
    can_submit: bool = False
    can_edit: bool = False


class JobTitlePayload(BaseModel):
    chat_id: int
    user_id: int
    job_title_id: int | None = None
    name: str
    permissions: dict[str, bool] = Field(default_factory=dict)


class WorkerPayload(BaseModel):
    chat_id: int
    user_id: int
    worker_user_id: int
    display_name: str = ""
    job_title_id: int


class DepartmentPayload(BaseModel):
    chat_id: int
    user_id: int
    department_id: int | None = None
    name: str
    description: str = ""


class DepartmentOperationPayload(BaseModel):
    chat_id: int
    user_id: int
    department_id: int
    operation_key: str
    can_view: bool = True
    can_submit: bool = False
    can_edit: bool = False


class DepartmentEntityPayload(BaseModel):
    chat_id: int
    user_id: int
    department_id: int
    operation_key: str
    entity_type: str
    entity_id: int
    can_view: bool = True
    can_submit: bool = True


class DepartmentMemberPayload(BaseModel):
    chat_id: int
    user_id: int
    department_id: int
    member_user_id: int
    display_name: str = ""
    role_level: int = 20
    operation_keys: list[str] = Field(default_factory=list)


class InventoryCorrectionPayload(BaseModel):
    chat_id: int
    user_id: int
    area_id: int
    entity_type: str
    entity_id: int
    actual_quantity: float
    unit: str = ""
    note: str = ""


class ReportPresetPayload(BaseModel):
    chat_id: int
    user_id: int
    preset_id: int | None = None
    name: str
    request_text: str = "отчёт за месяц"
    format: str = "xlsx"
    area_id: int | None = None



class InventorySessionPayload(BaseModel):
    chat_id: int
    user_id: int
    area_id: int
    note: str = ""


class InventorySessionItemPayload(BaseModel):
    chat_id: int
    user_id: int
    session_id: int
    entity_type: str
    entity_id: int
    actual_quantity: float
    unit: str = ""
    note: str = ""


class InventorySessionActionPayload(BaseModel):
    chat_id: int
    user_id: int
    session_id: int
    action: str
    note: str = ""


class ShiftPayload(BaseModel):
    chat_id: int
    user_id: int
    worker_user_id: int | None = None
    area_id: int | None = None
    note: str = ""


class ReportSchedulePayload(BaseModel):
    chat_id: int
    user_id: int
    preset_id: int
    delivery_chat_id: int | None = None
    frequency: str = "daily"
    hour: int = 8
    minute: int = 0
    weekday: int = 0
    month_day: int = 1
    enabled: bool = True
    timezone_name: str = "server"


class ShiftPlanPayload(BaseModel):
    chat_id: int
    user_id: int
    worker_user_id: int
    area_id: int | None = None
    planned_start: str
    planned_end: str
    note: str = ""


class InboxReadPayload(BaseModel):
    chat_id: int
    user_id: int
    item_id: int


class ReportRetryPayload(BaseModel):
    chat_id: int
    user_id: int
    schedule_id: int
    history_id: int | None = None


class NotificationPreferencesPayload(BaseModel):
    chat_id: int
    user_id: int
    inbox_enabled: bool = True
    telegram_enabled: bool = True
    inventory_approval_enabled: bool = True
    inventory_result_enabled: bool = True
    shift_plan_enabled: bool = True
    approval_reminders_enabled: bool = True
    reminder_after_minutes: int = 60
    repeat_every_minutes: int = 120
    max_reminders: int = 3


class SLASettingsPayload(BaseModel):
    chat_id: int
    user_id: int
    package_sla_minutes: int = 120
    handover_sla_minutes: int = 60
    critical_alert_sla_minutes: int = 30


class OperationPresetBatchPayload(BaseModel):
    chat_id: int
    user_id: int
    preset_id: int
    multipliers: list[float] = Field(default_factory=list)


class StockAlertRulePayload(BaseModel):
    chat_id: int
    user_id: int
    rule_id: int | None = None
    entity_type: str
    entity_id: int
    area_id: int | None = None
    name: str = ""
    is_enabled: bool = True
    calculation_mode: str = "hybrid"
    manual_consumption_qty: float = 0
    manual_period: str = "shift"
    shifts_per_day: float = 1
    work_days_per_week: float = 5
    warning_shifts: float = 10
    critical_shifts: float = 5
    emergency_shifts: float = 1
    absolute_warning_qty: float | None = None
    absolute_critical_qty: float | None = None
    safety_buffer_qty: float = 0
    learning_window_days: int = 28
    minimum_samples: int = 2
    stale_after_hours: int = 168
    anomaly_multiplier: float = 2
    demand_multiplier: float = 1
    yield_output_entity_id: int | None = None
    yield_input_qty: float = 0
    yield_output_qty: float = 0
    planned_output_entity_id: int | None = None
    planned_output_qty: float = 0
    planned_output_period: str = "shift"
    notify_owner: bool = True
    notify_system_admins: bool = True
    notify_department_heads: bool = True
    notify_work_chat: bool = False
    notify_user_ids: list[int] = Field(default_factory=list)
    repeat_minutes: int = 180
    alert_on_stale: bool = True
    alert_on_negative: bool = True
    alert_on_anomaly: bool = True


class StockObservationPayload(BaseModel):
    chat_id: int
    user_id: int
    mode: str
    entity_type: str
    entity_id: int
    area_id: int | None = None
    quantity: float
    unit: str = ""
    period_kind: str = "instant"
    period_count: float = 1
    note: str = ""


class OperationalEventPayload(BaseModel):
    chat_id: int
    user_id: int
    event_id: int | None = None
    event_type: str = "force_majeure"
    title: str = ""
    area_id: int | None = None
    department_id: int | None = None
    entity_id: int | None = None
    severity: str = "warning"
    impact_kind: str = "info"
    impact_value: float = 0
    unavailable_quantity: float = 0
    starts_at: str = ""
    ends_at: str | None = None
    note: str = ""


class IncidentActionPayload(BaseModel):
    chat_id: int
    user_id: int
    incident_id: int
    snooze_minutes: int = 0


class ShiftTemplatePayload(BaseModel):
    chat_id: int
    user_id: int
    template_id: int | None = None
    worker_user_id: int
    area_id: int | None = None
    pattern_type: str = "weekly"
    weekdays: list[int] = Field(default_factory=list)
    cycle_work_days: int = 2
    cycle_rest_days: int = 2
    cycle_anchor_date: str | None = None
    start_time: str = "09:00"
    end_time: str = "18:00"
    valid_from: str
    valid_until: str | None = None
    enabled: bool = True
    note: str = ""


class RestoreBackupPayload(BaseModel):
    chat_id: int
    user_id: int
    filename: str
    content_base64: str
    confirmation: str

class ShiftPackageSyncPayload(BaseModel):
    chat_id: int
    user_id: int
    client_package_id: str
    shift_id: int | None = None
    area_id: int | None = None
    note: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)


class ShiftPackageItemActionPayload(BaseModel):
    chat_id: int
    user_id: int
    item_id: int
    action: str
    note: str = ""


class ShiftPackageBulkActionPayload(BaseModel):
    chat_id: int
    user_id: int
    item_ids: list[int] = Field(default_factory=list)
    action: str
    note: str = ""


class ContinuitySettingsPayload(BaseModel):
    chat_id: int
    user_id: int
    package_reminder_after_minutes: int = 60
    package_repeat_minutes: int = 120
    handover_reminder_after_minutes: int = 30
    handover_repeat_minutes: int = 60
    max_reminders: int = 3


class HandoverChecklistSettingsPayload(BaseModel):
    chat_id: int
    user_id: int
    name: str = "Основной чек-лист"
    items: list[dict[str, Any]] = Field(default_factory=list)


class LabelTemplatePayload(BaseModel):
    chat_id: int
    user_id: int
    name: str
    page_mode: str = "a4"
    label_width_mm: float = 63
    label_height_mm: float = 32
    columns_count: int = 3
    rows_count: int = 8
    margin_x_mm: float = 8
    margin_y_mm: float = 8
    gap_x_mm: float = 3
    gap_y_mm: float = 3
    code_size_mm: float = 21
    code_type: str = "qr"
    is_default: bool = False


class ShiftHandoverPayload(BaseModel):
    chat_id: int
    user_id: int
    from_user_id: int | None = None
    to_user_id: int | None = None
    shift_id: int | None = None
    area_id: int | None = None
    summary: str = ""
    package_ids: list[int] = Field(default_factory=list)
    checklist: list[dict[str, Any]] = Field(default_factory=list)


class ShiftHandoverActionPayload(BaseModel):
    chat_id: int
    user_id: int
    handover_id: int


class DeviceActionPayload(BaseModel):
    chat_id: int
    user_id: int
    target_user_id: int
    device_id: str
    action: str
    reason: str = ""












class ProductionTaskPayload(BaseModel):
    chat_id: int
    user_id: int
    department_id: int
    entity_id: int
    operation_type: str = "production"
    target_quantity: float
    unit: str = "шт"
    title: str = ""
    assignee_user_id: int | None = None
    shift_plan_id: int | None = None
    area_id: int | None = None
    priority: str = "normal"
    due_at: str | None = None
    note: str = ""
    output_lot_id: int | None = None


class ProductionTaskActionPayload(BaseModel):
    chat_id: int
    user_id: int
    task_id: int
    action: str
    reason: str = ""
    note: str = ""


class DepartmentRequestPayload(BaseModel):
    chat_id: int
    user_id: int
    requester_department_id: int
    supplier_department_id: int
    entity_id: int
    quantity: float
    unit: str = "шт"
    from_area_id: int | None = None
    to_area_id: int | None = None
    priority: str = "normal"
    needed_at: str | None = None
    note: str = ""


class DepartmentRequestActionPayload(BaseModel):
    chat_id: int
    user_id: int
    request_id: int
    action: str
    quantity: float | None = None
    reason: str = ""
    note: str = ""


class ProductionLotPayload(BaseModel):
    chat_id: int
    user_id: int
    entity_id: int
    lot_code: str
    supplier_code: str = ""
    manufacture_date: str | None = None
    expiry_date: str | None = None
    note: str = ""


class LotRelationPayload(BaseModel):
    chat_id: int
    user_id: int
    parent_lot_id: int
    component_lot_id: int
    quantity: float
    unit: str = "шт"
    task_id: int | None = None


class EquipmentPayload(BaseModel):
    chat_id: int
    user_id: int
    equipment_id: int | None = None
    department_id: int | None = None
    area_id: int | None = None
    name: str
    code: str = ""
    status: str = "active"
    service_interval_days: int = 0
    warning_before_days: int = 3
    note: str = ""


class EquipmentDowntimePayload(BaseModel):
    chat_id: int
    user_id: int
    equipment_id: int
    reason_type: str = "other"
    reason: str
    task_id: int | None = None


class EquipmentDowntimeClosePayload(BaseModel):
    chat_id: int
    user_id: int
    downtime_id: int
    resolution: str


class MaintenancePayload(BaseModel):
    chat_id: int
    user_id: int
    equipment_id: int
    maintenance_type: str = "planned"
    note: str = ""


class QualityRulePayload(BaseModel):
    chat_id: int
    user_id: int
    rule_id: int | None = None
    department_id: int | None = None
    entity_id: int
    operation_type: str = "production"
    inspection_type: str = "output"
    is_enabled: bool = True
    sample_quantity: float = 0
    max_defect_percent: float = 0
    require_before_task_complete: bool = False
    auto_quarantine_on_fail: bool = True
    create_rework_task: bool = True
    rework_department_id: int | None = None
    rework_operation_type: str = ""


class QualityInspectionPayload(BaseModel):
    chat_id: int
    user_id: int
    inspection_type: str = "output"
    department_id: int | None = None
    area_id: int | None = None
    entity_id: int
    lot_id: int | None = None
    task_id: int | None = None
    equipment_id: int | None = None
    shift_plan_id: int | None = None
    worker_user_id: int | None = None
    checked_quantity: float = 0
    defect_quantity: float = 0
    unit: str = "шт"
    note: str = ""
    defects: list[dict[str, Any]] = Field(default_factory=list)


class QualityDecisionPayload(BaseModel):
    chat_id: int
    user_id: int
    inspection_id: int
    decision: str
    reason: str = ""
    area_id: int | None = None
    note: str = ""


class ReplenishmentSettingPayload(BaseModel):
    chat_id: int
    user_id: int
    entity_id: int
    area_id: int | None = None
    lead_time_days: float = 0
    target_cover_shifts: float = 10
    minimum_order_quantity: float = 0
    pack_quantity: float = 0
    preferred_supplier: str = ""
    is_enabled: bool = True


class ReplenishmentRequestPayload(BaseModel):
    chat_id: int
    user_id: int
    entity_id: int
    area_id: int | None = None
    requested_quantity: float
    unit: str = "шт"
    source: str = "manual"
    source_rule_id: int | None = None
    recommended_quantity: float = 0
    lead_time_days: float = 0
    needed_at: str | None = None
    supplier_note: str = ""
    reason: str = ""
    note: str = ""


class ReplenishmentActionPayload(BaseModel):
    chat_id: int
    user_id: int
    request_id: int
    action: str
    quantity: float | None = None
    reason: str = ""
    note: str = ""


class MaintenancePlanPayload(BaseModel):
    chat_id: int
    user_id: int
    equipment_id: int
    responsible_user_id: int | None = None
    interval_days: int = 0
    warning_before_days: int = 3
    next_due_at: str | None = None
    is_enabled: bool = True
    note: str = ""
    checklist: list[dict[str, Any]] = Field(default_factory=list)
    spare_parts: list[dict[str, Any]] = Field(default_factory=list)


class MaintenanceWorkActionPayload(BaseModel):
    chat_id: int
    user_id: int
    work_order_id: int
    action: str
    result: str = ""
    note: str = ""


class MaintenanceCheckPayload(BaseModel):
    chat_id: int
    user_id: int
    work_order_id: int
    check_id: int
    checked: bool = True
    note: str = ""


class MaintenancePartPayload(BaseModel):
    chat_id: int
    user_id: int
    work_order_id: int
    part_id: int
    actual_quantity: float = 0


def _check_token(token: str | None, init_data: str | None = None) -> int | None:
    expected = settings.miniapp_api_token
    if expected and token == expected:
        # Служебный ключ не должен позволять подменять произвольный user_id из запроса.
        # Он действует от имени системного владельца; несовпадающий user_id будет отвергнут _request_user().
        return int(settings.primary_owner_id)
    user = validate_telegram_init_data(init_data or "", settings.bot_token)
    if user.get("id"):
        user_id = int(user["id"])
        device = _REQUEST_DEVICE.get({})
        result = shift_continuity.touch_device(
            user_id,
            device.get("device_id", ""),
            device_name=device.get("device_name", ""),
            platform=device.get("platform", ""),
            user_agent=device.get("user_agent", ""),
        )
        if not result.get("allowed", True):
            raise HTTPException(status_code=403, detail="Доступ с этого устройства отозван владельцем.")
        return user_id
    if settings.bot_token or settings.miniapp_api_token:
        raise HTTPException(status_code=403, detail="access denied")
    return None


def _request_user(user_id: int | None, auth_user_id: int | None) -> int | None:
    if auth_user_id is not None:
        if user_id is not None and int(user_id) != int(auth_user_id):
            raise HTTPException(status_code=403, detail="access denied")
        return int(auth_user_id)
    return user_id


def _account_for_chat(chat_id: int) -> repo.AccountingAccount | None:
    scope = repo.resolve_scope_chat_id(int(chat_id))
    return repo.get_account_by_scope(scope)


def _check_user(chat_id: int, user_id: int | None, *, submit: bool = False, manage: bool = False) -> repo.AccountingAccount | None:
    account = _account_for_chat(chat_id)
    if not account:
        return None
    if user_id:
        device = _REQUEST_DEVICE.get({})
        shift_continuity.set_device_chat(int(user_id), device.get("device_id", ""), account.scope_chat_id)
    if not user_id:
        raise HTTPException(status_code=403, detail="access denied")
    if manage and not repo.user_has_account_access(account.id, user_id, require_manage=True):
        raise HTTPException(status_code=403, detail="access denied")
    if submit:
        row_ok = repo.user_has_account_access(account.id, user_id, require_manage=False)
        if not row_ok:
            raise HTTPException(status_code=403, detail="access denied")
    elif not repo.user_has_account_access(account.id, user_id, require_manage=False):
        raise HTTPException(status_code=403, detail="access denied")
    return account


def _check_system_admin(chat_id: int, user_id: int | None) -> None:
    _check_user(chat_id, user_id)
    if not repo.is_tenant_admin(chat_id, user_id):
        raise HTTPException(status_code=403, detail="admin access denied")


def _department_manage_allowed(chat_id: int, user_id: int | None) -> bool:
    return repo.user_can_manage_departments(chat_id, user_id)


def _permission_key(operation_type: str) -> str:
    return {
        "production": "production",
        "material_in": "material",
        "material_out": "material",
        "energy": "energy",
        "assembly": "assembly",
        "movement": "movement",
        "transfer_to_assembly": "movement",
        "shipment": "shipment",
        "shipment_client": "shipment",
        "shipment_fulfillment": "fulfillment",
        "return": "returns",
        "stock_in": "stock",
        "stock_out": "stock",
        "write_off": "stock",
        "inventory_adjust": "stock",
    }.get(operation_type, "")


def _operation_area_section(operation_type: str) -> str:
    return {
        "production": "production",
        "material_in": "material",
        "material_out": "material",
        "assembly": "assembly",
        "movement": "movement",
        "transfer_to_assembly": "movement",
        "shipment": "shipment",
        "shipment_client": "shipment",
        "shipment_fulfillment": "shipment",
        "return": "returns",
        "stock_in": "production",
        "stock_out": "production",
        "write_off": "production",
        "inventory_adjust": "production",
    }.get(operation_type, "")


def _check_operation_permission(
    chat_id: int,
    user_id: int,
    operation_type: str,
    *,
    area_id: int | None = None,
    from_area_id: int | None = None,
    to_area_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
) -> None:
    if repo.is_tenant_admin(chat_id, user_id):
        return
    department_allowed = repo.department_operation_allowed(chat_id, user_id, operation_type, "submit", entity_type, entity_id)
    if department_allowed is False:
        raise HTTPException(status_code=403, detail="department access denied")
    if department_allowed is True:
        permissions = repo.user_permissions_current_context(chat_id, user_id)
    else:
        permissions = repo.user_permissions_current_context(chat_id, user_id)
    key = _permission_key(operation_type)
    if not key or not permissions.get(key):
        raise HTTPException(status_code=403, detail="access denied")
    section = _operation_area_section(operation_type)
    if not section:
        return
    area_ids = [value for value in (from_area_id, to_area_id) if value is not None] if section == "movement" else [area_id]
    for selected_area in area_ids:
        if not repo.user_area_action_allowed(chat_id, user_id, section, selected_area, "submit"):
            raise HTTPException(status_code=403, detail="area access denied")


def _dashboard_area_ids(chat_id: int, user_id: int) -> set[int] | None:
    access = repo.area_section_access_for_user(chat_id, user_id, "overview")
    if not access.get("restricted"):
        return None
    return set(access.get("view") or [])


def _inventory_area_ids(chat_id: int, user_id: int) -> set[int] | None:
    access = repo.area_section_access_for_user(chat_id, user_id, "inventory")
    if not access.get("restricted"):
        return None
    return set(access.get("view") or [])


def _report_presets_for_user(chat_id: int, user_id: int) -> list[dict]:
    presets = repo.list_report_presets(chat_id, user_id)
    access = repo.area_section_access_for_user(chat_id, user_id, "reports")
    if not access.get("restricted"):
        return presets
    allowed = set(access.get("view") or [])
    return [item for item in presets if item.get("area_id") is None or int(item["area_id"]) in allowed]


def _entity_list(chat_id: int, user_id: int | None = None) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {"component": [], "product": [], "material": [], "stock_item": [], "meter": []}
    for item in repo.visible_entities_for_user(chat_id, user_id):
        result.setdefault(item.entity_type, []).append(
            {
                "id": item.id,
                "type": item.entity_type,
                "name": item.name,
                "unit": item.default_unit,
                "code": repo.primary_entity_code(item.id),
            }
        )
    return result


def _inventory_positions_for_user(chat_id: int, user_id: int, area_ids: set[int] | None = None) -> list[dict]:
    rows = repo.list_inventory_positions(chat_id, area_ids=area_ids)
    visible_ids = repo.visible_entity_ids_for_user(chat_id, user_id)
    if visible_ids is None:
        return rows
    return [item for item in rows if int(item.get("entity_id") or 0) in visible_ids]


def _dashboard_for_user(chat_id: int, user_id: int) -> dict:
    # Полная сводка содержит данные всего производства. Участникам отделов она
    # не передаётся; их рабочая панель строится только из назначенных позиций.
    if repo.user_has_department_membership(chat_id, user_id) and not repo.is_tenant_admin(chat_id, user_id):
        return {"month_totals": [], "inventory": {}, "area_summary": [], "material_days_by_area": [], "alerts": [], "recent": []}
    return dash.dashboard(chat_id, area_ids=_dashboard_area_ids(chat_id, user_id))


def _area_list(chat_id: int) -> list[dict[str, Any]]:
    return [{"id": a.id, "name": a.name} for a in repo.list_areas(chat_id)]


def _allowed_entity_types(operation_type: str) -> set[str]:
    if operation_type == "production":
        return {"component", "stock_item"}
    if operation_type in {"material_in", "material_out"}:
        return {"material"}
    if operation_type == "energy":
        return {"meter"}
    if operation_type == "assembly":
        return {"product"}
    if operation_type in {"shipment", "shipment_client", "shipment_fulfillment", "return"}:
        return {"product", "stock_item"}
    if operation_type in {"movement", "transfer_to_assembly", "stock_in", "stock_out", "write_off", "inventory_adjust"}:
        return {"component", "product", "material", "stock_item"}
    return set()



def _validate_tenant_operation_context(scope: int, user_id: int, payload: OperationPayload) -> None:
    """Reject forged department/location IDs before any preview or write."""
    tenant_admin = repo.is_tenant_admin(scope, user_id)
    source_departments = [payload.department_id, payload.from_department_id]
    for dep_id in [payload.department_id, payload.from_department_id, payload.to_department_id]:
        if dep_id is None:
            continue
        row = db.fetchone("SELECT id FROM departments WHERE id=? AND chat_id=?", (int(dep_id), int(scope)))
        if not row:
            raise HTTPException(status_code=400, detail="Отдел не принадлежит выбранному учёту.")
    if not tenant_admin:
        member_ids = {int(x.get("department_id") or 0) for x in repo.user_department_memberships(scope, user_id)}
        for dep_id in source_departments:
            if dep_id is not None and int(dep_id) not in member_ids:
                raise HTTPException(status_code=403, detail="Нельзя выполнять операцию от имени чужого отдела.")
    checks = [
        (payload.storage_location_id, payload.area_id, payload.department_id),
        (payload.from_location_id, payload.from_area_id, payload.from_department_id),
        (payload.to_location_id, payload.to_area_id, payload.to_department_id),
    ]
    for location_id, area_id, department_id in checks:
        if location_id is None:
            continue
        row = db.fetchone(
            "SELECT area_id,department_id FROM storage_locations WHERE id=? AND chat_id=? AND is_active=1",
            (int(location_id), int(scope)),
        )
        if not row:
            raise HTTPException(status_code=400, detail="Место хранения не принадлежит выбранному учёту.")
        if area_id is not None and row["area_id"] is not None and int(row["area_id"]) != int(area_id):
            raise HTTPException(status_code=400, detail="Место хранения относится к другому участку.")
        if department_id is not None and row["department_id"] is not None and int(row["department_id"]) != int(department_id):
            raise HTTPException(status_code=400, detail="Место хранения относится к другому отделу.")

def _operation_preview(scope: int, user_id: int, payload: OperationPayload) -> dict[str, object]:
    _validate_tenant_operation_context(scope, user_id, payload)
    _check_operation_permission(
        scope,
        user_id,
        payload.operation_type,
        area_id=payload.area_id,
        from_area_id=payload.from_area_id,
        to_area_id=payload.to_area_id,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
    )
    entity = repo.get_entity(payload.entity_id)
    if not entity or entity.chat_id != scope or entity.entity_type != payload.entity_type:
        raise HTTPException(status_code=400, detail="Позиция не найдена.")
    if payload.quantity <= 0:
        raise HTTPException(status_code=400, detail="Количество должно быть больше нуля.")
    if payload.operation_type not in {"production", "material_in", "material_out", "energy", "assembly", "movement", "transfer_to_assembly", "shipment", "shipment_client", "shipment_fulfillment", "return", "stock_in", "stock_out", "write_off", "inventory_adjust"}:
        raise HTTPException(status_code=400, detail="Неизвестное действие.")
    if entity.entity_type not in _allowed_entity_types(payload.operation_type):
        raise HTTPException(status_code=400, detail="Эта позиция не подходит для выбранного действия.")
    if payload.operation_type in {"movement", "transfer_to_assembly"}:
        if not payload.from_area_id or not payload.to_area_id:
            raise HTTPException(status_code=400, detail="Выберите площадку отправления и получения.")
        if payload.from_area_id == payload.to_area_id:
            raise HTTPException(status_code=400, detail="Площадки отправления и получения должны отличаться.")

    unit = payload.unit or entity.default_unit or "шт"
    warnings: list[str] = []
    balances: list[dict[str, object]] = []
    components: list[dict[str, object]] = []

    def area_name(area_id: int | None) -> str:
        if area_id is None:
            return "Общий склад"
        area = repo.get_area(int(area_id))
        return area.name if area else f"Площадка {area_id}"

    incoming = {"production", "material_in", "return", "stock_in"}
    outgoing = {"material_out", "shipment", "shipment_client", "shipment_fulfillment", "stock_out", "write_off"}
    if payload.operation_type in incoming | outgoing:
        current = repo.inventory_quantity(scope, payload.entity_type, payload.entity_id, unit, payload.area_id)
        delta = payload.quantity if payload.operation_type in incoming else -payload.quantity
        after = current + delta
        balances.append({
            "label": area_name(payload.area_id), "current": current, "delta": delta, "after": after, "unit": unit,
        })
        if after < 0:
            warnings.append(f"После операции остаток «{entity.name}» станет отрицательным: {after:g} {unit}.")
    elif payload.operation_type in {"movement", "transfer_to_assembly"}:
        current_from = repo.inventory_quantity(scope, payload.entity_type, payload.entity_id, unit, payload.from_area_id)
        current_to = repo.inventory_quantity(scope, payload.entity_type, payload.entity_id, unit, payload.to_area_id)
        after_from = current_from - payload.quantity
        balances.extend([
            {"label": area_name(payload.from_area_id), "current": current_from, "delta": -payload.quantity, "after": after_from, "unit": unit},
            {"label": area_name(payload.to_area_id), "current": current_to, "delta": payload.quantity, "after": current_to + payload.quantity, "unit": unit},
        ])
        if after_from < 0:
            warnings.append(f"На площадке «{area_name(payload.from_area_id)}» не хватает «{entity.name}»: доступно {current_from:g} {unit}.")
    elif payload.operation_type == "assembly":
        current_product = repo.inventory_quantity(scope, "product", payload.entity_id, unit, payload.area_id)
        balances.append({
            "label": area_name(payload.area_id), "current": current_product, "delta": payload.quantity,
            "after": current_product + payload.quantity, "unit": unit,
        })
        composition = repo.list_product_components(payload.entity_id)
        if not composition:
            warnings.append("Для изделия не указан состав. Комплектующие автоматически не спишутся.")
        for comp in composition:
            need = float(comp.get("quantity") or 0) * float(payload.quantity)
            comp_unit = str(comp.get("default_unit") or "шт")
            current = repo.inventory_quantity(scope, "component", int(comp["component_id"]), comp_unit, payload.area_id)
            after = current - need
            item = {
                "entity_id": int(comp["component_id"]), "name": str(comp.get("name") or "Комплектующая"),
                "required": need, "current": current, "after": after, "unit": comp_unit,
            }
            components.append(item)
            if after < 0:
                warnings.append(f"Не хватает «{item['name']}»: нужно {need:g} {comp_unit}, доступно {current:g} {comp_unit}.")
    elif payload.operation_type == "inventory_adjust":
        current = repo.inventory_quantity(scope, payload.entity_type, payload.entity_id, unit, payload.area_id)
        balances.append({
            "label": area_name(payload.area_id), "current": current, "delta": payload.quantity,
            "after": current + payload.quantity, "unit": unit,
        })

    if payload.area_id is None and payload.operation_type not in {"movement", "transfer_to_assembly", "energy"} and len(repo.list_areas(scope)) > 1:
        warnings.append("Площадка не выбрана. Запись попадёт на общий склад.")
    if payload.operation_type in {"shipment_client", "shipment_fulfillment"} and not payload.storage_place:
        warnings.append("Получатель не выбран. В записи останется только общий тип отгрузки.")

    operation_label = {
        "production": "Изготовление", "material_in": "Приход", "material_out": "Расход",
        "energy": "Показание", "assembly": "Сборка", "movement": "Перемещение",
        "transfer_to_assembly": "Передача", "shipment": "Отгрузка",
        "shipment_client": "Передача заказчику", "shipment_fulfillment": "Передача на внешний склад",
        "return": "Возврат", "stock_in": "Приход на склад", "stock_out": "Расход со склада",
        "write_off": "Списание", "inventory_adjust": "Корректировка остатка",
    }.get(payload.operation_type, payload.operation_type)
    summary = f"{operation_label}: {entity.name} — {payload.quantity:g} {unit}"
    fingerprint_payload = {
        "operation_type": payload.operation_type, "entity_type": payload.entity_type, "entity_id": payload.entity_id,
        "quantity": float(payload.quantity), "unit": unit, "area_id": payload.area_id,
        "from_area_id": payload.from_area_id, "to_area_id": payload.to_area_id,
        "department_id": payload.department_id, "from_department_id": payload.from_department_id, "to_department_id": payload.to_department_id,
        "storage_location_id": payload.storage_location_id, "from_location_id": payload.from_location_id, "to_location_id": payload.to_location_id,
        "balances": balances, "components": components,
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:32]
    return {
        "summary": summary, "entity_name": entity.name, "unit": unit, "warnings": warnings,
        "requires_confirmation": bool(warnings), "balances": balances, "components": components,
        "fingerprint": fingerprint,
    }


def _operation_item_allowed(scope: int, user_id: int, item: dict[str, object]) -> bool:
    try:
        _check_operation_permission(
            scope, user_id, str(item.get("operation_type") or ""),
            area_id=int(item["area_id"]) if item.get("area_id") is not None else None,
            from_area_id=int(item["from_area_id"]) if item.get("from_area_id") is not None else None,
            to_area_id=int(item["to_area_id"]) if item.get("to_area_id") is not None else None,
            entity_type=str(item.get("entity_type") or ""),
            entity_id=int(item.get("entity_id") or 0),
        )
        return True
    except HTTPException:
        return False


def _work_access_for_user(scope: int, user_id: int) -> list[dict]:
    department = repo.department_work_access_for_user(scope, user_id)
    if department:
        return department
    permissions = repo.user_permissions_current_context(scope, user_id)
    entities = _entity_list(scope, user_id)
    operation_types = {
        "production": ("production", ["component", "stock_item"]),
        "material_in": ("material", ["material"]),
        "material_out": ("material", ["material"]),
        "energy": ("energy", ["meter"]),
        "assembly": ("assembly", ["product"]),
        "movement": ("movement", ["component", "product", "material", "stock_item"]),
        "transfer_to_assembly": ("movement", ["component", "product", "material", "stock_item"]),
        "shipment": ("shipment", ["product", "stock_item"]),
        "shipment_client": ("shipment", ["product", "stock_item"]),
        "shipment_fulfillment": ("fulfillment", ["product", "stock_item"]),
        "return": ("returns", ["product", "stock_item"]),
        "stock_in": ("stock", ["stock_item"]),
        "stock_out": ("stock", ["stock_item"]),
        "write_off": ("stock", ["component", "product", "material", "stock_item"]),
        "inventory_adjust": ("stock", ["component", "product", "material", "stock_item"]),
    }
    result: list[dict] = []
    for operation_key, (permission_key, types) in operation_types.items():
        if not (permissions.get(permission_key) or repo.is_tenant_admin(scope, user_id)):
            continue
        available: list[dict] = []
        for entity_type in types:
            available.extend(entities.get(entity_type) or [])
        if available:
            result.append({"operation_key": operation_key, "entities": available})
    return result


def _operation_presets_for_user(scope: int, user_id: int) -> list[dict]:
    return [item for item in repo.list_operation_presets(scope, user_id) if _operation_item_allowed(scope, user_id, item)]


def _recent_operations_for_user(scope: int, user_id: int) -> list[dict]:
    return [item for item in repo.list_recent_operation_templates(scope, user_id, 12) if _operation_item_allowed(scope, user_id, item)]


def _risk_for_user(chat_id: int, user_id: int) -> dict[str, object]:
    return stock_risk.dashboard_for_user(chat_id, user_id)


def _shift_packages_for_user(chat_id: int, user_id: int, **filters: Any) -> list[dict[str, Any]]:
    can_review = repo.is_tenant_admin(chat_id, user_id) or repo.user_can_manage_departments(chat_id, user_id)
    if not can_review:
        filters = dict(filters)
        filters["worker_user_id"] = int(user_id)
        filters.pop("department_id", None)
        return shift_continuity.list_shift_packages(chat_id, limit=100, **filters)
    rows = shift_continuity.list_shift_packages(chat_id, limit=200, **filters)
    return [
        item for item in rows
        if int(item.get("user_id") or 0) == int(user_id)
        or shift_continuity.can_review_worker_packages(chat_id, user_id, int(item.get("user_id") or 0))
    ]


def _device_rows_for_user(chat_id: int, user_id: int) -> list[dict[str, Any]]:
    rows = shift_continuity.list_devices(shift_continuity.account_user_ids(chat_id)) if repo.is_tenant_admin(chat_id, user_id) else shift_continuity.list_devices([user_id])
    # Build/version is platform-owner information and is intentionally not exposed
    # through ordinary tenant Mini App APIs.
    result=[]
    for item in rows:
        clean=dict(item); clean.pop("app_version", None); result.append(clean)
    return result


_MINIAPP_HEARTBEAT_TASK: asyncio.Task | None = None


async def _miniapp_heartbeat_loop() -> None:
    while True:
        try:
            control_center.heartbeat("miniapp", "ok", "event loop active")
        except Exception:
            pass
        await asyncio.sleep(60)


@app.on_event("startup")
async def _startup() -> None:
    global _MINIAPP_HEARTBEAT_TASK
    init_db()
    control_center.heartbeat("miniapp", "ok", "startup")
    _MINIAPP_HEARTBEAT_TASK = asyncio.create_task(_miniapp_heartbeat_loop(), name="miniapp-heartbeat")


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _MINIAPP_HEARTBEAT_TASK
    if _MINIAPP_HEARTBEAT_TASK is not None:
        _MINIAPP_HEARTBEAT_TASK.cancel()
        try:
            await _MINIAPP_HEARTBEAT_TASK
        except asyncio.CancelledError:
            pass
        _MINIAPP_HEARTBEAT_TASK = None












MINI_UI_VERSION = "20260812d"

@app.get("/mini")
def mini(request: Request):
    # Never redirect the Telegram Mini App launch URL. Telegram Web keeps its
    # signed tgWebAppData in the URL fragment; an HTTP redirect cannot preserve
    # that fragment because it is never sent to the server. The versioned JS
    # filename in index.html is the cache-buster, so serving HTML directly is safe.
    response = FileResponse(STATIC / "index.html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Mini-App-Version"] = MINI_UI_VERSION
    return response


@app.get("/health")
def health() -> dict[str, object]:
    # Liveness must stay instant and must not wait for SQLite locks. Reverse proxies use this endpoint.
    return {
        "status": "ok",
        "bot_enabled": settings.bot_enabled,
        "miniapp_enabled": settings.miniapp_enabled,
        "uptime_seconds": int(max(0, time.time() - _PROCESS_STARTED_AT)),
        "pid": os.getpid(),
    }


@app.get("/ready")
def ready() -> JSONResponse:
    probe = db.database_probe(1200)
    payload = {
        "status": "ready" if probe.get("ok") else "degraded",
        "database": bool(probe.get("ok")),
        "database_latency_ms": probe.get("latency_ms"),
        "database_error": probe.get("error") or "",
        "mini_app": True,
    }
    return JSONResponse(payload, status_code=200 if probe.get("ok") else 503)


















@app.post("/api/client-sync")
def client_sync_api(
    payload: ClientSyncPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    device = _REQUEST_DEVICE.get({})
    shift_continuity.update_device_sync_state(
        payload.user_id, device.get("device_id", ""), chat_id=scope, app_version=payload.app_version,
        sync_status=payload.sync_status, pending_queue_count=payload.pending_queue_count,
        draft_present=payload.draft_present, last_error=payload.last_error, health_ok=True,
    )
    return {
        "status": "ok",
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "active_scope_chat_id": scope,
    }


@app.get("/api/accounts")
def accounts(
    user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    uid = _request_user(user_id, auth_user_id)
    if uid is None:
        raise HTTPException(status_code=403, detail="access denied")
    items = repo.list_accounts_for_user(uid, include_accessible=True)
    active = repo.get_active_account(int(uid))
    allowed_ids = {int(a.id) for a in items}
    if active and int(active.id) not in allowed_ids:
        active = None
    return {
        "accounts": [{"id": a.id, "name": a.name, "scope_chat_id": a.scope_chat_id, "is_general": a.is_general} for a in items],
        # В личном Telegram-чате chat.id совпадает с user.id. Поэтому это тот же
        # активный учёт, который пользователь выбрал в меню бота. Mini App
        # использует его как источник истины вместо устаревшего localStorage.
        "active_account_id": active.id if active else None,
        "active_scope_chat_id": active.scope_chat_id if active else None,
    }


@app.post("/api/accounts/select")
def select_account(
    payload: AccountSelectPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    uid = _request_user(payload.user_id, auth_user_id)
    if uid is None:
        raise HTTPException(status_code=403, detail="access denied")
    account = repo.get_account_by_id(int(payload.account_id))
    if not account or not repo.user_has_account_access(account.id, uid):
        raise HTTPException(status_code=403, detail="access denied")
    # Telegram private chat id equals the user's Telegram id. Keep the bot and
    # Mini App on the same active account in both directions.
    repo.upsert_chat(int(uid), "Личный чат", "private", connected=True)
    ok, message = repo.set_active_account(int(uid), account.id, user_id=int(uid))
    if not ok:
        raise HTTPException(status_code=403, detail=message)
    repo.log_site_action(int(account.scope_chat_id), int(uid), "select_account", details=str(account.id))
    return {
        "ok": True,
        "message": message,
        "account": {"id": account.id, "name": account.name, "scope_chat_id": account.scope_chat_id},
    }


@app.get("/api/bootstrap")
def bootstrap(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    account = _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    permissions = repo.user_permissions_current_context(scope, user_id)
    area_access = repo.area_access_map_for_user(scope, user_id)
    can_manage = repo.user_can_manage_current_context(scope, user_id)
    can_manage_departments = repo.user_can_manage_departments(scope, user_id)
    is_system_admin = repo.is_tenant_admin(scope, user_id)
    is_tenant_admin = is_system_admin
    has_departments = repo.user_has_department_membership(scope, user_id)
    repo.log_site_action(scope, user_id, "bootstrap")
    control_center.heartbeat("miniapp", "ok", "bootstrap")
    return {
        "account": {"id": account.id, "name": account.name} if account else None,
        "scope_chat_id": scope,
        "permissions": permissions,
        "area_access": area_access,
        "can_manage": can_manage,
        "can_manage_departments": can_manage_departments,
        "is_system_admin": is_system_admin,
        "is_tenant_admin": is_tenant_admin,
        "department_memberships": repo.user_department_memberships(scope, user_id),
        "work_access": _work_access_for_user(scope, user_id),
        "entity_codes": repo.list_entity_codes(scope) if is_system_admin else [],
        "operation_presets": _operation_presets_for_user(scope, user_id),
        "recent_operations": _recent_operations_for_user(scope, user_id),
        "setup_health": repo.setup_health(scope) if is_system_admin else {},
        "workspace": control_center.workspace_profile(scope, user_id),
        "control_summary": control_center.control_summary(scope, user_id) if (is_system_admin or can_manage_departments) else {},
        "departments": repo.list_departments(scope, None if is_system_admin else user_id, manageable_only=not is_system_admin) if can_manage_departments else [],
        "areas": _area_list(scope),
        "entities": _entity_list(scope, user_id),
        "destinations": repo.list_destinations(scope) if (is_system_admin or not has_departments) else [],
        "job_titles": repo.list_job_titles_detailed(scope) if can_manage else [],
        "workers": repo.list_workers_detailed(scope) if can_manage else [],
        "area_access_rules": repo.list_area_section_access(scope) if can_manage else [],
        "inventory_positions": _inventory_positions_for_user(scope, user_id, _inventory_area_ids(scope, user_id)) if (permissions.get("stock") or is_system_admin or has_departments) else [],
        "inventory_sessions": repo.list_inventory_sessions(scope, area_ids=_inventory_area_ids(scope, user_id)) if ((permissions.get("stock") and not has_departments) or is_system_admin) else [],
        "report_presets": _report_presets_for_user(scope, user_id) if (permissions.get("reports") or is_system_admin) else [],
        "report_schedules": repo.list_report_schedules(scope, user_id),
        "report_delivery_history": repo.list_report_delivery_history(scope, user_id),
        "inbox_items": repo.list_inbox_items(scope, user_id),
        "worker_activity": repo.worker_activity_analytics(scope, 30, None if can_manage else user_id),
        "worker_shifts": repo.list_worker_shifts(scope, None if can_manage else user_id, limit=80),
        "current_open_shift": shift_continuity.current_open_shift(scope, user_id),
        "shift_packages": _shift_packages_for_user(scope, user_id),
        "shift_handovers": shift_continuity.list_handovers(scope, user_id, can_manage=(is_system_admin or can_manage_departments)),
        "handover_recipients": shift_continuity.handover_recipients(scope, user_id),
        "handover_checklist": shift_continuity.active_handover_checklist(scope),
        "continuity_settings": shift_continuity.get_continuity_settings(scope) if is_system_admin else {},
        "label_templates": shift_continuity.list_label_templates(scope) if is_system_admin else [],
        "miniapp_devices": _device_rows_for_user(scope, user_id),
        "current_device_id": _REQUEST_DEVICE.get({}).get("device_id", ""),
        "shift_plans": repo.list_shift_plans(scope, None if can_manage else user_id),
        "shift_templates": repo.list_shift_templates(scope, None if can_manage else user_id),
        "shift_calendar": repo.shift_calendar(scope, str(date.today() - timedelta(days=7)), str(date.today() + timedelta(days=45)), None if can_manage else user_id),
        "attendance_deviations": repo.attendance_deviations(scope, None if can_manage else user_id, 30),
        "attendance_summary": repo.attendance_summary(scope, str(date.today() - timedelta(days=29)), str(date.today()), None if can_manage else user_id),
        "notification_preferences": repo.get_notification_preferences(scope, user_id),
        "stock_risk": _risk_for_user(scope, user_id),
        "workflow": production_flow.workflow_snapshot(scope, user_id),
        "workflow_options": production_flow.workflow_options(scope, user_id),
        "quality_supply": {"quality": quality_control.quality_snapshot(scope, user_id), "replenishment": replenishment.snapshot(scope, user_id), "maintenance": maintenance_planning.snapshot(scope, user_id)},
        "plan_targets": repo.list_assembly_plan_targets(scope) if ((permissions.get("assembly") and not has_departments) or is_system_admin) else [],
        "dashboard": _dashboard_for_user(scope, user_id),
    }



def _flow_user(chat_id: int, user_id: int | None, x_access_token: str | None, x_telegram_init_data: str | None, *, submit: bool = False) -> tuple[int, int]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    uid = _request_user(user_id, auth_user_id)
    if uid is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, uid, submit=submit)
    return repo.resolve_scope_chat_id(chat_id), int(uid)


def _flow_error(exc: Exception) -> None:
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


@app.get("/api/workflow")
def workflow_snapshot(
    chat_id: int = Query(...), user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(chat_id, user_id, x_access_token, x_telegram_init_data)
    return {"workflow": production_flow.workflow_snapshot(scope, uid), "workflow_options": production_flow.workflow_options(scope, uid)}


@app.post("/api/production-tasks")
def save_production_task(
    payload: ProductionTaskPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try:
        task = production_flow.create_task(
            scope, uid, payload.department_id, payload.entity_id,
            operation_type=payload.operation_type, target_quantity=payload.target_quantity, unit=payload.unit,
            title=payload.title, assignee_user_id=payload.assignee_user_id, shift_plan_id=payload.shift_plan_id, area_id=payload.area_id,
            priority=payload.priority, due_at=payload.due_at, note=payload.note, output_lot_id=payload.output_lot_id,
        )
    except Exception as exc:
        _flow_error(exc)
    repo.log_site_action(scope, uid, "production_task_create", str(task.get("id") or ""))
    return {"message": "Задание создано.", "task": task, "workflow": production_flow.workflow_snapshot(scope, uid)}


@app.post("/api/production-tasks/action")
def production_task_action(
    payload: ProductionTaskActionPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try:
        task = production_flow.task_action(scope, uid, payload.task_id, payload.action, reason=payload.reason, note=payload.note)
    except Exception as exc:
        _flow_error(exc)
    repo.log_site_action(scope, uid, "production_task_action", f"{payload.task_id}:{payload.action}")
    return {"message": "Задание обновлено.", "task": task, "workflow": production_flow.workflow_snapshot(scope, uid)}


@app.post("/api/department-requests")
def save_department_request(
    payload: DepartmentRequestPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try:
        item = production_flow.create_request(
            scope, uid, payload.requester_department_id, payload.supplier_department_id, payload.entity_id, payload.quantity,
            unit=payload.unit, from_area_id=payload.from_area_id, to_area_id=payload.to_area_id,
            priority=payload.priority, needed_at=payload.needed_at, note=payload.note,
        )
    except Exception as exc:
        _flow_error(exc)
    repo.log_site_action(scope, uid, "department_request_create", str(item.get("id") or ""))
    return {"message": "Заявка создана.", "request": item, "workflow": production_flow.workflow_snapshot(scope, uid)}


@app.post("/api/department-requests/action")
def department_request_action(
    payload: DepartmentRequestActionPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try:
        item = production_flow.request_action(scope, uid, payload.request_id, payload.action, quantity=payload.quantity, reason=payload.reason, note=payload.note)
    except Exception as exc:
        _flow_error(exc)
    repo.log_site_action(scope, uid, "department_request_action", f"{payload.request_id}:{payload.action}")
    return {"message": "Заявка обновлена.", "request": item, "workflow": production_flow.workflow_snapshot(scope, uid)}


@app.post("/api/lots")
def save_production_lot(
    payload: ProductionLotPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try:
        lot = production_flow.create_lot(scope, uid, payload.entity_id, payload.lot_code, supplier_code=payload.supplier_code, manufacture_date=payload.manufacture_date, expiry_date=payload.expiry_date, note=payload.note)
    except Exception as exc:
        _flow_error(exc)
    return {"message": "Партия создана.", "lot": lot, "workflow": production_flow.workflow_snapshot(scope, uid)}


@app.post("/api/lots/relation")
def save_lot_relation(
    payload: LotRelationPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try:
        production_flow.link_lots(scope, uid, payload.parent_lot_id, payload.component_lot_id, payload.quantity, payload.unit, payload.task_id)
        lot = production_flow.get_lot(scope, payload.parent_lot_id, uid)
    except Exception as exc:
        _flow_error(exc)
    return {"message": "Связь партий сохранена.", "lot": lot}


@app.get("/api/lots/trace")
def lot_trace(
    chat_id: int = Query(...), lot_id: int = Query(...), user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(chat_id, user_id, x_access_token, x_telegram_init_data)
    lot = production_flow.get_lot(scope, lot_id, uid)
    if not lot:
        raise HTTPException(status_code=404, detail="Партия не найдена или недоступна.")
    return {"lot": lot}


@app.post("/api/equipment")
def save_equipment(
    payload: EquipmentPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try:
        item = production_flow.save_equipment(scope, uid, payload.name, equipment_id=payload.equipment_id, department_id=payload.department_id, area_id=payload.area_id, code=payload.code, status=payload.status, service_interval_days=payload.service_interval_days, warning_before_days=payload.warning_before_days, note=payload.note)
    except Exception as exc:
        _flow_error(exc)
    return {"message": "Оборудование сохранено.", "equipment": item, "workflow": production_flow.workflow_snapshot(scope, uid)}


@app.post("/api/equipment/downtime")
def open_equipment_downtime(
    payload: EquipmentDowntimePayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try:
        item = production_flow.open_downtime(scope, uid, payload.equipment_id, reason_type=payload.reason_type, reason=payload.reason, task_id=payload.task_id)
    except Exception as exc:
        _flow_error(exc)
    return {"message": "Простой зарегистрирован.", "downtime": item, "workflow": production_flow.workflow_snapshot(scope, uid)}


@app.post("/api/equipment/downtime/close")
def close_equipment_downtime(
    payload: EquipmentDowntimeClosePayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try:
        item = production_flow.close_downtime(scope, uid, payload.downtime_id, payload.resolution)
    except Exception as exc:
        _flow_error(exc)
    return {"message": "Простой закрыт.", "downtime": item, "workflow": production_flow.workflow_snapshot(scope, uid)}


@app.post("/api/equipment/maintenance")
def record_equipment_maintenance(
    payload: MaintenancePayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try:
        item = production_flow.record_maintenance(scope, uid, payload.equipment_id, maintenance_type=payload.maintenance_type, note=payload.note)
    except Exception as exc:
        _flow_error(exc)
    return {"message": "Обслуживание записано.", "maintenance": item, "workflow": production_flow.workflow_snapshot(scope, uid)}


@app.get("/api/quality-supply")
def quality_supply_snapshot(
    chat_id: int = Query(...), user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(chat_id, user_id, x_access_token, x_telegram_init_data)
    return {"quality_supply": {"quality": quality_control.quality_snapshot(scope, uid), "replenishment": replenishment.snapshot(scope, uid), "maintenance": maintenance_planning.snapshot(scope, uid)}}


@app.post("/api/quality/rules")
def save_quality_rule(payload: QualityRulePayload, x_access_token: Annotated[str | None, Header()] = None, x_telegram_init_data: Annotated[str | None, Header()] = None) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try:
        item = quality_control.save_rule(scope, uid, payload.model_dump(exclude={"chat_id","user_id"}))
    except Exception as exc: _flow_error(exc)
    return {"message":"Правило контроля сохранено.","rule":item,"quality_supply":{"quality":quality_control.quality_snapshot(scope,uid),"replenishment":replenishment.snapshot(scope,uid),"maintenance":maintenance_planning.snapshot(scope,uid)}}


@app.post("/api/quality/inspections")
def create_quality_inspection(payload: QualityInspectionPayload, x_access_token: Annotated[str | None, Header()] = None, x_telegram_init_data: Annotated[str | None, Header()] = None) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try:
        values = payload.model_dump(exclude={"chat_id","user_id","entity_id"})
        item = quality_control.create_inspection(scope, uid, payload.entity_id, **values)
    except Exception as exc: _flow_error(exc)
    return {"message":"Контроль качества записан.","inspection":item,"quality_supply":{"quality":quality_control.quality_snapshot(scope,uid),"replenishment":replenishment.snapshot(scope,uid),"maintenance":maintenance_planning.snapshot(scope,uid)}}


@app.post("/api/quality/inspections/action")
def quality_inspection_action(payload: QualityDecisionPayload, x_access_token: Annotated[str | None, Header()] = None, x_telegram_init_data: Annotated[str | None, Header()] = None) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try:
        item = quality_control.decide_inspection(scope, uid, payload.inspection_id, payload.decision, reason=payload.reason or payload.note)
    except Exception as exc: _flow_error(exc)
    return {"message":"Решение по качеству сохранено.","inspection":item,"quality_supply":{"quality":quality_control.quality_snapshot(scope,uid),"replenishment":replenishment.snapshot(scope,uid),"maintenance":maintenance_planning.snapshot(scope,uid)}}


@app.post("/api/replenishment/settings")
def save_replenishment_setting(payload: ReplenishmentSettingPayload, x_access_token: Annotated[str | None, Header()] = None, x_telegram_init_data: Annotated[str | None, Header()] = None) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id, payload.user_id, x_access_token, x_telegram_init_data, submit=True)
    try: item=replenishment.save_setting(scope,uid,payload.model_dump(exclude={"chat_id","user_id"}))
    except Exception as exc: _flow_error(exc)
    return {"message":"Параметры пополнения сохранены.","setting":item,"replenishment":replenishment.snapshot(scope,uid)}


@app.post("/api/replenishment/requests")
def create_replenishment_request(payload: ReplenishmentRequestPayload, x_access_token: Annotated[str | None, Header()] = None, x_telegram_init_data: Annotated[str | None, Header()] = None) -> dict[str, object]:
    scope, uid = _flow_user(payload.chat_id,payload.user_id,x_access_token,x_telegram_init_data,submit=True)
    try:item=replenishment.create_request(scope,uid,payload.model_dump(exclude={"chat_id","user_id"}))
    except Exception as exc:_flow_error(exc)
    return {"message":"Заявка на пополнение создана.","request":item,"replenishment":replenishment.snapshot(scope,uid)}


@app.post("/api/replenishment/requests/action")
def replenishment_request_action(payload: ReplenishmentActionPayload, x_access_token: Annotated[str | None, Header()] = None, x_telegram_init_data: Annotated[str | None, Header()] = None) -> dict[str, object]:
    scope, uid=_flow_user(payload.chat_id,payload.user_id,x_access_token,x_telegram_init_data,submit=True)
    try:item=replenishment.request_action(scope,uid,payload.request_id,payload.action,quantity=payload.quantity,reason=payload.reason,note=payload.note)
    except Exception as exc:_flow_error(exc)
    return {"message":"Статус пополнения обновлён.","request":item,"replenishment":replenishment.snapshot(scope,uid)}


@app.post("/api/maintenance/plans")
def save_maintenance_plan(payload: MaintenancePlanPayload, x_access_token: Annotated[str | None, Header()] = None, x_telegram_init_data: Annotated[str | None, Header()] = None) -> dict[str, object]:
    scope,uid=_flow_user(payload.chat_id,payload.user_id,x_access_token,x_telegram_init_data,submit=True)
    try:item=maintenance_planning.save_plan(scope,uid,payload.model_dump(exclude={"chat_id","user_id"}))
    except Exception as exc:_flow_error(exc)
    return {"message":"План ТО сохранён.","plan":item,"maintenance":maintenance_planning.snapshot(scope,uid)}


@app.post("/api/maintenance/work/action")
def maintenance_work_action(payload: MaintenanceWorkActionPayload, x_access_token: Annotated[str | None, Header()] = None, x_telegram_init_data: Annotated[str | None, Header()] = None) -> dict[str, object]:
    scope,uid=_flow_user(payload.chat_id,payload.user_id,x_access_token,x_telegram_init_data,submit=True)
    try:item=maintenance_planning.work_action(scope,uid,payload.work_order_id,payload.action,result=payload.result,note=payload.note)
    except Exception as exc:_flow_error(exc)
    return {"message":"ТО обновлено.","work_order":item,"maintenance":maintenance_planning.snapshot(scope,uid)}


@app.post("/api/maintenance/work/check")
def maintenance_work_check(payload: MaintenanceCheckPayload, x_access_token: Annotated[str | None, Header()] = None, x_telegram_init_data: Annotated[str | None, Header()] = None) -> dict[str, object]:
    scope,uid=_flow_user(payload.chat_id,payload.user_id,x_access_token,x_telegram_init_data,submit=True)
    try:item=maintenance_planning.set_check(scope,uid,payload.work_order_id,payload.check_id,payload.checked,payload.note)
    except Exception as exc:_flow_error(exc)
    return {"message":"Чек-лист обновлён.","work_order":item}


@app.post("/api/maintenance/work/part")
def maintenance_work_part(payload: MaintenancePartPayload, x_access_token: Annotated[str | None, Header()] = None, x_telegram_init_data: Annotated[str | None, Header()] = None) -> dict[str, object]:
    scope,uid=_flow_user(payload.chat_id,payload.user_id,x_access_token,x_telegram_init_data,submit=True)
    try:item=maintenance_planning.set_part(scope,uid,payload.work_order_id,payload.part_id,payload.actual_quantity)
    except Exception as exc:_flow_error(exc)
    return {"message":"Расход запчасти обновлён.","work_order":item}


@app.get("/api/production-needs-report")
def production_needs_download(
    chat_id:int=Query(...), user_id:int|None=Query(None), start_date:str|None=Query(None), end_date:str|None=Query(None), format:str=Query("xlsx"),
    x_access_token:Annotated[str|None,Header()]=None, x_telegram_init_data:Annotated[str|None,Header()]=None,
):
    scope,uid=_flow_user(chat_id,user_id,x_access_token,x_telegram_init_data)
    try:path=production_needs_report.create_pdf(scope,uid,start_date=start_date,end_date=end_date) if format.lower()=="pdf" else production_needs_report.create_xlsx(scope,uid,start_date=start_date,end_date=end_date)
    except Exception as exc:_flow_error(exc)
    media="application/pdf" if path.suffix.lower()==".pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return FileResponse(path,media_type=media,filename=path.name)


@app.get("/api/plan-fact")
def workflow_plan_fact(
    chat_id: int = Query(...), user_id: int | None = Query(None), start_date: str | None = Query(None), end_date: str | None = Query(None), department_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    scope, uid = _flow_user(chat_id, user_id, x_access_token, x_telegram_init_data)
    return {"plan_fact": production_flow.plan_fact_summary(scope, uid, start_date=start_date, end_date=end_date, department_id=department_id)}


@app.get("/api/stock-risks")
def stock_risks(
    chat_id: int = Query(...), user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    return _risk_for_user(repo.resolve_scope_chat_id(chat_id), user_id)


@app.post("/api/stock-alert-rules")
def save_stock_alert_rule(
    payload: StockAlertRulePayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_system_admin(payload.chat_id, payload.user_id)
    ok, message, rule_id = stock_risk.save_rule(payload.chat_id, payload.user_id, payload.model_dump())
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    if rule_id:
        snapshot = stock_risk.evaluate_rule(rule_id)
        if snapshot:
            stock_risk.persist_snapshot(snapshot)
    return {"message": message, "stock_risk": _risk_for_user(repo.resolve_scope_chat_id(payload.chat_id), payload.user_id)}


@app.delete("/api/stock-alert-rules")
def delete_stock_alert_rule(
    chat_id: int = Query(...), user_id: int | None = Query(None), rule_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_system_admin(chat_id, user_id)
    if not stock_risk.delete_rule(chat_id, rule_id):
        raise HTTPException(status_code=404, detail="Правило не найдено.")
    return {"stock_risk": _risk_for_user(repo.resolve_scope_chat_id(chat_id), user_id)}


@app.post("/api/stock-observations")
def save_stock_observation(
    payload: StockObservationPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    entity = repo.get_entity(payload.entity_id)
    if not entity or entity.chat_id != scope or entity.entity_type != payload.entity_type or payload.quantity < 0:
        raise HTTPException(status_code=400, detail="bad observation")
    if payload.mode not in {"balance", "consumption"}:
        raise HTTPException(status_code=400, detail="bad observation mode")
    if payload.period_kind not in {"instant", "shift", "day", "week", "custom"} or payload.period_count <= 0:
        raise HTTPException(status_code=400, detail="bad observation period")
    if payload.area_id:
        area = repo.get_area(payload.area_id)
        if not area or int(area.chat_id) != int(scope):
            raise HTTPException(status_code=400, detail="bad area")
    unit = payload.unit or entity.default_unit or "шт"
    if payload.mode == "balance":
        _check_operation_permission(scope, payload.user_id, "inventory_adjust", area_id=payload.area_id, entity_type=payload.entity_type, entity_id=payload.entity_id)
        current = repo.inventory_quantity(scope, payload.entity_type, payload.entity_id, unit, payload.area_id)
        op = {"operation_type": "inventory_adjust", "entity_type": payload.entity_type, "entity_id": payload.entity_id,
              "entity_name": entity.name, "quantity": payload.quantity-current, "fact_quantity": payload.quantity,
              "old_quantity": current, "unit": unit, "area_id": payload.area_id}
    else:
        op_type = "material_out" if payload.entity_type == "material" else ("stock_out" if payload.entity_type == "stock_item" else "write_off")
        _check_operation_permission(scope, payload.user_id, op_type, area_id=payload.area_id, entity_type=payload.entity_type, entity_id=payload.entity_id)
        op = {"operation_type": op_type, "entity_type": payload.entity_type, "entity_id": payload.entity_id,
              "entity_name": entity.name, "quantity": payload.quantity, "unit": unit, "area_id": payload.area_id,
              "skip_risk_observation": True}
    note = payload.note or f"Mini App · {payload.mode} · {payload.period_kind} {payload.period_count}"
    saved = accounting.apply_operations(scope, payload.chat_id, payload.user_id, [op], raw_text=note)
    if payload.mode != "balance" and saved:
        stock_risk.record_observation(scope, payload.entity_type, payload.entity_id, payload.area_id, payload.user_id, "mini",
                                      "consumption", payload.quantity, unit, payload.period_kind, payload.period_count, note,
                                      dedupe_key=f"mini:{scope}:{payload.user_id}:{time.time_ns()}")
        stock_risk.evaluate_related_rules(scope, payload.entity_type, payload.entity_id, payload.area_id)
    return {"saved": saved, "stock_risk": _risk_for_user(scope, payload.user_id),
            "inventory_positions": _inventory_positions_for_user(scope, payload.user_id, _inventory_area_ids(scope, payload.user_id))}


@app.post("/api/operational-events")
def save_operational_event(
    payload: OperationalEventPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    if not repo.is_tenant_admin(scope, payload.user_id) and not repo.user_has_department_membership(scope, payload.user_id):
        raise HTTPException(status_code=403, detail="access denied")
    values = payload.model_dump()
    values["starts_at"] = values.get("starts_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, message, event_id = stock_risk.save_event(scope, payload.user_id, values)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"message": message, "event_id": event_id, "stock_risk": _risk_for_user(scope, payload.user_id)}


@app.post("/api/operational-events/resolve")
def resolve_operational_event(
    payload: OperationalEventPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, submit=True)
    if not payload.event_id or not stock_risk.resolve_event(payload.chat_id, payload.event_id, payload.user_id):
        raise HTTPException(status_code=404, detail="Событие не найдено.")
    return {"stock_risk": _risk_for_user(repo.resolve_scope_chat_id(payload.chat_id), payload.user_id)}


@app.post("/api/stock-incidents/acknowledge")
def acknowledge_stock_incident(
    payload: IncidentActionPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    if not stock_risk.acknowledge_incident(payload.chat_id, payload.incident_id, payload.user_id, payload.snooze_minutes):
        raise HTTPException(status_code=404, detail="Тревога не найдена.")
    return {"stock_risk": _risk_for_user(repo.resolve_scope_chat_id(payload.chat_id), payload.user_id)}


@app.get("/api/dashboard")
def dashboard(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    repo.log_site_action(scope, user_id, "dashboard")
    return _dashboard_for_user(scope, user_id)


@app.post("/api/operations/preview")
def preview_operation(
    payload: OperationPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    return {"preview": _operation_preview(scope, payload.user_id, payload)}


def _create_operation_core(
    payload: OperationPayload,
    account: repo.AccountingAccount | None,
    scope: int,
) -> dict[str, object]:
    request_key = str(payload.client_request_id or "").strip()[:120]
    if request_key:
        duplicate = repo.get_operation_by_client_request(scope, payload.user_id, request_key)
        if duplicate:
            return {
                "saved": 1, "duplicate": True, "operation_id": duplicate.get("id"),
                "account": account.name if account else "",
                "dashboard": _dashboard_for_user(scope, payload.user_id),
                "recent_operations": _recent_operations_for_user(scope, payload.user_id),
                "operation_presets": _operation_presets_for_user(scope, payload.user_id),
                "inventory_positions": _inventory_positions_for_user(scope, payload.user_id, _inventory_area_ids(scope, payload.user_id)),
                "workflow": production_flow.workflow_snapshot(scope, payload.user_id),
            }
    preview = _operation_preview(scope, payload.user_id, payload)
    supplied_fingerprint = str(payload.preview_fingerprint or "").strip()
    if supplied_fingerprint and supplied_fingerprint != str(preview.get("fingerprint") or ""):
        raise HTTPException(status_code=409, detail={
            "message": "Остатки изменились после проверки. Проверьте обновлённые данные.",
            "preview": preview, "stock_changed": True,
        })
    if preview.get("requires_confirmation") and not payload.confirm_warnings:
        raise HTTPException(status_code=409, detail={"message": "Требуется подтверждение предупреждений.", "preview": preview})
    entity = repo.get_entity(payload.entity_id)
    if not entity:
        raise HTTPException(status_code=400, detail="Позиция не найдена.")
    try:
        production_flow.validate_operation_context(scope, payload.user_id, task_id=payload.task_id, lot_id=payload.lot_id, entity_id=payload.entity_id, operation_type=payload.operation_type)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    op = {
        "operation_type": payload.operation_type,
        "entity_type": payload.entity_type,
        "entity_id": payload.entity_id,
        "entity_name": entity.name,
        "quantity": payload.quantity,
        "unit": payload.unit or entity.default_unit or "шт",
        "area_id": payload.area_id,
        "from_area_id": payload.from_area_id,
        "to_area_id": payload.to_area_id,
        "destination_type": payload.destination_type,
        "storage_place": payload.storage_place,
        "client_request_id": request_key or None,
        "source_channel": "mini",
        "task_id": payload.task_id,
        "lot_id": payload.lot_id,
        "department_id": payload.department_id,
        "from_department_id": payload.from_department_id,
        "to_department_id": payload.to_department_id,
        "storage_location_id": payload.storage_location_id,
        "from_location_id": payload.from_location_id,
        "to_location_id": payload.to_location_id,
    }
    try:
        saved = accounting.apply_operations(scope, payload.chat_id, payload.user_id, [op], raw_text=payload.note or "mini app")
    except Exception:
        duplicate = repo.get_operation_by_client_request(scope, payload.user_id, request_key) if request_key else None
        if duplicate:
            return {
                "saved": 1, "duplicate": True, "operation_id": duplicate.get("id"),
                "account": account.name if account else "",
                "dashboard": _dashboard_for_user(scope, payload.user_id),
                "recent_operations": _recent_operations_for_user(scope, payload.user_id),
                "operation_presets": _operation_presets_for_user(scope, payload.user_id),
                "inventory_positions": _inventory_positions_for_user(scope, payload.user_id, _inventory_area_ids(scope, payload.user_id)),
                "workflow": production_flow.workflow_snapshot(scope, payload.user_id),
            }
        raise
    if not saved:
        raise HTTPException(status_code=409, detail="Запись не была сохранена. Проверьте доступы и данные.")
    saved_operation = repo.get_operation_by_client_request(scope, payload.user_id, request_key) if request_key else None
    if payload.preset_id:
        repo.touch_operation_preset(scope, payload.user_id, payload.preset_id)
    repo.log_site_action(scope, payload.user_id, "operation", payload.operation_type)
    repo.log_sync_event(scope, "mini", "saved", payload.operation_type)
    return {
        "saved": saved, "duplicate": False, "operation_id": saved_operation.get("id") if saved_operation else None,
        "account": account.name if account else "",
        "dashboard": _dashboard_for_user(scope, payload.user_id),
        "recent_operations": _recent_operations_for_user(scope, payload.user_id),
        "operation_presets": _operation_presets_for_user(scope, payload.user_id),
        "inventory_positions": _inventory_positions_for_user(scope, payload.user_id, _inventory_area_ids(scope, payload.user_id)),
        "workflow": production_flow.workflow_snapshot(scope, payload.user_id),
        "preview": preview,
    }


def _create_operation_unlocked(
    payload: OperationPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    account = _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    return _create_operation_core(payload, account, scope)


@app.post("/api/operations")
def create_operation(
    payload: OperationPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    # Один критический участок на процесс: проверка остатков и запись не расходятся
    # при одновременных нажатиях с разных телефонов.
    with _OPERATION_SAVE_LOCK:
        return _create_operation_unlocked(payload, x_access_token, x_telegram_init_data)


def _package_review_recipients(scope: int, worker_user_id: int) -> list[int]:
    recipients = set(repo.tenant_admin_user_ids(scope))
    rows = db.fetchall(
        """
        SELECT DISTINCT head.user_id
        FROM department_members worker
        JOIN departments d ON d.id=worker.department_id AND d.chat_id=? AND d.is_archived=0
        JOIN department_members head ON head.department_id=d.id AND head.is_active=1 AND head.role_level>=50
        WHERE worker.user_id=? AND worker.is_active=1
        """,
        (scope, int(worker_user_id)),
    )
    recipients.update(int(row["user_id"]) for row in rows)
    recipients.discard(int(worker_user_id))
    return sorted(recipients)


def _notify_package_review(scope: int, package: dict[str, Any]) -> None:
    if str(package.get("status") or "") not in {"review", "partial", "rejected"}:
        return
    title = "Нужно проверить записи смены"
    message = (
        f"Сотрудник: {package.get('worker_name') or package.get('user_id')}. "
        f"Принято: {package.get('accepted_count', 0)}; на проверке: {package.get('review_count', 0)}; "
        f"отклонено: {package.get('rejected_count', 0)}; ошибок: {package.get('error_count', 0)}."
    )
    for recipient in _package_review_recipients(scope, int(package.get("user_id") or 0)):
        repo.create_inbox_item(
            scope, recipient, "shift_package_review", title, message,
            "shift_sync_package", int(package["id"]), deduplicate=True, priority="high", force=True,
        )


def _package_item_error(exc: HTTPException) -> tuple[str, str, list[str]]:
    detail = exc.detail
    if isinstance(detail, dict):
        preview = detail.get("preview") or {}
        warnings = [str(x) for x in (preview.get("warnings") or [])]
        message = str(detail.get("message") or (warnings[0] if warnings else "Требуется проверка."))
    else:
        warnings = []
        message = str(detail or "Ошибка записи.")
    if exc.status_code == 409:
        return "review", message, warnings
    if exc.status_code in {400, 403, 404}:
        return "rejected", message, warnings
    return "error", message, warnings


@app.post("/api/shift-packages/sync")
def sync_shift_package(
    payload: ShiftPackageSyncPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    account = _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    if not payload.items:
        raise HTTPException(status_code=400, detail="Пакет смены пуст.")
    if len(payload.items) > 200:
        raise HTTPException(status_code=400, detail="В одном пакете допускается не более 200 записей.")
    open_shift = shift_continuity.current_open_shift(scope, payload.user_id)
    shift_id = payload.shift_id
    if open_shift and (not shift_id or int(shift_id) == int(open_shift["id"])):
        shift_id = int(open_shift["id"])
    elif shift_id:
        row = db.fetchone("SELECT id FROM worker_shifts WHERE chat_id=? AND id=? AND user_id=?", (scope, int(shift_id), int(payload.user_id)))
        if not row:
            shift_id = None
    area_id = payload.area_id if payload.area_id is not None else (open_shift.get("area_id") if open_shift else None)
    device_id = _REQUEST_DEVICE.get({}).get("device_id", "")
    package = shift_continuity.upsert_shift_package(
        scope, payload.user_id, payload.client_package_id,
        shift_id=shift_id, area_id=area_id, device_id=device_id, note=payload.note,
    )
    with _OPERATION_SAVE_LOCK:
        for sequence, raw_item in enumerate(payload.items):
            body = raw_item.get("body") if isinstance(raw_item, dict) and isinstance(raw_item.get("body"), dict) else raw_item
            if not isinstance(body, dict):
                continue
            request_id = str(body.get("client_request_id") or "").strip()[:120]
            if not request_id:
                request_id = hashlib.sha256(
                    json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()[:32]
                body["client_request_id"] = request_id
            item = shift_continuity.upsert_shift_package_item(int(package["id"]), request_id, sequence, body)
            if str(item.get("status") or "") in {"accepted", "duplicate"}:
                continue
            try:
                op_payload = OperationPayload(**{**body, "chat_id": scope, "user_id": int(payload.user_id)})
                result = _create_operation_core(op_payload, account, scope)
                shift_continuity.update_package_item(
                    int(item["id"]), "duplicate" if result.get("duplicate") else "accepted",
                    message="Запись уже была сохранена." if result.get("duplicate") else "Сохранено.",
                    operation_id=int(result.get("operation_id") or 0) or None,
                )
            except HTTPException as exc:
                status, message, warnings = _package_item_error(exc)
                shift_continuity.update_package_item(int(item["id"]), status, message=message, warnings=warnings)
            except Exception as exc:
                shift_continuity.update_package_item(int(item["id"]), "error", message=f"Временная ошибка: {type(exc).__name__}")
    package = shift_continuity.recount_shift_package(int(package["id"])) or package
    package["worker_name"] = str(payload.user_id)
    _notify_package_review(scope, package)
    repo.log_sync_event(scope, "mini_package", str(package.get("status") or "received"), str(package.get("id") or ""))
    return {
        "package": package,
        "shift_packages": _shift_packages_for_user(scope, payload.user_id),
        "dashboard": _dashboard_for_user(scope, payload.user_id),
        "inventory_positions": _inventory_positions_for_user(scope, payload.user_id, _inventory_area_ids(scope, payload.user_id)),
        "recent_operations": _recent_operations_for_user(scope, payload.user_id),
    }


@app.get("/api/shift-packages")
def list_shift_packages_api(
    chat_id: int = Query(...), user_id: int | None = Query(None),
    worker_user_id: int | None = Query(None), department_id: int | None = Query(None), area_id: int | None = Query(None),
    status: str | None = Query(None), date_from: str | None = Query(None), date_to: str | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    return {"shift_packages": _shift_packages_for_user(scope, user_id, worker_user_id=worker_user_id, department_id=department_id, area_id=area_id, status=status, date_from=date_from, date_to=date_to)}


def _review_shift_package_item(scope: int, actor_user_id: int, item_id: int, action: str, note: str = "") -> dict[str, Any]:
    item = shift_continuity.get_package_item(item_id)
    if not item or int(item.get("chat_id") or 0) != int(scope):
        raise HTTPException(status_code=404, detail="Запись пакета не найдена.")
    worker_user_id = int(item.get("user_id") or 0)
    if not shift_continuity.can_review_worker_packages(scope, actor_user_id, worker_user_id):
        raise HTTPException(status_code=403, detail="Нет права проверять записи этого сотрудника.")
    action = str(action or "").strip().lower()
    reason = str(note or "").strip()
    before_status = str(item.get("status") or "")
    if action == "reject":
        if not reason:
            raise HTTPException(status_code=400, detail="Укажите причину отклонения.")
        shift_continuity.update_package_item(item_id, "rejected", message=reason)
    elif action == "approve":
        body = dict(item.get("payload") or {})
        body.update({"chat_id": scope, "user_id": worker_user_id, "confirm_warnings": True, "preview_fingerprint": ""})
        account = _check_user(scope, worker_user_id, submit=True)
        try:
            with _OPERATION_SAVE_LOCK:
                result = _create_operation_core(OperationPayload(**body), account, scope)
            shift_continuity.update_package_item(
                item_id, "duplicate" if result.get("duplicate") else "accepted",
                message=note or ("Запись уже была сохранена." if result.get("duplicate") else "Подтверждено руководителем."),
                operation_id=int(result.get("operation_id") or 0) or None,
            )
        except HTTPException as exc:
            status_value, message, warnings = _package_item_error(exc)
            shift_continuity.update_package_item(item_id, status_value, message=message, warnings=warnings)
            raise HTTPException(status_code=409, detail=message)
    else:
        raise HTTPException(status_code=400, detail="Неизвестное действие.")
    package = shift_continuity.recount_shift_package(int(item["package_id"]), reviewed_by=actor_user_id)
    updated_item = shift_continuity.get_package_item(item_id) or {}
    control_center.record_decision(
        scope, actor_user_id, worker_user_id=worker_user_id, target_type="shift_sync_item", target_id=item_id,
        action=action, reason=reason, before_status=before_status, after_status=str(updated_item.get("status") or ""),
        metadata={"package_id": int(item.get("package_id") or 0)},
    )
    repo.create_inbox_item(
        scope, worker_user_id, "shift_package_result", "Запись смены проверена",
        (reason or ("Запись принята руководителем." if action == "approve" else "Запись отклонена руководителем."))[:1000],
        "shift_sync_item", int(item_id), deduplicate=False, priority="normal" if action == "approve" else "high", force=True,
    )
    repo.log_site_action(scope, actor_user_id, f"shift_package_{action}", str(item_id))
    return {"package": package or {}, "worker_user_id": worker_user_id}


@app.post("/api/shift-packages/item-action")
def shift_package_item_action(
    payload: ShiftPackageItemActionPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    result = _review_shift_package_item(scope, payload.user_id, payload.item_id, payload.action, payload.note)
    return {"package": result["package"], "shift_packages": _shift_packages_for_user(scope, payload.user_id)}

@app.post("/api/shift-packages/bulk-action")
def shift_package_bulk_action(
    payload: ShiftPackageBulkActionPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    ids = sorted({int(x) for x in payload.item_ids if int(x) > 0})[:200]
    if not ids:
        raise HTTPException(status_code=400, detail="Не выбраны записи.")
    accepted = 0
    failed: list[dict[str, object]] = []
    for item_id in ids:
        try:
            _review_shift_package_item(scope, payload.user_id, item_id, payload.action, payload.note)
            accepted += 1
        except HTTPException as exc:
            failed.append({"item_id": item_id, "error": str(exc.detail)})
    return {
        "message": f"Обработано: {accepted} из {len(ids)}.",
        "processed": accepted, "failed": failed,
        "shift_packages": _shift_packages_for_user(scope, payload.user_id),
    }



@app.post("/api/shift-handovers")
def create_shift_handover_api(
    payload: ShiftHandoverPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    from_user_id = int(payload.from_user_id or payload.user_id)
    if from_user_id != int(payload.user_id) and not shift_continuity.can_review_worker_packages(scope, payload.user_id, from_user_id):
        raise HTTPException(status_code=403, detail="Нет права передавать смену этого сотрудника.")
    if payload.to_user_id:
        _check_user(scope, int(payload.to_user_id))
        allowed_recipients = {int(item["id"]) for item in shift_continuity.handover_recipients(scope, from_user_id)}
        if int(payload.to_user_id) not in allowed_recipients and not repo.is_tenant_admin(scope, payload.user_id):
            raise HTTPException(status_code=403, detail="Получатель не относится к доступному рабочему контуру.")
    package_ids = list(payload.package_ids)
    if not package_ids:
        package_ids = [int(x["id"]) for x in shift_continuity.list_shift_packages(scope, user_id=from_user_id, unresolved_only=True, limit=50)]
    open_shift = shift_continuity.current_open_shift(scope, from_user_id)
    try:
        handover = shift_continuity.create_handover(
            scope, from_user_id, payload.user_id,
            to_user_id=payload.to_user_id,
            shift_id=payload.shift_id or (int(open_shift["id"]) if open_shift else None),
            area_id=payload.area_id if payload.area_id is not None else (open_shift.get("area_id") if open_shift else None),
            summary=payload.summary,
            package_ids=package_ids,
            checklist=payload.checklist,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repo.log_site_action(scope, payload.user_id, "shift_handover_create", str(handover.get("id") or ""))
    can_manage = repo.is_tenant_admin(scope, payload.user_id) or repo.user_can_manage_departments(scope, payload.user_id)
    return {"message": "Передача смены сохранена.", "shift_handovers": shift_continuity.list_handovers(scope, payload.user_id, can_manage=can_manage)}


@app.post("/api/shift-handovers/acknowledge")
def acknowledge_shift_handover_api(
    payload: ShiftHandoverActionPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    can_manage = repo.is_tenant_admin(scope, payload.user_id) or repo.user_can_manage_departments(scope, payload.user_id)
    handover = shift_continuity.get_handover(payload.handover_id)
    if not shift_continuity.acknowledge_handover(scope, payload.handover_id, payload.user_id, can_manage=can_manage):
        raise HTTPException(status_code=403, detail="Передача не найдена или недоступна.")
    if handover and int(handover.get("from_user_id") or 0) != int(payload.user_id):
        repo.create_inbox_item(
            scope, int(handover["from_user_id"]), "shift_handover_ack",
            "Передача смены принята",
            f"Передача смены №{payload.handover_id} принята сотрудником {payload.user_id}.",
            "shift_handover", int(payload.handover_id), deduplicate=False, priority="normal", force=True,
        )
    repo.log_site_action(scope, payload.user_id, "shift_handover_ack", str(payload.handover_id))
    return {"message": "Передача смены принята.", "shift_handovers": shift_continuity.list_handovers(scope, payload.user_id, can_manage=can_manage)}


@app.post("/api/devices/action")
def device_action_api(
    payload: DeviceActionPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    if int(payload.target_user_id) != int(payload.user_id) and not repo.is_tenant_admin(scope, payload.user_id):
        raise HTTPException(status_code=403, detail="Управлять чужими устройствами может только владелец или полный администратор.")
    if payload.action == "revoke":
        ok = shift_continuity.revoke_device(payload.user_id, payload.target_user_id, payload.device_id, reason=payload.reason)
        message = "Доступ устройства отозван."
    elif payload.action == "restore" and repo.is_tenant_admin(scope, payload.user_id):
        ok = shift_continuity.restore_device(payload.user_id, payload.target_user_id, payload.device_id)
        message = "Доступ устройства восстановлен."
    else:
        raise HTTPException(status_code=400, detail="Неизвестное действие.")
    if not ok:
        raise HTTPException(status_code=404, detail="Устройство не найдено.")
    repo.log_site_action(scope, payload.user_id, f"device_{payload.action}", payload.device_id[:120])
    return {"message": message, "miniapp_devices": _device_rows_for_user(scope, payload.user_id)}


@app.get("/api/control-summary")
def control_summary_api(
    chat_id: int = Query(...), user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    if not (repo.is_tenant_admin(scope, user_id) or repo.user_can_manage_departments(scope, user_id)):
        raise HTTPException(status_code=403, detail="Раздел доступен руководителям и администраторам.")
    return {"control_summary": control_center.control_summary(scope, user_id)}


@app.post("/api/control-sla")
def control_sla_api(
    payload: SLASettingsPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    try:
        saved = control_center.save_sla_settings(scope, payload.user_id, payload.model_dump())
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    repo.log_site_action(scope, payload.user_id, "control_sla_update")
    return {"message": "Время реакции сохранено.", "sla": saved, "control_summary": control_center.control_summary(scope, payload.user_id)}


@app.get("/api/diagnostics")
def diagnostics_api(
    chat_id: int = Query(...), user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    try:
        return {"diagnostics": control_center.diagnostics_snapshot(scope, user_id)}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/operation-presets/batch")
def operation_preset_batch_api(
    payload: OperationPresetBatchPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    preset = next((x for x in _operation_presets_for_user(scope, payload.user_id) if int(x.get("id") or 0) == int(payload.preset_id)), None)
    if not preset:
        raise HTTPException(status_code=404, detail="Шаблон не найден.")
    multipliers = [float(x) for x in payload.multipliers[:50] if float(x) > 0]
    if not multipliers:
        raise HTTPException(status_code=400, detail="Укажите множители количества.")
    base_qty = float(preset.get("quantity") or 0)
    if base_qty <= 0:
        raise HTTPException(status_code=400, detail="В шаблоне не задано базовое количество.")
    items = []
    for mult in multipliers:
        items.append({
            "operation_type": preset.get("operation_type"), "entity_type": preset.get("entity_type"), "entity_id": int(preset.get("entity_id") or 0),
            "quantity": base_qty * mult, "unit": preset.get("unit") or "шт", "area_id": preset.get("area_id"),
            "from_area_id": preset.get("from_area_id"), "to_area_id": preset.get("to_area_id"), "destination_type": preset.get("destination_type") or "",
            "storage_place": preset.get("storage_place") or "", "note": preset.get("note") or "", "preset_id": int(preset["id"]),
        })
    return {"items": items}


@app.get("/api/entity-labels")
def entity_labels_api(
    chat_id: int = Query(...), user_id: int | None = Query(None), entity_ids: str = Query(...),
    code_type: str = Query("qr"), copies: int = Query(1), template_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_system_admin(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    try:
        ids = [int(part) for part in entity_ids.replace(";", ",").split(",") if part.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Неверный список позиций.") from exc
    if not ids or len(ids) > 200:
        raise HTTPException(status_code=400, detail="Выберите от 1 до 200 позиций.")
    template = None
    if template_id is not None:
        row = db.fetchone("SELECT * FROM label_templates WHERE chat_id=? AND id=?", (scope, int(template_id)))
        if not row:
            raise HTTPException(status_code=404, detail="Шаблон этикетки не найден.")
        template = dict(row)
    try:
        content = label_service.build_entity_labels_pdf(scope, ids, code_type="code128" if code_type == "code128" else "qr", copies=copies, template=template)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename = f"etiketki_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return StreamingResponse(
        io.BytesIO(content), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/continuity/settings")
def save_continuity_settings_api(
    payload: ContinuitySettingsPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_system_admin(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    settings_row = shift_continuity.save_continuity_settings(scope, payload.user_id, payload.model_dump())
    repo.log_site_action(scope, payload.user_id, "continuity_settings", "save")
    return {"message": "Напоминания сохранены.", "continuity_settings": settings_row}


@app.post("/api/continuity/checklist")
def save_handover_checklist_api(
    payload: HandoverChecklistSettingsPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_system_admin(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    items = shift_continuity.save_handover_checklist(scope, payload.user_id, payload.items, payload.name)
    repo.log_site_action(scope, payload.user_id, "handover_checklist", "save")
    return {"message": "Чек-лист передачи смены сохранён.", "handover_checklist": items}


@app.post("/api/label-templates")
def save_label_template_api(
    payload: LabelTemplatePayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_system_admin(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    shift_continuity.save_label_template(scope, payload.user_id, payload.model_dump())
    repo.log_site_action(scope, payload.user_id, "label_template", payload.name[:100])
    return {"message": "Шаблон этикетки сохранён.", "label_templates": shift_continuity.list_label_templates(scope)}


@app.get("/api/continuity-audit.xlsx")
def continuity_audit_export(
    chat_id: int = Query(...), user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_system_admin(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    content = continuity_audit.build_continuity_audit_xlsx(scope)
    repo.log_site_action(scope, user_id, "continuity_audit", "xlsx")
    filename = f"audit_smen_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(io.BytesIO(content), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/entity-codes")
def save_entity_code(
    payload: EntityCodePayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_system_admin(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    ok, message, code_id = repo.set_entity_code(scope, payload.entity_id, payload.code, payload.user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, "entity_code_save", str(code_id or ""))
    return {"message": message, "entity_codes": repo.list_entity_codes(scope), "entities": _entity_list(scope, payload.user_id), "work_access": _work_access_for_user(scope, payload.user_id)}


@app.delete("/api/entity-codes")
def remove_entity_code(
    chat_id: int = Query(...), user_id: int | None = Query(None), code_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_system_admin(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    if not repo.delete_entity_code(scope, code_id):
        raise HTTPException(status_code=404, detail="Код не найден.")
    repo.log_site_action(scope, user_id, "entity_code_delete", str(code_id))
    return {"message": "Код удалён.", "entity_codes": repo.list_entity_codes(scope), "entities": _entity_list(scope, user_id), "work_access": _work_access_for_user(scope, user_id)}


@app.get("/api/entity-codes/resolve")
def resolve_entity_code(
    chat_id: int = Query(...), code: str = Query(...), user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    item = repo.resolve_entity_code(scope, code)
    if not item:
        raise HTTPException(status_code=404, detail="Позиция с таким кодом не найдена.")
    allowed_ids = {int(entity["id"]) for access in _work_access_for_user(scope, user_id) for entity in access.get("entities", [])}
    if int(item["entity_id"]) not in allowed_ids:
        raise HTTPException(status_code=404, detail="Позиция с таким кодом недоступна.")
    return {"entity": {"id": int(item["entity_id"]), "type": item["entity_type"], "name": item["entity_name"], "unit": item["default_unit"], "code": item["code"]}}


@app.post("/api/operation-presets")
def save_operation_preset(
    payload: OperationPresetPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    preview_payload = OperationPayload(**payload.model_dump(), client_request_id="", confirm_warnings=True)
    _operation_preview(scope, payload.user_id, preview_payload)
    ok, message = repo.save_operation_preset(
        scope, payload.user_id, name=payload.name, operation_type=payload.operation_type,
        entity_type=payload.entity_type, entity_id=payload.entity_id, quantity=payload.quantity, unit=payload.unit,
        area_id=payload.area_id, from_area_id=payload.from_area_id, to_area_id=payload.to_area_id,
        destination_type=payload.destination_type, storage_place=payload.storage_place, note=payload.note,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"message": message, "operation_presets": _operation_presets_for_user(scope, payload.user_id)}


@app.delete("/api/operation-presets")
def delete_operation_preset(
    chat_id: int = Query(...), user_id: int | None = Query(None), preset_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id, submit=True)
    scope = repo.resolve_scope_chat_id(chat_id)
    if not repo.delete_operation_preset(scope, user_id, preset_id):
        raise HTTPException(status_code=404, detail="Быстрое действие не найдено.")
    return {"message": "Быстрое действие удалено.", "operation_presets": _operation_presets_for_user(scope, user_id)}


@app.get("/api/plans")
def get_plans(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    if repo.user_has_department_membership(scope, user_id) and not repo.is_tenant_admin(chat_id, user_id):
        raise HTTPException(status_code=403, detail="access denied")
    return {"targets": repo.list_assembly_plan_targets(scope)}


@app.post("/api/plans")
def save_plan(
    payload: PlanPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_system_admin(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    product = repo.get_entity(payload.product_id)
    if not product or product.chat_id != scope or product.entity_type != "product":
        raise HTTPException(status_code=400, detail="bad product")
    saved = repo.set_assembly_plan_targets(scope, payload.product_id, payload.targets)
    repo.log_site_action(scope, payload.user_id, "plan", product.name)
    return {"saved": saved, "targets": repo.list_assembly_plan_targets(scope)}


@app.delete("/api/plans")
def clear_plan(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    product_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_system_admin(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    removed = repo.clear_assembly_plan_product(scope, product_id) if product_id else repo.clear_assembly_plan(scope)
    repo.log_site_action(scope, user_id, "plan_clear")
    return {"removed": removed, "targets": repo.list_assembly_plan_targets(scope)}


@app.get("/api/audit")
def audit(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    permissions = repo.user_permissions_current_context(scope, user_id)
    if not repo.is_tenant_admin(scope, user_id):
        raise HTTPException(status_code=403, detail="access denied")
    repo.log_site_action(scope, user_id, "audit")
    return {
        "site_actions": repo.list_site_actions(scope, limit=12),
        "sync_events": repo.list_sync_events(scope, limit=12),
    }




@app.get("/api/security-status")
def security_status(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    permissions = repo.user_permissions_current_context(scope, user_id)
    return {
        "protected_access": bool(settings.miniapp_api_token or x_telegram_init_data),
        "encrypted_backups": bool(settings.backup_encryption_key),
        "can_download_backup": bool(permissions.get("setup") or permissions.get("export") or repo.is_tenant_admin(scope, user_id)),
        "can_restore_backup": bool(repo.is_tenant_admin(scope, user_id) or (repo.get_account_by_scope(scope) and int(repo.get_account_by_scope(scope).owner_user_id) == int(user_id))),
        "account_separated": True,
    }


@app.get("/api/backup")
def site_backup(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> FileResponse:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_system_admin(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    path = backups.create_account_backup(scope, user_id)
    repo.log_site_action(scope, user_id, "backup")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@app.post("/api/restore")
def site_restore(
    payload: RestoreBackupPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_system_admin(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    account = repo.get_account_by_scope(scope)
    if not (account and int(account.owner_user_id) == int(payload.user_id)):
        raise HTTPException(status_code=403, detail="restore owner only")
    if payload.confirmation.strip().upper() != "ВОССТАНОВИТЬ":
        raise HTTPException(status_code=400, detail="Введите слово ВОССТАНОВИТЬ.")
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
        result = backups.restore_account_backup(scope, payload.user_id, content, payload.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Восстановление не выполнено. Исходные данные сохранены.") from exc
    repo.log_site_action(scope, payload.user_id, "restore", str(result.get("created_at") or ""))
    return {"message": "Учёт восстановлен из копии.", **result}


@app.post("/api/report")
def make_report(
    payload: ReportPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> FileResponse:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    permissions = repo.user_permissions_current_context(scope, payload.user_id)
    if repo.user_has_department_membership(scope, payload.user_id) and not repo.is_tenant_admin(scope, payload.user_id):
        raise HTTPException(status_code=403, detail="department reports are not available")
    if not (permissions.get("reports") or permissions.get("export") or permissions.get("setup")) and not repo.is_tenant_admin(scope, payload.user_id):
        raise HTTPException(status_code=403, detail="access denied")
    access = repo.area_section_access_for_user(scope, payload.user_id, "reports")
    area_ids: set[int] | None = None
    if payload.area_id is not None:
        area = repo.get_area(payload.area_id)
        if not area or area.chat_id != scope:
            raise HTTPException(status_code=400, detail="bad area")
        if access.get("restricted") and int(payload.area_id) not in set(access.get("view") or []):
            raise HTTPException(status_code=403, detail="area access denied")
        area_ids = {int(payload.area_id)}
    elif access.get("restricted"):
        area_ids = set(access.get("view") or [])
        if not area_ids:
            raise HTTPException(status_code=403, detail="area access denied")
    if payload.format.lower() == "pdf":
        path = reporting.create_pdf_report(scope, payload.request_text, user_id=payload.user_id, area_ids=area_ids)
        media = "application/pdf"
    else:
        path = reporting.create_xlsx_report(scope, payload.request_text, user_id=payload.user_id, area_ids=area_ids)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    repo.log_site_action(scope, payload.user_id, "report", payload.format.lower())
    return FileResponse(path, media_type=media, filename=path.name)

# --- Отделы и ограниченная выдача доступа step70 ---

@app.post("/api/departments")
def department_save(
    payload: DepartmentPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_system_admin(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    ok, message, department_id = repo.save_department(scope, payload.user_id, payload.name, payload.description, payload.department_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, "department_save", str(department_id or ""))
    return {"message": message, "departments": repo.list_departments(scope)}


@app.delete("/api/departments")
def department_delete(
    chat_id: int = Query(...), user_id: int | None = Query(None), department_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_system_admin(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    ok, message = repo.archive_department(scope, user_id, department_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"message": message, "departments": repo.list_departments(scope)}


@app.post("/api/departments/operation")
def department_operation_save(
    payload: DepartmentOperationPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_system_admin(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    ok, message = repo.set_department_operation_rule(scope, payload.user_id, payload.department_id, payload.operation_key, can_view=payload.can_view, can_submit=payload.can_submit, can_edit=payload.can_edit)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"message": message, "departments": repo.list_departments(scope)}


@app.delete("/api/departments/operation")
def department_operation_delete(
    chat_id: int = Query(...), user_id: int | None = Query(None), department_id: int = Query(...), operation_key: str = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_system_admin(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    ok, message = repo.delete_department_operation_rule(scope, user_id, department_id, operation_key)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"message": message, "departments": repo.list_departments(scope)}


@app.post("/api/departments/entity")
def department_entity_save(
    payload: DepartmentEntityPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_system_admin(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    ok, message = repo.set_department_entity_rule(scope, payload.user_id, payload.department_id, payload.operation_key, payload.entity_type, payload.entity_id, can_view=payload.can_view, can_submit=payload.can_submit)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"message": message, "departments": repo.list_departments(scope)}


@app.delete("/api/departments/entity")
def department_entity_delete(
    chat_id: int = Query(...), user_id: int | None = Query(None), department_id: int = Query(...), operation_key: str = Query(...), entity_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_system_admin(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    ok, message = repo.delete_department_entity_rule(scope, user_id, department_id, operation_key, entity_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"message": message, "departments": repo.list_departments(scope)}


@app.post("/api/departments/member")
def department_member_save(
    payload: DepartmentMemberPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    ok, message = repo.save_department_member(scope, payload.user_id, payload.department_id, payload.member_user_id, payload.display_name, payload.role_level, payload.operation_keys)
    if not ok:
        raise HTTPException(status_code=403, detail=message)
    return {"message": message, "departments": repo.list_departments(scope, None if repo.is_tenant_admin(scope, payload.user_id) else payload.user_id)}


@app.delete("/api/departments/member")
def department_member_delete(
    chat_id: int = Query(...), user_id: int | None = Query(None), department_id: int = Query(...), member_user_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    ok, message = repo.archive_department_member(scope, user_id, department_id, member_user_id)
    if not ok:
        raise HTTPException(status_code=403, detail=message)
    return {"message": message, "departments": repo.list_departments(scope, None if repo.is_tenant_admin(scope, user_id) else user_id)}


@app.post("/api/job-titles")
def save_job_title(
    payload: JobTitlePayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_system_admin(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    if payload.job_title_id:
        ok, message = repo.update_job_title_record(scope, payload.job_title_id, payload.name, payload.permissions)
        action = "job_title_update"
    else:
        cleaned = {key: bool(payload.permissions.get(key)) for key in repo.PERMISSION_KEYS}
        ok, message = repo.create_job_title(scope, payload.name, cleaned)
        action = "job_title_create"
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, action, payload.name)
    return {"message": message, "job_titles": repo.list_job_titles_detailed(scope)}


@app.delete("/api/job-titles")
def delete_job_title(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    job_title_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_system_admin(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    ok, message = repo.archive_job_title_record(scope, job_title_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, user_id, "job_title_archive", str(job_title_id))
    return {"message": message, "job_titles": repo.list_job_titles_detailed(scope), "area_access_rules": repo.list_area_section_access(scope)}


@app.post("/api/workers")
def save_worker(
    payload: WorkerPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_system_admin(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    ok, message = repo.save_worker_record(scope, payload.worker_user_id, payload.display_name, payload.job_title_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, "worker_save", str(payload.worker_user_id))
    return {"message": message, "workers": repo.list_workers_detailed(scope)}


@app.delete("/api/workers")
def delete_worker(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    worker_user_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_system_admin(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    ok, message = repo.archive_worker_record(scope, worker_user_id)
    if not ok:
        raise HTTPException(status_code=404, detail=message)
    repo.log_site_action(scope, user_id, "worker_archive", str(worker_user_id))
    return {"message": message, "workers": repo.list_workers_detailed(scope)}


@app.get("/api/inventory-history")
def inventory_history(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    area_id: int | None = Query(None),
    entity_type: str | None = Query(None),
    entity_id: int | None = Query(None),
    limit: int = Query(60),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    permissions = repo.user_permissions_current_context(scope, user_id)
    if not (permissions.get("stock") or permissions.get("edit") or permissions.get("setup") or permissions.get("reports")) and not repo.is_tenant_admin(scope, user_id):
        raise HTTPException(status_code=403, detail="access denied")
    if area_id is not None:
        area = repo.get_area(area_id)
        if not area or area.chat_id != scope:
            raise HTTPException(status_code=400, detail="bad area")
        if not repo.user_area_action_allowed(scope, user_id, "inventory", area_id, "view"):
            raise HTTPException(status_code=403, detail="area access denied")
    elif repo.area_section_access_for_user(scope, user_id, "inventory").get("restricted"):
        raise HTTPException(status_code=400, detail="area required")
    if repo.user_has_department_membership(scope, user_id) and not repo.is_tenant_admin(scope, user_id):
        if entity_id is None:
            raise HTTPException(status_code=400, detail="entity required")
        visible_ids = repo.visible_entity_ids_for_user(scope, user_id) or set()
        if int(entity_id) not in visible_ids:
            raise HTTPException(status_code=403, detail="entity access denied")
    rows = repo.list_inventory_history(scope, area_id=area_id, entity_type=entity_type, entity_id=entity_id, limit=limit)
    repo.log_site_action(scope, user_id, "inventory_history")
    return {"history": rows}


@app.post("/api/inventory-correction")
def inventory_correction(
    payload: InventoryCorrectionPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    permissions = repo.user_permissions_current_context(scope, payload.user_id)
    if not repo.is_tenant_admin(scope, payload.user_id) and not (permissions.get("setup") or (permissions.get("stock") and permissions.get("edit"))):
        raise HTTPException(status_code=403, detail="access denied")
    if payload.actual_quantity < 0:
        raise HTTPException(status_code=400, detail="bad quantity")
    area = repo.get_area(payload.area_id)
    entity = repo.get_entity(payload.entity_id)
    if not area or area.chat_id != scope:
        raise HTTPException(status_code=400, detail="bad area")
    if not entity or entity.chat_id != scope or entity.entity_type != payload.entity_type or entity.entity_type not in {"component", "product", "material", "stock_item"}:
        raise HTTPException(status_code=400, detail="bad entity")
    _check_operation_permission(scope, payload.user_id, "inventory_adjust", area_id=payload.area_id, entity_type=entity.entity_type, entity_id=entity.id)
    if not repo.user_area_action_allowed(scope, payload.user_id, "inventory", payload.area_id, "edit"):
        raise HTTPException(status_code=403, detail="area access denied")
    unit = payload.unit.strip() or entity.default_unit or "шт"
    old_quantity = repo.inventory_quantity(scope, entity.entity_type, entity.id, unit, payload.area_id)
    delta = float(payload.actual_quantity) - float(old_quantity)
    if abs(delta) > 1e-9 and not payload.note.strip():
        raise HTTPException(status_code=400, detail="Укажите причину корректировки остатка.")
    saved = 0
    if abs(delta) > 1e-9:
        saved = accounting.apply_operations(
            scope,
            payload.chat_id,
            payload.user_id,
            [{
                "operation_type": "inventory_adjust",
                "entity_type": entity.entity_type,
                "entity_id": entity.id,
                "entity_name": entity.name,
                "quantity": delta,
                "unit": unit,
                "area_id": payload.area_id,
            }],
            raw_text=payload.note.strip() or "Корректировка остатков на сайте",
        )
    if abs(delta) > 1e-9:
        control_center.record_decision(
            scope, payload.user_id, worker_user_id=payload.user_id, target_type="inventory_correction", target_id=int(saved or 0),
            action="adjust", reason=payload.note.strip(), before_status=str(old_quantity), after_status=str(payload.actual_quantity),
            metadata={"entity_id": entity.id, "entity_name": entity.name, "area_id": payload.area_id, "delta": delta},
        )
    repo.log_site_action(scope, payload.user_id, "inventory_correction", f"{entity.name}: {old_quantity} -> {payload.actual_quantity}")
    repo.log_sync_event(scope, "site", "saved", "inventory correction")
    return {
        "saved": saved,
        "old_quantity": old_quantity,
        "actual_quantity": payload.actual_quantity,
        "delta": delta,
        "inventory_positions": repo.list_inventory_positions(scope, area_ids=_inventory_area_ids(scope, payload.user_id)),
        "history": repo.list_inventory_history(scope, area_id=payload.area_id, entity_type=entity.entity_type, entity_id=entity.id, limit=60),
        "dashboard": _dashboard_for_user(scope, payload.user_id),
    }


@app.post("/api/report-presets")
def save_report_preset(
    payload: ReportPresetPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    permissions = repo.user_permissions_current_context(scope, payload.user_id)
    if not (permissions.get("reports") or permissions.get("export") or permissions.get("setup")) and not repo.is_tenant_admin(scope, payload.user_id):
        raise HTTPException(status_code=403, detail="access denied")
    if payload.area_id is not None and not repo.user_area_action_allowed(scope, payload.user_id, "reports", payload.area_id, "view"):
        raise HTTPException(status_code=403, detail="area access denied")
    ok, message, saved_id = repo.save_report_preset(
        scope, payload.user_id, payload.name, payload.request_text, payload.format, payload.area_id, payload.preset_id
    )
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, "report_preset_save", str(saved_id or ""))
    return {"message": message, "presets": _report_presets_for_user(scope, payload.user_id)}


@app.delete("/api/report-presets")
def delete_report_preset(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    preset_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    if not repo.archive_report_preset(scope, user_id, preset_id):
        raise HTTPException(status_code=404, detail="preset not found")
    repo.log_site_action(scope, user_id, "report_preset_archive", str(preset_id))
    return {"presets": _report_presets_for_user(scope, user_id)}


@app.post("/api/destinations")
def save_destination(
    payload: DestinationPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, manage=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    destination_type = (payload.destination_type or "storage").strip()
    if destination_type not in {"storage", "client", "fulfillment", "other"}:
        raise HTTPException(status_code=400, detail="bad destination type")
    if payload.destination_id:
        ok, message = repo.update_destination(scope, payload.destination_id, payload.name, destination_type)
        action = "destination_update"
    else:
        ok, message = repo.create_destination(scope, payload.name, destination_type)
        action = "destination_create"
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, action, payload.name)
    return {"message": message, "destinations": repo.list_destinations(scope)}


@app.delete("/api/destinations")
def delete_destination(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    destination_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id, manage=True)
    scope = repo.resolve_scope_chat_id(chat_id)
    if not repo.archive_destination(scope, destination_id):
        raise HTTPException(status_code=404, detail="destination not found")
    repo.log_site_action(scope, user_id, "destination_archive", str(destination_id))
    return {"destinations": repo.list_destinations(scope)}


@app.post("/api/area-access")
def save_area_access(
    payload: AreaAccessPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_system_admin(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    ok = repo.set_area_section_access(
        scope,
        payload.job_title_id,
        payload.area_id,
        payload.section_key,
        can_view=payload.can_view,
        can_submit=payload.can_submit,
        can_edit=payload.can_edit,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="bad area access")
    repo.log_site_action(scope, payload.user_id, "area_access_save", payload.section_key)
    return {"rules": repo.list_area_section_access(scope)}


@app.delete("/api/area-access")
def remove_area_access(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    job_title_id: int = Query(...),
    area_id: int = Query(...),
    section_key: str = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id, manage=True)
    scope = repo.resolve_scope_chat_id(chat_id)
    repo.delete_area_section_access(scope, job_title_id, area_id, section_key)
    repo.log_site_action(scope, user_id, "area_access_delete", section_key)
    return {"rules": repo.list_area_section_access(scope)}


# --- Массовая инвентаризация step66 ---


def _inventory_session_permission(scope: int, user_id: int, area_id: int, action: str) -> None:
    if repo.is_tenant_admin(scope, user_id):
        return
    permissions = repo.user_permissions_current_context(scope, user_id)
    if not permissions.get("stock"):
        raise HTTPException(status_code=403, detail="access denied")
    if not repo.user_area_action_allowed(scope, user_id, "inventory", area_id, action):
        raise HTTPException(status_code=403, detail="area access denied")


def _deny_department_inventory_session_access(scope: int, user_id: int) -> None:
    # Массовый пересчёт содержит данные всего участка. Сотрудники отделов
    # работают только через разрешённые им позиции и не получают этот API.
    if repo.user_has_department_membership(scope, user_id) and not repo.is_tenant_admin(scope, user_id):
        raise HTTPException(status_code=403, detail="access denied")


@app.get("/api/inventory-sessions")
def inventory_sessions_list(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    session_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    _deny_department_inventory_session_access(scope, user_id)
    permissions = repo.user_permissions_current_context(scope, user_id)
    if not repo.is_tenant_admin(scope, user_id) and not (permissions.get("stock") or permissions.get("reports") or permissions.get("setup")):
        raise HTTPException(status_code=403, detail="access denied")
    if session_id is not None:
        session = repo.get_inventory_session(scope, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="session not found")
        if not repo.user_area_action_allowed(scope, user_id, "inventory", int(session["area_id"]), "view"):
            raise HTTPException(status_code=403, detail="area access denied")
        return {"session": session}
    return {"sessions": repo.list_inventory_sessions(scope, area_ids=_inventory_area_ids(scope, user_id))}


@app.post("/api/inventory-sessions")
def inventory_session_create(
    payload: InventorySessionPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    _deny_department_inventory_session_access(scope, payload.user_id)
    _inventory_session_permission(scope, payload.user_id, payload.area_id, "submit")
    ok, message, session_id = repo.create_inventory_session(scope, payload.area_id, payload.user_id, payload.note)
    if not ok or not session_id:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, "inventory_session_create", str(session_id))
    return {"message": message, "session": repo.get_inventory_session(scope, session_id), "sessions": repo.list_inventory_sessions(scope, area_ids=_inventory_area_ids(scope, payload.user_id))}


@app.post("/api/inventory-session-items")
def inventory_session_item_save(
    payload: InventorySessionItemPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    _deny_department_inventory_session_access(scope, payload.user_id)
    session = repo.get_inventory_session(scope, payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if int(session.get("created_by") or 0) != int(payload.user_id) and not repo.user_can_manage_current_context(scope, payload.user_id):
        raise HTTPException(status_code=403, detail="access denied")
    _inventory_session_permission(scope, payload.user_id, int(session["area_id"]), "submit")
    entity = repo.get_entity(payload.entity_id)
    unit = payload.unit.strip() or (entity.default_unit if entity else "шт")
    ok, message = repo.save_inventory_session_item(scope, payload.session_id, payload.entity_type, payload.entity_id, unit, payload.actual_quantity, payload.note)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, "inventory_session_item", str(payload.session_id))
    return {"message": message, "session": repo.get_inventory_session(scope, payload.session_id), "sessions": repo.list_inventory_sessions(scope, area_ids=_inventory_area_ids(scope, payload.user_id))}


@app.delete("/api/inventory-session-items")
def inventory_session_item_delete(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    session_id: int = Query(...),
    item_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id, submit=True)
    scope = repo.resolve_scope_chat_id(chat_id)
    _deny_department_inventory_session_access(scope, user_id)
    session = repo.get_inventory_session(scope, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if int(session.get("created_by") or 0) != int(user_id) and not repo.user_can_manage_current_context(scope, user_id):
        raise HTTPException(status_code=403, detail="access denied")
    if not repo.delete_inventory_session_item(scope, session_id, item_id):
        raise HTTPException(status_code=404, detail="item not found")
    return {"session": repo.get_inventory_session(scope, session_id), "sessions": repo.list_inventory_sessions(scope, area_ids=_inventory_area_ids(scope, user_id))}


@app.post("/api/inventory-session-action")
def inventory_session_action(
    payload: InventorySessionActionPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    _deny_department_inventory_session_access(scope, payload.user_id)
    session = repo.get_inventory_session(scope, payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    action = payload.action.strip().lower()
    if action in {"submit", "cancel"}:
        if int(session.get("created_by") or 0) != int(payload.user_id) and not repo.user_can_manage_current_context(scope, payload.user_id):
            raise HTTPException(status_code=403, detail="access denied")
        _inventory_session_permission(scope, payload.user_id, int(session["area_id"]), "submit")
        if action == "submit":
            ok, message = repo.submit_inventory_session(scope, payload.session_id)
        else:
            ok, message = repo.decide_inventory_session(scope, payload.session_id, payload.user_id, "cancelled", payload.note)
        saved = 0
    elif action in {"approve", "reject"}:
        permissions = repo.user_permissions_current_context(scope, payload.user_id)
        can_approve = repo.is_tenant_admin(scope, payload.user_id) or repo.user_can_manage_current_context(scope, payload.user_id) or (permissions.get("stock") and permissions.get("edit"))
        if not can_approve:
            raise HTTPException(status_code=403, detail="access denied")
        _inventory_session_permission(scope, payload.user_id, int(session["area_id"]), "edit")
        if action == "approve":
            ok, message, saved = inventory_session_service.approve_session(scope, payload.session_id, payload.user_id, payload.note)
        else:
            ok, message = repo.decide_inventory_session(scope, payload.session_id, payload.user_id, "rejected", payload.note)
            saved = 0
    else:
        raise HTTPException(status_code=400, detail="bad action")
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    if action == "submit":
        repo.queue_inventory_approval_notifications(scope, payload.session_id)
    elif action in {"approve", "reject", "cancel"}:
        result_status = {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[action]
        repo.resolve_inventory_approval_notifications(scope, payload.session_id, result_status, payload.user_id)
    repo.log_site_action(scope, payload.user_id, f"inventory_session_{action}", str(payload.session_id))
    repo.log_sync_event(scope, "site", "saved", f"inventory session {action}")
    return {
        "message": message,
        "saved": saved,
        "session": repo.get_inventory_session(scope, payload.session_id),
        "sessions": repo.list_inventory_sessions(scope, area_ids=_inventory_area_ids(scope, payload.user_id)),
        "inventory_positions": repo.list_inventory_positions(scope, area_ids=_inventory_area_ids(scope, payload.user_id)),
        "dashboard": _dashboard_for_user(scope, payload.user_id),
        "inbox_items": repo.list_inbox_items(scope, payload.user_id),
    }


# --- Входящие ответственных step67 ---

@app.get("/api/inbox")
def inbox_items(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    return {"inbox_items": repo.list_inbox_items(scope, user_id)}


@app.post("/api/inbox/read")
def inbox_mark_read(
    payload: InboxReadPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    if not repo.mark_inbox_item_read(scope, payload.user_id, payload.item_id):
        raise HTTPException(status_code=404, detail="inbox item not found")
    return {"inbox_items": repo.list_inbox_items(scope, payload.user_id)}


# --- Смены и активность сотрудников step66/67 ---

@app.get("/api/worker-activity")
def worker_activity(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    days: int = Query(30),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    can_manage = repo.user_can_manage_current_context(scope, user_id)
    return {
        "activity": repo.worker_activity_analytics(scope, days, None if can_manage else user_id),
        "shifts": repo.list_worker_shifts(scope, None if can_manage else user_id, limit=120),
        "shift_plans": repo.list_shift_plans(scope, None if can_manage else user_id),
        "attendance_deviations": repo.attendance_deviations(scope, None if can_manage else user_id, days),
    }


@app.post("/api/shifts/start")
def shift_start(
    payload: ShiftPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    target = int(payload.worker_user_id or payload.user_id)
    if target != int(payload.user_id) and not shift_continuity.can_review_worker_packages(scope, payload.user_id, target):
        raise HTTPException(status_code=403, detail="access denied")
    if payload.area_id is not None and not repo.user_area_action_allowed(scope, target, "overview", payload.area_id, "view") and not repo.is_tenant_admin(scope, payload.user_id):
        raise HTTPException(status_code=403, detail="area access denied")
    ok, message, _shift_id = repo.start_worker_shift(scope, target, payload.area_id, payload.user_id, payload.note)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, "shift_start", str(target))
    can_manage = repo.user_can_manage_current_context(scope, payload.user_id)
    return {"message": message, "activity": repo.worker_activity_analytics(scope, 30, None if can_manage else payload.user_id), "shifts": repo.list_worker_shifts(scope, None if can_manage else payload.user_id), "current_open_shift": shift_continuity.current_open_shift(scope, payload.user_id), "shift_packages": _shift_packages_for_user(scope, payload.user_id), "shift_handovers": shift_continuity.list_handovers(scope, payload.user_id, can_manage=(repo.is_tenant_admin(scope, payload.user_id) or repo.user_can_manage_departments(scope, payload.user_id))), "shift_plans": repo.list_shift_plans(scope, None if can_manage else payload.user_id), "attendance_deviations": repo.attendance_deviations(scope, None if can_manage else payload.user_id, 30)}


@app.post("/api/shifts/end")
def shift_end(
    payload: ShiftPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    target = int(payload.worker_user_id or payload.user_id)
    if target != int(payload.user_id) and not shift_continuity.can_review_worker_packages(scope, payload.user_id, target):
        raise HTTPException(status_code=403, detail="access denied")
    ok, message = repo.end_worker_shift(scope, target, payload.user_id, payload.note)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, "shift_end", str(target))
    can_manage = repo.user_can_manage_current_context(scope, payload.user_id)
    return {"message": message, "activity": repo.worker_activity_analytics(scope, 30, None if can_manage else payload.user_id), "shifts": repo.list_worker_shifts(scope, None if can_manage else payload.user_id), "current_open_shift": shift_continuity.current_open_shift(scope, payload.user_id), "shift_packages": _shift_packages_for_user(scope, payload.user_id), "shift_handovers": shift_continuity.list_handovers(scope, payload.user_id, can_manage=(repo.is_tenant_admin(scope, payload.user_id) or repo.user_can_manage_departments(scope, payload.user_id))), "shift_plans": repo.list_shift_plans(scope, None if can_manage else payload.user_id), "attendance_deviations": repo.attendance_deviations(scope, None if can_manage else payload.user_id, 30)}


# --- Планирование смен step67 ---

@app.post("/api/shift-plans")
def shift_plan_save(
    payload: ShiftPlanPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, manage=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    if payload.area_id is not None and not repo.user_area_action_allowed(scope, payload.worker_user_id, "overview", payload.area_id, "view") and not repo.is_tenant_admin(scope, payload.user_id):
        raise HTTPException(status_code=403, detail="area access denied")
    ok, message, plan_id = repo.create_shift_plan(
        scope, payload.worker_user_id, payload.area_id, payload.planned_start, payload.planned_end, payload.user_id, payload.note
    )
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.create_inbox_item(
        scope, payload.worker_user_id, "shift_plan", "Назначена плановая смена",
        f"Начало: {payload.planned_start.replace('T', ' ')}. Окончание: {payload.planned_end.replace('T', ' ')}.",
        "shift_plan", plan_id, deduplicate=False,
    )
    repo.log_site_action(scope, payload.user_id, "shift_plan_create", str(plan_id or ""))
    return {"message": message, "shift_plans": repo.list_shift_plans(scope), "attendance_deviations": repo.attendance_deviations(scope, None, 30)}


@app.delete("/api/shift-plans")
def shift_plan_delete(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    plan_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id, manage=True)
    scope = repo.resolve_scope_chat_id(chat_id)
    ok, message = repo.cancel_shift_plan(scope, plan_id, user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, user_id, "shift_plan_cancel", str(plan_id))
    return {"message": message, "shift_plans": repo.list_shift_plans(scope), "attendance_deviations": repo.attendance_deviations(scope, None, 30)}


# --- Повторяющиеся графики, календарь и посещаемость step68 ---

@app.post("/api/shift-templates")
def shift_template_save(
    payload: ShiftTemplatePayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id, manage=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    if payload.area_id is not None and not repo.user_area_action_allowed(scope, payload.worker_user_id, "overview", payload.area_id, "view") and not repo.is_tenant_admin(scope, payload.user_id):
        raise HTTPException(status_code=403, detail="area access denied")
    ok, message, template_id = repo.save_shift_template(
        scope, payload.worker_user_id, payload.area_id, payload.pattern_type, payload.weekdays,
        payload.cycle_work_days, payload.cycle_rest_days, payload.cycle_anchor_date,
        payload.start_time, payload.end_time, payload.valid_from, payload.valid_until,
        payload.user_id, payload.note, payload.template_id, payload.enabled,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, "shift_template_save", str(template_id or ""))
    return {
        "message": message,
        "shift_templates": repo.list_shift_templates(scope),
        "shift_plans": repo.list_shift_plans(scope),
        "shift_calendar": repo.shift_calendar(scope, str(date.today() - timedelta(days=7)), str(date.today() + timedelta(days=60))),
    }


@app.delete("/api/shift-templates")
def shift_template_delete(
    chat_id: int = Query(...), user_id: int | None = Query(None), template_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id, manage=True)
    scope = repo.resolve_scope_chat_id(chat_id)
    ok, message = repo.disable_shift_template(scope, template_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail=message)
    repo.log_site_action(scope, user_id, "shift_template_disable", str(template_id))
    return {"message": message, "shift_templates": repo.list_shift_templates(scope), "shift_plans": repo.list_shift_plans(scope)}


@app.get("/api/shift-calendar")
def shift_calendar(
    chat_id: int = Query(...), user_id: int | None = Query(None),
    start_date: str = Query(...), end_date: str = Query(...),
    worker_user_id: int | None = Query(None), area_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    can_manage = repo.user_can_manage_current_context(scope, user_id)
    selected_worker = worker_user_id if can_manage else user_id
    if area_id is not None and not repo.user_area_action_allowed(scope, user_id, "overview", area_id, "view"):
        raise HTTPException(status_code=403, detail="area access denied")
    return {"shift_calendar": repo.shift_calendar(scope, start_date, end_date, selected_worker, area_id)}


@app.get("/api/attendance-summary")
def attendance_summary(
    chat_id: int = Query(...), user_id: int | None = Query(None),
    start_date: str = Query(...), end_date: str = Query(...),
    worker_user_id: int | None = Query(None), area_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    permissions = repo.user_permissions_current_context(scope, user_id)
    can_manage = repo.user_can_manage_current_context(scope, user_id)
    if not can_manage and not (permissions.get("reports") or permissions.get("export")):
        worker_user_id = user_id
    if not can_manage:
        worker_user_id = user_id
    if area_id is not None and not repo.user_area_action_allowed(scope, user_id, "reports", area_id, "view"):
        raise HTTPException(status_code=403, detail="area access denied")
    return {
        "attendance_summary": repo.attendance_summary(scope, start_date, end_date, worker_user_id, area_id),
        "attendance_details": repo.attendance_detail_rows(scope, start_date, end_date, worker_user_id, area_id),
    }


@app.get("/api/attendance-export")
def attendance_export(
    chat_id: int = Query(...), user_id: int | None = Query(None),
    start_date: str = Query(...), end_date: str = Query(...), report_format: str = Query("xlsx"),
    worker_user_id: int | None = Query(None), area_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> FileResponse:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    permissions = repo.user_permissions_current_context(scope, user_id)
    can_manage = repo.user_can_manage_current_context(scope, user_id)
    if not can_manage and not (permissions.get("reports") or permissions.get("export")):
        raise HTTPException(status_code=403, detail="access denied")
    if not can_manage:
        worker_user_id = user_id
    if area_id is not None and not repo.user_area_action_allowed(scope, user_id, "reports", area_id, "view"):
        raise HTTPException(status_code=403, detail="area access denied")
    try:
        if report_format.lower() == "pdf":
            path = reporting.create_attendance_pdf_report(scope, start_date, end_date, worker_user_id, area_id)
            media = "application/pdf"
        else:
            path = reporting.create_attendance_xlsx_report(scope, start_date, end_date, worker_user_id, area_id)
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repo.log_site_action(scope, user_id, "attendance_export", report_format.lower())
    return FileResponse(path, media_type=media, filename=path.name)


@app.post("/api/notification-preferences")
def notification_preferences_save(
    payload: NotificationPreferencesPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    values = payload.model_dump(exclude={"chat_id", "user_id"})
    saved = repo.save_notification_preferences(scope, payload.user_id, values)
    repo.log_site_action(scope, payload.user_id, "notification_preferences")
    return {"message": "Настройки уведомлений сохранены.", "notification_preferences": saved}


# --- Автоматическая отправка отчётов step66/67 ---

@app.post("/api/report-schedules")
def report_schedule_save(
    payload: ReportSchedulePayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    permissions = repo.user_permissions_current_context(scope, payload.user_id)
    if not repo.is_tenant_admin(scope, payload.user_id) and not (permissions.get("reports") or permissions.get("export") or permissions.get("setup")):
        raise HTTPException(status_code=403, detail="access denied")
    preset = repo.get_report_preset(scope, payload.user_id, payload.preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="preset not found")
    if preset.get("area_id") is not None and not repo.user_area_action_allowed(scope, payload.user_id, "reports", int(preset["area_id"]), "view"):
        raise HTTPException(status_code=403, detail="area access denied")
    try:
        timezone_name = report_scheduler.normalize_timezone_name(payload.timezone_name)
        next_run_at = report_scheduler.next_run_text(payload.frequency, payload.hour, payload.minute, payload.weekday, payload.month_day, timezone_name=timezone_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    delivery_chat_id = int(payload.delivery_chat_id or payload.user_id)
    ok, message, schedule_id = repo.save_report_schedule(
        scope, payload.user_id, payload.preset_id, delivery_chat_id, payload.frequency,
        payload.hour, payload.minute, payload.weekday, payload.month_day, next_run_at, payload.enabled, timezone_name,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, "report_schedule_save", str(schedule_id or ""))
    return {"message": message, "schedules": repo.list_report_schedules(scope, payload.user_id)}


@app.delete("/api/report-schedules")
def report_schedule_delete(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    schedule_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    if not repo.delete_report_schedule(scope, user_id, schedule_id):
        raise HTTPException(status_code=404, detail="schedule not found")
    repo.log_site_action(scope, user_id, "report_schedule_delete", str(schedule_id))
    return {"schedules": repo.list_report_schedules(scope, user_id), "delivery_history": repo.list_report_delivery_history(scope, user_id)}


@app.get("/api/report-deliveries")
def report_delivery_history(
    chat_id: int = Query(...),
    user_id: int | None = Query(None),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    user_id = _request_user(user_id, auth_user_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="access denied")
    _check_user(chat_id, user_id)
    scope = repo.resolve_scope_chat_id(chat_id)
    return {"delivery_history": repo.list_report_delivery_history(scope, user_id)}


@app.post("/api/report-deliveries/retry")
def report_delivery_retry(
    payload: ReportRetryPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    _check_user(payload.chat_id, payload.user_id)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    ok, message, history_id = repo.queue_report_delivery_retry(scope, payload.user_id, payload.schedule_id, payload.history_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, "report_delivery_retry", str(history_id or ""))
    return {"message": message, "delivery_history": repo.list_report_delivery_history(scope, payload.user_id)}


# ---------------- Step 82: company structure / transfers / Excel ----------
@app.get("/api/company/structure")
def company_structure_api(chat_id:int=Query(...), user_id:int|None=Query(None),
    x_access_token:Annotated[str|None,Header()]=None,x_telegram_init_data:Annotated[str|None,Header()]=None)->dict[str,object]:
    auth=_check_token(x_access_token,x_telegram_init_data);uid=_request_user(user_id,auth)
    if uid is None:raise HTTPException(status_code=403,detail="access denied")
    _check_user(chat_id,uid);scope=repo.resolve_scope_chat_id(chat_id)
    areas=[]
    for a in repo.list_areas(scope):
        row=db.fetchone('SELECT site_id FROM areas WHERE id=? AND chat_id=?',(a.id,scope));areas.append({'id':a.id,'name':a.name,'site_id':row['site_id'] if row else None})
    # This endpoint is used by the ordinary workplace and transfer forms. Never send
    # department members/rules here: only neutral destination names are required.
    departments=[dict(r) for r in db.fetchall("SELECT id,name FROM departments WHERE chat_id=? AND is_archived=0 ORDER BY name",(scope,))]
    stock=repo.stock_location_breakdown(scope)
    visible=repo.visible_entity_ids_for_user(scope,uid)
    if visible is not None:
        stock=[x for x in stock if int(x.get('entity_id') or 0) in visible]
    return {'sites':repo.list_company_sites(scope),'areas':areas,'departments':departments,'storage_locations':repo.list_storage_locations(scope),'stock':stock}

@app.post("/api/company/sites")
def company_site_api(payload:CompanySitePayload,x_access_token:Annotated[str|None,Header()]=None,x_telegram_init_data:Annotated[str|None,Header()]=None)->dict[str,object]:
    auth=_check_token(x_access_token,x_telegram_init_data);payload.user_id=_request_user(payload.user_id,auth) or payload.user_id
    _check_system_admin(payload.chat_id,payload.user_id);ok,msg,site_id=repo.create_company_site(payload.chat_id,payload.user_id,payload.settlement,payload.name,payload.address)
    if not ok:raise HTTPException(status_code=400,detail=msg)
    return {'message':msg,'site_id':site_id,'sites':repo.list_company_sites(payload.chat_id)}

@app.post("/api/company/area-site")
def area_site_api(payload:AreaSitePayload,x_access_token:Annotated[str|None,Header()]=None,x_telegram_init_data:Annotated[str|None,Header()]=None)->dict[str,object]:
    auth=_check_token(x_access_token,x_telegram_init_data);payload.user_id=_request_user(payload.user_id,auth) or payload.user_id
    _check_system_admin(payload.chat_id,payload.user_id);ok,msg=repo.bind_area_to_site(payload.chat_id,payload.user_id,payload.area_id,payload.site_id)
    if not ok:raise HTTPException(status_code=400,detail=msg)
    return {'message':msg}

@app.post("/api/company/storage-locations")
def storage_location_api(payload:StorageLocationPayload,x_access_token:Annotated[str|None,Header()]=None,x_telegram_init_data:Annotated[str|None,Header()]=None)->dict[str,object]:
    auth=_check_token(x_access_token,x_telegram_init_data);payload.user_id=_request_user(payload.user_id,auth) or payload.user_id
    _check_system_admin(payload.chat_id,payload.user_id);ok,msg,lid=repo.create_storage_location(payload.chat_id,payload.user_id,payload.name,payload.site_id,payload.area_id,payload.department_id,payload.code)
    if not ok:raise HTTPException(status_code=400,detail=msg)
    return {'message':msg,'location_id':lid,'storage_locations':repo.list_storage_locations(payload.chat_id)}

@app.get("/api/stock/locations")
def stock_locations_api(chat_id:int=Query(...),user_id:int|None=Query(None),entity_type:str|None=Query(None),entity_id:int|None=Query(None),x_access_token:Annotated[str|None,Header()]=None,x_telegram_init_data:Annotated[str|None,Header()]=None)->dict[str,object]:
    auth=_check_token(x_access_token,x_telegram_init_data);uid=_request_user(user_id,auth)
    if uid is None:raise HTTPException(status_code=403,detail='access denied')
    _check_user(chat_id,uid);scope=repo.resolve_scope_chat_id(chat_id)
    rows=repo.stock_location_breakdown(scope,entity_type,entity_id);visible=repo.visible_entity_ids_for_user(scope,uid)
    if visible is not None:rows=[x for x in rows if int(x.get('entity_id') or 0) in visible]
    return {'stock':rows}

@app.get("/api/transfers")
def transfers_api(chat_id:int=Query(...),user_id:int|None=Query(None),status:str|None=Query(None),x_access_token:Annotated[str|None,Header()]=None,x_telegram_init_data:Annotated[str|None,Header()]=None)->dict[str,object]:
    auth=_check_token(x_access_token,x_telegram_init_data);uid=_request_user(user_id,auth)
    if uid is None:raise HTTPException(status_code=403,detail='access denied')
    _check_user(chat_id,uid)
    try:return {'transfers':stock_transfers.list_transfers(chat_id,uid,status)}
    except PermissionError as exc:raise HTTPException(status_code=403,detail=str(exc))

@app.post("/api/transfers")
def create_transfer_api(payload:TransferCreatePayload,x_access_token:Annotated[str|None,Header()]=None,x_telegram_init_data:Annotated[str|None,Header()]=None)->dict[str,object]:
    auth=_check_token(x_access_token,x_telegram_init_data);payload.user_id=_request_user(payload.user_id,auth) or payload.user_id;_check_user(payload.chat_id,payload.user_id,submit=True)
    try:t=stock_transfers.create_transfer(payload.chat_id,payload.user_id,from_area_id=payload.from_area_id,to_area_id=payload.to_area_id,from_department_id=payload.from_department_id,to_department_id=payload.to_department_id,from_location_id=payload.from_location_id,to_location_id=payload.to_location_id,note=payload.note,items=[x.model_dump() for x in payload.items])
    except PermissionError as exc:raise HTTPException(status_code=403,detail=str(exc))
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    return {'message':'Передача создана. Остаток получателя изменится только после приёмки.','transfer':t,'transfers':stock_transfers.list_transfers(payload.chat_id,payload.user_id)}

@app.post("/api/transfers/accept")
def accept_transfer_api(payload:TransferAcceptPayload,x_access_token:Annotated[str|None,Header()]=None,x_telegram_init_data:Annotated[str|None,Header()]=None)->dict[str,object]:
    auth=_check_token(x_access_token,x_telegram_init_data);payload.user_id=_request_user(payload.user_id,auth) or payload.user_id;_check_user(payload.chat_id,payload.user_id,submit=True)
    try:t=stock_transfers.accept_transfer(payload.chat_id,payload.user_id,payload.transfer_id,[x.model_dump() for x in payload.items],payload.note)
    except PermissionError as exc:raise HTTPException(status_code=403,detail=str(exc))
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc))
    return {'message':'Передача принята. Остатки перемещены.','transfer':t,'transfers':stock_transfers.list_transfers(payload.chat_id,payload.user_id)}

@app.post("/api/excel/import/analyze")
async def excel_analyze_api(chat_id:int=Form(...),user_id:int=Form(...),file:UploadFile=File(...),x_access_token:Annotated[str|None,Header()]=None,x_telegram_init_data:Annotated[str|None,Header()]=None)->dict[str,object]:
    auth=_check_token(x_access_token,x_telegram_init_data);uid=_request_user(user_id,auth) or user_id;_check_system_admin(chat_id,uid)
    data=await file.read()
    try:return excel_bridge.analyze_bytes(chat_id,uid,data,file.filename or 'import.xlsx')
    except (ValueError,PermissionError) as exc:raise HTTPException(status_code=400,detail=str(exc))

@app.get("/api/excel/import/preview")
def excel_preview_api(chat_id:int=Query(...),user_id:int|None=Query(None),batch_id:str=Query(...),x_access_token:Annotated[str|None,Header()]=None,x_telegram_init_data:Annotated[str|None,Header()]=None)->dict[str,object]:
    auth=_check_token(x_access_token,x_telegram_init_data);uid=_request_user(user_id,auth)
    if uid is None:raise HTTPException(status_code=403,detail='access denied')
    _check_system_admin(chat_id,uid)
    try:return excel_bridge.get_preview(chat_id,uid,batch_id)
    except Exception as exc:raise HTTPException(status_code=400,detail=str(exc))

@app.post("/api/excel/import/confirm")
def excel_confirm_api(payload:ExcelConfirmPayload,x_access_token:Annotated[str|None,Header()]=None,x_telegram_init_data:Annotated[str|None,Header()]=None)->dict[str,object]:
    auth=_check_token(x_access_token,x_telegram_init_data);payload.user_id=_request_user(payload.user_id,auth) or payload.user_id;_check_system_admin(payload.chat_id,payload.user_id)
    try:return excel_bridge.confirm_import(payload.chat_id,payload.user_id,payload.batch_id,payload.create_missing)
    except Exception as exc:raise HTTPException(status_code=400,detail=str(exc))

@app.post("/api/excel/import/cancel")
def excel_cancel_api(payload:ExcelConfirmPayload,x_access_token:Annotated[str|None,Header()]=None,x_telegram_init_data:Annotated[str|None,Header()]=None)->dict[str,object]:
    auth=_check_token(x_access_token,x_telegram_init_data);payload.user_id=_request_user(payload.user_id,auth) or payload.user_id;_check_system_admin(payload.chat_id,payload.user_id);excel_bridge.cancel_import(payload.chat_id,payload.user_id,payload.batch_id);return {'message':'Импорт отменён.'}

@app.get("/api/reports/location-ledger.xlsx")
def location_ledger_api(chat_id:int=Query(...),user_id:int|None=Query(None),entity_type:str=Query('component'),date_from:str=Query(''),date_to:str=Query(''),x_access_token:Annotated[str|None,Header()]=None,x_telegram_init_data:Annotated[str|None,Header()]=None):
    auth=_check_token(x_access_token,x_telegram_init_data);uid=_request_user(user_id,auth)
    if uid is None:raise HTTPException(status_code=403,detail='access denied')
    _check_user(chat_id,uid)
    data=excel_bridge.build_location_ledger_xlsx(chat_id,uid,entity_type,date_from,date_to)
    return StreamingResponse(io.BytesIO(data),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="uchet_{entity_type}.xlsx"'})
