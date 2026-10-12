"""Operator coverage at shift handover, including the reviewed meeting notice.

Offline provider fixtures check the actual text/OCR and cache boundaries. They
are not live model evaluations or assertions about every handover phrase.
"""
import json
import re
import threading
from types import SimpleNamespace

import pytest
import factory_chat_operation_semantics as operations
import factory_translation_guard as guard
import translation_alignment as alignment
import translation_learning_policy as policy
import translation_quality_gate as quality

SOURCE = "沒人交接的設備關閉風扇。"
BAD = "Matikan kipas pada peralatan yang tidak ada serah terima."
FIXED = "Matikan kipas pada peralatan yang tidak diambil alih oleh petugas pengganti."
NOTICE_SOURCE = (
    "@All 今天早班開會，下班前注意以下。"
    "沒人交接的設備關閉風扇，沒有生產的設備膠輪關閉不要空轉。"
    "注意自己作業區內有沒有工單掛牌掉落，最近牌不離捆被提報執行狀況不佳。")
NOTICE_SEGMENTS = [
    "@All Hari ini ada rapat shift pagi. Sebelum pulang, perhatikan hal-hal berikut.",
    BAD + " Untuk peralatan yang tidak sedang berproduksi, matikan roda karetnya dan jangan biarkan berputar tanpa beban.",
    "Periksa apakah ada TAG work order yang jatuh di area kerja masing-masing. "
    "Akhir-akhir ini pelaksanaan ketentuan TAG tidak boleh terpisah dari bundel dilaporkan kurang baik.",
]


@pytest.mark.parametrize("source", [SOURCE, "沒有人接班的機台要關閉風扇。",
    "設備尚未有人接手時，先關掉風扇。", "无人接班的设备关闭风扇。"])
def test_no_person_takeover_generalizes_across_source_wording(source):
    rows = operations.relations(source, "zh", "id")
    assert any(r["operation"] == "operator_takeover_condition" for r in rows)
    assert operations.validate(rows, BAD, "zh", "id")
    assert not operations.validate(rows, FIXED, "zh", "id")


@pytest.mark.parametrize("target", [
    FIXED,
    "Matikan kipas pada peralatan yang belum ada petugas yang mengambil alih.",
    "Jika belum ada operator pengganti, matikan kipas peralatan tersebut.",
    "Matikan kipas pada peralatan yang belum diambil alih oleh operator shift berikutnya.",
    "Jika operator shift berikutnya belum mengambil alih mesin, matikan kipasnya.",
    "Kipas peralatan tanpa operator pengganti harus dimatikan.",
    "Jika tidak ada yang mengambil alih peralatan, matikan kipasnya.",
    "Jika belum ada petugas untuk menerima serah terima, matikan kipasnya.",
])
def test_natural_actor_active_passive_and_conditional_variants_pass(target):
    assert quality.validate_translation(SOURCE, target, "zh", "id").ok
    assert guard.validate_translation(SOURCE, target, "zh", "id").ok
    assert operations.canonicalize(SOURCE, target, "zh", "id") == target


@pytest.mark.parametrize("target", [
    BAD,
    "Matikan kipas pada peralatan yang tidak ada operator.",
    "Matikan kipas jika operator shift berikutnya sudah mengambil alih mesin.",
    "Jangan matikan kipas pada peralatan yang belum diambil alih oleh operator pengganti.",
    "Hidupkan kipas jika belum ada operator pengganti.",
    "Matikan roda karet dan jangan matikan kipas jika belum ada operator pengganti.",
    "Kipas tetap menyala; matikan roda karet jika belum ada operator pengganti.",
    "Matikan kipas. Belum ada operator pengganti untuk mesin yang lain.",
    "Matikan kipas karena tidak ada serah terima. Mesin yang lain belum diambil alih operator pengganti.",
    "Kipas pada peralatan yang belum diambil alih oleh operator pengganti sudah dimatikan.",
    "Kipas pada peralatan yang belum diambil alih oleh operator pengganti tidak perlu dimatikan.",
])
def test_missing_actor_wrong_condition_and_other_actions_cannot_supply_fan_rule(target):
    assert not quality.validate_translation(SOURCE, target, "zh", "id").ok
    assert not guard.validate_translation(SOURCE, target, "zh", "id").ok


def test_repair_changes_only_the_bare_handover_predicate_and_never_adds_meeting():
    assert quality.canonicalize_source_terms(SOURCE, BAD, "zh", "id") == FIXED
    assert "rapat" not in FIXED
    original = " ".join(NOTICE_SEGMENTS)
    repaired = quality.canonicalize_source_terms(NOTICE_SOURCE, original, "zh", "id")
    assert repaired == original.replace(BAD, FIXED)
    assert quality.validate_translation(NOTICE_SOURCE, repaired, "zh", "id").ok
    assert guard.validate_translation(NOTICE_SOURCE, repaired, "zh", "id").ok


@pytest.mark.parametrize("source,target", [
    ("未交接的設備關閉風扇。", BAD),
    ("交接紀錄沒有填寫，請關閉風扇。", "Catatan serah terima belum diisi. Matikan kipas."),
    ("不是沒人交接，請關閉風扇。", "Bukan berarti tidak ada operator pengganti. Matikan kipas."),
    ("沒人交接的設備不要關閉風扇。", "Jangan matikan kipas pada peralatan yang tidak ada serah terima."),
    ("沒人交接的設備要關閉風扇嗎？", "Apakah kipas pada peralatan yang tidak ada serah terima harus dimatikan?"),
    ("沒人交接的設備不用關閉風扇。", "Kipas pada peralatan yang tidak ada serah terima tidak perlu dimatikan."),
    ("沒人交接的設備不關閉風扇。", "Kipas pada peralatan yang tidak ada serah terima tidak dimatikan."),
    ("沒人交接的設備風扇已關閉。", "Kipas pada peralatan yang tidak ada serah terima sudah dimatikan."),
    ("沒人交接的設備關了風扇。", "Kipas pada peralatan yang tidak ada serah terima sudah dimatikan."),
])
def test_procedure_record_question_and_prohibition_are_not_blindly_rewritten(source, target):
    assert not operations.relations(source, "zh", "id")
    assert operations.canonicalize(source, target, "zh", "id") == target


