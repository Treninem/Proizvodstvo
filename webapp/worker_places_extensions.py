from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from app.services import repository as repo
from app.services import telegram_users, worker_places
from webapp import server
from webapp.tree_extensions import _snapshot


_INSTALLED = False


class ContextPayload(BaseModel):
    chat_id: int
    user_id: int


class AssignWorkerPlacesPayload(ContextPayload):
    worker_ref: str = Field(min_length=1, max_length=160)
    display_name: str = Field(default="", max_length=180)
    job_title_id: int
    workplace_keys: list[str] = Field(min_length=1, max_length=60)


def _authenticated_user(
    requested_user_id: int,
    x_access_token: str | None,
    x_telegram_init_data: str | None,
) -> int:
    auth_user_id = server._check_token(x_access_token, x_telegram_init_data)
    resolved = server._request_user(requested_user_id, auth_user_id) or requested_user_id
    return int(resolved)


def _managed_scope(chat_id: int, user_id: int) -> int:
    server._check_user(int(chat_id), int(user_id))
    scope = int(repo.resolve_scope_chat_id(int(chat_id)))
    if not repo.user_can_manage_current_context(scope, int(user_id)):
        raise HTTPException(status_code=403, detail="Нет права назначать сотрудников в этом учёте.")
    return scope


def workplaces_api(
    payload: ContextPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    actor = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    scope = _managed_scope(payload.chat_id, actor)
    return {"workplaces": worker_places.list_available_workplaces(scope)}


def assign_worker_places_api(
    payload: AssignWorkerPlacesPayload,
    x_access_token: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    actor = _authenticated_user(payload.user_id, x_access_token, x_telegram_init_data)
    scope = _managed_scope(payload.chat_id, actor)
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
            detail=(
                "Пользователь по @username пока не найден. Пусть он один раз напишет боту "
                "или в рабочую группу, либо укажите Telegram ID."
            ),
        )
    target_id = int(target.get("user_id") or 0)
    if target_id <= 0:
        raise HTTPException(status_code=400, detail="Не удалось определить Telegram ID сотрудника.")
    display_name = " ".join(
        str(payload.display_name or target.get("display_name") or target_id).split()
    ).strip()[:180]

    # Validate all physical places before changing the job title. This keeps the
    # assignment atomic from the user's point of view if a stale place was chosen.
    available = worker_places.available_workplace_map(scope)
    selected = []
    seen: set[str] = set()
    for raw in payload.workplace_keys:
        key = str(raw or "").strip()
        if not key or key in seen:
            continue
        if key not in available:
            raise HTTPException(status_code=400, detail="Одно из выбранных рабочих мест больше недоступно.")
        selected.append(key)
        seen.add(key)
    if not selected:
        raise HTTPException(status_code=400, detail="Выберите хотя бы одно рабочее место.")

    repo.set_worker_job(scope, target_id, display_name, int(payload.job_title_id))
    ok, message = worker_places.set_worker_workplaces(scope, target_id, selected, actor)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    labels = [str(item.get("label") or "Рабочее место") for item in worker_places.list_worker_workplaces(scope, target_id)]
    return {
        "message": (
            f"{display_name}: {job.get('name')}. Рабочие места: " + ", ".join(labels)
        ),
        **_snapshot(scope, actor),
    }


def install_worker_places_extensions() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    existing = {getattr(route, "path", "") for route in server.app.routes}
    if "/api/step92/workplaces" not in existing:
        server.app.add_api_route(
            "/api/step92/workplaces",
            workplaces_api,
            methods=["POST"],
            include_in_schema=False,
        )
    if "/api/step92/worker/assign" not in existing:
        server.app.add_api_route(
            "/api/step92/worker/assign",
            assign_worker_places_api,
            methods=["POST"],
            include_in_schema=False,
        )
    _INSTALLED = True
