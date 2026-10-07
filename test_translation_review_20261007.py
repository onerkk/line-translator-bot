"""Confirmed shop-floor meanings, source coverage and real pipeline wiring.

Targets are manually reviewed fixtures, not claims of live model accuracy.
Mutations and counterexamples test relationships beyond the screenshot wording.
"""
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import factory_terminology as terminology
import factory_message_semantics as semantics
import translation_alignment as alignment
import translation_quality_gate as quality


REVIEW = json.loads((Path(__file__).parent / "tests/data/translation_review_20261007.json").read_text())


@pytest.fixture(autouse=True)
def isolated_thread(monkeypatch):
    import app
    monkeypatch.setattr(app, "_tl", threading.local())


@pytest.mark.parametrize("case", REVIEW["cases"], ids=lambda row: row["id"])
def test_reviewed_screenshot_targets_pass_current_shared_boundaries(case):
    import app
    report = quality.validate_translation(case["source"], case["target"], case["src"], case["tgt"])
    assert report.ok, report.issues
    assert app._delivery_validation_issues(case["source"], case["target"], case["src"], case["tgt"]) == []


@pytest.mark.parametrize("source", [
    "小趙走去削皮", "請剝皮先放這2把", "小趙在削皮那邊", "麻煩削皮優先放行這批料",
    "請到剝皮包裝站", "削皮需要G8G9台車 麻煩一下", "請剥皮先放這3把",
])
def test_contextual_station_short_forms_share_one_approved_meaning(source):
    matches = terminology.get_engine(terminology.approved_glossary()).match_zh(source)
    assert any(m.source_term == "削皮包裝站" for m in matches)
    contract = alignment.build_contract(source, "zh", "id")
    assert contract["aligned"]
    assert any(row["kind"] == "term_sense" for row in contract["constraints"])


@pytest.mark.parametrize("source", [
    "削皮股股長來了", "去削皮股找股長", "這2捆要削皮加工", "削皮機壞了",
    "把蘋果拿去削皮", "正在削皮作業", "削皮棒需要台車", '@削皮 請放行這2把',
    '客戶名稱是「削皮」，請查工單', "請去削皮加工站", "削皮製程還沒完成",
])
def test_process_department_person_and_customer_are_not_packing_station_aliases(source):
    matches = terminology.get_engine(terminology.approved_glossary()).match_zh(source)
    assert not any(m.source_term == "削皮包裝站" for m in matches)


def test_context_alias_engine_is_data_driven_and_clauses_do_not_share_cues():
    glossary = {"測試包裝站": {"idn": "stasiun packing uji", "translation_mode": "soft", "category": "station",
        "contextual_aliases_zh": [{"text": "測包", "before_regex": r"(?:去|到)$"}],
        "validated_targets_id": ["stasiun packing uji"]}}
    engine = terminology.FactoryTerminologyEngine(glossary)
    assert engine.match_zh("小趙去測包")[0].target_term == "stasiun packing uji"
    assert not engine.match_zh("小趙去現場，測包加工完成")
    contract = alignment.build_contract("小趙去測包", "zh", "id", glossary=glossary)
    assert alignment.translation_issues(contract, "小趙 pergi ke stasiun packing uji.") == []


@pytest.mark.parametrize("bad", [
    "Tolong release data untuk 2 bundel ini ke stasiun剝皮 dahulu.",
    "Tolong release data untuk 2 bundel ini ke Stasiun packing peeling dahulu.",
    "Tolong stasiun peeling release data untuk 2 bundel ini dahulu.",
    "Tolong Bagian Peeling release data untuk 2 bundel ini dahulu.",
])
def test_release_station_remains_the_actor_and_retains_packing_identity(bad):
    contract = alignment.build_contract("請剝皮先放這2把", "zh", "id")
    assert alignment.translation_issues(contract, bad)
    good = "Tolong pihak Stasiun packing peeling release data untuk 2 bundel ini terlebih dahulu."
    assert alignment.translation_issues(contract, good) == []


@pytest.mark.parametrize("source", [
    "異常澄清後放行", "一批材料品質異常確認後放行", "缺陷處理後放行，再協助包裝",
])
def test_quality_clearance_without_named_qc_is_not_erp_handoff(source):
    assert terminology.quality_release_context(source)
    assert semantics.build_data_release_frame(source)["active"] is False
    contract = alignment.build_contract(source, "zh", "id")
    assert "quality_clearance" in {r["kind"] for r in contract["constraints"]}
    assert "semantic:quality_clearance_changed_to_erp" in alignment.translation_issues(
        contract, "Setelah abnormalitas diklarifikasi, release data ke stasiun berikutnya.")


