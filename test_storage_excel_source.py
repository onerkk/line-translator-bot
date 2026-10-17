"""Regression checks for storage rows extracted from the ag-grid workbook."""

import copy
import json
import re
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
    # This is the immutable workbook reference, not the admin-editable table.
    data = json.loads((_DIRECTORY / "storage_reference.json").read_text(encoding="utf-8"))
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


def test_live_storage_table_has_valid_rows_without_requiring_historical_equality():
    from storage_import import normalize_storage_lookup

    live = json.loads((_DIRECTORY / "storage_data.json").read_text(encoding="utf-8"))
    assert isinstance(live, dict) and live
    for customer, rows in live.items():
        assert isinstance(customer, str) and customer.strip(), repr(customer)
        assert isinstance(rows, list), customer
        for row in rows:
            assert isinstance(row, list) and len(row) == 2, (customer, row)
            assert all(isinstance(value, str) and value.strip() for value in row), (customer, row)
    normalized = normalize_storage_lookup(live)
    assert set(normalized) == set(live)
    for customer, rows in normalized.items():
        assert len(rows) == len(live[customer]), customer
        for index, (condition, area) in enumerate(rows):
            assert re.fullmatch(r"(?:(?:<=|>=|<|>)\d+(?:\.\d+)?)+", condition), (customer, condition)
            assert area == live[customer][index][1], (customer, area)


def test_admin_edits_remain_authoritative_without_changing_reference():
    from work_order_storage_reference import effective_work_order_storage_lookup

    reference = {"示例客戶": [["<=3200", "OLD01"], [">3200", "OLD02"]]}
    live = {"示例客戶": [["<=3200", "NEW01、NEW02"], [">3200", "NEW03"]],
            "新增客戶": [[">4200", "NEW04"]]}
    before_live, before_reference = copy.deepcopy(live), copy.deepcopy(reference)
    assert effective_work_order_storage_lookup(live, reference) == live
    assert live == before_live
    assert reference == before_reference
