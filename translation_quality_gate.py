"""translation_quality_gate.py

Provider-neutral, availability-safe translation quality pipeline.

Core properties:
- immutable data is protected before any normalization/model call;
- validation separates hard corruption from advisory formatting warnings;
- critical documents are translated as a whole and independently reviewed;
- reviewer/model outages never discard an already valid translation;
- if the first candidate is invalid, a fresh source-grounded retry is attempted;
- an optional non-LLM fallback may be used only after deterministic validation;
- no phrase-specific translation replacements are embedded in this module.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_QUOTES = '"“”„‟＂「」『』‘’\'`'
_QUOTED_DATA_RE = re.compile(
    r'(?P<open>[' + re.escape(_QUOTES) + r'])\s*'
    r'(?P<value>(?:[-–—]|[A-Z0-9][A-Z0-9._/+:%×x-]{0,31}))\s*'
    r'(?P<close>[' + re.escape(_QUOTES) + r'])'
)
_TECH_TOKEN_RE = re.compile(
    r'(?<![\w])('
    r'(?:[A-Z]{1,4}\d[A-Z0-9._/+:%×x-]{0,24})|'
    r'(?:\d+[A-Z][A-Z0-9._/+:%×x-]{0,24})|'
    r'(?:[A-Z]{1,4}(?:[/._+-][A-Z0-9]{1,8})+)|'
    r'(?:\d+(?:\.\d+)?\s*(?:mm|cm|kg|g|t|%|°C|℃))|'
    r'(?:[A-Z]{1,4})'
    r')(?![\w])'
)
_PLACEHOLDER_RE = re.compile(r'⟦IMM\d+_[0-9A-F]{8}⟧')
_MENTION_RE = re.compile(r'@[A-Za-z0-9_.-]+|@[^\s,，。!?！？:：;；]{1,48}')
_PIPELINE_TOKEN_RE = re.compile(r'(?:⟦PN\d+⟧|__MENTION_\d+__|__CUST_\d+__)')
_LATIN_RUN_RE = re.compile(r'(?:\b[A-Za-z]{2,}\b(?:[\s,;:/()\-]+|$)){4,}', re.I)
_MARKERS = ("✅", "❌", "⚠️", "📢", "•", "▪", "▫", "→")
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
    # ``ok`` means no hard integrity failure. Warnings may still exist and are
    # suitable for reviewer guidance, but they must not cause silent message loss.
    ok: bool
    issues: List[str]
    hard_issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _placeholder(index: int, literal: str) -> str:
    digest = hashlib.sha1(literal.encode("utf-8")).hexdigest()[:8].upper()
    return f"⟦IMM{index}_{digest}⟧"


def _replace_matches(text: str, regex: re.Pattern, mapping: Dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        literal = match.group(0)
        if "IMM" in literal or "MENTION" in literal or "PN" in literal:
            return literal
        ph = _placeholder(len(mapping), literal)
        mapping[ph] = literal
        return ph
    return regex.sub(repl, text)


def protect_immutable_spans(text: str) -> ProtectedText:
    """Protect mentions, quoted field values, codes and measurements.

    This must run before Indonesian abbreviation normalization so a field value
    such as quoted ``Y`` can never become ``ya``.
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
    return (ph, f"【{core}】", f"[{core}]", f"({core})", core, core.replace("_", " "))


def restore_immutable_spans(text: str, mapping: Mapping[str, str]) -> str:
    if not text or not mapping:
        return text
    result = text
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


