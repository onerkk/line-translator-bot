"""SDK startup preparation and transport-gap attribution without paid calls."""
import copy
import importlib
import json
import logging
from types import SimpleNamespace

import pytest

import ai_provider as ai
import webhook_runtime
from test_translation_instruction_cost_quality import offline_transport


@pytest.fixture
def preparation(monkeypatch):
    monkeypatch.setattr(ai, "_prepared_translation_sdks", set())
    imports = []
    monkeypatch.setattr(ai.importlib, "import_module", lambda name: imports.append(name))
    def no_client():
        pytest.fail("Import-only startup preparation must not construct HTTP clients")
    for name in ("_get_openai_client", "_get_anthropic_client", "_get_gemini_client"):
        monkeypatch.setattr(ai, name, no_client)
    return imports


def test_prepares_only_configured_families_once_without_clients(preparation, monkeypatch):
    monkeypatch.setattr(ai, "get_available_providers", lambda *_: ["gemini", "openai", "anthropic"])
    rows = ai.prepare_translation_sdk_resources()
    assert preparation == ["openai.resources.chat", "anthropic.resources.messages"]
    assert [row["sdk"] for row in rows] == ["openai", "anthropic"]
    assert all(row["status"] == "loaded" and row["http_requests"] == 0 for row in rows)
    assert ai.prepare_translation_sdk_resources() == []
    assert len(preparation) == 2


def test_no_configured_provider_means_no_optional_sdk_import(preparation, monkeypatch):
    monkeypatch.setattr(ai, "get_available_providers", lambda *_: [])
    assert ai.prepare_translation_sdk_resources() == [] and preparation == []


def test_preparation_preserves_provider_keys_order_and_quota_state(offline_transport, preparation, monkeypatch):
    monkeypatch.setitem(ai._current_config, "active_provider", "openai")
    monkeypatch.setitem(ai._current_config, "quota_exhausted_providers", {"anthropic": {"reason": "billing"}})
    before = copy.deepcopy(ai._current_config)
    ai.prepare_translation_sdk_resources()
    assert ai._current_config == before
    assert preparation == ["openai.resources.chat"]


def test_failed_optional_import_does_not_disable_other_provider_and_can_retry(preparation, monkeypatch, caplog):
    monkeypatch.setattr(ai, "get_available_providers", lambda *_: ["anthropic", "openai"])
    def import_sdk(name):
        preparation.append(name)
        if name.startswith("anthropic"):
            raise ImportError("private path must not be logged")
    monkeypatch.setattr(ai.importlib, "import_module", import_sdk)
    with caplog.at_level(logging.INFO, logger="app"):
        rows = ai.prepare_translation_sdk_resources()
    assert [row["status"] for row in rows] == ["unavailable", "loaded"]
    assert "private path" not in caplog.text
    assert ai._prepared_translation_sdks == {"openai"}
    monkeypatch.setattr(ai.importlib, "import_module", lambda name: preparation.append(name))
    assert ai.prepare_translation_sdk_resources()[0]["sdk"] == "anthropic"
    assert preparation.count("openai.resources.chat") == 1


def test_real_sdk_imports_prepare_resources_without_creating_a_client(offline_transport, monkeypatch):
    # Real SDK resource modules are imported with the offline runner blocking
    # network. No credentials, HTTP clients or API methods are needed.
    monkeypatch.setattr(ai, "_prepared_translation_sdks", set())
    monkeypatch.setattr(ai, "get_available_providers", lambda *_: ["openai", "anthropic", "gemini"])
    before = [ai._openai_client, ai._anthropic_client, ai._gemini_client]
    rows = ai.prepare_translation_sdk_resources()
    assert all(row["status"] == "loaded" for row in rows)
    assert all(a is b for a, b in zip(before, [ai._openai_client, ai._anthropic_client, ai._gemini_client]))
    assert callable(importlib.import_module("openai.resources.chat").Chat)
    assert callable(importlib.import_module("anthropic.resources.messages").Messages)


