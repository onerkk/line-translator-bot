"""User-confirmed station knowledge; original-source scope remains authoritative."""
import pytest
import app
import factory_knowledge as knowledge
from test_month_end_notice_delivery import SOURCE


@pytest.mark.parametrize("source", [SOURCE, "月底前人力會優先開三站。", "包裝今天開3站。", "開三站"])
def test_confirmed_three_packing_stations_enter_first_pass_knowledge(source):
    cards = knowledge.retrieve(source, "zh", "id", limit=3)
    assert "three_packing_stations_staffing" in [c["id"] for c in cards]
    prompt = knowledge.build_prompt(cards)
    for fact in ("圓型包裝站", "異型包裝站", "削皮包裝站", "tiga stasiun packing"):
        assert fact in prompt


def test_actual_notice_contract_includes_the_confirmed_packaging_scope():
    prompt = app.build_translation_semantic_contract_prompt(app.build_translation_semantic_contract(SOURCE, "zh", "id"))
    assert "tiga stasiun packing" in prompt
    assert "圓型包裝站" in prompt and "削皮包裝站" in prompt


@pytest.mark.parametrize("source", ["人力優先開兩站。", "研磨站今天開三站。", "捷運新開三站。", "請到第3站。"])
def test_other_counts_explicit_station_types_and_station_ids_do_not_inherit_three_packing_stations(source):
    assert "three_packing_stations_staffing" not in [c["id"] for c in knowledge.retrieve(source, "zh", "id", limit=8)]


def test_existing_exact_notice_names_packing_without_inventing_station_ids():
    target = app.factory_translation_guard_module.exact_verified_target("月底前人力會優先開三站。", "zh", "id")
    assert target == "Sampai akhir bulan, tenaga kerja akan diprioritaskan untuk mengoperasikan tiga stasiun packing."
    assert "801" not in target
    assert app._final_delivery_guard("月底前人力會優先開三站。", target, "zh", "id") == target
