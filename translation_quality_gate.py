"""Provider-neutral translation quality pipeline.

Design goals
------------
1. Protect data values and locked terminology before the single model call.
2. Restore literals and canonical terms deterministically after generation.
3. Validate completeness, language purity and structure locally.
4. High-risk factory notices may receive one independent source-grounded review call.
5. Provider failover remains operational; semantic review prefers a different configured provider when available.
6. No sentence-specific translation replacements live in this module.

The module is intentionally provider-neutral and works with the project's
``ai_provider.chat_complete`` interface for OpenAI, Gemini and Claude.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import glossary_policy as gp_module
import factory_semantic_audit as fsa_module
import factory_quantity_semantics as fqs_module
import factory_message_semantics as fmr_module

logger = logging.getLogger(__name__)

# Deployment contract: app.py verifies this exact build at startup.
QUALITY_GATE_API_VERSION = 24
QUALITY_GATE_BUILD_ID = "2026-08-11.1-bidirectional-source-relation-integrity"

# ASCII placeholders survive all three providers more reliably than decorative
# Unicode brackets.  The hash prevents accidental collision with ordinary text.
_PLACEHOLDER_RE = re.compile(r"__QG_KEEP_(\d{3})_([0-9A-F]{8})__")
_UNKNOWN_PLACEHOLDER_RE = re.compile(r"QG[\s_-]*KEEP[\s_-]*\d{1,4}[\s_-]*[0-9A-F]{6,10}", re.I)
_PIPELINE_TOKEN_RE = re.compile(r'(?:__QG_KEEP_\d{3}_[0-9A-F]{8}__|__PERSON_\d+__|⟦PN\d+⟧|__MENTION_\d+__|__CUST_\d+__)')

_QUOTES_OPEN = '"“”„‟＂「」『』‘’\'`'
_QUOTES_CLOSE = _QUOTES_OPEN
_QUOTES_ALL = _QUOTES_OPEN

# Quote-wrapped source spans are scanned broadly, then filtered by
# ``_is_immutable_quoted_value``.  The previous one-token-only regex missed
# operational form labels containing spaces or mixed case (for example a Work
# Order field value such as ``"NO Kondom"``), causing a correct translation to
# be rejected as untranslated source language.  Filtering in code keeps normal
# quoted prose translatable instead of treating every quotation as immutable.
_QUOTED_DATA_RE = re.compile(
    r'(?P<open>[' + re.escape(_QUOTES_OPEN) + r'])\s*'
    r'(?P<value>[^' + re.escape(_QUOTES_ALL) + r'\r\n]{1,64}?)\s*'
    r'(?P<close>[' + re.escape(_QUOTES_CLOSE) + r'])'
)

# A one-letter uppercase value in parentheses is normally a form/control flag
# (Y/N, A/B, etc.), not prose.  It must survive translation exactly.  This is
# intentionally limited to a single ASCII uppercase letter so ordinary
# parenthetical explanations remain fully translatable.
_PARENTHESIZED_FLAG_RE = re.compile(
    r'(?P<open>[（(])\s*(?P<value>[A-Z])\s*(?P<close>[）)])'
)

_QUOTED_CODELIKE_RE = re.compile(r'(?:[-–—]|[A-Z0-9][A-Z0-9._/+:%×x-]{0,31})\Z')
_QUOTED_LABEL_TOKEN_RE = re.compile(r'[A-Za-zÀ-ÖØ-öø-ÿ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9._/+:%×x-]{0,31}')
_QUOTED_CONTROL_WORDS = frozenset({
    "NO", "YES", "Y", "N", "OK", "NG", "PASS", "FAIL", "ON", "OFF",
    "OPEN", "CLOSE", "HOLD", "RELEASE", "START", "STOP", "AUTO", "MANUAL",
})

_MENTION_RE = re.compile(
    r'@[^\s,，。!?！？:：;；]{1,48}(?:\s+[a-z][a-z0-9_.-]{1,31}){0,2}'
)

# Plain uppercase words are not identifiers.  Indonesian factory notices are
# frequently written in all caps, so the former ``[A-Z]{1,4}`` rule incorrectly
# protected ordinary words such as DAN and BATU as immutable data.  Only a
# curated set of globally stable industrial acronyms is treated as an immutable
# bare-alpha token; every other identifier must have a digit or separator.
_KNOWN_TECH_ACRONYMS = frozenset({
    "AC", "AI", "API", "CNC", "ERP", "HMI", "ID", "LINE", "MES", "OCR",
    "PLC", "QA", "QC", "RPM", "SOP", "TAG", "TIG", "UI", "UPS", "URL", "WIP", "WO",
})
_KNOWN_TECH_ACRONYM_PATTERN = "|".join(
    sorted((re.escape(value) for value in _KNOWN_TECH_ACRONYMS), key=len, reverse=True)
)
_TECH_TOKEN_RE = re.compile(
    r'(?<![A-Za-z0-9_])('
    r'(?:[A-Z]{1,4}\d[A-Z0-9._/+:%×x-]{0,24})|'
    r'(?:\d+[A-Z][A-Z0-9._/+:%×x-]{0,24})|'
    r'(?:[A-Z]{1,4}(?:[/._+-][A-Z0-9]{1,8})+)|'
    r'(?:\d+(?:[.,]\d+)?\s*(?:mm|cm|kg|g|t|%|°C|℃))|'
    rf'(?:{_KNOWN_TECH_ACRONYM_PATTERN})'
    r')(?![A-Za-z0-9_])'
)

_LATIN_RUN_RE = re.compile(r'(?:\b[A-Za-z]{2,}\b(?:[\s,;:/()\-]+|$)){4,}', re.I)
_MARKERS = ("✅", "❌", "⚠️", "📢", "•", "▪", "▫", "→")
_HAN_RE = re.compile(r'[\u3400-\u9fff]')
_LATIN_WORD_RE = re.compile(r'(?<![A-Za-z])([A-Za-z]{1,32})(?![A-Za-z])')
_LATIN_TOKEN_RE = re.compile(
    r'(?<![A-Za-zÀ-ÖØ-öø-ÿ])'
    r'([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ0-9._/+:%×x-]{0,63})'
    r'(?![A-Za-zÀ-ÖØ-öø-ÿ])'
)
_DASHES = "-–—−"

# Chinese factory notices routinely state a production unit once and omit it
# from the following target/average/forecast figures.  Indonesian cannot safely
# rely on the same ellipsis: ``入庫目標3750 ... 147噸 ... 130～135入庫量 ...
# 入到3600`` must keep ``ton`` on every production quantity.  The rules below
# are deliberately domain- and cue-based so material grades such as ``422`` are
# never reinterpreted as 422 tons.
_ZH_MASS_UNIT_TO_ID = {
    "公噸": "ton",
    "噸": "ton",
    "吨": "ton",
    "公斤": "kg",
    "千克": "kg",
}
_ZH_EXPLICIT_MASS_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>公噸|噸|吨|公斤|千克|kg)(?![A-Za-z])",
    re.I,
)
_ZH_QUANTITY_VALUE_PATTERN = (
    r"\d+(?:[.,]\d+)?(?:\s*(?:[～~\-–—至])\s*\d+(?:[.,]\d+)?)?"
)
_ZH_QUANTITY_CUE_BEFORE_RE = re.compile(
    r"(?P<cue>"
    r"(?:入庫|進倉|產出|出料|生產|包裝|出貨|庫存|處理|完成)?目標|"
    r"平均(?:每天|每日|一天|每班|每月)?(?:需|要|達|維持|做到|入到|產到|出到)?|"
    r"維持(?:每天|每日|一天|每班|每月)?|"
    r"(?:每天|每日|一天|每班|每月)(?:需|要|達|維持)?|"
    r"(?:能|可|預估|估計|預計)?(?:入庫|入到|進倉|做到|產到|出到|達到|完成到|累計到)"
    r")\s*(?P<value>" + _ZH_QUANTITY_VALUE_PATTERN + r")",
    re.I,
)
_ZH_QUANTITY_CUE_AFTER_RE = re.compile(
    r"(?P<value>" + _ZH_QUANTITY_VALUE_PATTERN + r")\s*"
    r"(?P<cue>入庫量|進倉量|產出量|出料量|產量|庫存量|處理量|完成量|實績)",
    re.I,
)
_ZH_QUANTITATIVE_PREFIX_RE = re.compile(
    r"(?:平均|維持|每天|每日|一天|每班|每月|約|大約|至少|至多|低於|高於|超過|不到|達到|目標|預估|估計|預計|能|可)\s*$",
    re.I,
)

# A translated Chinese notice may intentionally retain a source-side English
# process label as a bilingual heading, e.g. ``粗磨（ROUGH GRINDING）``.  This is
# structurally different from an untranslated Indonesian sentence fragment.  We
# recognize only multi-word, all-uppercase phrases that are copied exactly from
# the source, are visually attached to Chinese text, and contain no common source
# language function word.  The rule is document-structural rather than tied to a
# specific grinding sentence or fixed term list.
_UPPERCASE_PHRASE_RE = re.compile(
    r'(?<![A-Za-z])([A-Z][A-Z0-9/+._-]{1,31}(?:\s+[A-Z][A-Z0-9/+._-]{1,31}){1,4})(?![A-Za-z])'
)

# A mixed-case factory document often defines a short all-uppercase label once
# and then reuses it throughout the notice (for example a form field, inspection
# label, or physical tag name).  Repeated labels are source data, not untranslated
# prose.  The inference is deliberately document-structural: it is disabled for
# all-uppercase documents and excludes common Indonesian/English words.
_DOCUMENT_LABEL_TOKEN_RE = re.compile(
    r'(?<![A-Za-z0-9_])'
    r'([A-Z](?:[A-Z0-9._/+:%×x-]{0,14}[A-Z0-9]))'
    r'(?![A-Za-z0-9_])'
)
_PARENTHETICAL_LATIN_ALIAS_RE = re.compile(
    r'[（(]\s*([A-Za-z][A-Za-z0-9._/+:%×x-]{1,31}'
    r'(?:\s+[A-Za-z][A-Za-z0-9._/+:%×x-]{1,31}){0,2})\s*[）)]'
)


_IDENTITY_TOKEN_RE = re.compile(
    r'(?:__MENTION_\d+__|__PERSON_\d+__|⟦PN\d+⟧|__CUST_\d+__)',
    re.I,
)


def _canonical_identity_token(token: str) -> str:
    """Return a stable spelling for a recoverable identity placeholder."""
    raw = str(token or "")
    m = re.fullmatch(r'__MENTION_(\d+)__', raw, re.I)
    if m:
        return f"__MENTION_{int(m.group(1))}__"
    m = re.fullmatch(r'__PERSON_(\d+)__', raw, re.I)
    if m:
        return f"__PERSON_{int(m.group(1))}__"
    m = re.fullmatch(r'⟦PN(\d+)⟧', raw, re.I)
    if m:
        return f"⟦PN{int(m.group(1))}⟧"
    m = re.fullmatch(r'__CUST_(\d+)__', raw, re.I)
    if m:
        return f"__CUST_{int(m.group(1))}__"
    return raw


def _target_clause_starts(text: str) -> List[int]:
    """Return conservative sentence/paragraph starts for token reinsertion."""
    value = str(text or "")
    starts = [0]
    for match in re.finditer(r'(?:[.!?;,:]+(?:\s+|$)|\n+)', value):
        if match.end() < len(value):
            starts.append(match.end())
    out: List[int] = []
    for start in starts:
        while start < len(value) and value[start].isspace():
            start += 1
        if start not in out:
            out.append(start)
    return out or [0]


def _source_clause_index(text: str, position: int) -> int:
    prefix = str(text or "")[: max(0, int(position or 0))]
    return len(re.findall(r'(?:[。！？!?；;，,：:]+|\n+)', prefix))


def repair_identity_tokens(source: str, candidate: str) -> str:
    """Recover only locally-known mention/person placeholders.

    Translation providers occasionally simplify ``⟦PN1⟧`` to ``PN`` or drop a
    mention/name placeholder while still returning a usable translation.  Those
    tokens represent identity metadata already known by the application, so
    restoring them is deterministic and does not invent translated content.
    Codes, dates, quantities and other immutable values remain fail-closed.
    """
    source = str(source or "")
    repaired = str(candidate or "")
    occurrences = [
        (_canonical_identity_token(m.group(0)), m.start())
        for m in _IDENTITY_TOKEN_RE.finditer(source)
    ]
    if not occurrences or not repaired:
        return repaired

    expected_tokens: List[str] = []
    for token, _pos in occurrences:
        if token not in expected_tokens:
            expected_tokens.append(token)

    # Canonicalize common provider variants, but only for tokens that actually
    # occur in this source.  This avoids touching genuine factory codes.
    for token in expected_tokens:
        if token.startswith("__MENTION_"):
            idx = int(re.search(r"(\d+)", token).group(1))
            pattern = re.compile(
                rf"(?<![A-Za-z0-9])(?:\[\[\s*)?_*(?:MENTION|SEBUTAN|提及)"
                rf"[_\s-]*0*{idx}_*(?:\s*\]\])?(?![A-Za-z0-9])",
                re.I,
            )
        elif token.startswith("__PERSON_"):
            idx = int(re.search(r"(\d+)", token).group(1))
            pattern = re.compile(
                rf"(?<![A-Za-z0-9])(?:[⟦【〔\[（｟「『]\s*)?_*(?:PERSON|NAME|NAMA|PN)"
                rf"[_\s-]*0*{idx}_*(?:\s*[⟧】〕\]）｠」』])?(?![A-Za-z0-9])",
                re.I,
            )
        elif token.startswith("⟦PN"):
            idx = int(re.search(r"(\d+)", token).group(1))
            pattern = re.compile(
                rf"(?<![A-Za-z0-9])(?:[⟦【〔\[（｟「『]\s*)?_*(?:PERSON|NAME|NAMA|PN)"
                rf"[_\s-]*0*{idx}_*(?:\s*[⟧】〕\]）｠」』])?(?![A-Za-z0-9])",
                re.I,
            )
        else:  # legacy __CUST_n__
            idx = int(re.search(r"(\d+)", token).group(1))
            pattern = re.compile(
                rf"(?<![A-Za-z0-9])_*(?:CUST|CUSTOMER)[_\s-]*0*{idx}_*(?![A-Za-z0-9])",
                re.I,
            )
        repaired = pattern.sub(token, repaired)

    # Claude has been observed reducing the only protected name token to the
    # bare label "PN".  This is unambiguous only when exactly one distinct
    # protected-name token exists and the source itself has no literal PN code.
    person_tokens = [t for t in expected_tokens if t.startswith("__PERSON_") or t.startswith("⟦PN")]
    if len(person_tokens) == 1 and not re.search(r'(?<![A-Za-z0-9])PN(?![A-Za-z0-9])', source, re.I):
        deficit = source.count(person_tokens[0]) - repaired.count(person_tokens[0])
        if deficit > 0:
            repaired = re.sub(
                r'(?<![A-Za-z0-9])P\s*N(?![A-Za-z0-9])',
                person_tokens[0],
                repaired,
                count=deficit,
                flags=re.I,
            )

    # Reinsert any still-missing identity occurrences at the corresponding
    # target sentence/paragraph start.  Insertions are grouped and applied from
    # right to left so offsets remain stable.
    expected_by_token: Dict[str, List[int]] = {}
    for token, pos in occurrences:
        expected_by_token.setdefault(token, []).append(pos)
    pending: List[Tuple[int, str]] = []
    starts = _target_clause_starts(repaired)
    for token, positions in expected_by_token.items():
        missing_count = max(0, len(positions) - repaired.count(token))
        if not missing_count:
            continue
        for pos in positions[-missing_count:]:
            clause_idx = _source_clause_index(source, pos)
            target_start = starts[min(clause_idx, len(starts) - 1)]
            pending.append((target_start, token))

    grouped: Dict[int, List[str]] = {}
    for start, token in pending:
        grouped.setdefault(start, []).append(token)
    for start in sorted(grouped, reverse=True):
        prefix = " ".join(grouped[start]) + " "
        repaired = repaired[:start] + prefix + repaired[start:]

    return repaired.strip()

# Common function/content words are used only to disambiguate title-cased words
# at sentence boundaries from real proper names.  Ordinary lowercase source
# words are rejected even when absent from these sets.
_COMMON_ID_WORDS = {
    "ada", "agar", "akan", "anda", "atau", "bagi", "bahwa", "baik", "bahan",
    "barang", "baru", "belum", "bisa", "boleh", "buat", "dalam", "dan", "dari",
    "dengan", "di", "dilarang", "dipahami", "diperhatikan", "ditandai", "harap",
    "harus", "informasi", "ini", "jangan", "jika", "juga", "karena", "kerja",
    "kolom", "lagi", "maka", "material", "memahami", "menggunakan", "menjaga",
    "mohon", "operator", "pada", "pekerja", "pelindung", "produk", "produksi",
    "proses", "saat", "sampai", "sebelum", "semua", "setiap", "sesuai", "sudah",
    "supaya", "terima", "terkait", "tersebut", "tidak", "untuk", "wajib", "yang",
}
_COMMON_EN_WORDS = {
    "a", "all", "and", "are", "as", "at", "be", "before", "book", "by", "can",
    "do", "for", "from", "has", "have", "if", "in", "is", "it", "may", "must",
    "no", "not", "of", "on", "only", "operator", "order", "or", "please", "should",
    "that", "the", "this", "to", "use", "with", "without", "work", "worker", "you",
}


@dataclass(frozen=True)
class ProtectedText:
    original: str
    protected: str
    mapping: Dict[str, str] = field(default_factory=dict)


@dataclass
class ValidationResult:
    ok: bool
    issues: List[str]
    hard_issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _placeholder(index: int, literal: str) -> str:
    digest = hashlib.sha1(literal.encode("utf-8")).hexdigest()[:8].upper()
    return f"__QG_KEEP_{index:03d}_{digest}__"


def _new_placeholder(mapping: Dict[str, str], literal: str) -> str:
    ph = _placeholder(len(mapping), literal)
    mapping[ph] = literal
    return ph


def _replace_matches(text: str, regex: re.Pattern, mapping: Dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        literal = match.group(0)
        if _PLACEHOLDER_RE.search(literal):
            return literal
        return _new_placeholder(mapping, literal)
    return regex.sub(repl, text)


def _document_defined_uppercase_labels(text: str) -> List[str]:
    """Infer repeated all-uppercase labels from a mixed-case source document.

    This closes a general false-rejection class without whitelisting one sentence:
    a label must be repeated, consistently uppercase, non-linguistic, and embedded
    in a document that otherwise contains lowercase prose.  Ordinary emphatic
    words such as ``WAJIB`` or ``BOLEH`` therefore remain translatable language.
    """
    value = str(text or "")
    if not value or not re.search(r'[a-z]', value):
        return []
    common = _COMMON_ID_WORDS | _COMMON_EN_WORDS
    candidates: Dict[str, int] = {}
    for match in _DOCUMENT_LABEL_TOKEN_RE.finditer(value):
        token = match.group(1)
        folded = token.casefold()
        if folded in common or token.upper() in _KNOWN_TECH_ACRONYMS:
            continue
        candidates[token] = candidates.get(token, 0) + 1
    labels: List[str] = []
    for token, count in candidates.items():
        if count < 2:
            continue
        # Reject a token that also appears in ordinary lower/title case elsewhere.
        variants = re.findall(
            r'(?<![A-Za-z0-9_])' + re.escape(token) + r'(?![A-Za-z0-9_])',
            value,
            re.I,
        )
        if any(item != token for item in variants):
            continue
        labels.append(token)
    return sorted(labels, key=lambda item: (-len(item), item))


def _protect_document_defined_labels(text: str, mapping: Dict[str, str]) -> str:
    protected = str(text or "")
    for label in _document_defined_uppercase_labels(protected):
        pattern = re.compile(r'(?<![A-Za-z0-9_])' + re.escape(label) + r'(?![A-Za-z0-9_])')
        protected = _replace_matches(protected, pattern, mapping)
    return protected


def _is_immutable_quoted_value(value: str) -> bool:
    """Return whether a short quoted span is operational data, not prose.

    Accepted shapes are deliberately conservative:
    * the legacy one-token code/measurement/dash shapes;
    * a short (2–6 token) field/control label with a strong data signal such as
      an all-uppercase control token, acronym, digit, or technical separator.

    Lowercase natural-language quotations remain translatable.  This avoids the
    opposite failure mode where quoted instructions could leak untranslated.
    """
    raw = str(value or "").strip()
    if not raw or len(raw) > 64 or "\n" in raw or "\r" in raw:
        return False
    if _PLACEHOLDER_RE.fullmatch(raw):
        return True
    if _QUOTED_CODELIKE_RE.fullmatch(raw):
        return True
    if re.search(r'[.!?;:，。！？；：]', raw):
        return False

    tokens = raw.split()
    if not (2 <= len(tokens) <= 6):
        return False
    if not all(_QUOTED_LABEL_TOKEN_RE.fullmatch(token) for token in tokens):
        return False

    has_digit_or_separator = any(
        any(ch.isdigit() or ch in "._/+:%×x-" for ch in token)
        for token in tokens
    )
    has_control_or_acronym = any(
        token in _QUOTED_CONTROL_WORDS
        or token in _KNOWN_TECH_ACRONYMS
        or (token.isupper() and 1 <= len(token) <= 12)
        for token in tokens
    )
    return has_digit_or_separator or has_control_or_acronym


def _protect_parenthesized_flags(text: str, mapping: Dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        value = match.group("value")
        if _PLACEHOLDER_RE.fullmatch(value or ""):
            return match.group(0)
        return f'{match.group("open")}{_new_placeholder(mapping, value)}{match.group("close")}'
    return _PARENTHESIZED_FLAG_RE.sub(repl, text)


def _protect_quoted_values(text: str, mapping: Dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        value = (match.group("value") or "").strip()
        # A technical token may already have been protected before this pass. In
        # that case leave the quote-wrapped placeholder untouched.
        if _PLACEHOLDER_RE.fullmatch(value):
            return match.group(0)
        if not _is_immutable_quoted_value(value):
            return match.group(0)
        return f'{match.group("open")}{_new_placeholder(mapping, value)}{match.group("close")}'
    return _QUOTED_DATA_RE.sub(repl, text)


def _immutable_quoted_value_count(text: str) -> int:
    return sum(
        1 for match in _QUOTED_DATA_RE.finditer(text or "")
        if _is_immutable_quoted_value(match.group("value"))
    )


def protect_immutable_spans(text: str) -> ProtectedText:
    """Protect mentions, field values, codes and measurements.

    Ordering is deliberate:
    - mentions and document-defined labels are protected first;
    - parenthesized one-letter control flags such as ``(Y)`` next;
    - technical tokens next;
    - filtered quote-wrapped field values such as ``"NO Kondom"`` or ``"-"`` last.

    Therefore normalization cannot convert a control flag ``Y`` into ``ya`` and
    the quality gate does not reject correct translations merely because a Work
    Order field label contains spaces or mixed case.
    """
    if not text or not isinstance(text, str):
        return ProtectedText(text or "", text or "", {})
    mapping: Dict[str, str] = {}
    protected = _replace_matches(text, _MENTION_RE, mapping)
    protected = _protect_document_defined_labels(protected, mapping)
    protected = _protect_parenthesized_flags(protected, mapping)
    protected = _replace_matches(protected, _TECH_TOKEN_RE, mapping)
    protected = _protect_quoted_values(protected, mapping)
    return ProtectedText(text, protected, mapping)


def inspect_immutable_spans(text: str) -> ProtectedText:
    """Inventory immutable data without hiding it from the translator.

    Opaque ``__QG_KEEP_*`` tokens were originally sent to the model.  Some
    providers legitimately omitted those machine-looking tokens even while
    producing a complete translation, which made the local integrity gate
    report a false service outage.  Provider-facing translation now keeps real
    identifiers, field values and measurements visible and uses this inventory
    only for deterministic post-generation validation.

    ``mapping`` intentionally retains the legacy placeholder keys so existing
    callers can reuse ``mapping.values()`` as the immutable literal inventory,
    while ``protected`` remains exactly the original visible text.
    """
    envelope = protect_immutable_spans(text)
    visible_mapping = {
        ph: _normalize_data_atom(literal)
        for ph, literal in envelope.mapping.items()
        if _normalize_data_atom(literal)
    }
    return ProtectedText(envelope.original, envelope.original, visible_mapping)


def visible_immutable_instruction(literals: Iterable[str]) -> str:
    """Build a provider-facing constraint using real values, never placeholders."""
    values = []
    seen = set()
    for literal in literals or ():
        value = str(literal or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    if not values:
        return ""
    lines = [
        "<immutable_data>",
        "The following values are visible source data, not language. Copy each value exactly and do not omit, translate, split, normalize, or rename it:",
    ]
    lines.extend(f"- {value}" for value in values[:80])
    lines.append("</immutable_data>")
    return "\n".join(lines)


def visible_glossary_instruction(pairs: Sequence[Tuple[str, str]]) -> str:
    """Build terminology constraints without replacing source words by tokens."""
    rows = []
    seen = set()
    for source_term, target_term in pairs or ():
        src = str(source_term or "").strip()
        tgt = str(target_term or "").strip()
        key = (src, tgt)
        if not src or not tgt or key in seen:
            continue
        seen.add(key)
        rows.append(key)
    if not rows:
        return ""
    lines = [
        "<required_terminology>",
        "Translate each visible source term using the exact target term below. Do not copy internal placeholders and do not omit the surrounding meaning:",
    ]
    lines.extend(f"- {src} => {tgt}" for src, tgt in rows[:80])
    lines.append("</required_terminology>")
    return "\n".join(lines)


def _placeholder_pattern(ph: str) -> re.Pattern:
    m = _PLACEHOLDER_RE.fullmatch(ph)
    if not m:
        return re.compile(re.escape(ph))
    idx, digest = m.groups()
    # Tolerate brackets, omitted underscores and whitespace inserted by a model,
    # while still requiring the exact index+hash identity.
    return re.compile(
        r'(?:__|\[\[|\[|【|⟦|\()?\s*QG[\s_-]*KEEP[\s_-]*0*'
        + re.escape(str(int(idx)))
        + r'[\s_-]*' + re.escape(digest)
        + r'\s*(?:__|\]\]|\]|】|⟧|\))?',
        re.I,
    )


def canonicalize_placeholders(text: str, mapping: Mapping[str, str]) -> str:
    result = text or ""
    for ph in mapping:
        result = _placeholder_pattern(ph).sub(ph, result)
    return result


def restore_immutable_spans(text: str, mapping: Mapping[str, str]) -> str:
    if not text or not mapping:
        return text
    result = canonicalize_placeholders(text, mapping)
    for ph, literal in sorted(mapping.items(), key=lambda item: -len(item[0])):
        result = result.replace(ph, literal)
    return result


def protected_placeholders_present(text: str, mapping: Mapping[str, str]) -> Tuple[bool, List[str]]:
    canonical = canonicalize_placeholders(text or "", mapping)
    missing = [ph for ph in mapping if canonical.count(ph) < 1]
    return not missing, missing


_TERM_PLACEHOLDER_RE = re.compile(r"__QG_TERM_(\d{3})_([0-9A-F]{8})__")


def _term_placeholder(index: int, source_term: str, target_term: str) -> str:
    digest = hashlib.sha1((source_term + "\0" + target_term).encode("utf-8")).hexdigest()[:8].upper()
    return f"__QG_TERM_{index:03d}_{digest}__"


def protect_glossary_terms(text: str, glossary_pairs: Sequence[Tuple[str, str]]) -> ProtectedText:
    """Replace source-grounded hard terms with locked tokens for one-pass translation.

    The model receives each token together with its canonical target value in the
    prompt.  After generation the token is restored locally, so terminology does
    not require an LLM post-editor or a second provider call.
    """
    original = text or ""
    if not original or not glossary_pairs:
        return ProtectedText(original, original, {})
    protected = original
    mapping: Dict[str, str] = {}
    unique: List[Tuple[str, str]] = []
    seen = set()
    for source_term, target_term in glossary_pairs:
        src = (source_term or "").strip()
        tgt = (target_term or "").strip()
        key = (src, tgt)
        if not src or not tgt or key in seen:
            continue
        seen.add(key)
        unique.append(key)
    for source_term, target_term in sorted(unique, key=lambda pair: -len(pair[0])):
        if source_term not in protected:
            continue
        ph = _term_placeholder(len(mapping), source_term, target_term)
        mapping[ph] = target_term
        protected = protected.replace(source_term, ph)
    return ProtectedText(original, protected, mapping)


def glossary_placeholder_instruction(mapping: Mapping[str, str]) -> str:
    if not mapping:
        return ""
    lines = [
        "<locked_terminology>",
        "Copy every locked token exactly once wherever it occurs. Do not translate, delete, split, or explain the token.",
        "The server will replace each token with the canonical target term after this single response:",
    ]
    lines.extend(f"{ph} = {target}" for ph, target in mapping.items())
    lines.append("</locked_terminology>")
    return "\n".join(lines)


def restore_glossary_terms(text: str, mapping: Mapping[str, str]) -> str:
    if not text or not mapping:
        return text
    result = text
    for ph, target in sorted(mapping.items(), key=lambda item: -len(item[0])):
        result = result.replace(ph, target)
    return result


def glossary_placeholders_present(text: str, mapping: Mapping[str, str]) -> Tuple[bool, List[str]]:
    if not mapping:
        return True, []
    missing = [ph for ph in mapping if ph not in (text or "") and mapping[ph].casefold() not in (text or "").casefold()]
    return not missing, missing


def _paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r'\n\s*\n+', text or "") if p.strip()]


def _whole_word_in_source(token: str, source: str) -> bool:
    return bool(re.search(r'(?<![A-Za-z])' + re.escape(token) + r'(?![A-Za-z])', source or "", re.I))


def _glossary_allowed_latin(glossary_pairs: Sequence[Tuple[str, str]]) -> set[str]:
    allowed: set[str] = set()
    for _src, tgt in glossary_pairs or ():
        for token in _LATIN_WORD_RE.findall(tgt or ""):
            allowed.add(token.upper())
    return allowed


def _latin_tokens(text: str) -> List[Tuple[str, int, int]]:
    return [(m.group(1), m.start(1), m.end(1)) for m in _LATIN_TOKEN_RE.finditer(text or "")]


def _looks_like_technical_identifier(token: str) -> bool:
    """Return True only for shapes that are normally identifiers, not words.

    This intentionally does *not* treat every all-caps token as a code.  Factory
    announcements are commonly typed in uppercase, so accepting arbitrary
    uppercase words is exactly how untranslated fragments such as ``BOLEH`` used
    to pass the gate.
    """
    t = (token or "").strip()
    if not t:
        return False
    if any(ch.isdigit() for ch in t):
        return True
    if any(ch in "._/+:%×x-" for ch in t):
        return True
    if t.upper() in _KNOWN_TECH_ACRONYMS:
        return True
    # Mixed-case product/company identifiers such as OpenAI, iPhone, eSIM.
    letters = [ch for ch in t if ch.isalpha()]
    if letters and any(ch.isupper() for ch in letters) and any(ch.islower() for ch in letters):
        if not (t[:1].isupper() and t[1:].islower()):
            return True
    return False


def _inline_bilingual_allowed_latin(
    source: str,
    candidate: str,
    src_lang: str,
) -> set[str]:
    """Return tokens from source-grounded bilingual technical labels.

    The provider may render a heading as ``中文（SOURCE LABEL）`` or ``SOURCE
    LABEL 中文``.  We permit that label only when all of the following hold:

    * it is a 2–5 word uppercase phrase copied verbatim from the source;
    * it is attached to Chinese text or enclosed in parentheses in the target;
    * none of its words is a common source-language function/content word.

    This keeps genuine labels such as ``ROUGH GRINDING`` and ``FREE END`` while
    still rejecting leaks such as ``TIDAK BOLEH`` or a lone ``BOLEH``.
    """
    if not source or not candidate:
        return set()
    if not str(src_lang or "").lower().startswith(("id", "en")):
        return set()

    common = _source_common_words(src_lang)
    allowed: set[str] = set()
    for match in _UPPERCASE_PHRASE_RE.finditer(candidate):
        phrase = match.group(1)
        words = phrase.split()
        if any(word.casefold() in common for word in words):
            continue
        if not re.search(
            r'(?<![A-Za-z])' + re.escape(phrase) + r'(?![A-Za-z])',
            source,
        ):
            continue

        left = candidate[max(0, match.start() - 3):match.start()]
        right = candidate[match.end():min(len(candidate), match.end() + 3)]
        parenthesized = bool(
            re.search(r'[（(]\s*$', left) and re.match(r'^\s*[）)]', right)
        )
        attached_to_han = bool(_HAN_RE.search(left) or _HAN_RE.search(right))
        if not (parenthesized or attached_to_han):
            continue
        allowed.update(word.upper() for word in words)
    return allowed


def _source_parenthetical_alias_latin(source: str, src_lang: str) -> set[str]:
    """Allow explicit source-defined Latin aliases such as ``penjepit (clip)``.

    Parentheses are a strong, language-independent signal that the author supplied
    a label/alias.  Only short aliases that contain no common source-language word
    are admitted, so ordinary parenthesized clauses cannot bypass leakage checks.
    """
    common = _source_common_words(src_lang)
    allowed: set[str] = set()
    for match in _PARENTHETICAL_LATIN_ALIAS_RE.finditer(source or ""):
        words = [token for token, _start, _end in _latin_tokens(match.group(1))]
        if not words or len(words) > 3:
            continue
        if any(word.casefold() in common for word in words):
            continue
        allowed.update(word.upper() for word in words)
    return allowed


def _source_common_words(src_lang: str) -> set[str]:
    low = (src_lang or "").lower()
    if low.startswith("id"):
        return _COMMON_ID_WORDS
    if low.startswith("en"):
        return _COMMON_EN_WORDS
    return set()


def _probable_source_proper_name(token: str, source: str, src_lang: str) -> bool:
    """Conservatively allow real names/brands while rejecting sentence words.

    LINE display names often contain a CJK nickname followed by a lowercase
    Latin name, for example ``@蘇比 sobirin``.  That lowercase name is part of
    the mention and must survive an ID→ZH translation.  Treating every lowercase
    Latin token as untranslated Indonesian caused valid group messages to be
    blocked after mention restoration.
    """
    t = (token or "").strip()
    if not t or t.casefold() in _source_common_words(src_lang):
        return False
    if not _whole_word_in_source(t, source):
        return False
    # Exact mixed case is a strong brand signal (OpenAI/iPhone/eSIM).
    if _looks_like_technical_identifier(t):
        return True
    # Any Latin token inside an @mention/display-name span is immutable, even
    # when the display name is lowercase.  The mention regex intentionally
    # accepts up to two lowercase continuation tokens after the @name.
    for match in _MENTION_RE.finditer(source or ""):
        if _whole_word_in_source(t, match.group(0)):
            return True

    # The public translation boundary replaces each LINE mention with a stable
    # token before the provider call.  LINE may mark only the CJK nickname while
    # leaving an adjacent lowercase Latin display-name continuation outside the
    # metadata span, producing a protected source such as:
    #
    #   __MENTION_0__ sobirin __MENTION_1__ Jika ...
    #
    # ``sobirin`` is identity data, not untranslated Indonesian.  Recognize only
    # the conservative shape between two mention tokens; ordinary words after a
    # single mention remain subject to the normal source-language leakage gate.
    for match in re.finditer(
        r'__MENTION_\d+__\s+'
        r'(?P<names>[A-Za-z][A-Za-z0-9_.-]{1,31}'
        r'(?:\s+[A-Za-z][A-Za-z0-9_.-]{1,31})?)\s+'
        r'__MENTION_\d+__',
        source or "",
    ):
        if _whole_word_in_source(t, match.group("names")):
            return True
    # Ordinary title-case words may be names.  Requiring exact case prevents an
    # all-caps source word from being converted to title case and escaping.
    exact = bool(re.search(r'(?<![A-Za-z])' + re.escape(t) + r'(?![A-Za-z])', source or ""))
    return exact and len(t) >= 2 and t[:1].isupper() and t[1:].islower()


def _immutable_allowed_latin(immutable_literals: Iterable[str]) -> set[str]:
    allowed: set[str] = set()
    for literal in immutable_literals or ():
        for token, _s, _e in _latin_tokens(str(literal)):
            allowed.add(token.upper())
    return allowed


def _is_near_han(text: str, start: int, end: int) -> bool:
    """Detect a Latin token embedded in a Chinese sentence with light spacing."""
    left = (text or "")[max(0, start - 3):start]
    right = (text or "")[end:min(len(text or ""), end + 3)]
    return bool(_HAN_RE.search(left) or _HAN_RE.search(right))


def _target_zh_language_purity_issues(
    source: str,
    candidate: str,
    src_lang: str,
    *,
    immutable_literals: Iterable[str],
    glossary_pairs: Sequence[Tuple[str, str]],
) -> List[str]:
    """Find untranslated ordinary Latin words in a Chinese target.

    The key invariant is semantic, not positional: appearing in the source is
    *not* permission to remain untranslated.  Only immutable identifiers,
    explicit target-side glossary terms, technical codes and probable proper
    names may survive.  This catches a single leaked word, not only long Latin
    runs, and therefore closes the ``不BOLEH使用`` class of failures globally.
    """
    allowed = _glossary_allowed_latin(glossary_pairs)
    allowed.update(_immutable_allowed_latin(immutable_literals))
    allowed.update(_inline_bilingual_allowed_latin(source, candidate, src_lang))
    allowed.update(_source_parenthetical_alias_latin(source, src_lang))
    allowed.update(label.upper() for label in _document_defined_uppercase_labels(source))
    issues: List[str] = []
    common = _source_common_words(src_lang)

    for token, start, end in _latin_tokens(candidate):
        upper = token.upper()
        folded = token.casefold()
        if upper in allowed or _looks_like_technical_identifier(token):
            continue
        if _probable_source_proper_name(token, source, src_lang):
            continue

        in_source = _whole_word_in_source(token, source)
        ordinary_shape = token.islower() or token.isupper() or token[:1].isupper()
        if in_source and (ordinary_shape or folded in common):
            issues.append(f"untranslated_source_word:{token}")
            continue
        if folded in common:
            issues.append(f"source_language_leakage:{token}")
            continue
        if _is_near_han(candidate, start, end):
            issues.append(f"ungrounded_mixed_language:{token}")

    # Keep the long-run signal as a second, independent check.  It catches
    # punctuation-separated fragments whose individual tokens might look like
    # names but collectively form an untranslated source clause.
    if str(src_lang or "").lower().startswith(("id", "en")):
        for run in _LATIN_RUN_RE.findall(candidate):
            words = [w for w in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}", run)]
            unallowed = [w for w in words if w.upper() not in allowed and not _looks_like_technical_identifier(w)]
            if len(unallowed) >= 3:
                issues.append("source_language_leakage:latin_run")
                break
    return _dedupe(issues)


def infer_inline_bilingual_terms(source: str, src_lang: str, tgt_lang: str) -> List[Tuple[str, str]]:
    """Infer repeated source-phrase → Chinese-annotation terminology pairs.

    A common factory-writing pattern is ``source phrase (中文術語)``.  A single
    occurrence is too ambiguous to promote automatically.  When the same Chinese
    annotation is preceded by the same 1–4-word source suffix at least twice, the
    repeated association is strong enough to become a runtime glossary pair.
    This is document-level induction, not a sentence-specific replacement.
    """
    if not source or not str(tgt_lang or "").lower().startswith("zh"):
        return []
    if not str(src_lang or "").lower().startswith(("id", "en")):
        return []

    ann_re = re.compile(r"[（(]\s*([\u3400-\u9fff]{1,20})\s*[）)]")
    by_annotation: Dict[str, Dict[str, int]] = {}
    for match in ann_re.finditer(source):
        annotation = match.group(1).strip()
        # Restrict context to the current clause/line and at most 120 chars.
        prefix = source[max(0, match.start() - 120):match.start()]
        prefix = re.split(r"[\n。！？!?；;：:]", prefix)[-1]
        words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ-]*", prefix)
        if not words:
            continue
        counts = by_annotation.setdefault(annotation, {})
        for n in range(1, min(4, len(words)) + 1):
            phrase = " ".join(words[-n:]).strip()
            if len(phrase) < 3:
                continue
            counts[phrase.casefold()] = counts.get(phrase.casefold(), 0) + 1

    inferred: List[Tuple[str, str]] = []
    common = _source_common_words(src_lang)
    for annotation, counts in by_annotation.items():
        repeated = [p for p, c in counts.items() if c >= 2]
        repeated = [p for p in repeated if not all(w.casefold() in common for w in p.split())]
        if not repeated:
            continue
        # Prefer a repeated noun-like suffix rather than swallowing a leading
        # verb/function word (e.g. ``menggunakan kondom pelindung`` should infer
        # ``kondom pelindung``).  Require two words when available so a generic
        # final adjective/noun is not promoted by itself.
        noun_like = [p for p in repeated if p.split()[0].casefold() not in common]
        multiword = [p for p in noun_like if len(p.split()) >= 2]
        pool = multiword or noun_like or repeated
        best = sorted(pool, key=lambda p: (-len(p.split()), -len(p), p))[0]
        inferred.append((best, annotation))
    return inferred


def _merge_runtime_glossary_pairs(
    source: str,
    src_lang: str,
    tgt_lang: str,
    glossary_pairs: Sequence[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    merged: List[Tuple[str, str]] = []
    seen = set()
    for src_term, tgt_term in list(glossary_pairs or ()) + infer_inline_bilingual_terms(source, src_lang, tgt_lang):
        key = ((src_term or "").strip().casefold(), (tgt_term or "").strip())
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        merged.append(((src_term or "").strip(), (tgt_term or "").strip()))
    return merged


def _dedupe(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _partition_issues(issues: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Separate objective delivery failures from linguistic quality warnings.

    A local heuristic must never make a valid provider response disappear from
    LINE.  Only objective integrity failures (empty/truncated-like output,
    missing protected data, wrong target script, severe omission, source-language
    leakage, etc.) remain hard failures.  Register/readability diagnostics are
    warnings after the deterministic normalizer has run.
    """
    warning_prefixes = (
        "paragraph_count:",
        "style:",
        "overintensified_accusation:",
        "factory_object_error:",
        "agency_error:",
        "repeated_word:",
        "indonesian_sentence_too_long:",
        "indonesian_announcement_too_dense:",
    )
    hard: List[str] = []
    warnings: List[str] = []
    for issue in _dedupe(issues):
        (warnings if issue.startswith(warning_prefixes) else hard).append(issue)
    return hard, warnings


