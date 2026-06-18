"""Provider-neutral translation quality pipeline.

Design goals
------------
1. Protect data values before normalization or model calls.
2. Keep protected placeholders through translation *and* semantic review.
3. Restore literals only once, after a candidate has passed structural checks.
4. Validate semantic data values independently from quote glyphs/typography.
5. Never discard a structurally valid first-pass translation only because the
   optional reviewer is unavailable.
6. No sentence-specific translation replacements live in this module.

The module is intentionally provider-neutral and works with the project's
``ai_provider.chat_complete`` interface for OpenAI, Gemini and Claude.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

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
_INLINE_LATIN_IN_HAN_RE = re.compile(r'(?<=[\u3400-\u9fff])([A-Za-z]{1,12})(?=[\u3400-\u9fff])')
_DASHES = "-–—−"


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


def _dedupe(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _partition_issues(issues: Sequence[str]) -> Tuple[List[str], List[str]]:
    warning_prefixes = ("paragraph_count:",)
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
    glossary_pairs = list(glossary_pairs or ())

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

    for literal in immutable_literals or ():
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
        allowed = _glossary_allowed_latin(glossary_pairs)
        for fragment in _INLINE_LATIN_IN_HAN_RE.findall(candidate):
            if not _whole_word_in_source(fragment, source) and fragment.upper() not in allowed:
                issues.append(f"ungrounded_inline_latin:{fragment}")

        if src.startswith(("id", "en")):
            for run in _LATIN_RUN_RE.findall(candidate):
                words = re.findall(r"[A-Za-z]{2,}", run)
                if len(words) >= 4:
                    issues.append("source_language_leakage")
                    break

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
    system = (
        "You are an independent bilingual translation quality editor. Compare the source and current "
        "translation sentence by sentence and return one corrected final translation. Preserve every "
        "instruction, condition, negation, actor, object and sequence. Do not add information. Tokens "
        "matching __QG_KEEP_000_XXXXXXXX__ are immutable identifiers and must be copied exactly. Preserve "
        "numbers, symbols, @mentions, emoji and list markers. Preserve the document's logical sections; "
        "minor paragraph reflow is allowed only when meaning is unchanged. Apply only explicitly supplied "
        "terminology pairs. Remove accidental mixed-language fragments not grounded in the source. Output "
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
        "never infer a reversed mapping from a common word. Do not summarize, explain, add headings, mix "
        "languages or output alternatives. Output only the complete translation."
    )
    user = (
        f"SOURCE LANGUAGE: {src_lang}\nTARGET LANGUAGE: {_target_name(tgt_lang)}\n\n"
        f"UNAMBIGUOUS GLOSSARY PAIRS:\n{terms}\n"
        f"{retry_note}\nSOURCE DOCUMENT:\n{protected_source}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _call_chat_complete(
    ai_client: Any,
    *,
    model: str,
    messages: Sequence[Mapping[str, str]],
    max_tokens: int,
    timeout: int = 90,
) -> Any:
    attempts = (
        dict(model=model, messages=list(messages), max_tokens=max_tokens, temperature=0.0,
             reasoning_effort="none", verbosity="low", timeout=timeout),
        dict(model=model, messages=list(messages), max_tokens=max_tokens, temperature=0.0,
             timeout=timeout),
        dict(model=model, messages=list(messages), max_tokens=max_tokens, timeout=timeout),
    )
    last_exc: Optional[Exception] = None
    for idx, kwargs in enumerate(attempts):
        try:
            return ai_client.chat_complete(**kwargs)
        except Exception as exc:
            last_exc = exc
            logger.warning("[QualityGate] provider call attempt %d failed: %s", idx + 1, exc)
    if last_exc:
        raise last_exc
    raise RuntimeError("provider_call_failed")


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
    model: str,
    issues: Optional[Sequence[str]] = None,
    glossary_pairs: Optional[Sequence[Tuple[str, str]]] = None,
    ai_client: Any = None,
) -> Optional[str]:
    if not source or not candidate:
        return None
    if ai_client is None:
        try:
            import ai_provider as ai_client  # type: ignore
        except Exception:
            return None
    messages = _build_review_messages(
        source, candidate, src_lang, tgt_lang, list(issues or ()), list(glossary_pairs or ())
    )
    budget = max(1400, min(6000, len(source) * 4 + 800))
    try:
        resp = _call_chat_complete(ai_client, model=model, messages=messages, max_tokens=budget)
        return _extract_response_text(resp) or None
    except Exception as exc:
        logger.warning("[QualityGate] semantic review unavailable: %s", exc)
        return None


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
    """Validate an already-restored candidate and optionally review it.

    This compatibility entry point is used by the legacy translation path.  The
    dedicated whole-document path below keeps placeholders protected throughout.
    """
    glossary_pairs = list(glossary_pairs or ())
    immutable_literals = list(immutable_literals or ())
    initial = validate_translation(
        source, candidate, src_lang, tgt_lang,
        immutable_literals=immutable_literals,
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=critical,
    )
    if not critical:
        return {
            "ok": initial.ok, "text": candidate if initial.ok else None,
            "issues": initial.issues, "hard_issues": initial.hard_issues,
            "warnings": initial.warnings, "reviewed": False,
            "degraded": False, "cacheable": initial.ok, "path": "single_pass",
        }

    reviewed = review_translation(
        source, candidate, src_lang, tgt_lang,
        model=model, issues=initial.issues, glossary_pairs=glossary_pairs, ai_client=ai_client,
    )
    if reviewed:
        checked = validate_translation(
            source, reviewed, src_lang, tgt_lang,
            immutable_literals=immutable_literals,
            glossary_pairs=glossary_pairs,
            require_paragraph_fidelity=True,
        )
        if checked.ok:
            return {
                "ok": True, "text": reviewed, "issues": checked.issues,
                "hard_issues": [], "warnings": checked.warnings, "reviewed": True,
                "degraded": False, "cacheable": True, "path": "reviewed",
            }

    if initial.ok:
        return {
            "ok": True, "text": candidate,
            "issues": _dedupe(initial.issues + ["semantic_review_unavailable_or_rejected"]),
            "hard_issues": [],
            "warnings": _dedupe(initial.warnings + ["semantic_review_unavailable_or_rejected"]),
            "reviewed": False, "degraded": True, "cacheable": True,
            "path": "validated_first_pass",
        }
    return {
        "ok": False, "text": None,
        "issues": _dedupe(initial.issues + ["no_valid_review_candidate"]),
        "hard_issues": initial.hard_issues, "warnings": initial.warnings,
        "reviewed": bool(reviewed), "degraded": True, "cacheable": False,
        "path": "blocked",
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
) -> str:
    messages = _build_translation_messages(
        protected_source, src_lang, tgt_lang, glossary_pairs, retry_issues=retry_issues
    )
    budget = max(1600, min(8000, len(protected_source) * 4 + 1200))
    resp = _call_chat_complete(ai_client, model=model, messages=messages, max_tokens=budget)
    return _extract_response_text(resp)


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
    """Translate a complete critical document with protected-data continuity.

    The same placeholders remain in place through first-pass translation and
    optional semantic review.  They are restored only once at the end.  This
    prevents quote-style changes, reviewer edits or provider formatting from
    turning a valid field value into a false integrity failure.
    """
    if ai_client is None:
        try:
            import ai_provider as ai_client  # type: ignore
        except Exception:
            ai_client = None

    glossary_pairs = list(glossary_pairs or ())
    envelope = protect_immutable_spans(source)
    protected_source = envelope.protected
    all_issues: List[str] = []

    if ai_client is not None:
        # First high-quality whole-document translation.
        try:
            raw = _translate_candidate(
                protected_source, src_lang, tgt_lang,
                model=model, glossary_pairs=glossary_pairs, ai_client=ai_client,
            )
            raw = canonicalize_placeholders(raw, envelope.mapping)
            first_text, first_report = _finalize_protected_candidate(
                source, protected_source, raw, envelope, src_lang, tgt_lang,
                glossary_pairs, require_paragraph_fidelity=True,
            )
            all_issues.extend(first_report.issues)

            if first_text:
                # Review stays protected.  A reviewer outage or bad edit cannot
                # invalidate the already valid first pass.
                reviewed_raw = review_translation(
                    protected_source, raw, src_lang, tgt_lang,
                    model=model, issues=first_report.issues,
                    glossary_pairs=glossary_pairs, ai_client=ai_client,
                )
                if reviewed_raw:
                    reviewed_raw = canonicalize_placeholders(reviewed_raw, envelope.mapping)
                    reviewed_text, reviewed_report = _finalize_protected_candidate(
                        source, protected_source, reviewed_raw, envelope, src_lang, tgt_lang,
                        glossary_pairs, require_paragraph_fidelity=True,
                    )
                    if reviewed_text:
                        return {
                            "ok": True, "text": reviewed_text,
                            "issues": reviewed_report.issues,
                            "hard_issues": [], "warnings": reviewed_report.warnings,
                            "reviewed": True, "degraded": False, "cacheable": True,
                            "path": "protected_reviewed", "provider_path": "primary",
                        }
                    all_issues.extend(reviewed_report.issues)

                return {
                    "ok": True, "text": first_text,
                    "issues": _dedupe(first_report.issues + ["semantic_review_unavailable_or_rejected"]),
                    "hard_issues": [],
                    "warnings": _dedupe(first_report.warnings + ["semantic_review_unavailable_or_rejected"]),
                    "reviewed": False, "degraded": True, "cacheable": True,
                    "path": "protected_first_pass", "provider_path": "primary",
                }
        except Exception as exc:
            logger.warning("[QualityGate] critical first pass unavailable: %s", exc)
            all_issues.append("first_pass_unavailable")

        # One fresh source-grounded retry.  It receives only integrity codes, not
        # the failed wording, so this is not a string patch.
        try:
            raw_retry = _translate_candidate(
                protected_source, src_lang, tgt_lang,
                model=model, glossary_pairs=glossary_pairs, ai_client=ai_client,
                retry_issues=all_issues,
            )
            raw_retry = canonicalize_placeholders(raw_retry, envelope.mapping)
            retry_text, retry_report = _finalize_protected_candidate(
                source, protected_source, raw_retry, envelope, src_lang, tgt_lang,
                glossary_pairs, require_paragraph_fidelity=True,
            )
            if retry_text:
                return {
                    "ok": True, "text": retry_text, "issues": retry_report.issues,
                    "hard_issues": [], "warnings": _dedupe(retry_report.warnings + ["used_fresh_retry"]),
                    "reviewed": False, "degraded": True, "cacheable": True,
                    "path": "protected_fresh_retry", "provider_path": "fresh_retry",
                }
            all_issues.extend(retry_report.issues)
        except Exception as exc:
            logger.warning("[QualityGate] fresh critical retry unavailable: %s", exc)
            all_issues.append("fresh_retry_unavailable")

    # Optional non-LLM fallback.  It receives the protected document and must
    # preserve the same placeholder identities before it can be accepted.
    if fallback_translate is not None:
        try:
            fallback_raw = fallback_translate(protected_source, src_lang, tgt_lang) or ""
            fallback_raw = canonicalize_placeholders(fallback_raw, envelope.mapping)
            fallback_text, fallback_report = _finalize_protected_candidate(
                source, protected_source, fallback_raw, envelope, src_lang, tgt_lang,
                glossary_pairs, require_paragraph_fidelity=True,
            )
            if fallback_text:
                return {
                    "ok": True, "text": fallback_text, "issues": fallback_report.issues,
                    "hard_issues": [],
                    "warnings": _dedupe(fallback_report.warnings + ["used_validated_fallback"]),
                    "reviewed": False, "degraded": True, "cacheable": False,
                    "path": "protected_validated_fallback", "provider_path": "fallback",
                }
            all_issues.extend(fallback_report.issues)
        except Exception as exc:
            logger.warning("[QualityGate] fallback translator unavailable: %s", exc)
            all_issues.append("fallback_unavailable")

    return {
        "ok": False, "text": None,
        "issues": _dedupe(all_issues or ["no_translation_candidate"]),
        "hard_issues": _dedupe(all_issues or ["no_translation_candidate"]),
        "warnings": [], "reviewed": False, "degraded": True,
        "cacheable": False, "path": "all_candidates_failed", "provider_path": "none",
    }


def translation_failure_message(tgt_lang: str) -> str:
    low = (tgt_lang or "").lower()
    if low.startswith("id"):
        return "⚠️ Terjemahan gagal karena semua layanan penerjemahan sedang tidak tersedia. Silakan kirim ulang."
    if low.startswith("zh"):
        return "⚠️ 目前所有翻譯服務皆無法取得結果，請稍後重新傳送。"
    return "⚠️ All translation services are temporarily unavailable. Please resend later."
