"""One source-bound vision retry for missing finished-bar lengths.

The first OCR remains authoritative for every non-length field.  A focused
second reading only supplies MIN and MAX if neither reading contradicts the
other and the current customer table maps the full interval to one area.
"""

from __future__ import annotations

import base64
import io
import re

from work_order_query import _decimal, _read_fields, extract_work_order_info


_FOCUSED_LINE = re.compile(r"^(?:成品)?長度\s*(MIN|MAX)\s*[:：]\s*(.*?)\s*$", re.I)
_SOURCE_LINE = re.compile(
    r"^(?:成品)?長度\s*(MIN|MAX)(?:\s+Panjang\s*(?:MIN|MAX))?\s*[:：]\s*(.*?)\s*$",
    re.I,
)
_UNKNOWN = re.compile(r"^(?:[?？�\-—]|未辨識|未辨识|無法辨識|无法辨识|看不清|unreadable|unknown)$", re.I)


def focused_length_crop(image_base64):
    """Magnify the printed finished-length row for the single vision reread.

    This crop only supplies source pixels.  It never generates length numbers;
    merge_confirmed_length verifies both OCR readings against the live table.
    Return None for malformed images so the original photo can still be read.
    """
    try:
        from PIL import Image, ImageEnhance, ImageOps

        binary = base64.b64decode(image_base64, validate=True)
        with Image.open(io.BytesIO(binary)) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            width, height = image.size
            if min(width, height) < 300:
                return None
            # The finished-length MIN and adjacent MAX occupy the upper
            # middle of the same bilingual production form.  Include the
            # preceding cells and the following row so the vision model can
            # verify column identity instead of choosing a nearby number.
            bounds = ((0.24, 0.26, 0.93, 0.55) if width > height * 1.15
                      else (0.06, 0.13, 0.96, 0.61))
            x0, y0, x1, y1 = bounds
            image = image.crop((int(x0 * width), int(y0 * height),
                                int(x1 * width), int(y1 * height)))
            if image.width < 180 or image.height < 80:
                return None
            image = ImageOps.autocontrast(image, cutoff=0.5)
            scale = min(3.0, 2300 / image.width, 1050 / image.height)
            if scale > 1:
                image = image.resize((int(image.width * scale),
                                      int(image.height * scale)),
                                     Image.Resampling.LANCZOS)
            image = ImageEnhance.Sharpness(image).enhance(1.25)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=90, optimize=True)
            return base64.b64encode(buffer.getvalue()).decode("ascii")
    except (OSError, ValueError, TypeError, ImportError, base64.binascii.Error):
        return None


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
