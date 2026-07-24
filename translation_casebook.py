"""Verified translation correction retrieval and prompt compilation.

A human correction must influence more than the exact source sentence.  This
module turns built-in examples, administrator examples and active-learning
corrections into one contrastive casebook, retrieves only closely related cases,
and exposes them to the runtime semantic contract.

The implementation is deliberately provider-neutral and dependency-free.  It
uses weighted character/word features plus inverse-document-frequency weighting;
no embedding API is required on the user-facing path.
"""
from __future__ import annotations

import math
import re
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

TRANSLATION_CASEBOOK_API_VERSION = 2
TRANSLATION_CASEBOOK_BUILD_ID = "2026-07-24.4-unified-correction-casebook"

_HAN_RUN_RE = re.compile(r"[\u3400-\u9fff]+")
_LATIN_WORD_RE = re.compile(r"[a-z0-9]+(?:[-_/][a-z0-9]+)*", re.I)
_SPACE_RE = re.compile(r"\s+")
_CACHE_LOCK = threading.RLock()
_ACTIVE_CACHE: Dict[str, Any] = {"expires": 0.0, "rows": []}


@dataclass(frozen=True)
class RetrievedCase:
    source: str
    target: str
    direction: str
    score: float
    bad_target: str = ""
    reason: str = ""
    origin: str = "example"
    case_id: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "direction": self.direction,
            "score": round(float(self.score), 6),
            "bad_target": self.bad_target,
            "reason": self.reason,
            "origin": self.origin,
            "case_id": self.case_id,
        }


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = text.replace("\u3000", " ")
    return _SPACE_RE.sub(" ", text).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", _normalize(value))


def direction_key(src: str, tgt: str) -> Optional[str]:
    src = str(src or "").lower()
    tgt = str(tgt or "").lower()
    if src == "zh" and tgt == "id":
        return "zh2id"
    if src == "id" and tgt == "zh":
        return "id2zh"
    return None


def _case_from_example(raw: Mapping[str, Any], index: int = 0) -> Optional[Dict[str, Any]]:
    direction = str(raw.get("dir") or "zh2id").lower()
    if direction not in ("zh2id", "id2zh"):
        return None
    source = str((raw.get("zh") if direction == "zh2id" else raw.get("id")) or "").strip()
    target = str((raw.get("id") if direction == "zh2id" else raw.get("zh")) or "").strip()
    if not source or not target:
        return None
    bad_target = str(
        (raw.get("bad_id") if direction == "zh2id" else raw.get("bad_zh"))
        or raw.get("bad_target")
        or raw.get("original_translation")
        or ""
    ).strip()
    return {
        "source": source,
        "target": target,
        "direction": direction,
        "bad_target": bad_target,
        "reason": str(raw.get("reason") or raw.get("correction_reason") or "").strip(),
        "origin": str(raw.get("origin") or ("human_correction" if bad_target else "example")),
        # ``id`` is the Indonesian target field in the historical example
        # schema, so it must never be mistaken for a case identifier.
        "case_id": str(raw.get("case_id") or f"example:{index}"),
    }


def _case_from_correction(raw: Mapping[str, Any], index: int = 0) -> Optional[Dict[str, Any]]:
    src_lang = str(raw.get("src_lang") or "").lower()
    tgt_lang = str(raw.get("tgt_lang") or "").lower()
    direction = direction_key(src_lang, tgt_lang)
    if not direction:
        return None
    source = str(raw.get("src_text") or "").strip()
    target = str(raw.get("corrected_translation") or "").strip()
    if not source or not target:
        return None
    return {
        "source": source,
        "target": target,
        "direction": direction,
        "bad_target": str(raw.get("original_translation") or "").strip(),
        "reason": str(raw.get("correction_reason") or "").strip(),
        "origin": "active_learning",
        "case_id": "correction:" + str(raw.get("id") or index),
    }


def active_corrections_snapshot(active_learning_module: Any, *, ttl_seconds: int = 60, limit: int = 2000) -> List[Dict[str, Any]]:
    """Read corrections with a short TTL so SQLite is not queried per message."""
    now = time.monotonic()
    with _CACHE_LOCK:
        if float(_ACTIVE_CACHE.get("expires", 0.0)) > now:
            return [dict(row) for row in _ACTIVE_CACHE.get("rows", [])]
    rows: List[Dict[str, Any]] = []
    try:
        raw_rows = active_learning_module.list_corrections(limit=max(1, int(limit)), offset=0)
        for index, raw in enumerate(raw_rows or []):
            if isinstance(raw, Mapping):
                case = _case_from_correction(raw, index)
                if case:
                    rows.append(case)
    except Exception:
        rows = []
    with _CACHE_LOCK:
        _ACTIVE_CACHE["rows"] = [dict(row) for row in rows]
        _ACTIVE_CACHE["expires"] = now + max(5, int(ttl_seconds))
    return rows


