from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "webapp" / "server.py"
STATIC = ROOT / "webapp" / "static"
INDEX = STATIC / "index.html"
USER_AGENT = "Proizvodstvo-Step84-Live-Gate/1.0"


@dataclass(frozen=True)
class ExpectedDeployment:
    build: str
    mini_ui_version: str
    app_asset: str
    style_asset: str
    app_sha256: str
    style_sha256: str
    alias_app_sha256: str
    alias_style_sha256: str
    manifest_sha256: str


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: bytes
    headers: dict[str, str]
    url: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def expected_deployment() -> ExpectedDeployment:
    server_text = SERVER.read_text(encoding="utf-8")
    index_text = INDEX.read_text(encoding="utf-8")

    builds = set(re.findall(r'"build"\s*:\s*"([^"]+)"', server_text))
    if len(builds) != 1:
        raise RuntimeError(f"Expected exactly one backend build marker, found: {sorted(builds)}")
    build = next(iter(builds))

    ui_match = re.search(r'MINI_UI_VERSION\s*=\s*"([^"]+)"', server_text)
    if not ui_match:
        raise RuntimeError("MINI_UI_VERSION was not found in webapp/server.py")
    mini_ui_version = ui_match.group(1)

    app_match = re.search(r'<script[^>]+src="/static/(app-[^"?]+\.js)(?:\?[^\"]*)?"', index_text)
    if not app_match:
        raise RuntimeError("Versioned Mini App JavaScript was not found in index.html")
    app_asset = app_match.group(1)

    style_match = re.search(r'<link[^>]+href="/static/(style-[^"?]+\.css)(?:\?[^\"]*)?"', index_text)
    if not style_match:
        raise RuntimeError("Versioned Mini App stylesheet was not found in index.html")
    style_asset = style_match.group(1)

    expected_app = f"app-{mini_ui_version}.js"
    if app_asset != expected_app:
        raise RuntimeError(
            f"MINI_UI_VERSION={mini_ui_version}, but index.html references {app_asset}; expected {expected_app}"
        )

    app_path = STATIC / app_asset
    style_path = STATIC / style_asset
    alias_app = STATIC / "app.js"
    alias_style = STATIC / "style.css"
    manifest = STATIC / "manifest.webmanifest"
    for path in (app_path, style_path, alias_app, alias_style, manifest):
        if not path.is_file():
            raise RuntimeError(f"Required deployment file is missing: {path.relative_to(ROOT)}")

    return ExpectedDeployment(
        build=build,
        mini_ui_version=mini_ui_version,
        app_asset=app_asset,
        style_asset=style_asset,
        app_sha256=_sha256_file(app_path),
        style_sha256=_sha256_file(style_path),
        alias_app_sha256=_sha256_file(alias_app),
        alias_style_sha256=_sha256_file(alias_style),
        manifest_sha256=_sha256_file(manifest),
    )


