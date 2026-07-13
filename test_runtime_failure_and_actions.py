import types

import pytest
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


def test_parenthesized_plain_text_mention_is_detected_and_protected():
    source = "@(杰弗) 噴漆執行上有什麼問題？"

    assert app.extract_mentions(source) == ["@(杰弗)"]
    protected, placeholders = app.protect_mentions(source)

    assert protected == "__MENTION_0__ 噴漆執行上有什麼問題？"
    assert placeholders == {"__MENTION_0__": "@(杰弗)"}
    assert app.strip_mentions_for_detect(source).strip() == "噴漆執行上有什麼問題？"


def test_repeated_same_mention_gets_one_placeholder_per_occurrence():
    source = "@(杰弗) 先確認，@(杰弗) 再回報。"

    protected, placeholders = app.protect_mentions(source)

    assert protected == "__MENTION_0__ 先確認，__MENTION_1__ 再回報。"
    assert placeholders == {
        "__MENTION_0__": "@(杰弗)",
        "__MENTION_1__": "@(杰弗)",
    }
    restored = app.restore_mentions(protected, placeholders)
    assert restored == source
    assert app._post_restore_mentions_guard(restored, placeholders) == source


def test_malformed_line_mention_shell_is_repaired_from_user_profile(monkeypatch):
    mentionee = types.SimpleNamespace(
        index=0,
        length=3,
        type="user",
        user_id="U-mentioned-user",
    )
    message = types.SimpleNamespace(
        mention=types.SimpleNamespace(mentionees=[mentionee])
    )
    monkeypatch.setattr(app, "get_display_name", lambda group_id, user_id: "杰弗")

    normalized, mentions = app.normalize_line_mentions(
        "@() 噴漆執行上有什麼問題？",
        message,
        "group-1",
    )

    assert normalized == "@(杰弗) 噴漆執行上有什麼問題？"
    assert mentions == ["@(杰弗)"]


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


def _fake_translation_response(content):
    choice = types.SimpleNamespace(
        message=types.SimpleNamespace(content=content),
        finish_reason="stop",
        logprobs=None,
    )
    return types.SimpleNamespace(
        choices=[choice],
        usage=None,
        model="test-model",
        _jy_provider="openai",
        _jy_failover_attempts=[],
    )


def test_spray_paint_runtime_policy_matches_central_glossary():
    rule = next(
        item for item in app._FACTORY_DOMAIN_TERM_RULES_ZH_ID
        if item.get("key") == "spray_cat"
    )

    assert rule["preferred_id"] == "pengecatan semprot"
    assert "spray cat" in rule["forbidden_id_terms"]
    assert "噴漆" not in app.ZH_TO_ID_HARD
    assert app._validate_terminology_policy_consistency() is True


def test_terminology_policy_invariant_rejects_future_deprecated_hard_rule(monkeypatch):
    monkeypatch.setitem(app.ZH_TO_ID_HARD, "測試噴漆衝突", "spray cat")

    with pytest.raises(RuntimeError, match="Terminology policy conflict"):
        app._validate_terminology_policy_consistency()


def test_screenshot_sentence_repairs_old_spray_cat_output_instead_of_failing(monkeypatch):
    """Regression for the exact LINE screenshot failure.

    Older prompt/semantic rules asked the provider to emit ``spray cat`` while
    the final delivery gate rejected that same phrase as deprecated.  Even when
    a provider still returns the stale phrase, the local semantic layer must now
    migrate it to the canonical Indonesian term and keep the @mention intact.
    """
    stale_provider_output = (
        "__MENTION_0__ Apa masalah dalam pelaksanaan spray cat?"
    )
    monkeypatch.setattr(
        app.ai.chat.completions,
        "create",
        lambda **_kwargs: _fake_translation_response(stale_provider_output),
    )
    app.translation_cache.clear()
    app._tl.group_id = "regression-group"

    # Use the same raw text shown in LINE.  There is intentionally no LINE
    # mention metadata and no prebuilt placeholder map: the public translation
    # boundary must recognize and preserve the pasted @(name) form itself.
    actual = app.translate_multi(
        "@(杰弗) 噴漆執行上有什麼問題？",
        "zh",
        ["id"],
    )

    assert actual == [
        ("id", "@(杰弗) Apa kendala dalam proses pengecatan semprot?")
    ]
    report = tqg.validate_translation(
        "@(杰弗) 噴漆執行上有什麼問題？",
        actual[0][1],
        "zh",
        "id",
    )
    assert report.ok, report.issues


def test_natural_variant_preserves_parenthesized_mention(monkeypatch):
    seen = {}

    def fake_translate_openai(text, src, tgt, **_kwargs):
        seen["text"] = text
        return "__MENTION_0__ Apa kendala dalam proses pengecatan semprot?"

    monkeypatch.setattr(app, "translate_openai", fake_translate_openai)
    monkeypatch.setattr(
        app,
        "_final_delivery_guard",
        lambda source, candidate, src, tgt: candidate,
    )

    actual = app._translate_variant_preserving_mentions(
        "@(杰弗) 噴漆執行上有什麼問題？",
        "zh",
        "id",
    )

    assert seen["text"].startswith("__MENTION_0__")
    assert actual == "@(杰弗) Apa kendala dalam proses pengecatan semprot?"


def test_spray_paint_prompt_hint_never_recommends_deprecated_term():
    hint = app.build_factory_context_hint_zh_id("噴漆執行上有什麼問題？")

    assert "噴漆=pengecatan semprot/mengecat" in hint
    assert "禁止 spray cat" in hint
