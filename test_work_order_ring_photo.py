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
def test_dacapo_photo_grinding_gl_17_957_to_18_needs_ring_regardless_of_form(form):
    text = PHOTO_DACAPO.replace("套環：Y\n", "套環：" + form + "\n" if form is not None else "")
    info = extract_work_order_info(text, STORAGE, PACKAGING)
    assert info["fields"]["flow"] == "CHRAPDGL"
    assert info["ring"] == {
        "status": "yes", "reason": "size_rule", "process": "grinding", "threshold": 16,
    }
    result = build_work_order_cards(text, STORAGE, PACKAGING)
    assert len(result["messages"]) == 1
    assert "需要套環 / Wajib pakai cincin pelindung" in result["fallback_text"]


@pytest.mark.parametrize("separator", [" ", "  "])
def test_ocr_whitespace_inside_printed_process_keeps_gl_terminal(separator):
    text = _photo(**{"CHRAPDGL": f"CHRAPD{separator}GL"})
    assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"]["status"] == "yes"


def test_tab_separator_is_an_ocr_column_boundary_not_part_of_the_process():
    text = _photo(**{"CHRAPDGL": "CHRAPD\tGL"})
    assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"]["status"] == "unknown"


def test_printed_y_or_n_never_resolves_missing_diameter_even_when_process_is_known():
    missing = _photo(**{"成品尺寸MIN：17.957": "成品尺寸MIN：?",
                        "成品尺寸MAX：18": "成品尺寸MAX：?"})
    for variant in (missing, missing.replace("套環：Y", "套環：N"),
                    missing.replace("套環：Y\n", "")):
        assert extract_work_order_info(variant, STORAGE, PACKAGING)["ring"] == {
            "status": "unknown", "reason": "diameter", "process": "grinding"}


def test_missing_or_unrecognized_process_stays_unknown_even_with_printed_y_or_n():
    for process in ("?", "CHRAPD8L", "CHRAP", "GL?"):
        text = _photo(**{"訂單流程：CHRAPDGL": "訂單流程：" + process})
        for variant in (text, text.replace("套環：Y", "套環：N"),
                        text.replace("套環：Y\n", "")):
            assert extract_work_order_info(variant, STORAGE, PACKAGING)["ring"] == {
                "status": "unknown", "reason": "flow", "process": None}


@pytest.mark.parametrize("value", ["N", "Y?", "Y", "?", None])
def test_printed_ring_never_overrides_grinding_size_requirement(value):
    text = PHOTO_DACAPO.replace("套環：Y\n", "套環：" + value + "\n" if value is not None else "")
    ring = extract_work_order_info(text, STORAGE, PACKAGING)["ring"]
    assert ring == {"status": "yes", "reason": "size_rule", "process": "grinding", "threshold": 16}


def test_packaging_material_d_remains_no_ring_even_when_form_says_y():
    text = _photo(**{"訂單流程：CHRAPDGL": "訂單流程：CHRAPD"})
    assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"] == {
        "status": "no", "reason": "packaging_material", "process": "packaging"}
    assert extract_work_order_info(text.replace("套環：Y", "套環：N"), STORAGE, PACKAGING)["ring"] == {
        "status": "no", "reason": "packaging_material", "process": "packaging"}


def test_special_no_kondom_note_overrides_even_printed_y_and_gl_18():
    text = _photo(**{"特殊備註：": "特殊備註：不要黑色套環（NO KONDOM）"})
    assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"] == {
        "status": "no", "reason": "explicit_note", "process": None}


@pytest.mark.parametrize(("flow", "min_size", "max_size", "form", "status"), [
    ("CHRAPGL", "16", "16", "Y", "yes"),
    ("CHRAPGL", "15.9", "15.9", "N", "no"),
    ("CHRAPL", "20", "20", "Y", "yes"),
    ("CHRAPL", "19.9", "19.9", "N", "no"),
    ("CHRAPL", "19.9", "19.9", "Y", "no"),
    ("CHRAPGL", "16", "16", "N", "yes"),
    ("CHRAPGL", "15.9", "15.9", "Y", "no"),
    ("CHRAPL", "20", "20", "N", "yes"),
])
def test_process_cutoffs_independent_of_printed_form(flow, min_size, max_size, form, status):
    text = _photo(**{"CHRAPDGL": flow, "17.957": min_size,
                       "成品尺寸MAX：18": "成品尺寸MAX：" + max_size, "套環：Y": "套環：" + form})
    assert extract_work_order_info(text, STORAGE, PACKAGING)["ring"]["status"] == status


def test_threshold_crossing_and_invalid_diameter_remain_unknown_even_with_form_y():
    crossing = _photo(**{"成品尺寸MIN：17.957": "成品尺寸MIN：15.99",
                         "成品尺寸MAX：18": "成品尺寸MAX：16.01"})
    assert extract_work_order_info(crossing, STORAGE, PACKAGING)["ring"]["reason"] == "threshold_crossing"
    invalid = _photo(**{"成品尺寸MIN：17.957": "成品尺寸MIN：18",
                        "成品尺寸MAX：18": "成品尺寸MAX：17"})
    assert extract_work_order_info(invalid, STORAGE, PACKAGING)["ring"]["status"] == "unknown"


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
