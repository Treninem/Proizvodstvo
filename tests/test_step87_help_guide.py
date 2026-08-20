from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.frontend_runtime import ensure_frontend_runtime_ready


ROOT = Path(__file__).resolve().parents[1]


class HelpGuideTests(unittest.TestCase):
    def test_bot_guide_covers_current_user_workflows_without_technical_details(self) -> None:
        source = (ROOT / "app" / "handlers" / "help_guide.py").read_text(encoding="utf-8")
        for phrase in (
            "Места хранения",
            "Название должности можно изменить",
            "Mini App",
            "Задания, заявки и смены",
            "Критические остатки",
            "Качество, оборудование и критические остатки",
            "Отчёты, исправления и названия",
        ):
            self.assertIn(phrase, source)
        visible_help = source.split("GUIDE_PAGES", 1)[1].split("GUIDE_LABELS", 1)[0].lower()
        for technical_word in ("endpoint", "database", "токен", "api", "http", "sql"):
            self.assertNotIn(technical_word, visible_help)

    def test_miniapp_guide_covers_bot_and_panel_workflows(self) -> None:
        source = (ROOT / "webapp" / "static" / "help-guide.js").read_text(encoding="utf-8")
        for phrase in (
            "Как пользоваться",
            "Работа через бота",
            "Рабочий ввод в Mini App",
            "Склад и инвентаризация",
            "Сотрудники, должности и права",
            "Задания и заявки между отделами",
            "Смены и передача работы",
            "Критические остатки",
            "Качество, снабжение и оборудование",
            "Если допустили ошибку",
        ):
            self.assertIn(phrase, source)
        lower = source.lower()
        for technical_word in ("endpoint", "database", "токен", "sql"):
            self.assertNotIn(technical_word, lower)

    def test_runtime_injects_help_script_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            static = root / "webapp" / "static"
            static.mkdir(parents=True)
            (static / "index.html").write_text(
                '<body><script src="/static/app-test.js?v=x"></script></body>',
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
            (static / "help-guide.js").write_text("// help", encoding="utf-8")

            first = ensure_frontend_runtime_ready(root)
            self.assertTrue(first.changed)
            html = (static / "index.html").read_text(encoding="utf-8")
            self.assertEqual(html.count('/static/help-guide.js?v=20260820b'), 1)

            second = ensure_frontend_runtime_ready(root)
            self.assertFalse(second.changed)
            html2 = (static / "index.html").read_text(encoding="utf-8")
            self.assertEqual(html2.count('/static/help-guide.js?v=20260820b'), 1)


if __name__ == "__main__":
    unittest.main()
