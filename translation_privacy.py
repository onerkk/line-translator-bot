"""Local privacy protection for provider-bound translation requests.

The translation pipeline still keeps the original text locally for glossary,
semantic and delivery validation.  Only the payload sent to an external LLM or
NMT provider is masked.  The provider copies stable placeholders and the server
restores the original values before any user-visible result is returned.

This module deliberately uses conservative patterns.  Generic factory numbers,
work-order IDs and equipment codes are *not* treated as personal data unless a
clear label identifies them as an account, employee or identity value.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


PRIVACY_BUILD_ID = "2026-08-18.1-provider-boundary-masking"

_PLACEHOLDER_RE = re.compile(r"__QG_KEEP_(\d{3})_([0-9A-F]{8})__")

_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}"
    r"(?![A-Za-z0-9._%+-])"
)
_TW_MOBILE_RE = re.compile(
    r"(?<!\d)(?:\+?886[- .]?)?0?9\d{2}(?:[- .]?\d{3}){2}(?!\d)"
)
_ID_MOBILE_RE = re.compile(
    r"(?<!\d)(?:\+?62[- .]?|0)8\d{1,3}(?:[- .]?\d){7,10}(?!\d)"
)
_TW_NATIONAL_ID_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?![A-Za-z0-9])")

# Label-bound values avoid mistaking a 16-digit work order, coil number or bank
# batch code for a person.  The label remains visible; only the value is masked.
_LABELLED_VALUE_RE = re.compile(
    r"(?P<label>"
    r"(?:NIK|NO\.?\s*KTP|KTP|ARC|居留證(?:號碼)?|居留证(?:号码)?|"
    r"身分證(?:字號|號碼)?|身份证(?:字号|号码)?|護照(?:號碼)?|护照(?:号码)?|"
    r"PASSPORT(?:\s*(?:NO|NUMBER))?|"
    r"員工(?:編號|工號)|员工(?:编号|工号)|工號|工号|EMPLOYEE\s*ID|"
    r"BANK\s*ACCOUNT|ACCOUNT\s*(?:NO|NUMBER)|銀行帳號|银行账号|REKENING|"
    r"LINE\s*ID)"
    r"\s*[:：#號号-]?\s*"
    r")"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9._/-]{3,31})",
    re.I,
)


@dataclass(frozen=True)
class PrivacyEnvelope:
    original: str
    masked: str
    mapping: Mapping[str, str]
    categories: Mapping[str, str]

    @property
    def has_sensitive_data(self) -> bool:
        return bool(self.mapping)


def privacy_enabled() -> bool:
    """Return whether external-provider masking is enabled (default: on)."""
    raw = os.environ.get("TRANSLATION_PRIVACY_MODE", "mask").strip().lower()
    return raw not in {"0", "false", "off", "disabled", "none"}


def _placeholder(index: int, literal: str) -> str:
    # QG_KEEP is already understood by the project's provider validators and
    # tolerant placeholder-restoration logic.  Reserve 900-999 for privacy.
    digest = hashlib.sha1(literal.encode("utf-8")).hexdigest()[:8].upper()
    return f"__QG_KEEP_{900 + index:03d}_{digest}__"


def _normalise_literals(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in values or ():
        value = str(raw or "").strip()
        if len(value) < 2 or value in seen or _PLACEHOLDER_RE.fullmatch(value):
            continue
        seen.add(value)
        out.append(value)
    return out


def _collect_matches(text: str, extra_literals: Iterable[str]) -> List[Tuple[int, int, str, str]]:
    matches: List[Tuple[int, int, str, str]] = []
    for category, pattern in (
        ("email", _EMAIL_RE),
        ("phone", _TW_MOBILE_RE),
        ("phone", _ID_MOBILE_RE),
        ("identity", _TW_NATIONAL_ID_RE),
    ):
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), match.group(0), category))

    for match in _LABELLED_VALUE_RE.finditer(text):
        start, end = match.span("value")
        matches.append((start, end, match.group("value"), "labelled_identifier"))

    for literal in _normalise_literals(extra_literals):
        for match in re.finditer(re.escape(literal), text):
            matches.append((match.start(), match.end(), literal, "protected_name"))

    # Prefer the longest match at a position, then discard overlaps.  This keeps
    # an entire phone/e-mail token under one placeholder instead of fragmenting it.
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    accepted: List[Tuple[int, int, str, str]] = []
    cursor = -1
    for item in matches:
        if item[0] < cursor:
            continue
        accepted.append(item)
        cursor = item[1]
    return accepted[:100]


def mask_sensitive_text(
    text: str,
    *,
    extra_literals: Iterable[str] = (),
    enabled: bool | None = None,
) -> PrivacyEnvelope:
    """Mask conservative PII patterns and explicitly protected names locally."""
    original = str(text or "")
    is_enabled = privacy_enabled() if enabled is None else bool(enabled)
    if not original or not is_enabled:
        return PrivacyEnvelope(original, original, {}, {})

    matches = _collect_matches(original, extra_literals)
    if not matches:
        return PrivacyEnvelope(original, original, {}, {})

    chunks: List[str] = []
    mapping: Dict[str, str] = {}
    categories: Dict[str, str] = {}
    cursor = 0
    literal_placeholders: Dict[str, str] = {}
    for start, end, literal, category in matches:
        chunks.append(original[cursor:start])
        placeholder = literal_placeholders.get(literal)
        if placeholder is None:
            placeholder = _placeholder(len(mapping), literal)
            literal_placeholders[literal] = placeholder
            mapping[placeholder] = literal
            categories[placeholder] = category
        chunks.append(placeholder)
        cursor = end
    chunks.append(original[cursor:])
    return PrivacyEnvelope(original, "".join(chunks), mapping, categories)


def _placeholder_pattern(placeholder: str) -> re.Pattern[str]:
    match = _PLACEHOLDER_RE.fullmatch(placeholder)
    if not match:
        return re.compile(re.escape(placeholder))
    index, digest = match.groups()
    return re.compile(
        r"(?:__|\[\[|\[|【|⟦|\()?\s*QG[\s_-]*KEEP[\s_-]*0*"
        + re.escape(str(int(index)))
        + r"[\s_-]*"
        + re.escape(digest)
        + r"\s*(?:__|\]\]|\]|】|⟧|\))?",
        re.I,
    )


def restore_sensitive_text(text: str, envelope: PrivacyEnvelope) -> str:
    """Restore known placeholders, tolerating harmless provider punctuation drift."""
    result = str(text or "")
    if not result or not envelope.mapping:
        return result
    for placeholder in envelope.mapping:
        result = _placeholder_pattern(placeholder).sub(placeholder, result)
    for placeholder, literal in sorted(envelope.mapping.items(), key=lambda item: -len(item[0])):
        result = result.replace(placeholder, literal)
    return result


def placeholders_preserved(text: str, envelope: PrivacyEnvelope) -> Tuple[bool, List[str]]:
    """Verify each private-value placeholder survived the provider response."""
    value = str(text or "")
    missing = [
        placeholder
        for placeholder in envelope.mapping
        if not _placeholder_pattern(placeholder).search(value)
    ]
    return not missing, missing


def mask_messages(messages: Sequence[Mapping[str, object]], envelope: PrivacyEnvelope) -> List[Dict[str, object]]:
    """Return provider messages with every known literal replaced everywhere."""
    replacements = sorted(
        ((literal, placeholder) for placeholder, literal in envelope.mapping.items()),
        key=lambda item: -len(item[0]),
    )
    output: List[Dict[str, object]] = []
    for message in messages:
        copied = dict(message)
        content = copied.get("content")
        if isinstance(content, str):
            for literal, placeholder in replacements:
                content = content.replace(literal, placeholder)
            # Few-shot/TM messages can contain private values unrelated to the
            # current source. Mask those too; they never need to be restored in
            # the current answer.
            content = mask_sensitive_text(content).masked
            copied["content"] = content
        output.append(copied)
    return output


def mask_known_literals(text: str, envelope: PrivacyEnvelope) -> str:
    """Apply an existing envelope to another local representation of the text."""
    result = str(text or "")
    for placeholder, literal in sorted(
        envelope.mapping.items(), key=lambda item: -len(item[1])
    ):
        result = result.replace(literal, placeholder)
    return result


def provider_instruction(envelope: PrivacyEnvelope) -> str:
    if not envelope.mapping:
        return ""
    placeholders = "\n".join(f"- {value}" for value in envelope.mapping)
    return (
        "<sensitive_data_placeholders>\n"
        "The server masked private values before this request. Copy every placeholder "
        "exactly wherever it appears; never translate, omit, expand, guess or explain it. "
        "The server will restore the private values locally after translation.\n"
        + placeholders
        + "\n</sensitive_data_placeholders>"
    )


def privacy_summary(envelope: PrivacyEnvelope) -> Dict[str, object]:
    counts: Dict[str, int] = {}
    for category in envelope.categories.values():
        counts[category] = counts.get(category, 0) + 1
    return {"masked": len(envelope.mapping), "categories": counts}