def _fetch(base_url: str, path: str, timeout: float) -> HttpResult:
    base = base_url.rstrip("/") + "/"
    url = urllib.parse.urljoin(base, path.lstrip("/"))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/html,*/*",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpResult(
                status=int(response.status),
                body=response.read(),
                headers={key.lower(): value for key, value in response.headers.items()},
                url=response.geturl(),
            )
    except urllib.error.HTTPError as exc:
        return HttpResult(
            status=int(exc.code),
            body=exc.read(),
            headers={key.lower(): value for key, value in exc.headers.items()},
            url=exc.geturl(),
        )


def _json(result: HttpResult, label: str) -> dict:
    try:
        value = json.loads(result.body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{label}: response is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label}: JSON response is not an object")
    return value


def check_once(base_url: str, expected: ExpectedDeployment, timeout: float = 15.0) -> list[str]:
    problems: list[str] = []

    try:
        health = _fetch(base_url, "/health", timeout)
        if health.status != 200:
            problems.append(f"/health HTTP {health.status}, expected 200")
        else:
            payload = _json(health, "/health")
            if payload.get("status") != "ok":
                problems.append(f"/health status={payload.get('status')!r}, expected 'ok'")
            if str(payload.get("build") or "") != expected.build:
                problems.append(
                    f"/health build={payload.get('build')!r}, expected {expected.build!r}"
                )
    except Exception as exc:  # noqa: BLE001
        problems.append(f"/health request failed: {exc}")

    try:
        ready = _fetch(base_url, "/ready", timeout)
        if ready.status != 200:
            problems.append(f"/ready HTTP {ready.status}, expected 200")
        else:
            payload = _json(ready, "/ready")
            if payload.get("status") != "ready":
                problems.append(f"/ready status={payload.get('status')!r}, expected 'ready'")
            if not bool(payload.get("database")):
                problems.append("/ready database is not true")
            if str(payload.get("build") or "") != expected.build:
                problems.append(
                    f"/ready build={payload.get('build')!r}, expected {expected.build!r}"
                )
    except Exception as exc:  # noqa: BLE001
        problems.append(f"/ready request failed: {exc}")

    mini_html = ""
    try:
        mini = _fetch(base_url, "/mini", timeout)
        if mini.status != 200:
            problems.append(f"/mini HTTP {mini.status}, expected 200 without redirect")
        else:
            final_path = urllib.parse.urlsplit(mini.url).path.rstrip("/") or "/"
            if final_path != "/mini":
                problems.append(f"/mini changed URL to {mini.url!r}; redirects are forbidden")
            mini_version = mini.headers.get("x-mini-app-version", "")
            if mini_version != expected.mini_ui_version:
                problems.append(
                    f"/mini X-Mini-App-Version={mini_version!r}, expected {expected.mini_ui_version!r}"
                )
            cache_control = mini.headers.get("cache-control", "").lower()
            if "no-store" not in cache_control:
                problems.append(f"/mini Cache-Control={cache_control!r}, expected no-store")
            mini_html = mini.body.decode("utf-8")
            if f"/static/{expected.app_asset}" not in mini_html:
                problems.append(f"/mini HTML does not reference {expected.app_asset}")
            if f"/static/{expected.style_asset}" not in mini_html:
                problems.append(f"/mini HTML does not reference {expected.style_asset}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"/mini request failed: {exc}")

    binary_checks = (
        (f"/static/{expected.app_asset}", expected.app_sha256, expected.app_asset),
        (f"/static/{expected.style_asset}", expected.style_sha256, expected.style_asset),
        ("/static/app.js", expected.alias_app_sha256, "app.js alias"),
        ("/static/style.css", expected.alias_style_sha256, "style.css alias"),
        ("/static/manifest.webmanifest", expected.manifest_sha256, "manifest.webmanifest"),
    )
    for path, local_digest, label in binary_checks:
        try:
            result = _fetch(base_url, path, timeout)
            if result.status != 200:
                problems.append(f"{path} HTTP {result.status}, expected 200")
                continue
            live_digest = _sha256_bytes(result.body)
            if live_digest != local_digest:
                problems.append(
                    f"{label} SHA-256 differs: live={live_digest[:16]} local={local_digest[:16]}"
                )
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{path} request failed: {exc}")

    try:
        unauthorized = _fetch(base_url, "/api/accounts?user_id=1", timeout)
        if unauthorized.status != 403:
            problems.append(
                f"unauthorized /api/accounts HTTP {unauthorized.status}, expected 403"
            )
    except Exception as exc:  # noqa: BLE001
        problems.append(f"unauthorized /api/accounts request failed: {exc}")

    if mini_html:
        try:
            manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
            if manifest.get("start_url") != "/mini":
                problems.append(
                    f"local manifest start_url={manifest.get('start_url')!r}, expected '/mini'"
                )
        except Exception as exc:  # noqa: BLE001
            problems.append(f"local manifest validation failed: {exc}")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify that Bothost serves the exact current GitHub main runtime")
    parser.add_argument("--base-url", default="https://procontrol.bothost.tech")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--delay", type=float, default=20.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    attempts = max(1, int(args.attempts))
    expected = expected_deployment()
    print(
        "STEP84 expected "
        f"build={expected.build} mini={expected.mini_ui_version} "
        f"app={expected.app_asset} app_sha256={expected.app_sha256[:16]}..."
    )

    last_problems: list[str] = []
    for attempt in range(1, attempts + 1):
        last_problems = check_once(args.base_url, expected, timeout=max(1.0, args.timeout))
        if not last_problems:
            print(
                f"STEP84 LIVE_OK attempt={attempt}/{attempts} "
                f"build={expected.build} mini={expected.mini_ui_version}"
            )
            return 0
        print(f"STEP84 LIVE_STALE attempt={attempt}/{attempts}")
        for problem in last_problems:
            print(f" - {problem}")
        if attempt < attempts:
            time.sleep(max(0.0, args.delay))

    print("STEP84 LIVE_GATE_FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
