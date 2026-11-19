"""In-bot model comparison (/比較): which model translates our hard sentences best,
how fast, and at what cost?

The exam uses the reviewed cases (translation_reviews/*.json): every one is a
sentence the bot once got wrong, with the reviewer's corrected meaning.  To keep
memory from answering, each case is first rewritten into a NEW sentence of the
same kind (same tricky point, other numbers / names / wording).  Every model
then translates the same new sentences through the real pipeline, with all
learned knowledge available (like live traffic) but no cache or memory lookups.

Each call in a translation is pinned to the model under test and recorded, so
cost and seconds are what that model actually spent.  One fixed judge, blind
to which model wrote the output, decides whether the translation would mislead
a worker.  Nothing is written to any learning or memory store and no LINE
message is sent from here.
"""
from __future__ import annotations

import json
import statistics
import time
from typing import Any, Callable, Dict, List, Optional

import ai_provider
import learning_eval_live

COMPARE_VERSION = "2026-11-19.2-hybrid"
USD_TO_TWD = 32.0

# (key, label, provider, model).  provider None = the live routing as configured.
CONFIGS = (
    ("current", "現行設定", None, None),
    # Live routing, but sentences that are risky or match an earlier correction
    # go to the quality tier (app._risk_route_to_quality).
    ("hybrid", "分流（困難句用 Sol）", None, "hybrid"),
    ("gpt-6.1-sol", "GPT-6.1 Sol", "openai", "gpt-6.1-sol"),
    ("claude-haiku-5-5", "Claude Haiku 5.5", "anthropic", "claude-haiku-5-5"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5", "anthropic", "claude-sonnet-5-5"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash", "gemini", "gemini-2.5-flash"),
)

JUDGE_PROMPT = """You check machine translations for a Taiwan stainless-steel factory LINE group
(Traditional Chinese <-> Indonesian). Workers act on these translations.

Background - a reviewer once found this kind of mistake in a similar message:
Earlier message: %s
Wrong translation: %s
Correct translation: %s
Reviewer's reason: %s

Now judge THIS translation of a DIFFERENT message:
Message: %s
Translation: %s

Is the translation correct enough that a worker would understand and act correctly?
Mark it wrong if it gets wrong (or drops) the action, the object, a number or unit,
who / which station, negation, a request vs a statement, time, or the kind of trap
described above. Ignore style and word choice that keeps the meaning.
Reply with JSON only: {"correct": true or false, "error": "<if wrong: what is wrong, in Traditional Chinese, under 20 characters>"}"""


def available_configs(has_key: Optional[Callable[[str], bool]] = None,
                      blocked: Optional[Callable[[str], bool]] = None) -> List[tuple]:
    return [c for c, _why in _config_status(has_key, blocked) if not _why]


def skipped_configs(has_key=None, blocked=None) -> List[tuple]:
    return [(c[1], why) for c, why in _config_status(has_key, blocked) if why]


def _config_status(has_key=None, blocked=None):
    has_key = has_key or ai_provider._provider_has_key
    blocked = blocked or ai_provider._provider_quota_blocked
    out = []
    for cfg in CONFIGS:
        provider = "openai" if cfg[3] == "hybrid" else cfg[2]
        why = ""
        if provider is not None and not has_key(provider):
            why = "沒有設定金鑰"
        elif provider is not None and blocked(provider):
            why = "額度用完"
        out.append((cfg, why))
    return out


def translate_with(app, text: str, src: str, tgt: str, provider: Optional[str], model: Optional[str]) -> Dict[str, Any]:
    calls: List[Dict[str, Any]] = []
    rec_token = ai_provider._CALL_RECORDER.set(calls)
    route_token = ai_provider._FORCED_ROUTE.set((provider, model) if provider else None)
    hybrid_var = getattr(app, "_RISK_ROUTE_OVERRIDE", None)
    hybrid_token = hybrid_var.set(model == "hybrid") if hybrid_var is not None and not provider else None
    started = time.monotonic()
    try:
        output = learning_eval_live.translate_text(app, text, src, tgt, hide_learning=False)
    finally:
        if hybrid_token is not None:
            hybrid_var.reset(hybrid_token)
        ai_provider._FORCED_ROUTE.reset(route_token)
        ai_provider._CALL_RECORDER.reset(rec_token)
    errors = [c["error"] for c in calls if c.get("error")]
    if provider and not any(not c.get("error") for c in calls):
        # The model under test never answered; whatever came back is a
        # fallback (e.g. public NMT) and must not be scored as its work.
        output = "[error: " + (errors[0] if errors else "no call to the model") + "]"
    return {"output": output, "seconds": time.monotonic() - started,
            "cost_usd": sum(c.get("cost_usd", 0.0) for c in calls),
            "models": sorted({c["model"] for c in calls if c.get("model") and not c.get("error")}),
            "calls": len(calls), "errors": errors}


# A judge from another company than the models under test avoids self-preference.
JUDGE_CANDIDATES = (("anthropic", "claude-sonnet-5-5"), ("gemini", "gemini-2.5-flash"), (None, "gpt-6.1-sol"))