def normalize_indonesian_factory_register(
    source: str,
    candidate: str,
    src_lang: str,
    tgt_lang: str,
) -> str:
    """Deterministically normalize recurrent factory-register errors.

    This is source-conditioned concept normalization, not a second translation
    request and not a sentence lookup table.  The rules apply to any Chinese
    factory notice containing the corresponding concept, regardless of wording.
    They are deliberately conservative and touch only target fragments whose
    meaning is demonstrably wrong or non-standard in this plant.
    """
    result = (candidate or "").strip()
    if not result:
        return result
    if not str(src_lang or "").lower().startswith("zh"):
        return result
    if not str(tgt_lang or "").lower().startswith("id"):
        return result

    source = source or ""

    # Standard Indonesian orthography.  Word boundaries avoid changing names or
    # technical identifiers that merely contain the same character sequence.
    result = re.sub(r"\bfaham\b", "paham", result, flags=re.I)
    result = re.sub(r"\bsilahkan\b", "silakan", result, flags=re.I)

    # Management pressure in Chinese workplace notices is commonly a warning
    # about tighter oversight, not an allegation that management is physically
    # or unlawfully pressuring workers.
    if "高層" in source and "施壓" in source:
        result = re.sub(
            r"\bmanajemen\s+atas\s+(?:sudah\s+)?mulai\s+menekan\b",
            "manajemen juga semakin memperhatikan pekerjaan kita",
            result,
            flags=re.I,
        )
        result = re.sub(
            r"\bmanajemen\s+atas\b[^.!?\n]{0,35}\bmenekan\b",
            "manajemen juga semakin memperhatikan pekerjaan kita",
            result,
            flags=re.I,
        )

    # 敷衍 must not be escalated to lying/deception.  When the source explicitly
    # concerns false production figures, replace the whole accusation fragment
    # with a neutral, operationally clear data-accuracy instruction.
    if "敷衍" in source and not any(x in source for x in ("欺騙", "騙人", "說謊")):
        if any(x in source for x in ("虛假", "數據", "產量", "資料")):
            result = re.sub(
                r"Jangan\s+[^.!?\n]{0,220}\b(?:membohongi|menipu)\b[^.!?\n]*[.!?]?",
                "Jangan membuat atau mengisi data produksi yang tidak sesuai dengan kondisi sebenarnya.",
                result,
                flags=re.I,
            )
        result = re.sub(
            r"\b(?:membohongi|menipu)\s+(?:kami|kita)\b",
            "sekadar memberi laporan kepada kami",
            result,
            flags=re.I,
        )

    # Plant product terminology and agency semantics.
    if "研磨棒" in source:
        result = re.sub(r"\bbatang\s+gerinda\b", "grinding rod", result, flags=re.I)
    if "無法配合" in source:
        result = re.sub(
            r"\btidak\s+bisa\s+(?:mengikuti|mematuhi)\s+(?:peraturan|aturan)(?:\s+kerja)?\b",
            "tidak mau mematuhi aturan kerja",
            result,
            flags=re.I,
        )

    # Collapse accidental adjacent duplicate words locally.  This is safe for
    # ordinary Indonesian prose and prevents a harmless typo from blocking the
    # entire group reply.
    result = re.sub(
        r"\b([A-Za-zÀ-ÖØ-öø-ÿ]{2,})\s+\1\b",
        r"\1",
        result,
        flags=re.I,
    )
    return result.strip()


