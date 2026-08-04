from __future__ import annotations

import base64
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
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

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"

app = FastAPI(title="Производственный учёт — Mini App", docs_url=None, redoc_url=None)
if settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Access-Token", "X-Telegram-Init-Data"],
    )
if settings.trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))
app.mount("/static", StaticFiles(directory=STATIC), name="static")



@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; connect-src 'self' https:; frame-ancestors 'self' https:; base-uri 'self'; form-action 'self'",
    )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response




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












def _check_token(token: str | None, init_data: str | None = None) -> int | None:
    expected = settings.miniapp_api_token
    if expected and token == expected:
        # Служебный ключ интеграции. Пользователь всё равно проверяется отдельно.
        return None
    user = validate_telegram_init_data(init_data or "", settings.bot_token)
    if user.get("id"):
        return int(user["id"])
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
    if repo.is_global_owner_id(user_id):
        return account
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
    if not repo.is_system_admin_id(user_id):
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
    if repo.is_system_admin_id(user_id):
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
    if repo.user_has_department_membership(chat_id, user_id) and not repo.is_system_admin_id(user_id):
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


def _risk_for_user(chat_id: int, user_id: int) -> dict[str, object]:
    return stock_risk.dashboard_for_user(chat_id, user_id)


@app.on_event("startup")
def _startup() -> None:
    init_db()












@app.get("/mini")
def mini() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "bot_enabled": settings.bot_enabled,
        "miniapp_enabled": settings.miniapp_enabled,
    }


@app.get("/ready")
def ready() -> dict[str, object]:
    row = db.fetchone("SELECT 1 AS ok")
    return {"status": "ready", "database": bool(row), "mini_app": True}


















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
    return {"accounts": [{"id": a.id, "name": a.name, "scope_chat_id": a.scope_chat_id, "is_general": a.is_general} for a in items]}


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
    is_system_admin = repo.is_system_admin_id(user_id)
    has_departments = repo.user_has_department_membership(scope, user_id)
    repo.log_site_action(scope, user_id, "bootstrap")
    return {
        "account": {"id": account.id, "name": account.name} if account else None,
        "scope_chat_id": scope,
        "permissions": permissions,
        "area_access": area_access,
        "can_manage": can_manage,
        "can_manage_departments": can_manage_departments,
        "is_system_admin": is_system_admin,
        "department_memberships": repo.user_department_memberships(scope, user_id),
        "work_access": repo.department_work_access_for_user(scope, user_id),
        "departments": repo.list_departments(scope, None if is_system_admin else user_id, manageable_only=not is_system_admin) if can_manage_departments else [],
        "areas": _area_list(scope),
        "entities": _entity_list(scope, user_id),
        "destinations": repo.list_destinations(scope) if (is_system_admin or not has_departments) else [],
        "job_titles": repo.list_job_titles_detailed(scope) if can_manage else [],
        "workers": repo.list_workers_detailed(scope) if can_manage else [],
        "area_access_rules": repo.list_area_section_access(scope) if can_manage else [],
        "inventory_positions": _inventory_positions_for_user(scope, user_id, _inventory_area_ids(scope, user_id)) if (permissions.get("stock") or is_system_admin) else [],
        "inventory_sessions": repo.list_inventory_sessions(scope, area_ids=_inventory_area_ids(scope, user_id)) if ((permissions.get("stock") and not has_departments) or is_system_admin) else [],
        "report_presets": _report_presets_for_user(scope, user_id) if (permissions.get("reports") or is_system_admin) else [],
        "report_schedules": repo.list_report_schedules(scope, user_id),
        "report_delivery_history": repo.list_report_delivery_history(scope, user_id),
        "inbox_items": repo.list_inbox_items(scope, user_id),
        "worker_activity": repo.worker_activity_analytics(scope, 30, None if can_manage else user_id),
        "worker_shifts": repo.list_worker_shifts(scope, None if can_manage else user_id, limit=80),
        "shift_plans": repo.list_shift_plans(scope, None if can_manage else user_id),
        "shift_templates": repo.list_shift_templates(scope, None if can_manage else user_id),
        "shift_calendar": repo.shift_calendar(scope, str(date.today() - timedelta(days=7)), str(date.today() + timedelta(days=45)), None if can_manage else user_id),
        "attendance_deviations": repo.attendance_deviations(scope, None if can_manage else user_id, 30),
        "attendance_summary": repo.attendance_summary(scope, str(date.today() - timedelta(days=29)), str(date.today()), None if can_manage else user_id),
        "notification_preferences": repo.get_notification_preferences(scope, user_id),
        "stock_risk": _risk_for_user(scope, user_id),
        "plan_targets": repo.list_assembly_plan_targets(scope) if ((permissions.get("assembly") and not has_departments) or is_system_admin) else [],
        "dashboard": _dashboard_for_user(scope, user_id),
    }


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
    if not repo.is_system_admin_id(payload.user_id) and not repo.user_has_department_membership(scope, payload.user_id):
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


