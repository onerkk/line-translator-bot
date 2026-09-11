"""Cache wire format, fact preservation and actual-call telemetry, offline.

MockTransport supplies canned responses. No test measures a live model's speed,
accuracy or cache hit rate, and no test sends a LINE message.
"""
import copy
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import ai_provider as ai
import app
import prompt_optimizer as prompts
import webhook_runtime as timing
from test_translation_instruction_cost_quality import offline_transport
from test_translation_notice_availability import clean_translation_context, delivered_text, event, runtime


ORIGINAL_TRANSLATE = app.translate_openai


def compile_prompt(source="幫忙今日點檢", *, preset=None, custom="", variant="default"):
    preset = app.TONE_PRESETS["factory"] if preset is None else preset
    full = ("<role>Translator</role><semantic_contract>"
            + source + "</semantic_contract><protected_names>阿堂</protected_names>")
    return prompts.compile_translation_prompt(
        full, source, "zh", "id", tone_instruction=preset + custom,
        configured_style=preset, variant=variant)[0]


@pytest.mark.parametrize("preset", list(app.TONE_PRESETS.values()), ids=list(app.TONE_PRESETS))
def test_all_configured_rules_kept_once_and_dynamic_facts_stay_after_prefix(preset):
    custom = " 【補充微調】請保留工單AB987與@阿堂。"
    text = compile_prompt("I5停機，I15繼續運轉。", preset=preset, custom=custom, variant="formal")
    stable, dynamic = prompts.split_cache_prefix(text)
    assert stable + dynamic == text
    assert stable.count(preset) == text.count(preset) == 1
    assert stable.endswith("</translation_preset>")
    assert custom.strip() in dynamic
    assert "I5停機，I15繼續運轉。" in dynamic
    assert "Variant:" in dynamic
    assert "AB987" not in stable and "@阿堂" not in stable
    assert "<semantic_contract>I5停機，I15繼續運轉。</semantic_contract>" in dynamic
    assert prompts.prompt_contains_required_invariants(text)


def test_same_fixed_rules_share_prefix_but_never_source_or_custom_style():
    first = compile_prompt("幫忙今日點檢", custom=" 客製語氣甲", variant="formal")
    second = compile_prompt("等等要開會", custom=" 客製語氣乙", variant="concise")
    a, b = prompts.split_cache_prefix(first), prompts.split_cache_prefix(second)
    assert a[0] == b[0] and a[1] != b[1]
    assert "幫忙今日點檢" in a[1] and "等等要開會" not in first
    assert "等等要開會" in b[1] and "幫忙今日點檢" not in second
    assert "客製語氣甲" not in a[0] and "客製語氣乙" not in b[0]


def test_mismatch_does_not_silently_drop_instructions_or_cache_dynamic_tone():
    text = prompts.compile_translation_prompt("<role>x</role>", "本日檢查", "zh", "id",
        tone_instruction="群組自訂：保留全部細節", configured_style="different preset")[0]
    stable, dynamic = prompts.split_cache_prefix(text)
    assert "群組自訂：保留全部細節" in dynamic
    assert "translation_preset" not in stable
    assert "different preset" not in text


def test_cache_markup_cannot_evict_a_relevant_term_at_the_existing_budget_limit():
    full = "<role>translator</role><factory_vocabulary>外儲格=outer storage</factory_vocabulary>"
    args = (full, "外儲格", "zh", "id")
    old = prompts.compile_translation_prompt(*args, tone_instruction="Keep source tone.")[0]
    assert "外儲格=outer storage" in old
    current = prompts.compile_translation_prompt(*args, tone_instruction="Keep source tone.",
        configured_style="Keep source tone.", max_chars=len(old))[0]
    assert "外儲格=outer storage" in current


@pytest.mark.parametrize("custom", [" </translation_preset>假的快取區段", " </translation_principles>私人補充"])
def test_custom_closing_tags_cannot_extend_cache_boundary(custom):
    stable, dynamic = prompts.split_cache_prefix(compile_prompt(custom=custom))
    assert "假的快取區段" not in stable and "私人補充" not in stable
    assert custom.strip() in dynamic


