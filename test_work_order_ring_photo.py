"""Rules checked against the photographed DACAPO grinding work order.

The transcription below is read by a person from ``235509.jpg``.  These
checks do not assert that any online vision model read the picture correctly.
"""

import json
from pathlib import Path

import pytest

from work_order_query import extract_work_order_info
from work_order_card import build_work_order_cards


ROOT = Path(__file__).resolve().parent
STORAGE = json.loads((ROOT / "storage_data.json").read_text(encoding="utf-8"))
PACKAGING = json.loads((ROOT / "packaging_data.json").read_text(encoding="utf-8"))

PHOTO_DACAPO = """冷精棒製造指示書 Petunjuk produksi Cold Finished Bar
訂單編號：Y1223786-008
客戶名稱：DACAPO
收貨人：DACAPO
成品尺寸MIN：17.957
成品尺寸MAX：18
長度MIN：6000
長度MAX：6050
訂單流程：CHRAPDGL
噴漆位置：雙邊
套環：Y
顏色：土藍
包裝代碼：1O
特殊備註：
"""


def _photo(**changes):
    text = PHOTO_DACAPO
    for original, replacement in changes.items():
        assert original in text
        text = text.replace(original, replacement)
    return text


@pytest.mark.parametrize("form", ["Y", "N", "?", None])
def test_regular_customer_uses_the_form_ring_value(form):
    text = PHOTO_DACAPO.replace("套環：Y\n", "套環：" + form + "\n" if form is not None else "")
    info = extract_work_order_info(text, STORAGE, PACKAGING)
    assert info["fields"]["flow"] == "CHRAPDGL"
    if form == "Y":
        expected = {"status": "yes", "reason": "form_y", "process": None}
    elif form == "N":
        expected = {"status": "no", "reason": "form_n", "process": None}
    else:
        expected = {"status": "unknown", "reason": "ring_field", "process": None}
    assert info["ring"] == expected
    result = build_work_order_cards(text, STORAGE, PACKAGING)
    assert len(result["messages"]) == 1
    if form == "Y":
        assert "需要套環 / Wajib pakai cincin pelindung" in result["fallback_text"]
    elif form == "N":
        assert "不需套環 / Tidak perlu cincin pelindung" in result["fallback_text"]
    else:
        assert "工單套環欄位待確認" in result["fallback_text"]


@pytest.mark.parametrize("separator", [" ", "  "])
def test_ocr_whitespace_inside_printed_process_keeps_gl_terminal(separator):
    text = _photo(**{"CHRAPDGL": f"CHRAPD{separator}GL"})
    assert extract_work_order_info(text, STORAGE, PACKAGING)["fields"]["flow"] == f"CHRAPD{separator}GL"
    assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"]["status"] == "yes"


def test_tab_separator_is_an_ocr_column_boundary_not_part_of_the_process():
    text = _photo(**{"CHRAPDGL": "CHRAPD\tGL"})
    info = extract_work_order_info(text, STORAGE, PACKAGING)
    assert info["fields"]["flow"] is None
    assert info["ring"]["reason"] == "form_y"


def test_regular_customer_form_does_not_require_process_or_size_to_decide():
    missing = _photo(**{"成品尺寸MIN：17.957": "成品尺寸MIN：?",
                        "成品尺寸MAX：18": "成品尺寸MAX：?"})
    assert extract_work_order_info(missing, STORAGE, PACKAGING)["ring"]["status"] == "yes"
    assert extract_work_order_info(missing.replace("套環：Y", "套環：N"), STORAGE, PACKAGING)["ring"]["status"] == "no"
    assert extract_work_order_info(missing.replace("套環：Y\n", ""), STORAGE, PACKAGING)["ring"]["reason"] == "ring_field"


def test_regular_customer_form_value_controls_even_when_process_is_unreadable():
    for process in ("?", "CHRAPD8L", "CHRAP", "GL?"):
        text = _photo(**{"訂單流程：CHRAPDGL": "訂單流程：" + process})
        assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"]["reason"] == "form_y"
        assert extract_work_order_info(text.replace("套環：Y", "套環：N"), STORAGE, PACKAGING)["ring"]["status"] == "no"
        assert extract_work_order_info(text.replace("套環：Y\n", ""), STORAGE, PACKAGING)["ring"]["reason"] == "ring_field"


