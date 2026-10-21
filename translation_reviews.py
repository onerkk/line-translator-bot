"""Screenshot reviews as learning data, not patches.

Workflow
--------
1. The owner screenshots a real LINE translation and asks the reviewer (Claude
   in chat) to check it.
2. The reviewer answers with a *review bundle*: one JSON file in
   ``translation_reviews/`` following the schema below. No code.
3. At boot (and via ``python translation_reviews.py ingest``) every bundle is
   validated and applied to the three learning layers the bot already has:

   * ``corrections``  → approved rows in active_learning → contrastive few-shot
                        cases retrieved by translation_casebook for similar
                        messages (bad target vs corrected target)
   * ``glossary``     → merged into GLOSSARY_LOOKUP before factory_terminology
                        indexes it (soft/hard mode, bidirectional)
   * ``rules``        → factory_knowledge cards (context + examples +
                        forbidden phrases) retrieved by the knowledge store

   Ingestion is idempotent: active_learning dedups identical corrections, the
   glossary never overwrites an existing term unless ``replace`` is true, and
   rule ids are prefixed with the bundle id.

4. ``learning_loop_eval.py`` measures whether the bundles generalise.

Bundle schema (schema_version 1)
--------------------------------
{
  "schema_version": 1,
  "review_id": "2026-10-21-samples",          # [a-z0-9-_.], unique per file
  "source": "screenshot review by Claude",
  "items": [
    {
      "id": "sample-collection",                # unique within the bundle
      "src": "zh", "tgt": "id",
      "source": "蘇比 你今日體檢沒有採集檢…",     # exact LINE text
      "observed": "…belum mengambil sampel…",   # what the bot produced (optional)
      "corrected": "…sampel kamu belum diambil…",
      "category": "term",                        # term|negation|number_unit|station_equipment|tone|omission|other
      "reason": "採集檢 = 體檢時的檢體採集，主詞是工人不是體檢",
      "glossary": [{"zh": "採集檢", "id": "pengambilan sampel (pemeriksaan kesehatan)", "mode": "soft"}],
      "rule": {                                  # optional knowledge card
        "when": [["體檢", "採集"]],                # all_groups matcher (list of OR-groups, AND between groups)
        "directions": ["zh-id"],
        "context": ["採集檢 指體檢時的檢體（尿液／糞便）採集；主詞是工人。"],
        "forbidden": ["pemeriksaan kesehatan belum mengambil"]
      }
    }
  ]
}
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

TRANSLATION_REVIEWS_BUILD_ID = "2026-10-21.1-review-bundles"
REVIEWS_DIRNAME = "translation_reviews"
CATEGORIES = ("term", "negation", "number_unit", "station_equipment", "tone", "omission", "other")
_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,78}")
_PLACEHOLDER_RE = re.compile(r"\.\.\.|…|<[^>]+>|\[[^\]]*\]|XXX", re.I)


class ReviewError(ValueError):
    pass


def reviews_dir(base: str | None = None) -> str:
    return os.path.join(base or os.path.dirname(os.path.abspath(__file__)), REVIEWS_DIRNAME)


def load_bundles(base: str | None = None) -> List[Dict[str, Any]]:
    bundles = []
    for path in sorted(glob.glob(os.path.join(reviews_dir(base), "*.json"))):
        with open(path, encoding="utf-8") as fh:
            bundles.append(validate_bundle(json.load(fh), os.path.basename(path)))
    return bundles


def validate_bundle(doc: Dict[str, Any], name: str = "bundle") -> Dict[str, Any]:
    if not isinstance(doc, dict) or int(doc.get("schema_version", 0) or 0) != 1:
        raise ReviewError(f"{name}: schema_version must be 1")
    review_id = str(doc.get("review_id") or "").strip()
    if not _ID_RE.fullmatch(review_id):
        raise ReviewError(f"{name}: review_id invalid")
    items = doc.get("items")
    if not isinstance(items, list) or not items:
        raise ReviewError(f"{name}: items must be a non-empty list")
    seen = set()
    for index, item in enumerate(items):
        where = f"{name}: items[{index}]"
        if not isinstance(item, dict):
            raise ReviewError(where + " must be an object")
        item_id = str(item.get("id") or "").strip()
        if not _ID_RE.fullmatch(item_id) or item_id in seen:
            raise ReviewError(where + " id invalid or duplicate")
        seen.add(item_id)
        src, tgt = str(item.get("src") or "").lower(), str(item.get("tgt") or "").lower()
        if src == tgt or not re.fullmatch(r"[a-z]{2,3}", src) or not re.fullmatch(r"[a-z]{2,3}", tgt):
            raise ReviewError(where + " src/tgt invalid")
        source = str(item.get("source") or "").strip()
        corrected = str(item.get("corrected") or "").strip()
        if not source or not corrected:
            raise ReviewError(where + " source and corrected are required")
        if _PLACEHOLDER_RE.search(corrected) and not _PLACEHOLDER_RE.search(source):
            raise ReviewError(where + " corrected contains a placeholder; write the full translation")
        if corrected == str(item.get("observed") or "").strip():
            raise ReviewError(where + " corrected equals observed; nothing to learn")
        if item.get("category") not in CATEGORIES:
            raise ReviewError(where + f" category must be one of {CATEGORIES}")
        for term in item.get("glossary") or []:
            if not isinstance(term, dict) or not str(term.get("zh") or "").strip() or not str(term.get("id") or "").strip():
                raise ReviewError(where + " glossary entries need zh and id")
            if term.get("mode", "soft") not in ("soft", "hard"):
                raise ReviewError(where + " glossary mode must be soft or hard")
        rule = item.get("rule")
        if rule is not None:
            if not isinstance(rule, dict) or not rule.get("when") or not rule.get("context"):
                raise ReviewError(where + " rule needs when and context")
            if not all(isinstance(group, list) and group for group in rule["when"]):
                raise ReviewError(where + " rule.when must be a list of non-empty lists")
    return doc


# ─── glossary layer ────────────────────────────────────────────────────────

def apply_glossary(lookup: Dict[str, Any], bundles: List[Dict[str, Any]]) -> int:
    """Merge reviewed terms into the raw glossary dict before normalisation."""
    added = 0
    for bundle in bundles:
        for item in bundle["items"]:
            for term in item.get("glossary") or []:
                zh, idn = str(term["zh"]).strip(), str(term["id"]).strip()
                if zh in lookup and not term.get("replace"):
                    continue
                lookup[zh] = {
                    "idn": idn, "canonical_idn": idn,
                    "translation_mode": term.get("mode", "soft"),
                    "note_zh": str(item.get("reason") or "")[:200],
                    "review_note": "review:" + bundle["review_id"] + "/" + item["id"],
                    "reverse_safe": bool(term.get("reverse_safe", False)),
                }
                added += 1
    return added


# ─── knowledge layer ───────────────────────────────────────────────────────

def knowledge_entries(bundles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Review rules as factory_knowledge cards (validated by that module)."""
    entries = []
    for bundle in bundles:
        for item in bundle["items"]:
            rule = item.get("rule")
            if not rule:
                continue
            entry_id = re.sub(r"[^a-z0-9_.-]", "-", ("review_" + bundle["review_id"] + "_" + item["id"]).lower())[:80]
            directions = [str(d).lower() for d in (rule.get("directions") or [item["src"] + "-" + item["tgt"]])]
            entry = {
                "id": entry_id, "enabled": True, "priority": int(rule.get("priority", 200)),
                "directions": directions, "title": str(rule.get("title") or item.get("reason") or item["id"])[:120],
                "match": {"all_groups": [[str(x) for x in group] for group in rule["when"]],
                          "require_all_groups": True, "min_score": int(rule.get("min_score", 4))},
                "context": [str(line) for line in rule["context"]],
                "requirements": [],
                "examples": [{"source": item["source"], "target": item["corrected"]}],
            }
            if rule.get("forbidden"):
                entry["forbidden_target_rules"] = [{"phrases": [str(p) for p in rule["forbidden"]]}]
            entries.append(entry)
    return entries


