from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import factory_message_semantics as semantics
import factory_translation_guard as guard
import translation_quality_gate as quality_gate

try:
    import app
except ModuleNotFoundError as exc:  # Pure semantic tests still run in minimal CI.
    app = None
    APP_IMPORT_ERROR = exc
else:
    APP_IMPORT_ERROR = None


ROOT = Path(__file__).resolve().parent

CUSTOMER_SOURCE = (
    "今天剩柏緯、上銀、津展。"
    "包裝系統備註遞延料再幫忙包裝入庫。"
)
CUSTOMER_TARGET = (
    "Hari ini hanya tersisa pesanan untuk 柏緯, 上銀, dan 津展. "
    "Untuk material yang ditandai tertunda di sistem packaging, "
    "mohon bantu kemas lalu masukkan ke gudang."
)
CUSTOMER_SCREENSHOT_BAD = (
    "Hari ini masih tersisa 柏緯、上銀、津展. Untuk material yang ditunda "
    "sesuai catatan sistem pengemasan, mohon bantu kemas dan masukkan ke gudang."
)

OIL_SOURCE = "i19 minyak mesin menetes"
OIL_TARGET = "I19 機台漏油"
OIL_SCREENSHOT_BAD = "I19 機油滴漏"

FLOW_SOURCE = (
    "下午急單差不多後，這份上面的遞延料幫忙安排處理一下，"
    "四把在包裝，三把會陸續拋光過去"
)
FLOW_TARGET = (
    "Setelah work order mendesak sore ini hampir selesai, mohon atur "
    "penanganan material tertunda yang tercantum di atas. Empat bundel "
    "berada di bagian packaging. Tiga bundel akan dikirim secara bertahap "
    "ke bagian polishing."
)
FLOW_SCREENSHOT_BAD = (
    "Setelah work order mendesak sore hari hampir selesai, tolong atur "
    "penanganan material tertunda yang tercantum di atas. 4 bundel sedang "
    "di bagian packaging, 3 bundel akan dipoles dan dikirim bertahap."
)

SHORT_SOURCE = "沒短尺亂維護"
SHORT_TARGET = (
    "Tidak ada material pendek, tetapi penanganan material pendek malah "
    "dilakukan sembarangan."
)
SHORT_SCREENSHOT_BAD = (
    "Tidak ada batang pendek, jangan melakukan maintenance sembarangan."
)

TRASH_SOURCE = "Orang malem tida membuang sampah"
TRASH_TARGET = "晚班人員沒有倒垃圾"

DRINK_SOURCE = "喝完亂丟"
DRINK_TARGET = "Setelah diminum, malah dibuang sembarangan."
DRINK_SCREENSHOT_BAD = "Setelah minum, jangan buang sembarangan."

MES_SOURCE = (
    "@All 各站優先生產本月份訂單，藍底特別注意。\n"
    "今天五點後MES系統中止服務，所有的異動資料都在四點半左右完成。\n"
    "包裝出貨急單再麻煩優先處理。\n"
    "異型站的料幫忙分流過來"
)
MES_TARGET = (
    "@All Semua stasiun harus memprioritaskan produksi pesanan bulan ini; "
    "pesanan berlatar biru perlu mendapat perhatian khusus. Hari ini, "
    "setelah pukul 5.00, sistem MES akan berhenti beroperasi. Semua perubahan "
    "data harus diselesaikan sekitar pukul 4.30. Mohon prioritaskan work order "
    "mendesak untuk packaging dan pengiriman. Mohon alihkan material dari "
    "Stasiun packing barang bentuk khusus ke sini."
)
MES_SCREENSHOT_TARGET = (
    "@All Semua stasiun prioritaskan produksi pesanan bulan ini, yang berlatar "
    "biru harap diperhatikan khusus. Hari ini setelah pukul lima, sistem MES "
    "akan berhenti beroperasi. Semua data perubahan harap diselesaikan sekitar "
    "pukul empat setengah. Untuk work order mendesak bagian packing dan "
    "pengiriman, mohon diprioritaskan lagi. Material dari Stasiun packing "
    "barang bentuk khusus mohon dialihkan ke sini."
)
MES_BAD = (
    "@All Semua stasiun prioritaskan pesanan bulan ini. Sistem MES berhenti "
    "pukul 4.30. Mohon prioritaskan bagian packing."
)