def pick_judge(has_key=None, blocked=None) -> tuple:
    has_key = has_key or ai_provider._provider_has_key
    blocked = blocked or ai_provider._provider_quota_blocked
    for provider, model in JUDGE_CANDIDATES:
        if provider is None or (has_key(provider) and not blocked(provider)):
            return provider, model
    return None, ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL


def _judge_once(app, model: str, prompt: str, provider: Optional[str] = None) -> Dict[str, Any]:
    # Reasoning models spend part of max_completion_tokens thinking before the
    # JSON appears; a tight cap returns an empty answer.
    token = ai_provider._FORCED_ROUTE.set((provider, model) if provider else None)
    try:
        text = learning_eval_live._ask(app, model, prompt, 1200)
    finally:
        ai_provider._FORCED_ROUTE.reset(token)
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start:end + 1])
    return {"correct": bool(data.get("correct")), "error": str(data.get("error") or "")[:40], "judge": model}


def judge(app, case: Dict[str, Any], variant: str, output: str, model: str,
          provider: Optional[str] = None) -> Dict[str, Any]:
    prompt = JUDGE_PROMPT % (case["source"], case["original"] or "(not recorded)", case["corrected"],
                             case["reason"] or "(none)", variant, output)
    failures = []
    for cand_provider, candidate in ((provider, model), (None, app.pick_model("評分"))):
        try:
            return _judge_once(app, candidate, prompt, cand_provider)
        except Exception as exc:
            failures.append(f"{candidate}:{type(exc).__name__}")
    return {"correct": None, "error": "judge error: " + ", ".join(failures)}


def run(app, limit: int = 20, seed: Optional[int] = None, judge_model: Optional[str] = None,
        variant_model: Optional[str] = None, configs: Optional[List[tuple]] = None,
        variant_fn=None, translate_fn=None, judge_fn=None) -> Dict[str, Any]:
    variant_fn = variant_fn or learning_eval_live.make_variant
    translate_fn = translate_fn or translate_with
    judge_fn = judge_fn or judge
    skipped_models = [] if configs else skipped_configs()
    configs = list(configs or available_configs())
    judge_provider = None
    if not judge_model:
        judge_provider, judge_model = pick_judge()
    variant_model = variant_model or app.pick_model("評分")
    cases, skipped = [], 0
    for case in learning_eval_live.load_cases(limit, seed):
        variant = variant_fn(app, case, variant_model)
        if variant:
            cases.append((case, variant))
        else:
            skipped += 1
    per: Dict[str, Dict[str, Any]] = {key: {"label": label, "rows": []} for key, label, _p, _m in configs}
    for case, variant in cases:
        for key, _label, provider, model in configs:
            res = translate_fn(app, variant, case["src_lang"], case["tgt_lang"], provider, model)
            out = res.get("output") or ""
            if not out or out.startswith("[error"):
                reason = (res.get("errors") or [""])[0]
                verdict = {"correct": False, "error": ("模型沒有回應" + (f"（{reason}）" if reason else ""))
                           if out.startswith("[error") else "沒有譯文"}
            else:
                verdict = (judge_fn(app, case, variant, out, judge_model, judge_provider)
                           if judge_fn is judge else judge_fn(app, case, variant, out, judge_model))
            per[key]["rows"].append({"id": case["item_id"], "variant": variant, **res, **verdict})
    summary = []
    for key, label, provider, model in configs:
        rows = per[key]["rows"]
        scored = [r for r in rows if r.get("correct") is not None]
        secs = [r["seconds"] for r in rows]
        cost = sum(r.get("cost_usd", 0.0) for r in rows)
        models: Dict[str, int] = {}
        for r in rows:
            for m in r.get("models", []):
                models[m] = models.get(m, 0) + 1
        summary.append({
            "key": key, "label": label, "n": len(rows), "scored": len(scored),
            "wrong": sum(1 for r in scored if not r["correct"]),
            "avg_seconds": round(statistics.mean(secs), 2) if secs else 0.0,
            "p90_seconds": round(sorted(secs)[max(0, int(len(secs) * 0.9) - 1)], 2) if secs else 0.0,
            "cost_usd": cost,
            "twd_per_1000": round(cost / len(rows) * 1000 * USD_TO_TWD, 1) if rows else 0.0,
            "models": models,
            "wrong_items": [(r["id"], r.get("error", "")) for r in scored if not r["correct"]],
            "judge_failed": [r.get("error", "") for r in rows if r.get("correct") is None],
            "no_response": sum(1 for r in scored if str(r.get("error", "")).startswith("模型沒有回應")),
        })
    # Paired view against the live setup: which sentences each option fixes or breaks.
    base = {r["id"]: r.get("correct") for r in per.get("current", {}).get("rows", [])}
    for entry in summary:
        if entry["key"] == "current" or not base:
            continue
        rows = {r["id"]: r.get("correct") for r in per[entry["key"]]["rows"]}
        entry["fixes"] = [i for i, ok in rows.items() if ok is True and base.get(i) is False]
        entry["breaks"] = [i for i, ok in rows.items() if ok is False and base.get(i) is True]
    share, sample = (None, 0)
    if hasattr(app, "risk_route_share"):
        try:
            share, sample = app.risk_route_share()
        except Exception:
            share, sample = None, 0
    by_key = {e["key"]: e for e in summary}
    estimate = None
    if share is not None and "current" in by_key and "gpt-6.1-sol" in by_key:
        estimate = round(by_key["current"]["twd_per_1000"] * (1 - share)
                         + by_key["gpt-6.1-sol"]["twd_per_1000"] * share, 1)
    return {"version": COMPARE_VERSION, "ts": int(time.time()), "cases": len(cases), "skipped": skipped,
            "judge_model": judge_model, "judge_provider": judge_provider, "summary": summary, "rows": per,
            "skipped_models": skipped_models, "live_hard_share": share, "live_sample": sample,
            "hybrid_live_twd_per_1000": estimate}


