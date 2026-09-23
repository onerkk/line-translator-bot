"""Keep the pictured 方鉦 storage answer when OCR retains explicit lengths in common forms."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from test_work_order_query import PHOTO_5
from work_order_query import extract_work_order_info


STORAGE = json.loads(Path(__file__).with_name("storage_data.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("length_lines", [
    "長度MIN：2500 mm\n長度MAX：2550 mm",
    "長度MIN：2500\nMAX：2550",
    "長度MIN / Panjang MIN: 2500 mm\nMAX: 2550 mm",
    "長度MIN：2500 mm; MAX：2550 mm",
    "長度MIN / Panjang MIN | MAX\n2500 mm | 2550 mm",
])
def test_pictured_customer_storage_recovers_explicit_length_variants(length_lines):
    text = PHOTO_5.replace("長度MIN：2500\n長度MAX：2550", length_lines)
    result = extract_work_order_info(text, STORAGE, {})
    assert result["length"] == (Decimal("2500"), Decimal("2550"))
    assert result["storage"] == {"status": "ok", "area": "EH79", "customer": "方鉦"}


@pytest.mark.parametrize("length_lines", [
    "MAX：2550",  # No labelled length MIN nearby.
    "長度MIN：2500\n短尺MIN：2000\nMAX：2550",  # Not adjacent to length.
    "長度MIN：2500\nMAX：2400",  # Reversed readings.
    "長度MIN：2500 mm\nMAX：2550 kg",  # Wrong unit.
    "長度MIN：2.500 mm\nMAX：2.550 mm",  # Ambiguous number formatting.
    "長度MIN：2500\nMAX：2550\n長度MAX：2600",  # Conflicting readings.
])
def test_no_storage_guess_when_ocr_length_values_are_missing_or_conflicting(length_lines):
    text = PHOTO_5.replace("長度MIN：2500\n長度MAX：2550", length_lines)
    result = extract_work_order_info(text, STORAGE, {})
    assert result["length"] is None
    assert result["storage"]["status"] == "unknown_length"
    assert result["storage"]["area"] is None
