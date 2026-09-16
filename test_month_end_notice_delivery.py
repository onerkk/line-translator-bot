"""September 16 incident: real validators, actual handler, fake external I/O."""
from types import SimpleNamespace

import pytest

import app
import factory_knowledge as knowledge
import factory_semantic_audit as audit
import translation_retry_queue as queue
from test_translation_notice_availability import (
    clean_translation_context, runtime, event, delivered_text, retry_pending,
)


SOURCE = ("中班幫忙入19噸，今日計劃量破130噸。\n\n"
          "本月入庫目標提高到3600，明天開始到月底前平均一天143噸，"
          "月底前人力會優先開三站，入庫時間注意平均一點。")
TARGET = ("Shift tengah, tolong masukkan 19 ton ke gudang agar rencana hari ini tembus 130 ton.\n\n"
          "Target pemasukan gudang bulan ini dinaikkan menjadi 3.600 ton. "
          "Mulai besok sampai akhir bulan, rata-rata 143 ton per hari. "
          "Sampai akhir bulan, tenaga kerja akan diprioritaskan untuk mengoperasikan tiga stasiun. "
          "Atur waktu pencatatan masuk gudang agar lebih merata.")


@pytest.mark.parametrize("until", ["sampai", "hingga", "sampai dengan"])
def test_complete_notice_accepts_period_through_month_end(until):
    target = TARGET.replace("sampai akhir", until + " akhir").replace("Sampai akhir", until.capitalize() + " akhir")
    assert app.factory_translation_guard_module.exact_verified_target(SOURCE, "zh", "id") is None
    app._tl.semantic_contract = app.build_translation_semantic_contract(SOURCE, "zh", "id")
    response = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=target), finish_reason="stop")])
    assert app._build_translation_response_validator(SOURCE, "zh", "id")(response, "test") == (True, "ok")
    assert app._final_delivery_guard(SOURCE, target, "zh", "id") == target


@pytest.mark.parametrize("wording", [
    "Atur waktu pencatatan masuk gudang agar lebih merata.",
    "Atur agar waktu masuk gudang dicatat lebih merata.",
    "Waktu input data masuk gudang perlu diatur lebih merata.",
])
def test_record_timing_accepts_active_passive_and_nominal_forms(wording):
    source = "入庫時間注意平均一點。"
    cards = knowledge.retrieve(source, "zh", "id", limit=10)
    assert knowledge.validate_translation(cards, source, wording) == (True, [])


@pytest.mark.parametrize("wording", [
    "Target pemasukan gudang bulan ini 3.600 ton.",
    "Target barang masuk ke gudang bulan ini 3.600 ton.",
    "Target penerimaan barang di gudang bulan ini 3.600 ton.",
])
def test_warehouse_intake_accepts_equivalent_indonesian(wording):
    source = "本月入庫目標3600噸。"
    assert knowledge.validate_translation(knowledge.retrieve(source, "zh", "id", limit=10), source, wording) == (True, [])


@pytest.mark.parametrize("candidate", [
    TARGET.replace("Mulai besok sampai akhir bulan, ", ""),
    TARGET.replace("Sampai akhir bulan, tenaga", "Setelah akhir bulan, tenaga"),
    TARGET.replace("Mulai besok", "Mulai hari ini"),
    TARGET.replace("143 ton", "134 ton"),
    TARGET.replace("tiga stasiun", "dua stasiun"),
    TARGET.replace("Atur waktu pencatatan masuk gudang agar lebih merata.", ""),
])
def test_quality_defects_are_advisory_and_disable_learning(candidate):
    assert app._delivery_validation_issues(SOURCE, candidate, "zh", "id")
    assert app._final_delivery_guard(SOURCE, candidate, "zh", "id") == candidate.strip()
    assert app._tl.cacheable is False
    assert app._tl.delivery_degraded is True


def test_actual_handler_sends_complete_incident_without_retry(runtime):
    runtime.provider_result = TARGET
    app.handle_message(event(SOURCE))
    assert TARGET in delivered_text(runtime)
    assert len(runtime.generations) == len(runtime.sends) == 1
    assert queue.pending_count() == 0
    app.handle_message(event(SOURCE))
    assert len(runtime.generations) == len(runtime.sends) == 1


def test_empty_provider_does_not_requeue_or_repeat_on_redelivery(runtime):
    runtime.provider_down = True
    app.handle_message(event(SOURCE))
    assert not runtime.sends
    key = "notice-group:notice-message"
    assert queue.get(key)["status"] == "failed"
    assert not queue.was_delivered(key)
    assert queue.pending_count() == 0
    assert not queue.claim_job(key, owner="recovery")
    app.handle_message(event(SOURCE))
    assert len(runtime.generations) == 1
