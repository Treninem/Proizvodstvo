from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "webapp" / "static"

OLD_MINI = "20260812g"
NEW_MINI = "20260813a"
OLD_BUILD = "83i"
NEW_BUILD = "84a"
OLD_BOT = "83"
NEW_BOT = "84"

SERVER = ROOT / "webapp" / "server.py"
INDEX = STATIC / "index.html"
OWNER = ROOT / "app" / "handlers" / "owner.py"
KEYBOARDS = ROOT / "app" / "keyboards.py"
SOURCE_APP = STATIC / f"app-{OLD_MINI}.js"
NEW_APP = STATIC / f"app-{NEW_MINI}.js"
ALIAS_APP = STATIC / "app.js"
LIVE_TEST = ROOT / "tests" / "test_step84_live_deployment_gate.py"


def _replace_exact(text: str, old: str, new: str, *, expected: int | None, label: str) -> str:
    count = text.count(old)
    if expected is not None and count != expected:
        # Idempotent reruns are valid after the promotion has already happened.
        if count == 0 and new in text:
            return text
        raise RuntimeError(f"{label}: expected {expected} occurrences of {old!r}, found {count}")
    if count == 0:
        if new in text:
            return text
        raise RuntimeError(f"{label}: neither old nor new marker was found")
    return text.replace(old, new)


def promote(*, check: bool = False) -> list[str]:
    changed: list[str] = []

    source = SOURCE_APP.read_text(encoding="utf-8")
    if "if(tab==='more')" not in source or "mobile-open" not in source:
        raise RuntimeError("Refusing release promotion: source Mini App does not contain the Step84 More-menu fix")
    release_marker = f"// Mini App release: {NEW_MINI}\n"
    released_source = source if source.startswith(release_marker) else release_marker + source

    expected_files: dict[Path, str] = {}

    server = SERVER.read_text(encoding="utf-8")
    server = _replace_exact(
        server,
        f'MINI_UI_VERSION = "{OLD_MINI}"',
        f'MINI_UI_VERSION = "{NEW_MINI}"',
        expected=1,
        label="server MINI_UI_VERSION",
    )
    # /health and /ready intentionally expose the same deployment build marker.
    build_old = f'"build": "{OLD_BUILD}"'
    build_new = f'"build": "{NEW_BUILD}"'
    old_count = server.count(build_old)
    if old_count:
        if old_count < 2:
            raise RuntimeError(f"server build marker: expected at least two occurrences, found {old_count}")
        server = server.replace(build_old, build_new)
    elif server.count(build_new) < 2:
        raise RuntimeError("server build marker: new marker is not present in both health endpoints")
    expected_files[SERVER] = server

    index = INDEX.read_text(encoding="utf-8")
    index = _replace_exact(
        index,
        f"app-{OLD_MINI}.js",
        f"app-{NEW_MINI}.js",
        expected=1,
        label="index active Mini App asset",
    )
    # Query string is only a secondary cache buster; the filename is authoritative.
    index = index.replace("?v=83", "?v=84")
    expected_files[INDEX] = index

    keyboards = KEYBOARDS.read_text(encoding="utf-8")
    keyboards = _replace_exact(
        keyboards,
        f'MINI_UI_VERSION = "{OLD_MINI}"',
        f'MINI_UI_VERSION = "{NEW_MINI}"',
        expected=1,
        label="Telegram Mini App button version",
    )
    expected_files[KEYBOARDS] = keyboards

    owner = OWNER.read_text(encoding="utf-8")
    owner = owner.replace(f"Версия бота: {OLD_BOT} · Mini App {OLD_MINI}", f"Версия бота: {NEW_BOT} · Backend {NEW_BUILD} · Mini App {NEW_MINI}")
    owner = owner.replace(
        f"Версия бота: {OLD_BOT}\\nMini App: {OLD_MINI}\\nАрхитектура: tenant-isolation v2",
        f"Версия бота: {NEW_BOT}\\nBackend: {NEW_BUILD}\\nMini App: {NEW_MINI}\\nАрхитектура: tenant-isolation v2",
    )
    if NEW_MINI not in owner or f"Backend: {NEW_BUILD}" not in owner:
        raise RuntimeError("owner version screen was not promoted")
    expected_files[OWNER] = owner

    live_test = LIVE_TEST.read_text(encoding="utf-8")
    live_test = live_test.replace(f'self.assertEqual(expected.build, "{OLD_BUILD}")', f'self.assertEqual(expected.build, "{NEW_BUILD}")')
    live_test = live_test.replace(f'self.assertEqual(expected.mini_ui_version, "{OLD_MINI}")', f'self.assertEqual(expected.mini_ui_version, "{NEW_MINI}")')
    live_test = live_test.replace(f'self.assertEqual(expected.app_asset, "app-{OLD_MINI}.js")', f'self.assertEqual(expected.app_asset, "app-{NEW_MINI}.js")')
    expected_files[LIVE_TEST] = live_test

    expected_files[NEW_APP] = released_source
    expected_files[ALIAS_APP] = released_source

    for path, expected_content in expected_files.items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == expected_content:
            continue
        changed.append(str(path.relative_to(ROOT)))
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected_content, encoding="utf-8", newline="\n")

    if check and changed:
        raise RuntimeError("Step84 release is not promoted: " + ", ".join(changed))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote Step84 to a cache-safe Mini App/backend release")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = promote(check=args.check)
    if args.check:
        print(f"STEP84 RELEASE_OK build={NEW_BUILD} mini={NEW_MINI} bot={NEW_BOT}")
    else:
        print("STEP84 RELEASE_PROMOTED " + (", ".join(changed) if changed else "already-current"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
