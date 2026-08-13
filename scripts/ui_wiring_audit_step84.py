from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "webapp" / "static"
HTML = STATIC / "index.html"
APP = STATIC / "app-20260812g.js"

# Some data-* attributes are intentionally handled by generic code rather than
# an action==='...' branch. Keep those explicit so the audit remains strict.
SPECIAL_TABS = {"more"}


def _values(pattern: str, text: str) -> set[str]:
    return {m.group(1) for m in re.finditer(pattern, text, flags=re.I)}


def audit_ui_wiring(html: str, js: str) -> list[str]:
    problems: list[str] = []

    html_ids = _values(r'\bid=["\']([^"\']+)["\']', html)
    page_ids = {value[5:] for value in html_ids if value.startswith("page-")}
    html_tabs = _values(r'\bdata-tab=["\']([^"\']+)["\']', html)
    html_actions = _values(r'\bdata-action=["\']([^"\']+)["\']', html)

    # A tab must either have a real page or be a deliberately special control.
    for tab in sorted(html_tabs):
        if tab not in page_ids and tab not in SPECIAL_TABS:
            problems.append(f"data-tab={tab!r} has no page-{tab} element")

    if "more" in html_tabs:
        more_marker = "if(tab==='more')"
        if more_marker not in js:
            problems.append("mobile data-tab='more' has no special JavaScript handler")
        if "mobile-open" not in js[js.find(more_marker): js.find(more_marker) + 1200] if more_marker in js else "":
            problems.append("mobile More handler does not open the .tabs drawer")

    # Every static HTML data-action must be connected to a JS action branch.
    # Dynamic list-row actions are checked separately by runtime E2E tests.
    for action in sorted(html_actions):
        single = f"action==='{action}'"
        double = f'action==="{action}"'
        if single not in js and double not in js:
            problems.append(f"data-action={action!r} has no action handler")

    # Every statically referenced getElementById/byId target that is not built
    # dynamically should exist in the HTML. byId is intentionally null-safe,
    # but a typo would silently turn a working-looking control into a no-op.
    referenced_ids = _values(r'\bbyId\(["\']([^"\']+)["\']\)', js)
    optional_runtime_ids = {
        # These are guarded compatibility hooks and are allowed to be absent.
        "connectionBanner",
    }
    for ref in sorted(referenced_ids - optional_runtime_ids):
        if ref not in html_ids:
            problems.append(f"JavaScript references missing element id={ref!r}")

    # Primary mobile navigation is a product invariant.
    expected_primary = {
        "production": "work",
        "stock": "overview",
        "plan": "plan",
        "reports": "reports",
        "more": "more",
    }
    for primary, tab in expected_primary.items():
        pattern = rf'<button[^>]*data-tab=["\']{re.escape(tab)}["\'][^>]*data-primary-nav=["\']{re.escape(primary)}["\']|<button[^>]*data-primary-nav=["\']{re.escape(primary)}["\'][^>]*data-tab=["\']{re.escape(tab)}["\']'
        if not re.search(pattern, html, flags=re.I):
            problems.append(f"primary mobile navigation {primary!r} -> {tab!r} is missing")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit static Mini App controls against their JavaScript wiring")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    problems = audit_ui_wiring(HTML.read_text(encoding="utf-8"), APP.read_text(encoding="utf-8"))
    if problems:
        print(f"STEP84 UI_WIRING_FAILED count={len(problems)}")
        for problem in problems:
            print(f" - {problem}")
        return 1
    if not args.quiet:
        print("STEP84 UI_WIRING_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