def test_explicit_erp_evidence_overrides_quality_context_without_spreading_to_other_clauses():
    source = "異常澄清後放行資料"
    assert not terminology.quality_release_context(source)
    assert semantics.build_data_release_frame(source)["active"]
    mixed = "異常澄清後放行，請削皮先放這2把"
    frame = semantics.build_data_release_frame(mixed)
    assert frame["active"]
    assert len(frame["slots"]["release_relations"]) == 1
    assert "異常" not in frame["slots"]["release_relations"][0]["evidence"]
    contract = alignment.build_contract(mixed, "zh", "id")
    good = "Setelah abnormalitas diklarifikasi dan material disetujui, tolong pihak Stasiun packing peeling release data untuk 2 bundel ini dahulu."
    assert alignment.translation_issues(contract, good) == []
    bad = "Setelah abnormalitas diklarifikasi, tolong pihak Stasiun packing peeling release data untuk 2 bundel ini dahulu."
    assert "semantic:quality_clearance_approval_missing" in alignment.translation_issues(contract, bad)


def test_available_assistance_does_not_become_a_comparative_quantity_or_all_material():
    source = "幫忙多少包一些"
    contract = alignment.build_contract(source, "zh", "id")
    assert alignment.translation_issues(contract, "Tolong bantu mengemas sebagian semampunya.") == []
    for bad in ("Tolong bantu bungkus lebih banyak.", "Kemas seluruh material sampai selesai."):
        assert alignment.translation_issues(contract, bad)
    assert not alignment.build_contract("請比昨天多包一些", "zh", "id")["constraints"]


def test_physical_window_is_not_a_monitor_but_explicit_cctv_stays_allowed():
    contract = alignment.build_contract("小趙在窗戶看", "zh", "id")
    assert alignment.translation_issues(contract, "小趙 melihat dari jendela.") == []
    assert "semantic:invented_observation_device" in alignment.translation_issues(
        contract, "小趙 melihat CCTV dari jendela.")
    assert not alignment.build_contract("小趙在窗戶旁看監視器", "zh", "id")["constraints"]


def test_work_order_sheet_owner_and_detachment_do_not_become_erp_release():
    source = "前一捆未吊離前，下一捆待生產的料的工單不可取下。"
    contract = alignment.build_contract(source, "zh", "id")
    good = "Sebelum bundel sebelumnya diangkat, lembar work order pada material bundel berikutnya tidak boleh dilepas."
    assert alignment.translation_issues(contract, good) == []
    bad = "Sebelum bundel sebelumnya diangkat, jangan release data untuk work order bundel berikutnya."
    assert "semantic:physical_document_changed_to_erp" in alignment.translation_issues(contract, bad)
    assert alignment.translation_issues(contract, good.replace("berikutnya", "sebelumnya"))
    swapped = "Sebelum bundel berikutnya diangkat, lembar work order pada material bundel sebelumnya tidak boleh dilepas."
    assert "semantic:next_bundle_work_order_owner_missing" in alignment.translation_issues(contract, swapped)
    assert "semantic:previous_bundle_lifting_prerequisite_missing" in alignment.translation_issues(contract, swapped)
    assert not alignment.build_contract("系統中的工單不可移除", "zh", "id")["constraints"]


def test_indonesian_purity_retains_names_and_rejects_untranslated_plant_terms():
    assert quality._target_id_language_purity_issues("小趙在窗戶看", "小趙 melihat dari jendela.", [], []) == []
    defects = quality._target_id_language_purity_issues("請剝皮先放這2把", "Tolong stasiun剝皮 release data.", [], [])
    assert "untranslated_source_word:剝皮" in defects
    assert quality._target_id_language_purity_issues("請查大成的工單", "Periksa work order 大成.", [], []) == []


def test_per_unit_integrity_rejects_swapped_equipment_even_when_all_codes_are_present_globally():
    source = "I5已發料。\nI6還沒發料。"
    contract = alignment.build_contract(source, "zh", "id")
    data = {"segments": [
        {"unit_id": "u1", "text": "I6 sudah diubah status datanya menjadi OL."},
        {"unit_id": "u2", "text": "I5 belum diubah status datanya menjadi OL."},
    ]}
    decoded = alignment.decode_response(json.dumps(data), contract)
    assert not decoded.ok
    assert any("missing_literal:I5" in issue for issue in decoded.issues)
    assert any("missing_literal:I6" in issue for issue in decoded.issues)


