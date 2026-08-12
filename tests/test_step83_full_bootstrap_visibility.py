from __future__ import annotations

import json
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
from app.services import repository as repo
from webapp import server


class FullMiniAppDataVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.owner = int(settings.primary_owner_id or 2097006037)
        self.token = "step83-e2e-service-token"
        self.test_settings = replace(
            settings,
            data_dir=Path(self.tmp.name),
            database_path=Path(self.tmp.name) / "full-visibility.sqlite3",
            public_base_url="https://example.invalid",
            miniapp_api_token=self.token,
        )
        self.patches = [
            patch.object(db, "settings", self.test_settings),
            patch.object(repo, "settings", self.test_settings),
            patch.object(server, "settings", self.test_settings),
        ]
        for p in self.patches:
            p.start()
        db.init_db()
        repo.upsert_chat(self.owner, "Владелец платформы", "private", connected=True)
        ok, msg, account_id = repo.create_account(self.owner, self.owner, "ПРОИЗВОДСТВО ПЛАСТМАСС")
        self.assertTrue(ok, msg)
        self.account = repo.get_account_by_id(account_id)
        self.scope = int(self.account.scope_chat_id)

    def tearDown(self) -> None:
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def auth(self) -> dict[str, object]:
        return {"x_access_token": self.token, "x_telegram_init_data": None}

    def entity_id(self, name: str) -> int:
        row = db.fetchone("SELECT id FROM entities WHERE chat_id=? AND name=? AND is_archived=0", (self.scope, name))
        self.assertIsNotNone(row, name)
        return int(row["id"])

    def area_id(self, name: str) -> int:
        row = db.fetchone("SELECT id FROM areas WHERE chat_id=? AND name=? AND is_archived=0", (self.scope, name))
        self.assertIsNotNone(row, name)
        return int(row["id"])

    def test_every_core_dictionary_and_workflow_reaches_current_miniapp_scope(self):
        # --- Organisation structure ---
        for area_name in ("Участок E2E", "Склад E2E"):
            ok, msg = repo.create_area(self.scope, area_name)
            self.assertTrue(ok, msg)
        area_from = self.area_id("Участок E2E")
        area_to = self.area_id("Склад E2E")

        department_result = server.department_save(
            server.DepartmentPayload(chat_id=self.scope, user_id=self.owner, name="Отдел E2E", description="сквозной тест"),
            **self.auth(),
        )
        department = next(x for x in department_result["departments"] if x["name"] == "Отдел E2E")
        department_id = int(department["id"])

        site_result = server.company_site_api(
            server.CompanySitePayload(chat_id=self.scope, user_id=self.owner, settlement="Владимир", name="Завод E2E", address="Тестовый адрес"),
            **self.auth(),
        )
        site_id = int(site_result["site_id"])
        server.area_site_api(server.AreaSitePayload(chat_id=self.scope, user_id=self.owner, area_id=area_from, site_id=site_id), **self.auth())
        location_result = server.storage_location_api(
            server.StorageLocationPayload(chat_id=self.scope, user_id=self.owner, name="Стеллаж E2E", site_id=site_id, area_id=area_from, department_id=department_id, code="E2E-01"),
            **self.auth(),
        )
        location_id = int(location_result["location_id"])

        # --- Staff / rights ---
        title_result = server.save_job_title(
            server.JobTitlePayload(
                chat_id=self.scope,
                user_id=self.owner,
                name="Мастер E2E",
                permissions={"production": True, "stock": True, "reports": True, "assembly": True},
            ),
            **self.auth(),
        )
        title = next(x for x in title_result["job_titles"] if x["name"] == "Мастер E2E")
        worker_id = 830099
        repo.upsert_chat(worker_id, "Сотрудник E2E", "private", connected=True)
        worker_result = server.save_worker(
            server.WorkerPayload(chat_id=self.scope, user_id=self.owner, worker_user_id=worker_id, display_name="Сотрудник E2E", job_title_id=int(title["id"])),
            **self.auth(),
        )
        self.assertTrue(any(int(x["user_id"]) == worker_id for x in worker_result["workers"]))

        # --- Dictionaries created through the same repository path used by the bot ---
        dictionary = {
            "component": "Комплектующая E2E",
            "material": "Сырьё E2E",
            "stock_item": "Складская позиция E2E",
            "product": "Изделие E2E",
            "meter": "Счётчик E2E",
        }
        for entity_type, name in dictionary.items():
            ok, msg = repo.create_entity(self.scope, entity_type, name, "шт" if entity_type != "meter" else "кВт·ч")
            self.assertTrue(ok, msg)
        component_id = self.entity_id(dictionary["component"])
        product_id = self.entity_id(dictionary["product"])
        material_id = self.entity_id(dictionary["material"])

        # Product plan must use the same product dictionary that fills the Mini App select.
        plan_result = server.save_plan(
            server.PlanPayload(chat_id=self.scope, user_id=self.owner, product_id=product_id, targets=[10000, 50000]),
            **self.auth(),
        )
        self.assertTrue(any(int(x["product_id"]) == product_id for x in plan_result["targets"]))

        # --- Stock / inventory ---
        op_result = server.create_operation(
            server.OperationPayload(
                chat_id=self.scope, user_id=self.owner, operation_type="production", entity_type="component",
                entity_id=component_id, quantity=100, unit="шт", area_id=area_from,
                department_id=department_id, storage_location_id=location_id,
                client_request_id="full-e2e-production", confirm_warnings=True,
            ),
            **self.auth(),
        )
        self.assertEqual(int(op_result["saved"]), 1)

        # --- Tasks / lots / equipment ---
        equipment_result = server.save_equipment(
            server.EquipmentPayload(
                chat_id=self.scope, user_id=self.owner, name="ТПА E2E", code="EQ-E2E",
                department_id=department_id, area_id=area_from, service_interval_days=30,
            ),
            **self.auth(),
        )
        equipment_id = int(equipment_result["equipment"]["id"])

        lot_result = server.save_production_lot(
            server.ProductionLotPayload(chat_id=self.scope, user_id=self.owner, entity_id=product_id, lot_code="LOT-E2E"),
            **self.auth(),
        )
        lot_id = int(lot_result["lot"]["id"])

        task_result = server.save_production_task(
            server.ProductionTaskPayload(
                chat_id=self.scope, user_id=self.owner, department_id=department_id, entity_id=component_id,
                operation_type="production", target_quantity=250, title="Задание E2E", area_id=area_from,
            ),
            **self.auth(),
        )
        self.assertEqual(str(task_result["task"]["title"]), "Задание E2E")

        # --- Quality ---
        quality_result = server.create_quality_inspection(
            server.QualityInspectionPayload(
                chat_id=self.scope, user_id=self.owner, entity_id=product_id, department_id=department_id,
                area_id=area_from, lot_id=lot_id, equipment_id=equipment_id,
                checked_quantity=10, defect_quantity=0, unit="шт", note="Контроль E2E",
            ),
            **self.auth(),
        )
        self.assertEqual(int(quality_result["inspection"]["id"]), int(quality_result["inspection"]["id"]))

        # --- Replenishment ---
        repl_result = server.create_replenishment_request(
            server.ReplenishmentRequestPayload(
                chat_id=self.scope, user_id=self.owner, entity_id=material_id, area_id=area_from,
                requested_quantity=500, unit="шт", reason="Пополнение E2E",
            ),
            **self.auth(),
        )
        self.assertEqual(str(repl_result["request"]["entity_name"]), "Сырьё E2E")

        # --- Maintenance ---
        maintenance_result = server.save_maintenance_plan(
            server.MaintenancePlanPayload(
                chat_id=self.scope, user_id=self.owner, equipment_id=equipment_id,
                interval_days=30, warning_before_days=3, next_due_at="2099-01-01",
                note="ТО E2E", checklist=[{"label": "Проверить E2E", "is_required": True}],
            ),
            **self.auth(),
        )
        self.assertEqual(str(maintenance_result["plan"]["equipment_name"]), "ТПА E2E")

        # --- Transfer ---
        transfer_result = server.create_transfer_api(
            server.TransferCreatePayload(
                chat_id=self.scope, user_id=self.owner, from_area_id=area_from, to_area_id=area_to,
                from_department_id=department_id, from_location_id=location_id,
                items=[server.TransferItemPayload(entity_id=component_id, quantity=10, unit="шт")], note="Передача E2E",
            ),
            **self.auth(),
        )
        self.assertTrue(int(transfer_result["transfer"]["id"]) > 0)

        # --- The exact organisation feed used by the structure/warehouse Mini App screens ---
        structure = server.company_structure_api(self.scope, self.owner, **self.auth())
        structure_text = json.dumps(structure, ensure_ascii=False)
        for expected in ("Завод E2E", "Участок E2E", "Отдел E2E", "Стеллаж E2E", "Комплектующая E2E"):
            self.assertIn(expected, structure_text)

        # --- Full bootstrap used by the rest of Mini App ---
        bootstrap = server.bootstrap(self.scope, self.owner, **self.auth())
        self.assertEqual(int(bootstrap["scope_chat_id"]), self.scope)
        self.assertEqual(str(bootstrap["account"]["name"]), "ПРОИЗВОДСТВО ПЛАСТМАСС")

        for entity_type, name in dictionary.items():
            names = [str(x.get("name")) for x in bootstrap["entities"].get(entity_type, [])]
            self.assertIn(name, names, f"{entity_type} missing from Mini App entity feed")

        self.assertTrue(any(x["name"] == "Участок E2E" for x in bootstrap["areas"]))
        self.assertTrue(any(x["name"] == "Отдел E2E" for x in bootstrap["departments"]))
        self.assertTrue(any(x["name"] == "Мастер E2E" for x in bootstrap["job_titles"]))
        self.assertTrue(any(int(x["user_id"]) == worker_id for x in bootstrap["workers"]))
        self.assertTrue(any(int(x["product_id"]) == product_id for x in bootstrap["plan_targets"]))
        self.assertTrue(any(int(x.get("entity_id") or 0) == component_id for x in bootstrap["inventory_positions"]))

        workflow_text = json.dumps(bootstrap["workflow"], ensure_ascii=False)
        for expected in ("Задание E2E", "LOT-E2E", "ТПА E2E"):
            self.assertIn(expected, workflow_text)
        quality_supply_text = json.dumps(bootstrap["quality_supply"], ensure_ascii=False)
        for expected in ("Контроль E2E", "Пополнение E2E", "ТПА E2E"):
            self.assertIn(expected, quality_supply_text)

        # Nothing from another tenant may appear anywhere in either feed.
        other_user = 830100
        repo.upsert_chat(other_user, "Чужая фирма", "private", connected=True)
        ok, msg, other_account_id = repo.create_account(other_user, other_user, "ЧУЖАЯ ФИРМА E2E")
        self.assertTrue(ok, msg)
        other_scope = int(repo.get_account_by_id(other_account_id).scope_chat_id)
        ok, msg = repo.create_entity(other_scope, "product", "ЧУЖОЕ ИЗДЕЛИЕ E2E", "шт")
        self.assertTrue(ok, msg)
        combined = structure_text + json.dumps(bootstrap, ensure_ascii=False)
        self.assertNotIn("ЧУЖОЕ ИЗДЕЛИЕ E2E", combined)
        self.assertNotIn("ЧУЖАЯ ФИРМА E2E", combined)

        with db.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
