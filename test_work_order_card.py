"""The operational card stays legible while preserving source-based decisions."""

import json
from pathlib import Path

from linebot.v3.messaging import Message

from test_work_order_query import PHOTO_5
import work_order_card
from work_order_card import build_work_order_cards


BASE = Path(__file__).resolve().parent
STORAGE = json.loads((BASE / "storage_data.json").read_text(encoding="utf-8"))
PACKAGING = json.loads((BASE / "packaging_data.json").read_text(encoding="utf-8"))


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


def test_main_card_separates_length_min_max_and_packaging_details():
    card = build_work_order_cards(PHOTO_5, STORAGE, PACKAGING)
    main, detail = card["messages"]
    assert main["type"] == detail["type"] == "flex"
    assert main["contents"]["type"] == detail["contents"]["type"] == "bubble"
    text = _body_text(main)
    assert "Y1223801-012" in text
    assert "方鉦" in text
    assert "長度  ·  Panjang / Length" in text
    assert "MIN\n2500\nmm" in text
    assert "MAX\n2550\nmm" in text
    assert "成品尺寸  ·  Ukuran jadi / Finished size" in text
    assert "MIN\n3.97\nmm" in text
    assert "MAX\n4\nmm" in text
    assert "9G" in text
    assert "不噴 / Tidak dicat / No spray paint" in text
    assert "不需套環 / Tidak perlu cincin / No ring required" in text
    assert "Masukkan bundel kecil" not in text
    details_text = _body_text(detail)
    assert "原表" not in text
    assert "Masukkan bundel kecil" in details_text
    assert "Place small bundles" in details_text
    assert "中文原文" in details_text
    assert "Panjang" not in details_text and "Protective ring" not in details_text
    assert "MIN 2500 · MAX 2550 mm" in card["fallback_text"]
    assert all(Message.from_dict(msg).type == "flex" for msg in card["messages"])
    assert all(len(json.dumps(msg["contents"], ensure_ascii=False).encode("utf-8")) < 30000
               and len(msg["altText"]) < 300 for msg in card["messages"])


def test_known_storage_area_is_displayed_and_not_inferred_for_unmapped_length():
    mapping = {"方鉦": [["<=3200", "EH79"], [">3200", "EH72"]]}
    card = build_work_order_cards(PHOTO_5, mapping, PACKAGING)
    assert "EH79" in _body_text(card["messages"][0])
    assert "EH79" in card["fallback_text"]
    unmapped = build_work_order_cards(PHOTO_5, {"方鉦": [[">=3200", "EH79"]]}, PACKAGING)
    body = _body_text(unmapped["messages"][0])
    assert "EH79" not in body
    assert "此長度無儲區" in body


def test_one_unreadable_length_cell_keeps_other_value_but_not_storage():
    partial = PHOTO_5.replace("長度MAX：2550", "長度MAX：?")
    card = build_work_order_cards(partial, STORAGE, PACKAGING)
    main = _body_text(card["messages"][0])
    assert "MIN\n2500\nmm" in main
    assert "MAX\n待確認\nPeriksa / Verify" in main
    assert "長度待確認" in main
    assert "EH79" not in main
    partial = PHOTO_5.replace("長度MIN：2500", "長度MIN：?")
    main = _body_text(build_work_order_cards(partial, STORAGE, PACKAGING)["messages"][0])
    assert "MIN\n待確認\nPeriksa / Verify" in main
    assert "MAX\n2550\nmm" in main


def test_ambiguous_indonesian_number_format_keeps_original_for_manual_review():
    image = PHOTO_5.replace("長度MIN：2500", "長度MIN：4.200")
    image = image.replace("長度MAX：2550", "長度MAX：4.250")
    main = _body_text(build_work_order_cards(image, STORAGE, PACKAGING)["messages"][0])
    assert "MIN\n4.200\n格式待核對" in main
    assert "MAX\n4.250\n格式待核對" in main
    assert "長度待確認" in main
    assert "EH79" not in main and "EH72" not in main


