"""Test actual SDK-call retries, not only a mocked provider dispatcher."""
import copy
from types import SimpleNamespace

import pytest
import ai_provider as ai


def response(text="Terima kasih.", finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text),
        finish_reason=finish)], usage=None, model="offline")


@pytest.fixture
def transport(monkeypatch):
    cfg = copy.deepcopy(ai.DEFAULT_CONFIG)
    cfg["active_provider"] = "gemini"
    cfg["quota_exhausted_providers"] = {}
    cfg["provider_failover"] = True
    cfg["failover_policy"]["single_provider_retry"] = False
    cfg["gemini"]["api_key"] = "offline"
    monkeypatch.setattr(ai, "_current_config", cfg)
    monkeypatch.setattr(ai, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai, "get_available_providers", lambda *_a, **_k: ["gemini"])
    monkeypatch.setattr(ai, "_client_with_limits", lambda client, timeout: client)
    monkeypatch.setattr(ai, "_record_provider_success", lambda *_a: None)
    monkeypatch.setattr(ai, "_record_provider_failure", lambda *_a: None)
    monkeypatch.setattr(ai, "_resolve_gemini_model", lambda _m: "offline")
    monkeypatch.setattr(ai, "_USAGE_OBSERVER", None)
    def install(create):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        monkeypatch.setattr(ai, "_get_gemini_client", lambda: client)
    return install


def test_parameter_fallback_cannot_exceed_actual_attempt_budget(transport, monkeypatch):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        raise ValueError("400 unsupported reasoning_effort")
    transport(create)
    monkeypatch.setenv("TRANSLATION_TOTAL_API_ATTEMPTS", "2")
    @ai.translation_request_budget
    def run():
        with pytest.raises(Exception):
            ai.chat_complete(model="offline", messages=[{"role": "user", "content": "terima kasih"}])
        return ai.translation_budget_snapshot()
    budget = run()
    assert len(calls) == budget["attempts"] == 2


def test_optional_parameter_fallback_uses_remaining_time(transport, monkeypatch):
    clock = [100.0]
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            clock[0] += 4.0
            raise ValueError("400 unsupported reasoning_effort")
        return response()
    transport(create)
    monkeypatch.setattr(ai.time, "monotonic", lambda: clock[0])
    @ai.translation_request_budget
    def run():
        return ai.chat_complete(model="offline", messages=[{"role": "user", "content": "terima kasih"}],
            timeout=5, failover_total_timeout=5, failover_per_provider_timeout=5)
    assert run().choices[0].message.content == "Terima kasih."
    assert calls[0]["timeout"] == 5 and 0 < calls[1]["timeout"] <= 1


def test_unrelated_400_does_not_remove_all_optional_features(transport):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        raise ValueError("400 invalid API key")
    transport(create)
    with pytest.raises(Exception):
        ai.chat_complete(model="offline", messages=[{"role": "user", "content": "hello"}])
    assert len(calls) == 1


def test_context_window_truncation_is_never_kept_as_degraded_text(transport, monkeypatch):
    transport(lambda **_k: response("Periksa PMI.", "model_context_window_exceeded"))
    with pytest.raises(Exception):
        ai.chat_complete(model="offline", messages=[{"role": "user", "content": "source"}],
            translation_max_generations=1, response_validator=lambda *_a: (False, "truncated"))


def test_empty_second_response_cannot_erase_nonempty_first_candidate(transport):
    replies = iter([response("Periksa PMI."), response("")])
    transport(lambda **_k: next(replies))
    result = ai.chat_complete(model="offline", messages=[{"role": "user", "content": "source"}],
        translation_max_generations=2, response_validator=lambda *_a: (False, "needs local review"))
    assert result.choices[0].message.content == "Periksa PMI."
    assert result._jy_quality_degraded


def test_refusal_is_counted_before_failover_and_never_delivered(transport, monkeypatch):
    usage_seen = []
    monkeypatch.setattr(ai, "_USAGE_OBSERVER", lambda resp: usage_seen.append(resp))
    transport(lambda **_k: response("I cannot do this.", "refusal"))
    @ai.translation_request_budget
    def run():
        with pytest.raises(Exception):
            ai.chat_complete(model="offline", messages=[{"role": "user", "content": "source"}],
                translation_max_generations=1)
        assert ai.translation_budget_snapshot()["generations"] == 1
    run()
    assert len(usage_seen) == 1