def _normalize_data_atom(value: str) -> str:
    v = (value or "").strip()
    if v and all(ch in _DASHES for ch in v):
        return "-"
    # Technical-token matching deliberately permits dots inside identifiers, but
    # a sentence-final period is punctuation rather than part of the work-order
    # or machine code.  Keeping it in the immutable atom caused false
    # ``missing_literal`` failures whenever target punctuation changed to 。.
    if re.search(r"[A-Za-z0-9]", v):
        v = re.sub(r"(?<=[A-Za-z0-9])[.,;:!?，。；：！？]+$", "", v)
    # Decimal comma and decimal point represent the same measurement value in
    # Indonesian/Chinese factory notices.  Canonicalize only digit-surrounded
    # commas; list punctuation remains untouched.
    v = re.sub(r"(?<=\d),(?=\d)", ".", v)
    return v


def _source_atom_is_quoted(source: str, atom: str) -> bool:
    a = re.escape(atom)
    if atom == "-":
        a = "[" + re.escape(_DASHES) + "]"
    return bool(re.search(r'[' + re.escape(_QUOTES_OPEN) + r']\s*' + a + r'\s*[' + re.escape(_QUOTES_CLOSE) + r']', source or ""))


def _count_semantic_atom(text: str, atom: str, *, quoted_preferred: bool = False) -> int:
    atom = _normalize_data_atom(atom)
    if not atom:
        return 0
    if atom == "-":
        if quoted_preferred:
            return len(re.findall(
                r'[' + re.escape(_QUOTES_ALL) + r']\s*[' + re.escape(_DASHES) + r']\s*[' + re.escape(_QUOTES_ALL) + r']',
                text or "",
            ))
        return len(re.findall(r'[' + re.escape(_DASHES) + r']', text or ""))
    if atom.startswith("@"):
        return (text or "").count(atom)
    measurement = re.fullmatch(
        r'(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|kg|g|t|%|°C|℃)',
        atom,
        re.I,
    )
    if measurement:
        number = re.escape(measurement.group("number")).replace(r"\.", r"[.,]")
        unit = re.escape(measurement.group("unit"))
        return len(re.findall(
            rf'(?<![\d.]){number}\s*{unit}(?![A-Za-z0-9])',
            text or "",
            re.I,
        ))
    if re.fullmatch(r'[A-Za-z0-9._/+:%×x-]+', atom):
        return len(re.findall(r'(?<![A-Za-z0-9])' + re.escape(atom) + r'(?![A-Za-z0-9])', text or ""))
    return (text or "").count(atom)


