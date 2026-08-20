from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from app.handlers.component_picker import _selection_keyboard, _selected_in_order


ROOT = Path(__file__).resolve().parents[1]


class ComponentPickerTests(unittest.TestCase):
    def _components(self, count: int):
        return [SimpleNamespace(id=i, name=f"Комплектующая {i}", default_unit="шт") for i in range(1, count + 1)]

    def test_keyboard_allows_multiple_and_select_all(self):
        components = self._components(25)
        selected = {1, 3, 7}
        keyboard = _selection_keyboard(components, selected, 0, {1: 2.0})
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        texts = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertIn("components:picker:all", callbacks)
        self.assertIn("components:picker:none", callbacks)
        self.assertIn("components:picker:next", callbacks)
        self.assertIn("components:picker:page:1", callbacks)
        self.assertTrue(any(text.startswith("✅ Комплектующая 1") for text in texts))
        self.assertTrue(any("выбрано 3" in text for text in texts))

    def test_selected_order_can_include_every_created_component(self):
        components = self._components(50)
        selected = {item.id for item in components}
        ordered = _selected_in_order(components, selected)
        self.assertEqual(ordered, list(range(1, 51)))

    def test_picker_is_wired_before_old_setup_handler(self):
        source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertLess(source.index("dp.include_router(component_picker.router)"), source.index("dp.include_router(setup.router)"))
        self.assertLess(source.index("try_handle_component_picker_message,"), source.index("try_handle_wizard_message,"))

    def test_picker_never_requires_component_names_to_be_typed(self):
        source = (ROOT / "app" / "handlers" / "component_picker.py").read_text(encoding="utf-8")
        self.assertIn("Можно выбрать одну, несколько или сразу все", source)
        self.assertIn("components:picker:toggle:", source)
        self.assertNotIn("create_missing=True", source)
        self.assertNotIn("Введите комплектующие, которые нужно добавить", source)


if __name__ == "__main__":
    unittest.main()
