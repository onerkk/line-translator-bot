"""Retry and safely merge critical work-order cells from rotated photo views."""

from __future__ import annotations

import base64
import io
import re

from packaging_lookup import normalize_code
from work_order_query import _field_of, _packaging, _read_fields, extract_work_order_info


_LABELS = {
    "paint": "噴漆位置",
    "ring_on_form": "套環",
    "packaging": "包裝代碼",
}
_SOURCE_LINE = re.compile(r"^\s*([^:：|\t]{1,55})\s*[:：]\s*(.*?)\s*$")
_UNREADABLE = re.compile(
    r"^(?:[?？�]|未辨識|未辨识|無法辨識|无法辨识|看不清|不清楚|unreadable|unknown|n/?a|null)$",
    re.I,
)
_POSITION_VALUES = {
    "N", "NO", "（空白）", "不噴", "不喷", "不噴漆", "不喷漆", "NONE", "TIDAKDICAT",
    "TIDAKADA", "TIDAKDISEMPROTCAT", "單邊", "單側", "一端", "单边", "单侧",
    "雙邊", "兩邊", "双边", "两边", "雙側", "双侧", "兩端", "两端",
    "SATUSISI", "SATUUJUNG", "DUASISI", "KEDUAUJUNG",
}


def _position_key(value):
    return re.sub(r"\s+", "", str(value or "").strip()).upper()


def _unreadable(value):
    text = str(value or "").strip()
    return not text or bool(_UNREADABLE.fullmatch(text))


def _field_values(text):
    rows = {field: [] for field in _LABELS}
    indexes = {field: [] for field in _LABELS}
    for index, line in enumerate(str(text or "").splitlines()):
        match = _SOURCE_LINE.fullmatch(line.strip())
        if not match:
            continue
        field = _field_of(match.group(1))
        if field in rows:
            rows[field].append(match.group(2).strip())
            indexes[field].append(index)
    return rows, indexes


def needs_cell_retry(ocr_text, packaging_lookup=None):
    """Retry only when position/ring is unreadable or packaging cannot match."""
    if not isinstance(ocr_text, str) or not ocr_text.strip():
        return False
    info = extract_work_order_info(ocr_text, {}, packaging_lookup or {})
    if not info.get("is_work_order"):
        return False
    fields = info.get("fields", {})
    paint = _position_key(fields.get("paint"))
    ring = _position_key(fields.get("ring_on_form"))
    return (paint not in _POSITION_VALUES
            or ring not in {"Y", "N"}
            or info.get("packaging", {}).get("status") != "ok")


def orientation_candidates(image_base64):
    """Return enhanced clockwise/counter-clockwise views; callers send the
    original too, so vision can pick the upright table without assuming that
    every portrait photo contains a sideways document.
    """
    try:
        from PIL import Image, ImageEnhance, ImageOps

        binary = base64.b64decode(image_base64, validate=True)
        with Image.open(io.BytesIO(binary)) as source:
            source = ImageOps.exif_transpose(source).convert("RGB")
            if min(source.size) < 160:
                return []
            candidates = []
            for angle in (90, 270):
                view = source.rotate(angle, expand=True)
                longest = max(view.size)
                scale = min(2.0, 2400 / longest)
                if scale > 1:
                    view = view.resize((int(view.width * scale),
                                        int(view.height * scale)),
                                       Image.Resampling.LANCZOS)
                view = ImageOps.autocontrast(view, cutoff=0.2)
                view = ImageEnhance.Sharpness(view).enhance(1.18)
                output = io.BytesIO()
                view.save(output, format="JPEG", quality=91, optimize=True)
                candidates.append({
                    "rotation": angle,
                    "base64": base64.b64encode(output.getvalue()).decode("ascii"),
                })
            return candidates
    except (OSError, ValueError, TypeError, ImportError, base64.binascii.Error):
        return []


def _focused_values(retry_text, packaging_lookup):
    if not isinstance(retry_text, str):
        return {}
    values = {}
    seen = set()
    duplicates = set()
    for line in retry_text.splitlines():
        match = _SOURCE_LINE.fullmatch(line.strip())
        if not match:
            continue
        field = _field_of(match.group(1))
        if field not in _LABELS:
            continue
        if field in seen:
            duplicates.add(field)
            continue
        seen.add(field)
        value = match.group(2).strip()
        if field == "paint":
            if _position_key(value) in _POSITION_VALUES:
                values[field] = value
        elif field == "ring_on_form":
            if _position_key(value) in {"Y", "N"}:
                values[field] = _position_key(value)
        elif field == "packaging":
            code = normalize_code(value)
            if (re.fullmatch(r"[A-Z0-9]{1,12}", code)
                    and _packaging(code, packaging_lookup or {}).get("status") == "ok"):
                values[field] = code
    return {field: value for field, value in values.items() if field not in duplicates}


def merge_confirmed_cells(ocr_text, retry_text, packaging_lookup=None):
    """Fill unreadable cells and repair only codes absent from the live table.

    Readable Y/N and position cells are never replaced. A retry cannot resolve
    duplicate source labels, and a package correction must match one live row.
    """
    if not isinstance(ocr_text, str) or not ocr_text.strip():
        return ocr_text
    lookup = packaging_lookup or {}
    candidates = _focused_values(retry_text, lookup)
    if not candidates:
        return ocr_text

    source_rows, source_indexes = _field_values(ocr_text)
    parsed = _read_fields(ocr_text)
    replacements = {}
    additions = []
    for field, candidate in candidates.items():
        occurrences = source_rows[field]
        if len(occurrences) > 1:
            continue
        if not occurrences:
            # If an aligned table already yielded a readable value, do not
            # append a second, potentially contradictory value.
            if parsed.get(field):
                continue
            additions.append(f"{_LABELS[field]}：{candidate}")
            continue

        existing = occurrences[0]
        if field == "paint":
            replaceable = _unreadable(existing)
        elif field == "ring_on_form":
            replaceable = _unreadable(existing)
        else:
            replaceable = (_packaging(existing, lookup).get("status") != "ok")
        if not replaceable:
            continue
        replacements[source_indexes[field][0]] = f"{_LABELS[field]}：{candidate}"

    if not replacements and not additions:
        return ocr_text
    lines = ocr_text.splitlines()
    for index, line in replacements.items():
        lines[index] = line
    lines.extend(additions)
    return "\n".join(lines)
