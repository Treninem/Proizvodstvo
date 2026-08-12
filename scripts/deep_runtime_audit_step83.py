from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

# This audit must never touch the production SQLite file. Override the data dir
# before importing application settings so every run gets an isolated database.
_AUDIT_TMP = tempfile.TemporaryDirectory(prefix="proizvodstvo_deep_audit_")
os.environ["BOT_DATA_DIR"] = _AUDIT_TMP.name
os.environ.setdefault("OWNER_TELEGRAM_ID", "2097006037")
os.environ.setdefault("PUBLIC_BASE_URL", "https://example.invalid")
os.environ.setdefault("MINIAPP_API_TOKEN", "ci-only-miniapp-token-not-a-secret")

from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.services import repository as repo
from webapp import server

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "webapp" / "static"


def _sample_parameter(name: str, schema: dict[str, object], ids: dict[str, int]) -> object:
    if name in ids:
        return ids[name]
    low = name.lower()
    if low.endswith("_id") or low == "id":
        return 1
    if "date" in low:
        return "2026-08-12"
    if low == "from":
        return "2026-08-01"
    if low == "to":
        return "2026-08-12"
    if "status" in low:
        return "open"
    if "type" in low:
        return "component"
    if "limit" in low:
        return 10
    kind = str(schema.get("type") or "")
    if kind == "integer":
        return 1
    if kind == "number":
        return 1
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    return "audit"


def audit_get_api_runtime() -> None:
    db.init_db()
    owner = int(settings.primary_owner_id or 2097006037)
    repo.upsert_chat(owner, "Deep audit owner", "private", connected=True)
    ok, message, account_id = repo.create_account(owner, owner, "DEEP API AUDIT")
    if not ok:
        raise AssertionError(message)
    account = repo.get_account_by_id(account_id)
    if account is None:
        raise AssertionError("audit account was not created")
    scope = int(account.scope_chat_id)

    repo.create_area(scope, "API Area")
    for entity_type, name, unit in (
        ("component", "API Component", "шт"),
        ("material", "API Material", "кг"),
        ("stock_item", "API Stock", "шт"),
        ("product", "API Product", "шт"),
        ("meter", "API Meter", "кВт·ч"),
    ):
        created, _ = repo.create_entity(scope, entity_type, name, unit)
        if not created:
            raise AssertionError(f"could not seed {entity_type}")

    area_row = db.fetchone(
        "SELECT id FROM areas WHERE chat_id=? AND is_archived=0 ORDER BY id LIMIT 1",
        (scope,),
    )
    entity_row = db.fetchone(
        "SELECT id FROM entities WHERE chat_id=? AND is_archived=0 ORDER BY id LIMIT 1",
        (scope,),
    )
    ids = {
        "chat_id": scope,
        "scope_chat_id": scope,
        "user_id": owner,
        "account_id": int(account.id),
        "area_id": int(area_row["id"]) if area_row else 1,
        "entity_id": int(entity_row["id"]) if entity_row else 1,
    }

    client = TestClient(server.app, raise_server_exceptions=False)
    spec = server.app.openapi()
    headers = {"X-Access-Token": settings.miniapp_api_token}
    tested: list[tuple[str, int]] = []
    failures: list[tuple[str, object, str]] = []

    for path, item in sorted(spec.get("paths", {}).items()):
        operation = item.get("get")
        if not operation or not path.startswith("/api/"):
            continue
        params: dict[str, object] = {}
        rendered = path
        parameters = operation.get("parameters", [])
        for parameter in parameters:
            name = str(parameter.get("name") or "")
            where = parameter.get("in")
            value = _sample_parameter(name, parameter.get("schema") or {}, ids)
            if where == "path":
                rendered = rendered.replace("{" + name + "}", quote(str(value), safe=""))
            elif where == "query" and (
                parameter.get("required") or name in ids or name == "limit"
            ):
                params[name] = value
        declared = {
            str(parameter.get("name") or "")
            for parameter in parameters
            if parameter.get("in") == "query"
        }
        for key in ("chat_id", "user_id"):
            if key in declared and key not in params:
                params[key] = ids[key]
        try:
            response = client.get(
                rendered,
                params=params,
                headers=headers,
                follow_redirects=False,
            )
            tested.append((path, response.status_code))
            if response.status_code >= 500:
                failures.append((path, response.status_code, response.text[:800]))
        except Exception as exc:  # pragma: no cover - reported with route context
            failures.append((path, "EXC", repr(exc)))

    if len(tested) < 30:
        raise AssertionError(f"too few GET API routes exercised: {len(tested)}")
    if failures:
        details = "\n".join(f"{path}: {status}: {body}" for path, status, body in failures)
        raise AssertionError(f"GET API runtime failures:\n{details}")
    print(f"GET_API_RUNTIME_OK routes={len(tested)}")


