from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "webapp" / "static"
INDEX = STATIC / "index.html"
SERVER = ROOT / "webapp" / "server.py"
KEYBOARDS = ROOT / "app" / "keyboards.py"
OWNER = ROOT / "app" / "handlers" / "owner.py"
DOCKERFILE = ROOT / "Dockerfile"
LIVE_TEST = ROOT / "tests" / "test_step84_live_deployment_gate.py"

NEW_BUILD = "85"
NEW_MINI = "20260816a"
NEW_ASSET = f"app-{NEW_MINI}.js"
ENTITY_CODE_FUNCTION = """function updateEntityCodeEntities(){
  const select=byId('entityCodeEntity');
  if(!select)return;
  const previous=val('entityCodeEntity');
  const type=val('entityCodeType')||'component';
  fillSelect('entityCodeEntity',entity(type),'Позиция');
  if(previous&&[...select.options].some(x=>x.value===String(previous)))select.value=String(previous);
}
"""


def write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def replace_once(text: str, pattern: str, replacement: str, label: str) -> str:
    result, count = re.subn(pattern, replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"{label}: expected one replacement, got {count}")
    return result


def main() -> int:
    index = INDEX.read_text(encoding="utf-8")
    active_match = re.search(r'/static/(app-[^"?]+\.js)', index)
    if not active_match:
        raise RuntimeError("Active Mini App JavaScript not found in index.html")
    old_asset = active_match.group(1)
    old_path = STATIC / old_asset
    if not old_path.is_file():
        raise RuntimeError(f"Active asset is missing: {old_asset}")

    source = old_path.read_text(encoding="utf-8")
    listener = "byId('entityCodeType')?.addEventListener('change',updateEntityCodeEntities);"
    marker = "function updateEntityCodeEntities(){"
    anchor = "function updateDepartmentEntityChoices(){"
    if listener not in source:
        raise RuntimeError("Entity-code listener is missing")
    if marker not in source:
        if anchor not in source:
            raise RuntimeError("Safe entity-code insertion anchor is missing")
        source = source.replace(anchor, ENTITY_CODE_FUNCTION + "\n" + anchor, 1)
    if source.count(marker) != 1:
        raise RuntimeError("Entity-code initializer must exist exactly once")

    source = replace_once(
        source,
        r"// Mini App release: [^\r\n]+",
        f"// Mini App release: {NEW_MINI}",
        "Mini App release comment",
    )
    source = replace_once(
        source,
        r'const MINI_APP_VERSION="[^"]+";',
        f'const MINI_APP_VERSION="{NEW_MINI}";',
        "Mini App JavaScript version",
    )
    write(STATIC / NEW_ASSET, source)
    write(STATIC / "app.js", source)

    index = replace_once(
        index,
        r'/static/app-[^"?]+\.js(?:\?[^"<]*)?',
        f'/static/{NEW_ASSET}?v={NEW_BUILD}-entity-code-bootstrap',
        "index active JavaScript",
    )
    write(INDEX, index)

    server = SERVER.read_text(encoding="utf-8")
    server = replace_once(
        server,
        r'MINI_UI_VERSION\s*=\s*"[^"]+"',
        f'MINI_UI_VERSION = "{NEW_MINI}"',
        "server Mini App version",
    )
    server, build_count = re.subn(
        r'("build"\s*:\s*")[^"]+("\s*[,}])',
        rf'\g<1>{NEW_BUILD}\g<2>',
        server,
    )
    if build_count < 1:
        raise RuntimeError("server backend build marker was not found")
    write(SERVER, server)

    keyboards = KEYBOARDS.read_text(encoding="utf-8")
    keyboards = replace_once(
        keyboards,
        r'MINI_UI_VERSION\s*=\s*"[^"]+"',
        f'MINI_UI_VERSION = "{NEW_MINI}"',
        "Telegram Mini App button version",
    )
    write(KEYBOARDS, keyboards)

    owner = OWNER.read_text(encoding="utf-8")
    owner, backend_count = re.subn(r'Backend(?::|) 84a', lambda m: f"Backend{':' if ':' in m.group(0) else ''} {NEW_BUILD}", owner)
    owner, mini_count = re.subn(r'Mini App(?::|) 20260813a', lambda m: f"Mini App{':' if ':' in m.group(0) else ''} {NEW_MINI}", owner)
    if backend_count < 3 or mini_count < 3:
        raise RuntimeError(f"Owner version markers incomplete: backend={backend_count}, mini={mini_count}")
    write(OWNER, owner)

    docker = DOCKERFILE.read_text(encoding="utf-8")
    docker = replace_once(
        docker,
        r'LABEL org\.opencontainers\.image\.version="[^"]+"',
        f'LABEL org.opencontainers.image.version="{NEW_BUILD}-mini-{NEW_MINI}"',
        "Docker image version",
    )
    write(DOCKERFILE, docker)

    live_test = LIVE_TEST.read_text(encoding="utf-8")
    live_test = live_test.replace('"84a"', f'"{NEW_BUILD}"')
    live_test = live_test.replace("Backend: 84a", f"Backend: {NEW_BUILD}")
    live_test = live_test.replace("20260813a", NEW_MINI)
    live_test = live_test.replace("app-20260813a.js", NEW_ASSET)
    write(LIVE_TEST, live_test)

    print(f"STEP85_PROMOTED build={NEW_BUILD} mini={NEW_MINI} asset={NEW_ASSET} from={old_asset}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
