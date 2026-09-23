"""Offline checks against transcriptions of the supplied work-order photos.

These strings are manually read from the photos, not the output of an online
OCR provider.  They test the operational decisions after source OCR.
"""

import json
from pathlib import Path

import pytest

from work_order_query import (_PACKAGING_DETAIL_EN, _PACKAGING_DETAIL_ID,
                              _PACKAGING_SHORT_EN, _PACKAGING_SHORT_ID,
                              build_work_order_reply, extract_work_order_info)


BASE = Path(__file__).resolve().parent
STORAGE = json.loads((BASE / "storage_data.json").read_text(encoding="utf-8"))
PACKAGING = json.loads((BASE / "packaging_data.json").read_text(encoding="utf-8"))


PHOTO_5 = """冷精棒製造指示書 Petunjuk produksi Cold Finished Bar
訂單編號：Y1223801-012
客戶名稱：方鉦
收貨人：方鉦
成品尺寸MIN：3.97
成品尺寸MAX：4
長度MIN：2500
長度MAX：2550
訂單流程：CHRAPDAEJL
噴漆位置：不噴
套環：N
顏色：N
包裝代碼：9G
特殊備註：
"""

PHOTO_4_CROP = """冷精棒製造指示書 Petunjuk produksi Cold Finished Bar
訂單編號：Y1223955-015
客戶名稱：望暉
收貨人：望暉
成品尺寸MIN：37.9
成品尺寸MAX：38
訂單流程：CHRAITEL
特殊備註：不要黑色套環（NO KONDOM）
"""


def test_photo_five_query_uses_live_tables_and_never_misreads_paint_color():
    info = extract_work_order_info(PHOTO_5, STORAGE, PACKAGING)
    assert info["is_work_order"]
    assert info["customer"] == "方鉦"
    assert info["storage"]["status"] == "unmapped_length"  # >=3200 in source table
    assert info["packaging"]["code"] == "9G"
    assert info["packaging"]["old_code"] == "C"
    assert info["packaging"]["detail"] == PACKAGING["9G"]["詳細包裝方式說明(冷精棒-設計)"]
    assert info["paint"] == {"status": "no", "color_code": None}
    assert info["ring"]["status"] == "no"
    reply = build_work_order_reply(PHOTO_5, STORAGE, PACKAGING)
    assert "不噴 / Tidak perlu dicat" in reply
    assert "顏色代碼" not in reply
    assert "9G" in reply and "kode lama C" in reply
    assert "Masukkan bundel kecil" in reply
    assert "Packaging method: Wooden crate and plastic wrapping film for small bundles" in reply
    assert "Packaging details: Place small bundles" in reply
    assert "儲區資料的長度條件未涵蓋" in reply
    assert "EH79" not in reply


def test_every_current_package_has_an_indonesian_short_method_without_ai():
    assert len(PACKAGING) == 24
    assert {entry["簡稱"] for entry in PACKAGING.values()} <= set(_PACKAGING_SHORT_ID)
    assert {entry["簡稱"] for entry in PACKAGING.values()} <= set(_PACKAGING_SHORT_EN)
    source_details = {entry["詳細包裝方式說明(冷精棒-設計)"] for entry in PACKAGING.values()}
    assert source_details <= set(_PACKAGING_DETAIL_ID)
    assert source_details <= set(_PACKAGING_DETAIL_EN)
    reply = build_work_order_reply(PHOTO_5, STORAGE, PACKAGING)
    assert "Peti kayu + plastik pembungkus untuk ikatan kecil" in reply
    assert "Rincian pengemasan: Masukkan bundel kecil" in reply
    assert "Rincian dalam bahasa Mandarin perlu diperiksa" not in reply


def test_exact_offline_packaging_translation_precedes_ai_callbacks():
    calls = []
    def fail_if_called(text):
        calls.append(text)
        raise AssertionError("Bundled methods should not trigger translation API")
    reply = build_work_order_reply(PHOTO_5, STORAGE, PACKAGING,
                                   translate_zh_to_id=fail_if_called,
                                   translate_zh_to_en=fail_if_called)
    assert not calls
    assert "Masukkan bundel kecil" in reply
    assert "Place small bundles" in reply


