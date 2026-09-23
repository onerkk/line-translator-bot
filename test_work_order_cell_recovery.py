"""Focused, source-safe rereads for paint position, ring and package code."""

import base64
import io
import json
from pathlib import Path

from PIL import Image

from work_order_cell_recovery import (merge_confirmed_cells, needs_cell_retry,
                                      orientation_candidates)


ROOT = Path(__file__).resolve().parent
PACKAGING = json.loads((ROOT / "packaging_data.json").read_text(encoding="utf-8"))

ORDER = """冷精棒製造指示書
訂單編號：Y123456
客戶名稱：DACAPO
收貨人：DACAPO
成品尺寸MIN：20
成品尺寸MAX：21
長度MIN：6000
長度MAX：6050
訂單流程：CHRAPL
噴漆位置：?
套環：?
顏色：102
包裝代碼：1C
"""

RETRY = "噴漆位置：N\n套環：N\n包裝代碼：1D"


def test_missing_cells_and_unknown_package_trigger_focused_retry():
    assert needs_cell_retry(ORDER, PACKAGING)
    complete = ORDER.replace("噴漆位置：?", "噴漆位置：雙邊")
    complete = complete.replace("套環：?", "套環：Y").replace("包裝代碼：1C", "包裝代碼：1D")
    assert not needs_cell_retry(complete, PACKAGING)


def test_retry_repairs_only_unreadable_cells_and_known_package_code():
    merged = merge_confirmed_cells(ORDER, RETRY, PACKAGING)
    assert merged != ORDER
    assert "噴漆位置：N" in merged
    assert "套環：N" in merged
    assert "包裝代碼：1D" in merged
    assert "包裝代碼：1C" not in merged
    assert not needs_cell_retry(merged, PACKAGING)


def test_retry_never_overwrites_valid_source_cells():
    source = (ORDER.replace("噴漆位置：?", "噴漆位置：雙邊")
              .replace("套環：?", "套環：Y").replace("包裝代碼：1C", "包裝代碼：1D"))
    disagreeing = "噴漆位置：N\n套環：N\n包裝代碼：9G"
    assert merge_confirmed_cells(source, disagreeing, PACKAGING) == source


def test_duplicate_source_cells_are_not_resolved_by_a_second_read():
    source = ORDER.replace("噴漆位置：?", "噴漆位置：N").replace(
        "套環：?", "套環：Y\n套環：N").replace("包裝代碼：1C", "包裝代碼：1D")
    assert merge_confirmed_cells(source, "套環：Y", PACKAGING) == source


def test_unmatched_retry_package_code_is_not_adopted():
    assert merge_confirmed_cells(ORDER, "包裝代碼：1C", PACKAGING) == ORDER


def test_conflicting_duplicate_values_in_retry_are_rejected():
    retry = "噴漆位置：N\n噴漆位置：雙邊\n套環：N\n包裝代碼：1D"
    merged = merge_confirmed_cells(ORDER, retry, PACKAGING)
    assert "噴漆位置：?" in merged
    assert "套環：N" in merged
    assert "包裝代碼：1D" in merged


def test_rotated_enhanced_candidates_are_valid_images_in_both_directions():
    source = Image.new("RGB", (709, 1536), "white")
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG")
    candidates = orientation_candidates(base64.b64encode(buffer.getvalue()).decode("ascii"))
    assert [item["rotation"] for item in candidates] == [90, 270]
    sizes = []
    for candidate in candidates:
        image_bytes = base64.b64decode(candidate["base64"], validate=True)
        with Image.open(io.BytesIO(image_bytes)) as image:
            sizes.append(image.size)
    assert sizes[0][0] > sizes[0][1]
    assert sizes[1][0] > sizes[1][1]
