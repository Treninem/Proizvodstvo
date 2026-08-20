from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services import telegram_users
from app.services.frontend_runtime import ensure_frontend_runtime_ready


ROOT = Path(__file__).resolve().parents[1]


class MiniAppNavigationTests(unittest.TestCase):
    def test_menu_keeps_all_major_pages_and_uses_groups(self) -> None:
        source = (ROOT / "webapp" / "static" / "menu-navigation.js").read_text(encoding="utf-8")
        for phrase in (
            "Производство",
            "Склад",
            "Планирование и управление",
            "Качество и оборудование",
            "Сотрудники и доступ",
            "Настройка",
            "Справочники и составы",
            "Как пользоваться",
        ):
            self.assertIn(phrase, source)
        for tab in (
            "work", "overview", "production", "materials", "assembly", "movement",
            "shipment", "returns", "inventory", "transfers", "risks", "plan", "reports",
            "workflow", "shifts", "inbox", "control", "quality", "team", "departments",
            "area-access", "organization", "places", "security", "catalog", "help",
        ):
            self.assertIn(f"tab:'{tab}'", source)

    def test_old_scrolling_navigation_is_hidden(self) -> None:
        source = (ROOT / "webapp" / "static" / "miniapp-ux.css").read_text(encoding="utf-8")
        self.assertIn(".tabs,", source)
        self.assertIn(".mobile-nav.primary-nav{display:none!important}", source)
        self.assertIn(".app-menu-group", source)
        self.assertIn(".app-menu-submenu", source)

    def test_management_panel_has_username_assignment_and_full_setup(self) -> None:
        source = (ROOT / "webapp" / "static" / "management-panel.js").read_text(encoding="utf-8")
        for phrase in (
            "Telegram ID или @username",
            "Выберите созданную должность",
            "/api/extensions/workers/assign",
            "/api/extensions/catalog/area",
            "/api/extensions/catalog/entity",
            "/api/extensions/catalog/composition",
            "Выбрать все",
            "Создать учёт",
        ):
            self.assertIn(phrase, source)

    def test_extension_routes_cover_missing_setup_actions(self) -> None:
        source = (ROOT / "webapp" / "extensions.py").read_text(encoding="utf-8")
        for path in (
            "/api/extensions/accounts/create",
            "/api/extensions/catalog/snapshot",
            "/api/extensions/catalog/area",
            "/api/extensions/catalog/entity",
            "/api/extensions/catalog/composition",
            "/api/extensions/workers/assign",
        ):
            self.assertIn(path, source)
        self.assertIn("resolve_user_ref", source)
        self.assertIn("save_worker_record", source)
        self.assertIn("set_product_components", source)

    def test_telegram_username_directory_resolves_username_and_numeric_id(self) -> None:
        telegram_users.remember_user(991234567, "Step90_Test_User", "Step 90 User", -100123)
        by_username = telegram_users.resolve_user_ref("@step90_test_user")
        self.assertIsNotNone(by_username)
        self.assertEqual(int(by_username["user_id"]), 991234567)
        by_id = telegram_users.resolve_user_ref("991234567")
        self.assertIsNotNone(by_id)
        self.assertEqual(int(by_id["user_id"]), 991234567)
        unknown_numeric = telegram_users.resolve_user_ref("991234568")
        self.assertEqual(int(unknown_numeric["user_id"]), 991234568)

    def test_runtime_injects_menu_management_and_styles_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            static = root / "webapp" / "static"
            static.mkdir(parents=True)
            (static / "index.html").write_text(
                '<html><head></head><body><script src="/static/app-test.js?v=x"></script></body></html>',
                encoding="utf-8",
            )
            app = "\n".join(
                [
                    "function updateEntityCodeEntities(){}",
                    "function updateDepartmentEntityChoices(){}",
                    "byId('entityCodeType')?.addEventListener('change',updateEntityCodeEntities);",
                ]
            )
            (static / "app-test.js").write_text(app, encoding="utf-8")
            (static / "app.js").write_text(app, encoding="utf-8")
            (static / "management-panel.js").write_text("// management", encoding="utf-8")
            (static / "menu-navigation.js").write_text("// menu", encoding="utf-8")
            (static / "miniapp-ux.css").write_text("/* ux */", encoding="utf-8")

            first = ensure_frontend_runtime_ready(root)
            self.assertTrue(first.changed)
            html = (static / "index.html").read_text(encoding="utf-8")
            for marker in (
                "/static/management-panel.js?v=20260820a",
                "/static/menu-navigation.js?v=20260820a",
                "/static/miniapp-ux.css?v=20260820a",
            ):
                self.assertEqual(html.count(marker), 1)

            second = ensure_frontend_runtime_ready(root)
            self.assertFalse(second.changed)
            html = (static / "index.html").read_text(encoding="utf-8")
            for marker in (
                "/static/management-panel.js?v=20260820a",
                "/static/menu-navigation.js?v=20260820a",
                "/static/miniapp-ux.css?v=20260820a",
            ):
                self.assertEqual(html.count(marker), 1)


if __name__ == "__main__":
    unittest.main()
