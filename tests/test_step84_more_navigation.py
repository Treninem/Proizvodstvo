from __future__ import annotations

import unittest

from scripts.apply_step84_more_nav_fix import CORE, OLD, TARGETS, build_patched_source


class Step84MoreNavigationTests(unittest.TestCase):
    def test_audited_core_has_single_generic_tab_handler(self):
        core = CORE.read_text(encoding="utf-8")
        self.assertEqual(core.count(OLD), 1)

    def test_patch_handles_more_before_generic_show_tab(self):
        patched = build_patched_source(CORE.read_text(encoding="utf-8"))
        marker = "if(tab==='more')"
        generic = "if(tab){showTab(tab);"
        self.assertIn(marker, patched)
        self.assertIn("classList.toggle('mobile-open',opening)", patched)
        self.assertIn("aria-expanded", patched)
        self.assertLess(patched.index(marker), patched.index(generic, patched.index(marker)))

    def test_generated_targets_are_declared(self):
        self.assertEqual({path.name for path in TARGETS}, {"app-20260812g.js", "app.js"})


if __name__ == "__main__":
    unittest.main()