def invalidate_active_cache() -> None:
    with _CACHE_LOCK:
        _ACTIVE_CACHE["expires"] = 0.0
        _ACTIVE_CACHE["rows"] = []


def collect_cases(
    examples: Sequence[Mapping[str, Any]] = (),
    corrections: Sequence[Mapping[str, Any]] = (),
) -> List[Dict[str, Any]]:
    """Compile one conflict-free casebook with deterministic precedence.

    Precedence is: newest active-learning correction > human/custom correction
    > factory knowledge > built-in example.  Only one verified target survives
    for an exact source+direction key, preventing an old correction and a newer
    correction from being injected into the same prompt as contradictory truth.
    """
    candidates: List[Tuple[int, int, Dict[str, Any]]] = []

    # list_corrections() is newest-first.  Earlier rows therefore win ties.
    for index, raw in enumerate(corrections or ()):
        if not isinstance(raw, Mapping):
            continue
        case = raw if {"source", "target", "direction"}.issubset(raw.keys()) else _case_from_correction(raw, index)
        if case:
            candidates.append((400, -index, dict(case)))

    for index, raw in enumerate(examples or ()):
        if not isinstance(raw, Mapping):
            continue
        case = _case_from_example(raw, index)
        if not case:
            continue
        origin = str(case.get("origin") or "example").lower()
        if case.get("bad_target") or origin in {"human_correction", "custom_correction", "manual_correction"}:
            priority = 330
        elif origin == "factory_knowledge":
            priority = 240
        elif origin == "custom_example":
            priority = 180
        else:
            priority = 100
        # Custom examples are stored oldest-first; newer entries win ties.
        candidates.append((priority, index, dict(case)))

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected: List[Dict[str, Any]] = []
    seen_source = set()
    seen_pair = set()
    for _priority, _order, case in candidates:
        source_key = (str(case.get("direction")), _normalize(case.get("source")))
        pair_key = source_key + (_normalize(case.get("target")),)
        if source_key in seen_source or pair_key in seen_pair:
            continue
        seen_source.add(source_key)
        seen_pair.add(pair_key)
        selected.append(case)
    return selected


def _features(text: str, direction: str) -> Counter[str]:
    normalized = _normalize(text)
    features: Counter[str] = Counter()
    if direction == "zh2id":
        # Chinese factory messages freely insert punctuation/spaces inside one
        # term (上下料 / 上、下料 / 上下 料).  Build character n-grams over the
        # punctuation-free Han sequence so those variants remain retrievable.
        han_sequence = "".join(_HAN_RUN_RE.findall(normalized))
        length = len(han_sequence)
        if length == 1:
            features["h:" + han_sequence] += 0.25
        for size in (2, 3, 4, 5):
            if length < size:
                continue
            weight = {2: 0.8, 3: 1.4, 4: 2.0, 5: 2.5}[size]
            for pos in range(0, length - size + 1):
                features["h:" + han_sequence[pos:pos + size]] += weight
        for token in _LATIN_WORD_RE.findall(normalized):
            if len(token) >= 2:
                features["w:" + token] += 1.4
    else:
        words = [w for w in _LATIN_WORD_RE.findall(normalized) if len(w) >= 2]
        for word in words:
            features["w:" + word] += 1.0 if len(word) < 4 else 1.5
        for left, right in zip(words, words[1:]):
            features[f"b:{left} {right}"] += 2.2
    return features


def _idf(cases: Sequence[Mapping[str, Any]], direction: str) -> Dict[str, float]:
    docs = []
    for case in cases:
        if str(case.get("direction")) != direction:
            continue
        docs.append(set(_features(str(case.get("source") or ""), direction)))
    total = max(1, len(docs))
    counts: Counter[str] = Counter()
    for doc in docs:
        counts.update(doc)
    return {feature: math.log((total + 1.0) / (count + 1.0)) + 1.0 for feature, count in counts.items()}


def _weighted_overlap(query: Counter[str], candidate: Counter[str], idf: Mapping[str, float]) -> Tuple[float, float, float]:
    if not query or not candidate:
        return 0.0, 0.0, 0.0
    overlap = 0.0
    q_total = 0.0
    c_total = 0.0
    for feature, weight in query.items():
        q_total += float(weight) * float(idf.get(feature, 1.0))
    for feature, weight in candidate.items():
        c_total += float(weight) * float(idf.get(feature, 1.0))
    for feature in query.keys() & candidate.keys():
        overlap += min(float(query[feature]), float(candidate[feature])) * float(idf.get(feature, 1.0))
    coverage = overlap / q_total if q_total else 0.0
    precision = overlap / c_total if c_total else 0.0
    harmonic = (2.0 * coverage * precision / (coverage + precision)) if (coverage + precision) else 0.0
    return coverage, precision, harmonic