def _active_assets() -> tuple[Path, Path]:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js_match = re.search(r"app-(20\d{6}[a-z])\.js", html)
    css_match = re.search(r"style-(20\d{6}[a-z])\.css", html)
    if not js_match or not css_match:
        raise AssertionError("active versioned JS/CSS asset not found in index.html")
    return (
        STATIC / f"app-{js_match.group(1)}.js",
        STATIC / f"style-{css_match.group(1)}.css",
    )


def audit_dynamic_selects() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    active_js, _ = _active_assets()
    js = active_js.read_text(encoding="utf-8")
    total = 0
    dynamic: list[str] = []
    missing: list[str] = []

    for select_match in re.finditer(r"<select\b([^>]*)>(.*?)</select>", html, re.I | re.S):
        attrs, body = select_match.group(1), select_match.group(2)
        id_match = re.search(r"\bid=[\"']([^\"']+)", attrs, re.I)
        if not id_match:
            continue
        total += 1
        select_id = id_match.group(1)
        if len(re.findall(r"<option\b", body, re.I)) > 1:
            continue
        dynamic.append(select_id)
        escaped = re.escape(select_id)
        direct = bool(re.search(rf"fillSelect\(\s*['\"]{escaped}['\"]", js))
        dom = bool(
            re.search(
                rf"byId\(\s*['\"]{escaped}['\"]\s*\)[^\n]{{0,220}}(?:innerHTML|appendChild|options|replaceChildren)",
                js,
            )
        )
        generic = bool(
            re.search(rf"['\"]{escaped}['\"][^\n]{{0,300}}fillSelect", js)
            or re.search(rf"fillSelect[^\n]{{0,300}}['\"]{escaped}['\"]", js)
        )
        if not (direct or dom or generic):
            missing.append(select_id)

    if total < 100:
        raise AssertionError(f"unexpectedly few selects found: {total}")
    if missing:
        raise AssertionError("dynamic selects without population path: " + ", ".join(missing))
    print(f"DYNAMIC_SELECT_POPULATION_OK total={total} dynamic={len(dynamic)}")


def audit_frontend_aliases() -> None:
    active_js, active_css = _active_assets()
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    if manifest.get("start_url") != "/mini":
        raise AssertionError(f"stale manifest start_url: {manifest.get('start_url')!r}")
    if (STATIC / "app.js").read_bytes() != active_js.read_bytes():
        raise AssertionError("generic app.js is not identical to the active Mini App JS")
    if (STATIC / "style.css").read_bytes() != active_css.read_bytes():
        raise AssertionError("generic style.css is not identical to the active Mini App CSS")
    print(f"FRONTEND_ALIASES_OK js={active_js.name} css={active_css.name}")


def main() -> None:
    audit_get_api_runtime()
    audit_dynamic_selects()
    audit_frontend_aliases()
    print("DEEP_RUNTIME_AUDIT_OK")


if __name__ == "__main__":
    main()
