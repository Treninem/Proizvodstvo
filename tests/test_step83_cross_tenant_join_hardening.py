from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("OWNER_TELEGRAM_ID", "2097006037")
os.environ.setdefault("PUBLIC_BASE_URL", "https://example.invalid")

from app import db
from app.config import settings
from app.services import accounting
from app.services import quality_control
from app.services import repository as repo
from app.services import stock_risk


class CrossTenantJoinHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.test_settings = replace(settings, data_dir=Path(self.tmp.name), database_path=Path(self.tmp.name) / "joins.sqlite3")
        self.db_patch = patch.object(db, "settings", self.test_settings)
        self.repo_patch = patch.object(repo, "settings", self.test_settings)
        self.db_patch.start(); self.repo_patch.start()
        db.init_db()

        self.owner_a = 830001
        self.owner_b = 830002
        for uid, name in ((self.owner_a, "Фирма A"), (self.owner_b, "Фирма B")):
            repo.upsert_chat(uid, name, "private", connected=True)
            ok, msg, account_id = repo.create_account(uid, uid, name)
            self.assertTrue(ok, msg)
            setattr(self, "account_a" if uid == self.owner_a else "account_b", repo.get_account_by_id(account_id))

        self.scope_a = int(self.account_a.scope_chat_id)
        self.scope_b = int(self.account_b.scope_chat_id)
        for scope, suffix in ((self.scope_a, "A"), (self.scope_b, "B")):
            ok, msg = repo.create_area(scope, f"Площадка {suffix}")
            self.assertTrue(ok, msg)
            ok, msg = repo.create_entity(scope, "product", f"Изделие {suffix}", "шт")
            self.assertTrue(ok, msg)

        self.area_a = int(db.fetchone("SELECT id FROM areas WHERE chat_id=? AND normalized=?", (self.scope_a, "площадка a"))["id"])
        self.area_b = int(db.fetchone("SELECT id FROM areas WHERE chat_id=? AND normalized=?", (self.scope_b, "площадка b"))["id"])
        self.entity_a = int(db.fetchone("SELECT id FROM entities WHERE chat_id=? AND normalized=?", (self.scope_a, "изделие a"))["id"])
        self.entity_b = int(db.fetchone("SELECT id FROM entities WHERE chat_id=? AND normalized=?", (self.scope_b, "изделие b"))["id"])

    def tearDown(self) -> None:
        self.repo_patch.stop(); self.db_patch.stop(); self.tmp.cleanup()

    def test_corrupt_operation_cannot_leak_foreign_entity_or_area_names(self):
        saved = accounting.apply_operations(
            self.scope_a, self.owner_a, self.owner_a,
            [{
                "operation_type": "production", "entity_type": "product", "entity_id": self.entity_a,
                "quantity": 10, "unit": "шт", "area_id": self.area_a,
                "client_request_id": "cross-tenant-corrupt-op",
            }],
            "audit operation",
        )
        self.assertEqual(saved, 1)
        operation_id = int(db.fetchone("SELECT id FROM operations WHERE chat_id=? ORDER BY id DESC LIMIT 1", (self.scope_a,))["id"])
        # Simulate an old/corrupted DB link that normal application validation would reject.
        db.execute("UPDATE operations SET entity_id=?,area_id=? WHERE id=?", (self.entity_b, self.area_b, operation_id))

        rows = accounting.list_recent_operations(self.scope_a, user_id=self.owner_a, limit=20)
        row = next(item for item in rows if int(item["id"]) == operation_id)
        self.assertIsNone(row.get("entity_name"))
        self.assertIsNone(row.get("area_name"))

        client = repo.get_operation_by_client_request(self.scope_a, self.owner_a, "cross-tenant-corrupt-op")
        self.assertIsNotNone(client)
        self.assertIsNone(client.get("entity_name"))
        self.assertIsNone(client.get("area_name"))

    def test_corrupt_quality_link_cannot_reveal_foreign_names(self):
        item = quality_control.create_inspection(
            self.scope_a, self.owner_a, self.entity_a,
            area_id=self.area_a, checked_quantity=10, defect_quantity=0,
        )
        inspection_id = int(item["id"])
        db.execute("UPDATE quality_inspections SET entity_id=?,area_id=? WHERE id=?", (self.entity_b, self.area_b, inspection_id))
        # Scoped inner entity join makes a corrupted foreign inspection invisible instead of decorating it with foreign data.
        self.assertIsNone(quality_control.get_inspection(self.scope_a, inspection_id, self.owner_a))
        self.assertFalse(any(int(x["id"]) == inspection_id for x in quality_control.list_inspections(self.scope_a, self.owner_a)))

    def test_corrupt_stock_rule_cannot_reveal_foreign_names(self):
        ok, msg, rule_id = stock_risk.save_rule(self.scope_a, self.owner_a, {
            "entity_type": "product", "entity_id": self.entity_a, "area_id": self.area_a,
            "warning_shifts": 10, "critical_shifts": 5, "emergency_shifts": 1,
        })
        self.assertTrue(ok, msg)
        self.assertIsNotNone(rule_id)
        db.execute("UPDATE stock_alert_rules SET entity_id=?,area_id=? WHERE id=?", (self.entity_b, self.area_b, int(rule_id)))
        self.assertFalse(any(int(x["id"]) == int(rule_id) for x in stock_risk.list_rules(self.scope_a)))
        self.assertIsNone(stock_risk.get_rule(int(rule_id)))


if __name__ == "__main__":
    unittest.main()