def _canonical_quantity_value(value: str) -> str:
    value = re.sub(r"\s+", "", str(value or ""))
    value = re.sub(r"[～~–—至]", "-", value)
    return value


def infer_implicit_quantity_units(
    source: str,
    src_lang: str,
    tgt_lang: str,
) -> List[Dict[str, str]]:
    """Infer repeated mass units omitted in Chinese production metrics.

    This is discourse-level unit inheritance, not a sentence replacement.  A
    candidate number is eligible only when it is attached to a quantitative
    production cue (target, daily average, warehouse intake, output, inventory,
    etc.).  A bare material/grade code such as ``422待洗庫存`` has no such cue
    and is intentionally ignored.
    """
    if not str(src_lang or "").lower().startswith("zh"):
        return []
    if not str(tgt_lang or "").lower().startswith("id"):
        return []
    text = str(source or "")
    explicit = list(_ZH_EXPLICIT_MASS_RE.finditer(text))
    if not explicit:
        return []

    explicit_number_spans = [(m.start("value"), m.end("unit")) for m in explicit]
    found: List[Tuple[int, int, str, str]] = []

    def add(match: re.Match[str], *, after_cue: bool) -> None:
        start, end = match.span("value")
        if any(start < old_end and end > old_start for old_start, old_end in explicit_number_spans):
            return
        value = _canonical_quantity_value(match.group("value"))
        if not value:
            return
        cue = str(match.group("cue") or "")

        # A number before a quantity noun is ambiguous unless the nearby left
        # context contains a real quantitative operator, or the value is a
        # range.  This excludes steel grades such as ``422待洗庫存量``.
        if not after_cue:
            left = text[max(0, start - 18):start]
            if "-" not in value and not _ZH_QUANTITATIVE_PREFIX_RE.search(left):
                return

        # Do not infer across unrelated, distant sections.  The nearest stated
        # unit must be in the same operational passage.
        nearest = min(explicit, key=lambda item: abs(item.start() - start))
        if abs(nearest.start() - start) > 360:
            return
        source_unit = nearest.group("unit")
        target_unit = _ZH_MASS_UNIT_TO_ID.get(source_unit, source_unit.lower())
        found.append((start, end, value, cue + "\0" + target_unit))

    for match in _ZH_QUANTITY_CUE_BEFORE_RE.finditer(text):
        add(match, after_cue=True)
    for match in _ZH_QUANTITY_CUE_AFTER_RE.finditer(text):
        add(match, after_cue=False)

    # Stable source order and deduplication by exact numeric span/value.
    out: List[Dict[str, str]] = []
    seen = set()
    for start, end, value, packed in sorted(found, key=lambda item: (item[0], item[1])):
        cue, target_unit = packed.split("\0", 1)
        key = (start, end, value, target_unit)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "value": value,
            "target_unit": target_unit,
            "cue": cue,
            "start": str(start),
        })
    return out


def implicit_quantity_unit_instruction(source: str, src_lang: str, tgt_lang: str) -> str:
    requirements = infer_implicit_quantity_units(source, src_lang, tgt_lang)
    if not requirements:
        return ""
    rendered = "; ".join(
        f"{item['value']} => {item['target_unit']}"
        for item in requirements
    )
    return (
        "<implicit_quantity_units>Chinese factory writing may state a mass unit once and omit it "
        "from later quantities in the same production/warehouse passage. The following source "
        "quantities inherit that unit and MUST show it explicitly in Indonesian: "
        + rendered
        + ". Do not treat material grades or station codes as quantities.</implicit_quantity_units>"
    )


def _integer_target_pattern(digits: str) -> str:
    digits = str(digits or "")
    if len(digits) <= 3:
        return re.escape(digits)
    groups: List[str] = []
    remainder = digits
    while remainder:
        groups.insert(0, remainder[-3:])
        remainder = remainder[:-3]
    grouped = r"[.,\s]?".join(re.escape(group) for group in groups)
    return rf"(?:{re.escape(digits)}|{grouped})"


