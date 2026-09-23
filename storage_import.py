"""Import the storage export and normalize its A/B/C length bands.

The ag-grid export stores one customer and one lettered length band per row;
its six storage columns are alternative physical areas for that same band.
Keep the source row order and conflicting duplicate rows intact so the work
order resolver can refuse to invent a unique area when there is none.
"""

from __future__ import annotations

import re
import unicodedata


LENGTH_BANDS = {
    "A": "<=3200",
    "B": ">3200<=4200",
    "C": ">4200",
}


def _header_key(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def _cell_text(value):
    return str(value).strip() if value is not None else ""


def parse_storage_ag_grid_rows(rows):
    """Read the 客戶名稱／訂單長度／儲區1…6 Excel export.

    Return None when the heading belongs to a different storage file format.
    Invalid letter codes in a populated source row raise ValueError so an
    upload cannot silently make unqueryable storage rules.
    """
    if not rows:
        return None
    heading = [_header_key(cell) for cell in rows[0]]
    if "客戶名稱" not in heading or "訂單長度" not in heading:
        return None
    customer_col = heading.index("客戶名稱")
    length_col = heading.index("訂單長度")
    area_cols = sorted(
        ((int(match.group(1)), index) for index, cell in enumerate(heading)
         if (match := re.fullmatch(r"儲區([1-6])", cell))),
    )
    if not area_cols or area_cols[0][0] != 1:
        raise ValueError("儲區查詢 Excel 缺少『儲區1』欄位")

    storage = {}
    for row_number, row in enumerate(rows[1:], start=2):
        customer = _cell_text(row[customer_col]) if customer_col < len(row) else ""
        if not customer:
            continue
        areas = []
        for _index, col in area_cols:
            value = _cell_text(row[col]) if col < len(row) else ""
            if value and value not in areas:
                areas.append(value)
        if not areas:
            continue
        letter = (unicodedata.normalize("NFKC", _cell_text(row[length_col])).upper()
                  if length_col < len(row) else "")
        if letter not in LENGTH_BANDS:
            raise ValueError(f"第 {row_number} 列訂單長度 {letter or '空白'} 無法辨識；只接受 A、B、C")
        storage.setdefault(customer, []).append([LENGTH_BANDS[letter], "、".join(areas)])
    return storage


def normalize_storage_lookup(data):
    """Upgrade old persisted A/B/C rules without altering customer area codes.

    An already numeric rule, an explicit empty customer row, and custom area
    values keep their original meaning.  The input mapping is not mutated.
    """
    if not isinstance(data, dict):
        return data
    normalized = {}
    for customer, rows in data.items():
        if not isinstance(rows, list):
            normalized[customer] = rows
            continue
        entries = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                entries.append(row)
                continue
            rule, area = row
            letter = unicodedata.normalize("NFKC", _cell_text(rule)).upper()
            entries.append([LENGTH_BANDS.get(letter, rule), area])
        normalized[customer] = entries
    return normalized
