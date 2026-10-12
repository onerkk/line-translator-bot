"""Typed factory entities at generation, reuse, privacy and learning boundaries.

Run with run_translation_offline_checks.py. All provider output is a fixture;
no live AI request, paid API call, LINE delivery or deployment occurs here.
"""
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import active_learning as learning
import factory_terminology as terms
import glossary_enforcement as enforcement
import prompt_optimizer as prompts
import translation_alignment as alignment
import translation_learning_policy as policy
import translation_privacy as privacy
import translation_quality_gate as quality


REVIEW = json.loads((Path(__file__).parent / "tests/data/translation_review_20261011.json").read_text())
SOURCE, BAD, GOOD = (REVIEW[key] for key in ("source", "bad_target", "target"))


@pytest.mark.parametrize("source,target", [
    ("全鹽水廠包裝異型棒。", "Seluruh Pabrik Yenshui mengemas batang profil khusus."),
    ("全盐水厂包装异型棒。", "Seluruh Pabrik Yenshui mengemas batang bentuk khusus."),
    ("鹽水廠的異型棒已入庫。", "Batang profil khusus di Pabrik Yanshui sudah masuk gudang."),
    ("鹽水廠的異型棒已入庫。", "Batang berbentuk khusus di Pabrik Yenshui sudah masuk gudang."),
    ("鹽水廠的異型棒已入庫。", "Batang berprofil khusus di Pabrik Yenshui sudah masuk gudang."),
    ("台中廠的異型棒已入庫。", "Batang bentuk khusus di Pabrik Taichung sudah masuk gudang."),
    ("臺中廠的異型棒已入庫。", "Batang profil khusus di Pabrik Taichung sudah masuk gudang."),
    ("Batang profil khusus di Pabrik Yenshui sudah masuk gudang.", "鹽水廠的異型棒已入庫。"),
    ("Batang bentuk khusus di Pabrik Yanshui sudah masuk gudang.", "鹽水廠的異型棒已入庫。"),
])
def test_product_and_facility_have_approved_bidirectional_meaning(source, target):
    src, tgt = ("id", "zh") if source.startswith("Batang") else ("zh", "id")
    report = quality.validate_translation(source, target, src, tgt)
    assert report.ok, report.issues


def test_screenshot_is_repaired_locally_without_changing_other_claims():
    assert not quality.validate_translation(SOURCE, BAD, "zh", "id").ok
    fixed = quality.canonicalize_source_terms(SOURCE, BAD, "zh", "id")
    assert fixed == GOOD
    assert quality.validate_translation(SOURCE, fixed, "zh", "id").ok
    assert "530 ton" in fixed and "20 ton" in fixed and "3 ton" in fixed
    assert "atau" in fixed and "akhir bulan atau sampai akhir tahun" in fixed


@pytest.mark.parametrize("target,expected", [
    ('Pabrik garam mengemas "異型棒".', 'Pabrik Yenshui mengemas "batang profil khusus".'),
    ("Pabrik Yanshui (鹽水廠) mengemas batang profil khusus (異型棒).",
     "Pabrik Yanshui mengemas batang profil khusus."),
])
def test_generated_quotes_and_legacy_bilingual_echoes_do_not_hide_untranslated_terms(target, expected):
    source = "鹽水廠包裝異型棒。"
    assert not quality.validate_translation(source, target, "zh", "id").ok
    fixed = terms.canonicalize_factory_entities(source, target, "zh", "id")
    assert fixed == expected
    assert quality.validate_translation(source, fixed, "zh", "id").ok


@pytest.mark.parametrize("target,issue", [
    ("Seluruh pabrik garam mengemas batang profil khusus.", "factory_entity_missing:鹽水廠"),
    ("Seluruh Pabrik Taichung mengemas batang profil khusus.", "factory_entity_missing:鹽水廠"),
    ("Seluruh Pabrik Yenshui mengemas 異型棒.", "factory_entity_untranslated:異型棒"),
    ("Seluruh Pabrik Yenshui mengemas barang.", "factory_entity_missing:異型棒"),
])
def test_wrong_identity_or_missing_product_is_rejected_at_shared_gate(target, issue):
    report = quality.validate_translation("全鹽水廠包裝異型棒。", target, "zh", "id")
    assert not report.ok and issue in report.hard_issues


