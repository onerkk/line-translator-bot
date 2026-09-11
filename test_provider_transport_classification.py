"""Transport recovery uses exception identity, not vendor error wording."""
import errno

try:
    import httpx2 as httpx
except ImportError:
    import httpx
import pytest
import requests

import ai_provider
from test_translation_instruction_cost_quality import offline_transport, response


@pytest.mark.parametrize("error", [
    TimeoutError(),
    TimeoutError("Provider transport deadline exhausted"),
    ConnectionResetError(errno.ECONNRESET, "peer closed"),
    httpx.ReadTimeout(""),
    httpx.ReadError("peer closed"),
    httpx.RemoteProtocolError("Server disconnected without sending a response."),
    requests.exceptions.ReadTimeout(""),
])
def test_transport_error_reaches_next_provider(offline_transport, monkeypatch, error):
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(provider)
        if provider == "anthropic":
            raise error
        return response("Tolong bantu lakukan pemeriksaan hari ini.")
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    @ai_provider.translation_request_budget
    def run():
        result = ai_provider.chat_complete(model="test", messages=[{"role": "user", "content": "幫忙今日點檢"}])
        assert result.choices[0].message.content == "Tolong bantu lakukan pemeriksaan hari ini."
        assert ai_provider.translation_budget_snapshot()["attempts"] == 2
    run()
    assert calls == ["anthropic", "openai"]
    assert offline_transport["active_provider"] == "anthropic"
    assert offline_transport["quota_exhausted_providers"] == {}


def test_single_provider_read_timeout_gets_only_one_bounded_retry(offline_transport, monkeypatch):
    offline_transport["openai"]["api_key"] = offline_transport["gemini"]["api_key"] = ""
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(provider)
        if len(calls) == 1:
            raise httpx.ReadTimeout("")
        return response("ok")
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda _seconds: None)
    result = ai_provider.chat_complete(model="test", messages=[{"role": "user", "content": "text"}])
    assert result.choices[0].message.content == "ok"
    assert calls == ["anthropic", "anthropic"]


@pytest.mark.parametrize("error", [
    ValueError("bad input"), TypeError("invalid request"),
    OSError(errno.ENOSPC, "disk full"), PermissionError(errno.EACCES, "access denied"),
    httpx.LocalProtocolError("malformed request"),
])
def test_local_fault_is_not_a_retryable_transport_error(error):
    assert not ai_provider._is_availability_error(error)
    assert not ai_provider._is_provider_failover_error(error)