def _quantity_target_pattern(value: str) -> str:
    value = _canonical_quantity_value(value)
    parts = value.split("-", 1)

    def one(part: str) -> str:
        if re.fullmatch(r"\d+", part):
            return _integer_target_pattern(part)
        if re.fullmatch(r"\d+[.,]\d+", part):
            whole, decimal = re.split(r"[.,]", part, maxsplit=1)
            return _integer_target_pattern(whole) + r"[.,]" + re.escape(decimal)
        return re.escape(part)

    if len(parts) == 2:
        return one(parts[0]) + r"\s*(?:[-–—~～]|sampai|hingga)\s*" + one(parts[1])
    return one(parts[0])


def _quantity_has_target_unit(candidate: str, value: str, target_unit: str) -> bool:
    value_pattern = _quantity_target_pattern(value)
    unit = str(target_unit or "").lower()
    if unit == "ton":
        unit_pattern = r"ton"
    elif unit == "kg":
        unit_pattern = r"(?:kg|kilogram)"
    else:
        unit_pattern = re.escape(unit)
    # The unit may precede the number in a table-like phrase, but normal prose
    # places it after the value.  Bound the gap so a later unrelated quantity
    # cannot accidentally satisfy this requirement.
    patterns = (
        rf"(?<![A-Za-z0-9]){value_pattern}(?![A-Za-z0-9]).{{0,18}}?\b{unit_pattern}\b",
        rf"\b{unit_pattern}\b.{{0,10}}?(?<![A-Za-z0-9]){value_pattern}(?![A-Za-z0-9])",
    )
    return any(re.search(pattern, candidate or "", flags=re.I | re.S) for pattern in patterns)


def _implicit_quantity_unit_issues(
    source: str,
    candidate: str,
    src_lang: str,
    tgt_lang: str,
) -> List[str]:
    issues: List[str] = []
    for item in infer_implicit_quantity_units(source, src_lang, tgt_lang):
        value = item["value"]
        target_unit = item["target_unit"]
        if not _quantity_has_target_unit(candidate, value, target_unit):
            issues.append(f"missing_inherited_unit:{value}:{target_unit}")
    return issues



def _factory_incident_reporting_issues(source: str, candidate: str) -> List[str]:
    """Validate factory incident/self-report semantics for ZH→ID output.

    This is a source-conditioned semantic lint, not an exact-sentence matcher. It
    protects four roles that literal translation often destroys: non-punitive
    voluntary reporting, physical damage mechanism, report recipient, and the
    consequence of another department reporting first.
    """
    src = re.sub(r"\s+", "", source or "")
    low = (candidate or "").casefold()
    issues: List[str] = []

    self_report = any(x in src for x in ("自首", "主動承認", "自己承認", "主動回報", "自己回報", "主動報告", "自己報告"))
    nonpunitive = any(x in src for x in ("無罪", "不追究", "不處罰", "不懲處", "不處分", "不會處罰", "不會懲處", "不會處分", "不會被處罰", "不會被追究"))
    equipment = any(x in src for x in ("設備", "機台", "機器", "工具", "器材", "治具"))
    incident_context = equipment and any(x in src for x in ("撞壞", "碰壞", "摔壞", "掉落", "損壞", "讓我知道", "提報", "回報", "報告"))
    if not (self_report and nonpunitive and incident_context):
        return issues

    if re.search(r"\b(?:tidak\s+ada\s+dosa|tanpa\s+dosa|bebas\s+dosa|tidak\s+berdosa|tidak\s+bersalah)\b", low):
        issues.append("semantic:incident_self_report_literal_religious_or_legal")
    if not any(x in low for x in ("mengaku sendiri", "melapor sendiri", "melaporkan sendiri", "mengaku secara sukarela")):
        issues.append("semantic:incident_self_report_actor_missing")
    if not any(x in low for x in ("tidak akan dipermasalahkan", "tidak akan dihukum", "tidak akan dikenai sanksi", "tidak akan diberi sanksi", "tidak akan ditindak")):
        issues.append("semantic:incident_self_report_nonpunitive_meaning_missing")

    collision = any(x in src for x in ("撞壞", "撞到壞", "碰壞", "撞擊損壞", "碰撞損壞"))
    falling = any(x in src for x in ("摔壞", "掉落摔壞", "跌落損壞", "掉下去壞", "掉下損壞"))
    if collision and not any(x in low for x in ("tertabrak", "terbentur", "menabrak", "terkena benturan", "benturan")):
        issues.append("semantic:incident_collision_mechanism_missing")
    if falling and not any(x in low for x in ("terjatuh", "jatuh", "terlepas lalu jatuh")):
        issues.append("semantic:incident_fall_mechanism_missing")
    if (collision or falling) and "rusak" not in low:
        issues.append("semantic:incident_damage_result_missing")

    report_to_me = any(x in src for x in ("讓我知道", "跟我說", "告訴我", "回報我", "向我回報", "向我提報", "報告給我"))
    if report_to_me:
        has_report = bool(re.search(r"\b(?:laporkan|melaporkan|beri\s+tahu|beritahu)\b", low))
        if not (has_report and "saya" in low):
            issues.append("semantic:incident_report_to_supervisor_missing")

    other_unit = any(x in src for x in ("其他單位", "其它單位", "別的單位", "其他部門", "其它部門", "別的部門"))
    report = any(x in src for x in ("提報", "回報", "報告", "通報"))
    if other_unit and report:
        if not any(x in low for x in ("departemen lain", "unit lain", "bagian lain")):
            issues.append("semantic:incident_other_unit_missing")
        if not any(x in low for x in ("lebih dulu", "terlebih dahulu", "lebih dahulu", "duluan")):
            issues.append("semantic:incident_other_unit_first_report_missing")
        if not any(x in low for x in ("lebih serius", "semakin serius", "menjadi lebih serius", "sudah parah", "menjadi parah")):
            issues.append("semantic:incident_escalation_consequence_missing")

    return _dedupe(issues)


def _indonesian_readability_issues(source: str, candidate: str, src_lang: str) -> List[str]:
    """Reject structurally valid but operationally unreadable Indonesian.

    These checks are generic: no source sentence is translated here. They catch
    stale glossary definitions, duplicated mention markers and dense Chinese-
    syntax announcements that workers cannot scan reliably.
    """
    issues: List[str] = []
    source = source or ""
    candidate = candidate or ""
    low = candidate.casefold()

    if str(src_lang or "").lower().startswith("zh"):
        issues.extend(_factory_incident_reporting_issues(source, candidate))

    if "@@" not in source and re.search(r"(?<!@)@@+", candidate):
        issues.append("duplicated_mention_marker")

    for phrase in gp_module.deprecated_indonesian_phrases():
        if phrase.casefold() in low:
            issues.append(f"deprecated_glossary_phrase:{phrase}")

    # Source-conditioned factory/management semantic lint.  These are concept
    # rules, not sentence replacements: they prevent common Chinese-literal
    # constructions that change responsibility, accusation strength or the
    # physical object being discussed.
    if "敷衍" in source and not any(x in source for x in ("欺騙", "騙人", "說謊")):
        if re.search(r"\b(membohongi|menipu)\b", low):
            issues.append("overintensified_accusation:敷衍")
    if "研磨棒" in source and "batang gerinda" in low:
        issues.append("factory_object_error:研磨棒_is_not_grinding_tool")
    if "研磨棒" in source and not any(x in low for x in (
        "grinding rod", "batang hasil proses grinding", "batang yang diproses di bagian grinding"
    )):
        issues.append("style:factory_term:研磨棒_prefer_grinding_rod")
    if "調機" in source and "penyesuaian mesin" in low and "penyetelan" not in low:
        issues.append("style:factory_term:調機_prefer_penyetelan_or_penyetelan_penyesuaian")
    if "無法配合" in source and re.search(r"tidak bisa (mengikuti|mematuhi)", low):
        issues.append("agency_error:無法配合_is_noncompliance_not_inability")
    if "高層" in source and "施壓" in source:
        if "manajemen atas" in low or re.search(r"manajemen[^.]{0,40}\bmenekan\b", low):
            issues.append("style:management_register:avoid_literal_pressure")
    if "福利" in source:
        # Broad collective welfare is naturally expressed as kesejahteraan kita semua.
        # Only explicit allowances/facilities require the narrower tunjangan/fasilitas wording.
        explicit_allowance = any(x in source for x in ("津貼", "補助", "設施", "福利金", "獎金"))
        if explicit_allowance and "kesejahteraan" in low and not any(x in low for x in ("tunjangan", "fasilitas", "bonus")):
            issues.append("style:explicit_employee_benefit_needs_specific_term")

    if re.search(r"\bfaham\b", low):
        issues.append("style:standard_spelling:faham_to_paham")
    if re.search(r"\bsilahkan\b", low):
        issues.append("style:standard_spelling:silahkan_to_silakan")

    duplicate = re.search(r"\b([A-Za-zÀ-ÖØ-öø-ÿ]{2,})\s+\1\b", candidate, re.I)
    if duplicate:
        issues.append(f"repeated_word:{duplicate.group(1).casefold()}")

    source_han = len(_HAN_RE.findall(source))
    words = re.findall(r"\b[A-Za-zÀ-ÖØ-öø-ÿ]{2,}\b", candidate)
    if source_han >= 60 and len(words) >= 45:
        units = [
            part.strip()
            for part in re.split(r"(?:[.!?]+\s*|\n+)", candidate)
            if part.strip()
        ]
        unit_lengths = [len(re.findall(r"\b[A-Za-zÀ-ÖØ-öø-ÿ]{2,}\b", unit)) for unit in units]
        if unit_lengths and max(unit_lengths) > 55:
            issues.append(f"indonesian_sentence_too_long:{max(unit_lengths)}")
        if len(words) >= 65 and len(units) < 3:
            issues.append(f"indonesian_announcement_too_dense:{len(units)}")

    return _dedupe(issues)


def _indonesian_clarity_instruction(tgt_lang: str) -> str:
    if not str(tgt_lang or "").lower().startswith("id"):
        return ""
    return (
        " Write plain, standard Indonesian for Indonesian factory workers, not bureaucratic Indonesian and not "
        "word-for-word Chinese. Use standard spelling: paham, silakan, tidak, sudah. Prefer short sentences, clear "
        "paragraphs and direct subject-action-object order. Use 'kita' for shared workplace impact and 'kalian' only "
        "for direct instructions to workers. For a long supervisor notice that is clearly an announcement, a single "
        "heading 'Pengumuman' is allowed; do not add headings to ordinary short messages. State who must do what, "
        "when, why and the consequence whenever the source contains those elements. Resolve omitted Chinese subjects "
        "and objects from factory context, but do not invent facts or repeat a closing request that appears only once. "
        "Do not translate management pressure literally as 'manajemen atas menekan'; use natural workplace wording "
        "such as 'Manajemen juga semakin memperhatikan pekerjaan kita' or 'pengawasan semakin ketat' according to the "
        "source strength. For broad collective 福利, 'kesejahteraan kita semua' is natural; reserve 'tunjangan dan "
        "fasilitas karyawan' for explicit allowances or facilities. Preserve the source strength and do not intensify criticism into accusations. "
        "Do not intensify 敷衍 into membohongi or menipu "
        "unless the source explicitly alleges lying. For factory incident reporting, 自首無罪 means voluntary self-reporting will not be held against the worker; never use religious/legal literal wording such as 'tidak ada dosa' or 'tidak bersalah'. Preserve distinct damage causes such as collision and falling, the person who must be notified, whether another department reports first, and the resulting escalation. Prefer 'data produksi yang tidak sesuai dengan kondisi sebenarnya' "
        "or another concrete, non-accusatory expression. In this plant 研磨棒 is the product term 'grinding rod', never "
        "'batang gerinda'. 調機 is 'penyetelan mesin' or 'penyetelan/penyesuaian mesin'. 無法配合規定 describes "
        "noncompliance, not inability. Keep approved plant terms such as urgent order, work order and grinding when "
        "they are normal shop-floor usage. For quality notices, keep a product or process defect distinct from a defect in the machine itself, and prefer concrete wording such as 'produk yang cacat' or "
        "'produk yang tidak sesuai standar'. Glossary descriptions and notes are context only, not phrases to paste "
        "into the translation. Only explicit hard terminology pairs are literal constraints."
    )