def provider_records(caplog):
    return [json.loads(record.message.split("[AIProviderPerf] ", 1)[1])
            for record in caplog.records if "[AIProviderPerf] " in record.message]


def test_lazy_resource_loading_is_measured_separately_from_the_network(monkeypatch, caplog):
    clock = [100.0]
    monkeypatch.setattr(ai.time, "monotonic", lambda: clock[0])
    response = SimpleNamespace(usage=None, model="offline")
    def create(**kwargs):
        assert 0 < kwargs["timeout"] < 10
        clock[0] += 2.47
        return response
    class Client:
        @property
        def chat(self):
            clock[0] += 4.5  # Injected SDK import delay, not a real timing claim.
            return SimpleNamespace(completions=SimpleNamespace(create=create))
    monkeypatch.setattr(ai, "_log_sdk_request", lambda *_: clock.__setitem__(0, clock[0] + .03))
    @ai._bounded_provider_transport
    def _chat_complete_openai(*, timeout):
        with ai._provider_stage("client_setup"):
            clock[0] += .9
        with ai._provider_stage("request_options"):
            clock[0] += .2
        method = ai._translation_create_method(Client(), "openai")
        result = ai._sdk_create(method, {"timeout": timeout})
        clock[0] += .1
        return result
    with caplog.at_level(logging.INFO, logger="app"), webhook_runtime.timing_scope():
        trace = webhook_runtime.event_timing(SimpleNamespace(webhook_event_id="private-event"))["trace"]
        assert _chat_complete_openai(timeout=10) is response
    row = provider_records(caplog)[0]
    assert row["trace"] == trace and "private-event" not in caplog.text
    assert row["client_setup_ms"] == 900 and row["request_options_ms"] == 200
    assert row["resource_setup_ms"] == 4500 and row["sdk_ms"] == 2470
    assert row["sdk_logging_ms"] == 30 and row["other_ms"] == 100
    assert row["outside_sdk_ms"] == 5730 and row["total_ms"] == 8200 and row["sdk_calls"] == 1
    assert ai._TRANSPORT_SCOPE.get() is None


@pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic"])
def test_resource_binding_does_not_call_the_provider(provider):
    calls = []
    def create(**_):
        calls.append(1)
    client = SimpleNamespace(messages=SimpleNamespace(create=create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    method = ai._translation_create_method(client, provider)
    assert method is create and calls == []


def test_setup_time_keeps_the_original_api_deadline(monkeypatch, caplog):
    clock = [1.0]
    monkeypatch.setattr(ai.time, "monotonic", lambda: clock[0])
    def forbidden(**_):
        pytest.fail("An exhausted deadline must not start another network request")
    @ai._bounded_provider_transport
    def _chat_complete_openai(*, timeout):
        with ai._provider_stage("resource_setup"):
            clock[0] += 2
        return ai._sdk_create(forbidden, {"timeout": timeout})
    with caplog.at_level(logging.INFO, logger="app"), pytest.raises(TimeoutError):
        _chat_complete_openai(timeout=1)
    row = provider_records(caplog)[0]
    assert row["sdk_calls"] == 0 and row["resource_setup_ms"] == 2000 and row["sdk_ms"] == 0
    assert ai._TRANSPORT_SCOPE.get() is None


def test_provider_timing_failure_cannot_replace_a_result_or_error(monkeypatch):
    def broken(*_a, **_k):
        raise OSError("logging down")
    monkeypatch.setattr(logging.getLogger("app"), "info", broken)
    @ai._bounded_provider_transport
    def good():
        return "accepted"
    original = TimeoutError("original transport error")
    @ai._bounded_provider_transport
    def bad():
        raise original
    assert good() == "accepted"
    with pytest.raises(TimeoutError) as caught:
        bad()
    assert caught.value is original and ai._TRANSPORT_SCOPE.get() is None
