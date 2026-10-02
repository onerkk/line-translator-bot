from pathlib import Path

import factory_knowledge


ROOT = Path(__file__).resolve().parent
STORE = factory_knowledge.FactoryKnowledgeStore(ROOT / "factory_knowledge.json")
CARD_ID = "factory_housekeeping_management_emphasis"


def test_housekeeping_context_generalizes_across_management_and_floor_wording():
    source = (
        "月初設備沒料要加強一下環境，作業區地面掉落物要掃，油水擦乾淨，"
        "臨近步道要拖地。這兩個月外賓會比較多，總經理開始在釘環境了"
    )
    cards = STORE.retrieve(source, "zh", "id", limit=8)
    card = next(card for card in cards if card["id"] == CARD_ID)
    prompt = factory_knowledge.FactoryKnowledgeStore.build_prompt([card])
    assert "一般現場整理" in prompt
    assert "嚴格要求或監督" in prompt
    assert "不得自行窄化成原料" in prompt


def test_unstated_material_category_is_rejected_but_general_object_is_accepted():
    source = "作業區地面掉落物要掃乾淨。"
    cards = STORE.retrieve(source, "zh", "id", limit=8)
    assert any(card["id"] == CARD_ID for card in cards)

    accurate = "Sapu benda-benda yang jatuh di lantai area kerja sampai bersih."
    narrowed = "Sapu material yang jatuh di lantai area kerja sampai bersih."
    assert factory_knowledge.validate_translation(cards, source, accurate) == (True, [])
    ok, issues = factory_knowledge.validate_translation(cards, source, narrowed)
    assert not ok
    assert any("generic_dropped_object_narrowed_or_omitted" in issue for issue in issues)


def test_management_emphasis_and_housekeeping_are_not_confused_with_material_washing():
    source = "主管最近更嚴格要求作業區整潔，地板上掉落的雜物要清除。"
    cards = STORE.retrieve(source, "zh", "id", limit=8)
    assert any(card["id"] == CARD_ID for card in cards)

    washing_source = "422待洗庫存量低於40噸時，棒材要安排清洗。"
    washing_cards = STORE.retrieve(washing_source, "zh", "id", limit=8)
    assert all(card["id"] != CARD_ID for card in washing_cards)


def test_indonesian_housekeeping_translation_keeps_generic_object_in_chinese():
    source = (
        "General Manager semakin tegas memperhatikan kerapian area kerja. "
        "Barang yang terjatuh di lantai mohon disapu."
    )
    cards = STORE.retrieve(source, "id", "zh", limit=8)
    assert any(card["id"] == CARD_ID for card in cards)
    accurate = "總經理更加嚴格地重視作業區整潔。請把地面掉落物掃乾淨。"
    assert factory_knowledge.validate_translation(cards, source, accurate) == (True, [])
