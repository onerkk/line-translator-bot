"""Local field extraction for the bilingual Cold Finished Bar work order.

Reference: tests/fixtures/work_order_20260906.jpg (user supplied, unchanged).
The reference defines layout, never customer/ID defaults for other photos.
No extra vision request, image upload, fuzzy name correction or cached lookup.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


WORK_ORDER_OCR_HINT = (
    "3d. **冷精棒製造指示書 / Petunjuk produksi Cold Finished Bar**："
    "訂單資訊區的『訂單編號 / No.Pesan』、『客戶名稱 / Nama Pelanggan』、"
    "『收貨人 / Penerima Barang』是不同欄。每格內的中印雙語標題合併為同一欄，"
    "例如『客戶名稱 / Nama Pelanggan』；資料列仍按相同欄序與欄數用 | 分隔，"
    "空白格也要保留分隔符，不可省略。不要將雙語標題拆成額外的資料欄。"
    "客戶與收貨人即使相同也要各自照實抄錄，不能互相代填。"
    "訂單流程 / Alur Pemasangan、成品MC / Produk jadi MC、母材 ID_NO、"
    "眼模編號 / No. Kode、工作站 / stasiun kerja 各屬不同欄位，不能當成客戶。"
    "成品MC 與舊版 MIC_NO 都照原文保留。手寫圈選不能改變欄位歸屬；"
    "照片裁切、遮擋或看不清的欄位不得從相鄰欄、樣本或記憶補值。\n"
)


def _key(value: str) -> str:
    """Only width, case and whitespace equivalence; do not change letters/digits."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def resolve_storage_customer(value: str | None, names: Iterable[str]) -> str | None:
    """Require one complete customer name, never a substring of another name."""
    if not isinstance(value, str) or not value.strip():
        return None
    names = [name for name in names if isinstance(name, str) and name.strip()]
    if value in names:
        return value
    matches = {name for name in names if _key(name) == _key(value)}
    return next(iter(matches)) if len(matches) == 1 else None


# Count independent field families. The old substring count counted one title
# several times and treated generic production notices as work orders.
_FIELD_FAMILIES = {
    "title": ("製造指示書", "制造指示书", "petunjukproduksi", "coldfinishedbar"),
    "order": ("訂單編號", "订单编号", "no.pesan", "nopesan", "nomorpesanan"),
    "customer": ("客戶名稱", "客户名称", "namapelanggan", "namapclanggan", "customername"),
    "recipient": ("收貨人", "收货人", "penerimabarang", "consignee"),
    "size": ("成品尺寸", "ukuranminprodukjadi", "uk.1min/max", "尺寸1min/max"),
    "material": ("id_no", "idno", "mic_no", "micno"),
    "product_mc": ("成品mc", "produkjadimc"),
    "flow": ("訂單流程", "订单流程", "alurpemasangan", "final流程"),
    "anneal": ("退火代碼", "退火代码", "kodeprosespanas-dingin"),
    "station": ("工作站", "stasiunkerja"),
}

_CUSTOMER_LABEL = re.compile(
    r"客\s*[戶户]\s*(?:名\s*[稱称])?"
    r"|\bnama\s*p[ec]langgan\b|\bcustomer\s*(?:name)?\b", re.I
)
_OTHER_LABEL = re.compile(
    r"收\s*[貨货]\s*人|\bpenerima\b|\bconsignee\b"
    r"|訂\s*單\s*(?:編\s*號|流程|資訊)|订\s*单\s*(?:编\s*号|流程|信息)"
    r"|\bno\.?\s*pesan\b|\bnomor\s*pesanan\b"
    r"|生[計计產产]交期|\bestimasi\b|交期|性[質质][碼码]|\bkode\s*jenis\b"
    r"|倒角|\bchamfer\b|成品|短[邊边尺]|[長长]度|厚度|\bid[\s_]*no\b"
    r"|\bmic[\s_]*no\b|[備备][註注]|\b(?:min|max)\b|[訂订][單单]|品保"
    r"|特殊|[製制]造|退火|眼模|工作站|\balur\b|\bproduk\b", re.I
)
_HEADER_FRAGMENTS = re.compile(
    r"客\s*[戶户]\s*(?:名\s*[稱称])?|\bnama\b|\bp[ec]langgan\b"
    r"|\bcustomer\b|\bname\b", re.I
)
_UNCERTAIN = re.compile(
    r"[?？�]|無法辨識|无法辨识|看不清|不清楚|未辨識|未辨识|遮擋|遮挡|裁切"
    r"|\b(?:unknown|unreadable|null|none|n/?a)\b", re.I
)