def test_paint_code_names_only_if_spray_is_requested_and_verified():
    no_paint = PHOTO_5.replace("顏色：N", "顏色：109")
    main = _body_text(build_work_order_cards(no_paint, STORAGE, PACKAGING)["messages"][0])
    assert "色碼 / Kode" not in main and "hitam" not in main
    both = no_paint.replace("噴漆位置：不噴", "噴漆位置：雙邊")
    main = _body_text(build_work_order_cards(both, STORAGE, PACKAGING)["messages"][0])
    assert "雙邊 / Kedua sisi / Both sides" in main
    assert "109" in main and "黑 / hitam / black" in main
    main = _body_text(build_work_order_cards(both.replace("109", "X9"), STORAGE, PACKAGING)["messages"][0])
    assert "X9" in main and "顏色待核對" in main
    assert "hitam" not in main


def test_special_note_suppresses_ring_and_unknown_packaging_stays_unknown():
    image = PHOTO_5.replace("包裝代碼：9G", "包裝代碼：ZZ").replace("特殊備註：", "特殊備註：NO KONDOM")
    result = build_work_order_cards(image, STORAGE, PACKAGING)
    assert len(result["messages"]) == 1
    main = _body_text(result["messages"][0])
    assert "依工單備註不套環" in main
    assert "ZZ · 資料表無此碼" in main
    assert "木箱+膠膜(小捆)" not in main


def test_unknown_admin_method_uses_callbacks_without_fake_translations():
    method = "材質新方案"
    custom = {"ZZ": {"品保設計(新版)": "ZZ", "簡稱": "新包裝", "詳細包裝方式說明(冷精棒-設計)": method}}
    image = PHOTO_5.replace("包裝代碼：9G", "包裝代碼：ZZ")
    received = []

    def id_translator(value):
        received.append(("id", value))
        return "Metode " + value

    def en_translator(value):
        received.append(("en", value))
        return "Method " + value

    result = build_work_order_cards(image, STORAGE, custom,
                                    translate_zh_to_id=id_translator,
                                    translate_zh_to_en=en_translator)
    assert len(result["messages"]) == 2
    assert ("id", "新包裝") in received and ("en", "新包裝") in received
    assert ("id", method) in received and ("en", method) in received
    assert "Metode " + method in _body_text(result["messages"][1])
    assert "Method " + method in _body_text(result["messages"][1])
    unknown = build_work_order_cards(image, STORAGE, custom)
    assert "翻譯待核對" in _body_text(unknown["messages"][1])
    assert "Translation needs verification" in _body_text(unknown["messages"][1])


def test_untrusted_long_table_description_is_bounded_and_control_chars_removed():
    very_long = "進箱\u202e\x00" + "甲" * 12000
    custom = {"ZZ": {"品保設計(新版)": "ZZ", "簡稱": "新包裝", "詳細包裝方式說明(冷精棒-設計)": very_long}}
    image = PHOTO_5.replace("包裝代碼：9G", "包裝代碼：ZZ")
    result = build_work_order_cards(image, STORAGE, custom)
    assert len(result["messages"]) == 2
    assert all(len(json.dumps(m["contents"], ensure_ascii=False).encode("utf-8")) < 30000
               for m in result["messages"])
    detail = _body_text(result["messages"][1])
    assert "\u202e" not in detail and "\x00" not in detail
    assert detail.count("甲") <= 1350


def test_broken_or_oversized_detail_never_discards_the_primary_card(monkeypatch):
    def broken_detail(*_args, **_kwargs):
        raise RuntimeError("bad admin description")

    monkeypatch.setattr(work_order_card, "_detail_message", broken_detail)
    result = build_work_order_cards(PHOTO_5, STORAGE, PACKAGING)
    assert len(result["messages"]) == 1
    assert "2500" in _body_text(result["messages"][0])
    assert Message.from_dict(result["messages"][0]).type == "flex"

    def oversized_detail(*_args, **_kwargs):
        return {"type": "flex", "altText": "too large", "contents": {"type": "bubble", "body": {
            "type": "box", "layout": "vertical", "contents": [{"type": "text", "text": "甲" * 12000}]}}}

    monkeypatch.setattr(work_order_card, "_detail_message", oversized_detail)
    result = build_work_order_cards(PHOTO_5, STORAGE, PACKAGING)
    assert len(result["messages"]) == 1
    assert Message.from_dict(result["messages"][0]).type == "flex"


def test_not_work_order_returns_no_fabricated_card():
    result = build_work_order_cards("早安", STORAGE, PACKAGING)
    assert result["messages"] == []
    assert "Work order not confirmed" in result["fallback_text"]
