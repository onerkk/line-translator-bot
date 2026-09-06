"""Per-response billing estimates remain isolated when groups overlap."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
import threading

import pytest
import app
import ai_provider


@pytest.fixture(autouse=True)
def stats(monkeypatch):
    monkeypatch.setattr(app, "bot_stats", {})
    monkeypatch.setattr(app, "group_api_usage", {})
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(previous)


def reply(provider, model, inputs, outputs, cached=0):
    return NS(_jy_provider=provider, model=model, usage=NS(prompt_tokens=inputs,
        completion_tokens=outputs, prompt_tokens_details=NS(cached_tokens=cached)))


def test_concurrent_groups_are_charged_only_for_their_own_responses():
    ready = threading.Barrier(2)
    def run(group, resp):
        app._tl.group_id = group
        ready.wait(timeout=3)
        app.track_tokens(resp)
        app.track_tokens(resp)  # Legacy caller following the provider observer.
        app.track_group_usage(group, 0, 0, 0)
    r1 = reply("openai", "gpt-5.6-luna", 100, 20, 40)
    r2 = reply("gemini", "gemini-2.5-flash", 200, 30, 70)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(run, "A", r1), pool.submit(run, "B", r2)]
        for job in jobs:
            job.result()
    assert app.group_api_usage["A"]["tokens_prompt"] == 100
    assert app.group_api_usage["B"]["tokens_prompt"] == 200
    assert app.group_api_usage["A"]["tokens_completion"] == 20
    assert app.group_api_usage["B"]["provider"] == "gemini"
    assert app.group_api_usage["B"]["cost_usd"] > 0
    assert sum(g["cost_usd"] for g in app.group_api_usage.values()) == pytest.approx(
        app.bot_stats["oai_cost_usd"] + app.bot_stats["gem_cost_usd"])


def test_late_group_identification_attributes_once_without_charging_global_twice():
    resp = reply("openai", "gpt-5.6-luna", 100, 20, 40)
    app.track_tokens(resp)
    app.track_tokens(resp, "A")
    app.track_tokens(resp, "A")
    assert app.bot_stats["tokens_prompt"] == 100
    assert app.group_api_usage["A"]["tokens_prompt"] == 100


def test_one_hour_cache_writes_are_distinguished_from_five_minutes():
    # 100 new + 400*1.25 (5m) + 600*2 (1h) + 50*0.1 (read), at $1/M;
    # 20 output tokens at $5/M. Independent arithmetic, USD units.
    expected = (100 + 400 * 1.25 + 600 * 2 + 50 * .1 + 20 * 5) / 1_000_000
    actual = app.calc_cost_usd("anthropic", "claude-haiku-4-5", input_tok=100,
        output_tok=20, cache_write=1000, cache_write_1h=600, cache_read=50)
    assert actual == pytest.approx(expected)


@pytest.mark.parametrize("tier,multiplier", [("default", 1), ("flex", .5), ("fast", 2), ("priority", 2)])
def test_openai_cache_write_and_returned_tier_affect_cost_but_not_token_count(tier, multiplier):
    resp = reply("openai", "gpt-5.6-luna", 1000, 100, 400)
    resp.usage.prompt_tokens_details.cache_write_tokens = 300
    resp.service_tier = tier
    app.track_tokens(resp, "A")
    expected = (300 * .2 + 400 * .02 + 300 * .25 + 100 * 1.2) / 1_000_000 * multiplier
    assert app.group_api_usage["A"]["cost_usd"] == pytest.approx(expected)
    assert app.group_api_usage["A"]["tokens_prompt"] == 1000
    assert app.group_api_usage["A"]["cache_write"] == 300


def test_sonnet_adapter_keeps_cache_ttl_usage_for_accounting(monkeypatch):
    native = NS(content=[NS(type="text", text="Terima kasih.")], stop_reason="end_turn",
        usage=NS(input_tokens=100, output_tokens=20, cache_read_input_tokens=50,
            cache_creation_input_tokens=1000, cache_creation=NS(ephemeral_1h_input_tokens=600)))
    client = NS(messages=NS(create=lambda **_k: native))
    monkeypatch.setattr(ai_provider, "_get_anthropic_client", lambda: client)
    monkeypatch.setattr(ai_provider, "_client_with_limits", lambda c, t: c)
    monkeypatch.setattr(ai_provider, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_provider, "_current_config", {"claude_features": {}})
    monkeypatch.setattr(ai_provider, "_resolve_anthropic_model", lambda _: "claude-haiku-4-5")
    result = ai_provider._chat_complete_anthropic("offline", [{"role":"user", "content":"謝謝"}], 100)
    assert result.usage.cache_creation_1h_tokens == 600
    app.track_tokens(result, "A")
    assert app.group_api_usage["A"]["tokens_prompt"] == 1150
    assert app.group_api_usage["A"]["cost_usd"] == pytest.approx(.001905)
