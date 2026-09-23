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


def test_duplicate_six_row_customer_rules_stay_unresolved_where_they_overlap():
    # These five older multirow entries need an administrator to settle the
    # middle and long lengths; preserving both sets prevents a false answer.
    duplicate_rules = {customer: rows for customer, rows in STORAGE.items() if len(rows) == 6}
    assert len(duplicate_rules) == 5
    for customer, rows in duplicate_rules.items():
        assert rows[0][0] == "<=3200"
        assert _resolve_storage(customer, (Decimal("2500"), Decimal("2550")), STORAGE)["area"] == rows[0][1]
        assert _resolve_storage(customer, (Decimal("3500"), Decimal("3550")), STORAGE)["status"] == "ambiguous_mapping"


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
