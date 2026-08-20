from __future__ import annotations

import unittest
from pathlib import Path

from webapp.extensions import install_extensions
from webapp import server


ROOT = Path(__file__).resolve().parents[1]


class StorageManagementStep86Tests(unittest.TestCase):
    def test_bot_storage_callback_is_handled_before_generic_setup(self) -> None:
        main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        management_source = (ROOT / "app" / "handlers" / "management.py").read_text(encoding="utf-8")
        self.assertLess(
            main_source.index("dp.include_router(management.router)"),
            main_source.index("dp.include_router(setup.router)"),
        )
        self.assertIn('@router.callback_query(F.data == "wizard:destination")', management_source)
        self.assertIn("repo.create_destination(chat_id, text, \"storage\")", management_source)
        self.assertIn("repo.update_destination(", management_source)
        self.assertIn("repo.update_job_title_record(", management_source)
        self.assertIn("manage_account_rename", management_source)

    def test_miniapp_rename_routes_register_exactly_once(self) -> None:
        install_extensions()
        install_extensions()
        paths = [getattr(route, "path", "") for route in server.app.routes]
        self.assertEqual(paths.count("/api/extensions/account/rename"), 1)
        self.assertEqual(paths.count("/api/extensions/storage-location/rename"), 1)
        self.assertEqual(paths.count("/api/extensions/health"), 1)

    def test_miniapp_ui_extension_contains_required_controls(self) -> None:
        source = (ROOT / "webapp" / "static" / "app-extensions.js").read_text(encoding="utf-8")
        self.assertIn("Переименовать учёт", source)
        self.assertIn("data-extension-storage-rename", source)
        self.assertIn("/api/extensions/account/rename", source)
        self.assertIn("/api/extensions/storage-location/rename", source)


if __name__ == "__main__":
    unittest.main()
