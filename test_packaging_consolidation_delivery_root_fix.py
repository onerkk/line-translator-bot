from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import app
import factory_knowledge
import factory_semantic_audit as semantic_audit


SOURCE = (
    "急單再幫忙注意一下，大成改明天關帳，有到站優先包。\n\n"
    "大成出貨量不足，連同已在待併包內一起檢視，大成來料大於250公斤，"
    "後面無料或是待併的料還很遠就獨立包起來。"
)
GOOD = (
    "Mohon perhatikan lagi work order mendesak. 大成 mengubah jadwal tutup buku "
    "menjadi besok. Jika material sudah tiba di stasiun, prioritaskan packing.\n\n"
    "Untuk 大成, jumlah material yang siap dikirim tidak mencukupi. Periksa juga "
    "material yang sudah berada dalam daftar tunggu penggabungan packing. Jika "
    "jumlah material masuk untuk 大成 melebihi 250 kg, lalu tidak ada material "
    "berikutnya atau material berikutnya yang akan digabung masih lama tibanya, "
    "kemas secara terpisah."
)


def test_nominal_incoming_material_quantity_is_not_an_arrival_event():
    frame = semantic_audit.build_source_frame(
        "大成來料大於250公斤，請檢查重量。", "zh", "id"
    )
    profile = frame["operational"]["arrival_profile"]
    assert profile["event"] is False
    assert profile["future"] is False
    assert profile["nominal_mentions"] == ["來料"]
    assert frame["flags"]["arrival"] is False
    assert "material_arrival" not in {claim["claim_id"] for claim in frame["claims"]}


def test_at_station_condition_is_tense_appropriate_and_source_bound():
    frame = semantic_audit.build_source_frame(SOURCE, "zh", "id")
    profile = frame["operational"]["arrival_profile"]
    assert profile == {
        "event": True,
        "future": False,
        "completed": False,
        "conditional": True,
        "at_station": True,
        "evidence": ["到站"],
        "nominal_mentions": ["來料"],
    }
    assert semantic_audit.validate_translation(frame, GOOD) == (True, [])

    missing_condition = GOOD.replace("Jika material sudah tiba", "Material sudah tiba")
    ok, issues = semantic_audit.validate_translation(frame, missing_condition)
    assert ok is False
    assert "factory_semantic_audit:missing_conditional_material_arrival" in issues


def test_future_bulk_arrival_remains_strict():
    source = "月底前拋光機大尺寸棒材會集中大量到料，I5優先生產。"
    frame = semantic_audit.build_source_frame(source, "zh", "id")
    assert frame["flags"]["arrival"] is True
    assert frame["flags"]["arrival_future"] is True

    candidate = (
        "Sebelum akhir bulan, material batang berukuran besar untuk mesin polishing "
        "tiba dalam jumlah besar dalam waktu yang berdekatan. I5 memprioritaskan produksi."
    )
    ok, issues = semantic_audit.validate_translation(frame, candidate)
    assert ok is False
    assert "factory_semantic_audit:missing_future_material_arrival" in issues


def test_generalized_packaging_workflow_knowledge_is_retrieved_and_validated():
    cards = factory_knowledge.retrieve(SOURCE, "zh", "id", limit=5)
    assert [card["id"] for card in cards] == [
        "packaging_consolidation_threshold_decision"
    ]
    assert factory_knowledge.validate_translation(cards, SOURCE, GOOD) == (True, [])

    bad = GOOD.replace("tidak ada material berikutnya", "ada material gratis")
    ok, issues = factory_knowledge.validate_translation(cards, SOURCE, bad)
    assert ok is False
    assert any("no_following_material" in issue for issue in issues)
    assert any("forbidden:material gratis" in issue for issue in issues)


def test_valid_translation_reaches_the_real_delivery_boundary():
    assert app._delivery_validation_issues(SOURCE, GOOD, "zh", "id") == []
    assert app._final_delivery_guard(SOURCE, GOOD, "zh", "id") == GOOD


def test_unseen_line_redelivery_is_recovered_but_live_duplicate_is_suppressed():
    message_id = "redelivery-root-fix-" + uuid.uuid4().hex
    event = SimpleNamespace(
        message=SimpleNamespace(id=message_id),
        delivery_context=SimpleNamespace(is_redelivery=True),
    )
    app._release_processed_message(message_id)
    try:
        assert app._should_skip_message_event(event) is False
        assert app._should_skip_message_event(event) is True

        body = json.dumps({"events": [{"message": {"id": message_id}}]})
        app._release_webhook_message_claims(body)
        assert app._should_skip_message_event(event) is False
    finally:
        app._release_processed_message(message_id)
