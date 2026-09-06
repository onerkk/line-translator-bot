"""Source-grounded regressions: real delivery, prompt transport and stale memory.

All provider calls are replaced locally. These tests spend no API credits and
do not claim to measure model translation accuracy or network latency.
"""
from types import SimpleNamespace
import json
import time
from pathlib import Path

import pytest

import app
import factory_knowledge
import factory_source_understanding as source_terms
import factory_translation_guard as guard
import glossary_policy
import prompt_optimizer
import translation_extras
import translation_quality_gate as quality


SCREENSHOTS = [
    ("id", "zh", "ID ini tidak bisa di OL di I6", "此 ID 的資料無法在 I6 發料（OL）。"),
    ("id", "zh", "Sip pagi menjalan kan barang dengan kondisi laser kotor", "早班在雷射測徑儀髒污的情況下生產。"),
    ("id", "zh", "ID ini ada barang rusak 1 pcs saya buang sehingga kg berkurang saya menggunakan no pekerja Ketua kelas",
     "這筆 ID 有料件損壞，我丟棄了 1 支，因此重量減少。我使用的是班長的工號。"),
    ("zh", "id", "我再跟早班反應，你先擦拭後生產",
     "Saya akan menyampaikannya lagi kepada shift pagi. Kamu lap dulu, lalu mulai produksi."),
    ("zh", "id", "這個系統的網頁待機太久很常跳連不上網路，重新整理一下就好了",
     "Kalau halaman web sistem ini terlalu lama dibiarkan, sering muncul pesan tidak terhubung ke jaringan. Cukup muat ulang halaman, nanti normal kembali."),
]


@pytest.fixture
def isolated_context(monkeypatch):
    monkeypatch.setattr(app._tl, "group_id", "term-integrity-offline", raising=False)
    monkeypatch.setattr(app._tl, "user_id", "offline", raising=False)
    monkeypatch.setattr(app._tl, "quoted_context_source", "", raising=False)
    monkeypatch.setattr(app._tl, "disable_tone_emoji", True, raising=False)
    monkeypatch.setattr(app, "get_recent_media_scene", lambda *_a, **_k: "")
    monkeypatch.setattr(app, "_current_work_order_media_context", lambda: False)
    monkeypatch.setattr(app, "_translation_cache_context_bound", lambda *_a, **_k: False)


@pytest.mark.parametrize("src,tgt,source,target", SCREENSHOTS)
def test_screenshot_meanings_survive_shared_delivery_gate(src, tgt, source, target, isolated_context):
    assert quality.validate_translation(source, target, src, tgt).ok
    assert guard.validate_translation(source, target, src, tgt).ok
    contract = app.build_translation_semantic_contract(source, src, tgt)
    assert app.translation_satisfies_semantic_contract(contract, target)[0]
    assert app._final_delivery_guard(source, target, src, tgt)


@pytest.mark.parametrize("source,bad,issue", [
    ("ID ini tidak bisa di OL di I6", "此 ID 無法在 I6 進行線上操作。", "erp_ol"),
    ("ID ini tidak bisa di OL di I6", "此 ID 已在 I6 發料（OL）。", "state_changed"),
    ("Data I9 belum di-OL", "I9 資料已發料（OL）。", "state_changed"),
    ("Data I9 jangan di OL", "I9 資料可以發料（OL）。", "state_changed"),
    ("Laser mesin I6 kotor", "I6 雷射光故障了。", "laser_gauge"),
    ("Saya menggunakan no pekerja ketua kelas", "我使用課長的工人人數。", "employee_number"),
])
def test_wrong_senses_and_polarity_are_hard_failures(source, bad, issue, isolated_context):
    report = quality.validate_translation(source, bad, "id", "zh")
    assert not report.ok
    assert any(issue in x for x in report.hard_issues)
    assert not guard.validate_translation(source, bad, "id", "zh").ok
    assert not app._tm_bypass_integrity_ok(source, bad, "id", "zh")[0]


