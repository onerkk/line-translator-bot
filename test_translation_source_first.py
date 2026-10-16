"""Source-first composition, real pipeline admission and approved memory.

Model/OCR outputs are fixed fixtures. These tests establish wiring and local
invariants, NOT accuracy or latency of an uncalled external model.
"""
import ast
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import app
import active_learning as learning
import glossary_policy as policy
import glossary_enforcement as enforcement
import factory_terminology as terms
import prompt_optimizer as prompts
import translation_casebook as casebook

SOURCE = "巨昌有備註端面不貼華新標籤，這是指爐號標籤的部分，後續有包到再只注意一下"
TARGET = ("巨昌 memberi catatan agar label Huaxin tidak ditempel pada permukaan ujung. "
          "Yang dimaksud adalah label nomor heat. Saat nanti mengemas material untuk 巨昌, mohon perhatikan ketentuan ini.")


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(app, "_tl", threading.local())
    monkeypatch.setattr(app, "translation_cache", {})
    monkeypatch.setattr(app, "_event_log_write", lambda *a, **k: None)
    monkeypatch.setattr(app, "_post_translation_async", lambda *a, **k: None)
    app._tl.disable_tone_emoji = True
    app._tl.group_id = "G-source-first"
    return app


def fake_response(target):
    return SimpleNamespace(model="offline-fixture", choices=[SimpleNamespace(
        message=SimpleNamespace(content=target), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))


@pytest.mark.parametrize("source,target", [
    ("請檢查爐號標籤", "Periksa label nomor heat."),
    ("請檢查客戶標籤", "Periksa label pelanggan."),
    ("請檢查警告標籤", "Periksa label peringatan."),
    ("請檢查產品標籤", "Periksa label produk."),
    ("請檢查標籤", "Periksa label."),
    ("請檢查重量標籤", "Periksa label berat."),
    ("請檢查包裝標籤", "Periksa label kemasan."),
])
def test_generic_noun_does_not_force_a_product_subtype(source, target):
    # Even a persisted legacy glossary is migrated at every consumption boundary.
    glossary = {"標籤": {"idn": "label produk", "translation_mode": "hard"}}
    result = enforcement.enforce_glossary(source, target, glossary, "zh", "id")
    assert result["compliant"] and result["final_text"] == target
    assert terms.collect_applicable_pairs(source, glossary, "zh", "id") == [("標籤", "label")]


def test_runtime_editable_and_embedded_glossary_have_the_same_neutral_head():
    root = Path(__file__).parent
    glossary = json.loads((root / "glossary_data.json").read_text())
    tree = ast.parse((root / "app.py").read_text())
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "_GLOSSARY_JSON" for t in n.targets))
    fallback = json.loads(ast.literal_eval(assignment.value))
    for doc in (glossary, fallback, app.GLOSSARY_LOOKUP):
        assert policy.canonical_target(doc["標籤"]) == "label"
    reverse = enforcement.build_safe_reverse_index(glossary)
    assert reverse["label"]["target_term"] == "標籤"


@pytest.mark.parametrize("ocr", [False, True])
def test_good_first_response_survives_text_and_image_entry_without_product_insertion(runtime, monkeypatch, ocr):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        response = fake_response(TARGET)
        ok, reason = kwargs["response_validator"](response, "fixture")
        assert ok, reason
        return response
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    if ocr:
        monkeypatch.setattr(app, "ocr_image_openai", lambda *a, **k: SOURCE)
        result = app.ocr_and_translate_image("offline-image", "id")
        assert "label nomor heat" in str(result)
        assert "label produk" not in str(result)
    else:
        result = app.translate(SOURCE, "zh", "id")
        assert result == TARGET
        assert app.translate(SOURCE, "zh", "id") == TARGET
    assert len(calls) == 1
    assert calls[0]["translation_max_generations"] == 1
    system = calls[0]["messages"][0]["content"]
    assert "nomor heat" in system and "permukaan ujung" in system
    assert "label produk" not in system and "nomor furnace" not in system
    assert "relevant_failure_rules" not in system
    assert "relevant_context_rules" not in system
    assert "later clarifications" in system