def test_new_admin_method_uses_callbacks_only_for_unmatched_content():
    method = "新規格包裝說明"
    custom = {"ZZ": {"品保設計(新版)": "ZZ", "簡稱": "新包裝", "詳細包裝方式說明(冷精棒-設計)": method}}
    text = PHOTO_5.replace("包裝代碼：9G", "包裝代碼：ZZ")
    calls = []
    def translate_id(source):
        calls.append(("id", source))
        return "Metode kemasan baru"
    def translate_en(source):
        calls.append(("en", source))
        return "New packing method"
    reply = build_work_order_reply(text, STORAGE, custom,
                                   translate_zh_to_id=translate_id,
                                   translate_zh_to_en=translate_en)
    assert calls == [("id", method), ("en", method), ("en", "新包裝")]
    assert "Rincian pengemasan: Metode kemasan baru" in reply
    assert "Packaging details: New packing method" in reply


def test_new_admin_method_callback_failure_asks_confirmation_without_guessing():
    custom = {"ZZ": {"品保設計(新版)": "ZZ", "簡稱": "陌生方式", "詳細包裝方式說明(冷精棒-設計)": "陌生包裝材料和步驟"}}
    text = PHOTO_5.replace("包裝代碼：9G", "包裝代碼：ZZ")
    reply = build_work_order_reply(text, STORAGE, custom,
                                   translate_zh_to_id=lambda _: None,
                                   translate_zh_to_en=lambda _: None)
    assert "包裝方式 / Ringkasan pengemasan：陌生方式 / Periksa istilah pada tabel" in reply
    assert "Rincian pengemasan: Periksa penjelasan asli" in reply
    assert "Packaging method: Confirm method from source table" in reply


def test_explicit_special_note_overrides_polishing_38mm_and_crop_stays_unknown():
    info = extract_work_order_info(PHOTO_4_CROP, STORAGE, PACKAGING)
    assert info["ring"] == {"status": "no", "reason": "explicit_note", "process": None}
    assert info["packaging"]["status"] == "missing"
    assert info["paint"]["status"] == "unknown"
    reply = build_work_order_reply(PHOTO_4_CROP, STORAGE, PACKAGING)
    assert "不套環（特殊備註）" in reply
    assert "包裝 / Pengemasan：代碼待確認" in reply
    assert "噴漆 / Pengecatan semprot：位置待確認" in reply
    assert "Protective ring: Not required (explicit order note)" in reply
    assert "Spray paint: Confirm position" in reply


@pytest.mark.parametrize(("flow", "size", "expected"), [
    ("CHRAPD", "19.99", "no"), ("CHRAPD", "20", "yes"),
    ("CHRAPL", "20", "yes"), ("CHRAPGL", "15.99", "no"),
    ("CHRAPGL", "16", "yes"), ("CHRAPGD", "30", "unknown"),
    ("CHRAZ", "30", "unknown"), ("L", "30", "unknown"),
])
def test_workflow_suffix_and_inclusive_thresholds(flow, size, expected):
    text = PHOTO_5.replace("CHRAPDAEJL", flow).replace("3.97", size).replace("成品尺寸MAX：4", "成品尺寸MAX：" + size)
    assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"]["status"] == expected


def test_dimension_straddling_threshold_and_bad_ocr_never_auto_choose():
    body = PHOTO_5.replace("CHRAPDAEJL", "CHRAPGL").replace("3.97", "15.99").replace("成品尺寸MAX：4", "成品尺寸MAX：16.01")
    assert extract_work_order_info(body, STORAGE, PACKAGING)["ring"]["status"] == "unknown"
    body = body.replace("成品尺寸MAX：16.01", "成品尺寸MAX：?")
    assert extract_work_order_info(body, STORAGE, PACKAGING)["ring"]["status"] == "unknown"


def test_storage_uses_length_interval_and_rejects_boundary_crossing():
    corrected = {"方鉦": [["<=3200", "EH79"], [">3200<=4200", "EH72"], [">4200", "EG38"]]}
    info = extract_work_order_info(PHOTO_5, corrected, PACKAGING)
    assert info["storage"] == {"status": "ok", "area": "EH79", "customer": "方鉦"}
    assert "儲區 / Area penyimpanan：EH79" in build_work_order_reply(PHOTO_5, corrected, PACKAGING)
    crosses = PHOTO_5.replace("長度MIN：2500", "長度MIN：3200").replace("長度MAX：2550", "長度MAX：3201")
    assert extract_work_order_info(crosses, corrected, PACKAGING)["storage"]["status"] == "ambiguous_mapping"