SCREENSHOT_CASES = (
    (CUSTOMER_SOURCE, "zh", "id", "zh_id_remaining_customer_orders", CUSTOMER_TARGET),
    (OIL_SOURCE, "id", "zh", "id_zh_machine_oil_leak", OIL_TARGET),
    (FLOW_SOURCE, "zh", "id", "zh_id_deferred_material_process_flow", FLOW_TARGET),
    (SHORT_SOURCE, "zh", "id", "zh_id_careless_action_speech_act", SHORT_TARGET),
    (TRASH_SOURCE, "id", "zh", "id_zh_night_shift_trash_omission", TRASH_TARGET),
    (DRINK_SOURCE, "zh", "id", "zh_id_careless_action_speech_act", DRINK_TARGET),
    (MES_SOURCE, "zh", "id", "zh_id_mes_operational_notice", MES_TARGET),
)


def test_every_reported_source_is_parsed_and_rebuilt_from_roles():
    for source, src, tgt, kind, expected in SCREENSHOT_CASES:
        frame = semantics.build_frame(source, src, tgt)
        assert frame["active"] is True, source
        assert frame["complete"] is True, (source, frame["unparsed"])
        assert frame["kind"] == kind
        assert semantics.translate_source_directly(source, src, tgt) == expected
        assert semantics.validate_translation(frame, expected) == (True, [])


def test_reported_semantic_errors_are_rejected_by_relation_not_word_presence():
    cases = (
        (CUSTOMER_SOURCE, "zh", "id", CUSTOMER_SCREENSHOT_BAD, "customer_order_metonymy_missing"),
        (OIL_SOURCE, "id", "zh", OIL_SCREENSHOT_BAD, "oil_leak_machine_actor_missing"),
        (FLOW_SOURCE, "zh", "id", FLOW_SCREENSHOT_BAD, "process_destination_relation_missing"),
        (SHORT_SOURCE, "zh", "id", SHORT_SCREENSHOT_BAD, "statement_changed_to_prohibition"),
        (DRINK_SOURCE, "zh", "id", DRINK_SCREENSHOT_BAD, "statement_changed_to_prohibition"),
        (MES_SOURCE, "zh", "id", MES_BAD, "mes_stop_time_relation_missing"),
    )
    for source, src, tgt, bad, issue_suffix in cases:
        frame = semantics.build_frame(source, src, tgt)
        ok, issues = semantics.validate_translation(frame, bad)
        assert ok is False, (source, issues)
        assert any(item.endswith(issue_suffix) for item in issues), issues


def test_process_names_are_locations_when_directional_complement_is_present():
    source = (
        "下午急單快完成後，上述遞延材料請幫忙安排處理一下，"
        "兩捆目前在研磨，五捆將分批削皮送過去"
    )
    expected = (
        "Setelah work order mendesak sore ini hampir selesai, mohon atur "
        "penanganan material tertunda yang tercantum di atas. Dua bundel "
        "berada di bagian grinding. Lima bundel akan dikirim secara bertahap "
        "ke Bagian Peeling."
    )
    frame = semantics.build_frame(source, "zh", "id")

    assert frame["complete"] is True, frame
    assert frame["slots"]["current_count"] == 2
    assert frame["slots"]["destination_count"] == 5
    assert frame["slots"]["current_process_id"] == "bagian grinding"
    assert frame["slots"]["destination_process_id"] == "Bagian Peeling"
    assert semantics.translate_source_directly(source, "zh", "id") == expected
    assert semantics.validate_translation(frame, expected) == (True, [])


def test_customer_identifiers_are_dynamic_and_never_transliterated():
    source = (
        "今日只剩ABC-01、GS METAL、B&B。"
        "包裝系統註記遞延材料請幫忙包裝後入庫。"
    )
    expected = (
        "Hari ini hanya tersisa pesanan untuk ABC-01, GS METAL, dan B&B. "
        "Untuk material yang ditandai tertunda di sistem packaging, "
        "mohon bantu kemas lalu masukkan ke gudang."
    )
    frame = semantics.build_frame(source, "zh", "id")

    assert frame["slots"]["customer_names"] == ["ABC-01", "GS METAL", "B&B"]
    assert semantics.translate_source_directly(source, "zh", "id") == expected