def _plain(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    return unicodedata.normalize("NFKC", value).strip().strip("*` ")


def _header_only(value: str) -> bool:
    return not re.sub(r"[\s/:：*`_\-()]+", "", _HEADER_FRAGMENTS.sub("", value))


def _value(value: str, names: Iterable[str]) -> str | None:
    value = _plain(value).strip(" :/\"'*")
    if not value or _UNCERTAIN.search(value) or _header_only(value):
        return None
    # Check complete names before interpreting words such as NAME in a name.
    canonical = resolve_storage_customer(value, names)
    if canonical:
        return canonical
    if value.startswith("(") and value.endswith(")"):
        return _value(value[1:-1], names)
    if _OTHER_LABEL.search(value) or re.fullmatch(r"[\d\s.,+\-/]+", value):
        return None
    if len(value) < 2 or len(value) > 100:
        return None
    return value


def _inline_value(cell: str, label: re.Match, names: Iterable[str]) -> str | None:
    tail = cell[label.end():].strip(" :/\"'*")
    # Bilingual labels may follow the Chinese label in the same cell.
    while tail:
        if resolve_storage_customer(tail, names):
            return _value(tail, names)
        header_tail = tail.lstrip("( ")
        fragment = _HEADER_FRAGMENTS.match(header_tail)
        if not fragment:
            break
        tail = header_tail[fragment.end():].lstrip(" :/)\"'*")
    # A second field in the same line must not become part of the name.
    boundary = _OTHER_LABEL.search(tail)
    if boundary:
        tail = tail[:boundary.start()]
    return _value(tail, names)


def _cells(line: str) -> list[str]:
    line = _plain(line)
    if "|" in line:
        # Remove only Markdown's outer border, preserving internal empty cells.
        if line.startswith("|") and line.endswith("|"):
            line = line[1:]
            line = line[:-1]
        return [_plain(cell) for cell in line.split("|")]
    if "\t" in line:
        return [_plain(cell) for cell in line.split("\t")]
    return [line]


def _table_customer(rows: list[list[str]], row_no: int, col: int,
                    names: Iterable[str]) -> str | None:
    header = rows[row_no]
    for row in rows[row_no + 1:row_no + 11]:
        if all(not cell or re.fullmatch(r"[:\-\s]+", cell) for cell in row):
            continue
        # Missing separators make the column uncertain: never shift left/right.
        if len(row) != len(header):
            break
        cell = row[col]
        if _header_only(cell) and cell:
            continue  # Nama / Pelanggan or a repeated bilingual header row.
        # An empty/uncertain customer cell is data, not permission to scan into
        # the next section (product size, MC, material, die number, etc.).
        return _value(cell, names)
    return None


def _extract_customer(text: str, names: Iterable[str]) -> str | None:
    rows = [_cells(line) for line in text.splitlines() if line.strip()]
    candidates = set()
    unresolved = False
    for row_no, row in enumerate(rows):
        for col, cell in enumerate(row):
            label = _CUSTOMER_LABEL.match(cell)
            if not label:
                continue
            candidate = _inline_value(cell, label, names)
            if candidate is None and _header_only(cell):
                if len(row) > 1:
                    # Also support a two-column key/value OCR table.
                    if len(row) == 2 and col == 0 and not _OTHER_LABEL.search(row[1]):
                        candidate = _value(row[1], names)
                    else:
                        candidate = _table_customer(rows, row_no, col, names)
                else:
                    for following in rows[row_no + 1:row_no + 6]:
                        if len(following) != 1:
                            break
                        next_cell = following[0]
                        if _header_only(next_cell):
                            continue
                        candidate = _value(next_cell, names)
                        break
            if candidate:
                candidates.add(candidate)
            else:
                unresolved = True
    # Multiple work orders, conflicting readings or unreadable customer fields
    # cannot safely produce a single automatic storage answer.
    if unresolved or len(candidates) != 1:
        return None
    return next(iter(candidates))


def analyze_work_order_text(ocr_text: str | None,
                            customer_names: Iterable[str] = ()) -> dict:
    if not isinstance(ocr_text, str) or not ocr_text.strip():
        return {"is_work_order": False, "customer": None, "keyword_count": 0}
    normalized = _key(ocr_text)
    fields = {field for field, aliases in _FIELD_FAMILIES.items()
              if any(alias in normalized for alias in aliases)}
    is_work_order = (
        ("title" in fields and len(fields) >= 2)
        or (len(fields) >= 3 and bool(fields & {"order", "material", "product_mc"}))
    )
    return {
        "is_work_order": is_work_order,
        "customer": _extract_customer(ocr_text, tuple(customer_names)) if is_work_order else None,
        "keyword_count": len(fields),
    }
