from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_ENTITY_CODE_LISTENER = "byId('entityCodeType')?.addEventListener('change',updateEntityCodeEntities);"
_ENTITY_CODE_FUNCTION_MARKER = "function updateEntityCodeEntities(){"
_ENTITY_CODE_ANCHOR = "function updateDepartmentEntityChoices(){"
_ENTITY_CODE_FUNCTION = """function updateEntityCodeEntities(){
  const select=byId('entityCodeEntity');
  if(!select)return;
  const previous=val('entityCodeEntity');
  const type=val('entityCodeType')||'component';
  fillSelect('entityCodeEntity',entity(type),'Позиция');
  if(previous&&[...select.options].some(x=>x.value===String(previous)))select.value=String(previous);
}
"""


@dataclass(frozen=True)
class FrontendRuntimeResult:
    active_asset: str
    changed: bool


def _write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def ensure_frontend_runtime_ready(root: Path | None = None) -> FrontendRuntimeResult:
    """Repair known cache-safe Mini App runtime defects before Uvicorn starts.

    The Step84 source contains a listener for ``updateEntityCodeEntities`` but the
    function itself was accidentally omitted.  A browser then raises ReferenceError
    while wiring events and stops the rest of the Mini App bootstrap.  We repair the
    active versioned asset deterministically at process start and keep ``app.js`` as
    a byte-identical alias.

    The operation is idempotent and deliberately refuses to guess when the active
    asset or the expected wiring anchor cannot be found.
    """

    project_root = Path(root) if root is not None else ROOT
    static_dir = project_root / "webapp" / "static"
    index_path = static_dir / "index.html"
    index = index_path.read_text(encoding="utf-8")

    match = re.search(r'/static/(app-[^"?]+\.js)', index)
    if not match:
        raise RuntimeError("Active Mini App JavaScript is not referenced by index.html")

    active_asset = match.group(1)
    active_path = static_dir / active_asset
    if not active_path.is_file():
        raise RuntimeError(f"Active Mini App JavaScript is missing: {active_asset}")

    source = active_path.read_text(encoding="utf-8")
    if _ENTITY_CODE_LISTENER not in source:
        raise RuntimeError("Mini App entity-code change listener is missing")

    changed = False
    if _ENTITY_CODE_FUNCTION_MARKER not in source:
        if _ENTITY_CODE_ANCHOR not in source:
            raise RuntimeError("Cannot safely insert Mini App entity-code initializer")
        source = source.replace(
            _ENTITY_CODE_ANCHOR,
            _ENTITY_CODE_FUNCTION + "\n" + _ENTITY_CODE_ANCHOR,
            1,
        )
        _write_text(active_path, source)
        changed = True

    alias_path = static_dir / "app.js"
    alias_source = alias_path.read_text(encoding="utf-8") if alias_path.is_file() else ""
    if alias_source != source:
        _write_text(alias_path, source)
        changed = True

    if source.count(_ENTITY_CODE_FUNCTION_MARKER) != 1:
        raise RuntimeError("Mini App entity-code initializer must exist exactly once")

    return FrontendRuntimeResult(active_asset=active_asset, changed=changed)
