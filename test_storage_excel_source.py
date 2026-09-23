"""Regression checks for storage rows extracted from the ag-grid workbook."""

import json
from pathlib import Path

from storage_import import parse_storage_ag_grid_rows


_DIRECTORY = Path(__file__).resolve().parent


def test_ag_grid_rows_keep_all_distinct_areas_and_conflicting_repeats():
    rows = [
        ("客戶名稱", "訂單長度", "儲區1", "儲區2", "儲區3", "儲區4", "儲區5", "儲區6"),
        ("示例客戶", "A", "EH77", "EH24", "EH77", None, None, None),
        ("示例客戶", "B", "EH78", None, None, None, None, None),
        ("示例客戶", "C", "EG38", None, None, None, None, None),
        ("示例客戶", "B", "EH79", None, None, None, None, None),
        (None, "A", "EH10", None, None, None, None, None),
        ("空儲區", "C", None, None, None, None, None, None),
    ]
    assert parse_storage_ag_grid_rows(rows) == {
        "示例客戶": [
            ["<=3200", "EH77、EH24"],
            [">3200<=4200", "EH78"],
            [">4200", "EG38"],
            [">3200<=4200", "EH79"],
        ]
    }


def test_shipped_source_rows_preserve_original_conflicts_and_multiple_areas():
    data = json.loads((_DIRECTORY / "storage_data.json").read_text(encoding="utf-8"))
    reference = json.loads((_DIRECTORY / "storage_reference.json").read_text(encoding="utf-8"))
    assert data == reference
    assert len(data) == 398
    assert sum(len(rows) for rows in data.values()) == 1204
    assert data["方鉦"] == [["<=3200", "EH79"], [">3200<=4200", "EH72"], [">4200", "EH72"]]
    assert data["俊益"] == [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]]
    assert data["廉錩"][0] == ["<=3200", "EH77、EH24、EH25"]
    assert data["TCI"][1] == [">3200<=4200", "EC51、EH29、EC49、EC50"]
    assert data["開滋一廠"][1::3] == [[">3200<=4200", "EG39"], [">3200<=4200", "EH78"]]
    assert data["開滋二廠"][1::3] == [[">3200<=4200", "EG38"], [">3200<=4200", "EH78"]]
    assert data["開滋三廠"][1::3] == [[">3200<=4200", "EG38"], [">3200<=4200", "EH78"]]
    assert data["常州眾山"][::3] == [["<=3200", "EC47"], ["<=3200", "EH28"]]
    assert data["TSM"] == [
        ["<=3200", "EH28"], [">3200<=4200", "EG14"], [">4200", "EG34"],
        ["<=3200", "EH28"], [">3200<=4200", "EG14"], [">4200", "EG34"],
    ]
    assert data["域鑫科技"] == [[">4200", "EC40"]]
    assert data["NMSK"] == [["<=3200", "EH28"], [">4200", "EG34"]]
    assert data["鉅豐"] == [["<=3200", "EH79"]]
