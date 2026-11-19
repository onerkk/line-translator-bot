"""In-bot learning evaluation: does teaching fix the whole KIND of sentence?

History (2026-11-11): the first design hid each case's own rule and asked
whether the other rules helped ("leave one out"). Real runs showed why that is
the wrong exam: factory knowledge such as OKGO lives in exactly one rule, so
with that rule hidden both modes received byte-identical prompts and the
"better/worse" counts were model noise.

What the owner actually asks is: "I corrected one sentence – will a different
sentence of the same kind still go wrong?"  So for each reviewed case:

  1. a cheap model writes a NEW message of the same kind (other IDs, numbers,
     names and surrounding words, same tricky point);
  2. the real pipeline translates it twice in this thread only:
       untrained – every learned correction and review rule hidden
       trained   – everything learned available (no cache / memory lookups, and
                   the new sentence is not the stored one, so nothing can be
                   answered by recall)
  3. a judge is shown the documented mistake (wrong vs corrected example and
     the reviewer's reason) and answers one question for each output:
     does this translation repeat that mistake?

The headline number is the repeat-mistake rate, untrained vs trained.
Thread-local switches keep live traffic on full learning during a run; nothing
is written to any learning or memory store and no LINE message is sent here.
"""
from __future__ import annotations

import json
import random
import time
from typing import Any, Dict, List, Optional

import learning_loop_eval as harness
import translation_reviews
import conversation_context

EVAL_VERSION = "2026-11-19.3-original-turns"
MODES = (("untrained", True), ("trained", False))
_LANG = {"zh": "Traditional Chinese (Taiwan factory chat)", "id": "Indonesian (factory chat)"}

VARIANT_PROMPT = """You write test messages for a Taiwan stainless-steel factory LINE group.
Below is a real message and the translation mistake a reviewer found in it.
Write ONE new message in the SAME language that a worker could plausibly send and that
contains the SAME tricky point, so a translator could make the same mistake again.
Change the other details: different IDs / numbers / times / names / station codes and
different surrounding wording. Keep it about as long as the original. No @mentions.
Return JSON {"message":"the new current message","previous":"a short prior original when
needed to interpret the current message, otherwise an empty string"}. Use consistent
identifiers across turns. The previous turn is ordinary shop-floor dialogue in the
source language. Omit explanations, the target translation and reviewer instructions.

Original message (%s): %s
Reviewer's note on the mistake: %s"""

REPEAT_PROMPT = """A reviewer documented this translation mistake in a factory chat:
Original message: %s
Wrong translation: %s
Corrected translation: %s
Reviewer's reason: %s

Now a DIFFERENT message of the same kind was translated:
New message: %s
Its translation: %s

Does the new translation repeat the SAME KIND of mistake the reviewer described?
Ignore unrelated wording differences. Reply with JSON only:
{"repeats": true or false, "note": "<10 words"}"""


def load_cases(limit: int, seed: Optional[int] = None) -> List[Dict[str, Any]]:
    cases = []
    for bundle in translation_reviews.load_bundles():
        for item in bundle.get("items", []):
            cases.append({"id": f"{bundle['review_id']}_{item['id']}", "item_id": item["id"],
                          "source": item["source"], "corrected": item["corrected"],
                          "original": item.get("observed", ""), "reason": item.get("reason", ""),
                          "src_lang": item["src"], "tgt_lang": item["tgt"],
                          "category": item.get("category", "other")})
    rng = random.Random(seed if seed is not None else int(time.time()))
    rng.shuffle(cases)
    # This is an exam of reviewed mistakes, not a traffic-weighted sample.
    # Alternate directions while both have cases so a small exam covers each.
    buckets = [[c for c in cases if c["src_lang"] == lang] for lang in ("zh", "id")]
    mixed = [bucket[i] for i in range(max(map(len, buckets))) for bucket in buckets if i < len(bucket)]
    return mixed[:max(1, int(limit))]


def _ask(app, model: str, prompt: str, max_tokens: int) -> str:
    resp = app.ai_provider.chat_complete(
        model=model, messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens, reasoning_effort="none", translation_fast_quality=True)
    return harness._response_text(resp).strip()


def make_variant(app, case: Dict[str, Any], model: str) -> str:
    try:
        text = _ask(app, model, VARIANT_PROMPT % (_LANG.get(case["src_lang"], case["src_lang"]),
                                                 case["source"], case["reason"] or "(none)"), 400)
    except Exception:
        return ""
    text = text.strip()
    first, last = text.find("{"), text.rfind("}")
    if first >= 0:
        try:
            pair = json.loads(text[first:last + 1])
        except ValueError:
            return ""
        if not isinstance(pair, dict) or not isinstance(pair.get("message"), str):
            return ""
        previous = pair.get("previous", "")
        if not isinstance(previous, str) or len(previous) > conversation_context.MAX_SOURCE:
            return ""
        text = pair["message"]
        case["exam_previous"] = previous.strip()
    text = text.strip().strip('"').strip("「」").strip()
    if not text or text == case["source"].strip() or len(text) > max(400, 3 * len(case["source"])):
        return ""
    return text


def exam_snapshot(case, message):
    """Build a replay from this exam's originals; never open a production journal."""
    previous = case.get("exam_previous")
    if not previous:
        return None
    snap = conversation_context.empty("", message)
    snap.update(author="B", timestamp=1, media_evidence=[])
    row = dict(message_id="exam-prior", author="A", recipients=[], text=previous,
               ts=0, lang=case["src_lang"])
    return conversation_context.select(snap, [row], message, quoted_id=row["message_id"])


