from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import quote

_AUDIT_TMP = tempfile.TemporaryDirectory(prefix="proizvodstvo_api_all_methods_")
os.environ["BOT_DATA_DIR"] = _AUDIT_TMP.name
os.environ.setdefault("OWNER_TELEGRAM_ID", "2097006037")
os.environ.setdefault("PUBLIC_BASE_URL", "https://example.invalid")
os.environ.setdefault("MINIAPP_API_TOKEN", "ci-only-miniapp-token-not-a-secret")

from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.services import repository as repo
from webapp import server


def _resolve(schema: dict, components: dict) -> dict:
    ref = schema.get("$ref") if isinstance(schema, dict) else None
    if not ref:
        return schema or {}
    key = str(ref).rsplit("/", 1)[-1]
    return components.get(key, {})


def _sample(schema: dict, components: dict, name: str = "", ids: dict[str, int] | None = None, depth: int = 0):
    ids = ids or {}
    if depth > 8:
        return None
    schema = _resolve(schema or {}, components)
    if "default" in schema:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]
    for key in ("oneOf", "anyOf"):
        options = schema.get(key) or []
        if options:
            non_null = [x for x in options if _resolve(x, components).get("type") != "null"]
            return _sample((non_null or options)[0], components, name, ids, depth + 1)
    if schema.get("allOf"):
        merged: dict = {}
        for item in schema["allOf"]:
            merged.update(_resolve(item, components))
        return _sample(merged, components, name, ids, depth + 1)

    low = name.lower()
    if name in ids:
        return ids[name]
    if low.endswith("_id") or low == "id":
        return 1
    if "date" in low or schema.get("format") == "date":
        return "2026-08-12"
    if schema.get("format") in {"date-time", "datetime"} or low.endswith("_at"):
        return "2026-08-12T12:00:00"
    if low in {"from", "date_from", "start_date"}:
        return "2026-08-01"
    if low in {"to", "date_to", "end_date"}:
        return "2026-08-12"
    if "email" in low:
        return "audit@example.invalid"
    if "status" in low:
        return "open"
    if low in {"operation_type", "entity_type", "type"}:
        return "component" if low != "operation_type" else "production"
    if "unit" in low:
        return "шт"
    if "quantity" in low or "amount" in low or "count" in low:
        return 1
    if "name" in low or "title" in low or "note" in low or "reason" in low or "description" in low:
        return "API audit"

    kind = schema.get("type")
    if kind == "object" or schema.get("properties"):
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        result = {}
        for prop_name, prop_schema in properties.items():
            # Include all ordinary properties so handlers get beyond Pydantic parsing.
            # Skip only opaque binary bodies handled by multipart endpoints.
            if (prop_schema or {}).get("format") == "binary":
                continue
            value = _sample(prop_schema or {}, components, prop_name, ids, depth + 1)
            if value is not None or prop_name in required:
                result[prop_name] = value
        return result
    if kind == "array":
        item = _sample(schema.get("items") or {}, components, name, ids, depth + 1)
        minimum = int(schema.get("minItems") or 0)
        return [item] if minimum > 0 and item is not None else []
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    return "audit"


def main() -> None:
    db.init_db()
    owner = int(settings.primary_owner_id or 2097006037)
    repo.upsert_chat(owner, "All-method audit owner", "private", connected=True)
    ok, message, account_id = repo.create_account(owner, owner, "ALL METHOD API AUDIT")
    if not ok:
        raise AssertionError(message)
    account = repo.get_account_by_id(account_id)
    if account is None:
        raise AssertionError("audit account missing")
    scope = int(account.scope_chat_id)
    repo.create_area(scope, "Audit area")
    for entity_type, name, unit in (
        ("component", "Audit component", "шт"),
        ("material", "Audit material", "кг"),
        ("stock_item", "Audit stock", "шт"),
        ("product", "Audit product", "шт"),
        ("meter", "Audit meter", "кВт·ч"),
    ):
        repo.create_entity(scope, entity_type, name, unit)

    area = db.fetchone("SELECT id FROM areas WHERE chat_id=? AND is_archived=0 ORDER BY id LIMIT 1", (scope,))
    entity = db.fetchone("SELECT id FROM entities WHERE chat_id=? AND is_archived=0 ORDER BY id LIMIT 1", (scope,))
    ids = {
        "chat_id": scope,
        "scope_chat_id": scope,
        "user_id": owner,
        "account_id": int(account.id),
        "area_id": int(area["id"]) if area else 1,
        "entity_id": int(entity["id"]) if entity else 1,
        "worker_user_id": owner,
    }

    client = TestClient(server.app, raise_server_exceptions=False)
    spec = server.app.openapi()
    components = (spec.get("components") or {}).get("schemas") or {}
    headers = {"X-Access-Token": settings.miniapp_api_token}
    methods = ("get", "post", "put", "patch", "delete")
    results: list[tuple[str, str, int]] = []
    failures: list[tuple[str, str, object, str]] = []

    for path, path_item in sorted((spec.get("paths") or {}).items()):
        if not path.startswith("/api/"):
            continue
        for method in methods:
            operation = path_item.get(method)
            if not operation:
                continue
            rendered = path
            query: dict[str, object] = {}
            all_params = list(path_item.get("parameters") or []) + list(operation.get("parameters") or [])
            declared_query = set()
            for parameter in all_params:
                name = str(parameter.get("name") or "")
                where = parameter.get("in")
                value = _sample(parameter.get("schema") or {}, components, name, ids)
                if where == "path":
                    rendered = rendered.replace("{" + name + "}", quote(str(value), safe=""))
                elif where == "query":
                    declared_query.add(name)
                    if parameter.get("required") or name in ids or name == "limit":
                        query[name] = value
            for key in ("chat_id", "user_id"):
                if key in declared_query and key not in query:
                    query[key] = ids[key]

            body = None
            request_body = operation.get("requestBody") or {}
            json_content = (request_body.get("content") or {}).get("application/json") or {}
            if json_content.get("schema"):
                body = _sample(json_content["schema"], components, ids=ids)

            try:
                response = client.request(
                    method.upper(),
                    rendered,
                    params=query,
                    json=body if json_content else None,
                    headers=headers,
                    follow_redirects=False,
                )
                results.append((method.upper(), path, response.status_code))
                if response.status_code >= 500:
                    failures.append((method.upper(), path, response.status_code, response.text[:1200]))
            except Exception as exc:  # pragma: no cover
                failures.append((method.upper(), path, "EXC", repr(exc)))

    expected = sum(
        1
        for path, item in (spec.get("paths") or {}).items()
        if path.startswith("/api/")
        for method in methods
        if item.get(method)
    )
    if len(results) + sum(1 for f in failures if f[2] == "EXC") != expected:
        raise AssertionError(f"route coverage mismatch: expected={expected}, results={len(results)}, failures={len(failures)}")

    print(f"API_ALL_METHODS_TESTED={expected}")
    counts: dict[int, int] = {}
    for method, path, status in results:
        counts[status] = counts.get(status, 0) + 1
        print(f"API_METHOD {status} {method} {path}")
    print("API_STATUS_COUNTS", dict(sorted(counts.items())))
    if failures:
        print(f"API_ALL_METHODS_FAILURES={len(failures)}")
        for failure in failures:
            print("API_METHOD_FAIL", failure)
        raise SystemExit(1)
    if expected < 100:
        raise AssertionError(f"unexpectedly few API operations: {expected}")
    print("API_ALL_METHODS_RUNTIME_OK")


if __name__ == "__main__":
    main()
