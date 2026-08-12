from __future__ import annotations

import os
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("BOT_TOKEN", "123456789:TEST_TOKEN_FOR_CI_ONLY_1234567890")
os.environ.setdefault("OWNER_TELEGRAM_ID", "2097006037")
os.environ.setdefault("PUBLIC_BASE_URL", "https://example.invalid")

from app import db
from app.config import settings
from app.services import repository as repo


class AccountSyncAndProductVisibilityTests(unittest.TestCase):
    def test_bot_created_product_is_visible_to_miniapp_entity_feed(self):
        from webapp import server

        with tempfile.TemporaryDirectory() as tmp:
            test_settings = replace(settings, data_dir=Path(tmp), database_path=Path(tmp) / "sync.sqlite3")
            owner_id = 2097006037
            with patch.object(db, "settings", test_settings), patch.object(repo, "settings", test_settings):
                db.init_db()
                ok, message, account_id = repo.create_account(owner_id, owner_id, "ПРОИЗВОДСТВО ПЛАСТМАСС")
                self.assertTrue(ok, message)
                account = repo.get_account_by_id(account_id)
                self.assertIsNotNone(account)
                ok, message = repo.create_entity(owner_id, "product", "Фонтан для шаров 13")
                self.assertTrue(ok, message)
                products = server._entity_list(account.scope_chat_id, owner_id).get("product", [])
                self.assertIn("Фонтан для шаров 13", [str(item.get("name")) for item in products])

    def test_current_bot_account_has_priority_over_stale_button_scope(self):
        html = (ROOT / "webapp/static/index.html").read_text(encoding="utf-8")
        version = re.search(r'app-(20\d{6}[a-z])\.js', html).group(1)
        js = (ROOT / f"webapp/static/app-{version}.js").read_text(encoding="utf-8")
        active_pos = js.find("if(active && allowed.has(active)) chosen=active;")
        stale_pos = js.find("requestedChatId && allowed.has(String(requestedChatId))")
        self.assertGreaterEqual(active_pos, 0, "active account selection is missing")
        self.assertGreater(stale_pos, active_pos, "stale URL chat_id still overrides current bot account")

    def test_new_miniapp_buttons_are_not_pinned_to_scope_chat_id(self):
        from app import keyboards
        with patch.object(keyboards.settings, "public_base_url", "https://example.invalid"):
            url = keyboards.miniapp_url(2097006037)
        self.assertNotIn("chat_id=", url)


if __name__ == "__main__":
    unittest.main()
