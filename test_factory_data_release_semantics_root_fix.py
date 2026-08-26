import json
from pathlib import Path

import factory_knowledge
import factory_message_semantics as semantics
import prompt_optimizer


ROOT = Path(__file__).resolve().parent
SCREENSHOT_SOURCE = "@小麥（研磨股班長） 這把麻煩他們放一下"
SCREENSHOT_TARGET = (
    "@小麥（研磨股班長） Tolong minta mereka release data untuk bundel ini "
    "ke stasiun berikutnya."
)


def test_reported_bundle_request_is_classified_as_erp_data_release_before_provider():
    frame = semantics.build_frame(SCREENSHOT_SOURCE, "zh", "id")
    assert frame["active"] is True
    assert frame["complete"] is True
    assert frame["kind"] == "zh_id_erp_data_release"
    assert frame["slots"]["object_kind"] == "bundle"
    assert frame["slots"]["delegate"] == "third_plural"
    assert frame["slots"]["request"] is True
    assert semantics.translate_source_directly(
        SCREENSHOT_SOURCE, "zh", "id"
    ) == SCREENSHOT_TARGET


def test_data_release_parser_generalizes_by_slots_instead_of_exact_sentence():
    cases = {
        "這批幫忙放一下": (
            "Tolong release data untuk batch ini ke stasiun berikutnya."
        ),
        "麻煩先放這張工單": (
            "Tolong release data untuk work order ini ke stasiun berikutnya terlebih dahulu."
        ),
        "這兩把都放了": (
            "Data untuk dua bundel ini sudah di-release ke stasiun berikutnya."
        ),
        "這筆資料麻煩你放行": (
            "Tolong Anda release data ini ke stasiun berikutnya."
        ),
        "麻煩他們放這把": (
            "Tolong minta mereka release data untuk bundel ini ke stasiun berikutnya."
        ),
    }
    for source, expected in cases.items():
        frame = semantics.build_frame(source, "zh-TW", "id-ID")
        assert frame["active"] is True, source
        assert frame["complete"] is True, (source, frame["unparsed"])
        assert semantics.translate_source_directly(
            source, "zh-TW", "id-ID"
        ) == expected


def test_validator_rejects_the_reported_physical_placement_mistranslation():
    frame = semantics.build_frame(SCREENSHOT_SOURCE, "zh", "id")
    good = (
        "@小麥 Tolong minta mereka release data untuk bundel ini "
        "ke stasiun berikutnya."
    )
    assert semantics.validate_translation(frame, good) == (True, [])

    bad_candidates = (
        "@小麥 Tolong minta mereka meletakkan bundel ini.",
        "@小麥 Tolong minta mereka menaruh bundel ini.",
        "@小麥 Tolong minta mereka melepaskan bundel ini.",
        "@小麥 Tolong minta mereka release bundel ini ke stasiun berikutnya.",
        "@小麥 Tolong release data untuk bundel ini ke stasiun berikutnya.",
    )
    for candidate in bad_candidates:
        ok, issues = semantics.validate_translation(frame, candidate)
        assert ok is False, candidate
        assert issues, candidate


def test_physical_placement_qc_release_feeding_and_leave_are_not_data_release():
    controls = (
        "這把刀麻煩他們放在架上。",
        "這把材料放不下，先放照片裡的位置。",
        "週末儲格能放就放，放不下再放照片標示的位置。",
        "品保檢驗後有放行。",
        "QC放行後再通知我。",
        "請他們放下工具。",
        "今天放料前先確認機台。",
        "明天放假。",
    )
    for source in controls:
        frame = semantics.build_frame(source, "zh", "id")
        assert frame.get("kind") != "zh_id_erp_data_release", source
        assert semantics.translate_source_directly(source, "zh", "id") == ""


def test_partial_station_release_still_gets_a_mandatory_contract_and_validator():
    frame = semantics.build_frame("麻煩削皮優先放行這批料", "zh", "id")
    assert frame["active"] is True
    assert frame["complete"] is False
    prompt = semantics.build_prompt(frame)
    assert "ERP production-data release relation" in prompt
    assert "release data ke stasiun berikutnya" in prompt
    assert "meletakkan" in prompt
    ok, issues = semantics.validate_translation(
        frame, "Tolong stasiun peeling meletakkan batch material ini."
    )
    assert ok is False
    assert any("erp_data_release" in issue or "physical_placement" in issue for issue in issues)


def test_runtime_prompt_selector_catches_soft_request_and_completion_paraphrases():
    for source in (
        "這把麻煩他們放一下",
        "麻煩他們放這批",
        "這兩把都放好了",
        "這張工單幫忙先放",
    ):
        rules = prompt_optimizer._matching_historical_rules(source, "zh>id")
        assert any("[release-vs-put]" in rule for rule in rules), source


def test_editable_knowledge_matches_data_release_but_not_physical_or_qc_senses():
    store = factory_knowledge.FactoryKnowledgeStore(
        str(ROOT / "factory_knowledge.json")
    )
    release_cards = store.retrieve(
        "這把麻煩他們放一下", "zh", "id", limit=8
    )
    assert any(
        card.get("id") == "erp_colloquial_data_release_to_next_station"
        for card in release_cards
    )
    for source in (
        "這把刀麻煩他們放在架上",
        "品保檢驗後有放行",
    ):
        cards = store.retrieve(source, "zh", "id", limit=8)
        assert all(
            card.get("id") != "erp_colloquial_data_release_to_next_station"
            for card in cards
        )


def test_deployment_contract_pins_the_new_semantic_build_and_keeps_source_first_route():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert (
        '_EXPECTED_FACTORY_MESSAGE_SEMANTICS_BUILD_ID = '
        '"2026-08-26.2-short-event-role-integrity"'
    ) in source
    assert "factory_message_semantics_module.translate_source_directly(" in source
    knowledge = json.loads(
        (ROOT / "factory_knowledge.json").read_text(encoding="utf-8")
    )
    assert knowledge["build_id"] == "2026-08-25.1-packaging-consolidation-workflow"
