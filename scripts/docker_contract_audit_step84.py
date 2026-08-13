from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
SERVER = ROOT / "webapp" / "server.py"
INDEX = ROOT / "webapp" / "static" / "index.html"


def main() -> int:
    docker = DOCKERFILE.read_text(encoding="utf-8")
    server = SERVER.read_text(encoding="utf-8")
    index = INDEX.read_text(encoding="utf-8")

    build_markers = set(re.findall(r'"build"\s*:\s*"([^"]+)"', server))
    if len(build_markers) != 1:
        raise RuntimeError(f"Expected exactly one backend build identity, found {sorted(build_markers)}")
    build = next(iter(build_markers))

    mini_match = re.search(r'MINI_UI_VERSION\s*=\s*"([^"]+)"', server)
    if not mini_match:
        raise RuntimeError("MINI_UI_VERSION missing in webapp/server.py")
    mini = mini_match.group(1)

    asset_match = re.search(r'/static/(app-[^"?]+\.js)', index)
    if not asset_match:
        raise RuntimeError("Active Mini App JS missing from index.html")
    asset = asset_match.group(1)
    asset_path = ROOT / "webapp" / "static" / asset
    if not asset_path.is_file():
        raise RuntimeError(f"Active Mini App asset is missing: {asset}")

    expected_label = f'LABEL org.opencontainers.image.version="{build}-mini-{mini}"'
    if expected_label not in docker:
        raise RuntimeError(f"Docker image label is stale; expected: {expected_label}")
    if "COPY . ./" not in docker:
        raise RuntimeError("Dockerfile must copy the complete repository into /app")
    if "EXPOSE 3000" not in docker:
        raise RuntimeError("Dockerfile must expose port 3000")
    if "python scripts/live_start_check.py" not in docker:
        raise RuntimeError("Dockerfile must run live_start_check.py before runtime")
    if "exec python -m app.runtime" not in docker:
        raise RuntimeError("Dockerfile must launch app.runtime as the container process")

    print(f"STEP84 DOCKER_CONTRACT_OK build={build} mini={mini} asset={asset}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
