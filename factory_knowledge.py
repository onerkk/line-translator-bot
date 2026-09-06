"""Configuration-driven factory context retrieval for translation.

The translation model cannot infer undocumented plant shorthand reliably.  This
module keeps that knowledge in an editable JSON file and retrieves only the
entries relevant to the current message.  New terminology/workflows are added as
data, not as Python sentence patches.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import factory_message_semantics as message_semantics

FACTORY_KNOWLEDGE_API_VERSION = 1
DEFAULT_FILENAME = "factory_knowledge.json"


class KnowledgeError(ValueError):
    pass


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _contains(text: str, term: str) -> bool:
    return bool(term) and _normalize(term) in text


def _direction_key(src: str, tgt: str) -> str:
    return f"{(src or '').lower()}-{(tgt or '').lower()}"


def _safe_list(value: Any, field: str) -> List[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise KnowledgeError(f"{field} must be a list")
    return value


def validate_document(document: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(document, dict):
        raise KnowledgeError("knowledge document must be an object")
    schema = int(document.get("schema_version", 0) or 0)
    if schema != 1:
        raise KnowledgeError(f"unsupported schema_version={schema}")
    entries = _safe_list(document.get("entries"), "entries")
    seen = set()
    normalized_entries: List[Dict[str, Any]] = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise KnowledgeError(f"entries[{index}] must be an object")
        entry = copy.deepcopy(raw)
        entry_id = str(entry.get("id") or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,79}", entry_id):
            raise KnowledgeError(f"entries[{index}].id is invalid")
        if entry_id in seen:
            raise KnowledgeError(f"duplicate entry id: {entry_id}")
        seen.add(entry_id)
        directions = _safe_list(entry.get("directions", []), f"entries[{index}].directions")
        if not directions:
            raise KnowledgeError(f"entries[{index}].directions is empty")
        for direction in directions:
            if not re.fullmatch(r"[a-z]{2,3}-[a-z]{2,3}", str(direction).lower()):
                raise KnowledgeError(f"entries[{index}] has invalid direction: {direction}")
        match = entry.get("match") or {}
        if not isinstance(match, dict):
            raise KnowledgeError(f"entries[{index}].match must be an object")
        if match.get("semantic_relation") not in (None, "erp_data_release"):
            raise KnowledgeError(f"entries[{index}].match has an unknown semantic relation")
        if entry.get("semantic_validator") not in (None, "erp_data_release"):
            raise KnowledgeError(f"entries[{index}] has an unknown semantic validator")
        has_positive = any(match.get(key) for key in ("strong_phrases", "any_terms", "all_groups", "regex_any"))
        if not has_positive:
            raise KnowledgeError(f"entries[{index}].match has no positive matcher")
        for group_index, group in enumerate(_safe_list(match.get("all_groups", []), "all_groups")):
            if not isinstance(group, list) or not any(str(x).strip() for x in group):
                raise KnowledgeError(f"entries[{index}].match.all_groups[{group_index}] is invalid")
        for regex in (_safe_list(match.get("regex_any", []), "regex_any")
                      + _safe_list(match.get("required_regex_any", []), "required_regex_any")):
            try:
                re.compile(str(regex), re.I)
            except re.error as exc:
                raise KnowledgeError(f"entries[{index}] invalid regex {regex!r}: {exc}") from exc
        for rule in _safe_list(entry.get("forbidden_target_rules", []), "forbidden_target_rules"):
            if not isinstance(rule, dict) or not _safe_list(rule.get("phrases"), "phrases"):
                raise KnowledgeError(f"entries[{index}] has invalid forbidden_target_rules")
            for field in ("when_source", "unless_source"):
                condition = rule.get(field)
                if condition:
                    # Reuse the matcher schema, including mandatory sense evidence.
                    validate_document({"schema_version": 1, "entries": [{
                        "id": "condition", "directions": directions, "match": condition,
                    }]})
        requirements = _safe_list(entry.get("requirements", []), f"entries[{index}].requirements")
        for req_index, req in enumerate(requirements):
            if not isinstance(req, dict) or not _safe_list(req.get("target_any"), "target_any"):
                raise KnowledgeError(f"entries[{index}].requirements[{req_index}] is invalid")
        entry["id"] = entry_id
        entry["enabled"] = bool(entry.get("enabled", True))
        entry["priority"] = int(entry.get("priority", 50) or 50)
        entry["directions"] = [str(x).lower() for x in directions]
        normalized_entries.append(entry)
    out = copy.deepcopy(document)
    out["entries"] = normalized_entries
    out.setdefault("build_id", "unknown")
    return out


@dataclass(frozen=True)
class MatchResult:
    entry: Dict[str, Any]
    score: int
    evidence: Tuple[str, ...]

    def as_card(self) -> Dict[str, Any]:
        card = copy.deepcopy(self.entry)
        card["match_score"] = self.score
        card["match_evidence"] = list(self.evidence)
        return card


class FactoryKnowledgeStore:
    def __init__(self, path: Optional[str] = None):
        self.path = os.path.abspath(path or os.environ.get("FACTORY_KNOWLEDGE_PATH") or os.path.join(os.path.dirname(__file__), DEFAULT_FILENAME))
        self._lock = threading.RLock()
        self._document: Dict[str, Any] = {}
        self._mtime_ns: Optional[int] = None
        self._hash = ""
        self.reload(force=True)

    def _read(self) -> Dict[str, Any]:
        with open(self.path, "r", encoding="utf-8") as handle:
            return validate_document(json.load(handle))

    def reload(self, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            stat = os.stat(self.path)
            if not force and self._mtime_ns == stat.st_mtime_ns and self._document:
                return self.health()
            document = self._read()
            canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self._document = document
            self._casebook_examples = self._collect_casebook_examples(document)
            self._mtime_ns = stat.st_mtime_ns
            self._hash = hashlib.sha256(canonical).hexdigest()
            return self.health()

    def _reload_if_changed(self) -> None:
        try:
            self.reload(force=False)
        except FileNotFoundError:
            raise KnowledgeError(f"factory knowledge file missing: {self.path}")

    def health(self) -> Dict[str, Any]:
        with self._lock:
            entries = self._document.get("entries", [])
            return {
                "api_version": FACTORY_KNOWLEDGE_API_VERSION,
                "schema_version": self._document.get("schema_version"),
                "build_id": self._document.get("build_id"),
                "entry_count": len(entries),
                "enabled_count": sum(1 for e in entries if e.get("enabled", True)),
                "sha256": self._hash,
                "path": self.path,
            }

    def document(self) -> Dict[str, Any]:
        self._reload_if_changed()
        with self._lock:
            return copy.deepcopy(self._document)

    @staticmethod
    def _collect_casebook_examples(document):
        examples = []
        for entry in document.get("entries", []):
            if entry.get("enabled") is False:
                continue
            for example in entry.get("examples", []) or []:
                if not isinstance(example, dict) or not example.get("source") or not example.get("target"):
                    continue
                for direction, src, tgt in (("zh-id", "zh", "id"), ("id-zh", "id", "zh")):
                    if direction not in entry.get("directions", []):
                        continue
                    examples.append({src: str(example["source"]), tgt: str(example["target"]),
                        "dir": src + "2" + tgt, "bad_target": str(example.get("bad_target") or ""),
                        "reason": str(example.get("reason") or ""), "origin": "factory_knowledge",
                        "case_id": str(entry.get("id") or "factory_knowledge"),
                        "source_match": copy.deepcopy(entry.get("match") or {})})
        return examples

    def casebook_examples(self):
        """Reuse the compact index; edits reload atomically and callers get copies."""
        self._reload_if_changed()
        with self._lock:
            return copy.deepcopy(self._casebook_examples)

    @staticmethod
    def _score_entry(entry: Dict[str, Any], normalized_text: str) -> Optional[MatchResult]:
        match = entry.get("match") or {}
        evidence: List[str] = []
        if match.get("semantic_relation") == "erp_data_release":
            if not message_semantics.build_data_release_frame(normalized_text).get("active"):
                return None
        for term in match.get("none_terms", []) or []:
            if _contains(normalized_text, term):
                return None
        # Lexical overlap retrieves context; it cannot establish the word's
        # sense. Require explicit relation evidence when a card declares it.
        # Apply this before scoring so repeated incidental words never override it.
        required = match.get("required_regex_any", []) or []
        if required and not any(re.search(str(pattern), normalized_text, re.I)
                                for pattern in required):
            return None
        score = 0
        strong_hits = [str(term) for term in match.get("strong_phrases", []) or [] if _contains(normalized_text, term)]
        if strong_hits:
            score += 8 + min(4, len(strong_hits) - 1)
            evidence.extend(f"strong:{term}" for term in strong_hits[:4])
        all_groups = match.get("all_groups", []) or []
        require_all = bool(match.get("require_all_groups", True))
        matched_groups = 0
        for group in all_groups:
            hits = [str(term) for term in group if _contains(normalized_text, term)]
            if hits:
                matched_groups += 1
                score += 4
                evidence.append("group:" + hits[0])
            elif require_all:
                return None
        if all_groups and not require_all and matched_groups == 0:
            return None
        any_hits = [str(term) for term in match.get("any_terms", []) or [] if _contains(normalized_text, term)]
        score += min(6, len(any_hits))
        evidence.extend(f"term:{term}" for term in any_hits[:6])
        regex_hits = []
        for pattern in match.get("regex_any", []) or []:
            if re.search(str(pattern), normalized_text, flags=re.I):
                regex_hits.append(str(pattern))
        score += min(6, len(regex_hits) * 3)
        evidence.extend(f"regex:{pattern}" for pattern in regex_hits[:2])
        if match.get("require_any") and not (strong_hits or any_hits or regex_hits):
            return None
        min_score = int(match.get("min_score", 1) or 1)
        if score < min_score:
            return None
        return MatchResult(entry=entry, score=score, evidence=tuple(evidence))

    def retrieve(self, text: str, src: str, tgt: str, limit: int = 3) -> List[Dict[str, Any]]:
        self._reload_if_changed()
        normalized_text = _normalize(text)
        direction = _direction_key(src, tgt)
        matches: List[MatchResult] = []
        with self._lock:
            for entry in self._document.get("entries", []):
                if not entry.get("enabled", True) or direction not in entry.get("directions", []):
                    continue
                matched = self._score_entry(entry, normalized_text)
                if matched:
                    matches.append(matched)
        matches.sort(key=lambda item: (int(item.entry.get("priority", 50)), item.score), reverse=True)
        cards = [item.as_card() for item in matches[: max(1, int(limit or 1))]]
        for card in cards:
            card["_active_forbidden_phrases"] = applicable_forbidden_phrases(card, text)
        return cards

    @staticmethod
    def build_prompt(cards: Sequence[Dict[str, Any]], *, include_examples: bool = True) -> str:
        if not cards:
            return ""
        lines = ["<factory_context_knowledge>"]
        lines.append("The following plant-specific knowledge was retrieved from an editable local knowledge base. It describes intended meaning, not text to copy verbatim. Apply it only to this source message. Preserve every source fact and do not invent operational details.")
        for card in cards:
            entry_id = card.get("id", "unknown")
            lines.append(f"<knowledge id='{entry_id}' score='{int(card.get('match_score', 0))}'>")
            if card.get("title"):
                lines.append("Title: " + str(card["title"]))
            for sentence in card.get("context", []) or []:
                lines.append("Context: " + str(sentence))
            preferred = card.get("preferred_target_phrases", []) or []
            if preferred:
                lines.append("Preferred target concepts/phrases: " + "; ".join(str(x) for x in preferred))
            forbidden = card.get("_active_forbidden_phrases",
                                 card.get("forbidden_target_phrases", [])) or []
            if forbidden:
                lines.append("Forbidden target wording for this sense: " + "; ".join(str(x) for x in forbidden))
            for example in (card.get("examples", []) or []) if include_examples else ():
                if isinstance(example, dict) and example.get("source") and example.get("target"):
                    lines.append("Example source: " + str(example["source"]))
                    lines.append("Example target: " + str(example["target"]))
            lines.append("</knowledge>")
        lines.append("Before output, silently back-translate and verify actor, action, object, system-vs-physical movement, timing/distribution, cause and consequence. Output only the final translation.")
        lines.append("</factory_context_knowledge>")
        return "\n".join(lines)

    @staticmethod
    def validate_translation(cards: Sequence[Dict[str, Any]], source_text: str, translation: str) -> Tuple[bool, List[str]]:
        if not cards:
            return True, []
        src_norm = _normalize(source_text)
        tgt_norm = _normalize(translation)
        issues: List[str] = []
        if not tgt_norm:
            return False, ["factory_knowledge:empty_translation"]
        for card in cards:
            entry_id = str(card.get("id") or "unknown")
            # A caller may hold a card retrieved for an earlier/different source.
            # Always establish applicability from the current message again.
            if card.get("match") and not match_source(source_text, card["match"])[0]:
                continue
            if card.get("semantic_validator") == "erp_data_release":
                # This card supplies terminology and examples, while the same
                # predicate contract used by the runtime owns factual validation.
                # Fixed target substrings cannot model negation or passive voice.
                frame = message_semantics.build_data_release_frame(source_text)
                _, relation_issues = message_semantics.validate_translation(frame, translation)
                issues.extend(relation_issues)
            for phrase in applicable_forbidden_phrases(card, source_text):
                if _contains(tgt_norm, phrase):
                    issues.append(f"factory_knowledge:{entry_id}:forbidden:{phrase}")
            for req in card.get("requirements", []) or []:
                source_any = req.get("source_any", []) or []
                if source_any and not any(_contains(src_norm, term) for term in source_any):
                    continue
                source_none = req.get("source_none", []) or []
                if source_none and any(_contains(src_norm, term) for term in source_none):
                    continue
                target_any = req.get("target_any", []) or []
                if target_any and not any(_contains(tgt_norm, term) for term in target_any):
                    issue = str(req.get("issue") or "required_concept_missing")
                    issues.append(f"factory_knowledge:{entry_id}:{issue}")
            for pattern in card.get("preserve_source_regex", []) or []:
                for literal in re.findall(str(pattern), str(source_text or ""), flags=re.I):
                    value = literal[0] if isinstance(literal, tuple) else literal
                    value = str(value)
                    if value and _normalize(value) not in tgt_norm:
                        issues.append(f"factory_knowledge:{entry_id}:missing_literal:{value}")
        # stable order and no duplicates
        return not issues, list(dict.fromkeys(issues))

    def replace_document(self, document: Dict[str, Any]) -> Dict[str, Any]:
        validated = validate_document(document)
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix="factory_knowledge_", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(validated, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        return self.reload(force=True)

    def upsert_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        document = self.document()
        entry_id = str((entry or {}).get("id") or "")
        replaced = False
        for index, current in enumerate(document.get("entries", [])):
            if current.get("id") == entry_id:
                document["entries"][index] = copy.deepcopy(entry)
                replaced = True
                break
        if not replaced:
            document.setdefault("entries", []).append(copy.deepcopy(entry))
        health = self.replace_document(document)
        health["updated_id"] = entry_id
        health["created"] = not replaced
        return health

    def delete_entry(self, entry_id: str) -> Dict[str, Any]:
        document = self.document()
        before = len(document.get("entries", []))
        document["entries"] = [entry for entry in document.get("entries", []) if entry.get("id") != entry_id]
        if len(document["entries"]) == before:
            raise KnowledgeError(f"entry not found: {entry_id}")
        health = self.replace_document(document)
        health["deleted_id"] = entry_id
        return health


def match_source(text: str, match: Dict[str, Any]) -> Tuple[bool, int, List[str]]:
    """One source contract for knowledge, historical examples and validation."""
    if not match:
        return True, 0, []
    try:
        result = FactoryKnowledgeStore._score_entry({"match": match}, _normalize(text))
    except (re.error, TypeError, ValueError):
        # Malformed historical/user examples are ineligible; they must never
        # take down translation. Persisted knowledge edits are validated earlier.
        return False, 0, ["invalid_source_match"]
    if result is None:
        return False, 0, []
    return True, result.score, list(result.evidence)


def applicable_forbidden_phrases(card: Dict[str, Any], source: str) -> List[str]:
    """A phrase forbidden in one sense may be required by another source claim.

    Conditional rules are resolved against source evidence, never against the
    candidate. This prevents a translation from granting itself an exception.
    """
    phrases = list(card.get("forbidden_target_phrases", []) or [])
    for rule in card.get("forbidden_target_rules", []) or []:
        if rule.get("when_source") and not match_source(source, rule["when_source"])[0]:
            continue
        if rule.get("unless_source") and match_source(source, rule["unless_source"])[0]:
            continue
        phrases.extend(rule.get("phrases", []) or [])
    return list(dict.fromkeys(phrases))


_DEFAULT_STORE: Optional[FactoryKnowledgeStore] = None
_DEFAULT_LOCK = threading.Lock()


def get_store() -> FactoryKnowledgeStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_STORE is None:
                _DEFAULT_STORE = FactoryKnowledgeStore()
    return _DEFAULT_STORE


def retrieve(text: str, src: str, tgt: str, limit: int = 3) -> List[Dict[str, Any]]:
    return get_store().retrieve(text, src, tgt, limit=limit)


def build_prompt(cards: Sequence[Dict[str, Any]], *, include_examples: bool = True) -> str:
    return get_store().build_prompt(cards, include_examples=include_examples)


def validate_translation(cards: Sequence[Dict[str, Any]], source_text: str, translation: str) -> Tuple[bool, List[str]]:
    return get_store().validate_translation(cards, source_text, translation)
