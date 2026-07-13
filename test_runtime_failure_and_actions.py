import types

import ai_provider
import app
import translation_quality_gate as tqg


def test_lowercase_line_display_name_is_not_language_leakage():
    source = (
        "@蘇比 sobirin @(杰弗) Jika Anda menemukan bahwa Anda tidak akan "
        "menyemprot cat, kinerjanya akan dikurangi 0,5 secara langsung."
    )
    candidate = "@蘇比 sobirin @(杰弗) 如果發現你沒有噴漆，績效將直接扣除0.5。"

    report = tqg.validate_translation(source, candidate, "id", "zh")

    assert report.ok, report.issues
    assert app._final_delivery_guard(source, candidate, "id", "zh") == candidate


def test_final_guard_returns_none_not_localized_failure_as_translation():
    result = app._final_delivery_guard(
        "TIDAK BOLEH masuk.",
        "不BOLEH進入。",
        "id",
        "zh",
    )

    assert result is None
    assert not app._is_translation_failure_sentinel("正常翻譯")
    assert app._is_translation_failure_sentinel(tqg.translation_failure_message("zh"))


def test_post_restore_guard_accepts_valid_line_mentions():
    placeholders = {
        "__MENTION_0__": "@蘇比 sobirin",
        "__MENTION_1__": "@(杰弗)",
    }
    candidate = "@蘇比 sobirin @(杰弗) 如果發現沒有噴漆，績效將直接扣除0.5。"

    assert app._post_restore_mentions_guard(candidate, placeholders) == candidate


def test_plain_text_translation_actions_are_visible_without_flex():
    qr = app._build_translation_action_quick_reply(
        "group-1",
        "噴漆執行上有什麼問題？",
        "Apa kendala dalam pelaksanaan proses pengecatan?",
        "zh",
        "id",
        "message-1",
    )

    assert qr is not None
    labels = [item.action.label for item in qr.items]
    assert labels[:5] == ["✨ 更自然", "🔎 直譯", "📢 正式", "↩ 回譯", "👤 我的語言"]
    assert "📋 交班摘要" in labels
    assert "🎙️ 即時口譯" in labels


def test_default_quick_reply_menu_exposes_new_commands():
    entries = {item["id"]: item for item in app.QUICK_REPLY_DEFAULTS}
    assert entries["handover"]["text"] == "/交班摘要"
    assert entries["interpreter"]["text"] == "/即時口譯"
    assert entries["handover"]["enabled"] is True
    assert entries["interpreter"]["enabled"] is True


def test_single_provider_transient_error_retries_once(monkeypatch):
    calls = []

    class TemporaryError(RuntimeError):
        status_code = 503

    response = types.SimpleNamespace(choices=[], usage=None, model="test-model")

    def fake_dispatch(provider, **kwargs):
        calls.append((provider, kwargs.get("timeout")))
        if len(calls) == 1:
            raise TemporaryError("service unavailable")
        return response

    monkeypatch.setattr(ai_provider, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_provider, "get_available_providers", lambda *a, **k: ["openai"])
    monkeypatch.setattr(ai_provider, "_dispatch_provider", fake_dispatch)
    monkeypatch.setattr(ai_provider, "_record_provider_success", lambda *a, **k: None)
    monkeypatch.setattr(ai_provider, "_record_provider_failure", lambda *a, **k: None)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        ai_provider,
        "_current_config",
        {
            "provider_failover": True,
            "failover_policy": {
                "single_provider_retry": True,
                "total_timeout_seconds": 5,
                "per_provider_timeout_seconds": 2,
            },
        },
    )

    actual = ai_provider.chat_complete(
        model="test-model",
        messages=[{"role": "user", "content": "hello"}],
        timeout=2,
    )

    assert actual is response
    assert len(calls) == 2
    assert response._jy_provider == "openai"
    assert response._jy_failover_attempts[0]["kind"] == "transient_retry"


def test_single_provider_retry_preserves_usable_response_validator(monkeypatch):
    response = types.SimpleNamespace(choices=[], usage=None, model="test-model")

    monkeypatch.setattr(ai_provider, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_provider, "get_available_providers", lambda *a, **k: ["openai"])
    monkeypatch.setattr(ai_provider, "_dispatch_provider", lambda *a, **k: response)
    monkeypatch.setattr(ai_provider, "_record_provider_success", lambda *a, **k: None)
    monkeypatch.setattr(
        ai_provider,
        "_current_config",
        {
            "provider_failover": True,
            "failover_policy": {
                "single_provider_retry": True,
                "total_timeout_seconds": 5,
                "per_provider_timeout_seconds": 2,
            },
        },
    )

    actual = ai_provider.chat_complete(
        model="test-model",
        messages=[{"role": "user", "content": "hello"}],
        timeout=2,
        response_validator=lambda _response, _provider: (True, "ok"),
    )

    assert actual is response
    assert response._jy_provider == "openai"
