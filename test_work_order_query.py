"""Offline checks against transcriptions of the supplied work-order photos.

These strings are manually read from the photos, not the output of an online
OCR provider.  They test the operational decisions after source OCR.
"""

import json
from pathlib import Path

import pytest

from work_order_query import (_PACKAGING_DETAIL_EN, _PACKAGING_DETAIL_ID,
                              _PAINT_CODES,
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
    assert info["storage"] == {"status": "ok", "area": "EH79", "customer": "方鉦"}
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
    assert "儲區 / Area penyimpanan：EH79" in reply


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


@pytest.mark.parametrize("code", ["1D", "G"])
def test_1d_and_g_legacy_work_order_formatter_use_same_verified_method(code):
    image = PHOTO_5.replace("包裝代碼：9G", "包裝代碼：" + code)
    info = extract_work_order_info(image, STORAGE, PACKAGING)
    assert info["packaging"]["code"] == "1D"
    assert info["packaging"]["old_code"] == "G"
    concise = "PC布墊+鋼帶+PE布+膠膜兩層+2條棉繩"
    assert info["packaging"]["short"] == concise
    reply = build_work_order_reply(image, STORAGE, PACKAGING)
    assert "包裝代碼 / Kode kemasan：1D（舊碼 / kode lama G）" in reply
    assert "包裝方式 / Ringkasan pengemasan：" + concise in reply
    assert "Bantalan kain PC + pita baja + kain PE + dua lapis film plastik + dua tali katun" in reply
    assert "原表說明：" + PACKAGING["1D"]["詳細包裝方式說明(冷精棒-設計)"] in reply
    assert "3P袋+PE布" not in reply


@pytest.mark.parametrize("read_code", ["1O", "10", "１Ｏ", "１0"])
def test_photo_dacapo_ocr_zero_or_letter_o_resolves_unique_verified_1o(read_code):
    image = PHOTO_5.replace("包裝代碼：9G", "包裝代碼：" + read_code)
    info = extract_work_order_info(image, STORAGE, PACKAGING)
    package = info["packaging"]
    assert package["status"] == "ok"
    assert package["code"] == "1O"
    assert package["old_code"] == "7"
    assert package["short"] == PACKAGING["1O"]["簡稱"]
    assert package["detail"] == PACKAGING["1O"]["詳細包裝方式說明(冷精棒-設計)"]
    reply = build_work_order_reply(image, STORAGE, PACKAGING)
    assert "包裝代碼 / Kode kemasan：1O（舊碼 / kode lama 7）" in reply
    assert "查無包裝資料" not in reply


def test_photo_packaging_ocr_correction_preserves_exact_10_and_missing_or_conflicting_source():
    image = PHOTO_5.replace("包裝代碼：9G", "包裝代碼：10")
    exact = dict(PACKAGING)
    exact["10"] = {"品保設計(新版)": "10", "簡稱": "另一種獨立包裝"}
    result = extract_work_order_info(image, STORAGE, exact)["packaging"]
    assert result["status"] == "ok"
    assert result["code"] == "10"
    assert result["short"] == "另一種獨立包裝"

    no_reference = dict(PACKAGING)
    del no_reference["1O"]
    assert extract_work_order_info(image, STORAGE, no_reference)["packaging"]["status"] == "not_found"

    conflict = dict(PACKAGING)
    conflict["OTHER"] = {"品保設計(新版)": "1O", "簡稱": "不同包裝"}
    assert extract_work_order_info(image, STORAGE, conflict)["packaging"]["status"] == "not_found"

    mislabeled = dict(PACKAGING)
    mislabeled["1O"] = {"品保設計(新版)": "1Q", "簡稱": "資料表欄位不一致"}
    assert extract_work_order_info(image, STORAGE, mislabeled)["packaging"]["status"] == "not_found"

    # A hand-entered /pkg 10 remains an exact query; only photographed work
    # orders receive this verified, narrowly scoped OCR correction.
    from packaging_lookup import find_packaging_matches
    assert find_packaging_matches("10", PACKAGING) == []


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
    assert "不套環（工單備註）" in reply
    assert "包裝 / Pengemasan：代碼待確認" in reply
    assert "噴漆 / Pengecatan semprot：位置待確認" in reply
    assert "Protective ring: Not required (explicit order note)" in reply
    assert "Spray paint: Confirm position" in reply


@pytest.mark.parametrize(("flow", "size", "expected"), [
    ("CHRAPD", "19.99", "no"), ("CHRAPD", "20", "no"),
    ("CHRAPD", "99", "no"), ("CHRAPGD", "30", "no"),
    ("CHRAPL", "20", "yes"), ("CHRAPGL", "15.99", "no"),
    ("CHRAPGL", "16", "yes"), ("CHRAPL", "19.99", "no"),
    ("CHRAZ", "30", "unknown"), ("L", "30", "unknown"),
    ("D", "30", "no"),
])
def test_workflow_suffix_and_inclusive_thresholds(flow, size, expected):
    text = PHOTO_5.replace("CHRAPDAEJL", flow).replace("3.97", size).replace("成品尺寸MAX：4", "成品尺寸MAX：" + size)
    assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"]["status"] == expected


def test_packaging_material_suffix_d_never_needs_ring_even_without_a_size():
    text = PHOTO_5.replace("CHRAPDAEJL", "CHRAPGD").replace("成品尺寸MIN：3.97", "成品尺寸MIN：?").replace("成品尺寸MAX：4", "成品尺寸MAX：?")
    assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"] == {
        "status": "no", "reason": "packaging_material", "process": "packaging"}


