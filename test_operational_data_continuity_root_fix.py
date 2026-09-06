from __future__ import annotations

from pathlib import Path
from unittest import mock

import expressive_assets
import expressive_engine
import factory_message_semantics as semantics
import factory_translation_guard as guard
import translation_quality_gate as quality_gate

try:
    import app
except ModuleNotFoundError:  # Semantic checks still run in a minimal CI image.
    app = None


ROOT = Path(__file__).resolve().parent

TROLLEY_SOURCE = "@辰 @Dato潘 台車幫忙一下"
TROLLEY_TARGET = "@辰 @Dato潘 Mohon bantu menangani troli sebentar."

FREEZE_SOURCE = "@All 16：40後電腦上不要再做任何資料異動"
FREEZE_TARGET = (
    "@All Setelah pukul 16:40, jangan lakukan perubahan data apa pun lagi "
    "di komputer."
)

TRANSLATION_REQUEST_SOURCE = "@法比恩 Fabian 幫忙翻譯，確保設備人員都明白"
TRANSLATION_REQUEST_TARGET = (
    "@法比恩 Fabian Mohon bantu menerjemahkan ini agar semua personel yang "
    "menangani peralatan memahaminya."
)

MANUAL_HANDOVER_SOURCE = (
    "16：40～20：00需要異動的ID，在手寫報表背面寫上ID、重量、支數的紀錄，"
    "拍照傳到群組，9/2新系統開始使用，再用新系統異動資料"
)
MANUAL_HANDOVER_TARGET = (
    "Untuk ID yang perlu diubah antara pukul 16:40 dan 20:00, catat ID, "
    "berat, dan jumlah batang pada sisi belakang formulir laporan manual. "
    "Foto catatan tersebut lalu kirimkan ke grup. Mulai 2 September, gunakan "
    "sistem baru untuk melakukan perubahan data."
)

WEIGHT_FALLBACK_SOURCE = (
    "機器有生產。沒有辦法使用電腦維護重量的也沒關係。"
    "都直接寫在報表上面。早上一併交回！"
)
WEIGHT_FALLBACK_TARGET = (
    "Mesin tetap berproduksi. Tidak masalah jika data berat tidak dapat "
    "diperbarui melalui komputer. Catat semua data tersebut langsung pada "
    "formulir laporan. Kembalikan seluruh formulir laporan tersebut sekaligus "
    "pada pagi hari."
)

SYSTEM_OUTAGE_SOURCE = (
    "@All 生產上。電腦資料有任何問題直接拍照下來填寫原因！"
    "另外因為系統問題、很多資料沒辦法使用的、可以生產就生產、"
    "直接填寫報表、早上交上來、記得要寫日期"
)
SYSTEM_OUTAGE_TARGET = (
    "@All Jika ada masalah pada data di komputer, segera ambil foto untuk "
    "dokumentasi dan tuliskan penyebabnya. Jika banyak data tidak dapat "
    "digunakan karena gangguan sistem, tetap lakukan produksi jika memungkinkan. "
    "Isi formulir laporan secara langsung, serahkan pada pagi hari, dan pastikan "
    "tanggalnya dicantumkan."
)


DIRECT_CASES = (
    (TROLLEY_SOURCE, "zh_id_trolley_assistance", TROLLEY_TARGET),
    (FREEZE_SOURCE, "zh_id_computer_data_change_freeze", FREEZE_TARGET),
    (
        TRANSLATION_REQUEST_SOURCE,
        "zh_id_equipment_staff_translation_request",
        TRANSLATION_REQUEST_TARGET,
    ),
    (MANUAL_HANDOVER_SOURCE, "zh_id_manual_data_change_handover", MANUAL_HANDOVER_TARGET),
    (WEIGHT_FALLBACK_SOURCE, "zh_id_system_outage_manual_reporting", WEIGHT_FALLBACK_TARGET),
    (SYSTEM_OUTAGE_SOURCE, "zh_id_system_outage_manual_reporting", SYSTEM_OUTAGE_TARGET),
)


def test_every_reported_workflow_is_parsed_and_rendered_from_source_roles():
    for source, expected_kind, expected_target in DIRECT_CASES:
        frame = semantics.build_frame(source, "zh-TW", "id-ID")
        assert frame["active"] is True, source
        assert frame["complete"] is True, (source, frame["unparsed"])
        assert frame["kind"] == expected_kind
        assert semantics.translate_source_directly(source, "zh-TW", "id-ID") == expected_target
        assert semantics.validate_translation(frame, expected_target) == (True, [])


