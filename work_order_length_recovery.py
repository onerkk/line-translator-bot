"""One source-bound vision retry for missing finished-bar lengths.

The first OCR remains authoritative for every non-length field.  A focused
second reading only supplies MIN and MAX if neither reading contradicts the
other and the current customer table maps the full interval to one area.
"""

from __future__ import annotations

import re

from work_order_query import _decimal, _read_fields, extract_work_order_info


_FOCUSED_LINE = re.compile(r"^(?:成品)?長度\s*(MIN|MAX)\s*[:：]\s*(.*?)\s*$", re.I)
_SOURCE_LINE = re.compile(
    r"^(?:成品)?長度\s*(MIN|MAX)(?:\s+Panjang\s*(?:MIN|MAX))?\s*[:：]\s*(.*?)\s*$",
    re.I,
)
_UNKNOWN = re.compile(r"^(?:[?？�\-—]|未辨識|未辨识|無法辨識|无法辨识|看不清|unreadable|unknown)$", re.I)


def needs_length_retry(ocr_text, storage_lookup):
    """Spend one vision call only when a real customer has missing length."""
    if not isinstance(ocr_text, str) or not ocr_text.strip():
        return False
    info = extract_work_order_info(ocr_text, storage_lookup, {})
    return (info.get("is_work_order") is True
            and bool(info.get("customer"))
            and info.get("storage", {}).get("status") == "unknown_length")


def _focused_pair(text):
    """Accept precisely two labeled numbers; prose or a third value fails."""
    if not isinstance(text, str):
        return None
    found = {}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 2:
        return None
    for line in lines:
        match = _FOCUSED_LINE.fullmatch(line)
        if not match:
            return None
        field, raw = match.group(1).lower(), match.group(2)
        value = _decimal(raw)
        if field in found or value is None or value <= 0:
            return None
        found[field] = value
    if set(found) != {"min", "max"} or found["max"] < found["min"]:
        return None
    return found


def _original_lines(ocr_text, pair):
    """Remove only independent, checked length key/value lines.

    Existing readable values must agree with the retry.  Unknown markers can
    be replaced.  A malformed or ambiguous original number is never erased.
    Other table cells remain intact and are checked by the final full parser.
    """
    kept = []
    for line in ocr_text.splitlines():
        match = _SOURCE_LINE.fullmatch(line.strip())
        if not match:
            kept.append(line)
            continue
        field, raw = match.group(1).lower(), match.group(2).strip()
        if raw and not _UNKNOWN.fullmatch(raw):
            known = _decimal(raw)
            if known is None or known != pair[field]:
                return None
        # Replaced below by one verified MIN and MAX each.
    return kept


def merge_confirmed_length(ocr_text, retry_text, storage_lookup):
    """Return original OCR unchanged unless a single area is proven."""
    if not needs_length_retry(ocr_text, storage_lookup):
        return ocr_text
    pair = _focused_pair(retry_text)
    if not pair:
        return ocr_text
    original_fields = _read_fields(ocr_text)
    for field, key in (("length_min", "min"), ("length_max", "max")):
        old = original_fields.get(field)
        if old is None:
            continue
        number = _decimal(old)
        if number is None or number != pair[key]:
            return ocr_text
    kept = _original_lines(ocr_text, pair)
    if kept is None:
        return ocr_text
    candidate = "\n".join(kept + [
        f"長度MIN：{pair['min']}", f"長度MAX：{pair['max']}"
    ])
    before = extract_work_order_info(ocr_text, storage_lookup, {})
    after = extract_work_order_info(candidate, storage_lookup, {})
    if (after.get("storage", {}).get("status") != "ok"
            or after.get("customer") != before.get("customer")
            or after.get("length") != (pair["min"], pair["max"])):
        return ocr_text
    for field in ("order", "flow", "diameter_min", "diameter_max",
                  "paint", "color", "packaging", "special", "order_note",
                  "ring_on_form"):
        if before.get("fields", {}).get(field) != after.get("fields", {}).get(field):
            return ocr_text
    return candidate
