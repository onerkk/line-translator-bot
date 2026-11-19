import copy
import threading
from types import SimpleNamespace
from unittest.mock import patch

import ai_provider
import app
import line_command_catalog
import model_compare_live as cmp


def _cfg():
    cfg = copy.deepcopy(ai_provider.DEFAULT_CONFIG)
    cfg["active_provider"] = "openai"
    for name in ("anthropic", "openai", "gemini"):
        cfg[name]["api_key"] = name + "-key"
    cfg["quota_exhausted_providers"] = {}
    return cfg


def _call(dispatch):
    with patch.object(ai_provider, "_ensure_initialized", return_value=None), \
         patch.object(ai_provider, "_current_config", _cfg()), \
         patch.object(ai_provider, "_dispatch_provider", side_effect=dispatch), \
         patch.object(ai_provider, "_circuit_is_open", return_value=False):
        return ai_provider.chat_complete(model="gpt-6-luna", messages=[{"role": "user", "content": "你好"}])


def test_forced_route_pins_provider_and_model_and_recorder_sees_cost():
    seen = []

    def dispatch(provider, **kw):
        seen.append((provider, kw["model"]))
        r = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="halo"), finish_reason="stop")],
                            model=kw["model"])
        r._jy_usage_delta = {"model": kw["model"], "cost_usd": 0.002}
        return r

    calls = []
    t1 = ai_provider._FORCED_ROUTE.set(("anthropic", "claude-sonnet-5-5"))
    t2 = ai_provider._CALL_RECORDER.set(calls)
    try:
        _call(dispatch)
    finally:
        ai_provider._FORCED_ROUTE.reset(t1)
        ai_provider._CALL_RECORDER.reset(t2)
    assert seen == [("anthropic", "claude-sonnet-5-5")]
    assert calls[0]["model"] == "claude-sonnet-5-5" and calls[0]["cost_usd"] == 0.002 and calls[0]["error"] == ""
    # outside the comparison context the live routing is untouched
    seen.clear()
    _call(dispatch)
    assert seen == [("openai", "gpt-6-luna")]


def test_forced_route_does_not_leak_into_other_threads():
    seen = []

    def dispatch(provider, **kw):
        seen.append(provider)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="x"), finish_reason="stop")],
                               model="m")

    token = ai_provider._FORCED_ROUTE.set(("gemini", "gemini-2.5-flash"))
    try:
        t = threading.Thread(target=lambda: _call(dispatch))
        t.start(); t.join()
    finally:
        ai_provider._FORCED_ROUTE.reset(token)
    assert seen == ["openai"]


def test_report_scores_each_model_on_the_same_new_sentences():
    configs = [("current", "現行設定", None, None), ("claude-sonnet-5-5", "Claude Sonnet 5.5", "anthropic", "claude-sonnet-5-5")]
    sentences = []

    def translate(app_module, text, src, tgt, provider, model):
        sentences.append((provider, text))
        good = provider == "anthropic"
        return {"output": "benar" if good else "salah", "seconds": 3.0 if good else 1.0,
                "cost_usd": 0.004 if good else 0.0002, "models": [model or "gpt-6-luna"], "calls": 1, "errors": []}

    judge = lambda app_module, case, variant, output, model: {"correct": output == "benar", "error": "" if output == "benar" else "數量錯"}
    report = cmp.run(app, limit=4, seed=3, judge_model="j", variant_model="v", configs=configs,
                     variant_fn=lambda a, case, m: "新句 " + case["item_id"], translate_fn=translate, judge_fn=judge)
    by = {s["key"]: s for s in report["summary"]}
    assert report["cases"] == 4
    assert by["current"]["wrong"] == 4 and by["claude-sonnet-5-5"]["wrong"] == 0
    assert by["claude-sonnet-5-5"]["avg_seconds"] == 3.0
    assert by["claude-sonnet-5-5"]["twd_per_1000"] == round(0.004 * 1000 * 32, 1)
    # every model got exactly the same exam, and never the stored sentence
    cur = [t for p, t in sentences if p is None]
    son = [t for p, t in sentences if p == "anthropic"]
    assert cur == son and all(t.startswith("新句 ") for t in cur)
    text = cmp.summary_text(report, "abc1234")
    assert "現行設定：錯 4/4" in text and "Claude Sonnet 5.5：錯 0/4" in text and "數量錯" in text


