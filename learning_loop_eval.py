"""Measure whether the bot actually learns from approved corrections.

The learning loop today: /標錯 → admin approves → the correction becomes a
contrastive few-shot case (translation_casebook) retrieved for similar
messages. Nothing measured whether that makes *new* messages better, so every
prompt change was judged from screenshots. This harness turns the approved
corrections into a held-out evaluation set and scores the real pipeline:

  baseline   – pipeline with the evaluated correction hidden (leave-one-out),
               i.e. what a worker would have received before the admin fixed it
  learned    – same, but the correction stays available for retrieval

A correction that only helps when it is itself retrieved shows memorisation;
the leave-one-out score shows generalisation from the *other* corrections and
rules. Run it before and after any prompt/model change:

    python learning_loop_eval.py --provider openai --limit 40 --out eval_report
    python learning_loop_eval.py --dry-run          # no API calls: similarity only

It never writes to the correction store and never sends LINE messages. Paid
calls: one translation per case per mode, plus one judge call per case when a
judge model is configured (default on; --no-judge to skip).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from typing import Any, Dict, List

os.environ.setdefault("TRANSLATION_CASEBOOK_EVAL", "1")

HARNESS_VERSION = "2026-10-21.1"
ERROR_CATEGORIES = ("term", "negation", "number_unit", "station_equipment", "tone", "omission", "other")


def _load_app():
    import app  # noqa: WPS433 (production module; boot self-tests run here)
    import active_learning as al
    import translation_casebook as casebook
    return app, al, casebook


def load_cases(al, limit: int, group_id: str | None) -> List[Dict[str, Any]]:
    rows = al.list_corrections(limit=limit, offset=0, status="approved",
                               group_id=group_id, include_global=True)
    cases = []
    for row in rows:
        src = str(row.get("src_text") or "").strip()
        tgt = str(row.get("corrected_translation") or "").strip()
        if not src or not tgt or not row.get("src_lang") or not row.get("tgt_lang"):
            continue
        cases.append({
            "id": str(row.get("id")),
            "group_id": str(row.get("group_id") or ""),
            "src_lang": str(row["src_lang"]).lower(),
            "tgt_lang": str(row["tgt_lang"]).lower(),
            "source": src,
            "corrected": tgt,
            "original": str(row.get("original_translation") or "").strip(),
            "reason": str(row.get("correction_reason") or "").strip(),
        })
    return cases


def translate_with_exclusion(app, casebook, case, exclude: bool, provider: str | None) -> str:
    """Run the production pipeline; optionally hide this one correction."""
    original_snapshot = casebook.active_corrections_snapshot

    def filtered(*args, **kwargs):
        rows = original_snapshot(*args, **kwargs)
        if not exclude:
            return rows
        return [r for r in rows if str(r.get("case_id", "")).split(":")[-1] != case["id"]
                and str(r.get("id", "")) != case["id"]]

    casebook.active_corrections_snapshot = filtered
    saved = dict(app._tl.__dict__)
    try:
        app._tl.__dict__.clear()
        app._tl.group_id = case["group_id"]
        app._tl.tone = app.translation_tone
        app._tl.disable_tone_emoji = True
        if provider:
            app.ai_provider._current_config["failover_policy"]["provider_order"] = [provider]
        return app.translate(case["source"], case["src_lang"], case["tgt_lang"]) or ""
    finally:
        casebook.active_corrections_snapshot = original_snapshot
        app._tl.__dict__.clear()
        app._tl.__dict__.update(saved)


def similarity(casebook, left: str, right: str) -> float:
    try:
        return float(casebook._sequence_similarity(left, right))
    except Exception:
        return 0.0


JUDGE_PROMPT = """You grade a factory chat translation. Reply with JSON only:
{"score": 1-5, "category": one of %s, "note": "<=15 words"}
score 5 = conveys exactly the same instruction/facts as the reference, natural;
3 = understandable but a fact, term or nuance is off; 1 = wrong or missing.
category = the main error type (use "other" when score is 5).
Source (%s): %s
Reference translation approved by the admin: %s
Candidate translation: %s"""


def judge(app, case, candidate: str, model: str) -> Dict[str, Any]:
    prompt = JUDGE_PROMPT % (list(ERROR_CATEGORIES), case["src_lang"], case["source"], case["corrected"], candidate)
    try:
        resp = app.ai_provider.chat_complete(
            model=model, messages=[{"role": "user", "content": prompt}],
            max_tokens=120, reasoning_effort="none", translation_fast_quality=True)
        text = resp.choices[0].message.content if hasattr(resp, "choices") else str(resp)
        text = str(text).strip().strip("`")
        if text.startswith("json"):
            text = text[4:]
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        score = max(1, min(5, int(data.get("score", 3))))
        category = str(data.get("category") or "other")
        return {"score": score, "category": category if category in ERROR_CATEGORIES else "other",
                "note": str(data.get("note") or "")[:120]}
    except Exception as exc:  # judge failure must not hide the translation result
        return {"score": None, "category": "unjudged", "note": f"judge error: {exc}"[:120]}


def run(args) -> Dict[str, Any]:
    app, al, casebook = _load_app()
    cases = load_cases(al, args.limit, args.group)
    if not cases:
        return {"version": HARNESS_VERSION, "cases": 0, "note": "no approved corrections found"}
    results = []
    for case in cases:
        entry = dict(case)
        for mode, exclude in (("baseline", True), ("learned", False)):
            if args.dry_run:
                out = case["original"] if mode == "baseline" else case["corrected"]
            else:
                out = translate_with_exclusion(app, casebook, case, exclude, args.provider)
            entry[mode] = {"output": out, "similarity": round(similarity(casebook, out, case["corrected"]), 3)}
            if not args.dry_run and not args.no_judge:
                entry[mode].update(judge(app, case, out, args.judge_model))
                time.sleep(args.pause)
        results.append(entry)

    def mean(values):
        values = [v for v in values if v is not None]
        return round(statistics.mean(values), 3) if values else None

    summary = {
        "version": HARNESS_VERSION, "cases": len(results), "provider": args.provider or "configured order",
        "similarity": {m: mean([r[m]["similarity"] for r in results]) for m in ("baseline", "learned")},
        "judge_score": {m: mean([r[m].get("score") for r in results]) for m in ("baseline", "learned")},
        "error_categories_baseline": {},
        "regressions": [r["id"] for r in results
                        if (r["learned"].get("score") or 0) < (r["baseline"].get("score") or 0)],
    }
    for r in results:
        cat = r["baseline"].get("category")
        if cat and cat not in ("unjudged",):
            summary["error_categories_baseline"][cat] = summary["error_categories_baseline"].get(cat, 0) + 1
    summary["gate"] = ("pass" if (summary["judge_score"]["learned"] or summary["similarity"]["learned"] or 0)
                       >= (summary["judge_score"]["baseline"] or summary["similarity"]["baseline"] or 0)
                       and not summary["regressions"] else "review")
    return {"summary": summary, "results": results}


def write_report(report: Dict[str, Any], out: str) -> None:
    with open(out + ".json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    summary = report.get("summary") or report
    lines = [f"# Learning loop evaluation ({HARNESS_VERSION})", "",
             f"- cases: {summary.get('cases')}  provider: {summary.get('provider')}",
             f"- similarity baseline → learned: {summary.get('similarity')}",
             f"- judge score baseline → learned: {summary.get('judge_score')}",
             f"- baseline error categories: {summary.get('error_categories_baseline')}",
             f"- regressions (learned worse than baseline): {summary.get('regressions')}",
             f"- gate: **{summary.get('gate')}**", ""]
    for r in report.get("results", [])[:50]:
        lines += [f"## {r['id']}  ({r['src_lang']}→{r['tgt_lang']})", f"- source: {r['source']}",
                  f"- approved: {r['corrected']}",
                  f"- baseline: {r['baseline']['output']}  [{r['baseline'].get('score')}/{r['baseline']['similarity']}] {r['baseline'].get('note','')}",
                  f"- learned: {r['learned']['output']}  [{r['learned'].get('score')}/{r['learned']['similarity']}] {r['learned'].get('note','')}", ""]
    with open(out + ".md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=["openai", "anthropic", "gemini"], default=None)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--group", default=None, help="restrict to one LINE group id")
    parser.add_argument("--judge-model", default="gpt-6-luna")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="no API calls; similarity of stored rows only")
    parser.add_argument("--pause", type=float, default=0.2)
    parser.add_argument("--out", default="learning_loop_report")
    args = parser.parse_args(argv)
    report = run(args)
    write_report(report, args.out)
    print(json.dumps(report.get("summary", report), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