@pytest.mark.parametrize("flow", ("CHRAPL", "CHRAPGL"))
def test_unusual_three_decimal_size_can_still_resolve_ring_safely(flow):
    text = PHOTO_5.replace("CHRAPDAEJL", flow).replace("成品尺寸MIN：3.97", "成品尺寸MIN：31.938").replace("成品尺寸MAX：4", "成品尺寸MAX：32")
    assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"]["status"] == "yes"


def test_dimension_straddling_threshold_and_bad_ocr_never_auto_choose():
    body = PHOTO_5.replace("CHRAPDAEJL", "CHRAPGL").replace("3.97", "15.99").replace("成品尺寸MAX：4", "成品尺寸MAX：16.01")
    assert extract_work_order_info(body, STORAGE, PACKAGING)["ring"]["status"] == "unknown"
    body = body.replace("成品尺寸MAX：16.01", "成品尺寸MAX：?")
    assert extract_work_order_info(body, STORAGE, PACKAGING)["ring"]["status"] == "unknown"


def test_decimal_comma_size_does_not_become_a_hundredfold_larger_ring_size():
    text = PHOTO_5.replace("成品尺寸MIN：3.97", "成品尺寸MIN：3,97")
    text = text.replace("成品尺寸MAX：4", "成品尺寸MAX：4,00")
    info = extract_work_order_info(text.replace("CHRAPDAEJL", "CHRAPL"), STORAGE, PACKAGING)
    assert tuple(str(value) for value in info["diameter"]) == ("3.97", "4.00")
    assert info["ring"]["status"] == "no"


def test_ambiguous_grouping_punctuation_does_not_select_the_wrong_storage_or_ring():
    text = PHOTO_5.replace("長度MIN：2500", "長度MIN：4.200")
    text = text.replace("長度MAX：2550", "長度MAX：4.250")
    info = extract_work_order_info(text, STORAGE, PACKAGING)
    assert info["length"] is None
    assert info["storage"]["status"] == "unknown_length"
    assert "儲區 / Area penyimpanan：EH79" not in build_work_order_reply(text, STORAGE, PACKAGING)
    polishing = PHOTO_5.replace("CHRAPDAEJL", "CHRAPL")
    polishing = polishing.replace("成品尺寸MIN：3.97", "成品尺寸MIN：20.000")
    polishing = polishing.replace("成品尺寸MAX：4", "成品尺寸MAX：20.001")
    assert extract_work_order_info(polishing, STORAGE, PACKAGING)["ring"]["status"] == "yes"
    # One possible reading lies below the threshold and another above it.
    near_threshold = polishing.replace("20.000", "19.999").replace("20.001", "20.001")
    assert extract_work_order_info(near_threshold, STORAGE, PACKAGING)["ring"]["status"] == "unknown"


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
    assert "雙邊 / Kedua sisi" in reply and "顏色代碼 / Kode warna：B12" in reply
    assert "顏色 / Warna：" not in reply
    text = text.replace("雙邊", "單邊").replace("顏色：B12", "顏色：?")
    reply = build_work_order_reply(text, STORAGE, PACKAGING)
    assert "單邊 / Satu sisi" in reply and "顏色代碼 / Kode warna：待確認" in reply


