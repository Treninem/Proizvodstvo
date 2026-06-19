from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="prod_bot_components_")
os.environ.setdefault("GLOBAL_OWNER_IDS", "2097006037")

from app.db import init_db
from app.services import accounting, reporting, repository as repo


def main() -> None:
    init_db()
    chat_id = 61001
    repo.upsert_chat(chat_id, "Чат", "private", connected=None)
    for entity_type, name, unit in (
        ("product", "Изделие 1", "шт"),
        ("component", "Комплектующая 1", "шт"),
        ("component", "Комплектующая 2", "шт"),
        ("component", "Комплектующая 3", "шт"),
    ):
        ok, msg = repo.create_entity(chat_id, entity_type, name, unit)
        assert ok, msg
    product = repo.get_entity_by_name(chat_id, "product", "Изделие 1")
    c1 = repo.get_entity_by_name(chat_id, "component", "Комплектующая 1")
    c2 = repo.get_entity_by_name(chat_id, "component", "Комплектующая 2")
    c3 = repo.get_entity_by_name(chat_id, "component", "Комплектующая 3")
    assert product and c1 and c2 and c3

    repo.set_product_components(chat_id, product.id, [(c1.id, 2), (c2.id, 1)])
    assert len(repo.list_product_components(product.id)) == 2

    repo.add_or_update_product_components(chat_id, product.id, [(c2.id, 3), (c3.id, 4)])
    components = {int(row["component_id"]): float(row["quantity"]) for row in repo.list_product_components(product.id)}
    assert components[c1.id] == 2
    assert components[c2.id] == 3
    assert components[c3.id] == 4

    removed = repo.remove_product_components(chat_id, product.id, [c2.id])
    assert removed == 1
    components = {int(row["component_id"]): float(row["quantity"]) for row in repo.list_product_components(product.id)}
    assert c2.id not in components

    assert repo.update_product_component_quantity(chat_id, product.id, c1.id, 5)
    components = {int(row["component_id"]): float(row["quantity"]) for row in repo.list_product_components(product.id)}
    assert components[c1.id] == 5

    accounting.apply_operations(chat_id, chat_id, 1, [
        {"operation_type": "production", "entity_type": "component", "entity_id": c1.id, "quantity": 10, "unit": "шт", "area_id": None},
        {"operation_type": "production", "entity_type": "component", "entity_id": c3.id, "quantity": 8, "unit": "шт", "area_id": None},
    ], "test")
    text = reporting.build_assembly_capacity_report(chat_id, "сколько можно собрать Изделие 1")
    assert "Итого сейчас: 2" in text, text
    assert "не хватает" in text, text


    keyboard_source = (Path(__file__).resolve().parents[1] / "app" / "keyboards.py").read_text(encoding="utf-8")
    setup_source = (Path(__file__).resolve().parents[1] / "app" / "handlers" / "setup.py").read_text(encoding="utf-8")
    assert "def product_choice_keyboard" in keyboard_source
    assert "components:product:{product_id}" in keyboard_source
    assert "Выберите изделие." in setup_source
    assert "choose_product_for_components" in setup_source
    print("OK")


if __name__ == "__main__":
    main()