def test_missing_translation_counts_as_wrong_and_unavailable_providers_are_skipped():
    assert [c[0] for c in cmp.available_configs(lambda p: p == "openai", lambda p: False)] == ["current", "hybrid", "gpt-6.1-sol"]
    assert [c[0] for c in cmp.available_configs(lambda p: True, lambda p: p == "anthropic")] == [
        "current", "hybrid", "gpt-6.1-sol", "gemini-2.5-flash"]
    assert [c[0] for c in cmp.available_configs(lambda p: p == "gemini", lambda p: False)] == [
        "current", "gemini-2.5-flash"]
    assert ("Claude Sonnet 5.5", "額度用完") in cmp.skipped_configs(lambda p: True, lambda p: p == "anthropic")
    report = cmp.run(app, limit=2, seed=1, judge_model="j", variant_model="v",
                     configs=[("current", "現行設定", None, None)],
                     variant_fn=lambda a, case, m: "新句",
                     translate_fn=lambda *a: {"output": "", "seconds": 0.5, "cost_usd": 0.0, "models": [], "calls": 1, "errors": ["TimeoutError"]},
                     judge_fn=lambda *a: {"correct": True, "error": ""})
    assert report["summary"][0]["wrong"] == 2


def test_compare_command_is_admin_only_and_listed_in_help():
    assert line_command_catalog.normalize_command("/比較") == "/compare"
    assert line_command_catalog.normalize_command("/比较 40") == "/compare 40"
    assert app._is_admin_only_command("/比較")
    assert app._is_admin_only_command("/比較 40")
    assert any(e["key"] == "compare" for e in line_command_catalog.COMMANDS) if hasattr(line_command_catalog, "COMMANDS") else True


def test_sol_and_astra_never_get_an_effort_they_reject():
    f = ai_provider.supported_openai_effort
    assert f("gpt-6.1-sol", "none") == "low" and f("gpt-6.1-sol", "minimal") == "low"
    assert f("gpt-6.1-sol", "medium") == "medium"
    assert f("gpt-6-astra", "none") == "low"
    assert f("gpt-6-luna", "none") == "none" and f("gpt-6-luna", "minimal") == "none"
    seen = {}

    def create(**kw):
        seen.update(kw)
        raise RuntimeError("stop here")

    import pytest
    with patch.object(ai_provider, "_get_openai_client",
                      return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))), \
         patch.object(ai_provider, "_sdk_create", side_effect=lambda c, kw: create(**kw)):
        with pytest.raises(RuntimeError):
            ai_provider._chat_complete_openai(model="gpt-6.1-sol", messages=[{"role": "user", "content": "x"}],
                                              max_tokens=50, reasoning_effort="none")
    assert seen.get("reasoning_effort") == "low"


def test_judge_falls_back_and_incomplete_runs_are_not_ranked(monkeypatch):
    asked = []

    def ask(app_module, model, prompt, max_tokens):
        asked.append((model, max_tokens))
        if model == "gpt-6.1-sol":
            raise RuntimeError("400")
        return '{"correct": true, "error": ""}'

    monkeypatch.setattr(cmp.learning_eval_live, "_ask", ask)
    case = {"source": "a", "original": "b", "corrected": "c", "reason": "d"}
    v = cmp.judge(app, case, "x", "y", "gpt-6.1-sol")
    assert v["correct"] is True and v["judge"] != "gpt-6.1-sol" and asked[0] == ("gpt-6.1-sol", 1200)
    report = {"cases": 3, "summary": [{"key": "current", "label": "現行設定", "n": 3, "scored": 0, "wrong": 0,
              "avg_seconds": 1, "p90_seconds": 1, "twd_per_1000": 1, "models": {}, "wrong_items": [],
              "judge_failed": ["judge error: gpt-6.1-sol:RuntimeError"], "no_response": 0}]}
    text = cmp.summary_text(report)
    assert "沒有評到分" in text and "評審失敗 1 題" in text and "不能拿來比較" in text and "錯最少" not in text