def test_reported_failures_are_rejected_for_the_actual_relation_error():
    cases = (
        (
            TROLLEY_SOURCE,
            "Tolong bantu troli sebentar. 🙂",
            {"trolley_handling_relation_missing", "ungrounded_emoji_added:🙂"},
        ),
        (
            TRANSLATION_REQUEST_SOURCE,
            "Tolong bantu terjemahkan agar semua petugas peralatan memahaminya.",
            {"equipment_staff_relation_ambiguous"},
        ),
        (
            MANUAL_HANDOVER_SOURCE,
            (
                "ID yang perlu diubah dari 16:40~20:00, catat ID, berat, dan jumlah "
                "batang di belakang laporan tulis tangan. 📌 Foto lalu kirim ke grup. "
                "✅ Mulai 9/2 gunakan sistem baru, kemudian ubah data di sistem baru. 👀"
            ),
            {"month_day_date_ambiguous", "ungrounded_emoji_added:📌"},
        ),
        (
            WEIGHT_FALLBACK_SOURCE,
            (
                "Mesin tetap berproduksi. Jika tidak bisa menggunakan komputer untuk "
                "mencatat berat, tidak masalah. Langsung tulis di laporan. "
                "Kembalikan semuanya sekaligus pagi hari!"
            ),
            {"weight_data_update_changed_to_recording"},
        ),
    )
    for source, bad_target, expected_suffixes in cases:
        frame = semantics.build_frame(source, "zh", "id")
        ok, issues = semantics.validate_translation(frame, bad_target)
        assert ok is False, (source, issues)
        for suffix in expected_suffixes:
            assert any(item.endswith(suffix) for item in issues), (suffix, issues)


def test_semantically_complete_provider_paraphrase_is_not_over_rejected():
    screenshot_target = (
        "@All Untuk produksi, jika ada masalah dengan data di komputer, langsung "
        "foto dan tuliskan alasannya! Selain itu, jika karena masalah sistem banyak "
        "data yang tidak bisa digunakan, tetap lakukan produksi jika memungkinkan, "
        "lalu langsung isi laporan dan serahkan pagi hari. Ingat untuk menulis tanggal."
    )
    frame = semantics.build_frame(SYSTEM_OUTAGE_SOURCE, "zh", "id")
    assert semantics.validate_translation(frame, screenshot_target) == (True, [])
    assert guard.validate_translation(
        SYSTEM_OUTAGE_SOURCE, screenshot_target, "zh", "id"
    ).ok is True

    weight_entry_target = (
        "Mesin tetap berproduksi. Tidak masalah jika data berat tidak dapat "
        "dimasukkan melalui komputer. Catat semua data tersebut langsung pada "
        "laporan. Kembalikan seluruh laporan sekaligus pada pagi hari."
    )
    weight_frame = semantics.build_frame(WEIGHT_FALLBACK_SOURCE, "zh", "id")
    assert semantics.validate_translation(weight_frame, weight_entry_target) == (True, [])


def test_compositional_variants_are_supported_without_exact_sentence_matching():
    variants = (
        (
            "@阿明 請再幫忙處理一下台車",
            "@阿明 Mohon bantu menangani troli sekali lagi.",
        ),
        (
            "@All 18:05以後不要在電腦中變更任何資料",
            "@All Setelah pukul 18:05, jangan lakukan perubahan data apa pun lagi di komputer.",
        ),
        (
            "麻煩協助翻譯，讓所有設備人員都能理解",
            "Mohon bantu menerjemahkan ini agar semua personel yang menangani peralatan memahaminya.",
        ),
        (
            "08:10至19:25要變更的ID，在紙本報表的背面記錄ID、重量及支數，"
            "拍照後發送到群組；12／31開始使用新系統，之後透過新系統變更資料",
            (
                "Untuk ID yang perlu diubah antara pukul 08:10 dan 19:25, catat ID, "
                "berat, dan jumlah batang pada sisi belakang formulir laporan manual. "
                "Foto catatan tersebut lalu kirimkan ke grup. Mulai 31 Desember, gunakan "
                "sistem baru untuk melakukan perubahan data."
            ),
        ),
        (
            "因系統故障，多筆資料無法使用，能生產的照常生產，直接填寫紙本報表，"
            "上午集中繳回，務必註明日期",
            (
                "Jika banyak data tidak dapat digunakan karena gangguan sistem, tetap "
                "lakukan produksi jika memungkinkan. Isi formulir laporan secara langsung, "
                "serahkan sekaligus pada pagi hari, dan pastikan tanggalnya dicantumkan."
            ),
        ),
        (
            "机器有生产。没有办法使用电脑维护重量的也没关系。"
            "都直接写在报表上面。早上一并交回！",
            WEIGHT_FALLBACK_TARGET,
        ),
    )
    for source, expected in variants:
        assert semantics.translate_source_directly(source, "zh", "id") == expected, source


