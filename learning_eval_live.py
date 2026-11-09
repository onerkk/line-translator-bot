"""In-bot learning evaluation: is the system actually getting smarter?

For a sample of reviewed cases (translation_reviews bundles) the real pipeline
translates each source twice, in this thread only (thread-local switches, so
live traffic keeps its full learning while an evaluation runs):

  untrained    – every learned correction and every review rule card hidden:
                 what the bot produced before anyone taught it anything
  generalised  – only THIS case's own correction and own rule hidden: what it
                 can do for a sentence it has never been corrected on, using
                 everything learned from the other cases

A judge call scores each output 1–5 against the admin-approved reference.
"generalised > untrained" is the evidence that teaching transfers to new
sentences. Glossary terms added by reviews stay active in both modes, so the
reported gain is a lower bound. Nothing is written to the learning stores and
no LINE message is sent from here.
"""
from __future__ import annotations

import random
import statistics
import time
from typing import Any, Dict, List, Optional

import learning_loop_eval as harness
import translation_reviews

EVAL_VERSION = "2026-11-10.1"
MODES = (("untrained", "all"), ("generalised", "own"))


def load_cases(limit: int, seed: Optional[int] = None) -> List[Dict[str, Any]]:
    cases = []
    for bundle in translation_reviews.load_bundles():
        for item in bundle.get("items", []):
            cases.append({"id": f"{bundle['review_id']}_{item['id']}", "item_id": item["id"],
                          "source": item["source"], "corrected": item["corrected"],
                          "original": item.get("observed", ""),
                          "src_lang": item["src"], "tgt_lang": item["tgt"],
                          "category": item.get("category", "other")})
    random.Random(seed if seed is not None else int(time.time())).shuffle(cases)
    return cases[: max(1, int(limit))]


def translate_in_mode(app, case: Dict[str, Any], hide: str) -> str:
    saved = dict(app._tl.__dict__)
    try:
        app._tl.__dict__.clear()
        app._tl.group_id = ""
        app._tl.tone = getattr(app, "translation_tone", "factory")
        app._tl.disable_tone_emoji = True
        app._tl.eval_no_cache = True
        app._tl.eval_hide_source = case["source"] if hide == "own" else "*"
        app._tl.eval_hide_cards = case["item_id"] if hide == "own" else "all"
        return app.translate(case["source"], case["src_lang"], case["tgt_lang"]) or ""
    except Exception as exc:
        return f"[error: {type(exc).__name__}]"
    finally:
        app._tl.__dict__.clear()
        app._tl.__dict__.update(saved)


def run(app, limit: int = 10, seed: Optional[int] = None, judge_model: Optional[str] = None,
        judge=None) -> Dict[str, Any]:
    judge = judge or harness.judge
    # The cheapest configured translation model grades; short text keeps it on the routine tier.
    model = judge_model or app.pick_model("評分")
    results = []
    for case in load_cases(limit, seed):
        entry = {"id": case["id"], "category": case["category"]}
        for mode, hide in MODES:
            output = translate_in_mode(app, case, hide)
            verdict = judge(app, case, output, model) if output and not output.startswith("[error") else {
                "score": None, "category": "unjudged", "note": output[:60]}
            entry[mode] = {"output": output, "score": verdict.get("score"), "note": verdict.get("note", "")}
        results.append(entry)

    def mean(mode):
        values = [r[mode]["score"] for r in results if r[mode]["score"] is not None]
        return round(statistics.mean(values), 2) if values else None

    scored = [r for r in results if r["untrained"]["score"] is not None and r["generalised"]["score"] is not None]
    better = [r["id"] for r in scored if r["generalised"]["score"] > r["untrained"]["score"]]
    worse = [r["id"] for r in scored if r["generalised"]["score"] < r["untrained"]["score"]]
    return {"version": EVAL_VERSION, "ts": int(time.time()), "cases": len(results), "scored": len(scored),
            "untrained": mean("untrained"), "generalised": mean("generalised"),
            "better": better, "worse": worse, "same": len(scored) - len(better) - len(worse),
            "results": results}


def summary_text(report: Dict[str, Any], version: str = "") -> str:
    if not report.get("scored"):
        return "📊 學習評測：沒有可評分的案例（AI 呼叫失敗或沒有審核資料）。"
    a, b = report["untrained"], report["generalised"]
    delta = round(b - a, 2)
    verdict = ("有變聰明：沒教過的句子也翻得比較好" if delta > 0.15 else
               "退步了，需要檢查規則" if delta < -0.15 else "差不多：教過的東西還沒明顯幫到新句子")
    lines = [f"📊 學習評測（{report['scored']} 句，版本 {version or '?'}）",
             f"完全沒學過：{a} 分",
             f"用學到的東西翻「沒教過的句子」：{b} 分（{'+' if delta >= 0 else ''}{delta}）",
             f"進步 {len(report['better'])} 句｜一樣 {report['same']} 句｜退步 {len(report['worse'])} 句",
             f"結論：{verdict}",
             "（滿分 5 分；每句都把它自己的修正藏起來再考，所以考的是舉一反三，不是背答案）"]
    if report["worse"]:
        lines.append("退步的案例：" + "、".join(x.split("_", 1)[-1][:28] for x in report["worse"][:3]))
    return "\n".join(lines)
