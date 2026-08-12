from __future__ import annotations

from pathlib import Path
import textwrap


def patch_repository() -> None:
    p = Path("app/services/repository.py")
    s = p.read_text(encoding="utf-8")
    start = s.index("_GROUP_SCOPE_DATA_TABLES = (")
    end = s.index("def ensure_group_account_context", start)
    new = textwrap.dedent(r'''
    _SCOPE_ROUTING_OR_TRANSIENT_TABLES = frozenset({
        # These chat_id columns identify Telegram/routing/session context, not
        # tenant-owned business rows. They must never be mass-moved between scopes.
        "account_chat_access", "chat_active_account", "chat_area_bindings", "chats",
        "group_set_items", "setup_sessions", "pending_confirmations",
    })


    def _tenant_scope_tables_from_conn(conn) -> tuple[str, ...]:
        """Discover every persistent table whose chat_id is a tenant scope.

        This is schema-driven: future tables with chat_id are included automatically
        unless they are explicitly routing/transient tables above.
        """
        out: list[str] = []
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for row in rows:
            name = str(row[0])
            if name in _SCOPE_ROUTING_OR_TRANSIENT_TABLES:
                continue
            quoted = name.replace('"', '""')
            columns = conn.execute(f'PRAGMA table_info("{quoted}")').fetchall()
            if any(str(col[1]) == "chat_id" for col in columns):
                out.append(name)
        return tuple(out)


    def tenant_scope_tables() -> tuple[str, ...]:
        with db.connect() as conn:
            return _tenant_scope_tables_from_conn(conn)


    def _scope_business_row_count(chat_id: int) -> int:
        """Count every tenant-owned row, not a hand-maintained subset."""
        total = 0
        with db.connect() as conn:
            for table in _tenant_scope_tables_from_conn(conn):
                quoted = table.replace('"', '""')
                row = conn.execute(
                    f'SELECT COUNT(*) FROM "{quoted}" WHERE chat_id=?',
                    (int(chat_id),),
                ).fetchone()
                total += int(row[0] if row else 0)
        return total


    def _canonical_account_scope(account: AccountingAccount) -> int:
        return -900000000000 - int(account.id)


    def _merge_group_scope_into_canonical(account: AccountingAccount, group_chat_id: int) -> AccountingAccount:
        """Unify legacy group rows and newer split-scope rows transactionally.

        Any uniqueness/FK conflict rolls back the whole operation, so partial tenant
        migration is impossible.
        """
        group_chat_id = int(group_chat_id)
        canonical = _canonical_account_scope(account)
        if group_chat_id == canonical:
            return account
        if int(account.scope_chat_id) not in {group_chat_id, canonical}:
            return account
        occupied = db.fetchone(
            "SELECT id FROM accounting_accounts WHERE scope_chat_id=? AND id<>? AND is_archived=0",
            (canonical, int(account.id)),
        )
        if occupied:
            return account
        try:
            with db.connect() as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT OR IGNORE INTO chats(chat_id,title,chat_type,is_connected) VALUES(?,?,?,1)",
                    (canonical, f"Учёт: {account.name}", "account"),
                )
                for table in _tenant_scope_tables_from_conn(conn):
                    quoted = table.replace('"', '""')
                    conn.execute(
                        f'UPDATE "{quoted}" SET chat_id=? WHERE chat_id=?',
                        (canonical, group_chat_id),
                    )
                conn.execute(
                    "UPDATE accounting_accounts SET scope_chat_id=? WHERE id=?",
                    (canonical, int(account.id)),
                )
                fk = conn.execute("PRAGMA foreign_key_check").fetchall()
                if fk:
                    raise RuntimeError(f"foreign key check failed: {len(fk)}")
                conn.commit()
            repaired = get_account_by_id(int(account.id))
            return repaired or account
        except Exception as exc:
            try:
                db.execute(
                    "INSERT INTO security_events(chat_id,user_id,event_type,details) VALUES(?,?,?,?)",
                    (group_chat_id, int(account.owner_user_id), "scope_merge_blocked", str(exc)[:500]),
                )
            except Exception:
                pass
            return account


    def _repair_empty_account_scope_from_group(account: AccountingAccount, group_chat_id: int) -> AccountingAccount:
        """Compatibility wrapper: canonicalize all split legacy/new tenant data."""
        return _merge_group_scope_into_canonical(account, int(group_chat_id))


    ''')
    p.write_text(s[:start] + new + s[end:], encoding="utf-8")


def bump_version() -> None:
    src = Path("webapp/static/app-20260812e.js")
    dst = Path("webapp/static/app-20260812f.js")
    js = src.read_text(encoding="utf-8").replace(
        'const MINI_APP_VERSION="20260812e";',
        'const MINI_APP_VERSION="20260812f";',
        1,
    )
    dst.write_text(js, encoding="utf-8")
    idx = Path("webapp/static/index.html")
    x = idx.read_text(encoding="utf-8")
    x = x.replace(
        "/static/app-20260812e.js?v=83-legacyscope",
        "/static/app-20260812f.js?v=83-canonicalscope",
        1,
    )
    idx.write_text(x, encoding="utf-8")
    for path in ("app/keyboards.py", "webapp/server.py", "app/handlers/owner.py"):
        q = Path(path)
        q.write_text(q.read_text(encoding="utf-8").replace("20260812e", "20260812f"), encoding="utf-8")