def test_verified_paint_codes_match_all_supplied_cans():
    # Expected original names and numbers are read from the user's photos.
    assert {code: entry["zh"] for code, entry in _PAINT_CODES.items()} == {
        "46": "土藍", "101": "紅", "102": "白", "108": "黃",
        "109": "黑", "113": "桃紅", "115": "橘紅", "116": "青",
        "137": "軍綠", "144": "茶霧面", "169": "黃綠",
    }
    assert all(entry.get("id") and entry.get("en") for entry in _PAINT_CODES.values())
    # The can says 青; its appearance does not verify blue or green.
    assert _PAINT_CODES["116"]["id"] == _PAINT_CODES["116"]["en"] == "qing (青)"
    assert "tea" in _PAINT_CODES["144"]["en"]
    assert "matte" in _PAINT_CODES["144"]["en"]


def test_printed_paint_name_uses_unique_verified_rack_code_not_the_name_as_code():
    painted = PHOTO_5.replace("噴漆位置：不噴", "噴漆位置：雙邊")
    painted = painted.replace("顏色：N", "顏色：土藍")
    assert extract_work_order_info(painted, STORAGE, PACKAGING)["paint"] == {
        "status": "both", "color_code": "46", "color_name": "土藍"}
    reply = build_work_order_reply(painted, STORAGE, PACKAGING)
    assert "顏色代碼 / Kode warna：46" in reply
    assert "顏色代碼 / Kode warna：土藍" not in reply
    no_spray = painted.replace("噴漆位置：雙邊", "噴漆位置：不噴")
    assert extract_work_order_info(no_spray, STORAGE, PACKAGING)["paint"] == {
        "status": "no", "color_code": None}
    assert "顏色代碼 / Kode warna：" not in build_work_order_reply(no_spray, STORAGE, PACKAGING)


def test_paint_name_reverse_lookup_does_not_guess_unknown_or_duplicate_code():
    painted = PHOTO_5.replace("噴漆位置：不噴", "噴漆位置：單邊")
    painted = painted.replace("顏色：N", "顏色：土藍")
    assert extract_work_order_info(painted, STORAGE, PACKAGING, paint_codes={})["paint"] == {
        "status": "one", "color_code": None, "color_name": "土藍"}
    duplicate = {
        "46": {"zh": "土藍", "id": "biru tanah"},
        "47": {"zh": "土藍", "id": "biru tanah"},
    }
    assert extract_work_order_info(painted, STORAGE, PACKAGING, paint_codes=duplicate)["paint"] == {
        "status": "one", "color_code": None, "color_name": "土藍"}
    assert extract_work_order_info(painted.replace("土藍", "土青"), STORAGE, PACKAGING)["paint"] == {
        "status": "one", "color_code": None, "color_name": "土青"}


@pytest.mark.parametrize(("side", "indonesian", "english"), [
    ("單邊", "Satu sisi", "One side"),
    ("雙邊", "Kedua sisi", "Both sides"),
])
def test_known_code_from_final_photo_translates_only_when_painted(side, indonesian, english):
    text = PHOTO_5.replace("噴漆位置：不噴", "噴漆位置：" + side).replace("顏色：N", "顏色：１０９")
    reply = build_work_order_reply(text, STORAGE, PACKAGING)  # No explicit mapping from the LINE app.
    assert f"噴漆 / Pengecatan semprot：{side} / {indonesian}" in reply
    assert "顏色代碼 / Kode warna：109" in reply
    assert "顏色 / Warna：黑 / hitam" in reply
    assert f"Spray paint: {english}" in reply
    assert "Color code: 109" in reply and "Color: black" in reply


