from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.services import repository as repo
from app.services import telegram_users
from app.services.normalize import normalize_key
from webapp import server

_INSTALLED = False
_ENTITY_TYPES = {"product", "component", "material", "stock_item", "meter"}


class RenameAccountPayload(BaseModel):
    chat_id: int
    user_id: int
    account_id: int
    name: str = Field(min_length=1, max_length=180)


class RenameStorageLocationPayload(BaseModel):
    chat_id: int
    user_id: int
    location_id: int
    name: str = Field(min_length=1, max_length=180)


class ContextPayload(BaseModel):
    chat_id: int
    user_id: int


class CreateAccountPayload(BaseModel):
    user_id: int
    name: str = Field(min_length=1, max_length=180)
    is_general: bool = False


class CreateAreaPayload(BaseModel):
    chat_id: int
    user_id: int
    name: str = Field(min_length=1, max_length=180)


class CreateEntityPayload(BaseModel):
    chat_id: int
    user_id: int
    entity_type: str
    name: str = Field(min_length=1, max_length=180)
    default_unit: str = Field(default="шт", max_length=40)
    aliases: str = Field(default="", max_length=1000)
    area_ids: list[int] = Field(default_factory=list)


class CompositionItemPayload(BaseModel):
    component_id: int
    quantity: float


class ProductCompositionPayload(BaseModel):
    chat_id: int
    user_id: int
    product_id: int
    components: list[CompositionItemPayload] = Field(default_factory=list)


class AssignWorkerPayload(BaseModel):
    chat_id: int
    user_id: int
    worker_ref: str = Field(min_length=1, max_length=160)
    display_name: str = Field(default="", max_length=180)
    job_title_id: int


def _authenticated_user(
    requested_user_id: int,
    x_access_token: str | None,
    x_telegram_init_data: str | None,
) -> int:
    auth_user_id = server._check_token(x_access_token, x_telegram_init_data)
    resolved = server._request_user(requested_user_id, auth_user_id) or requested_user_id
    return int(resolved)


def _managed_scope(chat_id: int, actor_user_id: int) -> int:
    scope = repo.resolve_scope_chat_id(int(chat_id))
    if not repo.user_can_manage_current_context(scope, int(actor_user_id)):
        raise HTTPException(status_code=403, detail="Нет права менять настройки этого учёта.")
    return int(scope)


def _entity_dict(item) -> dict[str, object]:
    result: dict[str, object] = {
        "id": int(item.id),
        "name": str(item.name),
        "entity_type": str(item.entity_type),
        "default_unit": str(item.default_unit or "шт"),
    }
    if item.entity_type == "meter":
        result["area_ids"] = repo.list_meter_area_ids(int(item.id))
    elif item.entity_type == "stock_item":
        result["area_ids"] = repo.list_stock_item_area_ids(int(item.id))
    return result


def _catalog_snapshot(scope: int) -> dict[str, object]:
    areas = [{"id": int(area.id), "name": str(area.name)} for area in repo.list_areas(scope)]
    entities: dict[str, list[dict[str, object]]] = {}
    for entity_type in sorted(_ENTITY_TYPES):
        entities[entity_type] = [_entity_dict(item) for item in repo.list_entities(scope, {entity_type})]

    compositions: dict[str, list[dict[str, object]]] = {}
    for product in repo.list_entities(scope, {"product"}):
        rows = repo.list_product_components(int(product.id))
        compositions[str(product.id)] = [
            {
                "component_id": int(row.get("component_id") or 0),
                "quantity": float(row.get("quantity") or 0),
                "name": str(row.get("name") or ""),
                "unit": str(row.get("default_unit") or "шт"),
            }
            for row in rows
            if int(row.get("component_id") or 0) > 0
        ]

    known_users = []
    for item in telegram_users.list_recent_users(120):
        username = str(item.get("username") or "")
        known_users.append(
            {
                "user_id": int(item.get("user_id") or 0),
                "username": username,
                "display_name": str(item.get("display_name") or ""),
                "label": (
                    f"{item.get('display_name')} (@{username})"
                    if item.get("display_name") and username
                    else (f"@{username}" if username else str(item.get("display_name") or item.get("user_id") or ""))
                ),
            }
        )

    return {
        "areas": areas,
        "entities": entities,
        "compositions": compositions,
        "job_titles": repo.list_job_titles_detailed(scope),
        "workers": repo.list_workers_detailed(scope),
        "known_users": known_users,
    }