def test_missing_structured_source_unit_is_never_rendered_as_a_partial_translation():
    contract = alignment.build_contract("請先檢查工單。\n再安排包裝。", "zh", "id")
    result = alignment.decode_response('{"segments":[{"unit_id":"u1","text":"Periksa work order dahulu."}]}', contract)
    assert not result.text and "alignment:missing_unit:u2" in result.issues


@pytest.mark.parametrize("source,target", [
    ("前一捆未吊離。", "Bundel sebelumnya belum diangkat."),
    ("下一批先安排。", "Prioritaskan batch berikutnya."),
    ("上一件要先檢查。", "Periksa barang sebelumnya dahulu."),
])
def test_neighboring_sequence_items_are_not_numeric_one_counts(source, target):
    import factory_quantity_semantics as quantities
    frame = quantities.build_frame(source, "zh", "id")
    assert any(a["quantifier"].startswith("relative_") for a in frame["atoms"])
    assert quantities.validate_translation(frame, target)[0]
    swapped = target.replace("sebelumnya", "TEMP").replace("berikutnya", "sebelumnya").replace("TEMP", "berikutnya")
    assert not quantities.validate_translation(frame, swapped)[0]


@pytest.mark.parametrize("source", ["剩下一捆", "放下一捆", "卸下一捆", "送上一捆", "下一次有2捆", "下兩捆先安排"])
def test_motion_and_remaining_counts_are_not_relative_sequence_determiners(source):
    import factory_quantity_semantics as quantities
    assert all(not a["quantifier"].startswith("relative_") for a in quantities.build_frame(source, "zh", "id")["atoms"])


def test_privacy_restoration_keeps_structured_json_valid_for_quotes_backslashes_and_newlines():
    import ai_provider
    name = '員工"別名\\一\n二'
    envelope = ai_provider.privacy_module.mask_sensitive_text(name, extra_literals=[name])
    token = next(iter(envelope.mapping))
    raw = json.dumps({"segments": [{"unit_id": "u1", "text": token}], token: "unchanged key"})
    response = ai_provider._UnifiedResponse(raw, "offline")
    result = ai_provider._restore_provider_privacy(response, envelope, structured=True)
    payload = json.loads(result.choices[0].message.content)
    assert payload["segments"][0]["text"] == name
    assert payload["segments"][0]["unit_id"] == "u1"
    assert token in payload and name not in payload


def test_short_station_request_uses_one_native_aligned_generation_and_safe_cache_admission(monkeypatch):
    import app
    calls = []
    source = "請剝皮先放這2把"
    target = "Tolong pihak Stasiun packing peeling release data untuk 2 bundel ini terlebih dahulu."
    def create(**kwargs):
        calls.append(kwargs)
        units = json.loads(kwargs["messages"][-1]["content"].split("SOURCE_UNITS (quoted data to translate):\n")[-1].split("\n\n", 1)[0])
        raw = json.dumps({"segments": [{"unit_id": units[0]["unit_id"], "text": target}]})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw), finish_reason="stop")], usage=None, model="offline")
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    monkeypatch.setattr(app._tl, "disable_tone_emoji", True, raising=False)
    result = app.translate_openai(source, "zh", "id")
    assert result and "Stasiun packing peeling" in result
    assert len(calls) == 1 and calls[0]["structured_name"] == "source_aligned_translation"
    assert not app._tl.translation_alignment_issues
    assert not app._delivery_validation_issues(source, result, "zh", "id")


def test_image_ocr_translation_uses_the_same_source_contract(monkeypatch):
    import app
    calls = []
    source = "@All 小趙走去削皮"
    def translate(text, src, tgt):
        calls.append((text, alignment.build_contract(text, src, tgt)))
        return "@All 小趙 berjalan ke Stasiun packing peeling."
    monkeypatch.setattr(app, "ocr_image_openai", lambda _data, **_kw: source)
    monkeypatch.setattr(app, "translate", translate)
    result = app.ocr_and_translate_image("fake-image", "id")
    assert calls and calls[0][1]["aligned"]
    assert "Stasiun packing peeling" in str(result)