def test_no_spray_disregards_a_known_color_code_and_an_unknown_code_stays_raw():
    no_spray = PHOTO_5.replace("顏色：N", "顏色：109")
    reply = build_work_order_reply(no_spray, STORAGE, PACKAGING)
    assert "噴漆 / Pengecatan semprot：不噴 / Tidak perlu dicat" in reply
    assert "Spray paint: None" in reply
    assert "顏色代碼 / Kode warna：" not in reply
    assert "顏色 / Warna：" not in reply
    assert "Color code:" not in reply and "Color:" not in reply
    unknown = no_spray.replace("噴漆位置：不噴", "噴漆位置：單邊").replace("顏色：109", "顏色：109 黑色")
    reply = build_work_order_reply(unknown, STORAGE, PACKAGING)
    assert "顏色代碼 / Kode warna：待確認" in reply
    assert "顏色 / Warna：" not in reply and "Color:" not in reply


def test_table_cells_and_explicit_override_keep_paint_lookup_source_bound():
    table = """冷精棒製造指示書
訂單編號 / No.Pesan | 客戶名稱 / Nama Pelanggan | 收貨人 / Penerima Barang
Y1223801-012 | 方鉦 | 方鉦
噴漆位置 | 套環 | 顏色 | 包裝代碼
雙邊 | N | 109 | 9G
訂單流程 | 成品尺寸MIN | 成品尺寸MAX
CHRAPDAEJL | 3.97 | 4
"""
    info = extract_work_order_info(table, STORAGE, PACKAGING)
    assert info["paint"] == {"status": "both", "color_code": "109"}
    assert "顏色 / Warna：黑 / hitam" in build_work_order_reply(table, STORAGE, PACKAGING)
    assert "顏色 / Warna：" not in build_work_order_reply(table, STORAGE, PACKAGING, paint_codes={})


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


@pytest.mark.parametrize("header", [
    "長度MIN Panjang MIN | MAX",
    "長度MIN Panjang MIN | MAX | 計劃量",
])
def test_adjacent_bilingual_length_headers_keep_min_and_max_in_their_cells(header):
    text = PHOTO_5.replace("長度MIN：2500\n長度MAX：2550\n", "")
    values = "2500 | 2550" + (" | 506.00" if "計劃量" in header else "")
    text += header + "\n" + values + "\n短尺 MIN | 2000\n"
    info = extract_work_order_info(text, STORAGE, PACKAGING)
    assert info["fields"]["length_min"] == "2500"
    assert info["fields"]["length_max"] == "2550"
    assert str(info["length"][0]) == "2500"
    assert str(info["length"][1]) == "2550"


def test_isolated_max_without_an_adjacent_length_header_does_not_become_length():
    text = PHOTO_5.replace("長度MIN：2500\n長度MAX：2550\n", "")
    text += "成品尺寸MIN | MAX | 短邊MIN | MAX\n3.97 | 4 | 2000 | 2500\n"
    info = extract_work_order_info(text, STORAGE, PACKAGING)
    assert info["fields"]["length_min"] is None
    assert info["fields"]["length_max"] is None
    assert info["length"] is None


def test_unreadable_length_max_preserves_the_read_min_without_resolving_storage():
    text = PHOTO_5.replace("長度MAX：2550", "長度MAX：?")
    info = extract_work_order_info(text, STORAGE, PACKAGING)
    assert info["fields"]["length_min"] == "2500"
    assert info["fields"]["length_max"] is None
    assert info["length"] is None
    assert info["storage"]["status"] == "unknown_length"


def test_special_note_next_line_and_printed_n_does_not_override_size_rule():
    note = PHOTO_4_CROP.replace("特殊備註：不要黑色套環（NO KONDOM）", "特殊備註：\n不要黑色套環（NO KONDOM）")
    assert extract_work_order_info(note, STORAGE, PACKAGING)["ring"]["reason"] == "explicit_note"
    body = PHOTO_5.replace("CHRAPDAEJL", "CHRAPGL").replace("3.97", "25").replace("成品尺寸MAX：4", "成品尺寸MAX：25")
    assert extract_work_order_info(body, STORAGE, PACKAGING)["ring"] == {
        "status": "yes", "reason": "size_rule", "process": "grinding", "threshold": 16}
    order_note = body.replace("特殊備註：", "訂單備註：NO KONDOM")
    assert extract_work_order_info(order_note, STORAGE, PACKAGING)["ring"]["reason"] == "explicit_note"


def test_missing_work_order_marker_fails_closed():
    assert extract_work_order_info("客戶名稱：方鉦\n包裝代碼：9G", STORAGE, PACKAGING) == {"is_work_order": False}