def test_colloquial_indonesian_keeps_actor_negation_and_equipment_relation():
    cases = (
        ("BF235 oli bocor", "BF235 機台漏油"),
        ("Karyawan shift malam belum buang sampah", "晚班人員還沒倒垃圾"),
    )
    for source, expected in cases:
        frame = semantics.build_frame(source, "id-ID", "zh-TW")
        assert frame["complete"] is True, frame
        assert semantics.translate_source_directly(source, "id-ID", "zh-TW") == expected

    normalized, replacements = semantics.normalize_indonesian_factory_colloquialisms(
        "Orang malem tida membuang sampah"
    )
    assert normalized == "Orang malam tidak membuang sampah"
    assert replacements == 2


def test_statement_and_prohibition_are_distinct_source_speech_acts():
    complaint = semantics.build_frame(DRINK_SOURCE, "zh", "id")
    prohibition_source = "喝完不要亂丟"
    prohibition = semantics.build_frame(prohibition_source, "zh", "id")

    assert complaint["slots"]["modality"] == "observed_complaint"
    assert prohibition["slots"]["modality"] == "prohibition"
    assert semantics.validate_translation(complaint, DRINK_SCREENSHOT_BAD)[0] is False
    assert semantics.translate_source_directly(prohibition_source, "zh", "id") == (
        "Setelah minum, jangan dibuang sembarangan."
    )
    assert semantics.validate_translation(
        prohibition, "Setelah diminum, malah dibuang sembarangan."
    )[0] is False


def test_mes_times_and_all_operational_clauses_are_parameterized():
    source = (
        "@All 各站優先生產本月訂單，藍底訂單要特別注意。"
        "今日六點半後MES系統停止服務，所有變更資料都在五點45分前完成。"
        "包裝出貨急單請優先處理。異型站材料請幫忙調撥過來"
    )
    frame = semantics.build_frame(source, "zh", "id")
    target = semantics.translate_source_directly(source, "zh", "id")

    assert frame["complete"] is True, frame
    assert frame["slots"]["mes_stop_time"] == "6.30"
    assert frame["slots"]["change_data_deadline_time"] == "5.45"
    assert "pukul 6.30" in target
    assert "pukul 5.45" in target
    assert semantics.validate_translation(frame, target) == (True, [])


def test_values_cannot_cross_satisfy_the_wrong_relation_or_clause():
    customer_frame = semantics.build_frame(CUSTOMER_SOURCE, "zh", "id")
    customer_reversed = (
        "Hari ini hanya tersisa pesanan untuk 柏緯, 上銀, dan 津展. "
        "Untuk material yang ditandai tertunda di sistem packaging, mohon "
        "masukkan ke gudang lalu kemas."
    )
    ok, issues = semantics.validate_translation(customer_frame, customer_reversed)
    assert ok is False
    assert "factory_message_semantics:package_warehouse_sequence_reversed" in issues

    flow_frame = semantics.build_frame(FLOW_SOURCE, "zh", "id")
    swapped_counts = (
        "Setelah work order mendesak sore ini hampir selesai, mohon atur "
        "penanganan material tertunda yang tercantum di atas. Tiga bundel berada "
        "di bagian packaging. Empat bundel akan dikirim secara bertahap ke "
        "bagian polishing."
    )
    ok, issues = semantics.validate_translation(flow_frame, swapped_counts)
    assert ok is False
    assert "factory_message_semantics:current_count_process_relation_missing" in issues
    assert "factory_message_semantics:destination_count_process_relation_missing" in issues

    mes_frame = semantics.build_frame(MES_SOURCE, "zh", "id")
    swapped_times = (
        "@All Semua stasiun harus memprioritaskan produksi pesanan bulan ini; "
        "pesanan berlatar biru perlu mendapat perhatian khusus. Hari ini, setelah "
        "pukul 4.30, sistem MES akan berhenti beroperasi. Semua perubahan data "
        "harus diselesaikan sekitar pukul 5.00. Mohon prioritaskan work order "
        "mendesak untuk packaging dan pengiriman. Mohon alihkan material dari "
        "Stasiun packing barang bentuk khusus ke sini."
    )
    ok, issues = semantics.validate_translation(mes_frame, swapped_times)
    assert ok is False
    assert "factory_message_semantics:mes_stop_time_relation_missing" in issues
    assert "factory_message_semantics:change_data_deadline_missing" in issues


