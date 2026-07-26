#!/usr/bin/env python3
"""Offline release gate for factory translation assets.

Run this before deployment.  It verifies that the policy, knowledge, glossary,
regression corpus and deterministic guard are mutually compatible without
calling any translation provider or importing Flask/LINE.
"""
from __future__ import annotations

import argparse
import json
import py_compile
import sys
from pathlib import Path
from typing import Any, Dict, List

import factory_translation_guard as guard
import factory_translation_policy as policy
import translation_casebook as casebook

ROOT = Path(__file__).resolve().parent
REQUIRED_JSON = (
    "factory_knowledge.json",
    "factory_translation_regression.json",
    "glossary_data.json",
)
REQUIRED_PYTHON = (
    "factory_translation_guard.py",
    "factory_translation_policy.py",
    "translation_casebook.py",
    "factory_knowledge.py",
    "factory_terminology.py",
    "glossary_policy.py",
    "glossary_enforcement.py",
    "translation_quality_gate.py",
    "factory_semantic_audit.py",
    "factory_structured_report.py",
)


def _load_json(name: str) -> Any:
    with (ROOT / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def audit() -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    documents: Dict[str, Any] = {}

    for name in REQUIRED_JSON:
        try:
            documents[name] = _load_json(name)
        except Exception as exc:
            errors.append(f"json:{name}:{type(exc).__name__}:{exc}")

    for name in REQUIRED_PYTHON:
        try:
            py_compile.compile(str(ROOT / name), doraise=True)
        except Exception as exc:
            errors.append(f"compile:{name}:{type(exc).__name__}:{exc}")

    try:
        health = guard.reload()
    except Exception as exc:
        health = {}
        errors.append(f"guard_reload:{type(exc).__name__}:{exc}")

    regression = documents.get("factory_translation_regression.json") or {}
    verified_count = 0
    rejected_forbidden_count = 0
    for row in regression.get("cases", []) or []:
        case_id = str(row.get("id") or "unknown")
        try:
            src, tgt = str(row["direction"]).split("-", 1)
            good = guard.validate_translation(row["source"], row["verified_target"], src, tgt)
            if not good.ok:
                errors.append(f"regression_good_rejected:{case_id}:{good.issues}")
            else:
                verified_count += 1
            forbidden = [str(x) for x in row.get("forbidden_target", []) or [] if str(x).strip()]
            if forbidden:
                bad = str(row["verified_target"]).rstrip() + " " + forbidden[0]
                bad_report = guard.validate_translation(row["source"], bad, src, tgt)
                if bad_report.ok:
                    errors.append(f"regression_forbidden_accepted:{case_id}:{forbidden[0]}")
                else:
                    rejected_forbidden_count += 1
            exact = guard.exact_verified_target(row["source"], src, tgt)
            if not exact:
                errors.append(f"regression_not_exact_addressable:{case_id}")
        except Exception as exc:
            errors.append(f"regression_exception:{case_id}:{type(exc).__name__}:{exc}")

    try:
        if policy.mode() != "always":
            warnings.append(f"policy_mode_override:{policy.mode()}")
        if policy.review_mode() != "always":
            warnings.append(f"review_mode_override:{policy.review_mode()}")
        if not policy.fail_closed("zh", "id"):
            warnings.append("factory_fail_closed_disabled")
        if not policy.require_review_success("zh", "id"):
            warnings.append("review_success_requirement_disabled")
        if policy.allow_generic_nmt_fallback("zh", "id"):
            warnings.append("generic_nmt_fallback_enabled")
    except Exception as exc:
        errors.append(f"policy_exception:{type(exc).__name__}:{exc}")

    # Exact correction normalization must ignore only presentation differences,
    # never semantic paraphrases.
    try:
        exact_rows = casebook.collect_cases([
            {"zh": "本月木箱，暫不裝箱。", "id": "A", "dir": "zh2id", "origin": "human_correction"}
        ])
        if casebook.exact_verified_target("本月木箱 暫不裝箱", exact_rows) != "A":
            errors.append("casebook_punctuation_exact_failed")
        if casebook.exact_verified_target("下月木箱暫不裝箱", exact_rows) is not None:
            errors.append("casebook_paraphrase_misclassified_as_exact")
    except Exception as exc:
        errors.append(f"casebook_exception:{type(exc).__name__}:{exc}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "policy": policy.health(),
        "guard": health,
        "regression": {
            "case_count": len(regression.get("cases", []) or []),
            "verified_targets_accepted": verified_count,
            "forbidden_probes_rejected": rejected_forbidden_count,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()
    report = audit()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("PASS" if report["ok"] else "FAIL")
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