def _rename_account(account_id: int, actor_user_id: int, name: str) -> tuple[bool, str, int | None]:
    account = repo.get_account_by_id(int(account_id))
    if not account:
        return False, "Учёт не найден.", None
    if not repo.is_tenant_admin(account.scope_chat_id, int(actor_user_id)):
        return False, "Нет права переименовывать этот учёт.", None
    clean = " ".join(str(name or "").split()).strip()[:180]
    key = normalize_key(clean)
    if not key:
        return False, "Укажите новое название учёта.", None
    conflict = db.fetchone(
        "SELECT id FROM accounting_accounts WHERE owner_user_id=? AND normalized=? AND id<>? AND is_archived=0",
        (int(account.owner_user_id), key, int(account.id)),
    )
    if conflict:
        return False, "Учёт с таким названием уже существует.", None
    try:
        with db.connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE accounting_accounts SET name=?,normalized=? WHERE id=? AND is_archived=0",
                (clean, key, int(account.id)),
            )
            conn.execute(
                "UPDATE chats SET title=? WHERE chat_id=?",
                (f"Учёт: {clean}", int(account.scope_chat_id)),
            )
            conn.commit()
        repo.log_site_action(account.scope_chat_id, int(actor_user_id), "account_rename", clean)
        return True, f"Учёт переименован: {clean}", int(account.scope_chat_id)
    except Exception:
        return False, "Не удалось переименовать учёт.", None


def _rename_storage_location(
    chat_id: int,
    actor_user_id: int,
    location_id: int,
    name: str,
) -> tuple[bool, str]:
    scope = repo.resolve_scope_chat_id(int(chat_id))
    if not repo.is_tenant_admin(scope, int(actor_user_id)):
        return False, "Нет права менять места хранения."
    clean = " ".join(str(name or "").split()).strip()[:180]
    key = normalize_key(clean)
    if not key:
        return False, "Укажите новое название места хранения."
    row = db.fetchone(
        "SELECT id FROM storage_locations WHERE id=? AND chat_id=? AND is_archived=0",
        (int(location_id), int(scope)),
    )
    if not row:
        return False, "Место хранения не найдено."
    conflict = db.fetchone(
        "SELECT id FROM storage_locations WHERE chat_id=? AND normalized=? AND id<>? AND is_archived=0",
        (int(scope), key, int(location_id)),
    )
    if conflict:
        return False, "Место хранения с таким названием уже существует."
    try:
        db.execute(
            "UPDATE storage_locations SET name=?,normalized=? WHERE id=? AND chat_id=? AND is_archived=0",
            (clean, key, int(location_id), int(scope)),
        )
        try:
            repo.tenant_audit(scope, int(actor_user_id), "storage_location_rename", "storage_location", str(location_id), clean)
        except Exception:
            repo.log_site_action(scope, int(actor_user_id), "storage_location_rename", clean)
        return True, f"Место хранения переименовано: {clean}"
    except Exception:
        return False, "Не удалось переименовать место хранения."


def rename_account_api(
    payload: RenameAccountPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    ok, message, scope_chat_id = _rename_account(payload.account_id, user_id, payload.name)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"message": message, "scope_chat_id": scope_chat_id}