@app.post("/api/operations")
def create_operation(
    payload: OperationPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    auth_user_id = _check_token(x_access_token, x_telegram_init_data)
    payload.user_id = _request_user(payload.user_id, auth_user_id) or payload.user_id
    account = _check_user(payload.chat_id, payload.user_id, submit=True)
    scope = repo.resolve_scope_chat_id(payload.chat_id)
    _check_operation_permission(
        scope,
        payload.user_id,
        payload.operation_type,
        area_id=payload.area_id,
        from_area_id=payload.from_area_id,
        to_area_id=payload.to_area_id,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
    )
    entity = repo.get_entity(payload.entity_id)
    if not entity or entity.chat_id != scope or entity.entity_type != payload.entity_type:
        raise HTTPException(status_code=400, detail="bad entity")
    if payload.quantity <= 0:
        raise HTTPException(status_code=400, detail="bad quantity")
    if payload.operation_type == "movement" and payload.from_area_id and payload.to_area_id and payload.from_area_id == payload.to_area_id:
        raise HTTPException(status_code=400, detail="bad movement")
    if payload.operation_type not in {"production", "material_in", "material_out", "energy", "assembly", "movement", "transfer_to_assembly", "shipment", "shipment_client", "shipment_fulfillment", "return", "stock_in", "stock_out", "write_off", "inventory_adjust"}:
        raise HTTPException(status_code=400, detail="bad operation")
    if entity.entity_type not in _allowed_entity_types(payload.operation_type):
        raise HTTPException(status_code=400, detail="bad entity")
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
    }
    saved = accounting.apply_operations(scope, payload.chat_id, payload.user_id, [op], raw_text=payload.note or "mini app")
    repo.log_site_action(scope, payload.user_id, "operation", payload.operation_type)
    repo.log_sync_event(scope, "mini", "saved", payload.operation_type)
    return {
        "saved": saved,
        "account": account.name if account else "",
        "dashboard": _dashboard_for_user(scope, payload.user_id),
    }


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
    if repo.user_has_department_membership(scope, user_id) and not repo.is_system_admin_id(user_id):
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
    if not repo.is_system_admin_id(user_id):
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
        "can_download_backup": bool(permissions.get("setup") or permissions.get("export") or repo.is_global_owner_id(user_id)),
        "can_restore_backup": bool(repo.is_global_owner_id(user_id) or (repo.get_account_by_scope(scope) and int(repo.get_account_by_scope(scope).owner_user_id) == int(user_id))),
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
    if not (repo.is_global_owner_id(payload.user_id) or (account and int(account.owner_user_id) == int(payload.user_id))):
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
    if repo.user_has_department_membership(scope, payload.user_id) and not repo.is_system_admin_id(payload.user_id):
        raise HTTPException(status_code=403, detail="department reports are not available")
    if not (permissions.get("reports") or permissions.get("export") or permissions.get("setup")) and not repo.is_global_owner_id(payload.user_id):
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
    return {"message": message, "departments": repo.list_departments(scope, None if repo.is_system_admin_id(payload.user_id) else payload.user_id)}


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
    return {"message": message, "departments": repo.list_departments(scope, None if repo.is_system_admin_id(user_id) else user_id)}


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
    if not (permissions.get("stock") or permissions.get("edit") or permissions.get("setup") or permissions.get("reports")) and not repo.is_global_owner_id(user_id):
        raise HTTPException(status_code=403, detail="access denied")
    if area_id is not None:
        area = repo.get_area(area_id)
        if not area or area.chat_id != scope:
            raise HTTPException(status_code=400, detail="bad area")
        if not repo.user_area_action_allowed(scope, user_id, "inventory", area_id, "view"):
            raise HTTPException(status_code=403, detail="area access denied")
    elif repo.area_section_access_for_user(scope, user_id, "inventory").get("restricted"):
        raise HTTPException(status_code=400, detail="area required")
    if repo.user_has_department_membership(scope, user_id) and not repo.is_system_admin_id(user_id):
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
    if not repo.is_global_owner_id(payload.user_id) and not (permissions.get("setup") or (permissions.get("stock") and permissions.get("edit"))):
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
    if not (permissions.get("reports") or permissions.get("export") or permissions.get("setup")) and not repo.is_global_owner_id(payload.user_id):
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
    if repo.is_global_owner_id(user_id):
        return
    permissions = repo.user_permissions_current_context(scope, user_id)
    if not permissions.get("stock"):
        raise HTTPException(status_code=403, detail="access denied")
    if not repo.user_area_action_allowed(scope, user_id, "inventory", area_id, action):
        raise HTTPException(status_code=403, detail="area access denied")


