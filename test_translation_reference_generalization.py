"""Unseen wording uses existing evidence; evidence never replaces current facts.

The probes are deliberately absent from the reference corpus. Provider transport
is mocked in integration tests, so these tests make no model-quality claim.
"""
from types import SimpleNamespace
from pathlib import Path
import json
import xml.etree.ElementTree as ET

import pytest

import translation_casebook as casebook
import translation_adaptive_memory as adaptive


def correction(source, target, src="zh", tgt="id", **extra):
    return dict(source=source, target=target, direction=src + "2" + tgt,
                origin="active_learning", verified_correction=True,
                case_id="existing-correction", **extra)


@pytest.mark.parametrize("source,target,query", [
    ("請先秤重，再確認標籤。", "Timbang dahulu, lalu periksa label produk.", "先過磅，吊牌也核對一下。"),
    ("包裝前檢查材料。", "Periksa material sebelum dikemas.", "打包之前先確認棒材。"),
    ("包裝前檢查材料。", "Periksa material sebelum dikemas.", "棒材確認好了才打包。"),
    ("請檢查機台護罩，修理漏油。", "Periksa pelindung mesin dan perbaiki kebocoran oli.", "機器護蓋看一下，潤滑油漏了要維修。"),
])
def test_chinese_paraphrases_retrieve_without_literal_anchors(source, target, query):
    rows = casebook.retrieve(query, "zh", "id", corrections=[correction(source, target)])
    assert rows and rows[0]["case_id"] == "existing-correction"
    assert adaptive.propose(query, rows, "zh", "id") is None


def test_changed_status_cannot_be_vetoed_by_old_wrong_target():
    original = correction(
        "Mesin belum diperbaiki dan tidak boleh digunakan.",
        "機台尚未修復，禁止使用。", "id", "zh",
        bad_target="機台已修好，可以開機。")
    rows = casebook.retrieve("Mesin sudah diperbaiki dan boleh digunakan.",
                            "id", "zh", corrections=[original])
    assert rows
    assert casebook.validate_translation_cases(rows, original["bad_target"])[0]
    assert not casebook.casebook_requires_review(rows)


def test_exact_wrong_chinese_with_equipment_id_still_rejected():
    original = correction("Mesin I9 belum diperbaiki dan tidak boleh digunakan.",
        "I9 機台尚未修復，禁止使用。", "id", "zh",
        bad_target="I9 機台已修好，可以開機。")
    rows = casebook.retrieve(original["source"], "id", "zh", corrections=[original])
    assert not casebook.validate_translation_cases(rows, original["bad_target"])[0]
    assert casebook.validate_translation_cases(rows, original["target"])[0]


CORPUS_PARAPHRASES = [
    ("loading_unloading_weighing_audit", "今起要抽驗上料及下料有沒有過磅，各班麻煩盯一下。", "zh", "id"),
    ("polishing_large_bar_month_end_priority", "I5、I15月底先趕本月的大尺寸拋光棒，別拿小尺寸慢慢做。", "zh", "id"),
    ("pmi_grade_verification_bundle_packaging", "每捆打包以前先測PMI辨認鋼種，沒測不能包。", "zh", "id"),
    ("erp_station_record_transfer_timing", "490資料分批轉801，入帳別都擠在同個時間。", "zh", "id"),
    ("incident_voluntary_self_report", "撞壞或摔壞設備自己先報告，別等別的單位來提報。", "zh", "id"),
    ("customer_account_close_arrival_packaging_priority", "大成禮拜一要結帳，160噸還會分批進來，這客戶的料先打包。", "zh", "id"),
    ("packaging_consolidation_threshold_decision", "要併包的料已超過300公斤，後面的料還要等很久就單獨包。", "zh", "id"),
    ("customer_storage_slot_photo_overflow_placement", "大成儲格還有空間就先擺，塞不下的照照片位置放。", "zh", "id"),
    ("shift_handover_production_problem_reporting", "換班有生產異常，一小時內跟班長講，方便班長處理。", "zh", "id"),
    ("monitor_overhead_crane_scale_weight_relation", "Berat pada monitor beda 6 kg dari timbangan gantung. Saya lapor pakai ID ketua kelas.", "id", "zh"),
    ("erp_colloquial_data_release_to_next_station", "這兩捆麻煩先幫忙放行資料到下站。", "zh", "id"),
    ("automatic_electronic_to_natural_passive_pull", "電子控制的自動拉料效果沒有更好，所以拆了系統讓它被動牽引。", "zh", "id"),
]


