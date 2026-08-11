#!/usr/bin/env python3
"""Offline release gate for factory translation assets.

Run this before deployment.  It verifies that the policy, knowledge, glossary,
regression corpus and deterministic guard are mutually compatible without
calling any translation provider or importing Flask/LINE.
"""
from __future__ import annotations

import argparse
import ast
import json
import py_compile
import sys
from pathlib import Path
from typing import Any, Dict, List

import factory_translation_guard as guard
import factory_translation_policy as policy
import factory_measurement_semantics as measurement
import factory_message_semantics as message_semantics
import translation_casebook as casebook

ROOT = Path(__file__).resolve().parent
REQUIRED_JSON = (
    "factory_knowledge.json",
    "factory_translation_regression.json",
    "glossary_data.json",
)
REQUIRED_PYTHON = (
    "app.py",
    "factory_measurement_semantics.py",
    "factory_message_semantics.py",
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


def _literal_module_assignments(name: str) -> Dict[str, Any]:
    """Read top-level literal assignments without importing the production app."""
    tree = ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)
    values: Dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except Exception:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                values[target.id] = value
    return values


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

    measurement_health: Dict[str, Any] = {}
    try:
        measurement_health = measurement.health()
        if not ((measurement_health.get("self_test") or {}).get("ok")):
            errors.append("measurement_semantics_self_test_failed")

        app_literals = _literal_module_assignments("app.py")
        expected_api = app_literals.get("_EXPECTED_FACTORY_MEASUREMENT_SEMANTICS_API_VERSION")
        expected_build = app_literals.get("_EXPECTED_FACTORY_MEASUREMENT_SEMANTICS_BUILD_ID")
        if expected_api != measurement.FACTORY_MEASUREMENT_SEMANTICS_API_VERSION:
            errors.append(
                "measurement_semantics_api_mismatch:"
                f"app={expected_api!r}:module={measurement.FACTORY_MEASUREMENT_SEMANTICS_API_VERSION!r}"
            )
        if expected_build != measurement.FACTORY_MEASUREMENT_SEMANTICS_BUILD_ID:
            errors.append(
                "measurement_semantics_build_mismatch:"
                f"app={expected_build!r}:module={measurement.FACTORY_MEASUREMENT_SEMANTICS_BUILD_ID!r}"
            )
    except Exception as exc:
        errors.append(f"measurement_semantics_exception:{type(exc).__name__}:{exc}")

    message_semantics_health: Dict[str, Any] = {}
    try:
        message_semantics_health = message_semantics.health()
        if not ((message_semantics_health.get("self_test") or {}).get("ok")):
            errors.append("message_semantics_self_test_failed")

        app_literals = _literal_module_assignments("app.py")
        expected_api = app_literals.get("_EXPECTED_FACTORY_MESSAGE_SEMANTICS_API_VERSION")
        expected_build = app_literals.get("_EXPECTED_FACTORY_MESSAGE_SEMANTICS_BUILD_ID")
        if expected_api != message_semantics.FACTORY_MESSAGE_SEMANTICS_API_VERSION:
            errors.append(
                "message_semantics_api_mismatch:"
                f"app={expected_api!r}:module={message_semantics.FACTORY_MESSAGE_SEMANTICS_API_VERSION!r}"
            )
        if expected_build != message_semantics.FACTORY_MESSAGE_SEMANTICS_BUILD_ID:
            errors.append(
                "message_semantics_build_mismatch:"
                f"app={expected_build!r}:module={message_semantics.FACTORY_MESSAGE_SEMANTICS_BUILD_ID!r}"
            )

        route_probes = (
            (
                "Kg di layar monitor dengan di timbangan katrol selisih 6 kg. "
                "Saya laporan dengan id Ketu kelas",
                "id-ID", "zh-TW",
                "螢幕顯示的重量與天車電子磅秤相差 6 公斤。我用班長的 ID 回報。",
            ),
            (
                "Di layar monitor 995 kg sedangkan di timbangan katrol 989 kg",
                "id", "zh",
                "螢幕顯示 995 公斤，而天車電子磅秤顯示 989 公斤。",
            ),
            (
                "我過去了了解看看", "zh-TW", "id-ID",
                "Saya ke sana dulu untuk mengecek situasinya.",
            ),
        )
        for source, src, tgt, expected in route_probes:
            actual = message_semantics.translate_source_directly(source, src, tgt)
            if actual != expected:
                errors.append(
                    "message_semantics_route_mismatch:"
                    f"source={source!r}:expected={expected!r}:actual={actual!r}"
                )
    except Exception as exc:
        errors.append(f"message_semantics_exception:{type(exc).__name__}:{exc}")

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
        if policy.fail_closed("zh", "id"):
            errors.append("delivery_blocking_must_remain_disabled")
        if policy.require_review_success("zh", "id"):
            errors.append("review_must_not_veto_a_locally_valid_translation")
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
        "measurement_semantics": measurement_health,
        "message_semantics": message_semantics_health,
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