# ─── correction layer ──────────────────────────────────────────────────────

def apply_corrections(al_module: Any, bundles: List[Dict[str, Any]]) -> Dict[str, int]:
    stats = {"submitted": 0, "duplicate": 0, "rejected": 0}
    for bundle in bundles:
        for item in bundle["items"]:
            result = al_module.submit_correction(
                item["source"], str(item.get("observed") or ""), item["corrected"],
                item["src"], item["tgt"],
                correction_reason=("[" + item["category"] + "] " + str(item.get("reason") or ""))[:400],
                corrected_by="review:" + bundle["review_id"], group_id="",
                auto_approve=True, approved_by="review:" + bundle["review_id"],
            )
            if not result.get("ok"):
                stats["rejected"] += 1
                logger.warning("[Reviews] %s/%s rejected: %s", bundle["review_id"], item["id"], result.get("error"))
            elif result.get("duplicate"):
                stats["duplicate"] += 1
            else:
                stats["submitted"] += 1
    return stats


def category_counts(bundles: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for bundle in bundles:
        for item in bundle["items"]:
            counts[item["category"]] = counts.get(item["category"], 0) + 1
    return counts


def health(base: str | None = None) -> Dict[str, Any]:
    bundles = load_bundles(base)
    return {"build_id": TRANSLATION_REVIEWS_BUILD_ID, "bundles": len(bundles),
            "items": sum(len(b["items"]) for b in bundles), "categories": category_counts(bundles),
            "rules": len(knowledge_entries(bundles))}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("validate", "health"):
        print(json.dumps(health(), ensure_ascii=False, indent=1))
        return 0
    if argv[0] == "ingest":
        import active_learning as al
        al.init()
        bundles = load_bundles()
        print(json.dumps({"corrections": apply_corrections(al, bundles), **health()}, ensure_ascii=False, indent=1))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
