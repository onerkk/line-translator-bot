"""Checks that the LINE work-order card shows only requested bilingual facts."""

import json
from pathlib import Path

from linebot.v3.messaging import Message

from test_work_order_query import PHOTO_5
import work_order_card
from work_order_card import _ring_text, build_work_order_cards, build_work_order_fallback


BASE = Path(__file__).resolve().parent
STORAGE = json.loads((BASE / "storage_data.json").read_text(encoding="utf-8"))
PACKAGING = json.loads((BASE / "packaging_data.json").read_text(encoding="utf-8"))


PHOTO_SUNGE = """冷精棒製造指示書 Petunjuk produksi Cold Finished Bar
訂單編號：Y1224051-023
客戶名稱：SUNGE
收貨人：SUNGE
成品尺寸MIN：31.938
成品尺寸MAX：32
長度MIN：6000
長度MAX：6050
訂單流程：CHRAIPD
噴漆位置：不噴
套環：N
顏色：109
包裝代碼：1D
特殊備註：
"""


def _body_text(message):
    texts = []

    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "text":
                texts.append(value["text"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(message["contents"])
    return "\n".join(texts)


def _all_visible_text(result):
    return "\n".join([result["fallback_text"]] + [
        msg["altText"] + "\n" + _body_text(msg) for msg in result["messages"]
    ])


def _assert_only_requested_fields(result, source_order):
    visible = _all_visible_text(result)
    assert source_order not in visible
    for unwanted in ("訂單編號", "長度", "成品尺寸", "Panjang / Length", "Finished size",
                     "Customer", "Storage", "Spray paint", "Packaging", "Protective ring",
                     "ENGLISH", "English summary", "Order:", "Color:"):
        assert unwanted not in visible
    for message in result["messages"]:
        assert Message.from_dict(message).type == "flex"
        assert len(json.dumps(message["contents"], ensure_ascii=False).encode("utf-8")) < 30000
        assert len(message["altText"]) < 300


def test_photo_five_card_has_five_answers_and_full_bilingual_packaging_method():
    result = build_work_order_cards(PHOTO_5, STORAGE, PACKAGING)
    assert len(result["messages"]) == 1
    main_text = _body_text(result["messages"][0])
    for expected in ("方鉦", "EH79", "9G", "木箱+膠膜(小捆)",
                     "Peti kayu + plastik pembungkus untuk ikatan kecil",
                     "不噴 / Tidak dicat", "不需套環 / Tidak perlu cincin pelindung"):
        assert expected in main_text
    assert "包裝明細 / Rincian pengemasan" in main_text
    assert "木箱+小捆膠膜" in main_text
    assert "Masukkan bundel kecil" in main_text
    assert "Place small bundles" not in main_text
    assert "2500" not in _all_visible_text(result)
    assert "2550" not in _all_visible_text(result)
    assert "3.97" not in _all_visible_text(result)
    _assert_only_requested_fields(result, "Y1223801-012")


def test_sunge_photo_partial_customer_uses_canonical_storage_and_1d_legacy_g_method():
    result = build_work_order_cards(PHOTO_SUNGE, STORAGE, PACKAGING)
    assert len(result["messages"]) == 1
    summary = _body_text(result["messages"][0])
    assert "SUNGEUN" in summary and "EG33" in summary
    assert "1D（舊碼 G / kode lama G）" in summary
    assert "PC布墊 + 鋼帶 + PE布 + 膠膜兩層 + 2條棉繩" in summary
    assert "Bantalan kain PC + pita baja + kain PE + dua lapis film plastik + dua tali katun" in summary
    assert "原表簡稱與明細不一致" not in summary
    assert "3P袋+PE布" not in summary
    assert "不噴 / Tidak dicat" in summary
    assert "色碼 109" not in summary
    assert "不需套環 / Tidak perlu cincin pelindung" in summary
    assert "頭中尾內舖PC布墊" in summary
    assert "Letakkan bantalan kain PC" in summary
    for term in ("6000", "6050", "31.938", "CHRＡIPD", "CHRAIPD"):
        assert term not in _all_visible_text(result)
    _assert_only_requested_fields(result, "Y1224051-023")
    fallback = build_work_order_fallback(PHOTO_SUNGE, STORAGE, PACKAGING)
    assert "SUNGEUN" in fallback and "EG33" in fallback
    assert "1D(舊碼 G / kode lama G)" in fallback
    assert "原表簡稱與明細不一致" not in fallback
    assert "3P袋+PE布" not in fallback
    assert fallback == result["fallback_text"]


def test_unresolved_ring_summary_names_the_missing_rule_input_without_using_form_yn():
    cases = {
        "flow": "流程碼未辨識",
        "diameter": "成品規格未辨識",
        "invalid_diameter": "成品規格無效",
        "threshold_crossing": "規格範圍跨門檻",
    }
    for reason, expected in cases.items():
        text, color = _ring_text({"status": "unknown", "reason": reason})
        assert "套環待確認" in text
        assert expected in text
        assert "Perlu konfirmasi" in text
        assert color


def test_legacy_g_photo_resolves_to_same_1d_packaging_without_unsupported_bags():
    alias = PHOTO_SUNGE.replace("包裝代碼：1D", "包裝代碼：G")
    current = build_work_order_cards(PHOTO_SUNGE, STORAGE, PACKAGING)
    legacy = build_work_order_cards(alias, STORAGE, PACKAGING)
    assert _body_text(current["messages"][0]) == _body_text(legacy["messages"][0])
    assert legacy["fallback_text"] == current["fallback_text"]
    assert "1D（舊碼 G / kode lama G）" in _all_visible_text(legacy)


def test_1d_summary_requires_source_to_confirm_second_film_layer():
    changed = json.loads(json.dumps(PACKAGING, ensure_ascii=False))
    changed["1D"]["詳細包裝方式說明(冷精棒-設計)"] = changed["1D"][
        "詳細包裝方式說明(冷精棒-設計)"].replace("再捆一層膠膜後", "")
    result = build_work_order_cards(PHOTO_SUNGE, STORAGE, changed)
    visible = _all_visible_text(result)
    assert "膠膜兩層" not in visible
    assert "dua lapis film plastik" not in visible
    assert "3P袋+PE布" not in visible
    assert "包裝方式以原表明細為準" in visible


def test_storage_without_confirmed_area_stays_unconfirmed_without_showing_length():
    mapping = {"方鉦": [[">=3200", "EH79"]]}
    result = build_work_order_cards(PHOTO_5, mapping, PACKAGING)
    visible = _all_visible_text(result)
    assert "儲區待確認 / Gudang perlu diperiksa" in visible
    assert "儲區規則待核對 / Aturan gudang perlu diperiksa" in visible
    assert "EH79" not in visible
    assert "2500" not in visible and "長度" not in visible


def test_storage_pending_reason_is_specific_in_single_card_and_fallback():
    missing_read = PHOTO_5.replace("長度MIN：2500\n長度MAX：2550\n", "")
    scenarios = [
        ("unknown_length", missing_read, STORAGE,
         "工單資料未讀全 / Data pada surat kerja belum terbaca lengkap"),
        ("unknown_customer", PHOTO_5.replace("方鉦", "不存在客戶"), STORAGE,
         "客戶儲區資料待核對 / Data gudang pelanggan perlu diperiksa"),
        ("no_mapping", PHOTO_5, {"方鉦": []},
         "客戶儲區資料待核對 / Data gudang pelanggan perlu diperiksa"),
        ("invalid_mapping", PHOTO_5, {"方鉦": [["錯誤規則", "EH79"]]},
         "儲區規則待核對 / Aturan gudang perlu diperiksa"),
        ("ambiguous_mapping", PHOTO_5,
         {"方鉦": [["<=3200", "EH79"], ["<=3200", "EH80"]]},
         "儲區規則待核對 / Aturan gudang perlu diperiksa"),
    ]
    for status, image, mapping, reason in scenarios:
        assert work_order_card.extract_work_order_info(image, mapping, PACKAGING)["storage"]["status"] == status
        result = build_work_order_cards(image, mapping, PACKAGING)
        assert len(result["messages"]) == 1
        card = _body_text(result["messages"][0])
        assert "儲區待確認 / Gudang perlu diperiksa" in card
        assert reason in card
        assert reason in result["fallback_text"]
        assert "EH79" not in _all_visible_text(result)
        assert "長度" not in _all_visible_text(result) and "尺寸" not in _all_visible_text(result)
        assert "2500" not in _all_visible_text(result)
        _assert_only_requested_fields(result, "Y1223801-012")


def test_paint_color_only_when_paint_location_requires_it():
    no_paint = PHOTO_5.replace("顏色：N", "顏色：109")
    result = build_work_order_cards(no_paint, STORAGE, PACKAGING)
    main = _body_text(result["messages"][0])
    assert "色碼" not in main and "hitam" not in main
    painted = no_paint.replace("噴漆位置：不噴", "噴漆位置：雙邊")
    main = _body_text(build_work_order_cards(painted, STORAGE, PACKAGING)["messages"][0])
    assert "雙邊 / Kedua sisi" in main
    assert "色碼 109 · 黑 / hitam" in main
    assert "black" not in main
    unknown = painted.replace("顏色：109", "顏色：X9")
    main = _body_text(build_work_order_cards(unknown, STORAGE, PACKAGING)["messages"][0])
    assert "X9 · 顏色待核對" in main
    assert "hitam" not in main


def test_printed_chinese_paint_name_displays_verified_rack_code_or_unknown_name():
    painted = PHOTO_5.replace("噴漆位置：不噴", "噴漆位置：雙邊")
    painted = painted.replace("顏色：N", "顏色：土藍")
    result = build_work_order_cards(painted, STORAGE, PACKAGING)
    assert len(result["messages"]) == 1
    visible = _all_visible_text(result)
    assert "色碼 46 · 土藍 / biru bernuansa tanah" in visible
    assert "色碼 土藍" not in visible
    assert "顏色待核對" not in visible
    assert "色碼 46" in build_work_order_fallback(painted, STORAGE, PACKAGING)
    unknown = build_work_order_cards(painted, STORAGE, PACKAGING, paint_codes={})
    assert "顏色 土藍 · 色碼待確認" in _all_visible_text(unknown)
    assert "色碼 土藍" not in _all_visible_text(unknown)


def test_special_note_no_ring_and_unknown_packaging_without_fabrication():
    image = PHOTO_5.replace("包裝代碼：9G", "包裝代碼：ZZ")
    image = image.replace("特殊備註：", "特殊備註：NO KONDOM")
    result = build_work_order_cards(image, STORAGE, PACKAGING)
    assert len(result["messages"]) == 1
    main = _body_text(result["messages"][0])
    assert "依備註不套環" in main
    assert "ZZ · 查無包裝資料" in main
    assert "木箱+膠膜(小捆)" not in main


def test_new_admin_method_translates_only_into_indonesian():
    detail = "材質新方案"
    custom = {"ZZ": {"品保設計(新版)": "ZZ", "簡稱": "新包裝",
                     "詳細包裝方式說明(冷精棒-設計)": detail}}
    image = PHOTO_5.replace("包裝代碼：9G", "包裝代碼：ZZ")
    called = []

    def translate_id(value):
        called.append(("id", value))
        return "Metode " + value

    def translate_en(_value):
        raise AssertionError("English translator must not be called")

    result = build_work_order_cards(image, STORAGE, custom,
                                    translate_zh_to_id=translate_id,
                                    translate_zh_to_en=translate_en)
    assert called == [("id", "新包裝"), ("id", detail)]
    assert len(result["messages"]) == 1
    assert "Metode " + detail in _body_text(result["messages"][0])
    assert "Method " not in _all_visible_text(result)
    assert "翻譯待核對" in _body_text(build_work_order_cards(image, STORAGE, custom)["messages"][0])


def test_long_untrusted_detail_bounded_and_detail_failure_keeps_one_card(monkeypatch):
    very_long = "進箱\u202e\x00" + "甲" * 12000
    custom = {"ZZ": {"品保設計(新版)": "ZZ", "簡稱": "新包裝",
                     "詳細包裝方式說明(冷精棒-設計)": very_long}}
    image = PHOTO_5.replace("包裝代碼：9G", "包裝代碼：ZZ")
    result = build_work_order_cards(image, STORAGE, custom)
    assert len(result["messages"]) == 1
    assert "\u202e" not in _all_visible_text(result) and "\x00" not in _all_visible_text(result)
    assert _body_text(result["messages"][0]).count("甲") <= 1350
    _assert_only_requested_fields(result, "Y1223801-012")

    def broken_detail(*_args, **_kwargs):
        raise RuntimeError("corrupt admin description")

    monkeypatch.setattr(work_order_card, "_package_detail_rows", broken_detail)
    result = build_work_order_cards(PHOTO_5, STORAGE, PACKAGING)
    assert len(result["messages"]) == 1
    assert "方鉦" in _body_text(result["messages"][0])
    assert "Masukkan bundel kecil" in result["fallback_text"]
    assert "長度" not in result["fallback_text"]


def test_non_work_order_has_no_fabricated_card_or_english_fallback():
    result = build_work_order_cards("早安", STORAGE, PACKAGING)
    assert result["messages"] == []
    assert "無法確認是工單" in result["fallback_text"]
    assert "work order" not in result["fallback_text"].lower()
    assert result["fallback_text"] == build_work_order_fallback("早安", STORAGE, PACKAGING)
