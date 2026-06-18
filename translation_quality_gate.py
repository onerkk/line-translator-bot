"""translation_quality_gate.py

Provider-neutral translation integrity and synchronous quality gate.

Design goals:
- protect data-like literals before *any* normalization or model call;
- validate structure and source-grounded tokens without phrase-specific patches;
- use only unambiguous, direction-safe glossary constraints;
- run a second-pass semantic review for quality-critical messages before they are
  cached or sent to LINE.

The module deliberately contains no factory phrase -> translation replacements.
Terminology comes from the managed glossary and target-language annotations that
already exist in the source text.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Quotation marks accepted around field values/codes.
_QUOTES = '"“”„‟＂「」『』‘’\'`'

# Data, not natural language.  We intentionally do not protect long all-caps
# words (e.g. PENGUMUMAN), only compact codes/values and tokens containing a
# digit or technical separator.
_QUOTED_DATA_RE = re.compile(
    r'(?P<open>[' + re.escape(_QUOTES) + r'])\s*'
    r'(?P<value>(?:[-–—]|[A-Z0-9][A-Z0-9._/+:%×x-]{0,31}))\s*'
    r'(?P<close>[' + re.escape(_QUOTES) + r'])'
)
_TECH_TOKEN_RE = re.compile(
    r'(?<![\w])('
    r'(?:[A-Z]{1,4}\d[A-Z0-9._/+:%×x-]{0,24})|'        # BF2, R42, 316L-like with leading letters
    r'(?:\d+[A-Z][A-Z0-9._/+:%×x-]{0,24})|'            # 316L, 7J38029
    r'(?:[A-Z]{1,4}(?:[/._+-][A-Z0-9]{1,8})+)|'         # CYA/CYB, EH36-473
    r'(?:\d+(?:\.\d+)?\s*(?:mm|cm|kg|g|t|%|°C|℃))|'  # measurements
    r'(?:[A-Z]{1,4})'                                    # compact field values/acronyms: Y, QC, ERP
    r')(?![\w])'
)
_PLACEHOLDER_RE = re.compile(r'⟦IMM\d+_[0-9A-F]{8}⟧')
_MENTION_RE = re.compile(r'@[A-Za-z0-9_.-]+|@[^\s,，。!?！？:：;；]{1,48}')
_PIPELINE_TOKEN_RE = re.compile(r'(?:⟦PN\d+⟧|__MENTION_\d+__|__CUST_\d+__)')
_LATIN_RUN_RE = re.compile(r'(?:\b[A-Za-z]{2,}\b(?:[\s,;:/()\-]+|$)){4,}', re.I)

# List/meaning-bearing markers that should not silently disappear.
_MARKERS = ("✅", "❌", "⚠️", "📢", "•", "▪", "▫", "→")

# Target-script checks.
_HAN_RE = re.compile(r'[\u3400-\u9fff]')
_LATIN_WORD_RE = re.compile(r'(?<![A-Za-z])([A-Za-z]{1,32})(?![A-Za-z])')
_INLINE_LATIN_IN_HAN_RE = re.compile(r'(?<=[\u3400-\u9fff])([A-Za-z]{1,12})(?=[\u3400-\u9fff])')


@dataclass(frozen=True)
class ProtectedText:
    original: str
    protected: str
    mapping: Dict[str, str] = field(default_factory=dict)


@dataclass
class ValidationResult:
    ok: bool
    issues: List[str]


def _placeholder(index: int, literal: str) -> str:
    digest = hashlib.sha1(literal.encode("utf-8")).hexdigest()[:8].upper()
    return f"⟦IMM{index}_{digest}⟧"


def _replace_matches(text: str, regex: re.Pattern, mapping: Dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        literal = match.group(0)
        # Do not wrap our own or other pipeline placeholders.
        if "IMM" in literal or "MENTION" in literal or "PN" in literal:
            return literal
        ph = _placeholder(len(mapping), literal)
        mapping[ph] = literal
        return ph
    return regex.sub(repl, text)


def protect_immutable_spans(text: str) -> ProtectedText:
    """Protect field values, machine codes, measurements and compact acronyms.

    This is intentionally performed before Indonesian slang normalization.  It
    prevents a field value such as quoted ``Y`` from being normalized as the
    chat abbreviation ``y -> ya``.
    """
    if not text or not isinstance(text, str):
        return ProtectedText(text or "", text or "", {})
    mapping: Dict[str, str] = {}
    protected = _replace_matches(text, _MENTION_RE, mapping)
    protected = _replace_matches(protected, _QUOTED_DATA_RE, mapping)
    protected = _replace_matches(protected, _TECH_TOKEN_RE, mapping)
    return ProtectedText(text, protected, mapping)


def _placeholder_variants(ph: str) -> Sequence[str]:
    core = ph.strip("⟦⟧")
    return (
        ph,
        f"【{core}】",
        f"[{core}]",
        f"({core})",
        core,
        core.replace("_", " "),
    )


def restore_immutable_spans(text: str, mapping: Mapping[str, str]) -> str:
    if not text or not mapping:
        return text
    result = text
    # Longest first avoids accidental partial replacement.
    for ph, literal in sorted(mapping.items(), key=lambda item: -len(item[0])):
        for variant in _placeholder_variants(ph):
            result = result.replace(variant, literal)
    return result


def protected_placeholders_present(text: str, mapping: Mapping[str, str]) -> Tuple[bool, List[str]]:
    missing = [ph for ph in mapping if ph not in (text or "")]
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
    """Deterministic, language-agnostic checks before a result may be sent.

    The validator checks invariants and obvious corruption.  It does not contain
    phrase-specific expected translations; semantic correctness is handled by
    the independent review pass for quality-critical messages.
    """
    issues: List[str] = []
    source = source or ""
    candidate = (candidate or "").strip()
    glossary_pairs = list(glossary_pairs or ())

    if not candidate:
        return ValidationResult(False, ["empty_translation"])

    # Internal immutable placeholders must never leak. Pipeline name/mention
    # placeholders are allowed only when they already exist in the source and
    # must survive exactly until the outer wrapper restores them.
    if _PLACEHOLDER_RE.search(candidate):
        issues.append("placeholder_leak")
    for token in _PIPELINE_TOKEN_RE.findall(source):
        if candidate.count(token) < source.count(token):
            issues.append(f"missing_pipeline_token:{token}")
    for token in _PIPELINE_TOKEN_RE.findall(candidate):
        if token not in source:
            issues.append(f"invented_pipeline_token:{token}")

    # Every protected literal must survive exactly.  Exact count avoids changing
    # Y to ya/是 and avoids dropping lot numbers or dimensions.
    for literal in immutable_literals or ():
        src_count = source.count(literal)
        if src_count and candidate.count(literal) < src_count:
            issues.append(f"missing_literal:{literal}")

    # Meaning-bearing markers should not disappear.
    for marker in _MARKERS:
        if source.count(marker) > candidate.count(marker):
            issues.append(f"missing_marker:{marker}")

    # Preserve announcement paragraph boundaries.  This is applied only to
    # quality-critical messages, not casual one-line chat.
    if require_paragraph_fidelity:
        src_p = len(_paragraphs(source))
        tgt_p = len(_paragraphs(candidate))
        if src_p >= 2 and tgt_p != src_p:
            issues.append(f"paragraph_count:{src_p}->{tgt_p}")

    # Generic mixed-script corruption detector.  A Latin fragment embedded
    # between Han characters must be grounded as a whole word in the source or
    # in an approved glossary target.  This catches artifacts such as 不能LE使用
    # without maintaining a blacklist of specific bad strings.
    if str(tgt_lang).lower().startswith("zh"):
        allowed = _glossary_allowed_latin(glossary_pairs)
        for fragment in _INLINE_LATIN_IN_HAN_RE.findall(candidate):
            if not _whole_word_in_source(fragment, source) and fragment.upper() not in allowed:
                issues.append(f"ungrounded_inline_latin:{fragment}")

        # Four or more consecutive Latin words in a Chinese target are almost
        # always untranslated source leakage. Two-word technical labels such as
        # Book Order remain allowed. This is structural, not a phrase blacklist.
        if str(src_lang).lower().startswith(("id", "en")):
            for run in _LATIN_RUN_RE.findall(candidate):
                words = re.findall(r"[A-Za-z]{2,}", run)
                if len(words) >= 4:
                    issues.append("source_language_leakage")
                    break

        # A Chinese target should contain a reasonable amount of Han text for a
        # long alphabetic source.  Conservative threshold catches catastrophic
        # omissions, not stylistic brevity.
        src_info = len(re.findall(r'[A-Za-z0-9\u3400-\u9fff]', source))
        han_count = len(_HAN_RE.findall(candidate))
        if src_info >= 160 and han_count < max(28, int(src_info * 0.12)):
            issues.append("catastrophic_omission")

    return ValidationResult(not issues, issues)


def is_quality_critical(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    message_type: Optional[str] = None,
    factory_domain: bool = False,
) -> bool:
    """Select messages that need synchronous semantic review.

    Selection is structural, not phrase-specific: long messages, formal
    announcements, multi-paragraph instructions and checklist-style content.
    """
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
    l = (lang or "").lower()
    if l.startswith("zh"):
        return "Traditional Chinese used in Taiwan"
    if l.startswith("id"):
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
        "You are an independent bilingual translation quality editor. Compare the source and the current "
        "translation sentence by sentence, then output one corrected final translation. This is a semantic "
        "review, not a rewrite. Preserve every instruction, condition, polarity, actor, object and sequence. "
        "Do not add information that is absent from the source. Preserve all field values, codes, numbers, "
        "symbols, @mentions, emoji and paragraph boundaries exactly. If the source contains a parenthetical "
        "term already written in the target language, treat it as a terminology annotation for the adjacent "
        "source phrase and use it consistently. Apply only the terminology pairs explicitly supplied below; "
        "do not infer a glossary mapping from an ambiguous common word. Remove accidental mixed-language "
        "fragments that are not grounded in the source. Output only the final translation, with no analysis, "
        "label, markdown fence or alternatives."
    )
    user = (
        f"SOURCE LANGUAGE: {src_lang}\n"
        f"TARGET LANGUAGE: {_target_name(tgt_lang)}\n\n"
        f"SOURCE:\n{source}\n\n"
        f"CURRENT TRANSLATION:\n{candidate}\n\n"
        f"DETECTED QUALITY ISSUES:\n{issue_text}\n\n"
        f"UNAMBIGUOUS TERMINOLOGY CONSTRAINTS:\n{terminology}\n\n"
        "Return only the corrected final translation."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


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
    """Provider-neutral second-pass review through ai_provider.chat_complete."""
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
    # Chinese output is shorter than Indonesian source, but allow generous room
    # for long announcements and reasoning-model hidden tokens.
    budget = max(1400, min(6000, len(source) * 4 + 800))
    try:
        resp = ai_client.chat_complete(
            model=model,
            messages=messages,
            max_tokens=budget,
            temperature=0.0,
            reasoning_effort="none",
            verbosity="low",
            timeout=90,
        )
        if not getattr(resp, "choices", None):
            return None
        text = (resp.choices[0].message.content or "").strip()
        # Strip common accidental labels without altering body content.
        for prefix in ("修正後譯文：", "修正後：", "翻譯：", "Translation:"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        return text or None
    except Exception as exc:
        logger.warning("[QualityGate] review failed: %s", exc)
        return None


def _extract_response_text(resp: Any) -> str:
    if not getattr(resp, "choices", None):
        return ""
    text = (resp.choices[0].message.content or "").strip()
    # Provider features may wrap the answer in an XML tag.
    m = re.fullmatch(r"\s*<translation[^>]*>(.*?)</translation>\s*", text, re.I | re.S)
    if m:
        text = m.group(1).strip()
    for prefix in ("翻譯：", "Translation:", "Terjemahan:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text


def translate_quality_critical_document(
    source: str,
    src_lang: str,
    tgt_lang: str,
    *,
    model: str,
    glossary_pairs: Optional[Sequence[Tuple[str, str]]] = None,
    ai_client: Any = None,
) -> Dict[str, Any]:
    """Clean whole-document translation path for formal/critical messages.

    This path intentionally bypasses legacy sentence shortcuts, reverse glossary
    guessing, paragraph fan-out and phrase-replacement post-processors. It uses
    one whole-document translation followed by an independent semantic review.
    """
    if ai_client is None:
        try:
            import ai_provider as ai_client  # type: ignore
        except Exception:
            return {"ok": False, "text": None, "issues": ["provider_unavailable"], "reviewed": False}

    glossary_pairs = list(glossary_pairs or ())
    envelope = protect_immutable_spans(source)
    terms = "\n".join(f"- {s} => {t}" for s, t in glossary_pairs[:50]) or "(none)"
    system = (
        "You are a professional whole-document translator for a factory work group. "
        "Translate the source into " + _target_name(tgt_lang) + ". Read the complete document before translating. "
        "Preserve every instruction, condition, negation, actor, object and sequence. "
        "Preserve paragraph boundaries, list markers, emoji, @mentions, field values, codes, numbers and symbols exactly. "
        "Tokens such as ⟦IMM0_XXXXXXXX⟧ and ⟦PN1⟧ are immutable placeholders and must be copied exactly. "
        "When the source includes a parenthetical expression already in the target language, use it as contextual terminology guidance for the adjacent wording, but do not invent a mapping for unrelated common words. "
        "Use only the explicitly supplied unambiguous glossary pairs. Do not apply a reversed glossary entry merely because a common source word matches. "
        "Do not summarize, explain, add headings, mix languages, or output alternatives. Output only the translation."
    )
    user = (
        f"SOURCE LANGUAGE: {src_lang}\nTARGET LANGUAGE: {_target_name(tgt_lang)}\n\n"
        f"UNAMBIGUOUS GLOSSARY PAIRS:\n{terms}\n\n"
        f"SOURCE DOCUMENT:\n{envelope.protected}"
    )
    budget = max(1600, min(8000, len(source) * 4 + 1200))
    try:
        resp = ai_client.chat_complete(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=budget,
            temperature=0.0,
            reasoning_effort="none",
            verbosity="low",
            timeout=90,
        )
        candidate = _extract_response_text(resp)
    except Exception as exc:
        logger.warning("[QualityGate] critical first pass failed: %s", exc)
        return {"ok": False, "text": None, "issues": ["first_pass_unavailable"], "reviewed": False}

    candidate = restore_immutable_spans(candidate, envelope.mapping)
    if not candidate:
        return {"ok": False, "text": None, "issues": ["empty_translation"], "reviewed": False}

    return gate_and_revise(
        source, candidate, src_lang, tgt_lang,
        critical=True,
        model=model,
        immutable_literals=envelope.mapping.values(),
        glossary_pairs=glossary_pairs,
        ai_client=ai_client,
    )


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
    """Validate and, for critical messages, independently review before send.

    Returns {ok, text, issues, reviewed}.  A failed review never overwrites a
    structurally valid candidate; a structurally invalid result is blocked.
    """
    glossary_pairs = list(glossary_pairs or ())
    initial = validate_translation(
        source, candidate, src_lang, tgt_lang,
        immutable_literals=immutable_literals,
        glossary_pairs=glossary_pairs,
        require_paragraph_fidelity=critical,
    )

    # Casual/short messages stay single-pass for latency, but still must satisfy
    # deterministic integrity checks.
    if not critical:
        return {"ok": initial.ok, "text": candidate if initial.ok else None,
                "issues": initial.issues, "reviewed": False}

    reviewed = review_translation(
        source, candidate, src_lang, tgt_lang,
        model=model,
        issues=initial.issues,
        glossary_pairs=glossary_pairs,
        ai_client=ai_client,
    )
    if reviewed:
        checked = validate_translation(
            source, reviewed, src_lang, tgt_lang,
            immutable_literals=immutable_literals,
            glossary_pairs=glossary_pairs,
            require_paragraph_fidelity=True,
        )
        if checked.ok:
            return {"ok": True, "text": reviewed, "issues": [], "reviewed": True}

        # One focused retry, now with concrete deterministic failures.
        reviewed2 = review_translation(
            source, reviewed, src_lang, tgt_lang,
            model=model,
            issues=checked.issues,
            glossary_pairs=glossary_pairs,
            ai_client=ai_client,
        )
        if reviewed2:
            checked2 = validate_translation(
                source, reviewed2, src_lang, tgt_lang,
                immutable_literals=immutable_literals,
                glossary_pairs=glossary_pairs,
                require_paragraph_fidelity=True,
            )
            if checked2.ok:
                return {"ok": True, "text": reviewed2, "issues": [], "reviewed": True}
            return {"ok": False, "text": None, "issues": checked2.issues, "reviewed": True}

    # Quality-critical messages are fail-closed: without a successful semantic
    # review we do not publish or cache the candidate. This prevents a provider
    # timeout from silently downgrading the quality policy.
    issues = list(initial.issues)
    if not reviewed:
        issues.append("semantic_review_unavailable")
    return {"ok": False, "text": None, "issues": issues, "reviewed": bool(reviewed)}
