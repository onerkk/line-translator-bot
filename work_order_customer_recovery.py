"""Focused source reread for a missing, unknown, or conflicting customer cell.

Storage uses the printed customer-name column only.  A recipient is an
independent OCR cross-check and is never substituted as the customer.
"""

from __future__ import annotations

import base64
import io
import re
import unicodedata

from work_order_detection import (_extract_customer, _extract_recipient,
                                 analyze_work_order_text,
                                 resolve_storage_customer)


_UNKNOWN = re.compile(
    r"^(?:[?？�\-—]|未辨識|未辨识|無法辨識|无法辨识|看不清|unreadable|unknown|n/?a)$",
    re.I,
)
_SOURCE_LINE = re.compile(r"^\s*([^:：|\t]{1,55})\s*[:：]\s*(.*?)\s*$")
_CUSTOMER_LABELS = {"客戶名稱", "客户名称", "namapelanggan", "customername", "customer"}
_RECIPIENT_LABELS = {"收貨人", "收货人", "penerimabarang", "consignee"}


def _label_key(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s/():：.·_\-]+", "", value)


def _source_field(label):
    key = _label_key(label)
    if key in _CUSTOMER_LABELS:
        return "customer"
    if key in _RECIPIENT_LABELS:
        return "recipient"
    # OCR can preserve the Chinese and Indonesian header in one label.
    if key.startswith("客戶名稱") and key[len("客戶名稱"):] == "namapelanggan":
        return "customer"
    if key.startswith("客户名称") and key[len("客户名称"):] == "namapelanggan":
        return "customer"
    if key.startswith("收貨人") and key[len("收貨人"):] == "penerimabarang":
        return "recipient"
    if key.startswith("收货人") and key[len("收货人"):] == "penerimabarang":
        return "recipient"
    return None


def focused_customer_crop(image_base64):
    """Magnify the printed customer/recipient header row without editing it."""
    try:
        from PIL import Image, ImageEnhance, ImageOps

        binary = base64.b64decode(image_base64, validate=True)
        with Image.open(io.BytesIO(binary)) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            width, height = image.size
            if min(width, height) < 300:
                return None
            # Both supported work-order layouts put customer/recipient in the
            # first information block, before size, process and storage data.
            bounds = ((0.19, 0.23, 0.45, 0.45) if width > height * 1.15
                      else (0.20, 0.08, 0.56, 0.27))
            x0, y0, x1, y1 = bounds
            image = image.crop((int(x0 * width), int(y0 * height),
                                int(x1 * width), int(y1 * height)))
            if image.width < 140 or image.height < 65:
                return None
            image = ImageOps.autocontrast(image, cutoff=0.5)
            scale = min(4.0, 1800 / image.width, 900 / image.height)
            if scale > 1:
                image = image.resize((int(image.width * scale),
                                      int(image.height * scale)),
                                     Image.Resampling.LANCZOS)
            image = ImageEnhance.Sharpness(image).enhance(1.3)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=92, optimize=True)
            return base64.b64encode(buffer.getvalue()).decode("ascii")
    except (OSError, ValueError, TypeError, ImportError, base64.binascii.Error):
        return None


def needs_customer_retry(ocr_text, storage_lookup):
    """Reread only when the customer source is missing, unmapped, or conflicts."""
    if not isinstance(ocr_text, str) or not ocr_text.strip():
        return False
    names = tuple(storage_lookup) if isinstance(storage_lookup, dict) else ()
    analysis = analyze_work_order_text(ocr_text, names)
    if not analysis.get("is_work_order"):
        return False
    candidate = analysis.get("customer_candidate")
    return (bool(analysis.get("customer_conflict"))
            or not candidate
            or resolve_storage_customer(candidate, names) is None)