def test_unrelated_extra_clause_never_disappears_through_direct_translation():
    for source, src, tgt, _kind, _expected in SCREENSHOT_CASES:
        extended = source + "。明天停機保養"
        frame = semantics.build_frame(extended, src, tgt)
        assert frame["active"] is True, source
        assert frame["complete"] is False, (source, frame)
        assert "明天停機保養" in frame["unparsed"]
        assert semantics.translate_source_directly(extended, src, tgt) == ""


def test_nonmatching_controls_do_not_trigger_destructive_rewrites():
    controls = (
        ("今天剩蘋果、香蕉，晚餐吃掉", "zh", "id"),
        ("I19 minyak mesin baru diganti", "id", "zh"),
        ("Orang malam membuang sampah", "id", "zh"),
        ("沒短尺所以不用維護", "zh", "id"),
        ("四把在包裝，三把已經拋光完成", "zh", "id"),
    )
    for source, src, tgt in controls:
        assert semantics.build_frame(source, src, tgt)["active"] is False, source


def test_both_shared_acceptance_boundaries_enforce_the_same_relations():
    guard.reload()
    bad_by_source = {
        CUSTOMER_SOURCE: CUSTOMER_SCREENSHOT_BAD,
        OIL_SOURCE: OIL_SCREENSHOT_BAD,
        FLOW_SOURCE: FLOW_SCREENSHOT_BAD,
        SHORT_SOURCE: SHORT_SCREENSHOT_BAD,
        DRINK_SOURCE: DRINK_SCREENSHOT_BAD,
        MES_SOURCE: MES_BAD,
    }
    for source, src, tgt, _kind, good in SCREENSHOT_CASES:
        good_quality = quality_gate.validate_translation(source, good, src, tgt)
        good_guard = guard.validate_translation(source, good, src, tgt)
        assert good_quality.ok, (source, good_quality.issues)
        assert good_guard.ok, (source, good_guard.issues)
        if source in bad_by_source:
            bad = bad_by_source[source]
            assert quality_gate.validate_translation(source, bad, src, tgt).ok is False
            assert guard.validate_translation(source, bad, src, tgt).ok is False

    # These two translations shown in the report are semantically correct and
    # must not become collateral damage from the stricter release.
    assert quality_gate.validate_translation(
        TRASH_SOURCE, TRASH_TARGET, "id", "zh"
    ).ok is True
    assert guard.validate_translation(TRASH_SOURCE, TRASH_TARGET, "id", "zh").ok is True
    assert quality_gate.validate_translation(
        MES_SOURCE, MES_SCREENSHOT_TARGET, "zh", "id"
    ).ok is True
    assert guard.validate_translation(
        MES_SOURCE, MES_SCREENSHOT_TARGET, "zh", "id"
    ).ok is True


def test_complete_frames_use_public_source_first_route_without_provider():
    if app is None:
        return
    cases = (
        (OIL_SOURCE, "id", "zh", OIL_TARGET),
        (FLOW_SOURCE, "zh", "id", FLOW_TARGET),
        (DRINK_SOURCE, "zh", "id", DRINK_TARGET),
    )
    with mock.patch.object(
        app,
        "_translate_inner",
        side_effect=AssertionError("provider must not run for a complete source frame"),
    ):
        for source, src, tgt, expected in cases:
            assert app.translate(source, src, tgt) == expected
            outcome = app._get_translation_outcome()
            assert outcome["reason"] == "source_first_relation_translation"


def test_deployment_contract_and_boot_self_test_are_release_locked():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    glossary = json.loads((ROOT / "glossary_data.json").read_text(encoding="utf-8"))

    assert 'VERSION = "v3.48.1-admin-js-embedding-root-fix-2026-08-31"' in app_source
    assert "_EXPECTED_FACTORY_MESSAGE_SEMANTICS_API_VERSION = 3" in app_source
    assert (
        '_EXPECTED_FACTORY_MESSAGE_SEMANTICS_BUILD_ID = '
        '"2026-08-31.1-operational-discourse-and-flow"'
    ) in app_source
    health = semantics.health()
    assert health["api_version"] == 3
    assert health["build_id"] == "2026-08-31.1-operational-discourse-and-flow"
    assert health["self_test"]["ok"] is True
    assert glossary["短尺維護"]["idn"] == "penanganan material pendek"
    assert glossary["短尺料"]["idn"] == "material pendek"