def judge_input(case, message):
    if case.get("exam_previous"):
        return json.dumps({"previous_original": case["exam_previous"], "current_message": message},
                          ensure_ascii=False)
    return message


def translate_text(app, text: str, src: str, tgt: str, hide_learning: bool) -> str:
    saved = dict(app._tl.__dict__)
    try:
        app._tl.__dict__.clear()
        app._tl.group_id = ""
        app._tl.tone = getattr(app, "translation_tone", "factory")
        app._tl.disable_tone_emoji = True
        app._tl.eval_no_cache = True
        if hide_learning:
            app._tl.eval_hide_source = "*"
            app._tl.eval_hide_cards = "all"
        return app.translate(text, src, tgt) or ""
    except Exception as exc:
        return f"[error: {type(exc).__name__}]"
    finally:
        app._tl.__dict__.clear()
        app._tl.__dict__.update(saved)


def judge_repeat(app, case: Dict[str, Any], variant: str, output: str, model: str) -> Dict[str, Any]:
    try:
        text = _ask(app, model, REPEAT_PROMPT % (case["source"], case["original"] or "(not recorded)",
                                                 case["corrected"], case["reason"] or "(none)",
                                                 judge_input(case, variant), output), 120)
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1])
        if not isinstance(data.get("repeats"), bool):
            raise ValueError("judge did not return a boolean verdict")
        return {"repeats": data["repeats"], "note": str(data.get("note") or "")[:80]}
    except Exception as exc:
        return {"repeats": None, "note": f"judge error: {type(exc).__name__}"}


def run(app, limit: int = 10, seed: Optional[int] = None, model: Optional[str] = None,
        variant_fn=None, judge_fn=None) -> Dict[str, Any]:
    variant_fn = variant_fn or make_variant
    judge_fn = judge_fn or judge_repeat
    model = model or app.pick_model("評分")
    results, skipped = [], 0
    for case in load_cases(limit, seed):
        variant = variant_fn(app, case, model)
        if not variant:
            skipped += 1
            continue
        entry = {"id": case["id"], "item_id": case["item_id"], "variant": variant,
                 "previous": case.get("exam_previous", "")}
        for mode, hide in MODES:
            with conversation_context.scope(exam_snapshot(case, variant)):
                output = translate_text(app, variant, case["src_lang"], case["tgt_lang"], hide)
            if not output or output.startswith("[error"):
                entry[mode] = {"output": output, "repeats": None, "note": "no translation"}
            elif mode == "trained" and entry["untrained"]["output"] == output and entry["untrained"]["repeats"] is not None:
                entry[mode] = dict(entry["untrained"])
            else:
                entry[mode] = dict(judge_fn(app, case, variant, output, model), output=output)
        results.append(entry)
    scored = [r for r in results if r["untrained"]["repeats"] is not None and r["trained"]["repeats"] is not None]
    fixed = [r["item_id"] for r in scored if r["untrained"]["repeats"] and not r["trained"]["repeats"]]
    still = [r["item_id"] for r in scored if r["untrained"]["repeats"] and r["trained"]["repeats"]]
    broke = [r["item_id"] for r in scored if not r["untrained"]["repeats"] and r["trained"]["repeats"]]
    never = [r["item_id"] for r in scored if not r["untrained"]["repeats"] and not r["trained"]["repeats"]]
    return {"version": EVAL_VERSION, "ts": int(time.time()), "cases": len(results), "scored": len(scored),
            "skipped": skipped, "fixed": fixed, "still_wrong": still, "newly_wrong": broke, "never_wrong": never,
            "untrained_mistakes": len(fixed) + len(still), "trained_mistakes": len(still) + len(broke),
            "results": results}


def summary_text(report: Dict[str, Any], version: str = "") -> str:
    n = report.get("scored", 0)
    if not n:
        return "📊 學習評測：沒有可評分的案例（AI 呼叫失敗或沒有審核資料）。"
    before, after = report["untrained_mistakes"], report["trained_mistakes"]
    if before == 0 and after == 0:
        verdict = "這批句子 AI 本來就不會錯，看不出學習的差別（再跑一次會抽到別的句子）"
    elif after < before:
        verdict = "有學到：教過的錯，換一句同類的話也比較不會再犯"
    elif after == before:
        verdict = "沒有改善：教過的錯，換一句同類的話還是照犯" if before else "差不多"
    else:
        verdict = "變差了：學到的東西反而讓新句子出錯，需要檢查"
    lines = [f"📊 學習評測（{n} 題，版本 {version or '?'}）",
             "考法：每題把一句教過的話改寫成「同類的新句子」再翻，看會不會犯同樣的錯",
             f"沒學過：{before} 題犯同樣的錯",
             f"學過後：{after} 題犯同樣的錯",
             f"修好 {len(report['fixed'])}｜仍然錯 {len(report['still_wrong'])}｜本來就對 {len(report['never_wrong'])}｜反而變錯 {len(report['newly_wrong'])}",
             f"結論：{verdict}"]
    if report["still_wrong"]:
        lines.append("仍然錯：" + "、".join(report["still_wrong"][:4]))
    if report["newly_wrong"]:
        lines.append("反而變錯：" + "、".join(report["newly_wrong"][:4]))
    return "\n".join(lines)