def _dedupe(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _partition_issues(issues: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Separate integrity failures from advisory layout/style warnings.

    Exact paragraph-count equality is intentionally advisory. Models may merge a
    heading with its following paragraph without losing meaning; treating that as
    fatal caused valid translations to disappear from LINE.
    """
    warning_prefixes = ("paragraph_count:",)
    hard: List[str] = []
    warnings: List[str] = []
    for issue in _dedupe(issues):
        if issue.startswith(warning_prefixes):
            warnings.append(issue)
        else:
            hard.append(issue)
    return hard, warnings


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
    """Deterministic integrity checks.

    The validator does not prescribe sentence-level wording. It only blocks
    empty/untranslated/corrupted output, lost immutable data, missing semantic
    markers and catastrophic omissions. Formatting differences are warnings.
    """
    issues: List[str] = []
    source = source or ""
    candidate = (candidate or "").strip()
    glossary_pairs = list(glossary_pairs or ())

    if not candidate:
        return ValidationResult(False, ["empty_translation"], ["empty_translation"], [])

    if _PLACEHOLDER_RE.search(candidate):
        issues.append("placeholder_leak")
    for token in _PIPELINE_TOKEN_RE.findall(source):
        if candidate.count(token) < source.count(token):
            issues.append(f"missing_pipeline_token:{token}")
    for token in _PIPELINE_TOKEN_RE.findall(candidate):
        if token not in source:
            issues.append(f"invented_pipeline_token:{token}")

    for literal in immutable_literals or ():
        src_count = source.count(literal)
        if src_count and candidate.count(literal) < src_count:
            issues.append(f"missing_literal:{literal}")

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
        "instruction, condition, negation, actor, object and sequence. Do not add information. Preserve "
        "field values, codes, numbers, symbols, @mentions, emoji and list markers exactly. Preserve the "
        "document's logical sections; minor paragraph reflow is allowed only when meaning is unchanged. "
        "A parenthetical term already written in the target language is contextual terminology for the "
        "adjacent source phrase. Apply only explicitly supplied terminology pairs. Remove accidental "
        "mixed-language fragments not grounded in the source. Output only the final translation."
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
        "source into " + _target_name(tgt_lang) + ". Read the whole document before writing. Preserve every "
        "instruction, condition, negation, actor, object and sequence. Preserve list markers, emoji, @mentions, "
        "field values, codes, numbers and symbols exactly. Tokens such as ⟦IMM0_XXXXXXXX⟧, ⟦PN1⟧ and "
        "__MENTION_0__ are immutable and must be copied exactly. Preserve the logical section order and spacing "
        "well enough for a LINE announcement; do not summarize. A parenthetical expression already in the target "
        "language is terminology guidance for the adjacent phrase. Use only explicit unambiguous glossary pairs; "
        "never infer a reversed mapping from a common word. Do not explain, add headings, mix languages or output "
        "alternatives. Output only the translation."
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
    """Call the unified provider with parameter-compatible degradation.

    The unified provider normally accepts all parameters, but a provider SDK or
    proxy may reject optional controls. Retrying without optional controls fixes
    availability without changing the translation prompt or model policy.
    """
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
    """Validate, review and choose the safest available candidate.

    A valid first-pass translation is never discarded merely because the
    independent reviewer timed out. This prevents the quality system itself from
    turning a translation into silence.
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
            "ok": initial.ok,
            "text": candidate if initial.ok else None,
            "issues": initial.issues,
            "hard_issues": initial.hard_issues,
            "warnings": initial.warnings,
            "reviewed": False,
            "degraded": False,
            "cacheable": initial.ok,
            "path": "single_pass",
        }

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
            return {
                "ok": True, "text": reviewed, "issues": checked.issues,
                "hard_issues": [], "warnings": checked.warnings,
                "reviewed": True, "degraded": False, "cacheable": True,
                "path": "reviewed",
            }

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
                return {
                    "ok": True, "text": reviewed2, "issues": checked2.issues,
                    "hard_issues": [], "warnings": checked2.warnings,
                    "reviewed": True, "degraded": False, "cacheable": True,
                    "path": "reviewed_retry",
                }

    # Availability-safe choice: if the original candidate passed all hard
    # invariants, it is safer to deliver it than to discard it because the
    # reviewer was unavailable or introduced new corruption.
    if initial.ok:
        fallback_warnings = list(initial.warnings)
        fallback_warnings.append("semantic_review_unavailable_or_rejected")
        return {
            "ok": True,
            "text": candidate,
            "issues": _dedupe(initial.issues + ["semantic_review_unavailable_or_rejected"]),
            "hard_issues": [],
            "warnings": _dedupe(fallback_warnings),
            "reviewed": False,
            "degraded": True,
            "cacheable": True,
            "path": "validated_first_pass",
        }

    return {
        "ok": False,
        "text": None,
        "issues": _dedupe(initial.issues + ["no_valid_review_candidate"]),
        "hard_issues": initial.hard_issues,
        "warnings": initial.warnings,
        "reviewed": bool(reviewed),
        "degraded": True,
        "cacheable": False,
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
    """Whole-document translation with review, retry and graceful degradation.

    Candidate order:
      1. high-quality whole-document translation;
      2. independent semantic review;
      3. fresh source-grounded retry if hard validation failed;
      4. optional validated fallback translator.

    At no point is a valid candidate discarded solely due to reviewer outage.
    """
    if ai_client is None:
        try:
            import ai_provider as ai_client  # type: ignore
        except Exception:
            ai_client = None

    glossary_pairs = list(glossary_pairs or ())
    envelope = protect_immutable_spans(source)
    all_issues: List[str] = []

    if ai_client is not None:
        try:
            raw = _translate_candidate(
                envelope.protected, src_lang, tgt_lang,
                model=model, glossary_pairs=glossary_pairs, ai_client=ai_client,
            )
            candidate = restore_immutable_spans(raw, envelope.mapping)
            if candidate:
                gated = gate_and_revise(
                    source, candidate, src_lang, tgt_lang,
                    critical=True, model=model,
                    immutable_literals=envelope.mapping.values(),
                    glossary_pairs=glossary_pairs, ai_client=ai_client,
                )
                if gated.get("ok") and gated.get("text"):
                    gated["provider_path"] = "primary"
                    return gated
                all_issues.extend(gated.get("issues", []))
            else:
                all_issues.append("empty_first_pass")
        except Exception as exc:
            logger.warning("[QualityGate] critical first pass unavailable: %s", exc)
            all_issues.append("first_pass_unavailable")

        # Fresh retry from protected source. This is not a string patch and does
        # not reuse the bad translation; it receives only structural failure codes.
        try:
            raw_retry = _translate_candidate(
                envelope.protected, src_lang, tgt_lang,
                model=model, glossary_pairs=glossary_pairs, ai_client=ai_client,
                retry_issues=all_issues,
            )
            retry_candidate = restore_immutable_spans(raw_retry, envelope.mapping)
            if retry_candidate:
                retry_gate = gate_and_revise(
                    source, retry_candidate, src_lang, tgt_lang,
                    critical=True, model=model,
                    immutable_literals=envelope.mapping.values(),
                    glossary_pairs=glossary_pairs, ai_client=ai_client,
                )
                if retry_gate.get("ok") and retry_gate.get("text"):
                    retry_gate["provider_path"] = "fresh_retry"
                    return retry_gate
                all_issues.extend(retry_gate.get("issues", []))
            else:
                all_issues.append("empty_fresh_retry")
        except Exception as exc:
            logger.warning("[QualityGate] fresh critical retry unavailable: %s", exc)
            all_issues.append("fresh_retry_unavailable")

    if fallback_translate is not None:
        try:
            fallback_raw = fallback_translate(envelope.protected, src_lang, tgt_lang)
            fallback_candidate = restore_immutable_spans(fallback_raw or "", envelope.mapping)
            checked = validate_translation(
                source, fallback_candidate, src_lang, tgt_lang,
                immutable_literals=envelope.mapping.values(),
                glossary_pairs=glossary_pairs,
                require_paragraph_fidelity=True,
            )
            if checked.ok:
                return {
                    "ok": True,
                    "text": fallback_candidate,
                    "issues": checked.issues,
                    "hard_issues": [],
                    "warnings": _dedupe(checked.warnings + ["used_validated_fallback"]),
                    "reviewed": False,
                    "degraded": True,
                    "cacheable": False,
                    "path": "validated_fallback",
                    "provider_path": "fallback",
                }
            all_issues.extend(checked.issues)
        except Exception as exc:
            logger.warning("[QualityGate] fallback translator unavailable: %s", exc)
            all_issues.append("fallback_unavailable")

    return {
        "ok": False,
        "text": None,
        "issues": _dedupe(all_issues or ["no_translation_candidate"]),
        "hard_issues": _dedupe(all_issues or ["no_translation_candidate"]),
        "warnings": [],
        "reviewed": False,
        "degraded": True,
        "cacheable": False,
        "path": "all_candidates_failed",
        "provider_path": "none",
    }


def translation_failure_message(tgt_lang: str) -> str:
    low = (tgt_lang or "").lower()
    if low.startswith("id"):
        return "⚠️ Terjemahan sementara gagal diperiksa. Silakan kirim ulang pesan ini."
    if low.startswith("zh"):
        return "⚠️ 翻譯暫時無法完成品質檢查，請重新傳送這則訊息。"
    return "⚠️ Translation could not be completed safely. Please resend the message."