def test_actual_request_does_not_call_historical_prompt_parser(runtime, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("production must not scrape the old prompt")
    monkeypatch.setattr(prompts, "compile_translation_prompt", forbidden)
    monkeypatch.setattr(prompts, "_select_context_rules", forbidden)
    monkeypatch.setattr(prompts, "_select_vocab", forbidden)
    calls = []
    monkeypatch.setattr(app.ai.chat.completions, "create", lambda **kw: calls.append(kw) or fake_response(TARGET))
    assert app.translate_openai(SOURCE, "zh", "id") == TARGET
    assert len(calls) == 1


@pytest.mark.parametrize("source,needs", [
    ("今天辛苦了", False), ("Terima kasih", False),
    ("標籤不貼端面，這裡指客戶標籤", True),
    ("不要在所有標籤上寫，僅修改爐號標籤", True),
    ("Tidak perlu mengganti label", True),
    ("Hanya label pelanggan yang diganti", True),
    ("先確認資料再入庫", True),
])
def test_reasoning_budget_follows_relationships_and_polarity(source, needs):
    assert prompts.needs_semantic_reasoning(source) == needs


def test_simple_request_uses_fast_path_but_original_context_keeps_reasoning(runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(app.ai.chat.completions, "create", lambda **kw: calls.append(kw) or fake_response("Terima kasih atas kerja keras hari ini."))
    app.translate_openai("今天辛苦了", "zh", "id")
    assert calls[0]["translation_fast_quality"]
    assert calls[0].get("reasoning_effort") in (None, "none")
    app._tl.quoted_context_source = "下一班還沒接手"
    app.translate_openai("今天辛苦了", "zh", "id")
    assert not calls[1]["translation_fast_quality"]
    assert len(calls) == 2  # One generation for each independent request.


def test_direct_compiler_preserves_required_source_facts_even_with_tiny_legacy_budget(monkeypatch):
    monkeypatch.setenv("PROMPT_MAX_CHARS", "1")
    source_facts = "<semantic_contract>不得改成已完成，A1 的值是 0.25 kg。</semantic_contract>"
    prompt, _ = prompts.compile_request_prompt("A1", "zh", "id", semantic_prompt=source_facts,
        source_constraints=(source_facts, "<other_constraint>B2 != A1</other_constraint>"))
    assert prompt.count(source_facts) == 1
    assert "B2 != A1" in prompt
    prefix, suffix = prompts.split_cache_prefix(prompt)
    assert "A1" not in prefix
    assert source_facts in suffix


def test_unknown_human_correction_is_retrieved_without_new_error_rules(runtime, monkeypatch, tmp_path):
    import translation_memory as tm
    monkeypatch.setattr(learning, "AL_DB_PATH", str(tmp_path / "al.db"))
    monkeypatch.setattr(learning, "_init_done", False)
    monkeypatch.setattr(tm, "TM_DB_PATH", str(tmp_path / "tm.db"))
    monkeypatch.setattr(tm, "_init_done", False)
    monkeypatch.setenv("ACTIVE_LEARNING_VECTOR_SYNC", "0")
    casebook.invalidate_active_cache()
    source = "客戶的紫色貼紙先放辦公桌，黃色貼紙留在櫃子。"
    target = "Taruh stiker ungu milik pelanggan di meja kantor dahulu, dan biarkan stiker kuning di lemari."
    pending = learning.submit_correction(src_text=source, original_tgt="Stiker disimpan.", corrected_tgt=target,
        src_lang="zh", tgt_lang="id", corrected_by="operator", group_id="G-source-first")
    assert pending["ok"]
    assert not casebook.active_corrections_snapshot(learning, group_id="G-source-first")
    approval = learning.approve_correction(pending["correction_id"], approved_by="admin")
    assert approval["ok"], approval
    rows = casebook.active_corrections_snapshot(learning, group_id="G-source-first")
    assert len(rows) == 1
    query = "黃色貼紙留在櫃子，客戶的紫色貼紙改放辦公桌旁。"
    selected = casebook.retrieve(query, "zh", "id", corrections=rows)
    assert selected
    memory_prompt = casebook.build_prompt(selected)
    assert target in memory_prompt and source in memory_prompt
    assert casebook.exact_verified_target(query, selected) is None
    assert not casebook.active_corrections_snapshot(learning, group_id="G-other")
    revoked = learning.reject_correction(pending["correction_id"], rejected_by="admin", reason="wrong interpretation")
    assert revoked["ok"]
    assert not casebook.active_corrections_snapshot(learning, group_id="G-source-first")
    casebook.invalidate_active_cache()


def test_reference_budget_cannot_drop_a_late_negation_or_scope_clarification():
    source = "請把這份資料保留。" * 80 + "但絕對不要寄出。"
    target = "Simpan data ini. " * 100 + "Namun jangan pernah mengirimkannya."
    row = {"source": source, "target": target, "case_id": "long-complete"}
    assert not casebook.build_prompt([row], max_chars=600)
    prompt = casebook.build_prompt([row], max_chars=8000)
    assert source in prompt and target in prompt
    assert "reference excerpt" not in prompt
