"""Customer-prefix resolution for source-bound work-order storage lookup."""

import json
from pathlib import Path

import pytest

from work_order_detection import analyze_work_order_text, resolve_storage_customer
from work_order_query import extract_work_order_info


STORAGE = json.loads((Path(__file__).parent / "storage_data.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("printed", ["SUNGE", "sunge", "ＳＵＮＧＥ", "S U N G E"])
def test_printed_sunge_resolves_only_to_full_customer(printed):
    assert resolve_storage_customer(printed, STORAGE) == "SUNGEUN"


def test_customer_column_prefix_resolves_name_and_storage_using_complete_length():
    ocr = """冷精棒製造指示書 Petunjuk produksi Cold Finished Bar
訂單編號 / No.Pesan | 客戶名稱 / Nama Pelanggan | 收貨人 / Penerima Barang
Y1224051-023 | SUNGE | 不同收貨人
長度MIN / Panjang MIN | MAX
6000 | 6050
訂單流程：CHRAPL
"""
    info = extract_work_order_info(ocr, STORAGE)
    assert info["is_work_order"] is True
    assert info["customer"] == "SUNGEUN"
    assert info["storage"] == {"status": "ok", "area": "EG33", "customer": "SUNGEUN"}


def test_partial_customer_never_comes_from_recipient_column():
    header = "冷精棒製造指示書\n訂單編號 | 客戶名稱 | 收貨人\n"
    assert analyze_work_order_text(header + "Y1224051-023 | | SUNGEUN", STORAGE)["customer"] is None
    assert analyze_work_order_text(header + "Y1224051-023 | ? | SUNGEUN", STORAGE)["customer"] is None
    assert analyze_work_order_text(header + "Y1224051-023 | SUNGE | SUNGSIL METAL", STORAGE)["customer"] == "SUNGEUN"


@pytest.mark.parametrize("printed", ["SUNG", "SUN", "GEUN", "SUNG3", "SUNGEUNXX", "望"])
def test_short_ambiguous_nonprefix_and_wrong_spelling_stay_unresolved(printed):
    assert resolve_storage_customer(printed, STORAGE) is None


def test_multiple_candidate_prefixes_do_not_guess_even_with_same_storage():
    lookup = {"SUNGEUN": [[">4200", "EG33"]], "SUNGECK": [[">4200", "EG33"]]}
    assert resolve_storage_customer("SUNGE", lookup) is None
    text = "冷精棒製造指示書\n客戶名稱：SUNGE\n長度MIN：6000\n長度MAX：6050"
    result = extract_work_order_info(text, lookup)
    assert result["customer"] == "SUNGE"
    assert result["storage"]["status"] == "unknown_customer"


def test_an_exact_customer_wins_even_when_its_name_prefixes_another():
    lookup = {"SUNGE": [], "SUNGEUN": []}
    assert resolve_storage_customer("SUNGE", lookup) == "SUNGE"


def test_long_enough_chinese_prefix_uses_the_same_uniqueness_rule():
    assert resolve_storage_customer("鐿順", STORAGE) == "鐿順發"
    assert resolve_storage_customer("順發", STORAGE) is None