def validate_translation(
    source: str,
    candidate: str,
    src_lang: str,
    tgt_lang: str,
    *,
    immutable_literals: Optional[Iterable[str]] = None,
    glossary_pairs: Optional[Sequence[Tuple[str, str]]] = None,
    require_paragraph_fidelity: bool = False,
) -> ValidationResult:
    """Deterministic integrity checks on a restored translation.

    Immutable values are compared semantically.  Quote glyphs may legitimately
    change from Indonesian curly quotes to Taiwanese corner quotes; the field
    value itself must remain unchanged.
    """
    issues: List[str] = []
    source = source or ""
    candidate = (candidate or "").strip()
    glossary_pairs = _merge_runtime_glossary_pairs(
        source, src_lang, tgt_lang, list(glossary_pairs or ())
    )
    immutable_literals = list(immutable_literals or ())

    if not candidate:
        return ValidationResult(False, ["empty_translation"], ["empty_translation"], [])

    if _PLACEHOLDER_RE.search(candidate) or _UNKNOWN_PLACEHOLDER_RE.search(candidate):
        issues.append("placeholder_leak")

    for token in _PIPELINE_TOKEN_RE.findall(source):
        if candidate.count(token) < source.count(token):
            issues.append(f"missing_pipeline_token:{token}")
    for token in _PIPELINE_TOKEN_RE.findall(candidate):
        if token not in source:
            issues.append(f"invented_pipeline_token:{token}")

    for literal in immutable_literals:
        atom = _normalize_data_atom(str(literal))
        quoted = _source_atom_is_quoted(source, atom)
        src_count = _count_semantic_atom(source, atom, quoted_preferred=quoted)
        # For quoted atoms, accept any target-language quote pair but not a
        # completely missing value.  For ordinary codes/mentions require token
        # identity.
        cand_count = _count_semantic_atom(candidate, atom, quoted_preferred=quoted)
        if src_count and cand_count < src_count:
            issues.append(f"missing_literal:{atom}")

    for marker in _MARKERS:
        if source.count(marker) > candidate.count(marker):
            issues.append(f"missing_marker:{marker}")

    if require_paragraph_fidelity:
        src_p = len(_paragraphs(source))
        tgt_p = len(_paragraphs(candidate))
        if src_p >= 2 and tgt_p != src_p:
            issues.append(f"paragraph_count:{src_p}->{tgt_p}")

    src_norm = re.sub(r'\s+', ' ', source).strip().casefold()
    cand_norm = re.sub(r'\s+', ' ', candidate).strip().casefold()
    if len(src_norm) >= 24 and cand_norm == src_norm:
        issues.append("unchanged_source")

    tgt = str(tgt_lang or "").lower()
    src = str(src_lang or "").lower()
    if tgt.startswith("zh"):
        issues.extend(_target_zh_language_purity_issues(
            source,
            candidate,
            src,
            immutable_literals=immutable_literals,
            glossary_pairs=glossary_pairs,
        ))

        src_info = len(re.findall(r'[A-Za-z0-9\u3400-\u9fff]', source))
        han_count = len(_HAN_RE.findall(candidate))
        if src_info >= 40 and han_count < 4:
            issues.append("target_script_missing")
        if src_info >= 160 and han_count < max(28, int(src_info * 0.12)):
            issues.append("catastrophic_omission")

    elif tgt.startswith("id"):
        source_han = len(_HAN_RE.findall(source))
        latin_words = len(re.findall(r'\b[A-Za-zÀ-ÖØ-öø-ÿ]{2,}\b', candidate))
        if source_han >= 8 and latin_words < 3:
            issues.append("target_script_missing")
        if source_han >= 80 and latin_words < max(20, int(source_han * 0.22)):
            issues.append("catastrophic_omission")
        if src.startswith("zh"):
            issues.extend(_implicit_quantity_unit_issues(source, candidate, src, tgt))
            # Number/classifier atoms and their relations are validated from a
            # compositional source frame. This blocks fluent but wrong outputs
            # such as 包→bundel, dropped half quantities, or 又→plain dan.
            _quantity_frame = fqs_module.build_frame(source, src, tgt)
            _quantity_ok, _quantity_issues = fqs_module.validate_translation(_quantity_frame, candidate)
            if not _quantity_ok or _quantity_issues:
                issues.extend(_quantity_issues)
            issues.extend(_indonesian_readability_issues(source, candidate, src))

    # Bidirectional source-relation completeness. This checks that equipment,
    # readings, differences, reporting roles, movement, destinations and
    # inspection actions remain attached to the same source roles. It applies
    # before any cache/learning decision and is compositional across paraphrases.
    relation_frame = fmr_module.build_frame(source, src, tgt)
    relation_ok, relation_issues = fmr_module.validate_translation(
        relation_frame, candidate
    )
    if not relation_ok or relation_issues:
        issues.extend(relation_issues)

    issues = _dedupe(issues)
    hard, warnings = _partition_issues(issues)
    return ValidationResult(not hard, issues, hard, warnings)


def _validate_protected_candidate(
    protected_source: str,
    protected_candidate: str,
    mapping: Mapping[str, str],
    src_lang: str,
    tgt_lang: str,
    *,
    glossary_pairs: Sequence[Tuple[str, str]],
    require_paragraph_fidelity: bool,
) -> ValidationResult:
    candidate = canonicalize_placeholders(protected_candidate or "", mapping)
    issues: List[str] = []
    for ph in mapping:
        if candidate.count(ph) < protected_source.count(ph):
            issues.append(f"missing_placeholder:{ph}")
    for m in _UNKNOWN_PLACEHOLDER_RE.findall(candidate):
        if not any(_placeholder_pattern(ph).fullmatch(m) for ph in mapping):
            issues.append("unknown_placeholder")
    # Restore only for language/marker/length checks; semantic literal checks are
    # skipped here because placeholder identity was already checked exactly.
    restored_source = restore_immutable_spans(protected_source, mapping)
    restored_candidate = restore_immutable_spans(candidate, mapping)
    base = validate_translation(
        restored_source,
        restored_candidate,
        src_lang,
        tgt_lang,
        immutable_literals=(),
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=require_paragraph_fidelity,
    )
    issues.extend(base.issues)
    hard, warnings = _partition_issues(_dedupe(issues))
    return ValidationResult(not hard, _dedupe(issues), hard, warnings)


def is_quality_critical(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    message_type: Optional[str] = None,
    factory_domain: bool = False,
) -> bool:
    if not text:
        return False
    compact_len = len(re.sub(r'\s+', '', text))
    para_count = len(_paragraphs(text))
    marker_count = sum(text.count(m) for m in _MARKERS)
    quoted_data_count = _immutable_quoted_value_count(text)
    return bool(
        message_type == "announcement"
        or compact_len >= 180
        or para_count >= 3
        or marker_count >= 2
        or (factory_domain and quoted_data_count >= 1)
    )


def _target_name(lang: str) -> str:
    low = (lang or "").lower()
    if low.startswith("zh"):
        return "Traditional Chinese used in Taiwan"
    if low.startswith("id"):
        return "Indonesian"
    return lang or "target language"