def _deny_department_inventory_session_access(scope: int, user_id: int) -> None:
    # Массовый пересчёт содержит данные всего участка. Сотрудники отделов
    # работают только через разрешённые им позиции и не получают этот API.
    if repo.user_has_department_membership(scope, user_id) and not repo.is_system_admin_id(user_id):
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
    if not repo.is_global_owner_id(user_id) and not (permissions.get("stock") or permissions.get("reports") or permissions.get("setup")):
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
        can_approve = repo.is_global_owner_id(payload.user_id) or repo.user_can_manage_current_context(scope, payload.user_id) or (permissions.get("stock") and permissions.get("edit"))
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
    if target != int(payload.user_id) and not repo.user_can_manage_current_context(scope, payload.user_id):
        raise HTTPException(status_code=403, detail="access denied")
    if payload.area_id is not None and not repo.user_area_action_allowed(scope, target, "overview", payload.area_id, "view") and not repo.is_global_owner_id(payload.user_id):
        raise HTTPException(status_code=403, detail="area access denied")
    ok, message, _shift_id = repo.start_worker_shift(scope, target, payload.area_id, payload.user_id, payload.note)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, "shift_start", str(target))
    can_manage = repo.user_can_manage_current_context(scope, payload.user_id)
    return {"message": message, "activity": repo.worker_activity_analytics(scope, 30, None if can_manage else payload.user_id), "shifts": repo.list_worker_shifts(scope, None if can_manage else payload.user_id), "shift_plans": repo.list_shift_plans(scope, None if can_manage else payload.user_id), "attendance_deviations": repo.attendance_deviations(scope, None if can_manage else payload.user_id, 30)}


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
    if target != int(payload.user_id) and not repo.user_can_manage_current_context(scope, payload.user_id):
        raise HTTPException(status_code=403, detail="access denied")
    ok, message = repo.end_worker_shift(scope, target, payload.user_id, payload.note)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, payload.user_id, "shift_end", str(target))
    can_manage = repo.user_can_manage_current_context(scope, payload.user_id)
    return {"message": message, "activity": repo.worker_activity_analytics(scope, 30, None if can_manage else payload.user_id), "shifts": repo.list_worker_shifts(scope, None if can_manage else payload.user_id), "shift_plans": repo.list_shift_plans(scope, None if can_manage else payload.user_id), "attendance_deviations": repo.attendance_deviations(scope, None if can_manage else payload.user_id, 30)}


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
    if payload.area_id is not None and not repo.user_area_action_allowed(scope, payload.worker_user_id, "overview", payload.area_id, "view") and not repo.is_global_owner_id(payload.user_id):
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
    if payload.area_id is not None and not repo.user_area_action_allowed(scope, payload.worker_user_id, "overview", payload.area_id, "view") and not repo.is_global_owner_id(payload.user_id):
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
    if not repo.is_global_owner_id(payload.user_id) and not (permissions.get("reports") or permissions.get("export") or permissions.get("setup")):
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
