"""Source-bound second OCR reading for an incomplete work-order ring decision.

The printed ring Y/N is never used.  A focused reading can fill a missing
process or finished-size cell when missing or illegible, without replacing
readable valid cells; the normal work-order rules then decide the ring.
"""

from __future__ import annotations

import base64
import io
import re

from work_order_query import (_cells, _field_of, _number_candidates,
                              _read_fields, extract_work_order_info)


_FIELDS = ("flow", "diameter_min", "diameter_max")
_LABELS = {"flow": "訂單流程", "diameter_min": "成品尺寸MIN", "diameter_max": "成品尺寸MAX"}
_SOURCE_LINE = re.compile(r"^\s*([^:：|\t]{1,55})\s*[:：]\s*(.*?)\s*$")
_UNKNOWN = re.compile(
    r"^(?:[?？�\-—]|未辨識|未辨识|無法辨識|无法辨识|看不清|unreadable|unknown)$",
    re.I,
)
_PROCESS = re.compile(r"[A-Za-z]+(?: +[A-Za-z]+)*")


def _invalid_flow(raw):
    """An OCR digit in an otherwise complete process is not a valid suffix."""
    if not raw or _UNKNOWN.fullmatch(raw):
        return False
    compact = re.sub(r"\s+", "", raw).upper()
    return bool(re.fullmatch(r"[A-Z0-9]{3,}", compact)
                and any(c.isdigit() for c in compact)
                and (compact.endswith(("L", "D")) or compact[-1].isdigit()))


def _invalid_size(raw):
    """A numeric-looking cell may contain a letter mistaken for a digit."""
    if not raw or _UNKNOWN.fullmatch(raw):
        return False
    if _number_candidates(raw):
        return False
    value = re.sub(r"\s*(?:mm|毫米|公厘)$", "", raw, flags=re.I).strip()
    return bool(re.fullmatch(r"[0-9][0-9., ]*[A-Za-z][0-9., ]*", value))


def _replaceable_invalid(field, old, new):
    """Require exactly one OCR character to be repaired by the full reread."""
    if field == "flow":
        if not _invalid_flow(old):
            return False
        original = re.sub(r"\s+", "", old).upper()
        candidate = re.sub(r"\s+", "", new).upper()
        changes = [(index, left, right) for index, (left, right) in
                   enumerate(zip(original, candidate)) if left != right]
        if len(original) != len(candidate) or len(changes) != 1:
            return False
        index, old_char, new_char = changes[0]
        # Only the two characters deciding GL versus L may be promoted from
        # a malformed OCR reading.  A guess such as 6 -> B would silently
        # change an 18 mm grinding bar into a polishing bar.
        return ((index == len(original) - 2 and original.endswith("L")
                 and old_char.isdigit() and new_char == "G"
                 and candidate.endswith("GL"))
                or (index == len(original) - 1 and old_char == "1"
                    and new_char == "L" and original[-2] == "G"
                    and candidate.endswith("GL")))
    if not _invalid_size(old):
        return False
    original = re.sub(r"\s*(?:mm|毫米|公厘)$", "", old, flags=re.I).strip().upper()
    candidate = re.sub(r"\s*(?:mm|毫米|公厘)$", "", new, flags=re.I).strip().upper()
    changes = [(left, right) for left, right in zip(original, candidate)
               if left != right]
    return (len(original) == len(candidate) and len(changes) == 1
            and changes[0][0].isalpha() and changes[0][1].isdigit())


def focused_ring_crop(image_base64):
    """Magnify the top table containing finished size and process, if usable.

    The caller should send the original photograph alongside the crop; crop
    coordinates alone never prove which table column contains the number.
    """
    try:
        from PIL import Image, ImageEnhance, ImageOps

        binary = base64.b64decode(image_base64, validate=True)
        with Image.open(io.BytesIO(binary)) as photo:
            photo = ImageOps.exif_transpose(photo).convert("RGB")
            width, height = photo.size
            if min(width, height) < 300:
                return None
            bounds = ((0.08, 0.17, 0.97, 0.66) if width > height * 1.15
                      else (0.06, 0.12, 0.96, 0.63))
            x0, y0, x1, y1 = bounds
            photo = photo.crop((int(x0 * width), int(y0 * height),
                                int(x1 * width), int(y1 * height)))
            if photo.width < 180 or photo.height < 80:
                return None
            photo = ImageOps.autocontrast(photo, cutoff=0.5)
            scale = min(3.0, 2300 / photo.width, 1100 / photo.height)
            if scale > 1:
                photo = photo.resize((int(photo.width * scale),
                                      int(photo.height * scale)),
                                     Image.Resampling.LANCZOS)
            photo = ImageEnhance.Sharpness(photo).enhance(1.25)
            buffer = io.BytesIO()
            photo.save(buffer, format="JPEG", quality=90, optimize=True)
            return base64.b64encode(buffer.getvalue()).decode("ascii")
    except (OSError, ValueError, TypeError, ImportError, base64.binascii.Error):
        return None


