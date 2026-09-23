"""Bounded, source-based work-order diagnostics without photos or raw OCR.

Only the explicit fields in ``_sanitize_record`` can reach the JSON file.
The LINE request path may call every public function without handling errors;
diagnostics are best-effort and must never prevent a reply.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

try:
    import fcntl
except ImportError:  # Render uses Linux; keep a same-process fallback elsewhere.
    fcntl = None


_MAX_RECORDS = 500
_LOCAL_LOCK = threading.RLock()
_MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,96}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_CUSTOMER = re.compile(r"[\w\u3400-\u9fff &.%()（）/+\-]{1,100}\Z")
_FLOW = re.compile(r"[A-Za-z0-9?？ -]{1,40}\Z")
_NUMBER = re.compile(r"(?:[0-9.,+\- ]{1,24}(?:mm|毫米|公厘)?|[?？])\Z", re.I)
_RULE = re.compile(r"[A-Za-z0-9.<>≤≥=/ （）含以上以下~+\-]{1,80}\Z")
_AREA = re.compile(r"[A-Za-z0-9、，,/_\- ]{1,120}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_STORAGE_STATUS = {
    "ok", "unknown_customer", "no_mapping", "unknown_length",
    "invalid_mapping", "ambiguous_mapping", "unmapped_length", "not_work_order",
}
_STORAGE_REASON = _STORAGE_STATUS | {
    "unique_area", "missing_or_unreadable_length", "invalid_or_ambiguous_length",
    "unresolved_customer", "customer_not_in_live_table_or_ambiguous_prefix",
}
_RING_STATUS = {"yes", "no", "unknown", "not_work_order"}
_RING_REASON = {
    "explicit_note", "packaging_material", "size_rule", "flow", "diameter",
    "invalid_diameter", "threshold_crossing", "not_work_order",
}
_RETRY_KEYS = (
    "length_retry_triggered", "length_retry_accepted",
    "ring_retry_triggered", "ring_retry_accepted",
)


def _diagnostics_path() -> Path:
    configured = os.environ.get("WORK_ORDER_DIAGNOSTICS_PATH", "").strip()
    if configured:
        return Path(configured)
    persistent = Path("/var/data")
    directory = persistent if persistent.is_dir() and os.access(persistent, os.W_OK | os.X_OK) else Path(tempfile.gettempdir())
    return directory / "work_order_diagnostics.json"


def diagnostics_health():
    """Expose recording availability without returning an internal filesystem path."""
    try:
        path = _diagnostics_path()
        target = path if path.exists() else path.parent
        return {
            "writable": (target.is_file() if path.exists() else target.is_dir())
                        and os.access(target, os.W_OK),
            "persistent_disk": path.resolve().is_relative_to(Path("/var/data").resolve())
                               and Path("/var/data").is_dir(),
            "retention": _MAX_RECORDS,
        }
    except OSError:
        return {"writable": False, "persistent_disk": False, "retention": _MAX_RECORDS}


def _safe(value, pattern):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if pattern.fullmatch(value) else None


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_ocr_field(value, pattern, *, customer=False):
    value = _safe(value, pattern)
    if value is None:
        return None
    # A misplaced order number is not safe to retain as a customer or flow.
    # A real customer such as DACAPO/方鉦 survives; malformed CHRAPD6L is
    # retained so the operator can see exactly why the reread ran.
    if (customer and any(character.isdigit() for character in value)) or re.search(r"\d{3,}", value):
        return None
    return value


def _safe_ocr_number(value, *, length=False):
    value = _safe(value, _NUMBER)
    if value is None or value in {"?", "？"}:
        return value
    # An OCR column shift can place a numeric order ID into a length or size
    # cell.  Keep only plausible measured values in the admin trace.  This
    # does not change the actual work-order rule or its input.
    raw = re.sub(r"\s*(?:mm|毫米|公厘)$", "", value, flags=re.I).strip()
    if len(re.sub(r"\D", "", raw)) > 7:
        return None
    try:
        number = Decimal(raw.replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        return None
    if not (0 <= number <= (50000 if length else 5000)):
        return None
    return value


def _digest(value):
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()
    except Exception:
        return None


def _safe_rows(rows):
    result = []
    if isinstance(rows, list):
        for row in rows[:_MAX_RECORDS]:
            if isinstance(row, (tuple, list)) and len(row) == 2:
                rule = _safe(str(row[0]), _RULE)
                area = _safe(str(row[1]), _AREA)
                if rule and area and not re.search(r"\d{4,}", area):
                    result.append([rule, area])
    return result


def _sanitize_record(record):
    """Apply a strict whitelist again even to records supplied by callers."""
    if not isinstance(record, dict):
        return None
    message_id = _safe(record.get("message_id"), _MESSAGE_ID)
    if not message_id:
        return None
    storage_status = record.get("storage_status")
    ring_status = record.get("ring_status")
    ring_reason = record.get("ring_reason")
    storage_reason = record.get("storage_reason")
    result = {
        "message_id": message_id,
        "timestamp_utc": _safe(record.get("timestamp_utc"), _TIMESTAMP),
        "stage": _safe(record.get("stage"), _TOKEN),
        "build_tag": _safe(record.get("build_tag"), _TOKEN),
        "ocr_sha256": _safe(record.get("ocr_sha256"), _DIGEST),
        "storage_table_sha256": _safe(record.get("storage_table_sha256"), _DIGEST),
        "packaging_table_sha256": _safe(record.get("packaging_table_sha256"), _DIGEST),
        "customer_ocr": _safe_ocr_field(record.get("customer_ocr"), _CUSTOMER, customer=True),
        "customer_canonical": _safe(record.get("customer_canonical"), _CUSTOMER),
        "flow_ocr": _safe_ocr_field(record.get("flow_ocr"), _FLOW),
        "diameter_min": _safe_ocr_number(record.get("diameter_min")),
        "diameter_max": _safe_ocr_number(record.get("diameter_max")),
        "length_min": _safe_ocr_number(record.get("length_min"), length=True),
        "length_max": _safe_ocr_number(record.get("length_max"), length=True),
        "storage_rows": _safe_rows(record.get("storage_rows")),
        "storage_status": storage_status if storage_status in _STORAGE_STATUS else None,
        "storage_area": _safe(record.get("storage_area"), _AREA),
        "storage_reason": storage_reason if storage_reason in _STORAGE_REASON else None,
        "ring_status": ring_status if ring_status in _RING_STATUS else None,
        "ring_reason": ring_reason if ring_reason in _RING_REASON else None,
    }
    for key in _RETRY_KEYS:
        value = record.get(key)
        result[key] = value if isinstance(value, bool) else None
    return result


def make_diagnostic(ocr_text, storage_lookup, packaging_lookup, msg_id,
                    stage, build_tag, extra=None):
    """Build a redacted record from the actual OCR decisions and live lookups.

    ``extra`` accepts only four retry booleans; arbitrary extras cannot enter
    the returned record or its persistent file. The customer rows are read from
    the exact canonical key in the active storage lookup, never from examples.
    """
    try:
        from work_order_detection import _extract_customer
        from work_order_query import _read_fields, extract_work_order_info

        ocr_text = ocr_text if isinstance(ocr_text, str) else ""
        storage_lookup = storage_lookup if isinstance(storage_lookup, dict) else {}
        packaging_lookup = packaging_lookup if isinstance(packaging_lookup, dict) else {}
        fields = _read_fields(ocr_text)
        info = extract_work_order_info(ocr_text, storage_lookup, packaging_lookup, paint_codes={})
        is_order = bool(info.get("is_work_order"))
        storage = info.get("storage") or {}
        ring = info.get("ring") or {}
        canonical = storage.get("customer") if is_order else None
        if canonical not in storage_lookup:
            canonical = None
        status = storage.get("status") if is_order else "not_work_order"
        reason = status
        if status == "ok":
            reason = "unique_area"
        elif status == "unknown_customer":
            reason = ("customer_not_in_live_table_or_ambiguous_prefix"
                      if _extract_customer(ocr_text, ()) else "unresolved_customer")
        elif status == "unknown_length":
            reason = ("missing_or_unreadable_length"
                      if not fields.get("length_min") or not fields.get("length_max")
                      else "invalid_or_ambiguous_length")
        record = {
            "message_id": msg_id,
            "timestamp_utc": _utc_now(),
            "stage": stage,
            "build_tag": build_tag,
            "ocr_sha256": hashlib.sha256(ocr_text.encode("utf-8")).hexdigest() if ocr_text else None,
            "storage_table_sha256": _digest(storage_lookup),
            "packaging_table_sha256": _digest(packaging_lookup),
            "customer_ocr": _extract_customer(ocr_text, ()),
            "customer_canonical": canonical,
            "flow_ocr": fields.get("flow"),
            "diameter_min": fields.get("diameter_min"),
            "diameter_max": fields.get("diameter_max"),
            "length_min": fields.get("length_min"),
            "length_max": fields.get("length_max"),
            "storage_rows": storage_lookup.get(canonical) if canonical else [],
            "storage_status": status,
            "storage_area": storage.get("area"),
            "storage_reason": reason,
            "ring_status": ring.get("status") if is_order else "not_work_order",
            "ring_reason": ring.get("reason") if is_order else "not_work_order",
        }
        if isinstance(extra, dict):
            record.update({key: extra.get(key) for key in _RETRY_KEYS})
        return _sanitize_record(record)
    except Exception:
        # Parsing and diagnostics must never make image processing fail.
        return _sanitize_record({"message_id": msg_id, "stage": stage, "build_tag": build_tag})


@contextmanager
def _locked(path, *, shared=False):
    with _LOCAL_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = path.with_name(path.name + ".lock")
        descriptor = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _read_records(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    # Sanitize legacy and externally modified files before returning or writing.
    return [clean for item in data[-_MAX_RECORDS:]
            if (clean := _sanitize_record(item)) is not None]


def append_diagnostic(record):
    """Atomically append one sanitized record; return False on any I/O error."""
    clean = _sanitize_record({**record, "timestamp_utc": record.get("timestamp_utc") or _utc_now()}) if isinstance(record, dict) else None
    if clean is None:
        return False
    temp_name = None
    try:
        path = _diagnostics_path()
        with _locked(path):
            rows = (_read_records(path) + [clean])[-_MAX_RECORDS:]
            descriptor, temp_name = tempfile.mkstemp(prefix=".work_order_diag_", dir=str(path.parent))
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as writer:
                json.dump(rows, writer, ensure_ascii=False, separators=(",", ":"))
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(temp_name, path)
            temp_name = None
        return True
    except Exception:
        return False
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def load_diagnostics(limit=20, msg_id=None):
    """Read newest first, optionally filtering by one sanitized LINE message ID."""
    try:
        limit = max(1, min(int(limit), _MAX_RECORDS))
        if msg_id is not None:
            msg_id = _safe(msg_id, _MESSAGE_ID)
            if msg_id is None:
                return []
        path = _diagnostics_path()
        with _locked(path, shared=True):
            records = _read_records(path)
        if msg_id is not None:
            records = [record for record in records if record["message_id"] == msg_id]
        return list(reversed(records[-limit:]))
    except Exception:
        return []
