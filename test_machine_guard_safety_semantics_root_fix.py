from __future__ import annotations

import json
from pathlib import Path

import app
import factory_message_semantics as semantics


SCREENSHOT_SOURCE = (
    "@All 點名不會太早離開，注意紀律不要太鬆懈，設備護網要隨手蓋上，"
    "剛剛被提醒多台設備沒蓋好"
)
SCREENSHOT_TARGET = (
    "@All Saat pengecekan kehadiran, kita tidak akan meninggalkan tempat terlalu awal. "
    "Tetap jaga kedisiplinan dan jangan lengah. Setelah menggunakan mesin, segera "
    "pasang kembali pelindung mesin. Saya baru saja diingatkan bahwa pelindung "
    "pada beberapa mesin belum dipasang kembali dengan benar."
)
SCREENSHOT_BAD = (
    "@All Saat absen, jangan pulang terlalu cepat. Perhatikan disiplin dan jangan "
    "terlalu longgar. Tutup kembali pelindung mesin setelah digunakan. Baru saja "
    "diingatkan bahwa beberapa mesin tidak ditutup dengan benar."
)

REMINDER_SOURCE = "@法比恩 Fabian 設備護網幫忙提醒一下"
REMINDER_TARGET = (
    "@法比恩 Fabian Mohon bantu ingatkan agar pelindung mesin dipasang kembali "
    "dengan benar."
)
REMINDER_BAD = (
    "@法比恩 Fabian Mohon bantu mengingatkan tentang pelindung jaring peralatan."
)


def test_reported_notice_is_rebuilt_from_guard_safety_relations():
    frame = semantics.build_frame(SCREENSHOT_SOURCE, "zh", "id")

    assert frame["active"] is True
    assert frame["complete"] is True
    assert frame["kind"] == "zh_id_machine_guard_safety"
    assert frame["unparsed"] == ""
    assert semantics.translate_source_directly(
        SCREENSHOT_SOURCE, "zh", "id"
    ) == SCREENSHOT_TARGET
    assert semantics.validate_translation(frame, SCREENSHOT_TARGET) == (True, [])


def test_reported_fluent_but_wrong_translation_is_rejected_relationally():
    frame = semantics.build_frame(SCREENSHOT_SOURCE, "zh", "id")

    ok, issues = semantics.validate_translation(frame, SCREENSHOT_BAD)

    assert ok is False
    assert "factory_message_semantics:attendance_statement_changed_to_command" in issues
    assert (
        "factory_message_semantics:discipline_mistranslated_as_physical_looseness"
        in issues
    )
    assert (
        "factory_message_semantics:machine_replaced_guard_as_closed_subject"
        in issues
    )


def test_short_reminder_preserves_full_mention_and_makes_guard_duty_actionable():
    frame = semantics.build_frame(REMINDER_SOURCE, "zh", "id")

    assert frame["mentions"] == ["@法比恩 Fabian"]
    assert frame["complete"] is True
    assert semantics.translate_source_directly(
        REMINDER_SOURCE, "zh", "id"
    ) == REMINDER_TARGET
    assert semantics.validate_translation(frame, REMINDER_TARGET) == (True, [])

    ok, issues = semantics.validate_translation(frame, REMINDER_BAD)
    assert ok is False
    assert "factory_message_semantics:machine_guard_unnatural_literal_term" in issues
    assert "factory_message_semantics:machine_guard_reminder_object_incomplete" in issues


def test_attendance_statement_and_prohibition_keep_different_modalities():
    statement = semantics.translate_source_directly(
        "@All 點名不會太早離開，設備護網要蓋好", "zh", "id"
    )
    prohibition = semantics.translate_source_directly(
        "@All 點名不要太早離開，設備護網要蓋好", "zh", "id"
    )

    assert "tidak akan meninggalkan tempat terlalu awal" in statement
    assert "jangan meninggalkan tempat terlalu awal" not in statement
    assert "jangan meninggalkan tempat terlalu awal" in prohibition
    assert "tidak akan meninggalkan tempat terlalu awal" not in prohibition


def test_guard_alias_counts_and_anaphoric_subject_are_compositional():
    source = (
        "@All 機台護罩用完要蓋回去，剛被提醒三台機台護罩沒關好"
    )
    target = semantics.translate_source_directly(source, "zh", "id")

    assert "pelindung mesin" in target
    assert "pelindung pada tiga mesin" in target
    assert "tiga mesin belum ditutup" not in target
    assert semantics.validate_translation(
        semantics.build_frame(source, "zh", "id"), target
    ) == (True, [])


def test_extra_clause_is_never_dropped_by_direct_renderer():
    source = "設備護網要蓋好，明天停機保養"
    frame = semantics.build_frame(source, "zh", "id")

    assert frame["active"] is True
    assert frame["complete"] is False
    assert "明天停機保養" in frame["unparsed"]
    assert semantics.translate_source_directly(source, "zh", "id") == ""


def test_unrelated_network_equipment_and_ambiguous_machine_state_do_not_trigger():
    for source in (
        "網路設備幫忙提醒一下",
        "多台設備沒蓋好",
        "護網破損請叫修",
    ):
        assert semantics.build_frame(source, "zh", "id")["active"] is False


def test_glossary_and_app_require_the_same_versioned_safety_engine():
    root = Path(__file__).resolve().parent
    glossary = json.loads((root / "glossary_data.json").read_text(encoding="utf-8"))
    entry = glossary["設備護網"]
    assert entry["canonical_idn"] == "pelindung mesin"
    assert "護網" in entry["aliases_zh"]
    assert "pelindung jaring peralatan" in entry["forbidden_idn"]

    app_source = (root / "app.py").read_text(encoding="utf-8")
    assert (
        '_EXPECTED_FACTORY_MESSAGE_SEMANTICS_BUILD_ID = '
        '"2026-08-30.1-shopfloor-agent-roles"'
    ) in app_source
    assert semantics.health()["self_test"]["ok"] is True


def test_public_translation_pipeline_uses_source_relations_before_any_provider(
    monkeypatch,
):
    def provider_must_not_run(*_args, **_kwargs):
        raise AssertionError("complete machine-guard frame must not call a provider")

    monkeypatch.setattr(app, "_translate_inner", provider_must_not_run)

    assert app.translate(SCREENSHOT_SOURCE, "zh", "id") == SCREENSHOT_TARGET
    assert app.translate(REMINDER_SOURCE, "zh", "id") == REMINDER_TARGET
    assert app._get_translation_outcome()["reason"] == "source_first_relation_translation"