@pytest.mark.parametrize("source", [
    "Laser pointer saya kotor", "Operasi mata menggunakan laser", "Laser cutting mesin I6 kotor",
    "Saya ketua kelas di sekolah", "Lihat https://example.invalid/data/diOL/I6",
    "ID game saya tidak bisa OL di Instagram",
])
def test_unrelated_laser_school_and_url_do_not_receive_factory_senses(source):
    assert source_terms.factory_term_facts(source, "id") == []


@pytest.mark.parametrize("source,required", [
    ("ID ini tidak bisa di OL di I6", ("無法", "I6", "OL")),
    ("Data I9 blm di-OL", ("尚未", "I9", "OL")),
    ("ID 7H341006 sudah diOL di I9", ("已", "7H341006", "I9", "OL")),
    ("Data ini jangan di OL di E6", ("勿", "E6", "OL")),
])
def test_complete_status_delivery_never_spends_a_model_call(source, required, isolated_context, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("complete source-verifiable status unexpectedly used a provider")
    monkeypatch.setattr(app, "translate_openai", forbidden)
    monkeypatch.setattr(app, "_emergency_translation_fallback", forbidden)
    monkeypatch.setattr(app.ai_provider, "chat_complete", forbidden)
    result = app.translate(source, "id", "zh")
    assert result and all(value in result for value in required)


@pytest.mark.parametrize("source", [
    "ID ini tidak bisa di OL di I6 karena jumlahnya salah",
    "ID ini tidak bisa di OL di I6, tolong cek 5 batang",
    "ID ini sudah di OL di I6 dan data I9 belum di OL",
    "ID ini tidak bisa di OL di I6?",
    "ID ini\ntidak bisa di OL di I6",
])
def test_free_prose_and_questions_are_not_partially_translated_locally(source):
    assert source_terms.translate_complete_erp_status(source, "id", "zh") is None


@pytest.mark.parametrize("src,tgt,source,target", SCREENSHOTS)
def test_actual_provider_request_keeps_matched_terminology(src, tgt, source, target, isolated_context, monkeypatch):
    requests = []
    def fake(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=target), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2), model="offline", _provider="openai")
    monkeypatch.setattr(app.ai.chat.completions, "create", fake)
    monkeypatch.setattr(app._tl, "semantic_contract", app.build_translation_semantic_contract(source, src, tgt), raising=False)
    assert app.translate_openai(source, src, tgt)
    assert len(requests) == 1
    prompt = "\n".join(str(row["content"]) for row in requests[0]["messages"])
    if "OL" in source:
        assert "ERP 生產資料發料" in prompt
        assert "OL=sedang produksi/online" not in prompt
    if "laser" in source:
        assert "雷射測徑儀" in prompt
    if "no pekerja" in source:
        assert "工號" in prompt and "班長" in prompt


def test_prompt_budget_cannot_drop_matched_soft_terms():
    raw = "<role>Translator</role><source_terminology><factory_terminology>[SOFT] 發料 ~= mengubah status data menjadi OL</factory_terminology></source_terminology>"
    compiled, _ = prompt_optimizer.compile_translation_prompt(raw, "請發料", "zh", "id", max_chars=100)
    assert "mengubah status data menjadi OL" in compiled
    assert compiled.count("[SOFT]") == 1


def test_unrelated_example_words_do_not_select_a_historical_rule():
    raw = "<context_disambiguation>10. CRITICAL CONTEXT RULES: k) 放=POLYSEMY. 幫放一下=tolong bantu release. s) APOLOGY: Maf kan saya=抱歉.</context_disambiguation>"
    for text, src, tgt in ((SCREENSHOTS[1][2], "id", "zh"), (SCREENSHOTS[4][2], "zh", "id")):
        compiled, _ = prompt_optimizer.compile_translation_prompt(raw, text, src, tgt)
        assert "<relevant_context_rules>" not in compiled