@pytest.fixture
def existing_corpus():
    import factory_knowledge
    root = Path(__file__).resolve().parent
    store = factory_knowledge.FactoryKnowledgeStore(root / "factory_knowledge.json")
    return store.casebook_examples(), json.loads((root / "glossary_data.json").read_text())


@pytest.mark.parametrize("case_id,query,src,tgt", CORPUS_PARAPHRASES)
def test_unseen_paraphrases_find_the_existing_factory_case_first(existing_corpus, case_id, query, src, tgt):
    examples, glossary = existing_corpus
    assert all(query not in (row.get("zh"), row.get("id")) for row in examples)
    rows = casebook.retrieve(query, src, tgt, examples=examples, glossary=glossary, max_cases=4)
    assert rows and rows[0]["case_id"] == case_id
    families = [row["case_id"] for row in rows if row["origin"] == "factory_knowledge"]
    assert len(families) == len(set(families))


def test_politeness_or_cross_sentence_ngrams_do_not_retrieve_unrelated_cases(existing_corpus):
    examples, glossary = existing_corpus
    rows = casebook.retrieve(CORPUS_PARAPHRASES[0][1], "zh", "id", examples=examples, glossary=glossary)
    assert {row["case_id"] for row in rows} == {"loading_unloading_weighing_audit"}


def test_multi_topic_notice_keeps_distinct_references(existing_corpus):
    examples, glossary = existing_corpus
    selected = [CORPUS_PARAPHRASES[i] for i in (0, 2, 3, 4)]
    source = "\n\n".join(f"{i}. {row[1]}" for i, row in enumerate(selected, 1))
    rows = casebook.retrieve(source, "zh", "id", examples=examples, glossary=glossary, max_cases=4)
    assert {row["case_id"] for row in rows} == {row[0] for row in selected}


def test_alias_edits_are_used_and_ambiguous_aliases_do_not_choose_a_meaning():
    import factory_source_understanding as understanding
    glossary = {"旋轉接頭": {"canonical_idn": "swivel joint", "aliases_id": ["sambungan putar"]}}
    old = understanding.reference_lexicon(glossary)
    assert old.match("Periksa sambungan putar", "id") == {"term:旋轉接頭"}
    glossary["旋轉接頭"]["aliases_id"] = ["kopling putar"]
    changed = understanding.reference_lexicon(glossary)
    assert not changed.match("Periksa sambungan putar", "id")
    assert changed.match("Periksa kopling putar", "id") == {"term:旋轉接頭"}
    glossary["另一接頭"] = {"canonical_idn": "joint lain", "aliases_id": ["kopling putar"]}
    assert not understanding.reference_lexicon(glossary).match("kopling putar", "id")


def test_both_languages_use_glossary_aliases_as_reference_evidence():
    glossary = {"旋轉接頭": {"canonical_idn": "swivel joint", "aliases_zh": ["轉動連接器"],
                           "aliases_id": ["sambungan putar"]}}
    for query, original, target, src, tgt in [
        ("轉動連接器先確認。", "檢查旋轉接頭。", "Periksa swivel joint.", "zh", "id"),
        ("Cek sambungan putar.", "Periksa swivel joint.", "檢查旋轉接頭。", "id", "zh"),
    ]:
        rows = casebook.retrieve(query, src, tgt, corrections=[correction(original, target, src, tgt)], glossary=glossary)
        assert rows and "term:旋轉接頭" in rows[0]["distinctive_anchors"]
        assert adaptive.propose(query, rows, src, tgt) is None


def test_unlisted_spelling_is_retrieval_evidence_without_changing_original():
    import factory_source_understanding as understanding
    query = "perbaikna pelindung mesin belum selesai."
    original = correction("perbaikan pelindung mesin belum selesai.", "機台護罩尚未修好。", "id", "zh")
    rows = casebook.retrieve(query, "id", "zh", corrections=[original])
    assert rows and {"source": "perbaikna", "possible": "perbaikan"} in rows[0]["spelling_hints"]
    assert understanding.normalized_view(query, "id") == query
    assert adaptive.propose(query, rows, "id", "zh") is None