def test_paint_single_or_both_needs_visible_color_code_and_never_infers_meaning():
    text = PHOTO_5.replace("噴漆位置：不噴", "噴漆位置：雙邊").replace("顏色：N", "顏色：B12")
    info = extract_work_order_info(text, STORAGE, PACKAGING)
    assert info["paint"] == {"status": "both", "color_code": "B12"}
    reply = build_work_order_reply(text, STORAGE, PACKAGING)
    assert "雙邊 / Kedua ujung" in reply and "顏色代碼 / Kode warna：B12" in reply
    assert "顏色 / Warna：" not in reply
    text = text.replace("雙邊", "單邊").replace("顏色：B12", "顏色：?")
    reply = build_work_order_reply(text, STORAGE, PACKAGING)
    assert "單邊 / Satu ujung" in reply and "顏色代碼 / Kode warna：待確認" in reply


def test_legacy_code_with_multiple_methods_asks_for_new_code():
    lookup = {"1A": {"原包裝碼": "U", "品保設計(新版)": "1A", "簡稱": "A"},
              "2B": {"原包裝碼": "U", "品保設計(新版)": "2B", "簡稱": "B"}}
    text = PHOTO_5.replace("包裝代碼：9G", "包裝代碼：U")
    info = extract_work_order_info(text, STORAGE, lookup)
    assert info["packaging"]["status"] == "ambiguous"
    assert set(info["packaging"]["candidates"]) == {"1A", "2B"}
    reply = build_work_order_reply(text, STORAGE, lookup)
    assert "對應多種新版方式" in reply
    assert "包裝方式 / Ringkasan pengemasan" not in reply


def test_table_cells_stay_in_their_columns_and_customer_never_uses_recipient():
    table = """冷精棒製造指示書
訂單編號 / No.Pesan | 客戶名稱 / Nama Pelanggan | 收貨人 / Penerima Barang
Y1223801-012 | 方鉦 | 望暉
噴漆位置 | 套環 | 顏色 | 包裝代碼
不噴 | N | N | 9G
訂單流程 | 成品尺寸MIN | 成品尺寸MAX
CHRAPDAEJL | 3.97 | 4
"""
    info = extract_work_order_info(table, STORAGE, PACKAGING)
    assert info["customer"] == "方鉦"
    assert info["packaging"]["code"] == "9G"
    assert info["paint"]["status"] == "no"
    assert info["ring"]["status"] == "no"
    assert extract_work_order_info(table.replace("方鉦 | 望暉", "? | 望暉"), STORAGE, PACKAGING)["customer"] is None


def test_bilingual_headers_from_original_ocr_keep_field_and_value_together():
    text = """冷精棒製造指示書 Petunjuk produksi Cold Finished Bar
訂單編號 / No.Pesan | 客戶名稱 / Nama Pelanggan | 收貨人 / Penerima Barang
Y1223801-012 | 方鉦 | 望暉
訂單流程 / Alur Pemasangan | 噴漆位置 / Posisi semprot cat | 包裝代碼 / Kode kemasan
CHRAPDAEJL | 不噴 | 9G
成品尺寸MIN：3.97
成品尺寸MAX：4
"""
    info = extract_work_order_info(text, STORAGE, PACKAGING)
    assert info["fields"]["flow"] == "CHRAPDAEJL"
    assert info["ring"]["status"] == "no"
    assert info["paint"]["status"] == "no"
    assert info["packaging"]["code"] == "9G"


def test_special_note_next_line_and_form_n_does_not_override_size_rule():
    note = PHOTO_4_CROP.replace("特殊備註：不要黑色套環（NO KONDOM）", "特殊備註：\n不要黑色套環（NO KONDOM）")
    assert extract_work_order_info(note, STORAGE, PACKAGING)["ring"]["reason"] == "explicit_note"
    body = PHOTO_5.replace("CHRAPDAEJL", "CHRAPGL").replace("3.97", "25").replace("成品尺寸MAX：4", "成品尺寸MAX：25")
    assert extract_work_order_info(body, STORAGE, PACKAGING)["ring"]["status"] == "yes"
    order_note = body.replace("特殊備註：", "訂單備註：NO KONDOM")
    assert extract_work_order_info(order_note, STORAGE, PACKAGING)["ring"]["reason"] == "explicit_note"


def test_missing_work_order_marker_fails_closed():
    assert extract_work_order_info("客戶名稱：方鉦\n包裝代碼：9G", STORAGE, PACKAGING) == {"is_work_order": False}
