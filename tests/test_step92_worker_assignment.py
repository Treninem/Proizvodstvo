from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app import db as app_db
from app.handlers.job_assignment_v2 import normalized_assignment_command
from app.services import repository as repo
from app.services import worker_places


ROOT = Path(__file__).resolve().parents[1]


class Step92WorkerAssignmentTests(unittest.TestCase):
    def test_assignment_command_normalizes_plain_text_and_bot_mention(self) -> None:
        self.assertEqual(normalized_assignment_command("Назначить должность"), "назначить должность")
        self.assertEqual(normalized_assignment_command("  @ProChckbot   Назначить должность! "), "назначить должность")
        self.assertEqual(normalized_assignment_command("ДОЛЖНОСТЬ"), "должность")

    def test_main_wires_privacy_safe_group_command_before_legacy_flow(self) -> None:
        source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('BotCommand(command="role"', source)
        self.assertIn("BotCommandScopeAllGroupChats", source)
        self.assertIn("try_handle_reply_job_assignment_v2", source)
        self.assertIn("try_handle_workplace_intake", source)
        self.assertLess(source.index("try_handle_workplace_intake,"), source.index("try_handle_intake,"))
        self.assertLess(source.index("dp.include_router(job_assignment_v2.router)"), source.index("dp.include_router(start.router)"))

    def test_simplified_bot_menu_removes_duplicate_setup_and_worker_buttons(self) -> None:
        source = (ROOT / "app" / "handlers" / "bot_menu_v2.py").read_text(encoding="utf-8")
        menu_block = source[source.index("def bot_main_menu"):source.index("def _main_text")]
        self.assertIn("Открыть Mini App", menu_block)
        self.assertIn("Рабочие группы", menu_block)
        self.assertIn("Мои записи", menu_block)
        self.assertIn("Отчёты", menu_block)
        self.assertIn("Как пользоваться", menu_block)
        self.assertNotIn("Настроить организацию", menu_block)
        self.assertNotIn("Сотрудники", menu_block)
        self.assertIn("repo.is_primary_owner_id(user_id)", menu_block)

    def test_worker_can_have_multiple_physical_workplaces_and_operation_is_stamped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_settings = replace(
                app_db.settings,
                data_dir=Path(tmp),
                database_path=Path(tmp) / "step92.sqlite3",
            )
            with patch.object(app_db, "settings", test_settings):
                app_db.init_db()
                chat_id = -10092001
                user_id = 92002
                owner_id = 92003
                repo.upsert_chat(chat_id, "Рабочая группа", "supergroup", connected=True)
                ok, message = repo.create_area(chat_id, "Экструзия")
                self.assertTrue(ok, message)
                area = repo.list_areas(chat_id)[0]

                app_db.execute(
                    "INSERT INTO company_sites(chat_id,settlement,name,normalized,created_by) VALUES(?,?,?,?,?)",
                    (chat_id, "Муром", "Производство", "муром производство", owner_id),
                )
                site = app_db.fetchone("SELECT id FROM company_sites WHERE chat_id=?", (chat_id,))
                site_id = int(site["id"])
                app_db.execute("UPDATE areas SET site_id=? WHERE id=?", (site_id, int(area.id)))
                app_db.execute(
                    "INSERT INTO storage_locations(chat_id,site_id,area_id,name,normalized,created_by) VALUES(?,?,?,?,?,?)",
                    (chat_id, site_id, int(area.id), "Склад А", "склад а", owner_id),
                )
                app_db.execute(
                    "INSERT INTO storage_locations(chat_id,site_id,area_id,name,normalized,created_by) VALUES(?,?,?,?,?,?)",
                    (chat_id, site_id, int(area.id), "Склад Б", "склад б", owner_id),
                )

                ok, message = repo.create_job_title(chat_id, "Оператор", {"production": True})
                self.assertTrue(ok, message)
                job = repo.list_job_titles(chat_id)[0]
                repo.set_worker_job(chat_id, user_id, "Оператор 1", int(job["id"]))

                available = worker_places.list_available_workplaces(chat_id)
                self.assertEqual(len(available), 2)
                self.assertTrue(all("Муром" in str(item["label"]) for item in available))
                ok, message = worker_places.set_worker_workplaces(
                    chat_id,
                    user_id,
                    [str(item["key"]) for item in available],
                    owner_id,
                )
                self.assertTrue(ok, message)
                assigned = worker_places.list_worker_workplaces(chat_id, user_id)
                self.assertEqual(len(assigned), 2)

                stamped = worker_places.apply_workplace_to_operations(
                    [
                        {
                            "operation_type": "production",
                            "entity_type": "component",
                            "entity_id": 10,
                            "quantity": 5,
                            "unit": "шт",
                        }
                    ],
                    assigned[0],
                )[0]
                self.assertEqual(int(stamped["area_id"]), int(area.id))
                self.assertIsNotNone(stamped["storage_location_id"])
                self.assertIn("Муром", stamped["worker_workplace_label"])

    def test_multiple_workplaces_are_chosen_before_normal_accounting_confirmation(self) -> None:
        source = (ROOT / "app" / "handlers" / "workplace_intake.py").read_text(encoding="utf-8")
        self.assertIn("if len(places) == 1", source)
        self.assertIn("workplace_pending.create", source)
        self.assertIn("Куда отнести эту рабочую запись?", source)
        self.assertIn("worker_workplace_by_id", source)
        self.assertIn("accounting.create_pending", source)
        self.assertIn("apply_workplace_to_operations", source)

    def test_assignment_requires_one_or_more_workplaces_after_job_choice(self) -> None:
        source = (ROOT / "app" / "handlers" / "job_assignment_v2.py").read_text(encoding="utf-8")
        self.assertIn("Шаг 1 из 2", source)
        self.assertIn("Шаг 2 из 2", source)
        self.assertIn("Выбрать все", source)
        self.assertIn("Выберите хотя бы одно рабочее место", source)
        self.assertIn("worker_places.set_worker_workplaces", source)
        self.assertIn("Если мест несколько", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