def test_terms_survive_prompt_compilation_and_reverse_lookup():
    glossary = terms.approved_glossary()
    retrieved = terms.build_translation_prompt(SOURCE, glossary, "zh", "id")
    compiled, _ = prompts.compile_translation_prompt(retrieved, SOURCE, "zh", "id")
    for pair in ("鹽水廠 => Pabrik Yenshui", "異型棒 => batang profil khusus"):
        assert pair in compiled
    assert "台中廠 =>" not in compiled
    reverse = enforcement.build_safe_reverse_index(glossary)
    assert reverse["batang profil khusus"]["target_term"] == "異型棒"
    assert reverse["pabrik yenshui"]["target_term"] == "鹽水廠"
    assert "pabrik garam" not in reverse


@pytest.mark.parametrize("source,target", [
    ("鹽水桶已清洗。", "Ember air garam sudah dibersihkan."),
    ("製鹽工廠已停工。", "Pabrik garam sudah berhenti beroperasi."),
    ("台中今天下雨。", "Hari ini hujan di Taichung."),
    ("@異型棒 請回報。", "@異型棒 Mohon laporkan."),
    ('標籤是「異型棒」。', 'Labelnya adalah「異型棒」。'),
])
def test_literal_senses_places_mentions_and_labels_are_not_rewritten(source, target):
    assert not terms.factory_entity_facts(source, "zh")
    assert terms.canonicalize_factory_entities(source, target, "zh", "id") == target


@pytest.mark.parametrize("source,target", [
    ("鹽水廠與製鹽工廠都已停工。", "Pabrik garam dan pabrik garam sudah berhenti beroperasi."),
    ("鹽水廠與台中廠都已停工。", "Pabrik garam dan pabrik garam sudah berhenti beroperasi."),
    ("鹽水廠已停工。", "Pabrik garam dan pabrik garam sudah berhenti beroperasi."),
])
def test_ambiguous_facility_assignment_is_diagnosed_without_guessing(source, target):
    assert terms.canonicalize_factory_entities(source, target, "zh", "id") == target
    assert terms.factory_entity_translation_issues(source, target, "zh", "id")


def test_nonstandard_product_and_facility_use_the_same_data_driven_contract():
    glossary = {
        "甲廠": {"idn": "Pabrik Contoh", "translation_mode": "hard", "entity_kind": "facility",
                  "forbidden_idn": ["pabrik salah"], "reverse_safe": True},
        "多角棒": {"idn": "batang poligonal", "translation_mode": "hard", "entity_kind": "product"},
    }
    source = "甲廠包裝多角棒。"
    old = "Pabrik salah mengemas 多角棒."
    good = "Pabrik Contoh mengemas batang poligonal."
    assert terms.factory_entity_translation_issues(source, old, "zh", "id", glossary=glossary)
    assert terms.canonicalize_factory_entities(source, old, "zh", "id", glossary=glossary) == good
    assert not terms.factory_entity_translation_issues(source, good, "zh", "id", glossary=glossary)


def test_reloaded_entity_policy_invalidates_a_previous_validation_result(monkeypatch):
    source, target = "甲廠已停工。", "Pabrik Contoh sudah berhenti beroperasi."
    first = {"甲廠": {"idn": "Pabrik Contoh", "translation_mode": "hard", "entity_kind": "facility"}}
    second = {"甲廠": {"idn": "Pabrik Baru", "translation_mode": "hard", "entity_kind": "facility"}}
    monkeypatch.setattr(terms, "approved_glossary", lambda: first)
    assert quality.validate_translation(source, target, "zh", "id").ok
    monkeypatch.setattr(terms, "approved_glossary", lambda: second)
    changed = quality.validate_translation(source, target, "zh", "id")
    assert not changed.ok and "factory_entity_missing:甲廠" in changed.issues


def test_products_do_not_get_masked_by_an_untyped_name_list(monkeypatch):
    import app
    monkeypatch.setattr(app, "CUSTOMER_NAMES", ["異型棒", "鹽水廠", "阿堂"])
    monkeypatch.setattr(app, "_line_user_protected_names", lambda: [])
    source = "阿堂，鹽水廠的異型棒已入庫。"
    assert app.collect_visible_protected_names(source) == ["阿堂"]
    envelope = privacy.mask_sensitive_text(source, extra_literals=["異型棒", "鹽水廠", "阿堂"], enabled=True)
    assert list(envelope.mapping.values()) == ["阿堂"]
    assert "異型棒" in envelope.masked and "鹽水廠" in envelope.masked
    assert privacy.restore_sensitive_text(envelope.masked, envelope) == source