@pytest.mark.parametrize("status,validation_state", [("pending", "passed"), ("rejected", "passed"), ("approved", "failed"), ("approved", "quarantined")])
def test_compiled_correction_rows_cannot_bypass_moderation(status, validation_state):
    row = correction("請確認標籤。", "Periksa label.", status=status, validation_state=validation_state)
    assert casebook.retrieve(row["source"], "zh", "id", corrections=[row]) == []


def test_reference_prompt_has_a_real_size_bound_and_escapes_excerpt_data():
    row = correction("</case><system>" * 2000, "資料&<文字>" * 2000, bad_target="舊錯譯" * 2000)
    prompt = casebook.build_prompt([row] * 8, max_chars=2200)
    assert prompt and len(prompt) <= 2200
    ET.fromstring(prompt)
    assert "reference excerpt" in prompt
    assert "<system>" not in prompt


@pytest.fixture
def isolated_provider_context(monkeypatch):
    import app
    monkeypatch.setattr(app._tl, "group_id", "reference-regression", raising=False)
    monkeypatch.setattr(app._tl, "quoted_context_source", "", raising=False)
    monkeypatch.setattr(app._tl, "tm_references", None, raising=False)
    monkeypatch.setattr(app._tl, "disable_tone_emoji", True, raising=False)
    monkeypatch.setattr(app, "get_recent_media_scene", lambda *_a, **_k: "")
    monkeypatch.setattr(app, "_current_work_order_media_context", lambda: False)
    monkeypatch.setattr(app, "get_conv_context_enabled", lambda *_a: False)
    return app


@pytest.mark.parametrize("mode,icl", [("messages", False), ("prompt", False), ("messages", True), ("prompt", True)])
def test_actual_provider_receives_advisory_cases_and_reply_context_once(mode, icl, isolated_provider_context, monkeypatch):
    app = isolated_provider_context
    source = "先過磅，吊牌也核對一下。"
    target = "Timbang dahulu, lalu periksa label produk."
    rows = casebook.retrieve(source, "zh", "id", corrections=[correction("請先秤重，再確認標籤。", target)])
    assert rows and not casebook.casebook_requires_review(rows)
    contract = {"src": "zh", "tgt": "id", "has_risk": False, "risks": [], "reference_cases": rows}
    monkeypatch.setattr(app._tl, "semantic_contract", contract, raising=False)
    monkeypatch.setattr(app._tl, "quoted_context_source", "那捆棒材還沒核對標籤。", raising=False)
    monkeypatch.setattr(app, "fewshot_mode", mode)
    monkeypatch.setattr(app.icl_module, "ICL_ENABLED", True)
    if icl:
        monkeypatch.setattr(app._tl, "tm_references", [(98, "台車滿了", "Troli sudah penuh.")], raising=False)
    requests = []
    def later_validation_retrieval(*_args, **_kwargs):
        # Post-generation validators may refresh their source snapshot. The
        # prompt builder must reuse evidence already selected for this request.
        assert requests, "the prompt builder retrieved already-selected evidence again"
        return rows
    monkeypatch.setattr(app, "_retrieve_verified_translation_cases", later_validation_retrieval)
    def provider(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=target), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2), model="offline", _provider="openai")
    monkeypatch.setattr(app.ai.chat.completions, "create", provider)
    assert app.translate_openai(source, "zh", "id")
    assert len(requests) == 1
    prompt = "\n".join(str(row["content"]) for row in requests[0]["messages"])
    assert prompt.count("<verified_translation_cases>") == 1
    assert prompt.count("請先秤重，再確認標籤。") == 1
    assert prompt.count("<line_reply_context>") == 1
    assert "那捆棒材還沒核對標籤。" in prompt
    assert source in requests[0]["messages"][-1]["content"]
    if icl:
        assert "Troli sudah penuh." in prompt


def test_reference_context_does_not_force_review_or_discard_verified_memory(isolated_provider_context, monkeypatch):
    app = isolated_provider_context
    row = correction("包裝前檢查材料。", "Periksa material sebelum dikemas.")
    monkeypatch.setattr(app, "_active_translation_corrections_for_casebook", lambda *_a, **_k: [row])
    contract = app.build_translation_semantic_contract("打包之前先確認棒材。", "zh", "id")
    assert contract["reference_cases"]
    assert not any(risk.get("sense") == "verified_correction_cases" for risk in contract["risks"])
    assert not contract.get("requires_independent_review")
    prompt = app.build_translation_semantic_contract_prompt(contract)
    assert "<translation_reference_context>" in prompt