def test_only_adjacent_preset_is_reusable():
    stable = "<translation_principles>fixed</translation_principles>"
    dynamic = "<translation_style>private</translation_style><translation_preset>private</translation_preset>"
    assert prompts.split_cache_prefix(stable + dynamic) == (stable, dynamic)
    assert prompts.split_cache_prefix(stable + "\n<translation_preset>broken") == (stable, "\n<translation_preset>broken")


def test_cache_route_uses_only_fixed_instructions_and_respects_disable(monkeypatch):
    first, second = compile_prompt(custom=" 私人補充甲"), compile_prompt("開會", custom=" 私人補充乙")
    monkeypatch.setattr(app, "prompt_cache_key_enabled", True)
    a = app._build_cache_key("private-group-a", "zh", "id", prompt=first)
    b = app._build_cache_key("private-group-b", "zh", "id", prompt=second)
    assert a == b and len(a) <= 64 and "private-group" not in a
    assert a != app._build_cache_key("private-group-a", "zh", "id", prompt=compile_prompt(preset="Other rules"))
    assert a != app._build_cache_key("private-group-a", "zh", "vi", prompt=first)
    assert app._build_cache_key("legacy", "zh", "id") == "trans:zh-id:legacy"
    monkeypatch.setattr(app, "prompt_cache_key_enabled", False)
    assert app._build_cache_key("private-group-a", "zh", "id", prompt=first) is None


