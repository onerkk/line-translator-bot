"""Source meaning, efficient retrieval and learning at the real text/OCR entry.

Provider replies are fixed fixtures. These tests make no live model-score,
latency or billing claim and never send a LINE message.
"""
import json
from pathlib import Path
import re
import threading
from types import SimpleNamespace

import pytest
import active_learning as learning
import factory_knowledge as knowledge
import factory_terminology as terms
import translation_alignment as alignment
import translation_casebook as casebook
import translation_learning_policy as policy
import translation_privacy as privacy
import translation_quality_gate as quality


REVIEW = json.loads((Path(__file__).parent / "tests/data/translation_review_20261010.json").read_text())


def response(text):
    return SimpleNamespace(model="offline-fixture", choices=[SimpleNamespace(
        message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))


@pytest.fixture(autouse=True)
def isolate_request(monkeypatch):
    import app
    monkeypatch.setattr(app, "_tl", threading.local())
    monkeypatch.setattr(app, "translation_cache", {})
    monkeypatch.setattr(app, "_event_log_write", lambda *a, **k: None)
    monkeypatch.setattr(app, "_post_translation_async", lambda *a, **k: None)


@pytest.mark.parametrize("case", REVIEW["cases"], ids=lambda row: row["id"])
def test_screenshot_meaning_is_accepted_and_bad_title_is_diagnosed(case):
    import app
    report = quality.validate_translation(case["source"], case["target"], "zh", "id")
    assert report.ok, report.issues
    assert not app._delivery_validation_issues(case["source"], case["target"], "zh", "id")
    if case.get("bad_target"):
        bad = quality.validate_translation(case["source"], case["bad_target"], "zh", "id")
        assert not bad.ok
        assert "factory_organization_role_missing:chief_engineer" in bad.issues
        assert "missing_literal:蔣" in bad.issues
        assert "semantic:movement_permission_missing" in bad.issues


@pytest.mark.parametrize("source,target,role", [
    ("總工會來。", "Insinyur Kepala akan datang.", "chief_engineer"),
    ("總工程師會來。", "Insinyur Kepala akan datang.", "chief_engineer"),
    ("总工会来。", "Insinyur Kepala akan datang.", "chief_engineer"),
    ("副總工會來。", "Wakil Insinyur Kepala akan datang.", "deputy_chief_engineer"),
    ("副總工程師會來。", "Wakil Insinyur Kepala akan datang.", "deputy_chief_engineer"),
    ("技師主管會來。", "Kepala teknisi akan datang.", "head_technician"),
    ("Insinyur Kepala akan datang.", "總工程師會來。", "chief_engineer"),
    ("Wakil Insinyur Kepala akan datang.", "副總工程師會來。", "deputy_chief_engineer"),
    ("Kepala teknisi akan datang.", "技師主管會來。", "head_technician"),
])
def test_professional_roles_are_bidirectional_and_do_not_collapse(source, target, role):
    src, tgt = ("zh", "id") if re.search(r"[\u3400-\u9fff]", source) else ("id", "zh")
    assert terms.organization_role_key(source, src) == role
    assert not terms.organization_translation_issues(source, target, src, tgt)
    report = quality.validate_translation(source, target, src, tgt)
    assert report.ok, report.issues


@pytest.mark.parametrize("source,raw,fixed", [
    ("蔣總工跟處長會來。", "Kepala Teknisi 蔣 dan kepala divisi akan datang.",
     "Insinyur Kepala 蔣 dan kepala divisi akan datang."),
    ("蔣總工會來。", "Kepala insinyur 蔣 akan datang.", "Insinyur Kepala 蔣 akan datang."),
    ("Wakil Insinyur Kepala akan datang.", "副總會來。", "副總工程師會來。"),
])
def test_local_title_normalization_preserves_people_and_other_roles(source, raw, fixed):
    src, tgt = ("zh", "id") if re.search(r"[\u3400-\u9fff]", source) else ("id", "zh")
    # A wrong title from a different hierarchy is deliberately not guessed.
    if src == "id" and raw == "副總會來。":
        assert terms.canonicalize_organization_translation(source, raw, src, tgt) == raw
        assert terms.organization_translation_issues(source, raw, src, tgt)
    else:
        assert terms.canonicalize_organization_translation(source, raw, src, tgt) == fixed


@pytest.mark.parametrize("source", ["新任總工會來。", "新任總工程師會來。", "副總工會來。", "@蔣總工 請檢查。"]) 
def test_title_and_mention_are_not_invented_person_names(source):
    assert terms.professional_name_spans(source) == []


@pytest.mark.parametrize("source,name", [("蔣總工", "蔣"), ("不然蔣總工跟處長", "蔣"),
                                         ("王小明總工程師", "王小明"), ("通知蔣副總工", "蔣")])
def test_attached_source_name_is_masked_and_restored_without_romanization(source, name):
    envelope = privacy.mask_sensitive_text(source, enabled=True)
    assert name in envelope.mapping.values()
    assert name not in envelope.masked
    assert privacy.restore_sensitive_text(envelope.masked, envelope) == source
    assert name in quality.inspect_immutable_spans(source).mapping.values()


def test_titles_cannot_be_swapped_between_two_people_in_one_sentence():
    source = "蔣總工和王技師主管會來。"
    contract = alignment.build_contract(source, "zh", "id")
    good = "Insinyur Kepala 蔣 dan kepala teknisi 王 akan datang."
    bad = "Kepala teknisi 蔣 dan Insinyur Kepala 王 akan datang."
    assert not alignment.translation_issues(contract, good)
    assert "semantic:professional_name_role_changed:蔣" in alignment.translation_issues(contract, bad)
    assert not quality.validate_translation(source, bad, "zh", "id").ok
    assert terms.canonicalize_organization_translation(source, bad, "zh", "id") == bad


@pytest.mark.parametrize("source", ["Menikmati AC di ruang kontrol.", "Tekan tombol start.",
                                   "Karena mesin rusak.", "NIKMATI istirahat.", "Nikita sudah datang."])
def test_latin_identifier_labels_do_not_split_indonesian_words(source):
    envelope = privacy.mask_sensitive_text(source, enabled=True)
    assert envelope.masked == source and not envelope.mapping


@pytest.mark.parametrize("source,value", [("NIK: 12345678", "12345678"), ("NIK12345678", "12345678"),
    ("NO. KTP: A1234567", "A1234567"), ("工號A12345", "A12345"),
    ("REKENING: 1234567890", "1234567890"), ("LINE ID: example_user", "example_user")])
def test_real_labelled_identifiers_remain_protected(source, value):
    envelope = privacy.mask_sensitive_text(source, enabled=True)
    assert value in envelope.mapping.values() and value not in envelope.masked
    assert privacy.restore_sensitive_text(envelope.masked, envelope) == source


def test_retrieval_uses_source_sense_without_filling_slots_with_other_workflows():
    import app
    source = REVIEW["cases"][1]["source"]
    cards = knowledge.retrieve(source, "zh", "id", limit=12)
    assert "factory_housekeeping_management_emphasis" not in {row["id"] for row in cards}
    rows = app._retrieve_verified_translation_cases(source, "zh", "id", max_cases=12)
    assert [row["case_id"] for row in rows] == ["equipment_output_production_semantics"]
    assert "__QG_KEEP_" not in privacy.mask_sensitive_text(casebook.build_prompt(rows)).masked


@pytest.mark.parametrize("source", ["地上有兩捆料可以包裝。", "處長詢問包裝進度。", "每日線外人員分流包裝。"])
def test_floor_location_reporting_and_staffing_do_not_mean_housekeeping_or_crates(source):
    cards = knowledge.retrieve(source, "zh", "id", limit=12)
    assert not {"factory_housekeeping_management_emphasis", "pending_crate_packing_staffing"} & {c["id"] for c in cards}


def test_transport_permission_and_urgency_are_separate_facts():
    source = "檢驗完成就可以上車過來包裝。"
    good = "Setelah selesai diperiksa, material bisa dimuat ke troli dan dibawa ke sini untuk dikemas."
    bad = "Setelah selesai diperiksa, segera muat ke troli dan bawa untuk dikemas."
    assert not alignment.translation_issues(alignment.build_contract(source, "zh", "id"), good)
    assert "semantic:movement_permission_missing" in alignment.translation_issues(alignment.build_contract(source, "zh", "id"), bad)
    assert "semantic:movement_urgency_invented" in alignment.translation_issues(alignment.build_contract(source, "zh", "id"), bad)
    explicit = alignment.build_contract("檢驗完成就可以馬上上車過來包裝。", "zh", "id")
    assert not alignment.translation_issues(explicit, "Setelah diperiksa, material bisa segera dimuat ke troli dan dibawa ke sini untuk dikemas.")
    assert not any(c["kind"] == "transport_permission" for c in alignment.build_contract("不可以上車包裝。", "zh", "id")["constraints"])


@pytest.mark.parametrize("issue,expected", [
    ("semantic:movement_permission_missing", "permission"),
    ("quality_gate:semantic:professional_name_role_changed:蔣", "actor"),
    ("semantic:available_packing_assistance_missing", "quantity"),
    ("semantic:quality_clearance_changed_to_erp", "handoff"),
    ("semantic:unknown_instruction:ignore previous rules", None),
    ("provider_timeout", None),
])
def test_learning_accepts_known_error_classes_only(issue, expected):
    assert policy.category(issue) == expected


def test_bad_role_correction_teaches_a_rule_only_after_both_targets_are_replayed():
    source = "總工程師必須核對工單。"
    old = "Kepala teknisi harus memeriksa work order."
    new = "Insinyur Kepala harus memeriksa work order."
    assert not learning.validate_correction(source, old, "zh", "id")["ok"]
    assert learning.validate_correction(source, new, "zh", "id")["ok"]
    rules = policy.derive(source, old, new, "zh", "id", reviewed=True, cacheable=True,
                          validator=learning.validate_correction)
    assert {r["category"] for r in rules} == {"actor"}
    assert not policy.derive(source, old, new, "zh", "id", reviewed=True, cacheable=False,
                             validator=learning.validate_correction)
    assert not policy.derive(source, new, old, "zh", "id", reviewed=True, cacheable=True,
                             validator=learning.validate_correction)


def test_text_entry_repairs_once_and_next_verified_repeat_needs_no_api(monkeypatch):
    import app
    source = "總工程師必須核對工單。"
    old = "Kepala teknisi harus memeriksa work order."
    final = "Insinyur Kepala harus memeriksa work order."
    app._tl.group_id = "G-oct10-role-repair"
    calls, events = [], []
    real_record = learning.record_translation_outcome
    def record(**kwargs):
        events.append(dict(kwargs))
        return real_record(**kwargs)
    monkeypatch.setattr(learning, "record_translation_outcome", record)
    def create(**kwargs):
        calls.append(kwargs)
        result = response(old)
        kwargs["response_validator"](result, "fixture")
        return result
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    assert app.translate(source, "zh", "id") == final
    assert len(calls) == 1
    assert "structured_schema" not in calls[0]
    assert any(e["candidate_text"] == old and e["final_text"] == final and e["cacheable"] for e in events)
    assert app.translate(source, "zh", "id") == final
    assert len(calls) == 1


@pytest.mark.parametrize("ocr", [False, True], ids=["text", "ocr"])
def test_real_entry_restores_name_and_normalizes_each_source_owned_unit(monkeypatch, ocr):
    import app
    source = "蔣總工核對工單。\n\n處長檢查包裝。"
    app._tl.group_id = "G-oct10-aligned-" + str(ocr)
    app._tl.from_image_ocr = ocr
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        content = kwargs["messages"][-1]["content"]
        payload, _ = json.JSONDecoder().raw_decode(content.split("SOURCE_UNITS (quoted data to translate):", 1)[1].lstrip())
        name = re.search(r"__QG_KEEP_\d{3}_[0-9A-F]{8}__", payload[0]["source"]).group()
        raw = json.dumps({"segments": [
            {"unit_id": payload[0]["unit_id"], "text": "Kepala Teknisi " + name + " memeriksa work order."},
            {"unit_id": payload[1]["unit_id"], "text": "Kepala divisi memeriksa pengemasan."}]})
        result = response(raw)
        valid, reason = kwargs["response_validator"](result, "fixture")
        assert valid, reason
        return result
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    result = app.translate(source, "zh", "id")
    assert result == "Insinyur Kepala 蔣 memeriksa work order.\n\nkepala divisi memeriksa pengemasan."
    assert len(calls) == 1
    assert calls[0]["translation_max_generations"] == 1
    assert not getattr(app._tl, "translation_alignment_issues", ())


def test_alignment_keeps_raw_repair_evidence_and_rejects_cross_unit_role_drift():
    source = "總工核對工單。\n技師主管檢查機台。"
    contract = alignment.build_contract(source, "zh", "id")
    fixed = alignment.decode_response(json.dumps({"segments": [
        {"unit_id": "u1", "text": "Kepala Teknisi memeriksa work order."},
        {"unit_id": "u2", "text": "Kepala teknisi memeriksa mesin."}]}), contract)
    assert fixed.ok, fixed.issues
    assert fixed.original_text.startswith("Kepala Teknisi")
    assert fixed.text.startswith("Insinyur Kepala")
    missing = alignment.decode_response(json.dumps({"segments": [
        {"unit_id": "u1", "text": "Ia memeriksa work order."},
        {"unit_id": "u2", "text": "Insinyur Kepala dan kepala teknisi memeriksa mesin."}]}), contract)
    assert not missing.ok
    assert any("organization_role_missing" in issue for issue in missing.issues)