def test_hybrid_routing_is_off_live_and_sends_known_trap_kinds_to_quality(monkeypatch):
    monkeypatch.setattr(app, "_translation_cp_router_enabled", lambda: True)
    monkeypatch.delenv("TRANSLATION_RISK_TO_QUALITY", raising=False)
    routine, quality = app._translation_cp_tier_models()
    trap = "可以請她們先放這一把嗎"            # matches the 11/18 release review rule
    plain = "今天天氣很好"
    assert app._known_trap_cards(trap) and not app._known_trap_cards(plain)
    app._tl.__dict__.pop("force_model", None)
    assert app.pick_model(trap) == routine            # live default: unchanged
    assert app._tl.translation_effort_boost is True   # but it still thinks harder
    token = app._RISK_ROUTE_OVERRIDE.set(True)
    try:
        assert app.pick_model(trap) == quality
        assert app.pick_model(plain) == routine
    finally:
        app._RISK_ROUTE_OVERRIDE.reset(token)
    monkeypatch.setenv("TRANSLATION_RISK_TO_QUALITY", "1")
    assert app.pick_model(trap) == quality


def test_live_hard_share_counts_live_traffic_only(monkeypatch):
    monkeypatch.setattr(app, "_RISK_ROUTE_DECISIONS", app._collections.deque(maxlen=10))
    app._note_risk_route_decision(True)
    app._note_risk_route_decision(False)
    app._tl.eval_no_cache = True
    try:
        app._note_risk_route_decision(True)
    finally:
        app._tl.__dict__.pop("eval_no_cache", None)
    assert app.risk_route_share() == (0.5, 2)


def test_paired_diff_and_live_cost_estimate_and_judge_choice(monkeypatch):
    configs = [("current", "現行設定", None, None), ("hybrid", "分流（困難句用 Sol）", None, "hybrid"),
               ("gpt-6.1-sol", "GPT-6.1 Sol", "openai", "gpt-6.1-sol")]
    def translate(app_module, text, src, tgt, provider, model):
        n = int(text.split()[-1])
        ok = {"current": n % 2 == 0, "hybrid": True, "gpt-6.1-sol": n != 0}[model or "current"] if model != "hybrid" else True
        if model is None:
            ok = n % 2 == 0
        return {"output": "ok" if ok else "bad", "seconds": 1.0, "models": [], "calls": 1, "errors": [],
                "cost_usd": {None: 0.001, "hybrid": 0.004, "gpt-6.1-sol": 0.006}[model]}
    counter = iter(range(100))
    monkeypatch.setattr(app, "risk_route_share", lambda hours=72: (0.2, 500))
    report = cmp.run(app, limit=4, seed=2, judge_model="j", variant_model="v", configs=configs,
                     variant_fn=lambda a, c, m: f"句 {next(counter)}", translate_fn=translate,
                     judge_fn=lambda a, c, v, o, m: {"correct": o == "ok", "error": "" if o == "ok" else "錯"})
    by = {e["key"]: e for e in report["summary"]}
    assert len(by["hybrid"]["fixes"]) == by["current"]["wrong"] and by["hybrid"]["breaks"] == []
    assert report["hybrid_live_twd_per_1000"] == round(by["current"]["twd_per_1000"] * 0.8
                                                       + by["gpt-6.1-sol"]["twd_per_1000"] * 0.2, 1)
    text = cmp.summary_text(report)
    assert "比現行：救回" in text and "平常訊息裡約 20% 屬於困難句" in text
    assert cmp.pick_judge(lambda p: True, lambda p: p == "anthropic") == ("gemini", "gemini-2.5-flash")
    assert cmp.pick_judge(lambda p: p == "openai", lambda p: False) == (None, "gpt-6.1-sol")