def retrieve(
    query: str,
    src: str,
    tgt: str,
    *,
    examples: Sequence[Mapping[str, Any]] = (),
    corrections: Sequence[Mapping[str, Any]] = (),
    max_cases: int = 3,
    min_score: float = 0.22,
) -> List[Dict[str, Any]]:
    direction = direction_key(src, tgt)
    if not direction or not str(query or "").strip():
        return []
    cases = collect_cases(examples, corrections)
    direction_cases = [case for case in cases if str(case.get("direction")) == direction]
    if not direction_cases:
        return []
    query_norm = _normalize(query)
    query_compact = _compact(query)
    q_features = _features(query, direction)
    idf = _idf(direction_cases, direction)
    ranked: List[RetrievedCase] = []
    for case in direction_cases:
        source = str(case.get("source") or "").strip()
        source_norm = _normalize(source)
        source_compact = _compact(source)
        c_features = _features(source, direction)
        coverage, precision, harmonic = _weighted_overlap(q_features, c_features, idf)
        score = 0.55 * harmonic + 0.30 * coverage + 0.15 * precision
        # Long Chinese notices make cosine-like overlap deceptively small even
        # when several rare factory concepts match.  Reward multiple shared Han
        # phrases explicitly; one generic bigram alone is never enough.
        if direction == "zh2id":
            shared_han = [feature[2:] for feature in (q_features.keys() & c_features.keys())
                          if feature.startswith("h:")]
            long_shared = sum(1 for phrase in shared_han if len(phrase) >= 3)
            short_shared = sum(1 for phrase in shared_han if len(phrase) == 2)
            score += min(0.62, 0.13 * long_shared + 0.04 * short_shared)
        if query_norm == source_norm:
            score = 10.0
        elif query_compact and source_compact and (query_compact in source_compact or source_compact in query_compact):
            shorter = min(len(query_compact), len(source_compact))
            longer = max(len(query_compact), len(source_compact))
            score += 0.55 + 0.35 * (shorter / max(1, longer))
        # Human corrections deserve a modest tie-breaker, never enough to make an
        # unrelated case pass the threshold.
        if case.get("bad_target"):
            score += 0.03
        if score < float(min_score):
            continue
        ranked.append(RetrievedCase(
            source=source,
            target=str(case.get("target") or "").strip(),
            direction=direction,
            score=score,
            bad_target=str(case.get("bad_target") or "").strip(),
            reason=str(case.get("reason") or "").strip(),
            origin=str(case.get("origin") or "example"),
            case_id=str(case.get("case_id") or ""),
        ))
    ranked.sort(key=lambda item: (item.score, bool(item.bad_target), len(item.source)), reverse=True)
    return [item.as_dict() for item in ranked[: max(1, int(max_cases or 1))]]



_ID_STOPWORDS = {
    "yang", "dan", "atau", "untuk", "dengan", "dari", "pada", "saat", "ini",
    "itu", "akan", "telah", "sudah", "agar", "setiap", "mohon", "harap", "oleh",
    "dilakukan", "melalui", "secara", "terhadap", "dalam", "ke", "di", "hal",
}


def _token_sequence(text: str, direction: str) -> List[str]:
    normalized = _normalize(text)
    if direction == "zh2id":
        return [word for word in _LATIN_WORD_RE.findall(normalized) if len(word) >= 2]
    return [char for char in _compact(normalized) if "\u3400" <= char <= "\u9fff"]


def _ngram_strings(tokens: Sequence[str], min_n: int, max_n: int) -> List[str]:
    out: List[str] = []
    joiner = " " if tokens and any(len(token) > 1 for token in tokens) else ""
    for size in range(max_n, min_n - 1, -1):
        for pos in range(0, len(tokens) - size + 1):
            out.append(joiner.join(tokens[pos:pos + size]))
    return out