def _build_review_messages(
    source: str,
    candidate: str,
    src_lang: str,
    tgt_lang: str,
    issues: Sequence[str],
    glossary_pairs: Sequence[Tuple[str, str]],
) -> List[Dict[str, str]]:
    terminology = "\n".join(f"- {s} => {t}" for s, t in glossary_pairs[:40]) or "(none)"
    issue_text = "\n".join(f"- {x}" for x in issues) or "- Perform a full independent accuracy review."
    annotations = ", ".join(dict.fromkeys(re.findall(r"[（(]\s*([\u3400-\u9fff]{1,20})\s*[）)]", source or "")))
    annotation_rule = (
        " The source contains target-language annotations in parentheses: " + annotations + ". "
        "Treat them as terminology evidence. When a source phrase is followed by its Chinese annotation, "
        "use the canonical Chinese term once; do not output a literal translation plus a redundant duplicate annotation."
        if annotations and str(tgt_lang or "").lower().startswith("zh") else ""
    )
    inherited_unit_rule = implicit_quantity_unit_instruction(source, src_lang, tgt_lang)
    system = (
        "You are an independent bilingual translation quality editor for factory communications. Do not merely "
        "polish the current wording. Reconstruct the meaning from the source, silently back-translate each target "
        "sentence, compare it with the corresponding source meaning, and then write one fresh corrected final "
        "translation. Audit actor, action, object, timing, condition, negation, modality, severity, cause and "
        "consequence. Preserve every instruction, condition, negation, actor, object and sequence. Do not add "
        "information. Source identifiers and field values remain visible in the source; copy them exactly. Preserve "
        "numbers, symbols, @mentions, emoji and list markers. Preserve the document's logical sections; "
        "minor paragraph reflow is allowed only when meaning is unchanged. Apply only explicitly supplied "
        "terminology pairs. No ordinary source-language word may remain untranslated merely because it appears "
        "in the source. Retain Latin text only when it is an immutable identifier, a real proper name, a product/model "
        "code, or an explicit target-side glossary term." + annotation_rule + _indonesian_clarity_instruction(tgt_lang)
        + (" " + inherited_unit_rule if inherited_unit_rule else "")
        + " Output "
        "only the final translation."
    )
    user = (
        f"SOURCE LANGUAGE: {src_lang}\nTARGET LANGUAGE: {_target_name(tgt_lang)}\n\n"
        f"SOURCE:\n{source}\n\nCURRENT TRANSLATION:\n{candidate}\n\n"
        f"DETECTED ISSUES OR WARNINGS:\n{issue_text}\n\n"
        f"UNAMBIGUOUS TERMINOLOGY CONSTRAINTS:\n{terminology}\n\n"
        "Return only the corrected final translation."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _build_translation_messages(
    protected_source: str,
    src_lang: str,
    tgt_lang: str,
    glossary_pairs: Sequence[Tuple[str, str]],
    *,
    retry_issues: Optional[Sequence[str]] = None,
) -> List[Dict[str, str]]:
    terms = "\n".join(f"- {s} => {t}" for s, t in glossary_pairs[:50]) or "(none)"
    annotations = ", ".join(dict.fromkeys(re.findall(r"[（(]\s*([\u3400-\u9fff]{1,20})\s*[）)]", protected_source or "")))
    annotation_rule = (
        " The source contains target-language annotations in parentheses: " + annotations + ". "
        "Use them as terminology evidence. If an annotation is the canonical translation of the preceding phrase, "
        "render that meaning once and omit the redundant repeated annotation."
        if annotations and str(tgt_lang or "").lower().startswith("zh") else ""
    )
    retry_note = ""
    if retry_issues:
        retry_note = (
            "\nA previous candidate failed these integrity checks. Produce a fresh translation from the source, "
            "not an edit of the failed candidate:\n" + "\n".join(f"- {x}" for x in retry_issues[:20]) + "\n"
        )
    inherited_unit_rule = implicit_quantity_unit_instruction(protected_source, src_lang, tgt_lang)
    system = (
        "You are a professional whole-document translator for a factory work group. Translate the complete "
        "source into " + _target_name(tgt_lang) + ". Read the whole document before writing and internally "
        "verify the result before output. Preserve every instruction, condition, negation, actor, object and "
        "sequence. Preserve list markers, emoji and section order. Source identifiers, codes, measurements and "
        "field values remain visible in the source: copy each one exactly and never omit, translate, rename, split "
        "or decorate it. Use only explicit unambiguous glossary pairs; "
        "never infer a reversed mapping from a common word. No ordinary source-language word may remain untranslated; "
        "retain Latin text only for immutable identifiers, real proper names, product/model codes, or explicit target-side "
        "glossary terms." + annotation_rule + _indonesian_clarity_instruction(tgt_lang)
        + (" " + inherited_unit_rule if inherited_unit_rule else "")
        + " Do not summarize, explain, add headings, mix languages or output "
        "alternatives. Output only the complete translation."
    )
    user = (
        f"SOURCE LANGUAGE: {src_lang}\nTARGET LANGUAGE: {_target_name(tgt_lang)}\n\n"
        f"UNAMBIGUOUS GLOSSARY PAIRS:\n{terms}\n"
        f"{retry_note}\nSOURCE DOCUMENT:\n{protected_source}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _independent_provider_preference(ai_client: Any, used_provider: Optional[str]) -> Optional[List[str]]:
    """Prefer a different configured provider for semantic review/retry.

    The caller permits at most one additional review call.  This ordering
    prevents the same model family from approving its own semantic mistake when
    another configured provider is available.
    """
    if not used_provider or ai_client is None:
        return None
    getter = getattr(ai_client, "get_available_providers", None)
    if not callable(getter):
        return None
    try:
        available = list(getter("chat", include_open_circuits=False) or [])
    except TypeError:
        try:
            available = list(getter("chat") or [])
        except Exception:
            return None
    except Exception:
        return None
    ordered = [p for p in available if p != used_provider]
    if used_provider in available:
        ordered.append(used_provider)
    return ordered or None


def _response_provider(resp: Any) -> Optional[str]:
    provider = getattr(resp, "_jy_provider", None)
    return str(provider) if provider else None


def _call_chat_complete(
    ai_client: Any,
    *,
    model: str,
    messages: Sequence[Mapping[str, str]],
    max_tokens: int,
    timeout: int = 90,
    provider_preference: Optional[Sequence[str]] = None,
    structured_schema: Optional[Mapping[str, Any]] = None,
    structured_name: str = "translation_source_audit",
) -> Any:
    """Issue exactly one coordinated request.

    ai_provider may move to another configured provider only when the selected
    provider has an operational failure (quota, timeout, transport or 5xx).  This
    layer never retries with alternate parameters and never asks another model to
    edit a successful translation.
    """
    kwargs = dict(
        model=model,
        messages=list(messages),
        max_tokens=max_tokens,
        temperature=0.0,
        timeout=timeout,
        translation_fast_quality=True,
    )
    if provider_preference:
        kwargs["provider_preference"] = list(provider_preference)
    if structured_schema:
        kwargs["structured_schema"] = dict(structured_schema)
        kwargs["structured_name"] = str(structured_name or "translation_source_audit")
    return ai_client.chat_complete(**kwargs)

def _extract_response_text(resp: Any) -> str:
    if not getattr(resp, "choices", None):
        return ""
    text = (resp.choices[0].message.content or "").strip()
    fenced = re.fullmatch(r"```(?:text|markdown)?\s*(.*?)\s*```", text, re.I | re.S)
    if fenced:
        text = fenced.group(1).strip()
    tagged = re.fullmatch(r"\s*<translation[^>]*>(.*?)</translation>\s*", text, re.I | re.S)
    if tagged:
        text = tagged.group(1).strip()
    for prefix in ("修正後譯文：", "修正後：", "翻譯：", "Translation:", "Terjemahan:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text


def review_translation(
    source: str,
    candidate: str,
    src_lang: str,
    tgt_lang: str,
    *,
    model: str = "",
    issues: Optional[Sequence[str]] = None,
    glossary_pairs: Optional[Sequence[Tuple[str, str]]] = None,
    ai_client: Any = None,
    provider_preference: Optional[Sequence[str]] = None,
    review_context: str = "",
) -> Optional[str]:
    """Perform one independent source-grounded adjudication call.

    High-risk Chinese factory instructions use strict structured output: the
    reviewer must first enumerate source claims, resolve shorthand, prove claim
    coverage and disclose unsupported additions before its corrected translation
    is accepted.  Other directions retain the legacy plain-text review path.
    """
    if not source or not candidate or ai_client is None:
        return None
    glossary_pairs = _merge_runtime_glossary_pairs(
        source, src_lang, tgt_lang, list(glossary_pairs or ())
    )
    immutable = inspect_immutable_spans(source)
    visible_source = immutable.protected
    frame = fsa_module.build_source_frame(source, src_lang, tgt_lang)
    use_structured_audit = bool(frame.get("active"))
    if use_structured_audit:
        messages = fsa_module.build_structured_review_messages(
            visible_source,
            candidate,
            src_lang,
            tgt_lang,
            list(issues or ()),
            glossary_pairs,
            review_context,
            frame,
        )
    else:
        messages = _build_review_messages(
            visible_source,
            candidate,
            src_lang,
            tgt_lang,
            list(issues or ()),
            glossary_pairs,
        )
        if review_context:
            messages[-1] = dict(messages[-1])
            messages[-1]["content"] += (
                "\n\nRETRIEVED VERIFIED CONTEXT AND CORRECTION CASES:\n"
                + str(review_context)[:24000]
                + "\nUse this only where it matches the current source. The source remains authoritative."
            )
    immutable_note = visible_immutable_instruction(immutable.mapping.values())
    if immutable_note:
        messages[0] = dict(messages[0])
        messages[0]["content"] += "\n" + immutable_note
    try:
        budget = max(1000, min(5200, len(visible_source) * 4 + 1000))
        response = _call_chat_complete(
            ai_client,
            model=model,
            messages=messages,
            max_tokens=budget,
            timeout=55,
            provider_preference=provider_preference,
            structured_schema=(fsa_module.structured_review_schema() if use_structured_audit else None),
            structured_name="factory_translation_source_audit",
        )
        raw = _extract_response_text(response)
        if use_structured_audit:
            payload = fsa_module.parse_structured_payload(raw)
            payload_ok, payload_issues = fsa_module.validate_structured_payload(payload, frame)
            if not payload_ok:
                logger.warning("[QualityGate] structured source audit rejected: %s", payload_issues[:12])
                return None
            raw = str(payload.get("corrected_translation") or "").strip()
        text, _report = _finalize_visible_candidate(
            source,
            raw,
            immutable,
            src_lang,
            tgt_lang,
            glossary_pairs,
            require_paragraph_fidelity=is_quality_critical(source, src_lang, tgt_lang),
        )
        if use_structured_audit and text:
            semantic_ok, semantic_issues = fsa_module.validate_translation(frame, text)
            if not semantic_ok:
                logger.warning("[QualityGate] structured translation failed local frame: %s", semantic_issues[:12])
                return None
        return text
    except Exception as exc:
        logger.warning("[QualityGate] conditional source audit unavailable: %s", exc)
        return None

def _append_missing_data_note(candidate: str, missing_literals: Sequence[str], tgt_lang: str) -> str:
    """Keep a usable translation deliverable even when a model omitted source data.

    This is a deterministic safety fallback, not a translation rewrite.  The
    model candidate is preserved and any omitted identifiers/measurements are
    attached in a clearly labelled source-data note.  That is safer than either
    silently dropping the data or discarding the whole translation.
    """
    text = (candidate or "").strip()
    values: List[str] = []
    seen = set()
    for literal in missing_literals or ():
        value = str(literal or "").strip()
        if not value or value in seen or value in text:
            continue
        seen.add(value)
        values.append(value)
    if not values:
        return text
    low = str(tgt_lang or "").lower()
    if low.startswith("zh"):
        note = "（原文資料：" + "、".join(values) + "）"
    elif low.startswith("id"):
        note = "(Data asli: " + ", ".join(values) + ")"
    else:
        note = "(Source data: " + ", ".join(values) + ")"
    return (text + "\n" + note).strip()


def _best_effort_delivery_candidate(
    source: str,
    candidate: str,
    src_lang: str,
    tgt_lang: str,
    *,
    immutable_literals: Sequence[str],
    glossary_pairs: Sequence[Tuple[str, str]],
    require_paragraph_fidelity: bool,
    initial_report: Optional[ValidationResult] = None,
) -> Tuple[Optional[str], ValidationResult]:
    """Return the best non-empty provider output instead of creating an outage.

    Validation remains strict and all issues are retained for logs/metrics.  The
    delivery policy, however, is availability-first: a successful provider
    response is never converted into a generic translation failure merely by a
    local heuristic.  Missing immutable data is surfaced explicitly in a note,
    and the result is marked non-cacheable by callers.
    """
    text = repair_identity_tokens(source, (candidate or "").strip())
    text = normalize_indonesian_factory_register(source, text, src_lang, tgt_lang)
    if not text:
        report = initial_report or ValidationResult(False, ["empty_translation"], ["empty_translation"], [])
        return None, report

    report = initial_report or validate_translation(
        source, text, src_lang, tgt_lang,
        immutable_literals=immutable_literals,
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=require_paragraph_fidelity,
    )
    missing = [
        issue.split(":", 1)[1]
        for issue in report.issues
        if issue.startswith("missing_literal:") and ":" in issue
    ]
    if missing:
        text = _append_missing_data_note(text, missing, tgt_lang)
        report = validate_translation(
            source, text, src_lang, tgt_lang,
            immutable_literals=immutable_literals,
            glossary_pairs=glossary_pairs,
            require_paragraph_fidelity=require_paragraph_fidelity,
        )
    return text, report


def _merge_semantic_validation(
    report: ValidationResult,
    candidate: str,
    semantic_validator: Optional[Callable[[str], Tuple[bool, Sequence[str]]]],
) -> ValidationResult:
    if semantic_validator is None:
        return report
    try:
        ok, external_issues = semantic_validator(candidate)
    except Exception as exc:
        logger.warning("[QualityGate] semantic validator unavailable: %s", exc)
        return report
    issues = [str(issue) for issue in (external_issues or ()) if str(issue).strip()]
    if ok and not issues:
        return report
    merged_issues = list(dict.fromkeys(list(report.issues) + issues))
    merged_hard = list(dict.fromkeys(list(report.hard_issues) + issues))
    return ValidationResult(False, merged_issues, merged_hard, list(report.warnings))


def gate_and_revise(
    source: str,
    candidate: str,
    src_lang: str,
    tgt_lang: str,
    *,
    critical: bool,
    model: str,
    immutable_literals: Optional[Iterable[str]] = None,
    glossary_pairs: Optional[Sequence[Tuple[str, str]]] = None,
    ai_client: Any = None,
    force_review: bool = False,
    used_provider: Optional[str] = None,
    review_context: str = "",
    semantic_validator: Optional[Callable[[str], Tuple[bool, Sequence[str]]]] = None,
    require_review_success: bool = False,
) -> Dict[str, Any]:
    """Validate and, for high-risk messages, independently reconstruct once.

    Ordinary messages remain single-call.  Factory notices, announcements and
    messages matched to verified correction cases can request one additional
    source-grounded review.  The reviewer receives the original source, not just
    the first candidate, and a different configured provider is preferred.  When
    ``require_review_success`` is true, review success controls authoritative
    acceptance and cacheability, not basic availability.  A first candidate that
    already passed all deterministic source, glossary, immutable-data and semantic
    checks remains deliverable as degraded/non-cacheable when the reviewer is
    unavailable or returns an invalid mutation.
    """
    glossary_pairs = _merge_runtime_glossary_pairs(
        source, src_lang, tgt_lang, list(glossary_pairs or ())
    )
    immutable_values = list(immutable_literals or ())
    source_frame = fsa_module.build_source_frame(source, src_lang, tgt_lang)

    caller_semantic_validator = semantic_validator
    def _combined_semantic_validator(text: str) -> Tuple[bool, Sequence[str]]:
        combined_issues: List[str] = []
        if caller_semantic_validator is not None:
            try:
                caller_ok, caller_issues = caller_semantic_validator(text)
            except Exception as exc:
                logger.warning("[QualityGate] caller semantic validator unavailable: %s", exc)
                caller_ok, caller_issues = True, []
            if not caller_ok or caller_issues:
                combined_issues.extend(str(x) for x in (caller_issues or ()) if str(x).strip())
        frame_ok, frame_issues = fsa_module.validate_translation(source_frame, text)
        if not frame_ok or frame_issues:
            combined_issues.extend(str(x) for x in (frame_issues or ()) if str(x).strip())
        combined_issues = list(dict.fromkeys(combined_issues))
        return not combined_issues, combined_issues

    semantic_validator = _combined_semantic_validator if (caller_semantic_validator is not None or source_frame.get("active")) else None
    candidate = repair_identity_tokens(source, candidate)
    candidate = normalize_indonesian_factory_register(
        source, candidate, src_lang, tgt_lang
    )
    report = validate_translation(
        source, candidate, src_lang, tgt_lang,
        immutable_literals=immutable_values,
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=critical,
    )
    report = _merge_semantic_validation(report, candidate, semantic_validator)

    # Preserve the low-latency single-call path by default.  A second call is
    # permitted only when the caller explicitly classified the message as
    # high-risk or matched it to a verified correction case.
    review_requested = bool(force_review or fsa_module.should_force_review(source_frame))
    should_review = bool(ai_client is not None and review_requested)
    review_succeeded = False
    review_failure_reason = ""
    if should_review:
        preference = _independent_provider_preference(ai_client, used_provider)
        reviewed = review_translation(
            source,
            candidate,
            src_lang,
            tgt_lang,
            model=model,
            issues=(report.issues if report.issues else ["independent_source_semantic_audit"]),
            glossary_pairs=glossary_pairs,
            ai_client=ai_client,
            provider_preference=preference,
            review_context=review_context,
        )
        if reviewed:
            reviewed = repair_identity_tokens(source, reviewed)
            reviewed = normalize_indonesian_factory_register(source, reviewed, src_lang, tgt_lang)
            reviewed_report = validate_translation(
                source, reviewed, src_lang, tgt_lang,
                immutable_literals=immutable_values,
                glossary_pairs=glossary_pairs,
                require_paragraph_fidelity=critical,
            )
            reviewed_report = _merge_semantic_validation(
                reviewed_report, reviewed, semantic_validator
            )
            if reviewed_report.ok:
                review_succeeded = True
                return {
                    "ok": True,
                    "text": reviewed,
                    "issues": reviewed_report.issues,
                    "hard_issues": [],
                    "warnings": reviewed_report.warnings,
                    "reviewed": True,
                    "review_requested": review_requested,
                    "review_succeeded": True,
                    "degraded": False,
                    "cacheable": True,
                    "path": "independent_source_review_passed",
                }
            # A reviewer that fails deterministic integrity checks must never
            # replace an already valid first translation.
            if report.ok:
                return {
                    "ok": True,
                    "text": candidate,
                    "issues": reviewed_report.issues,
                    "hard_issues": reviewed_report.hard_issues,
                    "warnings": reviewed_report.warnings,
                    "reviewed": True,
                    "review_requested": review_requested,
                    "review_succeeded": False,
                    "degraded": True,
                    "cacheable": False,
                    "path": "independent_review_rejected_original_kept",
                }
            review_failure_reason = "independent_review_rejected"
        else:
            review_failure_reason = "independent_review_unavailable"
    elif review_requested:
        review_failure_reason = "independent_review_provider_unavailable"

    # Provider review is intentionally not a single point of failure.  For the
    # complete polishing/large-bar scheduling frame, reconstruct a conservative
    # target from source-proven slots whenever the model candidate still violates
    # the semantic contract.  This also protects paraphrases when the review API
    # is unavailable; incomplete/other scenarios never enter this fallback.
    if not report.ok and source_frame.get("active"):
        deterministic = fsa_module.deterministic_rebuild(source_frame)
        if deterministic:
            deterministic = repair_identity_tokens(source, deterministic)
            deterministic = normalize_indonesian_factory_register(
                source, deterministic, src_lang, tgt_lang
            )
            deterministic_report = validate_translation(
                source, deterministic, src_lang, tgt_lang,
                immutable_literals=immutable_values,
                glossary_pairs=glossary_pairs,
                require_paragraph_fidelity=critical,
            )
            deterministic_report = _merge_semantic_validation(
                deterministic_report, deterministic, semantic_validator
            )
            if deterministic_report.ok:
                return {
                    "ok": True,
                    "text": deterministic,
                    "issues": deterministic_report.issues,
                    "hard_issues": [],
                    "warnings": deterministic_report.warnings,
                    "reviewed": bool(should_review),
                    "review_requested": review_requested,
                    "review_succeeded": False,
                    "degraded": bool(review_requested),
                    "cacheable": True,
                    "path": "deterministic_source_frame_rebuild",
                }

    if report.ok:
        # The reviewer is an additional adjudicator, not a single point of
        # failure.  A network outage, missing second provider, timeout, or bad
        # reviewer mutation cannot erase a first translation that independently
        # passed every deterministic source-grounded check.  Keep it visible,
        # but never cache or learn from the degraded path.
        review_issue = review_failure_reason if review_requested and not review_succeeded else ""
        recorded_issues = list(dict.fromkeys(
            list(report.issues) + ([review_issue] if review_issue else [])
        ))
        recorded_warnings = list(dict.fromkeys(
            list(report.warnings) + ([review_issue] if review_issue else [])
        ))
        if not review_requested:
            path = "single_api_local_validation"
        elif review_failure_reason == "independent_review_rejected":
            path = "independent_review_rejected_original_kept"
        else:
            path = "review_unavailable_original_kept"
        return {
            "ok": True,
            "text": candidate,
            "issues": recorded_issues,
            "hard_issues": [],
            "warnings": recorded_warnings,
            "reviewed": bool(should_review),
            "review_requested": review_requested,
            "review_succeeded": False,
            "degraded": bool(review_requested),
            "cacheable": not bool(review_requested),
            "path": path,
        }

    # Strict review policy still blocks a first candidate that failed local
    # source-grounded validation.  The availability fix applies only to a clean
    # primary result; it does not turn malformed or semantically unsafe text into
    # a deliverable fallback.
    if review_requested and require_review_success:
        reason = review_failure_reason or "required_source_review_not_completed"
        return {
            "ok": False,
            "text": None,
            "issues": list(dict.fromkeys(list(report.issues) + [reason])),
            "hard_issues": list(dict.fromkeys(list(report.hard_issues) + [reason])),
            "warnings": report.warnings,
            "reviewed": bool(should_review),
            "review_requested": True,
            "review_succeeded": False,
            "degraded": True,
            "cacheable": False,
            "path": "required_source_review_failed",
        }

    best_text, best_report = _best_effort_delivery_candidate(
        source,
        candidate,
        src_lang,
        tgt_lang,
        immutable_literals=immutable_values,
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=critical,
        initial_report=report,
    )
    if best_text:
        semantic_ok = True
        semantic_issues: List[str] = []
        if semantic_validator is not None:
            try:
                semantic_ok, raw_semantic_issues = semantic_validator(best_text)
                semantic_issues = [
                    str(issue) for issue in (raw_semantic_issues or ()) if str(issue).strip()
                ]
            except Exception as exc:
                logger.warning("[QualityGate] semantic validator unavailable on fallback: %s", exc)
                semantic_ok = True
        if semantic_issues:
            best_report = ValidationResult(
                False,
                list(dict.fromkeys(list(best_report.issues) + semantic_issues)),
                list(dict.fromkeys(list(best_report.hard_issues) + semantic_issues)),
                list(best_report.warnings),
            )
        return {
            # Local heuristic failures remain availability-first.  Only a
            # verified-correction semantic violation can reject best-effort text.
            "ok": bool(semantic_ok),
            "text": best_text,
            "issues": best_report.issues,
            "hard_issues": best_report.hard_issues,
            "warnings": best_report.warnings,
            "reviewed": bool(should_review),
            "review_requested": review_requested,
            "review_succeeded": False,
            "degraded": True,
            "cacheable": False,
            "path": "best_effort_after_review" if review_requested else "best_effort_quality_warning",
        }

    return {
        "ok": False,
        "text": None,
        "issues": report.issues,
        "hard_issues": report.hard_issues,
        "warnings": report.warnings,
        "reviewed": bool(should_review),
        "review_requested": review_requested,
        "review_succeeded": False,
        "degraded": True,
        "cacheable": False,
        "path": "empty_translation_blocked",
    }


def _translate_candidate(
    protected_source: str,
    src_lang: str,
    tgt_lang: str,
    *,
    model: str,
    glossary_pairs: Sequence[Tuple[str, str]],
    ai_client: Any,
    retry_issues: Optional[Sequence[str]] = None,
    provider_preference: Optional[Sequence[str]] = None,
) -> Tuple[str, Optional[str]]:
    messages = _build_translation_messages(
        protected_source, src_lang, tgt_lang, glossary_pairs, retry_issues=retry_issues
    )
    budget = max(1600, min(8000, len(protected_source) * 4 + 1200))
    resp = _call_chat_complete(
        ai_client, model=model, messages=messages, max_tokens=budget,
        provider_preference=provider_preference,
    )
    return _extract_response_text(resp), _response_provider(resp)


def _finalize_protected_candidate(
    source: str,
    protected_source: str,
    protected_candidate: str,
    envelope: ProtectedText,
    src_lang: str,
    tgt_lang: str,
    glossary_pairs: Sequence[Tuple[str, str]],
    *,
    require_paragraph_fidelity: bool,
) -> Tuple[Optional[str], ValidationResult]:
    canonical = canonicalize_placeholders(protected_candidate or "", envelope.mapping)
    protected_report = _validate_protected_candidate(
        protected_source, canonical, envelope.mapping, src_lang, tgt_lang,
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=require_paragraph_fidelity,
    )
    if not protected_report.ok:
        return None, protected_report
    restored = restore_immutable_spans(canonical, envelope.mapping).strip()
    restored = normalize_indonesian_factory_register(
        source, restored, src_lang, tgt_lang
    )
    final_report = validate_translation(
        source, restored, src_lang, tgt_lang,
        immutable_literals=envelope.mapping.values(),
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=require_paragraph_fidelity,
    )
    return (restored if final_report.ok else None), final_report


def _finalize_visible_candidate(
    source: str,
    candidate: str,
    envelope: ProtectedText,
    src_lang: str,
    tgt_lang: str,
    glossary_pairs: Sequence[Tuple[str, str]],
    *,
    require_paragraph_fidelity: bool,
) -> Tuple[Optional[str], ValidationResult]:
    """Validate a translation produced from a source with visible immutable data."""
    restored = normalize_indonesian_factory_register(
        source, (candidate or "").strip(), src_lang, tgt_lang
    )
    report = validate_translation(
        source,
        restored,
        src_lang,
        tgt_lang,
        immutable_literals=envelope.mapping.values(),
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=require_paragraph_fidelity,
    )
    return (restored if report.ok else None), report


def translate_quality_critical_document(
    source: str,
    src_lang: str,
    tgt_lang: str,
    *,
    model: str,
    glossary_pairs: Optional[Sequence[Tuple[str, str]]] = None,
    ai_client: Any = None,
    fallback_translate: Optional[Callable[[str, str, str], Optional[str]]] = None,
) -> Dict[str, Any]:
    """Whole-document translation with one conditional fresh retry.

    The first provider call is the normal path. A second call is made only when
    deterministic validation finds a hard integrity defect; the retry starts
    from the protected source and detected issues, never from a free-form edit.
    """
    if ai_client is None:
        try:
            import ai_provider as ai_client  # type: ignore
        except Exception:
            ai_client = None
    if ai_client is None:
        return {
            "ok": False, "text": None, "issues": ["ai_provider_unavailable"],
            "hard_issues": ["ai_provider_unavailable"], "warnings": [],
            "reviewed": False, "degraded": True, "cacheable": False,
            "path": "single_api_unavailable", "provider_path": "none",
        }

    glossary_pairs = _merge_runtime_glossary_pairs(
        source, src_lang, tgt_lang, list(glossary_pairs or ())
    )
    immutable = inspect_immutable_spans(source)
    visible_source = immutable.protected
    messages = _build_translation_messages(
        visible_source, src_lang, tgt_lang, glossary_pairs
    )
    immutable_note = visible_immutable_instruction(immutable.mapping.values())
    if immutable_note:
        messages[0] = dict(messages[0])
        messages[0]["content"] = messages[0]["content"] + "\n" + immutable_note

    try:
        budget = max(1600, min(8000, len(visible_source) * 4 + 1200))
        response = _call_chat_complete(
            ai_client,
            model=model,
            messages=messages,
            max_tokens=budget,
        )
        raw = _extract_response_text(response)
        provider = _response_provider(response)
        text, report = _finalize_visible_candidate(
            source, raw, immutable,
            src_lang, tgt_lang, glossary_pairs,
            require_paragraph_fidelity=True,
        )
        if text:
            return {
                "ok": True, "text": text, "issues": report.issues,
                "hard_issues": [], "warnings": report.warnings,
                "reviewed": False, "degraded": False, "cacheable": True,
                "path": "single_api_whole_document", "provider_path": provider or "primary",
            }
        # No second provider call.  A failed local check only controls cache
        # admission; the best non-empty first response is still returned.
        best_text, best_report = _best_effort_delivery_candidate(
            source,
            raw,
            src_lang,
            tgt_lang,
            immutable_literals=list(immutable.mapping.values()),
            glossary_pairs=glossary_pairs,
            require_paragraph_fidelity=True,
            initial_report=report,
        )
        if best_text:
            return {
                "ok": True, "text": best_text, "issues": best_report.issues,
                "hard_issues": best_report.hard_issues, "warnings": best_report.warnings,
                "reviewed": False,
                "degraded": True, "cacheable": False,
                "path": "best_effort_whole_document",
                "provider_path": provider or "primary",
            }
        return {
            "ok": False, "text": None, "issues": report.issues,
            "hard_issues": report.hard_issues, "warnings": report.warnings,
            "reviewed": False, "degraded": True, "cacheable": False,
            "path": "empty_translation_blocked", "provider_path": provider or "primary",
        }
    except Exception as exc:
        logger.warning("[QualityGate] single document call unavailable: %s", exc)
        return {
            "ok": False, "text": None, "issues": ["single_api_unavailable"],
            "hard_issues": ["single_api_unavailable"], "warnings": [],
            "reviewed": False, "degraded": True, "cacheable": False,
            "path": "single_api_unavailable", "provider_path": "none",
        }

def ensure_delivery_safe_translation(
    source: str,
    candidate: str,
    src_lang: str,
    tgt_lang: str,
    *,
    model: str,
    glossary_pairs: Optional[Sequence[Tuple[str, str]]] = None,
    ai_client: Any = None,
    fallback_translate: Optional[Callable[[str, str, str], Optional[str]]] = None,
) -> Dict[str, Any]:
    """Final local-only availability boundary.

    No network call is made here.  Validation still controls cache admission and
    produces diagnostics, but a non-empty provider translation is not discarded
    solely because a heuristic is unhappy.  Missing immutable data is attached
    explicitly and degraded results are returned as non-cacheable.
    """
    source = source or ""
    candidate = repair_identity_tokens(source, candidate)
    candidate = normalize_indonesian_factory_register(
        source, candidate, src_lang, tgt_lang
    )
    glossary_pairs = _merge_runtime_glossary_pairs(
        source, src_lang, tgt_lang, list(glossary_pairs or ())
    )
    envelope = inspect_immutable_spans(source)
    require_paragraph_fidelity = is_quality_critical(source, src_lang, tgt_lang)
    report = validate_translation(
        source, candidate, src_lang, tgt_lang,
        immutable_literals=envelope.mapping.values(),
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=require_paragraph_fidelity,
    )
    if report.ok:
        return {
            "ok": True,
            "text": candidate,
            "issues": report.issues,
            "hard_issues": [],
            "warnings": report.warnings,
            "reviewed": False,
            "degraded": False,
            "cacheable": True,
            "path": "final_local_validation",
        }

    best_text, best_report = _best_effort_delivery_candidate(
        source,
        candidate,
        src_lang,
        tgt_lang,
        immutable_literals=list(envelope.mapping.values()),
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=require_paragraph_fidelity,
        initial_report=report,
    )
    return {
        "ok": bool(best_text),
        "text": best_text,
        "issues": best_report.issues,
        "hard_issues": best_report.hard_issues,
        "warnings": best_report.warnings,
        "reviewed": False,
        "degraded": bool(best_text),
        "cacheable": False,
        "path": "final_local_warning" if best_text else "empty_translation_blocked",
    }

def translation_failure_message(tgt_lang: str) -> str:
    """Deprecated compatibility shim.

    User-visible generic failure translations were removed.  Callers should
    return ``None``/empty and log diagnostics instead of sending or caching an
    artificial translated error message.
    """
    return ""