def test_incomplete_or_unrelated_messages_fail_closed_to_the_provider_route():
    controls = (
        "台車已經滿了",
        "我在家裡用電腦整理照片",
        "9/2是我的生日",
        "請翻譯這本設備手冊",
        "系統問題明天再討論",
    )
    for source in controls:
        frame = semantics.build_frame(source, "zh", "id")
        assert frame["active"] is False or frame["complete"] is False, (source, frame)
        assert semantics.translate_source_directly(source, "zh", "id") == ""

    extended = WEIGHT_FALLBACK_SOURCE + "午餐記得訂便當。"
    frame = semantics.build_frame(extended, "zh", "id")
    assert frame["active"] is True
    assert frame["complete"] is False
    assert "午餐記得訂便當" in frame["unparsed"]
    assert semantics.translate_source_directly(extended, "zh", "id") == ""


def test_factory_guard_blocks_bad_outputs_before_cache_or_learning():
    bad = (
        "Mesin tetap berproduksi. Jika tidak bisa menggunakan komputer untuk mencatat "
        "berat, tidak masalah. Langsung tulis di laporan. Kembalikan semuanya pagi hari."
    )
    report = guard.validate_translation(WEIGHT_FALLBACK_SOURCE, bad, "zh", "id")
    assert report.ok is False
    assert any("weight_data_update_changed_to_recording" in issue for issue in report.hard_issues)
    quality_report = quality_gate.validate_translation(
        WEIGHT_FALLBACK_SOURCE, bad, "zh", "id"
    )
    assert quality_report.ok is False
    assert "factory_message_semantics:weight_data_update_changed_to_recording" in quality_report.issues


def test_operational_messages_are_formal_and_never_gain_new_emoji():
    for source, _kind, target in DIRECT_CASES:
        assert expressive_assets.classify_context(source) == "factory"
        result = expressive_engine.enhance_translation(
            source,
            target,
            source_language="zh",
            settings=expressive_engine.ExpressiveSettings(
                enabled=True,
                display_mode="emoji",
                emoji_enabled=True,
                images_enabled=False,
                formal_safety_enabled=True,
            ),
        )
        assert result.text == target
        assert result.decorated_count == 0


def test_complete_frames_bypass_all_external_translation_providers():
    if app is None:
        return
    with mock.patch.object(
        app,
        "_translate_inner",
        side_effect=AssertionError("a complete operational frame must not call a provider"),
    ):
        for source, _kind, expected in DIRECT_CASES:
            assert app.translate(source, "zh", "id") == expected
            assert app._get_translation_outcome()["reason"] == "source_first_relation_translation"


def test_style_buttons_cannot_reintroduce_a_provider_semantic_error():
    if app is None:
        return
    screenshot_bad = (
        "Mesin tetap berproduksi. Jika tidak bisa menggunakan komputer untuk mencatat "
        "berat, tidak masalah. Langsung tulis di laporan. Kembalikan semuanya pagi hari."
    )
    with mock.patch.object(app, "translate_openai", return_value=screenshot_bad):
        assert app._translate_variant_preserving_mentions(
            WEIGHT_FALLBACK_SOURCE, "zh", "id"
        ) == WEIGHT_FALLBACK_TARGET


def test_release_contract_runs_the_new_behavioral_self_test_at_boot():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'VERSION = "v3.49.0-operational-data-continuity-root-fix-2026-09-02"' in app_source
    assert (
        '_EXPECTED_FACTORY_MESSAGE_SEMANTICS_BUILD_ID = '
        '"2026-09-07.2-release-predicate-polarity"'
    ) in app_source
    assert '_EXPECTED_EXPRESSIVE_ASSETS_VERSION = "2026-09-02.1-operational-record-context"' in app_source
    assert f'_EXPECTED_QG_BUILD_ID = "{quality_gate.QUALITY_GATE_BUILD_ID}"' in app_source
    health = semantics.health()
    assert health["build_id"] == "2026-09-07.2-release-predicate-polarity"
    assert health["self_test"]["ok"] is True