def test_bad_ol_cache_is_evicted_even_with_current_asset_fingerprint(isolated_context, monkeypatch):
    source = SCREENSHOTS[0][2]
    key = (source, "id", "zh", app._translation_cache_scope())
    rows = {key: ("此 ID 無法在 I6 進行線上操作。", time.time(), app._translation_cache_asset_fingerprint())}
    monkeypatch.setattr(app, "translation_cache", rows)
    assert app.cache_get(source, "id", "zh") is None
    assert key not in rows


@pytest.mark.parametrize("source,good,bad", [
    ("Data I6 sudah di OL dan data I9 belum di OL", "I6 資料已發料（OL），I9 資料尚未發料（OL）。", "I6 資料尚未發料（OL），I9 資料已發料（OL）。"),
    ("Data I6 sudah di OL tetapi tidak bisa produksi", "I6 資料已發料（OL），但是無法生產。", "I6 資料尚未發料（OL），但是無法生產。"),
    ("請勿在 I6 發料 OL", "Jangan di OL di I6.", "Data sudah di OL di I6."),
])
def test_erp_state_stays_with_its_operation_and_station(source, good, bad):
    src, tgt = ("zh", "id") if source.startswith("請") else ("id", "zh")
    frame = source_terms.analyze(source, src)
    assert source_terms.validate_factory_terms(frame, good, src, tgt)[0]
    assert not source_terms.validate_factory_terms(frame, bad, src, tgt)[0]


def test_shift_report_does_not_invent_calendar_emoji():
    report = translation_extras.build_expression_plan(SCREENSHOTS[1][2], SCREENSHOTS[1][3], source_language="id")
    assert not any(icon in report.text for icon in ("📅", "⏰", "🕒"))
    schedule = translation_extras.analyze_message_tone("明天早班 08:00 上班", "zh")
    assert schedule.primary == "work_schedule"


def test_spelling_normalization_preserves_shift_and_identifiers(isolated_context):
    result, _ = app.normalize_indonesian_text("Sip pagi menjalan kan barang dengan kondisi laser I6 kotor")
    assert result.lower().startswith("shift pagi menjalankan") and "I6" in result
    assert source_terms.normalize_known_variants("menjalan kan I6", "id", protected_names=("menjalan kan",))[0] == "menjalan kan I6"


def test_compact_knowledge_index_reloads_and_does_not_leak_mutations(tmp_path):
    document = {"schema_version": 1, "entries": [{"id": "erp-test", "directions": ["id-zh"],
        "match": {"any_terms": ["data"]}, "examples": [{"source": "Data sudah di OL", "target": "資料已發料（OL）。"}]}]}
    path = tmp_path / "knowledge.json"
    path.write_text(json.dumps(document))
    store = factory_knowledge.FactoryKnowledgeStore(str(path))
    first = store.casebook_examples()
    first[0]["zh"] = "污染"
    assert store.casebook_examples()[0]["zh"] == "資料已發料（OL）。"
    document["entries"][0]["examples"][0]["target"] = "資料已轉為 OL 狀態。"
    path.write_text(json.dumps(document))
    store.reload(force=True)
    assert store.casebook_examples()[0]["zh"] == "資料已轉為 OL 狀態。"


def test_audited_glossary_uses_correct_directions_and_quarantines_contradictions():
    data = json.loads(Path(__file__).with_name("glossary_data.json").read_text())
    assert "turun/naik" in data["E6冷抽機─張力輥向下/向上"]["canonical_idn"]
    assert "pengiriman" in data["工單訂單資訊「生計交期」"]["canonical_idn"]
    assert data["E824拋光設備區 旁按鈕「停用煞車」"]["canonical_idn"] == "nonaktifkan rem"
    for row in data.values():
        if row.get("review_note", "").startswith(("標題",)):
            assert glossary_policy.translation_mode(row) == "disabled"