def rename_storage_location_api(
    payload: RenameStorageLocationPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    ok, message = _rename_storage_location(payload.chat_id, user_id, payload.location_id, payload.name)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {
        "message": message,
        "storage_locations": repo.list_storage_locations(payload.chat_id),
    }


def catalog_snapshot_api(
    payload: ContextPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    scope = _managed_scope(payload.chat_id, user_id)
    return _catalog_snapshot(scope)


def create_account_api(
    payload: CreateAccountPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    clean = " ".join(payload.name.split()).strip()
    repo.upsert_chat(user_id, "Личный чат", "private", connected=True)
    ok, message, account_id = repo.create_account(user_id, user_id, clean, payload.is_general)
    if not ok or not account_id:
        raise HTTPException(status_code=400, detail=message)
    account = repo.get_account_by_id(int(account_id))
    repo.log_site_action(account.scope_chat_id if account else user_id, user_id, "account_create_miniapp", clean)
    return {
        "message": message,
        "account_id": int(account_id),
        "scope_chat_id": int(account.scope_chat_id) if account else None,
    }


def create_area_api(
    payload: CreateAreaPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    scope = _managed_scope(payload.chat_id, user_id)
    ok, message = repo.create_area(scope, payload.name)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, user_id, "area_create_miniapp", payload.name)
    return {"message": message, **_catalog_snapshot(scope)}


def create_entity_api(
    payload: CreateEntityPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    scope = _managed_scope(payload.chat_id, user_id)
    entity_type = str(payload.entity_type or "").strip().lower()
    if entity_type not in _ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="Неизвестный тип позиции.")
    unit = " ".join(str(payload.default_unit or "шт").split()).strip()[:40] or "шт"
    ok, message = repo.create_entity(scope, entity_type, payload.name, unit)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    entity = repo.get_entity_by_name(scope, entity_type, payload.name)
    if not entity:
        raise HTTPException(status_code=500, detail="Позиция создана, но не найдена для дальнейшей настройки.")
    if payload.aliases.strip():
        repo.add_aliases(scope, entity_type, int(entity.id), payload.aliases, source="miniapp")
    area_ids = sorted({int(x) for x in payload.area_ids if int(x) > 0})
    if entity_type == "meter":
        repo.bind_meter_to_areas(scope, int(entity.id), area_ids)
    elif entity_type == "stock_item":
        repo.bind_stock_item_to_areas(scope, int(entity.id), area_ids)
    repo.log_site_action(scope, user_id, "entity_create_miniapp", f"{entity_type}:{entity.id}:{entity.name}")
    return {"message": message, **_catalog_snapshot(scope)}


def save_composition_api(
    payload: ProductCompositionPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    scope = _managed_scope(payload.chat_id, user_id)
    product = repo.get_entity(int(payload.product_id))
    if not product or int(product.chat_id) != int(scope) or product.entity_type != "product":
        raise HTTPException(status_code=404, detail="Изделие не найдено в этом учёте.")
    selected: list[tuple[int, float]] = []
    seen: set[int] = set()
    for item in payload.components:
        component_id = int(item.component_id)
        quantity = float(item.quantity)
        if component_id in seen:
            continue
        component = repo.get_entity(component_id)
        if not component or int(component.chat_id) != int(scope) or component.entity_type != "component":
            raise HTTPException(status_code=400, detail="В составе есть неизвестная комплектующая.")
        if quantity <= 0:
            raise HTTPException(status_code=400, detail=f"Количество для «{component.name}» должно быть больше нуля.")
        selected.append((component_id, quantity))
        seen.add(component_id)
    repo.set_product_components(scope, int(product.id), selected)
    repo.log_site_action(scope, user_id, "product_composition_save_miniapp", f"{product.id}:{len(selected)}")
    return {"message": f"Состав «{product.name}» сохранён.", **_catalog_snapshot(scope)}


def assign_worker_api(
    payload: AssignWorkerPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    scope = _managed_scope(payload.chat_id, user_id)
    resolved = telegram_users.resolve_user_ref(payload.worker_ref)
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail="Пользователь с таким @username пока не найден. Попросите сотрудника один раз написать в рабочий чат или укажите его Telegram ID.",
        )
    worker_user_id = int(resolved.get("user_id") or 0)
    if worker_user_id <= 0:
        raise HTTPException(status_code=400, detail="Не удалось определить сотрудника.")
    username = str(resolved.get("username") or "")
    remembered_name = str(resolved.get("display_name") or "").strip()
    entered_name = " ".join(str(payload.display_name or "").split()).strip()
    if entered_name:
        display_name = entered_name
    elif remembered_name and username:
        display_name = f"{remembered_name} (@{username})"
    elif remembered_name:
        display_name = remembered_name
    elif username:
        display_name = f"@{username}"
    else:
        display_name = str(worker_user_id)
    ok, message = repo.save_worker_record(scope, worker_user_id, display_name, int(payload.job_title_id))
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, user_id, "worker_assign_miniapp", f"{worker_user_id}:{payload.job_title_id}")
    return {
        "message": message,
        "resolved_user": {
            "user_id": worker_user_id,
            "username": username,
            "display_name": display_name,
        },
        "workers": repo.list_workers_detailed(scope),
        "job_titles": repo.list_job_titles_detailed(scope),
    }


def extensions_health() -> dict[str, object]:
    return {"ok": True, "naming": True, "catalog": True, "username_assignment": True}


def install_extensions() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    existing = {getattr(route, "path", "") for route in server.app.routes}
    routes = [
        ("/api/extensions/account/rename", rename_account_api, ["POST"]),
        ("/api/extensions/storage-location/rename", rename_storage_location_api, ["POST"]),
        ("/api/extensions/catalog/snapshot", catalog_snapshot_api, ["POST"]),
        ("/api/extensions/accounts/create", create_account_api, ["POST"]),
        ("/api/extensions/catalog/area", create_area_api, ["POST"]),
        ("/api/extensions/catalog/entity", create_entity_api, ["POST"]),
        ("/api/extensions/catalog/composition", save_composition_api, ["POST"]),
        ("/api/extensions/workers/assign", assign_worker_api, ["POST"]),
        ("/api/extensions/health", extensions_health, ["GET"]),
    ]
    for path, endpoint, methods in routes:
        if path not in existing:
            server.app.add_api_route(path, endpoint, methods=methods, response_model=None)
    _INSTALLED = True
