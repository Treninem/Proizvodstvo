from __future__ import annotations

import ast
import os
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

# Import after CI environment variables are set.
from app import db
from app.config import settings
from app.services import repository as repo


class AuditFailure(RuntimeError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AuditFailure(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def audit_versions() -> None:
    index = read("webapp/static/index.html")
    server = read("webapp/server.py")
    keyboards = read("app/keyboards.py")
    owner = read("app/handlers/owner.py")
    m = re.search(r'app-(20\d{6}[a-z])\.js', index)
    check(bool(m), "index.html does not reference a versioned Mini App JS")
    version = m.group(1)
    js_path = ROOT / f"webapp/static/app-{version}.js"
    check(js_path.exists(), f"referenced Mini App asset is missing: {js_path.name}")
    js = js_path.read_text(encoding="utf-8")
    for name, text in (("server", server), ("keyboards", keyboards), ("owner", owner), ("js", js)):
        check(version in text, f"Mini App version mismatch: {name} does not contain {version}")
    # Telegram launch fragments must never be destroyed by an HTTP redirect.
    tree = ast.parse(server)
    mini_defs = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    if dec.func.attr == "get" and dec.args and isinstance(dec.args[0], ast.Constant) and dec.args[0].value == "/mini":
                        mini_defs.append(node)
    check(len(mini_defs) == 1, "expected exactly one /mini route")
    mini_src = ast.get_source_segment(server, mini_defs[0]) or ""
    check("RedirectResponse" not in mini_src, "/mini must not redirect because Telegram tgWebAppData lives in URL fragment")
    print(f"VERSION_OK {version}")


def audit_ui_wiring() -> None:
    html = read("webapp/static/index.html")
    m = re.search(r'app-(20\d{6}[a-z])\.js', html)
    version = m.group(1)
    js = read(f"webapp/static/app-{version}.js")

    # Every delegated action in HTML must have a literal counterpart in JS.
    actions = sorted(set(re.findall(r'data-action=["\']([^"\']+)', html)))
    missing_actions = [a for a in actions if a not in js]
    check(not missing_actions, "HTML actions with no JS handler/reference: " + ", ".join(missing_actions))

    # Every select must be read/populated by JS unless explicitly marked static-only.
    select_ids = sorted(set(re.findall(r'<select\b[^>]*\bid=["\']([^"\']+)', html, flags=re.I)))
    missing_selects = [sid for sid in select_ids if sid not in js]
    check(not missing_selects, "Selects not wired in JS: " + ", ".join(missing_selects))

    # Common explicit DOM lookups in JS must point to actual HTML nodes.
    html_ids = set(re.findall(r'\bid=["\']([^"\']+)', html))
    refs = set(re.findall(r'(?:getElementById|byId)\(\s*["\']([^"\']+)', js))
    missing_nodes = sorted(refs - html_ids)
    check(not missing_nodes, "JS references missing HTML ids: " + ", ".join(missing_nodes[:50]))

    print(f"UI_WIRING_OK actions={len(actions)} selects={len(select_ids)} dom_refs={len(refs)}")


def _server_function_graph(server_text: str):
    tree = ast.parse(server_text)
    funcs: dict[str, ast.AST] = {}
    routes: list[tuple[str, str, str]] = []
    calls: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        funcs[node.name] = node
        called = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                if isinstance(sub.func, ast.Name):
                    called.add(sub.func.id)
                elif isinstance(sub.func, ast.Attribute):
                    called.add(sub.func.attr)
        calls[node.name] = called
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.args:
                method = dec.func.attr.upper()
                if method in {"GET", "POST", "DELETE", "PUT", "PATCH"} and isinstance(dec.args[0], ast.Constant):
                    path = str(dec.args[0].value)
                    if path.startswith("/api/"):
                        routes.append((method, path, node.name))
    return funcs, calls, routes


def audit_api_auth_and_frontend_routes() -> None:
    server = read("webapp/server.py")
    html = read("webapp/static/index.html")
    version = re.search(r'app-(20\d{6}[a-z])\.js', html).group(1)
    js = read(f"webapp/static/app-{version}.js")
    funcs, graph, routes = _server_function_graph(server)

    auth_markers = {"_check_token", "_flow_user"}

    def reaches_auth(name: str, seen: set[str] | None = None) -> bool:
        seen = set(seen or ())
        if name in seen:
            return False
        seen.add(name)
        direct = graph.get(name, set())
        if direct & auth_markers:
            return True
        return any(c in funcs and reaches_auth(c, seen) for c in direct)

    unauth = [(m, p, n) for m, p, n in routes if not reaches_auth(n)]
    check(not unauth, "API routes without reachable token auth: " + "; ".join(f"{m} {p} -> {n}" for m, p, n in unauth))

    server_paths = {p for _m, p, _n in routes}
    # Literal frontend API URLs; query strings are normalized away.
    api_literals = set(re.findall(r'["\'](/api/[A-Za-z0-9_./{}-]+)(?:\?[^"\']*)?["\']', js))
    missing_routes = []
    for raw in sorted(api_literals):
        # Dynamic path fragments cannot be compared exactly; compare literal prefix.
        if "{" in raw or "}" in raw:
            continue
        if raw not in server_paths:
            missing_routes.append(raw)
    check(not missing_routes, "Frontend calls API paths with no server route: " + ", ".join(missing_routes))
    print(f"API_AUTH_OK routes={len(routes)} frontend_literals={len(api_literals)}")


def audit_schema_scope_coverage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_settings = replace(settings, data_dir=Path(tmp), database_path=Path(tmp) / "audit.sqlite3")
        with patch.object(db, "settings", test_settings), patch.object(repo, "settings", test_settings):
            db.init_db()
            with db.connect() as conn:
                all_chat = []
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
                    name = str(row[0])
                    cols = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
                    if any(str(c[1]) == "chat_id" for c in cols):
                        all_chat.append(name)
                tenant = set(repo._tenant_scope_tables_from_conn(conn))
                excluded = set(repo._SCOPE_ROUTING_OR_TRANSIENT_TABLES)
                unclassified = set(all_chat) - tenant - excluded
                check(not unclassified, "Unclassified chat_id tables: " + ", ".join(sorted(unclassified)))
                check(not (tenant & excluded), "Tables classified as both tenant and routing: " + ", ".join(sorted(tenant & excluded)))
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                check(integrity == "ok", f"fresh database integrity_check failed: {integrity}")
                fk = conn.execute("PRAGMA foreign_key_check").fetchall()
                check(not fk, f"fresh database foreign_key_check failed: {len(fk)} rows")
            required = {
                "areas", "departments", "entities", "inventory", "operations", "company_sites", "storage_locations",
                "stock_transfers", "excel_import_batches", "production_tasks", "production_lots", "equipment",
                "quality_inspections", "replenishment_requests", "maintenance_plans", "worker_shifts",
            }
            check(required <= tenant, "Critical tenant tables missing from migration classifier: " + ", ".join(sorted(required - tenant)))
            print(f"SCHEMA_SCOPE_OK chat_id_tables={len(all_chat)} tenant={len(tenant)} excluded={len(excluded)}")


def audit_split_scope_and_tenant_isolation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        test_settings = replace(settings, data_dir=Path(tmp), database_path=Path(tmp) / "tenant.sqlite3")
        with patch.object(db, "settings", test_settings), patch.object(repo, "settings", test_settings):
            db.init_db()
            owner_a, owner_b = 700001, 700002
            group_a, group_b = -100700001, -100700002
            for gid, title in ((group_a, "Фирма A"), (group_b, "Фирма B")):
                db.execute("INSERT OR REPLACE INTO chats(chat_id,title,chat_type,is_connected) VALUES(?,?,?,1)", (gid, title, "supergroup"))
            ok, msg, aid = repo.create_account(owner_a, group_a, "Фирма A")
            check(ok and aid, msg)
            ok, msg, bid = repo.create_account(owner_b, group_b, "Фирма B")
            check(ok and bid, msg)
            a = repo.get_account_by_id(aid); b = repo.get_account_by_id(bid)
            canonical_a = a.scope_chat_id
            canonical_b = b.scope_chat_id

            # New Step83 structure in canonical scope.
            ok, msg, site_id = repo.create_company_site(canonical_a, owner_a, "Город A", "Площадка A", "")
            check(ok and site_id, msg)
            ok, msg, loc_id = repo.create_storage_location(canonical_a, owner_a, "Место A", site_id=site_id, code="A-1")
            check(ok and loc_id, msg)

            # Legacy data in Telegram group scope.
            db.execute("INSERT INTO areas(chat_id,name,normalized) VALUES(?,?,?)", (group_a, "Участок A", "участок a"))
            area_id = int(db.fetchone("SELECT id FROM areas WHERE chat_id=? AND normalized=?", (group_a, "участок a"))["id"])
            db.execute("INSERT INTO entities(chat_id,entity_type,name,normalized,default_unit) VALUES(?,?,?,?,?)", (group_a, "stock_item", "Деталь A", "деталь a", "шт"))
            entity_id = int(db.fetchone("SELECT id FROM entities WHERE chat_id=? AND entity_type=? AND normalized=?", (group_a, "stock_item", "деталь a"))["id"])
            db.execute("INSERT INTO inventory(chat_id,area_id,entity_type,entity_id,unit,quantity) VALUES(?,?,?,?,?,?)", (group_a, area_id, "stock_item", entity_id, "шт", 123))
            db.execute("INSERT INTO operations(chat_id,group_chat_id,area_id,user_id,operation_type,entity_type,entity_id,quantity,unit,raw_text) VALUES(?,?,?,?,?,?,?,?,?,?)", (group_a, group_a, area_id, owner_a, "production", "stock_item", entity_id, 1, "шт", "audit"))

            # Simulate an older release that switched account scope to the group id.
            db.execute("UPDATE accounting_accounts SET scope_chat_id=? WHERE id=?", (group_a, aid))
            repaired = [x for x in repo.list_accounts_for_user(owner_a) if x.id == aid][0]
            check(repaired.scope_chat_id == canonical_a, "split-scope migration did not restore canonical scope")
            check(db.fetchone("SELECT quantity FROM inventory WHERE chat_id=? AND entity_id=?", (canonical_a, entity_id))["quantity"] == 123, "legacy inventory was not migrated")
            check(db.fetchone("SELECT name FROM company_sites WHERE chat_id=?", (canonical_a,))["name"] == "Площадка A", "new company site was lost")
            check(db.fetchone("SELECT name FROM storage_locations WHERE chat_id=?", (canonical_a,))["name"] == "Место A", "new storage location was lost")
            for table in repo.tenant_scope_tables():
                q = table.replace('"', '""')
                row = db.fetchone(f'SELECT COUNT(*) AS c FROM "{q}" WHERE chat_id=?', (group_a,))
                check(int(row["c"]) == 0, f"legacy tenant rows remain under group scope in {table}")

            # Tenant B must never see tenant A via account access or repositories.
            check(all(x.id != aid for x in repo.list_accounts_for_user(owner_b)), "tenant B can list tenant A account")
            check(not repo.user_has_account_access(aid, owner_b), "tenant B has access to tenant A")
            check(repo.user_has_account_access(aid, owner_a), "tenant A owner lost access")
            check(repo.list_company_sites(canonical_b) == [], "tenant B sees tenant A company sites")
            check(repo.list_storage_locations(canonical_b) == [], "tenant B sees tenant A storage locations")

            with db.connect() as conn:
                check(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "post-migration database integrity failed")
                check(not conn.execute("PRAGMA foreign_key_check").fetchall(), "post-migration foreign keys failed")
            print(f"TENANT_ISOLATION_OK accountA={aid} accountB={bid} canonicalA={canonical_a}")


def audit_sql_join_tenant_hardening() -> None:
    # Report potentially unsafe joins. This is a heuristic: it does not fail by itself,
    # but it makes every candidate visible in CI so a reviewer cannot miss the same class of bug.
    warnings = []
    for path in list((ROOT / "app").rglob("*.py")) + list((ROOT / "webapp").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            u = line.upper()
            if " JOIN " not in u or " ON " not in u or ".ID" not in u:
                continue
            # Cross-tenant data joins should usually constrain chat_id as well. Multi-line SQL
            # is reviewed in a short neighborhood to avoid obvious false positives.
            lines = text.splitlines()
            neighborhood = " ".join(lines[max(0, lineno-2): min(len(lines), lineno+2)]).lower()
            if "chat_id" not in neighborhood and any(t in neighborhood for t in ("areas", "entities", "departments", "equipment", "production_lots", "company_sites", "storage_locations")):
                warnings.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()[:180]}")
    print(f"SQL_JOIN_REVIEW candidates={len(warnings)}")
    for item in warnings[:80]:
        print("SQL_JOIN_CANDIDATE", item)


def audit_repository_hygiene() -> None:
    import subprocess
    forbidden_names = {".env", "production_account.sqlite3"}
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    bad = []
    for rel in tracked:
        p = Path(rel)
        if p.name in forbidden_names or p.suffix in {".sqlite", ".sqlite3", ".db", ".pyc"} or p.name.endswith(".zip.enc"):
            bad.append(rel)
    check(not bad, "Runtime/secrets artifacts tracked by Git: " + ", ".join(bad[:30]))
    runtime_defaults = read("runtime.defaults.env")
    for key in ("MINIAPP_API_TOKEN", "BACKUP_ENCRYPTION_KEY"):
        m = re.search(rf"^{key}=(.*)$", runtime_defaults, flags=re.M)
        check(m is not None and not m.group(1).strip(), f"{key} must be empty in runtime.defaults.env")
    print("REPOSITORY_HYGIENE_OK")


def main() -> None:
    audits = [
        audit_versions,
        audit_ui_wiring,
        audit_api_auth_and_frontend_routes,
        audit_schema_scope_coverage,
        audit_split_scope_and_tenant_isolation,
        audit_sql_join_tenant_hardening,
        audit_repository_hygiene,
    ]
    for fn in audits:
        print(f"=== {fn.__name__} ===")
        fn()
    print("FULL_SYSTEM_AUDIT_OK")


if __name__ == "__main__":
    main()
