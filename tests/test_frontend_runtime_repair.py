from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from app.services.frontend_runtime import ensure_frontend_runtime_ready


ROOT = Path(__file__).resolve().parents[1]


class FrontendRuntimeRepairTests(unittest.TestCase):
    def test_active_entity_code_initializer_is_repaired_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            static = root / "webapp" / "static"
            static.mkdir(parents=True)

            for name in ("index.html", "app-20260813a.js", "app.js"):
                shutil.copy2(ROOT / "webapp" / "static" / name, static / name)

            active = static / "app-20260813a.js"
            before = active.read_text(encoding="utf-8")
            self.assertIn("updateEntityCodeEntities", before)
            self.assertNotIn("function updateEntityCodeEntities(){", before)

            first = ensure_frontend_runtime_ready(root)
            self.assertTrue(first.changed)
            self.assertEqual(first.active_asset, "app-20260813a.js")

            repaired = active.read_text(encoding="utf-8")
            alias = (static / "app.js").read_text(encoding="utf-8")
            self.assertEqual(repaired.count("function updateEntityCodeEntities(){"), 1)
            self.assertEqual(repaired, alias)

            second = ensure_frontend_runtime_ready(root)
            self.assertFalse(second.changed)
            self.assertEqual(
                repaired,
                active.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
