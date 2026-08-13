from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "webapp" / "static"
CORE = STATIC / "app-core-20260812g.js"
TARGETS = (STATIC / "app-20260812g.js", STATIC / "app.js")

OLD = """  const tabNode=e.target.closest('[data-tab]');const tab=tabNode?.dataset.tab;if(tab){showTab(tab);const focus=tabNode?.dataset.focus;if(focus)setTimeout(()=>byId(focus)?.scrollIntoView({behavior:'smooth',block:'start'}),80);return;}\n"""

NEW = """  const tabNode=e.target.closest('[data-tab]');const tab=tabNode?.dataset.tab;
  if(tab==='more'){
    const drawer=document.querySelector('.tabs');
    const opening=!drawer?.classList.contains('mobile-open');
    drawer?.classList.toggle('mobile-open',opening);
    tabNode.classList.toggle('active',opening);
    tabNode.setAttribute('aria-expanded',opening?'true':'false');
    if(opening){
      document.querySelectorAll('.mobile-nav [data-tab]').forEach(node=>{if(node!==tabNode)node.classList.remove('active');});
    }else{
      const activePage=document.querySelector('.tab-page.active')?.id?.replace(/^page-/,'')||'';
      document.querySelectorAll('.mobile-nav [data-tab]').forEach(node=>node.classList.toggle('active',node.dataset.tab===activePage));
    }
    return;
  }
  if(tab){showTab(tab);const focus=tabNode?.dataset.focus;if(focus)setTimeout(()=>byId(focus)?.scrollIntoView({behavior:'smooth',block:'start'}),80);return;}\n"""


def build_patched_source(core: str) -> str:
    count = core.count(OLD)
    if count != 1:
        raise RuntimeError(f"Expected exactly one generic tab click handler, found {count}")
    patched = core.replace(OLD, NEW, 1)
    if "if(tab==='more')" not in patched or "mobile-open" not in patched:
        raise RuntimeError("Step84 More-menu patch was not applied")
    return patched


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Step84 Mini App navigation fix from audited core")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    core = CORE.read_text(encoding="utf-8")
    patched = build_patched_source(core)

    stale: list[str] = []
    for target in TARGETS:
        if args.check:
            if not target.exists() or target.read_text(encoding="utf-8") != patched:
                stale.append(str(target.relative_to(ROOT)))
        else:
            target.write_text(patched, encoding="utf-8", newline="\n")

    if stale:
        raise SystemExit("Step84 generated Mini App files are stale: " + ", ".join(stale))
    if args.check:
        print("STEP84 MORE_NAV_OK")
    else:
        print("STEP84 MORE_NAV_GENERATED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
