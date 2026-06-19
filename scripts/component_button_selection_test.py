from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="prod_bot_component_buttons_")
os.environ.setdefault("GLOBAL_OWNER_IDS", "2097006037")

from app.db import init_db
from app.services import repository as repo


def main() -> None:
    init_db()
    chat_id = 63101
    repo.upsert_chat(chat_id, "Чат", "private", connected=None)
    for entity_type, name in (
        ("product", "Изделие 1"),
        ("component", "Комплектующая 1"),
        ("component", "Комплектующая 2"),
    ):
        ok, msg = repo.create_entity(chat_id, entity_type, name, "шт")
        assert ok, msg
    product = repo.get_entity_by_name(chat_id, "product", "Изделие 1")
    c1 = repo.get_entity_by_name(chat_id, "component", "Комплектующая 1")
    c2 = repo.get_entity_by_name(chat_id, "component", "Комплектующая 2")
    assert product and c1 and c2
    repo.set_product_components(chat_id, product.id, [(c1.id, 2), (c2.id, 3)])
    assert repo.update_product_component_quantity(chat_id, product.id, c1.id, 5)
    removed = repo.remove_product_components(chat_id, product.id, [c2.id])
    assert removed == 1
    components = {int(row["component_id"]): float(row["quantity"]) for row in repo.list_product_components(product.id)}
    assert components == {c1.id: 5.0}, components

    keyboard_source = (ROOT / "app" / "keyboards.py").read_text(encoding="utf-8")
    setup_source = (ROOT / "app" / "handlers" / "setup.py").read_text(encoding="utf-8")
    assert "def component_choice_keyboard" in keyboard_source
    assert "components:{mode}:{component_id}" in keyboard_source
    assert 'component_choice_keyboard(components, "selectqty")' in setup_source
    assert 'component_choice_keyboard(components, "selectremove")' in setup_source
    assert "choose_component_for_quantity" in setup_source
    assert "await_selected_component_quantity" in setup_source
    assert "choose_component_for_remove" in setup_source
    print("OK")


if __name__ == "__main__":
    main()