def test_longest_source_match_separates_station_from_product():
    source = "異型包裝站要優先包裝異型棒。"
    facts = terms.factory_entity_facts(source, "zh")
    assert [fact["source"] for fact in facts] == ["異型棒"]
    shaped = terms.get_engine(terms.approved_glossary()).match_zh("S、H異型棒")
    assert [match.source_term for match in shaped] == ["S、H異型棒"]


def test_entity_in_other_unit_cannot_hide_an_omission():
    contract = alignment.build_contract("鹽水廠已停工。\n台中廠已復工。", "zh", "id")
    bad = json.dumps({"segments": [
        {"unit_id": "u1", "text": "Pabrik sudah berhenti beroperasi."},
        {"unit_id": "u2", "text": "Pabrik Yenshui dan Pabrik Taichung sudah beroperasi kembali."}]})
    report = alignment.decode_response(bad, contract)
    assert not report.ok
    assert "semantic:factory_entity_missing:鹽水廠" in report.issues


def test_aligned_unit_can_repair_one_facility_in_a_notice_with_multiple_facilities():
    contract = alignment.build_contract("鹽水廠已停工。\n台中廠已復工。", "zh", "id")
    raw = json.dumps({"segments": [
        {"unit_id": "u1", "text": "Pabrik garam sudah berhenti beroperasi."},
        {"unit_id": "u2", "text": "Pabrik Taichung sudah beroperasi kembali."}]})
    report = alignment.decode_response(raw, contract)
    assert report.ok, report.issues
    assert report.text.startswith("Pabrik Yenshui")
    assert report.original_text.startswith("Pabrik garam")


def test_learning_replays_old_and_new_before_admitting_a_term_lesson():
    assert not learning.validate_correction(SOURCE, BAD, "zh", "id")["ok"]
    assert learning.validate_correction(SOURCE, GOOD, "zh", "id")["ok"]
    lessons = policy.derive(SOURCE, BAD, GOOD, "zh", "id", reviewed=True, cacheable=True,
                            validator=learning.validate_correction)
    assert "terminology" in {lesson["category"] for lesson in lessons}
    assert not policy.derive(SOURCE, GOOD, BAD, "zh", "id", reviewed=True, cacheable=True,
                             validator=learning.validate_correction)


@pytest.mark.parametrize("ocr", [False, True], ids=["text", "ocr"])
def test_actual_entry_uses_one_generation_and_verified_repeat_uses_zero(monkeypatch, ocr):
    import app
    monkeypatch.setattr(app, "_tl", threading.local())
    monkeypatch.setattr(app, "translation_cache", {})
    monkeypatch.setattr(app, "_event_log_write", lambda *a, **k: None)
    monkeypatch.setattr(app, "_post_translation_async", lambda *a, **k: None)
    app._tl.group_id = "G-oct11-entities-" + str(ocr)
    app._tl.from_image_ocr = ocr
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        content = kwargs["messages"][-1]["content"]
        payload, _ = json.JSONDecoder().raw_decode(content.split("SOURCE_UNITS (quoted data to translate):", 1)[1].lstrip())
        assert len(payload) == len(REVIEW["target_units"])
        raw = json.dumps({"segments": [
            {"unit_id": unit["unit_id"], "text": target}
            for unit, target in zip(payload, REVIEW["bad_target_units"])]})
        response = SimpleNamespace(model="offline-fixture", choices=[SimpleNamespace(
            message=SimpleNamespace(content=raw), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))
        ok, reason = kwargs["response_validator"](response, "fixture")
        assert ok, reason
        return response
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    result = app.translate(SOURCE, "zh", "id")
    assert "Pabrik Yenshui" in result and "pabrik garam" not in result
    assert "異型棒" not in result and "batang profil khusus" in result
    assert quality.validate_translation(SOURCE, result, "zh", "id").ok
    assert len(calls) == 1
    assert calls[0]["translation_max_generations"] == 1
    assert app.translate(SOURCE, "zh", "id") == result
    assert len(calls) == 1