def _contrastive_anchors(case: Mapping[str, Any], limit: int = 8) -> List[str]:
    """Extract target-side phrases that distinguish the correction from the bad output.

    This is deliberately conservative: anchors are used only for high-similarity
    human-correction cases and never to force a whole verified sentence onto a
    different source.
    """
    direction = str(case.get("direction") or "")
    good = str(case.get("target") or "")
    bad = str(case.get("bad_target") or "")
    if not good or not bad or direction not in {"zh2id", "id2zh"}:
        return []
    good_tokens = _token_sequence(good, direction)
    bad_norm = _normalize(bad)
    anchors: List[str] = []
    min_n, max_n = ((2, 5) if direction == "zh2id" else (2, 6))
    for phrase in _ngram_strings(good_tokens, min_n, max_n):
        norm = _normalize(phrase)
        if not norm or norm in bad_norm:
            continue
        words = phrase.split()
        if direction == "zh2id" and all(word in _ID_STOPWORDS for word in words):
            continue
        if any(norm in _normalize(existing) or _normalize(existing) in norm for existing in anchors):
            continue
        anchors.append(phrase)
        if len(anchors) >= max(1, int(limit)):
            break
    return anchors


def _sequence_similarity(left: str, right: str) -> float:
    left_norm = _normalize(left)
    right_norm = _normalize(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    left_tokens = set(_LATIN_WORD_RE.findall(left_norm)) or set(_compact(left_norm))
    right_tokens = set(_LATIN_WORD_RE.findall(right_norm)) or set(_compact(right_norm))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def validate_translation_cases(
    cases: Sequence[Mapping[str, Any]],
    candidate: str,
) -> Tuple[bool, List[str]]:
    """Reject recurrence of a known human-corrected failure pattern.

    The validator does not require exact wording.  It checks only strongly
    retrieved contrastive cases, rejects output that remains closer to the known
    wrong translation than to the verified correction, and looks for at least
    one correction-specific semantic anchor.
    """
    text = str(candidate or "").strip()
    if not text:
        return False, ["casebook_empty_translation"]
    issues: List[str] = []
    candidate_norm = _normalize(text)
    for case in cases or ():
        score = float(case.get("score") or 0.0)
        bad = str(case.get("bad_target") or "").strip()
        good = str(case.get("target") or "").strip()
        if score < 0.28 or not bad or not good:
            continue
        bad_similarity = _sequence_similarity(candidate_norm, bad)
        good_similarity = _sequence_similarity(candidate_norm, good)
        case_id = str(case.get("case_id") or "unknown")
        if bad_similarity >= 0.72 and bad_similarity > good_similarity + 0.08:
            issues.append(f"known_bad_translation_pattern:{case_id}")
            continue
        anchors = _contrastive_anchors(case)
        if anchors and not any(_normalize(anchor) in candidate_norm for anchor in anchors):
            # Only enforce anchors for very close source matches.  Lower-scored
            # cases remain prompt evidence, never hard lexical constraints.
            if score >= 0.52:
                issues.append(f"missing_correction_semantic_anchor:{case_id}")
    return not issues, issues


def exact_verified_target(
    source: str,
    cases: Sequence[Mapping[str, Any]],
) -> Optional[str]:
    """Return a human/factory verified target only for an exact source match."""
    source_norm = _normalize(source)
    for case in cases or ():
        if _normalize(case.get("source")) == source_norm and str(case.get("target") or "").strip():
            return str(case.get("target") or "").strip()
    return None

def build_prompt(cases: Sequence[Mapping[str, Any]]) -> str:
    if not cases:
        return ""
    lines = ["<verified_translation_cases>"]
    lines.append(
        "These are retrieved, human-verified translation cases. Use them as contrastive evidence for meaning and register, "
        "not as sentences to copy blindly. The current source remains authoritative."
    )
    for index, case in enumerate(cases, 1):
        lines.append(
            f"<case index='{index}' id='{str(case.get('case_id') or '')}' score='{float(case.get('score') or 0):.3f}'>"
        )
        lines.append("Source pattern: " + str(case.get("source") or ""))
        bad = str(case.get("bad_target") or "").strip()
        if bad:
            lines.append("Known incorrect translation — do not repeat this error: " + bad)
        lines.append("Verified translation: " + str(case.get("target") or ""))
        reason = str(case.get("reason") or "").strip()
        if reason:
            lines.append("Correction rationale: " + reason)
        lines.append("</case>")
    lines.append(
        "Silently compare the current source with these cases. Transfer only the relevant semantic distinction; preserve the current source's own actor, action, object, modality, scope, timing and methods."
    )
    lines.append("</verified_translation_cases>")
    return "\n".join(lines)


def casebook_requires_review(cases: Sequence[Mapping[str, Any]]) -> bool:
    if not cases:
        return False
    top = max(float(case.get("score") or 0.0) for case in cases)
    strong_correction = any(
        bool(case.get("bad_target")) and float(case.get("score") or 0.0) >= 0.28
        for case in cases
    )
    return top >= 0.38 or strong_correction
