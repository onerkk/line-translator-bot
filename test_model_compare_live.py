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
    assert [c[0] for c in cmp.available_configs(lambda p: p == "openai", lambda p: False)] == ["current", "gpt-6.1-sol"]
    assert [c[0] for c in cmp.available_configs(lambda p: True, lambda p: p == "anthropic")] == [
        "current", "gpt-6.1-sol", "gemini-2.5-flash"]
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
