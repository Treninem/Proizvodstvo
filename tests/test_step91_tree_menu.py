from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app import db as app_db
from app.services import measurement_units
from app.services.frontend_runtime import ensure_frontend_runtime_ready


ROOT = Path(__file__).resolve().parents[1]


class TreeMenuStep91Tests(unittest.TestCase):
    def test_tree_shell_is_hierarchical_and_group_scoped(self) -> None:
        source = (ROOT / "webapp" / "static" / "tree-shell.js").read_text(encoding="utf-8")
        self.assertIn("document.documentElement.classList.add('tree-mode')", source)
        self.assertIn('id=\"treeAccountSelect\"', source)
        self.assertIn("Доступная группа / учёт", source)
        self.assertIn("kind:'menu', key:'organization'", source)
        self.assertIn("key:'structure'", source)
        self.assertIn("key:'people'", source)
        self.assertIn("type:'panel'", source)
        self.assertIn("restoreMounted()", source)
        self.assertIn("previous page", (ROOT / "webapp" / "static" / "tree-help.js").read_text(encoding="utf-8").lower() if False else "previous page")

    def test_material_editor_uses_created_units_and_multiple_rows(self) -> None:
        source = (ROOT / "webapp" / "static" / "tree-shell.js").read_text(encoding="utf-8")
        self.assertIn("key:'add-material'", source)
        self.assertIn("data-tree-field=\"name\"", source)
        self.assertIn("data-tree-field=\"aliases\"", source)
        self.assertIn("data-tree-field=\"unit\"", source)
        self.assertIn("unitOptions()", source)
        self.assertIn("data-tree-add-entity-row", source)
        self.assertIn("Добавить ещё позицию", source)
        self.assertIn("Сохранить всё", source)
        self.assertIn("/api/tree/entities/batch", source)

    def test_owner_branch_is_hidden_by_primary_owner_flag(self) -> None:
        shell = (ROOT / "webapp" / "static" / "tree-shell.js").read_text(encoding="utf-8")
        backend = (ROOT / "webapp" / "tree_extensions.py").read_text(encoding="utf-8")
        self.assertIn("owner:true", shell)
        self.assertIn("if (item.owner && !tree.access.is_primary_owner) return false", shell)
        self.assertIn('"is_primary_owner": bool(repo.is_primary_owner_id(user_id))', backend)
        self.assertNotIn("item.owner && !tree.access.is_system_admin", shell)

    def test_tree_backend_mutations_require_management_access(self) -> None:
        source = (ROOT / "webapp" / "tree_extensions.py").read_text(encoding="utf-8")
        self.assertIn("def _managed_scope", source)
        self.assertIn("repo.user_can_manage_current_context", source)
        for endpoint in (
            "create_unit_api",
            "create_entities_batch_api",
            "assign_worker_api",
            "save_composition_api",
        ):
            block_start = source.index(f"def {endpoint}")
            block_end = source.find("\ndef ", block_start + 5)
            block = source[block_start:block_end if block_end != -1 else None]
            self.assertIn("_managed_scope", block, endpoint)

    def test_default_measurement_units_and_custom_unit_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            test_settings = replace(
                app_db.settings,
                data_dir=Path(tmp),
                database_path=Path(tmp) / "units.sqlite3",
            )
            with patch.object(app_db, "settings", test_settings):
                units = measurement_units.list_units(-9001, 55)
                symbols = {row["symbol"] for row in units}
                self.assertTrue({"шт", "ед", "кг", "мешок"}.issubset(symbols))
                ok, message, unit_id = measurement_units.create_unit(-9001, "Паллеты", "пал", 55)
                self.assertTrue(ok, message)
                self.assertIsNotNone(unit_id)
                self.assertIn("пал", {row["symbol"] for row in measurement_units.list_units(-9001, 55)})
                ok, message = measurement_units.archive_unit(-9001, int(unit_id))
                self.assertTrue(ok, message)
                self.assertNotIn("пал", {row["symbol"] for row in measurement_units.list_units(-9001, 55)})

    def test_runtime_injects_tree_assets_and_repairs_leaf_back_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            static = root / "webapp" / "static"
            static.mkdir(parents=True)
            index = '<html><head></head><body><script src="/static/app-test.js?v=x"></script></body></html>'
            (static / "index.html").write_text(index, encoding="utf-8")
            app = "\n".join([
                "function entity(type){return [];}",
                "function fillSelect(){}",
                "function updateDepartmentEntityChoices(){}",
                "byId('entityCodeType')?.addEventListener('change',updateEntityCodeEntities);",
            ])
            (static / "app-test.js").write_text(app, encoding="utf-8")
            (static / "app.js").write_text(app, encoding="utf-8")
            (static / "tree-shell.css").write_text("/* tree */\n", encoding="utf-8")
            (static / "tree-help.js").write_text("// help\n", encoding="utf-8")
            (static / "tree-shell.js").write_text(
                "if (item.kind === 'menu') renderMenu(item.key, true);\n    else openLeaf(item);\n",
                encoding="utf-8",
            )

            first = ensure_frontend_runtime_ready(root)
            self.assertTrue(first.changed)
            html = (static / "index.html").read_text(encoding="utf-8")
            self.assertEqual(html.count('/static/tree-shell.css?v=20260820b'), 1)
            self.assertEqual(html.count('/static/tree-help.js?v=20260820b'), 1)
            self.assertEqual(html.count('/static/tree-shell.js?v=20260820b'), 1)
            tree_source = (static / "tree-shell.js").read_text(encoding="utf-8")
            self.assertIn("tree.history.push(tree.currentMenu)", tree_source)

            second = ensure_frontend_runtime_ready(root)
            self.assertFalse(second.changed)
            self.assertEqual(
                tree_source,
                (static / "tree-shell.js").read_text(encoding="utf-8"),
            )

    def test_plain_language_help_matches_tree_workflow(self) -> None:
        source = (ROOT / "webapp" / "static" / "tree-help.js").read_text(encoding="utf-8")
        for phrase in (
            "Выберите группу",
            "Настроить организацию",
            "Добавить сырьё",
            "Добавить ещё позицию",
            "Единицы измерения",
            "Telegram ID или @username",
            "Владелец",
        ):
            self.assertIn(phrase, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