def _focused_values(retry_text, names):
    """Require exactly one explicitly labeled customer and recipient cell."""
    if not isinstance(retry_text, str):
        return None
    lines = [line.strip() for line in retry_text.splitlines() if line.strip()]
    if len(lines) != 2:
        return None
    values = {}
    for line in lines:
        match = _SOURCE_LINE.fullmatch(line)
        if not match:
            return None
        label, raw = match.group(1), match.group(2).strip()
        field = _source_field(label)
        if field is None or field in values or not raw or _UNKNOWN.fullmatch(raw):
            return None
        if len(raw) > 100 or any(char in "\r\n|\t" for char in raw):
            return None
        values[field] = raw
    if set(values) != {"customer", "recipient"}:
        return None
    canonical = resolve_storage_customer(values["customer"], names)
    if canonical is None:
        return None
    return {"customer": canonical, "recipient": values["recipient"]}


def _source_lines_without_names(ocr_text):
    """Remove only standalone name key/value lines; refuse positional tables."""
    kept = []
    removed = False
    for line in ocr_text.splitlines():
        if "|" in line or "\t" in line:
            cells = re.split(r"[|\t]", line)
            if any(_source_field(cell) for cell in cells):
                return None
            kept.append(line)
            continue
        match = _SOURCE_LINE.fullmatch(line)
        if match and _source_field(match.group(1)):
            removed = True
            continue
        kept.append(line)
    return kept, removed


def merge_confirmed_customer(ocr_text, retry_text, storage_lookup):
    """Use the focused customer cell only after resolving the OCR conflict.

    If the first OCR saw two different names, the focused read must either
    reaffirm that customer or show that both printed columns contain the
    recipient value.  An unresolved conflict leaves the customer unset so no
    plausible but wrong storage mapping is emitted.
    """
    if not needs_customer_retry(ocr_text, storage_lookup):
        return ocr_text
    names = tuple(storage_lookup) if isinstance(storage_lookup, dict) else ()
    focused = _focused_values(retry_text, names)
    if focused is None:
        return ocr_text

    original_customer = _extract_customer(ocr_text, names)
    original_recipient = _extract_recipient(ocr_text, names)
    original = analyze_work_order_text(ocr_text, names)
    if original.get("customer_conflict") and original_customer:
        recipient = (resolve_storage_customer(focused["recipient"], names)
                     or focused["recipient"])
        if resolve_storage_customer(original_customer, names):
            same_customer = _label_key(original_customer) == _label_key(focused["customer"])
            confirms_recipient = (
                original_recipient is not None
                and _label_key(original_recipient) == _label_key(focused["customer"])
                and _label_key(recipient) == _label_key(focused["customer"])
            )
            if not (same_customer or confirms_recipient):
                return ocr_text
        elif not (_label_key(recipient) == _label_key(focused["customer"])
                  or (original_recipient is not None
                      and _label_key(original_recipient) == _label_key(focused["customer"]))):
            return ocr_text
    elif (original_customer
          and resolve_storage_customer(original_customer, names)
          and _label_key(original_customer) != _label_key(focused["customer"])):
        return ocr_text

    stripped = _source_lines_without_names(ocr_text)
    if stripped is None:
        return ocr_text
    kept, _removed = stripped
    candidate = "\n".join(kept + [f"客戶名稱：{focused['customer']}"])
    after = analyze_work_order_text(candidate, names)
    if (not after.get("is_work_order")
            or after.get("customer") != focused["customer"]
            or after.get("customer_conflict")):
        return ocr_text
    return candidate


def withhold_conflicting_customer(ocr_text, storage_lookup):
    """Fail closed on a customer/recipient mismatch if no focused read resolved it."""
    names = tuple(storage_lookup) if isinstance(storage_lookup, dict) else ()
    analysis = analyze_work_order_text(ocr_text, names)
    if not analysis.get("is_work_order") or not analysis.get("customer_conflict"):
        return ocr_text
    stripped = _source_lines_without_names(ocr_text)
    if stripped is None:
        # An unresolved positional table plus an explicit unreadable customer
        # makes the normal parser reject the customer instead of shifting cells.
        return ocr_text.rstrip() + "\n客戶名稱：?"
    kept, _removed = stripped
    return "\n".join(kept + ["客戶名稱：?"])
