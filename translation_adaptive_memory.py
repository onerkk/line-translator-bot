"""Conservative reuse of approved near-identical shop-floor translations.

Only explicit lexical variants and unambiguous numeric quantity slots can be
adapted locally. Every proposed target must still pass the caller's full current
semantic and delivery validators. Fuzzy similarity alone never authorizes reuse.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from collections import Counter
from functools import lru_cache
import re
from typing import Mapping

from factory_source_understanding import analyze
from translation_source_identity import canonical_source_key

ADAPTIVE_MEMORY_VERSION = "2026-09-05.1"
_UNITS = (r"公斤|公克|公噸|毫米|公分|公尺|分鐘|小時|噸|米|把|捆|包|箱|支|根|件|雙|個|"
          r"kg\b|mm\b|cm\b|meter\b|m\b|ton\b|bundel\b|bundle\b|batang\b|"
          r"pak\b|kotak\b|buah\b|pasang\b|menit\b|jam\b")
_QUANTITY = re.compile(r"(?<![A-Za-z0-9_.,/:-])(\d+(?:[.,]\d+)?)(?=\s*(?:" + _UNITS + r"))", re.I)
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.,])\d+(?:[.,]\d+)?(?![A-Za-z0-9_.,])")


def _numeric(value):
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation:
        return None


@lru_cache(maxsize=8192)
def _shape(text):
    slots = []
    def replace(match):
        slots.append(match.group(1))
        return f"__QUANTITY_{len(slots)}__"
    skeleton = _QUANTITY.sub(replace, text)
    return canonical_source_key(skeleton), tuple(slots)


def _adapt_quantity(query, source, target):
    q_shape, q_slots = _shape(query)
    s_shape, s_slots = _shape(source)
    if q_shape != s_shape or not q_slots or len(q_slots) != len(s_slots):
        return None
    # Repeated source values can refer to different roles. Without an explicit
    # bilingual alignment we cannot know which occurrence changed.
    old_values = [_numeric(n) for n in s_slots]
    if None in old_values or len(set(old_values)) != len(old_values):
        return None
    source_numbers = [_numeric(m.group()) for m in _NUMBER.finditer(source)]
    target_matches = list(_NUMBER.finditer(target))
    if Counter(source_numbers) != Counter(_numeric(m.group()) for m in target_matches):
        return None
    replacements = {}
    for old, new, value in zip(s_slots, q_slots, old_values):
        matches = [m for m in target_matches if _numeric(m.group()) == value]
        if len(matches) != 1 or source_numbers.count(value) != 1:
            return None
        # No unit conversion, inferred totals, or ambiguous thousands formats.
        if any(re.fullmatch(r"\d{1,3}[.,]\d{3}", token) for token in (old, new, matches[0].group())):
            return None
        replacements[matches[0].span()] = new
    for (start, end), new in sorted(replacements.items(), reverse=True):
        target = target[:start] + new + target[end:]
    return target


@lru_cache(maxsize=8192)
def _normalized_source(text, src, protected_names):
    return analyze(text, src, protected_names=protected_names)["normalized"]


def propose(source, cases, src, tgt, *, protected_names=()):
    """Return an auditable candidate, or None when equivalence is uncertain."""
    if (src, tgt) not in {("zh", "id"), ("id", "zh")}:
        return None
    protected_names = tuple(sorted(str(n) for n in protected_names or ()))
    current = _normalized_source(source, src, protected_names)
    current_key = canonical_source_key(current)
    proposals = []
    for case in cases or ():
        if (not isinstance(case, Mapping) or case.get("direction") != src + "2" + tgt
                or not (case.get("verified_correction") or case.get("origin") == "factory_knowledge")):
            continue
        reference, target = str(case.get("source") or ""), str(case.get("target") or "")
        if not reference or not target or canonical_source_key(source) == canonical_source_key(reference):
            continue
        normalized = _normalized_source(reference, src, protected_names)
        if current_key == canonical_source_key(normalized):
            candidate, kind = target, "recognized_source_variant"
        else:
            candidate, kind = _adapt_quantity(current, normalized, target), "approved_quantity_template"
        if candidate:
            proposals.append({"text": candidate, "kind": kind,
                              "case_id": case.get("case_id", ""), "reference_source": reference})
    # Conflicting approvals for different source variants require fresh source
    # translation. Retrieval rank is not permission to choose an arbitrary one.
    unique = {canonical_source_key(row["text"]) for row in proposals}
    return proposals[0] if len(unique) == 1 else None