@pytest.mark.parametrize("target", [
    "Matikan kipas pada peralatan yang tidak ada serah terima data ERP.",
    "Matikan kipas pada peralatan yang tidak ada laporan serah terima.",
    "Matikan kipas pada mesin A yang tidak ada serah terima; matikan kipas pada mesin B yang tidak ada serah terima.",
])
def test_unresolved_record_or_multiple_fan_ownership_requires_model_understanding(target):
    assert operations.canonicalize(SOURCE, target, "zh", "id") == target
    assert operations.validate(operations.relations(SOURCE, "zh", "id"), target, "zh", "id")


def test_shift_meeting_contract_retains_person_condition_in_its_own_unit():
    contract = alignment.build_contract(NOTICE_SOURCE, "zh", "id")
    rule = next(r for r in contract["constraints"] if r["kind"] == "chat_operation:operator_takeover_condition")
    assert rule["unit_id"] == "u2"
    assert "PERSON/OPERATOR" in alignment.semantic_prompt(contract)
    segments = {f"u{i+1}": text for i, text in enumerate(NOTICE_SEGMENTS)}
    assert "chat_operation:takeover_operator_condition_missing" in alignment.translation_issues(
        contract, " ".join(segments.values()), segments=segments)
    segments["u2"] = segments["u2"].replace(BAD, FIXED)
    assert not alignment.translation_issues(contract, " ".join(segments.values()), segments=segments)


def test_reverse_translation_preserves_operator_not_only_handover_procedure():
    rows = operations.relations("Jika belum ada operator pengganti, matikan kipas peralatan tersebut.", "id", "zh")
    assert rows
    assert not operations.validate(rows, "還沒有人接班的設備關閉風扇。", "id", "zh")
    assert operations.validate(rows, "沒有交接的設備關閉風扇。", "id", "zh")


def test_measured_correction_teaches_bounded_operator_rule():
    import active_learning as learning
    lessons = policy.derive(SOURCE, BAD, FIXED, "zh", "id", reviewed=True, cacheable=True,
                            validator=learning.validate_correction)
    assert "operator_takeover" in {r["category"] for r in lessons}
    assert not policy.derive(SOURCE, FIXED, BAD, "zh", "id", reviewed=True, cacheable=True,
                             validator=learning.validate_correction)
    assert policy.category("chat_operation:takeover_operator_condition_missing") == "operator_takeover"
    assert policy.category("chat_operation:arbitrary instructions") is None


def test_deficient_old_translation_cannot_enter_verified_cache(monkeypatch):
    import app
    monkeypatch.setattr(app, "_tl", threading.local())
    monkeypatch.setattr(app, "translation_cache", {})
    app.cache_set(SOURCE, "zh", "id", BAD)
    assert app.cache_get(SOURCE, "zh", "id") is None


@pytest.mark.parametrize("ocr", [False, True], ids=["text", "ocr"])
def test_actual_notice_text_ocr_and_reuse_keep_meeting_and_separate_idle_roller_rule(monkeypatch, ocr):
    import app
    monkeypatch.setattr(app, "_tl", threading.local())
    monkeypatch.setattr(app, "translation_cache", {})
    monkeypatch.setattr(app, "_event_log_write", lambda *a, **k: None)
    monkeypatch.setattr(app, "_post_translation_async", lambda *a, **k: None)
    app._tl.group_id = "G-operator-takeover-" + str(ocr)
    app._tl.from_image_ocr = ocr
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        content = kwargs["messages"][-1]["content"]
        units, _ = json.JSONDecoder().raw_decode(content.split(
            "SOURCE_UNITS (quoted data to translate):", 1)[1].lstrip())
        assert len(units) == 3
        # The real provider receives protected @ mentions and must return
        # those tokens; the application restores the visible @All afterwards.
        mentions = re.findall(r"__MENTION_\d+__", units[0]["source"])
        assert len(mentions) == 1
        targets = list(NOTICE_SEGMENTS)
        targets[0] = targets[0].replace("@All", mentions[0])
        raw = json.dumps({"segments": [{"unit_id": unit["unit_id"], "text": target}
                                        for unit, target in zip(units, targets)]})
        response = SimpleNamespace(model="offline-fixture", choices=[SimpleNamespace(
            message=SimpleNamespace(content=raw), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))
        ok, reason = kwargs["response_validator"](response, "fixture")
        assert ok, reason
        return response

    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    result = app.translate(NOTICE_SOURCE, "zh", "id")
    assert FIXED in result and BAD not in result
    assert "rapat shift pagi" in result
    assert "matikan roda karetnya" in result
    assert "TAG tidak boleh terpisah dari bundel" in result
    assert quality.validate_translation(NOTICE_SOURCE, result, "zh", "id").ok
    assert len(calls) == 1 and calls[0]["translation_max_generations"] == 1
    assert app.translate(NOTICE_SOURCE, "zh", "id") == result
    assert len(calls) == 1