def summary_text(report: Dict[str, Any], version: str = "") -> str:
    n = report.get("cases", 0)
    if not n or not report.get("summary"):
        return "🧪 模型比較：沒有可用的題目（AI 呼叫失敗或沒有審核資料）。"
    lines = [f"🧪 模型比較（{n} 題，版本 {version or '?'}）",
             "考法：把以前翻錯過的句子改寫成同類新句子，每個模型翻同一批，由同一個評審判斷會不會讓人誤會。",
             ""]
    marks = "①②③④⑤⑥⑦⑧"
    for i, s in enumerate(report["summary"]):
        rate = f"{s['wrong']}/{s['scored']}" if s["scored"] else "—（沒有評到分）"
        lines.append(f"{marks[i] if i < len(marks) else '•'} {s['label']}：錯 {rate}｜平均 {s['avg_seconds']} 秒"
                     f"（慢的 {s['p90_seconds']} 秒）｜每千句約 NT${s['twd_per_1000']}")
        if s["key"] == "current" and s["models"]:
            lines.append("　實際用到：" + "、".join(f"{m}×{c}" for m, c in
                                                sorted(s["models"].items(), key=lambda kv: -kv[1])[:3]))
        if s.get("judge_failed"):
            lines.append(f"　⚠ 評審失敗 {len(s['judge_failed'])} 題：{s['judge_failed'][0][:60]}")
        if s.get("no_response"):
            lines.append(f"　⚠ 模型沒回應 {s['no_response']} 題（算錯）")
        for item_id, err in [w for w in s["wrong_items"] if not str(w[1]).startswith("模型沒有回應")][:3]:
            lines.append(f"　✗ {item_id}：{err}")
        if s.get("fixes") or s.get("breaks"):
            lines.append(f"　比現行：救回 {len(s.get('fixes', []))} 題、反而錯 {len(s.get('breaks', []))} 題")
            if s.get("fixes"):
                lines.append("　救回：" + "、".join(s["fixes"][:5]))
            if s.get("breaks"):
                lines.append("　反而錯：" + "、".join(s["breaks"][:5]))
    complete = all(s["n"] and s["scored"] >= 0.8 * s["n"] for s in report["summary"])
    best = min(s["wrong"] for s in report["summary"] if s["scored"]) if any(s["scored"] for s in report["summary"]) else None
    if not complete:
        lines += ["", "⚠ 有模型評分不到八成，這次結果不能拿來比較，請修好上面的問題後再跑一次。"]
    elif best is not None:
        close = [s for s in report["summary"] if s["scored"] and s["wrong"] <= best + 1]
        live_cost = lambda s: (report["hybrid_live_twd_per_1000"]
                               if s["key"] == "hybrid" and report.get("hybrid_live_twd_per_1000") is not None
                               else s["twd_per_1000"])
        cheapest = min(close, key=live_cost)
        lines += ["", f"錯最少：{best} 題。錯得差不多（最多多錯 1 題）裡最便宜的是 {cheapest['label']}。",
                  "題數少時 1～2 題的差距可能是運氣，要確定可以再跑一次（會抽到別的題目）。"]
    if report.get("live_hard_share") is not None:
        line = (f"平常訊息裡約 {round(report['live_hard_share'] * 100)}% 屬於困難句"
                f"（最近 {report['live_sample']} 句）")
        if report.get("hybrid_live_twd_per_1000") is not None:
            line += f"；分流模式用在平常訊息，估計每千句約 NT${report['hybrid_live_twd_per_1000']}"
        lines.append(line)
        lines.append("（考卷全是難句，所以上面「分流」那行的費用會偏高；平常訊息的費用看這一行。）")
    if report.get("skipped_models"):
        lines.append("沒測：" + "、".join(f"{label}（{why}）" for label, why in report["skipped_models"]))
    judge_note = "（與受測模型同家，可能偏心）" if not report.get("judge_provider") else "（不同家，較公正）"
    lines.append(f"評審模型：{report.get('judge_model', '?')}{judge_note}；跳過 {report.get('skipped', 0)} 題（改寫失敗）")
    return "\n".join(lines)
