"""ERP reason abbreviations must not replace ordinary instructions or questions.

The real LINE handler and translation pipeline run with offline provider/LINE
transports. No paid API calls or messages are sent.
"""
import time

import pytest

import app
from test_translation_notice_availability import runtime, event, delivered_text  # noqa: F401


SOURCE = "套環要補上"
TARGET = "Cincin Pelindung harus dipasang."
WRONG = "Timbang ulang berat kotor"


@pytest.mark.parametrize("source", [
    SOURCE, "套環要補上。", "標籤要補上", "護罩要補上", "補上報表",
    "不要補毛重", "補毛重完成", "補毛重了嗎", "重點要看", "請退一步",
    "倒角不要做", "取樣已完成", "改Tag了嗎", "重新包裝完成了",
    "原因：套環要補上", "原因：不要補毛重", "原因：補毛重完成",
])
def test_prose_never_becomes_an_erp_reason_command(source):
    assert app._factory_reason_semantic_translate_zh_id(source) is None
    assert app._factory_reason_contract_risk(source) is None


@pytest.mark.parametrize("source", ["補", "毛", "重", "退", "角", "取", "併"])
def test_ambiguous_single_character_requires_an_explicit_reason_cell(source):
    assert app._factory_reason_semantic_translate_zh_id(source) is None


@pytest.mark.parametrize("source,target", [
    ("補毛重", WRONG), ("原因：補毛重", WRONG), ("原因欄：補", WRONG),
    ("端漆", "Ubah warna cat ujung"), ("改TAG", "Input ulang data dan tempel ulang TAG"),
    ("ID | 原因\n7H110003 | 補", "ID | Alasan\n7H110003 | " + WRONG),
    ("ID | 原因\n7H110003\n補", "ID | Alasan\n7H110003 | " + WRONG),
])
def test_complete_reason_labels_and_explicit_ocr_cells_keep_fast_translation(source, target):
    assert app._factory_reason_semantic_translate_zh_id(source) == target


@pytest.mark.parametrize("source", [
    "ID | 原因\n7H110003 | 套環要補上",
    "ID | 原因\n7H110003 | 補毛重\n7H110004 | 套環要補上",
    "ID | 原因\n7H110003\n補毛重\n7H110004\n套環要補上",
    "ID | 原因\n7H110003 | 補毛重\n7H110004 | 不要補毛重",
    "ID | 原因\n7H110003\n補毛重\n7H110004",
    "ID | 原因\n7H110003\n補毛重\n削皮",
])
def test_unknown_reason_cells_cannot_be_invented_or_silently_dropped(source):
    assert app._factory_reason_semantic_translate_zh_id(source) is None


def test_reason_contract_does_not_invent_other_operations_from_single_characters():
    source = "ID | 原因\n7H110003 | 補毛重\n7H110004 | 改端漆"
    risk = app._factory_reason_contract_risk(source)
    assert set(risk["entries"]) == {"補毛重", "改端漆"}


def test_actual_line_delivery_preserves_the_installation_instruction(runtime):
    runtime.provider_result = TARGET
    app.handle_message(event(SOURCE))
    assert TARGET in delivered_text(runtime)
    assert WRONG not in delivered_text(runtime)
    assert runtime.generations == [(SOURCE, "zh", "id")]


def test_unrelated_provider_result_cannot_reach_line(runtime):
    runtime.provider_result = WRONG
    app.handle_message(event(SOURCE))
    assert runtime.generations
    assert WRONG not in delivered_text(runtime)


def test_collar_uses_existing_glossary_sense_and_blocks_unrelated_weight_output(runtime):
    prompt = app.inject_glossary_hint(SOURCE, "zh", "id")
    assert "套環" in prompt and "Cincin Pelindung" in prompt
    assert app._final_delivery_guard(SOURCE, TARGET, "zh", "id") == TARGET
    assert app._final_delivery_guard(SOURCE, WRONG, "zh", "id") is None


@pytest.mark.parametrize("source,target", [
    ("套環不要補上", "Jangan pasang cincin pelindung."),
    ("套環已經補上了", "Cincin pelindung sudah dipasang."),
    ("套環補上了嗎？", "Apakah cincin pelindung sudah dipasang?"),
    ("套環要補上，毛重也要重秤", "Pasang cincin pelindung dan timbang ulang berat kotor."),
    ("套環要補上", "Pasang ring pelindung yang belum terpasang."),
])
def test_collar_guard_accepts_states_and_a_separate_real_weighing_task(runtime, source, target):
    assert app._final_delivery_guard(source, target, "zh", "id") == target


def test_old_reason_policy_cache_is_not_reused_after_fix(runtime, monkeypatch):
    monkeypatch.setattr(app, "_translation_cache_context_bound", lambda _text: False)
    old_version = app._FACTORY_REASON_SEMANTICS_BUILD_ID + ".old"
    with monkeypatch.context() as old:
        old.setattr(app, "_FACTORY_REASON_SEMANTICS_BUILD_ID", old_version)
        old_fingerprint = app._translation_cache_asset_fingerprint()
        old_persistent = app._translation_cache_persistent_fingerprint()
    key = (SOURCE, "zh", "id", app._translation_cache_scope())
    app.translation_cache[key] = (WRONG, time.time(), old_fingerprint)
    assert app._translation_cache_persistent_fingerprint() != old_persistent
    assert app.cache_get(SOURCE, "zh", "id") is None
    assert key not in app.translation_cache
