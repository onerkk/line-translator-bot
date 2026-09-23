"""Regression checks for the customer storage length bands imported from Excel.

The work order specifies a finished bar length interval.  The lookup must
match the entire interval against one unambiguous customer storage area.
"""

import json
from decimal import Decimal
from pathlib import Path

from work_order_query import _resolve_storage, extract_work_order_info


STORAGE = json.loads(Path(__file__).with_name("storage_data.json").read_text(encoding="utf-8"))


def test_photo_five_explicit_length_finds_customer_storage_area():
    work_order = """冷精棒製造指示書 Petunjuk produksi Cold Finished Bar
訂單編號：Y1223801-012
客戶名稱：方鉦
收貨人：方鉦
長度MIN：2500
長度MAX：2550
"""
    info = extract_work_order_info(work_order, STORAGE, {})
    assert info["length"] == (Decimal("2500"), Decimal("2550"))
    assert info["storage"] == {"status": "ok", "area": "EH79", "customer": "方鉦"}


def test_three_band_customer_rules_cover_boundaries_without_overlap():
    three_band = {customer: rows for customer, rows in STORAGE.items()
                  if len(rows) == 3 and rows[1][0] == ">3200<=4200"
                  and rows[2][0] == ">4200"}
    assert len(three_band) == 390
    for customer, rows in three_band.items():
        assert [row[0] for row in rows] == ["<=3200", ">3200<=4200", ">4200"]
        for low, high, area in (
            ("2500", "3200", rows[0][1]),
            ("3200.01", "4200", rows[1][1]),
            ("4200.01", "5000", rows[2][1]),
        ):
            actual = _resolve_storage(customer, (Decimal(low), Decimal(high)), STORAGE)
            assert actual == {"status": "ok", "area": area, "customer": customer}
    assert _resolve_storage("方鉦", (Decimal("3200"), Decimal("3200")), STORAGE)["area"] == "EH79"
    assert _resolve_storage("俊益", (Decimal("4200"), Decimal("4200")), STORAGE)["area"] == "EH78"
    assert _resolve_storage("俊益", (Decimal("4200.01"), Decimal("4201")), STORAGE)["area"] == "EG38"


def test_duplicate_source_rows_are_retained_without_choosing_an_unverified_area():
    # Excel records for some customers repeat A/B/C.  Retain every source row
    # in its original order, even where two rows disagree on one area.
    duplicate_rules = {customer: rows for customer, rows in STORAGE.items() if len(rows) == 6}
    assert len(duplicate_rules) == 5
    for customer, rows in duplicate_rules.items():
        assert [row[0] for row in rows] == ["<=3200", ">3200<=4200", ">4200"] * 2
    assert STORAGE["開滋一廠"][1::3] == [[">3200<=4200", "EG39"], [">3200<=4200", "EH78"]]
    assert STORAGE["開滋二廠"][1::3] == [[">3200<=4200", "EG38"], [">3200<=4200", "EH78"]]
    assert STORAGE["開滋三廠"][1::3] == [[">3200<=4200", "EG38"], [">3200<=4200", "EH78"]]
    assert STORAGE["常州眾山"][::3] == [["<=3200", "EC47"], ["<=3200", "EH28"]]
    for customer in ("開滋一廠", "開滋二廠", "開滋三廠"):
        assert _resolve_storage(customer, (Decimal("3500"), Decimal("3550")), STORAGE)["status"] == "ambiguous_mapping"
    assert _resolve_storage("常州眾山", (Decimal("2500"), Decimal("2550")), STORAGE)["status"] == "ambiguous_mapping"
    assert _resolve_storage("TSM", (Decimal("3500"), Decimal("3550")), STORAGE)["area"] == "EG14"


def test_storage_without_confirmed_length_mapping_stays_unresolved():
    # These customers have incomplete data, but their <=3200 rules were
    # independently confirmed against the embedded original lookup.
    assert STORAGE["NMSK"] == [["<=3200", "EH28"], [">4200", "EG34"]]
    assert STORAGE["鉅豐"] == [["<=3200", "EH79"]]
    assert _resolve_storage("NMSK", (Decimal("2500"), Decimal("2550")), STORAGE)["area"] == "EH28"
    assert _resolve_storage("鉅豐", (Decimal("2500"), Decimal("2550")), STORAGE)["area"] == "EH79"
    assert _resolve_storage("不存在的客戶", (Decimal("2500"), Decimal("2550")), STORAGE)["status"] == "unknown_customer"
    assert _resolve_storage("域鑫科技", (Decimal("2500"), Decimal("2550")), STORAGE)["status"] == "unmapped_length"
    assert _resolve_storage("NMSK", (Decimal("3500"), Decimal("3550")), STORAGE)["status"] == "unmapped_length"
    assert _resolve_storage("鉅豐", (Decimal("3500"), Decimal("3550")), STORAGE)["status"] == "unmapped_length"
    assert _resolve_storage("方鉦", None, STORAGE)["status"] == "unknown_length"


def test_storage_source_multiple_area_cells_remain_in_display_order():
    # The six source storage columns are one source row, not six competing
    # customer rules; keep all distinct codes in their column order.
    assert STORAGE["廉錩"][0] == ["<=3200", "EH77、EH24、EH25"]
    assert STORAGE["TCI"][1] == [">3200<=4200", "EC51、EH29、EC49、EC50"]
    assert STORAGE["江陰外庫"][0] == ["<=3200", "EC47、EC42、EC43"]
    assert _resolve_storage("廉錩", (Decimal("2500"), Decimal("2550")), STORAGE)["area"] == "EH77、EH24、EH25"


def test_storage_source_customer_and_rows_are_complete():
    assert len(STORAGE) == 398
    assert sum(map(len, STORAGE.values())) == 1204  # 1212 Excel data rows - 3 unnamed - 5 without storage
    assert "" not in STORAGE
    assert all(rule in {"<=3200", ">3200<=4200", ">4200"}
               for rows in STORAGE.values() for rule, _area in rows)
    assert "域鑫科技" in STORAGE and len(STORAGE["域鑫科技"]) == 1
    assert "NMSK" in STORAGE and len(STORAGE["NMSK"]) == 2
    assert "鉅豐" in STORAGE and len(STORAGE["鉅豐"]) == 1
