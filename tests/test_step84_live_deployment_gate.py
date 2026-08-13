from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from scripts.live_deployment_gate_step84 import ROOT, expected_deployment


class Step84LiveDeploymentGateTests(unittest.TestCase):
    def test_expected_deployment_matches_current_runtime(self):
        expected = expected_deployment()
        self.assertEqual(expected.build, "84a")
        self.assertEqual(expected.mini_ui_version, "20260813a")
        self.assertEqual(expected.app_asset, "app-20260813a.js")
        self.assertEqual(expected.style_asset, "style-20260812a.css")

    def test_frontend_aliases_are_byte_identical_to_active_assets(self):
        expected = expected_deployment()
        static = ROOT / "webapp" / "static"
        self.assertEqual(
            hashlib.sha256((static / expected.app_asset).read_bytes()).hexdigest(),
            hashlib.sha256((static / "app.js").read_bytes()).hexdigest(),
        )
        self.assertEqual(
            hashlib.sha256((static / expected.style_asset).read_bytes()).hexdigest(),
            hashlib.sha256((static / "style.css").read_bytes()).hexdigest(),
        )

    def test_generated_active_app_handles_more_as_drawer(self):
        expected = expected_deployment()
        source = (ROOT / "webapp" / "static" / expected.app_asset).read_text(encoding="utf-8")
        self.assertIn("Mini App release: 20260813a", source)
        self.assertIn('const MINI_APP_VERSION="20260813a";', source)
        self.assertNotIn('const MINI_APP_VERSION="20260812g";', source)
        self.assertIn("if(tab==='more')", source)
        self.assertIn("classList.toggle('mobile-open',opening)", source)
        self.assertIn("aria-expanded", source)
        self.assertLess(source.index("if(tab==='more')"), source.index("if(tab){showTab(tab);", source.index("if(tab==='more')")))

    def test_index_uses_new_filename_not_mutated_immutable_asset(self):
        html = (ROOT / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('/static/app-20260813a.js', html)
        self.assertNotIn('/static/app-20260812g.js', html)
        self.assertTrue((ROOT / "webapp" / "static" / "app-20260813a.js").is_file())

    def test_telegram_button_uses_current_mini_app_version(self):
        keyboards = (ROOT / "app" / "keyboards.py").read_text(encoding="utf-8")
        self.assertIn('MINI_UI_VERSION = "20260813a"', keyboards)
        self.assertNotIn('MINI_UI_VERSION = "20260812g"', keyboards)
        self.assertIn('return base + f"/mini?v={MINI_UI_VERSION}"', keyboards)

    def test_owner_version_identifies_exact_runtime(self):
        owner = (ROOT / "app" / "handlers" / "owner.py").read_text(encoding="utf-8")
        self.assertIn("Версия бота: 84", owner)
        self.assertIn("Backend: 84a", owner)
        self.assertIn("Mini App: 20260813a", owner)

    def test_manifest_opens_canonical_mini_route(self):
        import json

        manifest = json.loads((Path(ROOT) / "webapp" / "static" / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("start_url"), "/mini")


if __name__ == "__main__":
    unittest.main()
