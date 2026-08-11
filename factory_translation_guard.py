"""Deterministic acceptance boundary for the factory Chinese↔Indonesian bot.

The LLM is allowed to phrase a translation naturally, but it is not allowed to
change plant facts.  This module compiles the editable factory knowledge and the
approved regression corpus into one versioned guard that is used at every exit
boundary: exact corrections, provider output, cache admission and TM admission.

The implementation is local and provider-neutral.  It never calls an external
service and therefore remains available during provider failures.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import factory_knowledge
import factory_quantity_semantics as fqs_module
import factory_message_semantics as fmr_module

FACTORY_TRANSLATION_GUARD_API_VERSION = 1
FACTORY_TRANSLATION_GUARD_BUILD_ID = "2026-08-11.2-bidirectional-source-relations"

_ROOT = Path(__file__).resolve().parent
_DEFAULT_KNOWLEDGE = _ROOT / "factory_knowledge.json"
_DEFAULT_REGRESSION = _ROOT / "factory_translation_regression.json"
_SUPPORTED = {("zh", "id"), ("id", "zh")}
_SPACE_RE = re.compile(r"\s+")
_SOURCE_KEY_STRIP_RE = re.compile(r"[^0-9a-z\u3400-\u9fff%+./_@#-]+", re.I)
_TARGET_PUNCT_RE = re.compile(r"[\s\u3000]+")
_NUMBER_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>噸|吨|公噸|公吨|公斤|千克|kilogram|kg|KG|公克|克|gram|g|G|毫米|milimeter|mm|MM|公分|厘米|sentimeter|cm|CM|公尺|米|meter|m|M|ton|persen|%)(?![A-Za-z])"
)
_CODE_RE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z]{1,8}\d{1,8}(?:[./_-][A-Z0-9]+)*|\d+[A-Z][A-Z0-9./_-]*)(?![A-Za-z0-9])", re.I)

_UNIT_TARGETS: Dict[str, Tuple[str, ...]] = {
    "噸": ("ton",), "吨": ("ton",),
    "公斤": ("kg", "kilogram"), "千克": ("kg", "kilogram"), "kg": ("kg", "kilogram"),
    "公克": ("g", "gram"), "克": ("g", "gram"), "g": ("g", "gram"),
    "毫米": ("mm", "milimeter"), "mm": ("mm", "milimeter"),
    "公分": ("cm", "sentimeter"), "厘米": ("cm", "sentimeter"), "cm": ("cm", "sentimeter"),
    "公尺": ("m", "meter"), "米": ("m", "meter"), "m": ("m", "meter"),
    "%": ("%", "persen"),
}

_UNIT_TARGETS_ID_ZH: Dict[str, Tuple[str, ...]] = {
    "ton": ("噸", "吨", "公噸", "公吨", "ton"),
    "kilogram": ("公斤", "千克", "kg"), "kg": ("公斤", "千克", "kg"),
    "gram": ("公克", "克", "g"), "g": ("公克", "克", "g"),
    "milimeter": ("毫米", "mm"), "mm": ("毫米", "mm"),
    "sentimeter": ("公分", "厘米", "cm"), "cm": ("公分", "厘米", "cm"),
    "meter": ("公尺", "米", "m"), "m": ("公尺", "米", "m"),
    "persen": ("%", "百分比"), "%": ("%", "百分比"),
}


class GuardConfigurationError(ValueError):
    """Raised when approved translation assets contradict each other."""


@dataclass(frozen=True)
class GuardReport:
    ok: bool
    hard_issues: Tuple[str, ...]
    matched_cards: Tuple[str, ...]
    exact_case_id: str
    asset_fingerprint: str

    @property
    def issues(self) -> List[str]:
        return list(self.hard_issues)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "hard_issues": list(self.hard_issues),
            "matched_cards": list(self.matched_cards),
            "exact_case_id": self.exact_case_id,
            "asset_fingerprint": self.asset_fingerprint,
        }


def _lang(value: Any) -> str:
    low = str(value or "").strip().lower().replace("_", "-")
    if low.startswith("zh"):
        return "zh"
    if low.startswith("id"):
        return "id"
    return low.split("-", 1)[0]


def supports_direction(src: Any, tgt: Any) -> bool:
    return (_lang(src), _lang(tgt)) in _SUPPORTED


def direction_key(src: Any, tgt: Any) -> str:
    return f"{_lang(src)}-{_lang(tgt)}"


def canonical_source_key(value: Any) -> str:
    """Build a punctuation/spacing-insensitive key without paraphrase matching.

    This intentionally normalizes only presentation differences.  It does not
    equate synonyms such as 週一/星期一 or semantic paraphrases, so a verified
    sentence can never be pasted onto a different instruction.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("\u3000", " ")
    return _SOURCE_KEY_STRIP_RE.sub("", text)


