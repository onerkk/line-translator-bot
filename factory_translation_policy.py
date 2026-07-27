"""Unified routing and acceptance policy for factory Chinese↔Indonesian.

The bot serves one production environment, so generic consumer-MT routing is
not an acceptable default.  This module is the single policy authority used by
text and OCR routes:

* Chinese↔Indonesian requests use the factory semantic route by default.
* stale lexical/vector TM and generic NMT cannot bypass the current contract.
* verified exact corrections remain eligible after deterministic validation.
* newly generated factory translations receive source-grounded review only when
  risk warrants it by default; review outages never veto a locally valid result.
* generic Google/NMT fallback is disabled unless explicitly enabled.

Operational overrides are environment variables so an incident can be handled
without editing code, but the production defaults deliberately favor semantic
accuracy over availability and latency.
"""
from __future__ import annotations

import os
from typing import Any, Dict

FACTORY_TRANSLATION_POLICY_API_VERSION = 4
FACTORY_TRANSLATION_POLICY_BUILD_ID = "2026-07-27.1-availability-resilient-adaptive-review"

_SUPPORTED = {("zh", "id"), ("id", "zh")}
_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled"}


def _lang(value: Any) -> str:
    low = str(value or "").strip().lower().replace("_", "-")
    if low.startswith("zh"):
        return "zh"
    if low.startswith("id"):
        return "id"
    return low.split("-", 1)[0]


def _boolean_env(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "1" if default else "0") or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return bool(default)


def supports_direction(src: Any, tgt: Any) -> bool:
    return (_lang(src), _lang(tgt)) in _SUPPORTED


def mode() -> str:
    value = str(os.environ.get("FACTORY_TRANSLATION_MODE", "always") or "always").strip().lower()
    return value if value in {"always", "auto", "off"} else "always"


def should_force_factory_pipeline(text: Any, src: Any, tgt: Any, *, heuristic_match: bool = False) -> bool:
    """Return whether the request must use the factory-only translation route."""
    if not supports_direction(src, tgt):
        return False
    selected = mode()
    if selected == "off":
        return False
    if selected == "auto":
        return bool(heuristic_match)
    return True


def fail_closed(src: Any, tgt: Any) -> bool:
    """Reject an unverified factory translation instead of delivering it."""
    return supports_direction(src, tgt) and _boolean_env("FACTORY_TRANSLATION_FAIL_CLOSED", True)


def review_mode() -> str:
    """Return source-review policy: ``always``, ``adaptive`` or ``off``.

    ``adaptive`` is the production default: ordinary short messages use one
    provider call plus deterministic validation, while structurally high-risk or
    knowledge-matched messages request an independent source review.  ``always``
    remains available as an operational override, but a review outage must not
    discard a first candidate that already passed every local integrity gate.
    """
    value = str(os.environ.get("FACTORY_TRANSLATION_REVIEW_MODE", "adaptive") or "adaptive").strip().lower()
    aliases = {
        "on": "always", "required": "always", "strict": "always", "all": "always",
        "smart": "adaptive", "auto": "adaptive",
        "none": "off", "disabled": "off", "0": "off",
    }
    value = aliases.get(value, value)
    return value if value in {"always", "adaptive", "off"} else "adaptive"


def require_source_review(text: Any, src: Any, tgt: Any, *, adaptive_risk: bool = False) -> bool:
    """Decide whether a generated factory translation needs source review."""
    if not should_force_factory_pipeline(text, src, tgt, heuristic_match=adaptive_risk):
        return False
    selected = review_mode()
    if selected == "off":
        return False
    if selected == "adaptive":
        return bool(adaptive_risk)
    return True


def require_review_success(src: Any, tgt: Any) -> bool:
    """Whether review success is required for authoritative/cacheable status.

    Disabled by default.  A locally valid first translation may still be
    delivered when the independent reviewer is unavailable or returns an invalid
    mutation; it is marked degraded and is not cached or learned.  Actual source
    integrity failures remain fail-closed.
    """
    return supports_direction(src, tgt) and _boolean_env(
        "FACTORY_TRANSLATION_REQUIRE_REVIEW_SUCCESS", False
    )


def allow_generic_nmt_fallback(src: Any, tgt: Any) -> bool:
    """Generic fallback is opt-in in unified factory mode."""
    if not supports_direction(src, tgt):
        return True
    return _boolean_env("FACTORY_ALLOW_GENERIC_NMT_FALLBACK", False)


def build_prompt(text: Any, src: Any, tgt: Any) -> str:
    """Always-on factory interpretation contract for supported directions."""
    if not should_force_factory_pipeline(text, src, tgt):
        return ""
    direction = f"{_lang(src)}>{_lang(tgt)}"
    return (
        "<unified_factory_translation_policy>\n"
        f"Policy build: {FACTORY_TRANSLATION_POLICY_BUILD_ID}; direction: {direction}.\n"
        "This request belongs to the Walsin Lihwa Yanshui stainless-steel bar factory. "
        "Interpret the entire source as shop-floor, production-planning, packaging, warehouse, ERP, "
        "quality, maintenance, safety, personnel, or accounting communication unless the source explicitly says otherwise.\n"
        "Use the retrieved plant glossary, factory knowledge and verified correction cases as the authoritative terminology system. "
        "Do not fall back to everyday dictionary meanings when a plant meaning is available.\n"
        "Before output, silently reconstruct and verify actor, action, object, machine/station, material, movement direction, process state, "
        "time, quantity, unit, negation, modality, priority, accounting action, cause and consequence against the source.\n"
        "Never invent an operator, machine, crane, manual operation, automatic operation, data check, accounting action, "
        "cause, deadline, measurement or workflow step that is not stated or entailed by approved plant knowledge.\n"
        "Preserve customer names, employee names, codes, work-order IDs, station IDs, numbers and units exactly as written. "
        "Do not translate a Chinese customer name into an ordinary Indonesian adjective or noun.\n"
        "A newly generated translation is independently reconstructed from the source and then checked locally. "
        "Any candidate that fails either boundary is blocked instead of delivered, cached or learned. "
        "Output only one complete target-language translation.\n"
        "</unified_factory_translation_policy>"
    )


def health() -> Dict[str, Any]:
    return {
        "api_version": FACTORY_TRANSLATION_POLICY_API_VERSION,
        "build_id": FACTORY_TRANSLATION_POLICY_BUILD_ID,
        "mode": mode(),
        "review_mode": review_mode(),
        "review_success_required": require_review_success("zh", "id"),
        "generic_nmt_fallback": allow_generic_nmt_fallback("zh", "id"),
        "fail_closed": fail_closed("zh", "id"),
    }
