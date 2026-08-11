"""Unified routing, delivery and learning policy for factory Chinese↔Indonesian.

The bot serves a production environment, so translation quality and service
availability must be controlled separately:

* Chinese↔Indonesian requests use the factory semantic route by default.
* stale lexical/vector TM and generic NMT cannot bypass the current contract.
* verified exact corrections remain eligible after deterministic validation.
* validator uncertainty makes a candidate non-cacheable/non-learnable but does
  not turn a complete translation into a generic user-visible failure.
* objective corruption (missing names/codes/numbers, source-language leakage,
  unresolved placeholders, severe omission) triggers another provider or an
  automatic detached retry; it is never displayed as a translation.
* empty output, legacy failure payloads and pure model meta-commentary are also
  undeliverable. Provider outages may use an emergency NMT route.

This separation is deliberate: a heuristic quality rule must never become a
single point of availability failure, while unverified text must never pollute
cache/TM and repeat indefinitely.
"""
from __future__ import annotations

import os
from typing import Any, Dict

FACTORY_TRANSLATION_POLICY_API_VERSION = 6
FACTORY_TRANSLATION_POLICY_BUILD_ID = "2026-08-11.2-always-review-nonblocking-relations"

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


def block_unverified_delivery(src: Any, tgt: Any) -> bool:
    """Never let a quality heuristic suppress a non-empty translation.

    Delivery blocking was previously configurable, which allowed a stale Render
    environment variable to revive the old generic failure state after the code
    had been fixed.  Objective corruption is still rejected by the authoritative
    final boundary and retried through another provider; advisory disagreement
    affects only cache/TM admission.  The switch is therefore intentionally
    retired and always returns False.
    """
    return False


def fail_closed(src: Any, tgt: Any) -> bool:
    """Backward-compatible alias for the delivery-blocking switch."""
    return block_unverified_delivery(src, tgt)


def require_verified_for_cache(src: Any, tgt: Any) -> bool:
    """Require local verification before cache/TM/vector learning admission."""
    return supports_direction(src, tgt) and _boolean_env(
        "FACTORY_REQUIRE_VERIFIED_FOR_CACHE", True
    )


def review_mode() -> str:
    """Return source-review policy: ``always``, ``adaptive`` or ``off``."""
    value = str(os.environ.get("FACTORY_TRANSLATION_REVIEW_MODE", "always") or "always").strip().lower()
    aliases = {
        "on": "always", "required": "always", "strict": "always", "all": "always",
        "smart": "adaptive", "auto": "adaptive",
        "none": "off", "disabled": "off", "0": "off",
    }
    value = aliases.get(value, value)
    return value if value in {"always", "adaptive", "off"} else "always"


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

    Even when enabled, review failure affects authoritative status and learning,
    not the availability of a non-empty first translation.
    """
    return supports_direction(src, tgt) and _boolean_env(
        "FACTORY_TRANSLATION_REQUIRE_REVIEW_SUCCESS", False
    )


def allow_generic_nmt_fallback(src: Any, tgt: Any) -> bool:
    """Normal generic fallback remains opt-in in unified factory mode."""
    if not supports_direction(src, tgt):
        return True
    return _boolean_env("FACTORY_ALLOW_GENERIC_NMT_FALLBACK", False)


def allow_emergency_nmt_fallback(src: Any, tgt: Any) -> bool:
    """Allow one last NMT attempt only when the semantic provider returned empty."""
    if not supports_direction(src, tgt):
        return True
    return _boolean_env("FACTORY_ALLOW_EMERGENCY_NMT_FALLBACK", True)


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
        "Before output, silently reconstruct and verify speaker/actor, action, object, recipient, role, ID ownership, "
        "machine/station, instrument, material, movement, direction, destination, process state, time, quantity, unit, "
        "which reading belongs to which device, comparison/difference, negation, modality, priority, purpose, cause and consequence against the source.\n"
        "Never invent an operator, machine, crane, manual operation, automatic operation, data check, accounting action, "
        "cause, deadline, measurement or workflow step that is not stated or entailed by approved plant knowledge.\n"
        "Preserve customer names, employee names, codes, work-order IDs, station IDs, numbers and units exactly as written. "
        "For Work Order/ERP/label text, preserve every quoted control label (for example \"NO Kondom\") and every single-letter flag such as (Y)/(N) exactly; translate only the surrounding explanation. "
        "When two instructions conflict, preserve the conflict, the prohibition against acting immediately, and the required escalation/checking step; never silently choose or harmonize one instruction. "
        "Do not translate a Chinese customer name into an ordinary Indonesian adjective or noun.\n"
        "Output only one complete target-language translation. Never output an apology, safety-status message, "
        "translation-failure notice, explanation, or request to resend. Local validation controls cache/learning admission; "
        "objective integrity defects trigger automatic provider fallback or retry and must not be described to the user.\n"
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
        "emergency_nmt_fallback": allow_emergency_nmt_fallback("zh", "id"),
        "block_unverified_delivery": block_unverified_delivery("zh", "id"),
        "verified_cache_required": require_verified_for_cache("zh", "id"),
        "fail_closed": fail_closed("zh", "id"),
    }
