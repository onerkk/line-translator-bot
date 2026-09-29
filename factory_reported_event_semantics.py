"""Resolve and validate actors in reported factory findings.

Chinese factory messages often introduce a finding with a reporting verb and
then omit the subject of the reported discovery. When the named reporter is
the only supported actor, flattening the finding into an agentless passive
changes who observed the problem. This module records that relation without
providing a sentence translation or a source/target phrase pair.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List


BUILD_ID = "2026-09-29.1-reported-finding-actor"

_REPORTER = re.compile(
    r"(?P<actor>@[A-Za-z0-9_.-]+|[\u3400-\u9fff]{1,8})\s*"
    r"(?:向|對|跟)?\s*(?P<report>反應|反映|回報|報告|通報)"
)
_DISCOVERY = re.compile(
    r"(?:在|於)[\u3400-\u9fffA-Za-z0-9_-]{0,12}?"
    r"(?:被)?(?P<verb>發現|查到|看到|查獲|檢查到|找出|發覺)"
)
_EXPLICIT_AGENT = re.compile(
    r"(?:由|被)\s*(?P<actor>@[A-Za-z0-9_.-]+|[\u3400-\u9fff]{1,8})\s*"
    r"(?:(?:在|於)[\u3400-\u9fffA-Za-z0-9_-]{0,12}?)?\s*"
    r"(?:所)?\s*(?:發現|查到|看到|查獲|檢查到|找出|發覺)"
)
_COPULA_AGENT = re.compile(
    r"是(?P<actor>(?!在|於)[\u3400-\u9fff]{1,6})\s*"
    r"(?:在|於)[\u3400-\u9fffA-Za-z0-9_-]{0,12}?"
    r"(?:發現|查到|看到|查獲|檢查到|找出|發覺)"
)
_CLAUSE_SPLIT = re.compile(r"[，,。.!?！？;；\n]+")
_REPORT_ID = re.compile(
    r"\b(?:melaporkan|melapor|memberitahu|memberi\s+tahu|"
    r"menyampaikan|mengabarkan|melaporkannya)\b", re.I
)
_DISCOVERY_ACTIVE_ID = re.compile(
    r"\b(?:menemukan|mendapati|mengetahui|mengidentifikasi|"
    r"melihat|mendeteksi|temukan|menemukan\s+adanya)\b", re.I
)
_DISCOVERY_PASSIVE_ID = re.compile(
    r"\b(?:ditemukan|terdeteksi|terlihat|diketahui)\s+oleh\s+", re.I
)
_PRONOUN_ID = re.compile(r"\b(?:ia|dia|beliau)\b", re.I)


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or ""))


def _lang(value: Any) -> str:
    text = str(value or "").lower().replace("_", "-")
    return "zh" if text.startswith("zh") else "id" if text.startswith("id") else text


def build_frame(source: Any, src: Any, tgt: Any) -> Dict[str, Any]:
    """Build reported-discovery role facts when the source supports them."""
    if _lang(src) != "zh" or _lang(tgt) != "id":
        return {"active": False, "facts": []}

    facts: List[Dict[str, str]] = []
    for clause in _CLAUSE_SPLIT.split(_norm(source)):
        report = _REPORTER.search(clause)
        if not report:
            continue
        reported = clause[report.end():]
        discovery = _DISCOVERY.search(reported)
        if not discovery:
            continue

        # An explicitly named different finder takes precedence. Otherwise,
        # resolve the omitted subject to the current named topic/reporter.
        explicit = _EXPLICIT_AGENT.search(reported) or _COPULA_AGENT.search(reported)
        finder = explicit.group("actor") if explicit else report.group("actor")
        facts.append({
            "sense": "reported_discovery_actor",
            "reporter": report.group("actor"),
            "finder": finder,
            "report_verb": report.group("report"),
            "discovery_verb": discovery.group("verb"),
            "source_evidence": clause.strip(),
            "agent_resolution": "explicit" if explicit else "topic_continuity",
        })

    return {"active": bool(facts), "facts": facts}


def build_prompt(source: Any, src: Any, tgt: Any) -> str:
    frame = build_frame(source, src, tgt)
    if not frame["active"]:
        return ""
    return (
        "<reported_factory_finding_roles>\n"
        "A reporting clause contains a discovered event. Preserve the reporter and the actor of the discovery as separate semantic roles. "
        "Where Chinese omits the discovery subject and the named reporter is the current topic with no different finder stated, "
        "carry that actor into the discovery clause. Render both reporting and discovery explicitly in natural Indonesian; do not "
        "flatten the finding into an agentless passive or leave its actor recoverable only from context. If the source names a different "
        "finder, keep that person as the discovery actor.\n"
        "</reported_factory_finding_roles>"
    )


def validate_translation(frame: Any, target: Any) -> List[str]:
    """Flag missing report/discovery actors without requiring fixed wording."""
    if not isinstance(frame, dict) or not frame.get("active"):
        return []
    clauses = [part.strip() for part in _CLAUSE_SPLIT.split(_norm(target)) if part.strip()]
    issues: List[str] = []
    for fact in frame.get("facts", []):
        reporter = str(fact.get("reporter") or "")
        finder = str(fact.get("finder") or "")
        if not reporter or not finder:
            continue

        report_actor_present = False
        discovery_actor_present = False
        finder_seen = False
        for clause in clauses:
            reporter_positions = [m.start() for m in re.finditer(re.escape(reporter), clause, re.I)]
            finder_positions = [m.start() for m in re.finditer(re.escape(finder), clause, re.I)]
            if finder_positions:
                finder_seen = True
            if reporter_positions and _REPORT_ID.search(clause):
                report_actor_present = True

            # The named person may remain explicit or be carried by an
            # unambiguous Indonesian pronoun in a following clause. Passive
            # constructions are accepted only when the agent is stated.
            if any(
                (0 <= m.start() - pos <= 100)
                for pos in finder_positions
                for m in _DISCOVERY_ACTIVE_ID.finditer(clause)
            ):
                discovery_actor_present = True
            if finder_seen and _PRONOUN_ID.search(clause) and _DISCOVERY_ACTIVE_ID.search(clause):
                discovery_actor_present = True
            for passive in _DISCOVERY_PASSIVE_ID.finditer(clause):
                tail = clause[passive.end():passive.end() + 80]
                if re.match(r"\s*" + re.escape(finder) + r"\b", tail, re.I):
                    discovery_actor_present = True

        if not report_actor_present:
            issues.append("reported_event:reporter_action_missing")
        if not discovery_actor_present:
            issues.append("reported_event:discovery_actor_missing")
    return list(dict.fromkeys(issues))