def add_tests() -> None:
    t = Path("tests/test_step83_regressions.py")
    text = t.read_text(encoding="utf-8")
    if "from app import db as app_db" not in text:
        text = text.replace(
            "from app.services import excel_bridge",
            "from app import db as app_db\nfrom app.services import excel_bridge",
            1,
        )
    marker = '\n\nif __name__ == "__main__":\n'
    tests = textwrap.dedent(r'''
        def test_all_chat_id_tables_are_scope_classified(self):
            with tempfile.TemporaryDirectory() as tmp:
                test_settings = replace(app_db.settings, data_dir=Path(tmp), database_path=Path(tmp) / "scope.sqlite3")
                with patch.object(app_db, "settings", test_settings), patch.object(repo, "settings", test_settings):
                    app_db.init_db()
                    with app_db.connect() as conn:
                        all_scoped = []
                        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
                            name = str(row[0])
                            cols = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
                            if any(str(c[1]) == "chat_id" for c in cols):
                                all_scoped.append(name)
                        tenant = set(repo._tenant_scope_tables_from_conn(conn))
                    excluded = set(repo._SCOPE_ROUTING_OR_TRANSIENT_TABLES)
                    self.assertEqual(set(all_scoped), tenant | (set(all_scoped) & excluded))
                    for required in (
                        "company_sites", "storage_locations", "stock_transfers",
                        "excel_import_batches", "worker_shifts", "quality_inspections",
                        "maintenance_plans", "stock_alert_rules", "report_schedules",
                    ):
                        self.assertIn(required, tenant)

        def test_split_legacy_and_step83_scopes_merge_transactionally(self):
            with tempfile.TemporaryDirectory() as tmp:
                test_settings = replace(app_db.settings, data_dir=Path(tmp), database_path=Path(tmp) / "split.sqlite3")
                with patch.object(app_db, "settings", test_settings), patch.object(repo, "settings", test_settings):
                    app_db.init_db()
                    group = -100777001
                    owner = 777001
                    app_db.execute(
                        "INSERT OR REPLACE INTO chats(chat_id,title,chat_type,is_connected) VALUES(?,?,?,1)",
                        (group, "Завод", "supergroup"),
                    )
                    ok, msg, aid = repo.create_account(owner, group, "Завод")
                    self.assertTrue(ok, msg)
                    account = repo.get_account_by_id(aid)
                    canonical = account.scope_chat_id
                    # New Step83 structure exists in the canonical synthetic scope.
                    app_db.execute(
                        "INSERT INTO company_sites(chat_id,settlement,name,normalized,address,created_by) VALUES(?,?,?,?,?,?)",
                        (canonical, "Киржач", "Цех 1", "киржач цех 1", "", owner),
                    )
                    app_db.execute(
                        "INSERT INTO storage_locations(chat_id,name,normalized,code,created_by) VALUES(?,?,?,?,?)",
                        (canonical, "Стеллаж A", "стеллаж a", "A", owner),
                    )
                    # Legacy stock still exists under the Telegram group id.
                    cur = app_db.execute(
                        "INSERT INTO entities(chat_id,entity_type,name,normalized,default_unit) VALUES(?,?,?,?,?)",
                        (group, "stock_item", "Деталь", "деталь", "шт"),
                    )
                    entity_id = int(cur.lastrowid)
                    app_db.execute(
                        "INSERT INTO inventory(chat_id,area_id,entity_type,entity_id,unit,quantity) VALUES(?,?,?,?,?,?)",
                        (group, None, "stock_item", entity_id, "шт", 42),
                    )
                    # Simulate the previous faulty repair which pointed the account at the group.
                    app_db.execute("UPDATE accounting_accounts SET scope_chat_id=? WHERE id=?", (group, aid))
                    repaired = repo.list_accounts_for_user(owner)[0]
                    self.assertEqual(repaired.scope_chat_id, canonical)
                    self.assertEqual(
                        app_db.fetchone("SELECT quantity FROM inventory WHERE chat_id=? AND entity_id=?", (canonical, entity_id))["quantity"],
                        42,
                    )
                    self.assertIsNone(app_db.fetchone("SELECT 1 FROM inventory WHERE chat_id=?", (group,)))
                    self.assertEqual(app_db.fetchone("SELECT name FROM company_sites WHERE chat_id=?", (canonical,))["name"], "Цех 1")
                    self.assertEqual(app_db.fetchone("SELECT name FROM storage_locations WHERE chat_id=?", (canonical,))["name"], "Стеллаж A")
                    with app_db.connect() as conn:
                        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
    ''')
    if "test_split_legacy_and_step83_scopes_merge_transactionally" not in text:
        text = text.replace(marker, "\n" + tests + marker, 1)
    t.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    patch_repository()
    bump_version()
    add_tests()
    print("scope unification patch prepared")
