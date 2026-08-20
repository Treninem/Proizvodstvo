from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.services import repository as repo
from app.services.normalize import normalize_key
from webapp import server

_INSTALLED = False


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


def _authenticated_user(
    requested_user_id: int,
    x_access_token: str | None,
    x_telegram_init_data: str | None,
) -> int:
    auth_user_id = server._check_token(x_access_token, x_telegram_init_data)
    resolved = server._request_user(requested_user_id, auth_user_id) or requested_user_id
    return int(resolved)


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


def extensions_health() -> dict[str, object]:
    return {"ok": True, "naming": True}


def install_extensions() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    existing = {getattr(route, "path", "") for route in server.app.routes}
    if "/api/extensions/account/rename" not in existing:
        server.app.add_api_route(
            "/api/extensions/account/rename",
            rename_account_api,
            methods=["POST"],
            response_model=None,
        )
    if "/api/extensions/storage-location/rename" not in existing:
        server.app.add_api_route(
            "/api/extensions/storage-location/rename",
            rename_storage_location_api,
            methods=["POST"],
            response_model=None,
        )
    if "/api/extensions/health" not in existing:
        server.app.add_api_route(
            "/api/extensions/health",
            extensions_health,
            methods=["GET"],
            response_model=None,
        )
    _INSTALLED = True
