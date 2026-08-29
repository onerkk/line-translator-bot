from __future__ import annotations

import json
from pathlib import Path

import app
import factory_message_semantics as semantics


SCREENSHOT_SOURCE = "點名開車走了👋"
SCREENSHOT_TARGET = (
    "Setelah pengecekan kehadiran selesai, berangkat dengan mobil. 👋"
)
SCREENSHOT_BAD = "Setelah absensi, kendaraan berangkat lebih dulu."


def test_screenshot_event_is_rebuilt_from_source_roles():
    frame = semantics.build_frame(SCREENSHOT_SOURCE, "zh", "id")

    assert frame["active"] is True
    assert frame["complete"] is True
    assert frame["kind"] == "zh_id_attendance_vehicle_departure"
    assert frame["unparsed"] == ""
    assert frame["slots"]["actor_source"] == ""
    assert frame["slots"]["priority"] is False
    assert frame["slots"]["emoji_tokens"] == ["👋"]
    assert semantics.translate_source_directly(
        SCREENSHOT_SOURCE, "zh", "id"
    ) == SCREENSHOT_TARGET
    assert semantics.validate_translation(frame, SCREENSHOT_TARGET) == (True, [])


def test_reported_fluent_role_swap_addition_and_emoji_loss_are_all_rejected():
    frame = semantics.build_frame(SCREENSHOT_SOURCE, "zh", "id")

    ok, issues = semantics.validate_translation(frame, SCREENSHOT_BAD)

    assert ok is False
    assert "factory_message_semantics:human_vehicle_departure_missing" in issues
    assert (
        "factory_message_semantics:vehicle_promoted_to_departure_actor" in issues
    )
    assert "factory_message_semantics:ungrounded_departure_priority" in issues
    assert "factory_message_semantics:source_emoji_missing:👋" in issues


def test_actor_time_modality_destination_and_priority_are_compositional():
    cases = {
        "我點名後開車離開了": (
            "Setelah pengecekan kehadiran selesai, saya sudah meninggalkan "
            "lokasi dengan mobil."
        ),
        "点完名他先开车回家了": (
            "Setelah pengecekan kehadiran selesai, dia sudah pulang lebih "
            "dahulu dengan mobil."
        ),
        "點名前我會開車離開": (
            "Sebelum pengecekan kehadiran, saya akan meninggalkan lokasi "
            "dengan mobil."
        ),
        "在點名時不要開車走": (
            "Saat pengecekan kehadiran, jangan berangkat dengan mobil."
        ),
        "我點名後直接開車離場了": (
            "Setelah pengecekan kehadiran selesai, saya sudah langsung "
            "meninggalkan lokasi dengan mobil."
        ),
    }

    for source, expected in cases.items():
        frame = semantics.build_frame(source, "zh-TW", "id-ID")
        assert frame["active"] is True
        assert frame["complete"] is True
        assert semantics.translate_source_directly(
            source, "zh-TW", "id-ID"
        ) == expected
        assert semantics.validate_translation(frame, expected) == (True, [])


def test_priority_words_are_allowed_only_when_the_source_contains_priority():
    no_priority = semantics.build_frame(SCREENSHOT_SOURCE, "zh", "id")
    with_priority_source = "點完名他先開車回家了"
    with_priority = semantics.build_frame(with_priority_source, "zh", "id")

    assert semantics.validate_translation(
        no_priority,
        "Setelah pengecekan kehadiran selesai, berangkat lebih dahulu "
        "dengan mobil. 👋",
    )[0] is False
    assert semantics.validate_translation(
        with_priority,
        "Setelah pengecekan kehadiran selesai, dia sudah pulang dengan mobil.",
    )[0] is False


def test_vehicle_subject_sources_and_non_departure_phrases_do_not_trigger():
    for source in (
        "點名後車輛開走了",
        "點名開車的人到了",
        "點名後走路離開了",
        "車開走了",
    ):
        assert semantics.build_frame(source, "zh", "id")["active"] is False


def test_extra_clause_is_not_dropped_by_the_fast_renderer():
    source = "點名開車走了，明天停機保養"
    frame = semantics.build_frame(source, "zh", "id")

    assert frame["active"] is True
    assert frame["complete"] is False
    assert "明天停機保養" in frame["unparsed"]
    assert semantics.translate_source_directly(source, "zh", "id") == ""


def test_mentions_and_full_emoji_clusters_are_preserved():
    source = "@小麥（研磨股班長） 點名開車走了👋🏻"
    expected = (
        "@小麥（研磨股班長） Setelah pengecekan kehadiran selesai, "
        "berangkat dengan mobil. 👋🏻"
    )
    frame = semantics.build_frame(source, "zh", "id")

    assert frame["mentions"] == ["@小麥（研磨股班長）"]
    assert frame["slots"]["emoji_tokens"] == ["👋🏻"]
    assert semantics.translate_source_directly(source, "zh", "id") == expected
    assert semantics.validate_translation(frame, expected) == (True, [])


def test_public_pipeline_uses_the_local_source_first_route_without_a_provider(
    monkeypatch,
):
    def provider_must_not_run(*_args, **_kwargs):
        raise AssertionError("complete short-event frame must not call a provider")

    monkeypatch.setattr(app, "_translate_inner", provider_must_not_run)

    assert app.translate(SCREENSHOT_SOURCE, "zh", "id") == SCREENSHOT_TARGET
    assert app._get_translation_outcome()["reason"] == (
        "source_first_relation_translation"
    )
    debug = app._get_last_translate_debug()
    assert debug["pipeline_status"] == "source_first_relation_translation"
    assert debug["openai_status"] == "not_needed"


def test_deployment_contract_requires_the_new_role_integrity_engine():
    root = Path(__file__).resolve().parent
    app_source = (root / "app.py").read_text(encoding="utf-8")
    glossary = json.loads(
        (root / "glossary_data.json").read_text(encoding="utf-8")
    )

    assert (
        '_EXPECTED_FACTORY_MESSAGE_SEMANTICS_BUILD_ID = '
        '"2026-08-29.1-unit-trolley-ownership"'
    ) in app_source
    health = semantics.health()
    assert health["build_id"] == "2026-08-29.1-unit-trolley-ownership"
    assert health["self_test"]["ok"] is True
    assert health["self_test"]["checks"] >= 42
    assert glossary["點名"]["canonical_idn"] == "pengecekan kehadiran"
    assert "absensi" in glossary["點名"]["forbidden_idn"]
