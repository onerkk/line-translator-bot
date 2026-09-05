"""Behavioral regressions for translation correctness and paid-work reuse.

Provider and LINE calls are replaced locally; these tests spend no API credits.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import threading
import time

import pytest

import active_learning as learning
import ai_provider
import app
import factory_translation_guard as factory_guard
import prompt_optimizer
import translation_casebook as casebook
import translation_quality_gate as quality
import translation_request_guard as request_guard
import translation_retry_queue as queue
from translation_source_identity import canonical_source_key

_REGRESSION_CASES = json.loads(
    Path(__file__).with_name("factory_translation_regression.json").read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("case", _REGRESSION_CASES, ids=lambda case: case["id"])
def test_approved_factory_corpus_delivers_without_paid_api(case, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("approved exact case unexpectedly reached a paid/fallback provider")
    monkeypatch.setattr(app, "translate_openai", forbidden)
    monkeypatch.setattr(app, "_emergency_translation_fallback", forbidden)
    monkeypatch.setattr(app.ai_provider, "chat_complete", forbidden)
    monkeypatch.setattr(app._tl, "group_id", "corpus-offline", raising=False)
    monkeypatch.setattr(app._tl, "disable_tone_emoji", True, raising=False)
    monkeypatch.setattr(app._tl, "quoted_context_source", "", raising=False)
    monkeypatch.setattr(app, "get_recent_media_scene", lambda *_a, **_k: "")
    source_language, target_language = case["direction"].split("-", 1)
    result = app.translate(case["source"], source_language, target_language)
    assert result
    assert app._delivery_validation_issues(case["source"], result, source_language, target_language) == []


@pytest.mark.parametrize("left,right", [
    ("公差 < 0.04 mm", "公差 > 0.04 mm"),
    ("公差 ≥ 0.04 mm", "公差 > 0.04 mm"),
    ("Batas 0,04 mm", "Batas 004 mm"),
    ("I9: 40 kg", "I9: 140 kg"),
    ("20°C", "20C"),
    ("I9 = 2", "I9 ≠ 2"),
    ("物料 A→B", "物料 B→A"),
    ("di a", "dia"),
    ("I9 可以開機？", "I9 可以開機。"),
    ("檢驗 ✅", "檢驗 ❌"),
])
def test_different_source_facts_cannot_share_an_exact_key(left, right):
    for make_key in (canonical_source_key, casebook.canonical_source_key,
                     factory_guard.canonical_source_key, learning.canonical_source_key):
        assert make_key(left) != make_key(right)
    assert casebook.exact_verified_target(right, [{"source": left, "target": "approved"}]) is None


def test_presentation_variants_still_share_verified_corrections():
    assert canonical_source_key("本月木箱，暫不裝箱。") == canonical_source_key("本月木箱 暫不裝箱")
    assert canonical_source_key("Mesin I9 rusak.") == canonical_source_key("mesin  I9 rusak")


@pytest.mark.parametrize("wrong", ["小於 0.04 mm。", "至少 0.04 mm。", "不超過 0.04 mm。", "大於 0.4 mm。"])
def test_numeric_threshold_direction_is_an_integrity_requirement(wrong):
    report = quality.validate_translation("Diameter > 0,04 mm.", wrong, "id", "zh")
    assert not report.ok
    assert any(x.startswith("numeric_comparison_changed:") for x in report.hard_issues)


def test_same_threshold_can_use_localized_decimal_and_words():
    assert quality._comparison_integrity_issues("Diameter > 0,04 mm.", "直徑大於 0.04 mm。") == []
    assert quality._comparison_integrity_issues("Diameter <= 0,04 mm.", "直徑 ≤ 0.04 mm。") == []


def test_missing_data_cannot_be_repaired_by_appending_an_unattached_note():
    source = "Periksa nomor 7H341005 dan 7H341006."
    result = quality.ensure_delivery_safe_translation(
        source, "請檢查編號 7H341005。", "id", "zh", model="test"
    )
    assert not result["ok"]
    assert result["text"] is None
    assert not result["cacheable"]


def test_broken_semantic_validator_cannot_approve_a_translation():
    def broken(_text):
        raise RuntimeError("validator unavailable")
    result = quality.gate_and_revise(
        "Besok makan mi.", "明天吃麵。", "id", "zh", critical=False,
        model="test", ai_client=None, semantic_validator=broken,
    )
    assert not result["ok"]
    assert not result["cacheable"]


def test_final_guard_does_not_resurrect_rejected_output(monkeypatch):
    monkeypatch.setattr(app, "_best_effort_factory_delivery", lambda *_a, **_k: None)
    monkeypatch.setattr(app.tqg_module, "ensure_delivery_safe_translation", lambda *_a, **_k: {
        "ok": False, "text": None, "issues": ["semantic_validation_failed"]
    })
    assert app._final_delivery_guard("Besok makan mi.", "明天吃麵。", "id", "zh") is None


def test_disabled_factory_cards_are_not_exact_or_prompt_evidence(monkeypatch):
    document = {"entries": [{"id": "retired", "enabled": False,
        "directions": ["zh-id"], "examples": [{"source": "舊資料", "target": "data lama"}]}]}
    assert list(factory_guard.FactoryTranslationGuard._knowledge_exact_cases(document)) == []
    monkeypatch.setattr(app, "_FACTORY_KNOWLEDGE_STORE", SimpleNamespace(document=lambda: document))
    assert app._factory_knowledge_examples_for_casebook() == []


def test_prompt_budget_preserves_runtime_contract_and_nested_tail():
    original = (
        "<role>x</role><semantic_contract>outer<semantic_contract>inner</semantic_contract>"
        "KEEP_THE_TAIL</semantic_contract>"
        "<implicit_quantity_units>147 => ton</implicit_quantity_units>"
        "<factory_acceptance_boundary>APPROVED_TERMS</factory_acceptance_boundary>"
        "<source_bound_context>PHOTO_FACT</source_bound_context>"
        "<factory_vocabulary>工單=work order</factory_vocabulary>"
    )
    compiled, _ = prompt_optimizer.compile_translation_prompt(original, "工單", "zh", "id", max_chars=100)
    assert all(value in compiled for value in ("KEEP_THE_TAIL", "147 => ton", "APPROVED_TERMS", "PHOTO_FACT"))


def test_casebook_examples_are_sent_once_not_repeated_as_fewshot(monkeypatch):
    monkeypatch.setattr(app, "_retrieve_verified_translation_cases", lambda *_a, **_k: pytest.fail("duplicate retrieval"))
    monkeypatch.setattr(app._tl, "quoted_context_source", "", raising=False)
    prompt = "<semantic_contract><verified_translation_cases>approved example</verified_translation_cases></semantic_contract>"
    messages = app._build_messages_with_fewshot(prompt, "Translate: 先處理工單", "zh", "id", source_text="先處理工單")
    assert [row["role"] for row in messages] == ["system", "user"]


def test_factory_background_storage_has_no_unused_embedding_call(monkeypatch):
    writes = []
    monkeypatch.setenv("FACTORY_TRANSLATION_MODE", "always")
    monkeypatch.setattr(app.tm_module, "tm_store", lambda *_a, **_k: writes.append(True))
    monkeypatch.setattr(app.vec_tm_module, "vector_store", lambda *_a, **_k: pytest.fail("unused paid embedding"))
    app._post_translation_async("Besok makan mi.", "明天吃麵。", "id", "zh", "group", "test", 1.0, {})
    assert writes == [True]


def test_concurrent_identical_requests_use_one_translation(monkeypatch):
    calls = []
    monkeypatch.setenv("FACTORY_TRANSLATION_MODE", "always")
    monkeypatch.setattr(app, "translation_cache", {})
    monkeypatch.setattr(app, "_BG_POST_EXECUTOR", SimpleNamespace(submit=lambda *_a, **_k: None))
    monkeypatch.setattr(app, "get_recent_media_scene", lambda *_a, **_k: "")
    monkeypatch.setattr(app.tm_module, "tm_lookup_verified_exact", lambda *_a, **_k: None)
    def provider(*_args, **_kwargs):
        calls.append(True)
        time.sleep(0.03)
        return "明天午餐吃麵。"
    monkeypatch.setattr(app, "_translate_inner", provider)
    start = threading.Barrier(4)
    def run():
        app._tl.group_id = "concurrent-test"
        app._tl.disable_tone_emoji = True
        start.wait(timeout=5)
        return app.translate("Besok makan mi untuk makan siang.", "id", "zh")
    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(pool.map(lambda _: run(), range(4)))
    assert outputs == ["明天午餐吃麵。"] * 4
    assert calls == [True]
    assert request_guard._requests == {}


def test_serialization_releases_registry_after_exception():
    with pytest.raises(RuntimeError):
        with request_guard.serialize_request("failed"):
            raise RuntimeError("provider failed")
    with request_guard.serialize_request("failed"):
        pass
    assert request_guard._requests == {}


def test_existing_correction_keys_migrate_without_losing_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVE_LEARNING_DB_PATH", str(tmp_path / "corrections.db"))
    monkeypatch.setattr(learning, "_init_done", False)
    monkeypatch.setattr(learning, "AL_DB_PATH", None)
    learning.init()
    with sqlite3.connect(learning.AL_DB_PATH) as conn:
        conn.execute("""INSERT INTO corrections
            (src_lang,tgt_lang,src_text,src_text_hash,canonical_src_key,
             original_translation,corrected_translation,revision,created_at,updated_at)
            VALUES ('id','zh','batas < 0,04 mm','hash','batas004mm','舊譯文','修正版',7,1,1)""")
    learning._init_done = False
    learning.init()
    with sqlite3.connect(learning.AL_DB_PATH) as conn:
        key, revision = conn.execute("SELECT canonical_src_key,revision FROM corrections").fetchone()
    assert key == canonical_source_key("batas < 0,04 mm")
    assert revision == 7


def test_ocr_checkpoint_preserves_retry_lease_and_survives_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "queue.db"))
    queue.initialize()
    queue.enqueue("image", {"job_kind": "image"}, job_kind="image")
    assert queue.checkpoint("image", {"ocr_text": "I9 機台"})
    job = queue.claim_due_jobs(owner="worker", now=time.time(), lease_seconds=60)[0]
    assert job["payload"]["ocr_text"] == "I9 機台"
    assert not queue.checkpoint("image", {"ocr_text": "wrong"}, owner="other")
    assert not queue.checkpoint("image", {"ocr_text": "wrong"})
    assert queue.checkpoint("image", {"ocr_text": "I9 機台故障"}, owner="worker")
    refreshed = queue.get("image")
    assert refreshed["lease_owner"] == "worker"
    assert refreshed["attempts"] == job["attempts"]
    assert refreshed["payload"]["ocr_text"] == "I9 機台故障"


def test_image_retry_reuses_completed_ocr(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "queue.db"))
    monkeypatch.setattr(app, "download_line_image", lambda *_a: pytest.fail("duplicate download"))
    monkeypatch.setattr(app, "ocr_image_openai", lambda *_a, **_k: pytest.fail("duplicate OCR"))
    monkeypatch.setattr(app, "translate", lambda *_a, **_k: "明天吃麵。")
    monkeypatch.setattr(app, "_translation_retry_push", lambda *_a, **_k: None)
    assert app._translation_retry_image_attempt({"job_key": "cached-image", "payload": {
        "message_id": "image", "group_id": "group", "wo_setting": False,
        "ocr_text": "Besok makan mi.", "tgt": "id",
    }})


def test_bad_candidates_cannot_cascade_through_three_paid_generations(monkeypatch):
    attempts = []
    monkeypatch.setattr(ai_provider, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_provider, "_current_config", {"provider_failover": True})
    monkeypatch.setattr(ai_provider, "get_available_providers", lambda *_a, **_k: ["openai", "anthropic", "gemini"])
    monkeypatch.setattr(ai_provider, "_record_provider_success", lambda *_a, **_k: None)
    def dispatch(provider, **kwargs):
        assert "translation_max_generations" not in kwargs
        attempts.append((provider, kwargs))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="bad candidate"))])
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    result = ai_provider.chat_complete(
        "test", [{"role": "system", "content": "Translate accurately."}, {"role": "user", "content": "I9 stop"}],
        translation_max_generations=2,
        response_validator=lambda *_a: (False, "missing_literal:I9"),
    )
    assert [item[0] for item in attempts] == ["openai", "anthropic"]
    assert "missing_literal:I9" in attempts[1][1]["messages"][1]["content"]
    assert result._jy_quality_degraded is True