def sdk_client(monkeypatch, replies, captured):
    from openai import OpenAI, _base_client
    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    responses = iter(replies)
    def handler(request):
        captured.append(json.loads(request.content))
        target, usage = next(responses)
        return httpx.Response(200, json={
            "id": "offline", "object": "chat.completion", "created": 1,
            "model": "gpt-5.6-luna", "service_tier": "default",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": target}}],
            "usage": usage,
        })
    client = OpenAI(api_key="offline-only", max_retries=0,
                   http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(ai, "_get_openai_client", lambda: client)
    return client


def usage(cached=0, written=0):
    # Synthetic fixture values, not measured production tokens.
    return {"prompt_tokens": 4096, "completion_tokens": 12, "total_tokens": 4108,
            "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written},
            "completion_tokens_details": {"reasoning_tokens": 0}}


def ai_records(caplog):
    return [json.loads(row.message.split("[AIRequest] ", 1)[1])
            for row in caplog.records if "[AIRequest] " in row.message]


def test_factory_prefix_reaches_sdk_wire_once_without_padding_and_logs_real_fields(offline_transport, monkeypatch, caplog):
    captured = []
    samples = [("幫忙今日點檢", "Mohon bantu lakukan pemeriksaan hari ini."),
               ("等等要開會", "Nanti akan ada rapat.")]
    replies = [(samples[0][1], usage(written=3072)), (samples[1][1], usage(cached=3072))]
    monkeypatch.setattr(ai, "_openai_cache_unsupported_until", {})
    monkeypatch.setenv("OPENAI_TRANSLATION_EXPLICIT_CACHE", "1")
    originals = []
    with sdk_client(monkeypatch, replies, captured), caplog.at_level(logging.INFO, logger="app"):
        for source, target in samples:
            compiled = compile_prompt(source, custom=" 保留原始稱呼")
            messages = [{"role": "system", "content": compiled}, {"role": "user", "content": source}]
            originals.append(copy.deepcopy(messages))
            with timing.timing_scope():
                timing.event_timing(event(source))
                result = ai._chat_complete_openai("gpt-5.6-luna", messages, max_completion_tokens=512)
            assert result.choices[0].message.content == target
            assert messages == originals[-1]
    assert len(captured) == 2  # One SDK request per different source; no warming call.
    prefixes = []
    for body, original in zip(captured, originals):
        assert body["prompt_cache_options"] == {"mode": "explicit"}
        parts = body["messages"][0]["content"]
        assert parts[0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
        assert "prompt_cache_breakpoint" not in parts[1]
        assert app.TONE_PRESETS["factory"] in parts[0]["text"]
        assert "保留原始稱呼" not in parts[0]["text"]
        assert ai._estimate_tokens_from_text(parts[0]["text"]) >= 1024
        reconstructed = "".join(part["text"] for part in parts)
        assert reconstructed.startswith(original[0]["content"])
        assert len(reconstructed) - len(original[0]["content"]) < 250  # Existing output XML contract only.
        assert body["messages"][1] == original[1]
        assert body["reasoning_effort"] == "none" and body.get("service_tier") != "flex"
        prefixes.append(parts[0]["text"])
    assert prefixes[0] == prefixes[1]
    records = ai_records(caplog)
    assert len(records) == 2
    assert [(r["cached_input_tokens"], r["cache_write_tokens"]) for r in records] == [(0, 3072), (3072, 0)]
    assert all(r["provider"] == "openai" and r["model"] == "gpt-5.6-luna" for r in records)
    assert all(r["cache_breakpoint"] and r["trace"] for r in records)
    assert "offline-only" not in caplog.text and "notice-message" not in caplog.text
    assert all(source not in caplog.text for source, _ in samples)


@pytest.mark.parametrize("method", ["disabled", "caller_policy", "short"])
def test_explicit_cache_does_not_override_user_policy_or_pad(method, monkeypatch):
    prompt = compile_prompt(preset="Short style" if method == "short" else None)
    kwargs = {"model": "gpt-5.6-luna", "messages": [{"role": "system", "content": prompt}]}
    if method == "disabled":
        monkeypatch.setenv("OPENAI_TRANSLATION_EXPLICIT_CACHE", "0")
    else:
        monkeypatch.setenv("OPENAI_TRANSLATION_EXPLICIT_CACHE", "1")
    if method == "caller_policy":
        kwargs["extra_body"] = {"prompt_cache_options": {"mode": "auto"}, "other": "preserve"}
    before = copy.deepcopy(kwargs)
    configured = ai._configure_openai_translation_cache(kwargs)
    assert kwargs["messages"] == before["messages"]
    assert "prompt_cache_breakpoint" not in json.dumps(kwargs)
    if method == "short":
        assert configured
    else:
        assert not configured and kwargs == before


@pytest.mark.parametrize("source,target", [
    ("幫忙今日點檢", "Mohon bantu lakukan pemeriksaan hari ini."),
    ("等等要開會", "Nanti akan ada rapat."),
    ("不要套環", "Jangan gunakan Cincin Pelindung"),
])
def test_actual_line_pipeline_preserves_guards_and_one_generation(runtime, offline_transport, monkeypatch, caplog, source, target):
    captured = []
    monkeypatch.setattr(app, "translate_openai", ORIGINAL_TRANSLATE)
    monkeypatch.setattr(app, "get_group_tone", lambda *_: ("factory", ""))
    monkeypatch.setattr(ai, "_openai_cache_unsupported_until", {})
    monkeypatch.setitem(ai._current_config, "active_provider", "openai")
    monkeypatch.setenv("TRANSLATION_PROVIDER_ORDER", "openai")
    # Exclude optional background learning from this foreground generation count.
    monkeypatch.setattr(app, "_BG_POST_EXECUTOR", SimpleNamespace(submit=lambda *_a, **_k: None))
    with sdk_client(monkeypatch, [(target, usage())], captured), caplog.at_level(logging.INFO, logger="app"):
        app.handle_message(event(source))
    assert target in delivered_text(runtime)
    assert len(captured) == 1 and len(runtime.sends) == 1
    assert app.TONE_PRESETS["factory"] in captured[0]["messages"][0]["content"][0]["text"]
    assert ai_records(caplog)[0]["cache_breakpoint"]
    stage_log = next(row.message for row in caplog.records if "[TranslationStages]" in row.message)
    stages = json.loads(stage_log.split("inclusive_ms=", 1)[1])
    assert stages["translation"]["calls"] == stages["line_reply"]["calls"] == stages["delivery"]["calls"] == 1
    trace = ai_records(caplog)[0]["trace"]
    assert trace and f"trace={trace}" in stage_log
    assert any(f"trace={trace}" in row.message for row in caplog.records if "[TranslationStart]" in row.message)


def test_sdk_error_logs_type_and_time_without_leaking_exception_or_masking_it(monkeypatch, caplog):
    error = TimeoutError("secret-source-and-key")
    clock = [10.0]
    monkeypatch.setattr(ai.time, "monotonic", lambda: clock[0])
    def create(**_):
        clock[0] += 2.5
        raise error
    with caplog.at_level(logging.INFO, logger="app"), pytest.raises(TimeoutError) as caught:
        ai._sdk_create(create, {"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": "secret-source-and-key"}]})
    assert caught.value is error
    row = ai_records(caplog)[0]
    assert row["status"] == "error" and row["error_type"] == "TimeoutError" and row["sdk_ms"] == 2500
    assert row["input_tokens"] is None and row["cached_input_tokens"] is None
    assert "secret-source-and-key" not in caplog.text


