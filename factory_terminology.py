"""High-throughput factory terminology retrieval shared by text and OCR flows.

The glossary may contain hundreds or thousands of plant terms, abbreviations,
equipment names and organizational-unit labels.  Sending the entire glossary to
an LLM is slow and reduces translation quality.  This module builds a reusable
longest-match index, retrieves only source-grounded terms, and adds deterministic
organization-unit semantics such as ``一課`` and ``一股股長``.

The module does not translate complete sentences and does not contain sentence-
specific patches.  It supplies terminology constraints and OCR recognition hints
that are consumed by the standard translation pipeline.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import glossary_policy as gp_module

FACTORY_TERMINOLOGY_API_VERSION = 1
FACTORY_TERMINOLOGY_BUILD_ID = "2026-07-18.2-trie-shared-text-ocr"

_CACHE_LOCK = threading.RLock()
_ENGINE_CACHE: Dict[Tuple[int, int], "FactoryTerminologyEngine"] = {}
_TRIE_END = object()

_ZH_NUMERAL_VALUES = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

_ORG_SUFFIXES = ("課", "股", "處", "部", "組", "班", "線", "站")
_OCR_PRIORITY_SUFFIXES = (
    "機", "站", "課", "股", "處", "部", "組", "班", "線", "盤", "爐", "表",
    "單", "材", "棒", "包", "箱", "油", "門", "秤", "車", "架", "刀", "輪",
)


@dataclass(frozen=True)
class FactoryTermMatch:
    matched_text: str
    source_term: str
    target_term: str
    mode: str
    start: int
    end: int
    priority: int = 50
    note: str = ""
    category: str = ""


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return text.replace("\u3000", " ")


def _list_value(row: Mapping[str, Any], *keys: str) -> List[str]:
    out: List[str] = []
    for key in keys:
        raw = row.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, (list, tuple, set)):
            for item in raw:
                value = _normalize_text(item).strip()
                if value and value not in out:
                    out.append(value)
    return out


def source_aliases(value: Any) -> List[str]:
    row = value if isinstance(value, Mapping) else {}
    return _list_value(row, "aliases_zh", "source_aliases", "aliases")


def target_aliases(value: Any) -> List[str]:
    row = value if isinstance(value, Mapping) else {}
    return _list_value(row, "aliases_id", "target_aliases")


def chinese_number_to_arabic(raw: str) -> Optional[str]:
    token = _normalize_text(raw).strip()
    if not token:
        return None
    if token.isdigit():
        return str(int(token)) if len(token) > 1 else token
    if token in _ZH_NUMERAL_VALUES:
        return str(_ZH_NUMERAL_VALUES[token])
    if "十" in token:
        left, right = token.split("十", 1)
        tens = 1 if left == "" else _ZH_NUMERAL_VALUES.get(left)
        ones = 0 if right == "" else _ZH_NUMERAL_VALUES.get(right)
        if tens is not None and ones is not None:
            return str(tens * 10 + ones)
    if all(ch in _ZH_NUMERAL_VALUES for ch in token):
        # OCR sometimes emits digit-style Chinese numerals, e.g. 一二 -> 12.
        return "".join(str(_ZH_NUMERAL_VALUES[ch]) for ch in token)
    return None


def _normalize_id(value: str) -> str:
    text = _normalize_text(value).casefold().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def _overlaps(start: int, end: int, spans: Sequence[Tuple[int, int]]) -> bool:
    return any(start < old_end and end > old_start for old_start, old_end in spans)


def collect_organization_matches(text: str, src_lang: str, tgt_lang: str) -> List[FactoryTermMatch]:
    """Resolve numbered factory organizational units without romanizing them.

    Chinese labels such as ``一課`` and ``一股股長`` are structural unit names,
    not personal names.  The number is converted to an Arabic numeral and the
    organization level is translated consistently.
    """
    source = _normalize_text(text)
    src = (src_lang or "").lower()
    tgt = (tgt_lang or "").lower()
    matches: List[FactoryTermMatch] = []
    occupied: List[Tuple[int, int]] = []

    if src.startswith("zh") and tgt.startswith("id"):
        patterns: Sequence[Tuple[re.Pattern[str], str]] = (
            (re.compile(r"第?(?P<num>[零〇一二兩三四五六七八九十0-9]{1,4})股股長"), "kepala regu {n}"),
            (re.compile(r"第?(?P<num>[零〇一二兩三四五六七八九十0-9]{1,4})課課長"), "kepala seksi {n}"),
            (re.compile(r"第?(?P<num>[零〇一二兩三四五六七八九十0-9]{1,4})課"), "Seksi {n}"),
            (re.compile(r"第?(?P<num>[零〇一二兩三四五六七八九十0-9]{1,4})股"), "Regu {n}"),
        )
        for pattern, target_template in patterns:
            for found in pattern.finditer(source):
                if _overlaps(found.start(), found.end(), occupied):
                    continue
                number = chinese_number_to_arabic(found.group("num"))
                if number is None:
                    continue
                matched = found.group(0)
                matches.append(FactoryTermMatch(
                    matched_text=matched,
                    source_term=matched,
                    target_term=target_template.format(n=number),
                    mode="hard",
                    start=found.start(),
                    end=found.end(),
                    priority=130,
                    category="organization_unit",
                ))
                occupied.append((found.start(), found.end()))

        standalone = (
            ("處長", "kepala divisi", 120),
            ("課長", "kepala seksi", 115),
            ("股長", "kepala regu", 115),
            ("班長", "kepala regu", 105),
        )
        for source_term, target_term, priority in standalone:
            for found in re.finditer(re.escape(source_term), source):
                if _overlaps(found.start(), found.end(), occupied):
                    continue
                matches.append(FactoryTermMatch(
                    matched_text=source_term,
                    source_term=source_term,
                    target_term=target_term,
                    mode="hard",
                    start=found.start(),
                    end=found.end(),
                    priority=priority,
                    category="organization_role",
                ))
                occupied.append((found.start(), found.end()))

    elif src.startswith("id") and tgt.startswith("zh"):
        normalized = _normalize_id(source)
        reverse_patterns: Sequence[Tuple[re.Pattern[str], str]] = (
            (re.compile(r"\bkepala\s+regu\s+(?P<num>\d{1,3})\b", re.I), "{n}股股長"),
            (re.compile(r"\bkepala\s+seksi\s+(?P<num>\d{1,3})\b", re.I), "{n}課課長"),
            (re.compile(r"\bseksi\s+(?P<num>\d{1,3})\b", re.I), "{n}課"),
            (re.compile(r"\bregu\s+(?P<num>\d{1,3})\b", re.I), "{n}股"),
        )
        for pattern, target_template in reverse_patterns:
            for found in pattern.finditer(normalized):
                if _overlaps(found.start(), found.end(), occupied):
                    continue
                matched = found.group(0)
                matches.append(FactoryTermMatch(
                    matched_text=matched,
                    source_term=matched,
                    target_term=target_template.format(n=found.group("num")),
                    mode="hard",
                    start=found.start(),
                    end=found.end(),
                    priority=120,
                    category="organization_unit",
                ))
                occupied.append((found.start(), found.end()))

    ordered = sorted(matches, key=lambda item: (item.start, -(item.end - item.start), -item.priority))
    # Repeated mentions need one terminology constraint, not repeated prompt rows.
    # Preserve the first occurrence for source grounding and compactness.
    deduped: List[FactoryTermMatch] = []
    seen_mapping = set()
    for match in ordered:
        key = (match.matched_text.casefold(), match.target_term.casefold())
        if key in seen_mapping:
            continue
        seen_mapping.add(key)
        deduped.append(match)
    return deduped


class FactoryTerminologyEngine:
    """Immutable longest-match index for one normalized glossary snapshot.

    A character trie avoids rescanning or sorting the full glossary per request.
    Lookup cost is driven primarily by source-text length and matching prefix
    depth, so a large group of terms sharing the same first character does not
    degrade into a full bucket scan.
    """

    def __init__(self, glossary: Mapping[str, Any] | None):
        self.glossary = gp_module.normalize_glossary(glossary or {})
        self._trie: Dict[Any, Any] = {}
        self._surface_count = 0
        self._trie_nodes = 1
        self._ocr_terms: List[Tuple[int, str]] = []
        self._build()

    def _insert_surface(self, surface: str, canonical_source: str, row: Dict[str, Any]) -> None:
        node = self._trie
        for char in surface:
            child = node.get(char)
            if child is None:
                child = {}
                node[char] = child
                self._trie_nodes += 1
            node = child
        node.setdefault(_TRIE_END, []).append((surface, canonical_source, row))
        self._surface_count += 1

    def _build(self) -> None:
        ocr_candidates: List[Tuple[int, str]] = []
        for source_term, raw_row in self.glossary.items():
            row = gp_module.normalize_entry(source_term, raw_row)
            surfaces = [source_term] + source_aliases(row)
            seen_surface = set()
            for surface in surfaces:
                surface = _normalize_text(surface).strip()
                if not surface or surface in seen_surface:
                    continue
                seen_surface.add(surface)
                self._insert_surface(surface, source_term, row)
            score = int(row.get("priority", 50) or 50)
            category = str(row.get("category") or row.get("domain") or "")
            if row.get("ocr_hint") is True:
                score += 100
            if re.search(r"[A-Za-z0-9]", source_term):
                score += 40
            if source_term.endswith(_OCR_PRIORITY_SUFFIXES):
                score += 15
            if category in {"equipment", "station", "organization", "code", "erp", "process"}:
                score += 30
            if 2 <= len(source_term) <= 20:
                ocr_candidates.append((score, source_term))

        # A single surface may intentionally map to multiple entries. Resolve it
        # deterministically by glossary priority when the source is encountered.
        stack = [self._trie]
        while stack:
            node = stack.pop()
            terminals = node.get(_TRIE_END)
            if terminals:
                terminals.sort(
                    key=lambda item: int(item[2].get("priority", 50) or 50),
                    reverse=True,
                )
            stack.extend(child for key, child in node.items() if key is not _TRIE_END)
        self._ocr_terms = sorted(ocr_candidates, key=lambda item: (-item[0], len(item[1]), item[1]))

    def match_zh(self, text: str, *, limit: int = 80) -> List[FactoryTermMatch]:
        source = _normalize_text(text)
        results: List[FactoryTermMatch] = []
        seen_canonical = set()
        occupied_until = 0
        for position in range(len(source)):
            if position < occupied_until:
                continue
            node = self._trie
            cursor = position
            candidates: List[Tuple[int, str, str, Dict[str, Any]]] = []
            while cursor < len(source):
                node = node.get(source[cursor])
                if node is None:
                    break
                cursor += 1
                for surface, canonical_source, row in node.get(_TRIE_END, ()):
                    candidates.append((cursor, surface, canonical_source, row))
            if not candidates:
                continue
            candidates.sort(
                key=lambda item: (
                    item[0] - position,
                    int(item[3].get("priority", 50) or 50),
                ),
                reverse=True,
            )
            for end, surface, canonical_source, row in candidates:
                canonical_target = gp_module.canonical_target(row)
                mode = gp_module.translation_mode(row)
                if not canonical_target or mode == "disabled":
                    continue
                dedupe_key = (canonical_source, canonical_target)
                if dedupe_key in seen_canonical:
                    continue
                note = str(row.get("note_id") or row.get("note_zh") or "").strip()
                results.append(FactoryTermMatch(
                    matched_text=surface,
                    source_term=canonical_source,
                    target_term=canonical_target,
                    mode=mode,
                    start=position,
                    end=end,
                    priority=int(row.get("priority", 50) or 50),
                    note=note,
                    category=str(row.get("category") or row.get("domain") or ""),
                ))
                occupied_until = end
                seen_canonical.add(dedupe_key)
                break
            if len(results) >= max(1, int(limit or 1)):
                break
        return results

    def ocr_hint(self, *, max_items: int = 100) -> str:
        terms: List[str] = []
        seen = set()
        for _score, term in self._ocr_terms:
            if term in seen:
                continue
            seen.add(term)
            terms.append(term)
            if len(terms) >= max(1, int(max_items or 1)):
                break
        if not terms:
            return ""
        return (
            "廠內詞形辨識提示（僅協助 OCR 辨認原字，不可翻譯、不可依詞庫補寫圖片中不存在的字）:\n"
            + "、".join(terms)
        )

    def health(self) -> Dict[str, Any]:
        return {
            "api_version": FACTORY_TERMINOLOGY_API_VERSION,
            "build_id": FACTORY_TERMINOLOGY_BUILD_ID,
            "glossary_entries": len(self.glossary),
            "surface_buckets": len(self._trie),  # compatibility with prior health readers
            "trie_roots": len(self._trie),
            "trie_nodes": self._trie_nodes,
            "indexed_surfaces": self._surface_count,
            "ocr_candidates": len(self._ocr_terms),
        }


def invalidate_cache() -> None:
    with _CACHE_LOCK:
        _ENGINE_CACHE.clear()


def get_engine(glossary: Mapping[str, Any] | None) -> FactoryTerminologyEngine:
    glossary = glossary or {}
    key = (id(glossary), len(glossary))
    with _CACHE_LOCK:
        engine = _ENGINE_CACHE.get(key)
        if engine is None:
            engine = FactoryTerminologyEngine(glossary)
            _ENGINE_CACHE.clear()  # only the current glossary is useful in production
            _ENGINE_CACHE[key] = engine
        return engine


def collect_applicable_pairs(
    src_text: str,
    glossary: Mapping[str, Any] | None,
    src_lang: str,
    tgt_lang: str,
    *,
    safe_reverse_index: Optional[Mapping[str, Mapping[str, str]]] = None,
    limit: int = 100,
) -> List[Tuple[str, str]]:
    src = (src_lang or "").lower()
    tgt = (tgt_lang or "").lower()
    pairs: List[Tuple[str, str]] = []

    if src.startswith("zh") and tgt.startswith("id"):
        for match in collect_organization_matches(src_text, src, tgt):
            if match.mode == "hard":
                pairs.append((match.matched_text, match.target_term))
        for match in get_engine(glossary).match_zh(src_text, limit=limit):
            if match.mode == "hard":
                pairs.append((match.matched_text, match.target_term))

    elif src.startswith("id") and tgt.startswith("zh"):
        for match in collect_organization_matches(src_text, src, tgt):
            if match.mode == "hard":
                pairs.append((match.matched_text, match.target_term))
        normalized = _normalize_id(src_text)
        for norm, row in (safe_reverse_index or {}).items():
            if re.search(r"(?<![a-z0-9])" + re.escape(str(norm)) + r"(?![a-z0-9])", normalized):
                pairs.append((str(row.get("source_term") or norm), str(row.get("target_term") or "")))

    deduped: List[Tuple[str, str]] = []
    seen = set()
    for source_term, target_term in pairs:
        key = (source_term.casefold(), target_term.casefold())
        if source_term and target_term and key not in seen:
            seen.add(key)
            deduped.append((source_term, target_term))
            if len(deduped) >= limit:
                break
    return deduped


def build_translation_prompt(
    src_text: str,
    glossary: Mapping[str, Any] | None,
    src_lang: str,
    tgt_lang: str,
    *,
    safe_reverse_index: Optional[Mapping[str, Mapping[str, str]]] = None,
    max_items: int = 40,
) -> str:
    src = (src_lang or "").lower()
    tgt = (tgt_lang or "").lower()
    lines: List[str] = []

    org_matches = collect_organization_matches(src_text, src, tgt)
    if org_matches:
        lines.append("<factory_organization_terms>")
        lines.append(
            "These are factory organization levels, not person names. Never romanize Chinese unit labels. "
            "For example, 一股 is Regu 1, not Yigu; 一課 is Seksi 1, not Departemen 1."
        )
        for match in org_matches[:max_items]:
            lines.append(f"[HARD] {match.matched_text} => {match.target_term}")
        lines.append("</factory_organization_terms>")

    if src.startswith("zh"):
        matches = get_engine(glossary).match_zh(src_text, limit=max_items)
        # Dynamic organization parsing and explicit glossary entries can describe
        # the same surface.  Keep the deterministic organization rule once and
        # remove duplicate prompt rows to reduce tokens and model distraction.
        org_keys = {
            (match.matched_text.casefold(), match.target_term.casefold())
            for match in org_matches
        }
        matches = [
            match for match in matches
            if (match.matched_text.casefold(), match.target_term.casefold()) not in org_keys
        ]
        if matches:
            lines.append("<factory_terminology>")
            lines.append(
                "Only source-grounded plant terms are listed. HARD mappings must appear with the same meaning; "
                "SOFT mappings are semantic guidance and may inflect naturally in Indonesian."
            )
            for match in matches:
                if match.mode == "hard":
                    lines.append(f"[HARD] {match.matched_text} => {match.target_term}")
                else:
                    note = re.sub(r"\s+", " ", match.note)[:120]
                    suffix = f" | context: {note}" if note else ""
                    lines.append(f"[SOFT] {match.matched_text} ~= {match.target_term}{suffix}")
            lines.append("</factory_terminology>")
    elif src.startswith("id") and tgt.startswith("zh") and safe_reverse_index:
        pairs = collect_applicable_pairs(
            src_text, glossary, src, tgt,
            safe_reverse_index=safe_reverse_index,
            limit=max_items,
        )
        glossary_pairs = [pair for pair in pairs if pair[0] and pair[1]]
        if glossary_pairs:
            lines.append("<factory_terminology>")
            lines.append("Use only the direction-safe Indonesian-to-Chinese plant terms grounded in this source.")
            for source_term, target_term in glossary_pairs:
                lines.append(f"[HARD] {source_term} => {target_term}")
            lines.append("</factory_terminology>")

    return "\n".join(lines)


def build_ocr_hint(glossary: Mapping[str, Any] | None, *, max_items: int = 100) -> str:
    return get_engine(glossary).ocr_hint(max_items=max_items)


def normalize_ocr_text(text: str) -> str:
    """Apply only lossless/safe spacing normalization before terminology lookup.

    OCR engines often insert spaces between a Chinese numeral and an organization
    suffix (``一 課``) or between ``一 股 股 長``.  Removing those internal spaces is
    deterministic and improves both language detection and terminology matching.
    """
    value = _normalize_text(text).replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(
        r"(?P<num>[零〇一二兩三四五六七八九十0-9])\s+(?P<unit>[課股處部組班線站])",
        r"\g<num>\g<unit>",
        value,
    )
    value = re.sub(r"([課股處部組班線站])\s+(長)", r"\1\2", value)
    value = re.sub(r"([一二兩三四五六七八九十0-9]股)\s*(股長)", r"\1\2", value)
    return value
