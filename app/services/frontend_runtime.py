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
_EXTENSION_ASSET = "app-extensions.js"
_EXTENSION_TAG = '<script src="/static/app-extensions.js?v=20260820a"></script>'
_HELP_ASSET = "help-guide.js"
_HELP_TAG = '<script src="/static/help-guide.js?v=20260820c"></script>'
_MANAGEMENT_ASSET = "management-panel.js"
_MANAGEMENT_TAG = '<script src="/static/management-panel.js?v=20260820a"></script>'
_MENU_ASSET = "menu-navigation.js"
_MENU_TAG = '<script src="/static/menu-navigation.js?v=20260820a"></script>'
_UX_STYLE_ASSET = "miniapp-ux.css"
_UX_STYLE_TAG = '<link rel="stylesheet" href="/static/miniapp-ux.css?v=20260820a" />'
_TREE_HELP_ASSET = "tree-help.js"
_TREE_HELP_TAG = '<script src="/static/tree-help.js?v=20260820b"></script>'
_TREE_REFINEMENT_ASSET = "tree-refinement.js"
_TREE_REFINEMENT_TAG = '<script src="/static/tree-refinement.js?v=20260820c"></script>'
_WORKER_PLACES_ASSET = "worker-places-step92.js"
_WORKER_PLACES_TAG = '<script src="/static/worker-places-step92.js?v=20260821a"></script>'
_TREE_ASSET = "tree-shell.js"
_TREE_TAG = '<script src="/static/tree-shell.js?v=20260820c"></script>'
_TREE_STYLE_ASSET = "tree-shell.css"
_TREE_STYLE_TAG = '<link rel="stylesheet" href="/static/tree-shell.css?v=20260820b" />'
_TREE_HISTORY_OLD = "if (item.kind === 'menu') renderMenu(item.key, true);\n    else openLeaf(item);"
_TREE_HISTORY_NEW = "if (item.kind === 'menu') renderMenu(item.key, true);\n    else { tree.history.push(tree.currentMenu); openLeaf(item); }"
_TREE_OVERRIDE_OLD = """async function openLeaf(item) {
    if (!itemAllowed(item)) return;
    const leaf = item.leaf || {};"""
_TREE_OVERRIDE_NEW = """async function openLeaf(item) {
    if (!itemAllowed(item)) return;
    if (typeof window.__treeOpenOverride === 'function' && window.__treeOpenOverride(item, {mountNode,activateLegacy,setHeader,restoreMounted,notify,loadSnapshot,tree,menus})) return;
    const leaf = item.leaf || {};"""


@dataclass(frozen=True)
class FrontendRuntimeResult:
    active_asset: str
    changed: bool


def _write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _attach_optional_script(index: str, static_dir: Path, asset: str, tag: str) -> tuple[str, bool]:
    if not (static_dir / asset).is_file() or tag in index:
        return index, False
    if "</body>" in index:
        return index.replace("</body>", f"  {tag}\n</body>", 1), True
    return index.rstrip() + "\n" + tag + "\n", True


def _attach_optional_style(index: str, static_dir: Path, asset: str, tag: str) -> tuple[str, bool]:
    if not (static_dir / asset).is_file() or tag in index:
        return index, False
    if "</head>" in index:
        return index.replace("</head>", f"  {tag}\n</head>", 1), True
    return tag + "\n" + index, True


def _repair_tree_shell(static_dir: Path) -> bool:
    path = static_dir / _TREE_ASSET
    if not path.is_file():
        return False
    source = path.read_text(encoding="utf-8")
    changed = False
    if _TREE_HISTORY_NEW not in source:
        if _TREE_HISTORY_OLD not in source:
            raise RuntimeError("Tree Mini App navigation marker is missing")
        source = source.replace(_TREE_HISTORY_OLD, _TREE_HISTORY_NEW, 1)
        changed = True
    if _TREE_OVERRIDE_NEW not in source:
        if _TREE_OVERRIDE_OLD not in source:
            raise RuntimeError("Tree Mini App leaf override marker is missing")
        source = source.replace(_TREE_OVERRIDE_OLD, _TREE_OVERRIDE_NEW, 1)
        changed = True
    if changed:
        _write_text(path, source)
    return changed


def ensure_frontend_runtime_ready(root: Path | None = None) -> FrontendRuntimeResult:
    """Repair the active Mini App asset and attach optional interface additions."""

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

    changed = _repair_tree_shell(static_dir)
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

    index, style_added = _attach_optional_style(index, static_dir, _UX_STYLE_ASSET, _UX_STYLE_TAG)
    index, tree_style_added = _attach_optional_style(index, static_dir, _TREE_STYLE_ASSET, _TREE_STYLE_TAG)
    index, extension_added = _attach_optional_script(index, static_dir, _EXTENSION_ASSET, _EXTENSION_TAG)
    index, help_added = _attach_optional_script(index, static_dir, _HELP_ASSET, _HELP_TAG)
    index, management_added = _attach_optional_script(index, static_dir, _MANAGEMENT_ASSET, _MANAGEMENT_TAG)
    index, menu_added = _attach_optional_script(index, static_dir, _MENU_ASSET, _MENU_TAG)
    index, tree_help_added = _attach_optional_script(index, static_dir, _TREE_HELP_ASSET, _TREE_HELP_TAG)
    index, tree_refinement_added = _attach_optional_script(index, static_dir, _TREE_REFINEMENT_ASSET, _TREE_REFINEMENT_TAG)
    index, worker_places_added = _attach_optional_script(index, static_dir, _WORKER_PLACES_ASSET, _WORKER_PLACES_TAG)
    index, tree_added = _attach_optional_script(index, static_dir, _TREE_ASSET, _TREE_TAG)
    if (
        style_added
        or tree_style_added
        or extension_added
        or help_added
        or management_added
        or menu_added
        or tree_help_added
        or tree_refinement_added
        or worker_places_added
        or tree_added
    ):
        _write_text(index_path, index)
        changed = True

    return FrontendRuntimeResult(active_asset=active_asset, changed=changed)