def _normalize_target(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _TARGET_PUNCT_RE.sub(" ", text).strip()


def _compact_target(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff%+./_@#-]+", "", _normalize_target(value), flags=re.I)


def _dedupe(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _normalize_number(raw: str) -> str:
    value = str(raw or "").strip().replace(",", ".")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value


def _contains_phrase(target_norm: str, phrase: Any) -> bool:
    needle = _normalize_target(phrase)
    return bool(needle) and needle in target_norm


class FactoryTranslationGuard:
    """Versioned local acceptance boundary compiled from approved assets."""

    def __init__(self, knowledge_path: Optional[os.PathLike[str] | str] = None,
                 regression_path: Optional[os.PathLike[str] | str] = None):
        self.knowledge_path = Path(knowledge_path or os.environ.get("FACTORY_KNOWLEDGE_PATH") or _DEFAULT_KNOWLEDGE).resolve()
        self.regression_path = Path(regression_path or os.environ.get("FACTORY_TRANSLATION_REGRESSION_PATH") or _DEFAULT_REGRESSION).resolve()
        self._lock = threading.RLock()
        self._knowledge = factory_knowledge.FactoryKnowledgeStore(str(self.knowledge_path))
        self._regression: Dict[str, Any] = {}
        self._exact_index: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._fingerprint = ""
        self._self_test: Dict[str, Any] = {}
        self.reload()

    def _load_regression(self) -> Dict[str, Any]:
        with self.regression_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        if not isinstance(document, dict) or not isinstance(document.get("cases"), list):
            raise GuardConfigurationError("factory_translation_regression.json must contain a cases list")
        seen_ids = set()
        for index, case in enumerate(document["cases"]):
            if not isinstance(case, dict):
                raise GuardConfigurationError(f"regression cases[{index}] must be an object")
            case_id = str(case.get("id") or "").strip()
            direction = str(case.get("direction") or "").strip().lower()
            source = str(case.get("source") or "").strip()
            target = str(case.get("verified_target") or "").strip()
            if not case_id or case_id in seen_ids:
                raise GuardConfigurationError(f"invalid or duplicate regression id: {case_id!r}")
            if direction not in {"zh-id", "id-zh"} or not source or not target:
                raise GuardConfigurationError(f"invalid regression case: {case_id}")
            seen_ids.add(case_id)
        return document

    @staticmethod
    def _knowledge_exact_cases(document: Mapping[str, Any]) -> Iterable[Dict[str, Any]]:
        for entry in document.get("entries", []) or []:
            entry_id = str(entry.get("id") or "factory_knowledge")
            directions = set(str(item).lower() for item in (entry.get("directions") or []))
            for index, example in enumerate(entry.get("examples", []) or []):
                if not isinstance(example, Mapping) or not example.get("source") or not example.get("target"):
                    continue
                for direction in directions & {"zh-id", "id-zh"}:
                    yield {
                        "id": f"knowledge:{entry_id}:{index}",
                        "direction": direction,
                        "source": str(example["source"]),
                        "verified_target": str(example["target"]),
                        "required_target_any_groups": [],
                        "forbidden_target": [str(example.get("bad_target") or "")] if example.get("bad_target") else [],
                        "origin": "factory_knowledge",
                        "priority": 200,
                    }

    def _compile_exact_index(self, regression: Mapping[str, Any], knowledge_doc: Mapping[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for case in regression.get("cases", []) or []:
            row = dict(case)
            row["origin"] = "regression"
            row["priority"] = 300
            rows.append(row)
        rows.extend(self._knowledge_exact_cases(knowledge_doc))
        rows.sort(key=lambda row: int(row.get("priority", 0)), reverse=True)
        index: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in rows:
            key = (str(row["direction"]).lower(), canonical_source_key(row["source"]))
            if not key[1]:
                continue
            previous = index.get(key)
            if previous:
                if _normalize_target(previous["verified_target"]) != _normalize_target(row["verified_target"]):
                    raise GuardConfigurationError(
                        "conflicting verified targets for canonical source key: "
                        f"{previous.get('id')} vs {row.get('id')}"
                    )
                continue
            index[key] = row
        return index

    def _calculate_fingerprint(self, regression: Mapping[str, Any], knowledge_doc: Mapping[str, Any]) -> str:
        payload = {
            "guard_build": FACTORY_TRANSLATION_GUARD_BUILD_ID,
            "regression": regression,
            "knowledge": knowledge_doc,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def reload(self) -> Dict[str, Any]:
        with self._lock:
            self._knowledge.reload(force=True)
            knowledge_doc = self._knowledge.document()
            regression = self._load_regression()
            exact_index = self._compile_exact_index(regression, knowledge_doc)
            fingerprint = self._calculate_fingerprint(regression, knowledge_doc)
            self._regression = regression
            self._exact_index = exact_index
            self._fingerprint = fingerprint
            self._self_test = self._run_self_test()
            return self.health()

    @property
    def asset_fingerprint(self) -> str:
        with self._lock:
            return self._fingerprint

    def exact_case(self, source: Any, src: Any, tgt: Any) -> Optional[Dict[str, Any]]:
        if not supports_direction(src, tgt):
            return None
        with self._lock:
            row = self._exact_index.get((direction_key(src, tgt), canonical_source_key(source)))
            return dict(row) if row else None

    def exact_verified_target(self, source: Any, src: Any, tgt: Any) -> Optional[str]:
        case = self.exact_case(source, src, tgt)
        return str(case.get("verified_target") or "").strip() if case else None

    def retrieve_cards(self, source: Any, src: Any, tgt: Any, limit: int = 8) -> List[Dict[str, Any]]:
        if not supports_direction(src, tgt):
            return []
        return self._knowledge.retrieve(str(source or ""), _lang(src), _lang(tgt), limit=max(1, int(limit or 1)))

    def _dominant_cards(self, source: Any, src: Any, tgt: Any, cards: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        """Keep only knowledge cards that have dominant evidence for this source.

        The knowledge retriever intentionally favors high-priority entries so the
        prompt has broad context.  A hard acceptance gate must be narrower: an
        incidental shared word such as ``入庫`` must not make an unrelated card
        reject an otherwise approved translation.  Exact approved examples bind
        to their own entry; otherwise only cards close to the best lexical score
        participate in the hard gate.
        """
        rows = [dict(card) for card in cards if isinstance(card, Mapping)]
        if not rows:
            return []

        exact = self.exact_case(source, src, tgt)
        exact_entry_id = ""
        if exact and str(exact.get("id") or "").startswith("knowledge:"):
            parts = str(exact["id"]).split(":", 2)
            if len(parts) >= 2:
                exact_entry_id = parts[1]
        if exact_entry_id:
            bound = [card for card in rows if str(card.get("id") or "") == exact_entry_id]
            if bound:
                return bound

        scores = [max(0, int(card.get("match_score", 0) or 0)) for card in rows]
        top_score = max(scores or [0])
        # A score of six is at least one strong phrase or two independent regex
        # hits.  Requiring proximity to the best score prevents weak overlaps
        # from becoming hard requirements while retaining genuinely co-matched
        # factory concepts.
        floor = max(6, top_score - 2)
        selected = [
            card for card in rows
            if int(card.get("match_score", 0) or 0) >= floor
        ]
        selected.sort(
            key=lambda card: (int(card.get("match_score", 0) or 0), int(card.get("priority", 0) or 0)),
            reverse=True,
        )
        return selected[:4]

    def build_prompt(self, source: Any, src: Any, tgt: Any) -> str:
        if not supports_direction(src, tgt):
            return ""
        retrieved_cards = self.retrieve_cards(source, src, tgt, limit=10)
        cards = self._dominant_cards(source, src, tgt, retrieved_cards)
        exact = self.exact_case(source, src, tgt)
        lines = ["<factory_acceptance_boundary>"]
        lines.append(
            "The final translation will be checked locally against the same approved plant knowledge. "
            "Preserve every actor, action, object, customer name, number, unit, timing, negation, priority, process state and causal relation."
        )
        if exact:
            lines.append(
                "This source differs from an approved correction only by punctuation or spacing. "
                "Use this verified target exactly: " + str(exact["verified_target"])
            )
        if cards:
            lines.append("Matched plant knowledge IDs: " + ", ".join(str(card.get("id")) for card in cards))
        lines.append(
            "Do not output a fluent approximation that violates a required concept or uses a known forbidden translation; "
            "regenerate a source-complete translation before delivery."
        )
        lines.append("</factory_acceptance_boundary>")
        return "\n".join(lines)

    def _validate_regression_case(self, case: Mapping[str, Any], candidate: str) -> List[str]:
        target_norm = _normalize_target(candidate)
        issues: List[str] = []
        for group_index, group in enumerate(case.get("required_target_any_groups", []) or []):
            if group and not any(_contains_phrase(target_norm, phrase) for phrase in group):
                issues.append(f"factory_guard:{case.get('id')}:required_group_missing:{group_index}")
        for phrase in case.get("forbidden_target", []) or []:
            if phrase and _contains_phrase(target_norm, phrase):
                issues.append(f"factory_guard:{case.get('id')}:forbidden:{phrase}")
        return issues

    @staticmethod
    def _validate_names(source: str, candidate: str, protected_names: Sequence[str]) -> List[str]:
        issues: List[str] = []
        for name in _dedupe(protected_names):
            if name in source and name not in candidate:
                issues.append("factory_guard:protected_name_missing:" + name)
        return issues

    @staticmethod
    def _validate_codes(source: str, candidate: str) -> List[str]:
        issues: List[str] = []
        source_codes = _dedupe(match.group(0) for match in _CODE_RE.finditer(source or ""))
        compact_candidate = _compact_target(candidate)
        for code in source_codes:
            if _compact_target(code) not in compact_candidate:
                issues.append("factory_guard:code_missing:" + code)
        return issues

    @staticmethod
    def _validate_quantities(source: str, candidate: str, src: str, tgt: str) -> List[str]:
        issues: List[str] = []
        target_norm = _normalize_target(candidate)
        compact = _compact_target(candidate)
        for match in _NUMBER_UNIT_RE.finditer(source or ""):
            number = _normalize_number(match.group("number"))
            unit_raw = match.group("unit")
            unit_key = unit_raw.casefold()
            if number and number not in compact.replace(",", "."):
                issues.append(f"factory_guard:quantity_number_missing:{number}{unit_raw}")
                continue
            # Only enforce cross-language unit realization for ZH->ID. ID->ZH may
            # correctly preserve SI abbreviations unchanged and is already covered
            # by the immutable-data quality gate.
            if _lang(src) == "zh" and _lang(tgt) == "id":
                allowed = _UNIT_TARGETS.get(unit_key) or _UNIT_TARGETS.get(unit_raw) or ()
                if allowed and not any(re.search(r"(?<![a-z])" + re.escape(term.casefold()) + r"(?![a-z])", target_norm) for term in allowed):
                    issues.append(f"factory_guard:quantity_unit_missing:{number}{unit_raw}")
            elif _lang(src) == "id" and _lang(tgt) == "zh":
                allowed = _UNIT_TARGETS_ID_ZH.get(unit_key) or ()
                if allowed and not any(term.casefold() in target_norm for term in allowed):
                    issues.append(f"factory_guard:quantity_unit_missing:{number}{unit_raw}")
        return issues

    def validate(self, source: Any, candidate: Any, src: Any, tgt: Any,
                 *, protected_names: Sequence[str] = ()) -> GuardReport:
        if not supports_direction(src, tgt):
            return GuardReport(True, (), (), "", self.asset_fingerprint)
        source_text = str(source or "")
        target_text = str(candidate or "").strip()
        if not target_text:
            return GuardReport(False, ("factory_guard:empty_translation",), (), "", self.asset_fingerprint)
        retrieved_cards = self.retrieve_cards(source_text, src, tgt, limit=10)
        cards = self._dominant_cards(source_text, src, tgt, retrieved_cards)
        matched_ids = tuple(str(card.get("id") or "") for card in cards if card.get("id"))
        _ok, knowledge_issues = self._knowledge.validate_translation(cards, source_text, target_text)
        issues: List[str] = list(knowledge_issues or [])
        exact = self.exact_case(source_text, src, tgt)
        if exact:
            issues.extend(self._validate_regression_case(exact, target_text))
        issues.extend(self._validate_names(source_text, target_text, protected_names))
        issues.extend(self._validate_codes(source_text, target_text))
        issues.extend(self._validate_quantities(source_text, target_text, _lang(src), _lang(tgt)))
        quantity_frame = fqs_module.build_frame(source_text, _lang(src), _lang(tgt))
        quantity_ok, quantity_issues = fqs_module.validate_translation(quantity_frame, target_text)
        if not quantity_ok or quantity_issues:
            issues.extend("factory_guard:" + issue for issue in quantity_issues)
        relation_frame = fmr_module.build_frame(
            source_text, _lang(src), _lang(tgt)
        )
        relation_ok, relation_issues = fmr_module.validate_translation(
            relation_frame, target_text
        )
        if not relation_ok or relation_issues:
            issues.extend("factory_guard:" + issue for issue in relation_issues)
        issues = _dedupe(issues)
        return GuardReport(
            ok=not issues,
            hard_issues=tuple(issues),
            matched_cards=matched_ids,
            exact_case_id=str(exact.get("id") or "") if exact else "",
            asset_fingerprint=self.asset_fingerprint,
        )

    def _run_self_test(self) -> Dict[str, Any]:
        verified = 0
        rejected_bad = 0
        failures: List[str] = []
        knowledge_doc = self._knowledge.document()
        # Every approved knowledge example must retrieve its own card and pass.
        for entry in knowledge_doc.get("entries", []) or []:
            directions = set(entry.get("directions", []) or [])
            for example in entry.get("examples", []) or []:
                if not isinstance(example, Mapping):
                    continue
                for direction in directions & {"zh-id", "id-zh"}:
                    src, tgt = direction.split("-", 1)
                    # Validate the approved example against its owning card.
                    # Runtime uses dominant-card selection, but configuration
                    # self-tests must not be affected by incidental overlaps from
                    # other entries.
                    owner_card = dict(entry)
                    owner_card.setdefault("match_score", 999)
                    _ok, owner_issues = self._knowledge.validate_translation(
                        [owner_card], str(example.get("source") or ""), str(example.get("target") or "")
                    )
                    supplemental = []
                    supplemental.extend(self._validate_names(str(example.get("source") or ""), str(example.get("target") or ""), ()))
                    supplemental.extend(self._validate_codes(str(example.get("source") or ""), str(example.get("target") or "")))
                    source_example = str(example.get("source") or "")
                    target_example = str(example.get("target") or "")
                    supplemental.extend(self._validate_quantities(source_example, target_example, src, tgt))
                    qframe = fqs_module.build_frame(source_example, src, tgt)
                    _qok, qissues = fqs_module.validate_translation(qframe, target_example)
                    supplemental.extend("factory_guard:" + issue for issue in qissues)
                    approved_issues = _dedupe(list(owner_issues or []) + supplemental)
                    if not approved_issues:
                        verified += 1
                    else:
                        failures.append(f"approved example rejected:{entry.get('id')}:{approved_issues}")
                    bad = str(example.get("bad_target") or "").strip()
                    if bad:
                        _bad_ok, bad_owner_issues = self._knowledge.validate_translation(
                            [owner_card], str(example.get("source") or ""), bad
                        )
                        bad_source = str(example.get("source") or "")
                        bad_qframe = fqs_module.build_frame(bad_source, src, tgt)
                        _bad_qok, bad_qissues = fqs_module.validate_translation(bad_qframe, bad)
                        bad_issues = _dedupe(
                            list(bad_owner_issues or [])
                            + self._validate_codes(bad_source, bad)
                            + self._validate_quantities(bad_source, bad, src, tgt)
                            + ["factory_guard:" + issue for issue in bad_qissues]
                        )
                        if bad_issues:
                            rejected_bad += 1
                        else:
                            failures.append(f"known bad example accepted:{entry.get('id')}")
        # The formal regression corpus must remain exact-addressable and valid.
        for case in self._regression.get("cases", []) or []:
            src, tgt = str(case["direction"]).split("-", 1)
            exact = self.exact_case(case["source"], src, tgt)
            if not exact:
                failures.append(f"regression missing exact index:{case.get('id')}")
                continue
            report = self.validate(case["source"], case["verified_target"], src, tgt)
            if not report.ok:
                failures.append(f"regression target rejected:{case.get('id')}:{report.issues}")
        if failures:
            raise GuardConfigurationError("; ".join(failures[:20]))
        return {
            "approved_examples_validated": verified,
            "known_bad_examples_rejected": rejected_bad,
            "regression_cases": len(self._regression.get("cases", []) or []),
            "ok": True,
        }

    def health(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "api_version": FACTORY_TRANSLATION_GUARD_API_VERSION,
                "build_id": FACTORY_TRANSLATION_GUARD_BUILD_ID,
                "asset_fingerprint": self._fingerprint,
                "exact_case_count": len(self._exact_index),
                "knowledge": self._knowledge.health(),
                "regression_path": str(self.regression_path),
                "self_test": dict(self._self_test),
            }


_DEFAULT_GUARD = FactoryTranslationGuard()


def reload() -> Dict[str, Any]:
    return _DEFAULT_GUARD.reload()


def exact_case(source: Any, src: Any, tgt: Any) -> Optional[Dict[str, Any]]:
    return _DEFAULT_GUARD.exact_case(source, src, tgt)


def exact_verified_target(source: Any, src: Any, tgt: Any) -> Optional[str]:
    return _DEFAULT_GUARD.exact_verified_target(source, src, tgt)


def retrieve_cards(source: Any, src: Any, tgt: Any, limit: int = 8) -> List[Dict[str, Any]]:
    return _DEFAULT_GUARD.retrieve_cards(source, src, tgt, limit=limit)


def build_prompt(source: Any, src: Any, tgt: Any) -> str:
    return _DEFAULT_GUARD.build_prompt(source, src, tgt)


def validate_translation(source: Any, candidate: Any, src: Any, tgt: Any,
                         *, protected_names: Sequence[str] = ()) -> GuardReport:
    return _DEFAULT_GUARD.validate(source, candidate, src, tgt, protected_names=protected_names)


def asset_fingerprint() -> str:
    return _DEFAULT_GUARD.asset_fingerprint


def health() -> Dict[str, Any]:
    return _DEFAULT_GUARD.health()
