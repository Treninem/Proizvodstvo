from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, Query
from pydantic import BaseModel, Field

from app import db
from app.services import measurement_units
from app.services import repository as repo
from app.services import telegram_users
from app.services.normalize import normalize_key, split_aliases
from webapp import server

_INSTALLED = False
_ENTITY_TYPES = {"product", "component", "material", "stock_item", "meter"}


class TreeContextPayload(BaseModel):
    chat_id: int
    user_id: int


class UnitPayload(TreeContextPayload):
    name: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=1, max_length=40)


class BatchEntityItem(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    aliases: str = Field(default="", max_length=1000)
    unit: str = Field(min_length=1, max_length=40)
    area_ids: list[int] = Field(default_factory=list)


class BatchEntitiesPayload(TreeContextPayload):
    entity_type: str
    items: list[BatchEntityItem] = Field(min_length=1, max_length=100)


class AssignWorkerPayload(TreeContextPayload):
    worker_ref: str = Field(min_length=1, max_length=160)
    display_name: str = Field(default="", max_length=180)
    job_title_id: int


class CompositionItem(BaseModel):
    component_id: int
    quantity: float


class CompositionPayload(TreeContextPayload):
    product_id: int
    components: list[CompositionItem] = Field(default_factory=list)


def _authenticated_user(
    requested_user_id: int,
    x_access_token: str | None,
    x_telegram_init_data: str | None,
) -> int:
    auth_user_id = server._check_token(x_access_token, x_telegram_init_data)
    resolved = server._request_user(requested_user_id, auth_user_id) or requested_user_id
    return int(resolved)


def _read_scope(chat_id: int, user_id: int) -> int:
    server._check_user(int(chat_id), int(user_id))
    return int(repo.resolve_scope_chat_id(int(chat_id)))


def _managed_scope(chat_id: int, user_id: int) -> int:
    scope = _read_scope(chat_id, user_id)
    if not repo.user_can_manage_current_context(scope, int(user_id)):
        raise HTTPException(status_code=403, detail="Этот раздел доступен только управляющему учётом.")
    return scope


def _entity_snapshot(scope: int) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for entity_type in sorted(_ENTITY_TYPES):
        rows = []
        for item in repo.list_entities(scope, {entity_type}):
            rows.append(
                {
                    "id": int(item.id),
                    "name": str(item.name),
                    "entity_type": str(item.entity_type),
                    "default_unit": str(item.default_unit or "шт"),
                }
            )
        result[entity_type] = rows
    return result


def _composition_snapshot(scope: int) -> dict[str, list[dict[str, object]]]:
    out: dict[str, list[dict[str, object]]] = {}
    for product in repo.list_entities(scope, {"product"}):
        out[str(product.id)] = [
            {
                "component_id": int(row.get("component_id") or 0),
                "quantity": float(row.get("quantity") or 0),
                "name": str(row.get("name") or ""),
                "unit": str(row.get("default_unit") or "шт"),
            }
            for row in repo.list_product_components(int(product.id))
            if int(row.get("component_id") or 0) > 0
        ]
    return out


def _known_users() -> list[dict[str, object]]:
    users: list[dict[str, object]] = []
    for item in telegram_users.list_recent_users(200):
        username = str(item.get("username") or "")
        display_name = str(item.get("display_name") or "")
        user_id = int(item.get("user_id") or 0)
        users.append(
            {
                "user_id": user_id,
                "username": username,
                "display_name": display_name,
                "label": (
                    f"{display_name} (@{username})"
                    if display_name and username
                    else (f"@{username}" if username else (display_name or str(user_id)))
                ),
            }
        )
    return users


def _snapshot(scope: int, user_id: int) -> dict[str, object]:
    permissions = repo.user_permissions_current_context(scope, user_id)
    return {
        "access": {
            "is_primary_owner": bool(repo.is_primary_owner_id(user_id)),
            "is_tenant_admin": bool(repo.is_tenant_admin(scope, user_id)),
            "can_manage": bool(repo.user_can_manage_current_context(scope, user_id)),
            "can_manage_departments": bool(repo.user_can_manage_departments(scope, user_id)),
            "permissions": permissions,
        },
        "units": measurement_units.list_units(scope, user_id),
        "areas": [{"id": int(area.id), "name": str(area.name)} for area in repo.list_areas(scope)],
        "entities": _entity_snapshot(scope),
        "compositions": _composition_snapshot(scope),
        "job_titles": repo.list_job_titles_detailed(scope),
        "workers": repo.list_workers_detailed(scope),
        "known_users": _known_users(),
    }


def tree_snapshot_api(
    payload: TreeContextPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    scope = _read_scope(payload.chat_id, user_id)
    return _snapshot(scope, user_id)


def create_unit_api(
    payload: UnitPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    scope = _managed_scope(payload.chat_id, user_id)
    ok, message, _unit_id = measurement_units.create_unit(scope, payload.name, payload.symbol, user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, user_id, "measurement_unit_create", payload.symbol)
    return {"message": message, "units": measurement_units.list_units(scope, user_id)}


def archive_unit_api(
    chat_id: int = Query(...),
    user_id: int = Query(...),
    unit_id: int = Query(...),
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    actor = _authenticated_user(user_id, x_access_token, x_telegram_init_data)
    scope = _managed_scope(chat_id, actor)
    ok, message = measurement_units.archive_unit(scope, unit_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, actor, "measurement_unit_archive", str(unit_id))
    return {"message": message, "units": measurement_units.list_units(scope, actor)}


def _validate_batch(scope: int, entity_type: str, items: list[BatchEntityItem]) -> list[dict[str, object]]:
    if entity_type not in _ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="Неизвестный вид позиции.")
    prepared: list[dict[str, object]] = []
    batch_keys: set[str] = set()
    valid_area_ids = {int(area.id) for area in repo.list_areas(scope)}
    for index, item in enumerate(items, start=1):
        name = " ".join(str(item.name or "").split()).strip()[:180]
        key = normalize_key(name)
        unit = " ".join(str(item.unit or "").split()).strip()[:40]
        if not key:
            raise HTTPException(status_code=400, detail=f"Позиция {index}: укажите название.")
        if key in batch_keys:
            raise HTTPException(status_code=400, detail=f"Позиция {index}: название повторяется в этом списке.")
        batch_keys.add(key)
        if db.fetchone(
            "SELECT id FROM entities WHERE chat_id=? AND entity_type=? AND normalized=? AND is_archived=0",
            (scope, entity_type, key),
        ):
            raise HTTPException(status_code=400, detail=f"Позиция «{name}» уже существует.")
        if not measurement_units.unit_exists(scope, unit):
            raise HTTPException(status_code=400, detail=f"Для «{name}» выберите единицу из справочника.")
        area_ids = sorted({int(value) for value in item.area_ids if int(value) in valid_area_ids})
        prepared.append(
            {
                "name": name,
                "normalized": key,
                "unit": unit,
                "aliases": list(split_aliases(item.aliases)),
                "area_ids": area_ids,
            }
        )
    return prepared


def create_entities_batch_api(
    payload: BatchEntitiesPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    scope = _managed_scope(payload.chat_id, user_id)
    entity_type = str(payload.entity_type or "").strip().lower()
    prepared = _validate_batch(scope, entity_type, payload.items)
    created_ids: list[int] = []
    try:
        with db.connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            for item in prepared:
                cur = conn.execute(
                    """
                    INSERT INTO entities(chat_id,entity_type,name,normalized,default_unit)
                    VALUES(?,?,?,?,?)
                    """,
                    (scope, entity_type, item["name"], item["normalized"], item["unit"]),
                )
                entity_id = int(cur.lastrowid)
                created_ids.append(entity_id)
                for alias in item["aliases"]:
                    alias_key = normalize_key(alias)
                    if alias_key:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO aliases(chat_id,target_type,target_id,alias,normalized,source)
                            VALUES(?,?,?,?,?,'miniapp-tree')
                            """,
                            (scope, entity_type, entity_id, alias, alias_key),
                        )
                if entity_type == "meter":
                    for area_id in item["area_ids"]:
                        conn.execute(
                            "INSERT OR IGNORE INTO meter_area_bindings(meter_id,area_id) VALUES(?,?)",
                            (entity_id, area_id),
                        )
                elif entity_type == "stock_item":
                    for area_id in item["area_ids"]:
                        conn.execute(
                            "INSERT OR IGNORE INTO stock_item_area_bindings(stock_item_id,area_id) VALUES(?,?)",
                            (entity_id, area_id),
                        )
            conn.commit()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Не удалось сохранить список позиций. Проверьте данные и повторите.") from exc
    repo.log_site_action(scope, user_id, "entity_batch_create", f"{entity_type}:{len(created_ids)}")
    return {
        "message": f"Сохранено позиций: {len(created_ids)}.",
        "created_ids": created_ids,
        **_snapshot(scope, user_id),
    }


def assign_worker_api(
    payload: AssignWorkerPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    scope = _managed_scope(payload.chat_id, user_id)
    job = next(
        (item for item in repo.list_job_titles(scope) if int(item.get("id") or 0) == int(payload.job_title_id)),
        None,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Выбранная должность не найдена.")
    target = telegram_users.resolve_user_ref(payload.worker_ref)
    if not target:
        raise HTTPException(
            status_code=404,
            detail="Пользователь по @username пока не найден. Пусть он один раз напишет боту или в рабочую группу, либо укажите Telegram ID.",
        )
    target_id = int(target.get("user_id") or 0)
    display_name = " ".join(str(payload.display_name or target.get("display_name") or "").split()).strip()[:180]
    ok, message = repo.save_worker_record(scope, target_id, display_name, int(payload.job_title_id))
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    repo.log_site_action(scope, user_id, "worker_assign_tree", f"{target_id}:{payload.job_title_id}")
    return {"message": message, **_snapshot(scope, user_id)}


def save_composition_api(
    payload: CompositionPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    user_id = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    scope = _managed_scope(payload.chat_id, user_id)
    product = repo.get_entity(int(payload.product_id))
    if not product or int(product.chat_id) != scope or product.entity_type != "product":
        raise HTTPException(status_code=404, detail="Изделие не найдено.")
    selected: list[tuple[int, float]] = []
    seen: set[int] = set()
    for item in payload.components:
        component_id = int(item.component_id)
        if component_id in seen:
            continue
        component = repo.get_entity(component_id)
        if not component or int(component.chat_id) != scope or component.entity_type != "component":
            raise HTTPException(status_code=400, detail="В составе есть неизвестная комплектующая.")
        quantity = float(item.quantity)
        if quantity <= 0:
            raise HTTPException(status_code=400, detail=f"Для «{component.name}» укажите количество больше нуля.")
        selected.append((component_id, quantity))
        seen.add(component_id)
    repo.set_product_components(scope, int(product.id), selected)
    repo.log_site_action(scope, user_id, "composition_save_tree", f"{product.id}:{len(selected)}")
    return {"message": f"Состав «{product.name}» сохранён.", **_snapshot(scope, user_id)}


def tree_health() -> dict[str, object]:
    return {"ok": True, "tree_menu": True, "unit_catalog": True, "batch_positions": True, "owner_privacy": True}


def install_tree_extensions() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    measurement_units.ensure_schema()
    existing = {getattr(route, "path", "") for route in server.app.routes}
    routes = (
        ("/api/tree/snapshot", tree_snapshot_api, ["POST"]),
        ("/api/tree/units", create_unit_api, ["POST"]),
        ("/api/tree/units", archive_unit_api, ["DELETE"]),
        ("/api/tree/entities/batch", create_entities_batch_api, ["POST"]),
        ("/api/tree/worker/assign", assign_worker_api, ["POST"]),
        ("/api/tree/composition", save_composition_api, ["POST"]),
        ("/api/tree/health", tree_health, ["GET"]),
    )
    for path, endpoint, methods in routes:
        if path in existing and methods != ["DELETE"]:
            continue
        server.app.add_api_route(path, endpoint, methods=methods, response_model=None)
    _INSTALLED = True