def needs_ring_retry(ocr_text):
    """Retry only when missing or invalid OCR prevents the ring rule."""
    if not isinstance(ocr_text, str) or not ocr_text.strip():
        return False
    info = extract_work_order_info(ocr_text, {}, {})
    if not info.get("is_work_order"):
        return False
    ring = info.get("ring", {})
    if ring.get("status") != "unknown" or ring.get("reason") not in {"flow", "diameter"}:
        return False
    fields = info.get("fields", {})
    if ring["reason"] == "diameter":
        return any(not fields.get(field) or _UNKNOWN.fullmatch(fields[field])
                   or _invalid_size(fields[field])
                   for field in ("diameter_min", "diameter_max"))
    return (not fields.get("flow") or bool(_UNKNOWN.fullmatch(fields["flow"]))
            or _invalid_flow(fields["flow"]))


def _focused_fields(retry_text):
    """Accept three individually identified source cells, never free prose."""
    if not isinstance(retry_text, str):
        return None
    lines = [line.strip() for line in retry_text.splitlines() if line.strip()]
    if len(lines) != len(_FIELDS):
        return None
    values = {}
    for line in lines:
        match = _SOURCE_LINE.fullmatch(line)
        if not match:
            return None
        field = _field_of(match.group(1))
        if field not in _FIELDS or field in values:
            return None
        value = match.group(2).strip()
        if field == "flow":
            if not _PROCESS.fullmatch(value):
                return None
            flow = re.sub(r"\s+", "", value).upper()
            if len(flow) < 3 or not (flow.endswith("D") or flow.endswith("L")):
                return None
        else:
            if not re.fullmatch(r"\d+(?:[.,]\d+)?", value):
                return None
            if not _number_candidates(value):
                return None
        values[field] = value
    if set(values) != set(_FIELDS):
        return None
    if not any(0 < low <= high for low in _number_candidates(values["diameter_min"])
               for high in _number_candidates(values["diameter_max"])):
        return None
    return values


def _equal(field, old, new):
    if field == "flow":
        return re.sub(r"\s+", "", old).upper() == re.sub(r"\s+", "", new).upper()
    return _number_candidates(old) == _number_candidates(new)


def _safe_original(ocr_text, retry):
    """Keep valid values; replace unique, explicitly malformed source lines.

    The parser treats conflicting duplicate cells as missing.  Check each raw
    line so a retry cannot accidentally overwrite that conflict.
    """
    fields = _read_fields(ocr_text)
    remove = set()
    replacements = set()
    seen = {field: [] for field in _FIELDS}
    for index, line in enumerate(ocr_text.splitlines()):
        match = _SOURCE_LINE.fullmatch(line.strip())
        if match:
            field = _field_of(match.group(1))
            if field not in _FIELDS:
                continue
            seen[field].append(index)
            old = match.group(2).strip()
            if not old or _UNKNOWN.fullmatch(old):
                remove.add(index)
            elif not _equal(field, old, retry[field]):
                if not _replaceable_invalid(field, old, retry[field]):
                    return None
                remove.add(index)
                replacements.add(field)
        elif len(_cells(line)) > 1:
            # A tabular OCR cell may contain an unparsed conflicting value.
            # Preserve the whole original response rather than adding a new
            # key/value that could silently eclipse a misaligned table cell.
            if any(_field_of(cell) in _FIELDS for cell in _cells(line)):
                return None
    for field in _FIELDS:
        old = fields.get(field)
        if old is not None and not _equal(field, old, retry[field]) and field not in replacements:
            return None
        if old is None and seen[field]:
            # A second occurrence of a missing field could denote a duplicate
            # or conflicting form; a single explicit '?' can be repaired.
            if len(seen[field]) != 1 or seen[field][0] not in remove:
                return None
        if field in replacements and (len(seen[field]) != 1 or seen[field][0] not in remove):
            return None
    return remove, fields, replacements


def merge_confirmed_ring_fields(ocr_text, retry_text):
    """Merge a complete nonconflicting reread and let normal rules decide.

    This function does not inspect or use the printed Y/N and does not invent
    characters that either reading failed to see.  If safety checks fail it
    returns the first OCR verbatim, leaving the card at 待確認.
    """
    if not needs_ring_retry(ocr_text):
        return ocr_text
    retry = _focused_fields(retry_text)
    if retry is None:
        return ocr_text
    safe = _safe_original(ocr_text, retry)
    if safe is None:
        return ocr_text
    remove, original_fields, replacements = safe
    before = extract_work_order_info(ocr_text, {}, {})
    lines = [line for index, line in enumerate(ocr_text.splitlines()) if index not in remove]
    for field in _FIELDS:
        if not original_fields.get(field) or field in replacements:
            lines.append(f"{_LABELS[field]}：{retry[field]}")
    candidate = "\n".join(lines)
    after = extract_work_order_info(candidate, {}, {})
    if (after.get("ring", {}).get("status") not in {"yes", "no"}
            or after.get("is_work_order") is not True):
        return ocr_text
    for key in original_fields:
        if key not in _FIELDS and after.get("fields", {}).get(key) != original_fields[key]:
            return ocr_text
    if (before.get("customer") != after.get("customer")
            or before.get("length") != after.get("length")
            or before.get("packaging", {}).get("code") != after.get("packaging", {}).get("code")
            or before.get("paint", {}).get("status") != after.get("paint", {}).get("status")):
        return ocr_text
    return candidate
