from __future__ import annotations

import ast
import hashlib
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Step85ReleaseContractTests(unittest.TestCase):
    def test_owner_handler_is_python_only_and_compiles(self) -> None:
        path = ROOT / "app" / "handlers" / "owner.py"
        source = path.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertNotIn("</html>", source.lower())
        self.assertNotIn("<article", source.lower())
        self.assertIn("Backend: 85", source)
        self.assertIn("Mini App: 20260816a", source)

    def test_active_mini_app_has_entity_code_initializer_once(self) -> None:
        static = ROOT / "webapp" / "static"
        index = (static / "index.html").read_text(encoding="utf-8")
        match = re.search(r'/static/(app-[^"?]+\.js)', index)
        self.assertIsNotNone(match)
        asset = match.group(1)
        self.assertEqual(asset, "app-20260816a.js")
        source = (static / asset).read_text(encoding="utf-8")
        self.assertEqual(source.count("function updateEntityCodeEntities(){"), 1)
        self.assertIn("addEventListener('change',updateEntityCodeEntities)", source)
        self.assertIn('const MINI_APP_VERSION="20260816a";', source)
        self.assertEqual(
            hashlib.sha256((static / asset).read_bytes()).hexdigest(),
            hashlib.sha256((static / "app.js").read_bytes()).hexdigest(),
        )

    def test_backend_button_and_container_versions_match(self) -> None:
        server = (ROOT / "webapp" / "server.py").read_text(encoding="utf-8")
        keyboards = (ROOT / "app" / "keyboards.py").read_text(encoding="utf-8")
        docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('MINI_UI_VERSION = "20260816a"', server)
        self.assertEqual(set(re.findall(r'"build"\s*:\s*"([^"]+)"', server)), {"85"})
        self.assertIn('MINI_UI_VERSION = "20260816a"', keyboards)
        self.assertIn('LABEL org.opencontainers.image.version="85-mini-20260816a"', docker)


if __name__ == "__main__":
    unittest.main()
