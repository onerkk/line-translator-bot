import ast
import json
from pathlib import Path

import factory_knowledge


ROOT = Path(__file__).resolve().parent
APP = ROOT / "app.py"
KNOWLEDGE = ROOT / "factory_knowledge.json"


def test_app_does_not_pin_factory_knowledge_data_build_id():
    source = APP.read_text(encoding="utf-8")
    assert "_EXPECTED_FACTORY_KNOWLEDGE_BUILD_ID" not in source
    assert "_EXPECTED_FACTORY_KNOWLEDGE_SCHEMA_VERSION = 1" in source
    assert "factory knowledge deployment contract failed" in source
    ast.parse(source)


def test_factory_knowledge_build_label_is_real_and_schema_compatible():
    data = json.loads(KNOWLEDGE.read_text(encoding="utf-8"))
    build_id = str(data.get("build_id") or "").strip()
    assert data.get("schema_version") == 1
    assert build_id and build_id.lower() != "unknown"


def test_new_storage_contracts_are_behaviorally_retrievable_at_boot():
    store = factory_knowledge.FactoryKnowledgeStore(str(KNOWLEDGE))
    overflow = store.retrieve(
        "週末大成儲格能放就放，放不下再放照片裡這些位置。",
        "zh",
        "id",
        limit=5,
    )
    eh33 = store.retrieve(
        "入儲時EH33峰作金屬集中放這格",
        "zh",
        "id",
        limit=5,
    )
    assert any(row.get("id") == "customer_storage_slot_photo_overflow_placement" for row in overflow)
    assert any(row.get("id") == "customer_eh33_concentrated_storage_slot" for row in eh33)
