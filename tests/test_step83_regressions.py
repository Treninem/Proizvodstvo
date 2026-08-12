from __future__ import annotations

import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from openpyxl import Workbook

from app.services import excel_bridge
from app.services import repository as repo
from app.services import replenishment, quality_control, stock_transfers, production_flow, inventory_adjustment, backups
from webapp import server


class _DummyConn:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, *args, **kwargs):
        class Cur:
            rowcount = 1
        return Cur()
    def commit(self):
        return None


class Step83RegressionTests(unittest.TestCase):
    def test_account_listing_repairs_empty_synthetic_scope_from_legacy_group(self):
        row = {
            "id": 7, "owner_user_id": 55, "owner_chat_id": -100777,
            "scope_chat_id": -900000000007, "name": "Цех",
            "normalized": "цех", "is_general": 0,
        }
        repaired = repo.AccountingAccount(
            id=7, owner_user_id=55, owner_chat_id=-100777,
            scope_chat_id=-100777, name="Цех", normalized="цех", is_general=False,
        )
        with patch.object(repo.db, "fetchall", side_effect=[[row], []]), \
             patch.object(repo.db, "fetchone", return_value={"chat_type": "supergroup"}), \
             patch.object(repo, "_repair_empty_account_scope_from_group", return_value=repaired) as repair:
            accounts = repo.list_accounts_for_user(55)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0].scope_chat_id, -100777)
        repair.assert_called_once()
        self.assertEqual(repair.call_args.args[1], -100777)

    def test_encrypted_backups_are_visible_in_backup_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = root / "plain.zip"
            encrypted = root / "encrypted.zip.enc"
            ignored = root / "ignore.txt"
            for path in (plain, encrypted, ignored):
                path.write_bytes(b"x")
            with patch.object(backups, "backups_dir", return_value=root):
                names = {path.name for path in backups.list_backup_files(10)}
            self.assertEqual(names, {"plain.zip", "encrypted.zip.enc"})

    def test_conversational_inventory_phrase_does_not_change_stock(self):
        self.assertFalse(inventory_adjustment.looks_like_inventory_adjustment(
            "Остаток трубы примерно 5 метров, надо проверить"
        ))
        self.assertFalse(inventory_adjustment.looks_like_inventory_adjustment(
            "Остаток Трубка около 50 шт"
        ))
        self.assertTrue(inventory_adjustment.looks_like_inventory_adjustment(
            "Инвентаризация Трубка 50 шт"
        ))
        self.assertTrue(inventory_adjustment.looks_like_inventory_adjustment(
            "Остаток Трубка 50 шт"
        ))

    def test_service_token_is_bound_to_platform_owner(self):
        test_settings = replace(
            server.settings,
            miniapp_api_token="service-secret",
            primary_owner_id=2097006037,
        )
        with patch.object(server, "settings", test_settings):
            auth_user = server._check_token("service-secret", "")
            self.assertEqual(auth_user, 2097006037)
            self.assertEqual(server._request_user(2097006037, auth_user), 2097006037)
            with self.assertRaises(HTTPException) as ctx:
                server._request_user(123456, auth_user)
            self.assertEqual(ctx.exception.status_code, 403)

    def test_department_rule_deletes_resolve_scope_before_admin_check(self):
        seen = []
        with patch.object(repo, "_department_scope", return_value=-9001), \
             patch.object(repo, "is_tenant_admin", side_effect=lambda scope, user: seen.append((scope, user)) or True), \
             patch.object(repo, "_department_row", return_value={"id": 7, "chat_id": -9001}), \
             patch.object(repo.db, "execute") as execute:
            ok, _ = repo.delete_department_operation_rule(100, 55, 7, "production")
            self.assertTrue(ok)
            ok, _ = repo.delete_department_entity_rule(100, 55, 7, "production", 11)
            self.assertTrue(ok)
            self.assertEqual(seen, [(-9001, 55), (-9001, 55)])
            self.assertEqual(execute.call_count, 2)

    def test_excel_preview_keeps_more_than_1000_rows(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "Склад"
        ws.append(["Наименование", "Приход"])
        for i in range(1505):
            ws.append([f"Позиция {i+1}", i + 1])
        buf = io.BytesIO()
        wb.save(buf)
        captured = {}

        def fake_execute(sql, params=()):
            if "INSERT INTO excel_import_batches" in sql:
                captured["preview"] = json.loads(params[4])

        with patch.object(excel_bridge.repo, "resolve_scope_chat_id", side_effect=lambda x: x), \
             patch.object(excel_bridge.repo, "is_tenant_admin", return_value=True), \
             patch.object(excel_bridge.repo, "tenant_audit"), \
             patch.object(excel_bridge.db, "execute", side_effect=fake_execute):
            preview = excel_bridge.analyze_bytes(10, 20, buf.getvalue(), "large.xlsx")

        self.assertEqual(preview["total_rows"], 1505)
        self.assertEqual(len(preview["rows"]), 1505)
        self.assertEqual(len(captured["preview"]["rows"]), 1505)

    def test_excel_processing_resume_skips_existing_operation(self):
        preview = {
            "file_name": "resume.xlsx",
            "entity_type": "stock_item",
            "rows": [{
                "row": 2,
                "source_column": 2,
                "metric": "stock_in",
                "entity_type": "stock_item",
                "entity_name": "Тест",
                "location_name": "",
                "quantity": 5,
                "unit": "шт",
            }],
        }
        batch_row = {"status": "processing", "preview_json": json.dumps(preview, ensure_ascii=False)}
        calls = {"n": 0}

        def fake_fetchone(sql, params=()):
            if "excel_import_batches" in sql:
                return batch_row
            if "client_request_id" in sql:
                expected = "excel:b1:2:2:stock_in"
                self.assertEqual(params[2], expected)
                return {"id": 77}
            return None

        with patch.object(excel_bridge.repo, "resolve_scope_chat_id", side_effect=lambda x: x), \
             patch.object(excel_bridge.repo, "is_tenant_admin", return_value=True), \
             patch.object(excel_bridge.repo, "tenant_audit"), \
             patch.object(excel_bridge.db, "fetchone", side_effect=fake_fetchone), \
             patch.object(excel_bridge.db, "execute"), \
             patch.object(excel_bridge.accounting, "record_internal_operation") as record:
            result = excel_bridge.confirm_import(10, 20, "b1")

        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["operation_ids"], [77])
        record.assert_not_called()

    def test_replenishment_rejects_foreign_area(self):
        entity = {"id": 10, "entity_type": "stock_item", "default_unit": "шт", "name": "Тест"}
        def fake_fetchone(sql, params=()):
            if "FROM entities" in sql:
                return entity
            if "FROM areas" in sql:
                return None
            return None
        with patch.object(replenishment.repo, "resolve_scope_chat_id", return_value=-100), \
             patch.object(replenishment, "_visible", return_value=True), \
             patch.object(replenishment.db, "fetchone", side_effect=fake_fetchone):
            with self.assertRaisesRegex(ValueError, "Площадка не найдена"):
                replenishment.create_request(-100, 55, {"entity_id": 10, "area_id": 999, "requested_quantity": 1})

    def test_quality_rejects_explicit_foreign_lot(self):
        entity = {"id": 10, "entity_type": "product", "default_unit": "шт", "name": "Изделие"}
        with patch.object(quality_control.repo, "resolve_scope_chat_id", return_value=-100), \
             patch.object(quality_control, "_entity", return_value=entity), \
             patch.object(quality_control, "_visible_entity", return_value=True), \
             patch.object(quality_control, "_task", return_value=None), \
             patch.object(quality_control, "_lot", return_value=None), \
             patch.object(quality_control, "_equipment", return_value=None):
            with self.assertRaisesRegex(ValueError, "Партия не найдена"):
                quality_control.create_inspection(-100, 55, 10, department_id=7, lot_id=999, checked_quantity=1)

    def test_transfer_rejects_already_processed_before_inventory_change(self):
        transfer = {"id": 5, "status": "accepted", "to_department_id": 2, "items": []}
        with patch.object(stock_transfers.repo, "resolve_scope_chat_id", return_value=-100), \
             patch.object(stock_transfers, "get_transfer", return_value=transfer):
            with self.assertRaisesRegex(ValueError, "уже обработана"):
                stock_transfers.accept_transfer(-100, 55, 5)


    def test_replenishment_rejects_foreign_source_rule(self):
        entity = {"id": 10, "entity_type": "stock_item", "default_unit": "шт", "name": "Тест"}
        def fake_fetchone(sql, params=()):
            if "FROM entities" in sql:
                return entity
            if "FROM stock_alert_rules" in sql:
                return None
            return None
        with patch.object(replenishment.repo, "resolve_scope_chat_id", return_value=-100), \
             patch.object(replenishment, "_visible", return_value=True), \
             patch.object(replenishment.db, "fetchone", side_effect=fake_fetchone):
            with self.assertRaisesRegex(ValueError, "Правило пополнения не найдено"):
                replenishment.create_request(-100, 55, {"entity_id": 10, "source_rule_id": 999, "requested_quantity": 1})

    def test_lot_relation_rejects_foreign_task(self):
        def fake_fetchone(sql, params=()):
            if "FROM production_lots" in sql:
                return {"id": int(params[1])}
            return None
        with patch.object(production_flow.repo, "resolve_scope_chat_id", return_value=-100), \
             patch.object(production_flow.repo, "is_tenant_admin", return_value=True), \
             patch.object(production_flow.db, "fetchone", side_effect=fake_fetchone), \
             patch.object(production_flow, "get_lot", return_value={"id": 1}), \
             patch.object(production_flow, "_task_row", return_value=None):
            with self.assertRaisesRegex(ValueError, "Задание не найдено"):
                production_flow.link_lots(-100, 55, 1, 2, 1, task_id=999)


if __name__ == "__main__":
    unittest.main(verbosity=2)