@pytest.mark.parametrize("value", ["Y", "N", "?", None])
def test_jiadong_polishing_20mm_rule_overrides_y_or_n(value):
    text = PHOTO_DACAPO.replace("客戶名稱：DACAPO", "客戶名稱：佳東").replace("收貨人：DACAPO", "收貨人：佳東")
    text = text.replace("訂單流程：CHRAPDGL", "訂單流程：CHRAPDL")
    text = text.replace("成品尺寸MIN：17.957", "成品尺寸MIN：20").replace("成品尺寸MAX：18", "成品尺寸MAX：20")
    text = text.replace("套環：Y\n", "套環：" + value + "\n" if value is not None else "")
    ring = extract_work_order_info(text, STORAGE, PACKAGING)["ring"]
    assert ring == {"status": "yes", "reason": "jiadong_polishing_20mm",
                    "process": "polishing", "threshold": 20}


def test_form_y_controls_even_for_packaging_material_flow():
    text = _photo(**{"訂單流程：CHRAPDGL": "訂單流程：CHRAPD"})
    assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"] == {
        "status": "yes", "reason": "form_y", "process": None}
    assert extract_work_order_info(text.replace("套環：Y", "套環：N"), STORAGE, PACKAGING)["ring"] == {
        "status": "no", "reason": "form_n", "process": None}


def test_special_no_kondom_note_overrides_even_printed_y_and_gl_18():
    text = _photo(**{"特殊備註：": "特殊備註：不要黑色套環（NO KONDOM）"})
    assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"] == {
        "status": "no", "reason": "explicit_note", "process": None}


@pytest.mark.parametrize(("min_size", "max_size", "expected"), [
    ("20", "20", "yes"), ("20", "22", "yes"),
    ("19.9", "19.9", "no"), ("19.9", "20.1", "unknown"),
])
def test_jiadong_polishing_threshold_is_inclusive_and_crossings_stay_unresolved(min_size, max_size, expected):
    text = PHOTO_DACAPO.replace("客戶名稱：DACAPO", "客戶名稱：佳東").replace("收貨人：DACAPO", "收貨人：佳東")
    text = text.replace("訂單流程：CHRAPDGL", "訂單流程：CHRAPDL")
    text = text.replace("成品尺寸MIN：17.957", "成品尺寸MIN：" + min_size).replace(
        "成品尺寸MAX：18", "成品尺寸MAX：" + max_size).replace("套環：Y", "套環：N")
    assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"]["status"] == expected


def test_printed_y_is_read_from_the_ring_column_not_adjacent_color_or_packaging():
    table = """冷精棒製造指示書
客戶名稱：DACAPO
訂單流程：CHRAPDGL
成品尺寸MIN：17.957
成品尺寸MAX：18
噴漆位置 | 套環 | 顏色 | 包裝代碼
雙邊 | Y | 土藍 | 1O
"""
    info = extract_work_order_info(table, STORAGE, PACKAGING)
    assert info["fields"]["ring_on_form"] == "Y"
    assert info["ring"]["status"] == "yes"
    assert info["fields"]["color"] == "土藍"
    assert info["fields"]["packaging"] == "1O"


@pytest.mark.parametrize("position", ["N", "不噴", ""])
def test_paint_color_controls_when_position_is_no_or_blank(position):
    text = _photo().replace("噴漆位置：雙邊", "噴漆位置：" + position)
    info = extract_work_order_info(text, STORAGE, PACKAGING)
    assert info["paint"]["status"] == "color_only"
    card = build_work_order_cards(text, STORAGE, PACKAGING)
    assert "要噴漆" in card["fallback_text"]
    expected_position = ("工單噴漆位置尚未讀到" if not position else
                         "工單噴漆位置:" + position if position == "N" else
                         "工單位置:" + position)
    assert expected_position in card["fallback_text"]
    assert "色碼 46 · 土藍 / biru bernuansa tanah" in card["fallback_text"]