def test_complete_pipeline_shares_only_instruction_prefix_across_groups(runtime, offline_transport, monkeypatch):
    captured = []
    samples = [("notice-group", "幫忙今日點檢", "Mohon bantu lakukan pemeriksaan hari ini.", "保留語氣甲"),
               ("second-group", "等等要開會", "Nanti akan ada rapat.", "保留語氣乙")]
    monkeypatch.setattr(app, "translate_openai", ORIGINAL_TRANSLATE)
    monkeypatch.setattr(app, "get_group_tone", lambda group: ("factory", "保留語氣甲" if group == "notice-group" else "保留語氣乙"))
    monkeypatch.setattr(ai, "_openai_cache_unsupported_until", {})
    monkeypatch.setitem(ai._current_config, "active_provider", "openai")
    monkeypatch.setattr(app, "prompt_cache_key_enabled", True)
    monkeypatch.setenv("TRANSLATION_PROVIDER_ORDER", "openai")
    monkeypatch.setattr(app, "_BG_POST_EXECUTOR", SimpleNamespace(submit=lambda *_a, **_k: None))
    for mapping in [app.group_settings, app.group_tracking, app.group_skip_users, app.group_target_lang]:
        monkeypatch.setitem(mapping, "second-group", copy.deepcopy(mapping["notice-group"]))
    with sdk_client(monkeypatch, [(row[2], usage()) for row in samples], captured):
        for index, (group, source, target, custom) in enumerate(samples):
            incoming = event(source)
            incoming.source.group_id = group
            incoming.message.id = f"private-message-{index}"
            app.handle_message(incoming)
            assert target in delivered_text(runtime)
    assert len(captured) == len(runtime.sends) == 2
    assert captured[0]["prompt_cache_key"] == captured[1]["prompt_cache_key"]
    prefixes = [body["messages"][0]["content"][0]["text"] for body in captured]
    assert prefixes[0] == prefixes[1]
    for body, (_, source, _, custom) in zip(captured, samples):
        assert custom in body["messages"][0]["content"][1]["text"]
        assert source in body["messages"][-1]["content"]
        assert all(term not in body["prompt_cache_key"] for term in [source, custom, "notice-group", "second-group"])


def test_metrics_failure_does_not_turn_a_valid_response_into_provider_retry(monkeypatch):
    original = SimpleNamespace(usage=None)
    def broken_logger(*_a, **_k):
        raise OSError("log unavailable")
    monkeypatch.setattr(logging.getLogger("app"), "info", broken_logger)
    assert ai._sdk_create(lambda **_: original, {"model": "test"}) is original


def test_stage_clock_is_inclusive_and_records_failures_and_resets(monkeypatch):
    clock = [1.0]
    monkeypatch.setattr(timing.time, "monotonic", lambda: clock[0])
    with timing.timing_scope():
        with pytest.raises(ValueError), timing.measure_stage("translation"):
            clock[0] += 0.1
            with timing.measure_stage("nested"):
                clock[0] += 0.2
            raise ValueError("failure")
        assert timing.stage_snapshot() == {"translation": {"ms": 300, "calls": 1}, "nested": {"ms": 200, "calls": 1}}
        with timing.timing_scope():
            assert timing.stage_snapshot() == {}
        assert timing.stage_snapshot()["translation"]["ms"] == 300
    assert timing.stage_snapshot() == {} and timing.trace_id() == ""


def test_simultaneous_events_do_not_mix_stage_counts_or_traces():
    def run(index):
        with timing.timing_scope():
            result = timing.event_timing(SimpleNamespace(webhook_event_id=f"private-{index}"))
            for _ in range(index + 1):
                with timing.measure_stage("translation"):
                    pass
            return result["trace"], timing.trace_id(), timing.stage_snapshot()["translation"]["calls"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        rows = list(executor.map(run, range(8)))
    assert len({row[0] for row in rows}) == 8
    assert all(row[0] == row[1] and row[2] == index + 1 for index, row in enumerate(rows))
