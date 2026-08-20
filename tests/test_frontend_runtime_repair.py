from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.frontend_runtime import ensure_frontend_runtime_ready


class FrontendRuntimeRepairTests(unittest.TestCase):
    def _write_fixture(self, static: Path, *, with_extension: bool = False) -> str:
        index = '<body><script src="/static/app-test.js?v=fixture"></script></body>'
        (static / "index.html").write_text(index, encoding="utf-8")
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
        if with_extension:
            (static / "app-extensions.js").write_text("// extension fixture\n", encoding="utf-8")
        return broken

    def test_entity_code_initializer_is_repaired_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            static = root / "webapp" / "static"
            static.mkdir(parents=True)
            self._write_fixture(static)

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

    def test_optional_extension_script_is_injected_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            static = root / "webapp" / "static"
            static.mkdir(parents=True)
            self._write_fixture(static, with_extension=True)

            first = ensure_frontend_runtime_ready(root)
            self.assertTrue(first.changed)
            index = (static / "index.html").read_text(encoding="utf-8")
            marker = '/static/app-extensions.js?v=20260820a'
            self.assertEqual(index.count(marker), 1)

            second = ensure_frontend_runtime_ready(root)
            self.assertFalse(second.changed)
            index2 = (static / "index.html").read_text(encoding="utf-8")
            self.assertEqual(index2.count(marker), 1)


if __name__ == "__main__":
    unittest.main()
