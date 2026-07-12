"""Provider-neutral translation quality pipeline.

Design goals
------------
1. Protect data values and locked terminology before the single model call.
2. Restore literals and canonical terms deterministically after generation.
3. Validate completeness, language purity and structure locally without another AI call.
4. Provider failover is reserved for operational failure, never for quality editing.
5. No sentence-specific translation replacements live in this module.

The module is intentionally provider-neutral and works with the project's
``ai_provider.chat_complete`` interface for OpenAI, Gemini and Claude.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import glossary_policy as gp_module

logger = logging.getLogger(__name__)

# Deployment contract: app.py verifies this exact build at startup.
QUALITY_GATE_API_VERSION = 7
QUALITY_GATE_BUILD_ID = "2026-07-12.8-indonesian-factory-register"

# ASCII placeholders survive all three providers more reliably than decorative
# Unicode brackets.  The hash prevents accidental collision with ordinary text.
_PLACEHOLDER_RE = re.compile(r"__QG_KEEP_(\d{3})_([0-9A-F]{8})__")
_UNKNOWN_PLACEHOLDER_RE = re.compile(r"QG[\s_-]*KEEP[\s_-]*\d{1,4}[\s_-]*[0-9A-F]{6,10}", re.I)
_PIPELINE_TOKEN_RE = re.compile(r'(?:__QG_KEEP_\d{3}_[0-9A-F]{8}__|⟦PN\d+⟧|__MENTION_\d+__|__CUST_\d+__)')

_QUOTES_OPEN = '"“”„‟＂「」『』‘’\'`'
_QUOTES_CLOSE = _QUOTES_OPEN
_QUOTES_ALL = _QUOTES_OPEN

# Quote-wrapped field values.  The *inner value* is protected, while the quote
# characters remain visible to the model so it can naturally use target-language
# typography without changing the data value itself.
_QUOTED_DATA_RE = re.compile(
    r'(?P<open>[' + re.escape(_QUOTES_OPEN) + r'])\s*'
    r'(?P<value>(?:[-–—]|[A-Z0-9][A-Z0-9._/+:%×x-]{0,31}))\s*'
    r'(?P<close>[' + re.escape(_QUOTES_CLOSE) + r'])'
)

_MENTION_RE = re.compile(r'@[A-Za-z0-9_.-]+|@[^\s,，。!?！？:：;；]{1,48}')
_TECH_TOKEN_RE = re.compile(
    r'(?<![\w])('
    r'(?:[A-Z]{1,4}\d[A-Z0-9._/+:%×x-]{0,24})|'
    r'(?:\d+[A-Z][A-Z0-9._/+:%×x-]{0,24})|'
    r'(?:[A-Z]{1,4}(?:[/._+-][A-Z0-9]{1,8})+)|'
    r'(?:\d+(?:\.\d+)?\s*(?:mm|cm|kg|g|t|%|°C|℃))|'
    r'(?:[A-Z]{1,4})'
    r')(?![\w])'
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


def _protect_quoted_values(text: str, mapping: Dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        value = match.group("value")
        # A technical token (e.g. Y) may already have been protected before this
        # pass.  In that case leave the quote-wrapped placeholder untouched.
        if _PLACEHOLDER_RE.fullmatch(value or ""):
            return match.group(0)
        return f'{match.group("open")}{_new_placeholder(mapping, value)}{match.group("close")}'
    return _QUOTED_DATA_RE.sub(repl, text)


def protect_immutable_spans(text: str) -> ProtectedText:
    """Protect mentions, field values, codes and measurements.

    Ordering is deliberate:
    - mentions are protected first;
    - technical tokens next (so quoted ``Y`` becomes a protected atom);
    - quote-wrapped punctuation values such as ``"-"`` last.

    Therefore Indonesian slang normalization can never convert a field value
    ``Y`` into ``ya``.
    """
    if not text or not isinstance(text, str):
        return ProtectedText(text or "", text or "", {})
    mapping: Dict[str, str] = {}
    protected = _replace_matches(text, _MENTION_RE, mapping)
    protected = _replace_matches(protected, _TECH_TOKEN_RE, mapping)
    protected = _protect_quoted_values(protected, mapping)
    return ProtectedText(text, protected, mapping)


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
    if t.isupper() and 1 <= len(t) <= 4:
        return True
    # Mixed-case product/company identifiers such as OpenAI, iPhone, eSIM.
    letters = [ch for ch in t if ch.isalpha()]
    if letters and any(ch.isupper() for ch in letters) and any(ch.islower() for ch in letters):
        if not (t[:1].isupper() and t[1:].islower()):
            return True
    return False


def _source_common_words(src_lang: str) -> set[str]:
    low = (src_lang or "").lower()
    if low.startswith("id"):
        return _COMMON_ID_WORDS
    if low.startswith("en"):
        return _COMMON_EN_WORDS
    return set()


def _probable_source_proper_name(token: str, source: str, src_lang: str) -> bool:
    """Conservatively allow real names/brands while rejecting sentence words."""
    t = (token or "").strip()
    if not t or t.casefold() in _source_common_words(src_lang):
        return False
    if not _whole_word_in_source(t, source):
        return False
    # Exact mixed case is a strong brand signal (OpenAI/iPhone/eSIM).
    if _looks_like_technical_identifier(t):
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
    warning_prefixes = ("paragraph_count:", "style:")
    hard: List[str] = []
    warnings: List[str] = []
    for issue in _dedupe(issues):
        (warnings if issue.startswith(warning_prefixes) else hard).append(issue)
    return hard, warnings


def _normalize_data_atom(value: str) -> str:
    v = (value or "").strip()
    if v and all(ch in _DASHES for ch in v):
        return "-"
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
    if re.fullmatch(r'[A-Za-z0-9._/+:%×x-]+', atom):
        return len(re.findall(r'(?<![A-Za-z0-9])' + re.escape(atom) + r'(?![A-Za-z0-9])', text or ""))
    return (text or "").count(atom)



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
        "unless the source explicitly alleges lying. Prefer 'data produksi yang tidak sesuai dengan kondisi sebenarnya' "
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
            issues.extend(_indonesian_readability_issues(source, candidate, src))

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
    quoted_data_count = len(_QUOTED_DATA_RE.findall(text))
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
    system = (
        "You are an independent bilingual translation quality editor for factory communications. Do not merely "
        "polish the current wording. Reconstruct the meaning from the source, silently back-translate each target "
        "sentence, compare it with the corresponding source meaning, and then write one fresh corrected final "
        "translation. Audit actor, action, object, timing, condition, negation, modality, severity, cause and "
        "consequence. Preserve every instruction, condition, negation, actor, object and sequence. Do not add "
        "information. Tokens "
        "matching __QG_KEEP_000_XXXXXXXX__ are immutable identifiers and must be copied exactly. Preserve "
        "numbers, symbols, @mentions, emoji and list markers. Preserve the document's logical sections; "
        "minor paragraph reflow is allowed only when meaning is unchanged. Apply only explicitly supplied "
        "terminology pairs. No ordinary source-language word may remain untranslated merely because it appears "
        "in the source. Retain Latin text only when it is an immutable identifier, a real proper name, a product/model "
        "code, or an explicit target-side glossary term." + annotation_rule + _indonesian_clarity_instruction(tgt_lang) + " Output "
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
    system = (
        "You are a professional whole-document translator for a factory work group. Translate the complete "
        "source into " + _target_name(tgt_lang) + ". Read the whole document before writing and internally "
        "verify the result before output. Preserve every instruction, condition, negation, actor, object and "
        "sequence. Preserve list markers, emoji and section order. Tokens matching "
        "__QG_KEEP_000_XXXXXXXX__ are immutable identifiers: copy each token exactly once wherever it appears. "
        "Do not translate, rename, split or decorate these tokens. Use only explicit unambiguous glossary pairs; "
        "never infer a reversed mapping from a common word. No ordinary source-language word may remain untranslated; "
        "retain Latin text only for immutable identifiers, real proper names, product/model codes, or explicit target-side "
        "glossary terms." + annotation_rule + _indonesian_clarity_instruction(tgt_lang) + " Do not summarize, explain, add headings, mix languages or output "
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

    This does not add an extra call: critical documents already receive a review.
    It only prevents the same model family from approving its own semantic mistake
    when another configured provider is available.
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
) -> Optional[str]:
    """Compatibility shim for the former billable reviewer.

    Commercial single-request mode intentionally performs no model call here.
    It returns the original candidate only when deterministic validation passes;
    otherwise it returns ``None`` so the caller can block/log the result.
    """
    if not source or not candidate:
        return None
    report = validate_translation(
        source, candidate, src_lang, tgt_lang,
        glossary_pairs=_merge_runtime_glossary_pairs(
            source, src_lang, tgt_lang, list(glossary_pairs or ())
        ),
        require_paragraph_fidelity=is_quality_critical(source, src_lang, tgt_lang),
    )
    return candidate if report.ok else None

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
) -> Dict[str, Any]:
    """Deterministic delivery gate; never calls an AI reviewer.

    A successful provider response is either accepted by local structural checks
    or blocked.  Translation quality must be solved in the first-pass prompt,
    locked terminology and data protection, not by billable post-edit requests.
    """
    glossary_pairs = _merge_runtime_glossary_pairs(
        source, src_lang, tgt_lang, list(glossary_pairs or ())
    )
    report = validate_translation(
        source, candidate, src_lang, tgt_lang,
        immutable_literals=list(immutable_literals or ()),
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=critical,
    )
    return {
        "ok": report.ok,
        "text": candidate if report.ok else None,
        "issues": report.issues,
        "hard_issues": report.hard_issues,
        "warnings": report.warnings,
        "reviewed": False,
        "degraded": False,
        "cacheable": report.ok,
        "path": "single_api_local_validation" if report.ok else "single_api_blocked",
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
    final_report = validate_translation(
        source, restored, src_lang, tgt_lang,
        immutable_literals=envelope.mapping.values(),
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=require_paragraph_fidelity,
    )
    return (restored if final_report.ok else None), final_report


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
    """One-pass whole-document translation with deterministic validation.

    ``fallback_translate`` is accepted only for API compatibility and is not used.
    Operational provider failover remains inside ``ai_provider.chat_complete``;
    semantic review, retranslation and LLM post-editing are intentionally absent.
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
    immutable = protect_immutable_spans(source)
    terms = protect_glossary_terms(immutable.protected, glossary_pairs)
    protected_source = terms.protected
    messages = _build_translation_messages(
        protected_source, src_lang, tgt_lang, glossary_pairs
    )
    term_note = glossary_placeholder_instruction(terms.mapping)
    if term_note:
        messages[0] = dict(messages[0])
        messages[0]["content"] = messages[0]["content"] + "\n" + term_note

    try:
        budget = max(1600, min(8000, len(protected_source) * 4 + 1200))
        response = _call_chat_complete(
            ai_client,
            model=model,
            messages=messages,
            max_tokens=budget,
        )
        raw = _extract_response_text(response)
        provider = _response_provider(response)
        raw = restore_glossary_terms(raw, terms.mapping)
        raw = canonicalize_placeholders(raw, immutable.mapping)
        text, report = _finalize_protected_candidate(
            source, immutable.protected, raw, immutable,
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
        return {
            "ok": False, "text": None, "issues": report.issues,
            "hard_issues": report.hard_issues, "warnings": report.warnings,
            "reviewed": False, "degraded": True, "cacheable": False,
            "path": "single_api_blocked", "provider_path": provider or "primary",
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
    """Final local-only validation boundary.

    No retranslation, reviewer or fallback API is invoked.  Invalid output is
    blocked and logged so the first-pass contract can be improved at the source.
    """
    source = source or ""
    candidate = (candidate or "").strip()
    glossary_pairs = _merge_runtime_glossary_pairs(
        source, src_lang, tgt_lang, list(glossary_pairs or ())
    )
    envelope = protect_immutable_spans(source)
    report = validate_translation(
        source, candidate, src_lang, tgt_lang,
        immutable_literals=envelope.mapping.values(),
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=is_quality_critical(source, src_lang, tgt_lang),
    )
    return {
        "ok": report.ok,
        "text": candidate if report.ok else None,
        "issues": report.issues,
        "hard_issues": report.hard_issues,
        "warnings": report.warnings,
        "reviewed": False,
        "degraded": False,
        "cacheable": report.ok,
        "path": "final_local_validation" if report.ok else "final_local_blocked",
    }

def translation_failure_message(tgt_lang: str) -> str:
    low = (tgt_lang or "").lower()
    if low.startswith("id"):
        return "⚠️ Terjemahan gagal karena semua layanan penerjemahan sedang tidak tersedia. Silakan kirim ulang."
    if low.startswith("zh"):
        return "⚠️ 目前所有翻譯服務皆無法取得結果，請稍後重新傳送。"
    return "⚠️ All translation services are temporarily unavailable. Please resend later."
