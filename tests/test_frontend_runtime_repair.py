from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.frontend_runtime import ensure_frontend_runtime_ready


class FrontendRuntimeRepairTests(unittest.TestCase):
    def test_entity_code_initializer_is_repaired_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            static = root / "webapp" / "static"
            static.mkdir(parents=True)
            (static / "index.html").write_text(
                '<script src="/static/app-test.js?v=fixture"></script>',
                encoding="utf-8",
            )
            broken = "\n".join(
                [
                    "const byId=(id)=>document.getElementById(id);",
                    "function val(id){return byId(id)?.value||'';}",
                    "function entity(type){return [];}",
                    "function fillSelect(){}",
                    "function updateDepartmentEntityChoices(){}",
                    "byId('entityCodeType')?.addEventListener('change',updateEntityCodeEntities);",
                ]
            )
            (static / "app-test.js").write_text(broken, encoding="utf-8")
            (static / "app.js").write_text(broken, encoding="utf-8")

            first = ensure_frontend_runtime_ready(root)
            self.assertTrue(first.changed)
            self.assertEqual(first.active_asset, "app-test.js")

            repaired = (static / "app-test.js").read_text(encoding="utf-8")
            alias = (static / "app.js").read_text(encoding="utf-8")
            self.assertEqual(repaired.count("function updateEntityCodeEntities(){"), 1)
            self.assertEqual(repaired, alias)

            second = ensure_frontend_runtime_ready(root)
            self.assertFalse(second.changed)
            self.assertEqual(
                repaired,
                (static / "app-test.js").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
