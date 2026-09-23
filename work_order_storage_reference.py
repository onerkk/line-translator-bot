"""Conservative work-order lookup against the verified bundled customer table.

The admin storage sheet is authoritative for its existing customers.  A
historical shipped JSON mistakenly wrote the first, short-length band as
">=3200" for 397 customers.  Restore that band's original meaning only
when every other source row still matches the independently corrected bundled
reference.  An incomplete admin upload can also omit a known customer; for
work-order recognition, only, use that customer's verified bundled rows.
"""

from __future__ import annotations

import json
from pathlib import Path

from work_order_detection import _key as _customer_key


_REFERENCE_PATH = Path(__file__).with_name("storage_reference.json")


def load_storage_reference():
    """Return an independent source table, untouched by admin Excel uploads."""
    try:
        data = json.loads(_REFERENCE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


STORAGE_REFERENCE = load_storage_reference()


def effective_work_order_storage_lookup(live, reference=None):
    """Prefer current admin rows; repair only exact verified legacy mistakes.

    A blank or custom row for an existing customer remains a deliberate admin
    setting.  Missing customer names in a partial upload are filled from the
    reference without changing the live mapping or its persisted JSON.
    """
    reference = STORAGE_REFERENCE if reference is None else reference
    source = live if isinstance(live, dict) else {}
    if not isinstance(reference, dict) or not reference:
        return source
    # Case, full-width letters and spacing in an Excel customer name can
    # differ from the bundled spelling while still naming the same customer.
    # Do not resurrect the reference spelling beside an authoritative live
    # entry: doing so would make an exact OCR match choose the stale rules.
    live_names = {
        _customer_key(customer): customer for customer in source
        if isinstance(customer, str)
    }
    merged = {
        customer: rows for customer, rows in reference.items()
        if not isinstance(customer, str) or _customer_key(customer) not in live_names
    }
    merged.update(source)
    for customer, baseline in reference.items():
        live_name = live_names.get(_customer_key(customer)) if isinstance(customer, str) else customer
        current = source.get(live_name)
        if not isinstance(current, list) or not isinstance(baseline, list):
            continue
        if not current or not baseline or len(current) != len(baseline):
            continue
        if baseline[0][0] != "<=3200" or current[0] != [">=3200", baseline[0][1]]:
            continue
        if current[1:] == baseline[1:]:
            merged[live_name] = [list(baseline[0]), *current[1:]]
    return merged
