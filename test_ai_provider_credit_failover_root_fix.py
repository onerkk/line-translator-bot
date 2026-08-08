import copy
from types import SimpleNamespace
from unittest.mock import patch

import ai_provider


def _cfg():
    cfg = copy.deepcopy(ai_provider.DEFAULT_CONFIG)
    cfg["active_provider"] = "anthropic"
    cfg["anthropic"]["api_key"] = "claude-key"
    cfg["openai"]["api_key"] = "openai-key"
    cfg["gemini"]["api_key"] = "gemini-key"
    cfg["provider_failover"] = True
    cfg["auto_switch_on_exhaust"] = True
    cfg["failover_policy"]["provider_order"] = ["anthropic", "openai", "gemini"]
    cfg["failover_policy"]["strict_failover_order"] = True
    cfg["failover_policy"]["adaptive_backup_order"] = False
    cfg["quota_exhausted_providers"] = {}
    cfg["auto_switch_state"] = {}
    return cfg


def _response(text="ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        model="fake-model",
    )


def test_exhausted_claude_is_persistently_filtered_from_future_requests():
    cfg = _cfg()
    cfg["active_provider"] = "openai"
    cfg["quota_exhausted_providers"]["anthropic"] = {
        "at": 1,
        "error": "credit balance is too low",
    }
    with patch.object(ai_provider, "_ensure_initialized", return_value=None), \
         patch.object(ai_provider, "_current_config", cfg), \
         patch.object(ai_provider, "_circuit_is_open", return_value=False):
        providers = ai_provider.get_available_providers(
            "chat", preference=["openai", "gemini", "anthropic"]
        )
    assert providers == ["openai", "gemini"]


def test_claude_credit_exhaustion_switches_to_openai_before_gemini():
    cfg = _cfg()
    notices = []
    with patch.object(ai_provider, "_ensure_initialized", return_value=None), \
         patch.object(ai_provider, "_current_config", cfg), \
         patch.object(ai_provider, "_save_config_to_disk", return_value=True), \
         patch.object(ai_provider, "_notify_admin", side_effect=notices.append):
        ai_provider._last_auto_switch_by_provider = {}
        alt = ai_provider._auto_switch_on_exhaust(
            "anthropic", RuntimeError("credit balance is too low")
        )
    assert alt == "openai"
    assert cfg["active_provider"] == "openai"
    assert "anthropic" in cfg["quota_exhausted_providers"]
    assert cfg["auto_switch_state"]["from"] == "anthropic"
    assert cfg["auto_switch_state"]["to"] == "openai"
    assert any("OpenAI" in msg for msg in notices)


def test_two_depleted_providers_can_advance_to_gemini_in_same_minute():
    cfg = _cfg()
    with patch.object(ai_provider, "_ensure_initialized", return_value=None), \
         patch.object(ai_provider, "_current_config", cfg), \
         patch.object(ai_provider, "_save_config_to_disk", return_value=True), \
         patch.object(ai_provider, "_notify_admin", return_value=None):
        ai_provider._last_auto_switch_by_provider = {}
        first = ai_provider._auto_switch_on_exhaust(
            "anthropic", RuntimeError("credit balance is too low")
        )
        second = ai_provider._auto_switch_on_exhaust(
            "openai", RuntimeError("insufficient_quota")
        )
    assert first == "openai"
    assert second == "gemini"
    assert cfg["active_provider"] == "gemini"
    assert set(cfg["quota_exhausted_providers"]) == {"anthropic", "openai"}


def test_chat_complete_request_order_is_claude_openai_gemini_and_switch_is_durable():
    cfg = _cfg()
    calls = []

    def dispatch(provider, **_kwargs):
        calls.append(provider)
        if provider == "anthropic":
            raise RuntimeError("credit balance is too low")
        if provider == "openai":
            raise RuntimeError("insufficient_quota")
        return _response("selamat")

    with patch.object(ai_provider, "_ensure_initialized", return_value=None), \
         patch.object(ai_provider, "_current_config", cfg), \
         patch.object(ai_provider, "_save_config_to_disk", return_value=True), \
         patch.object(ai_provider, "_notify_admin", return_value=None), \
         patch.object(ai_provider, "_dispatch_provider", side_effect=dispatch), \
         patch.object(ai_provider, "_circuit_is_open", return_value=False):
        ai_provider._last_auto_switch_by_provider = {}
        result = ai_provider.chat_complete(
            model=ai_provider.DEFAULT_OPENAI_MODEL,
            messages=[{"role": "user", "content": "你好"}],
            provider_preference=["anthropic", "openai", "gemini"],
            failover_total_timeout=30,
            failover_per_provider_timeout=5,
        )

    assert calls == ["anthropic", "openai", "gemini"]
    assert getattr(result, "_jy_provider") == "gemini"
    assert cfg["active_provider"] == "gemini"
    assert set(cfg["quota_exhausted_providers"]) == {"anthropic", "openai"}


def test_stale_settings_restore_cannot_undo_auto_billing_switch_but_manual_switch_can():
    cfg = _cfg()
    cfg["active_provider"] = "openai"
    cfg["quota_exhausted_providers"]["anthropic"] = {
        "at": 1,
        "error": "credit balance is too low",
    }
    cfg["auto_switch_state"] = {
        "active": True,
        "from": "anthropic",
        "to": "openai",
        "at": 1,
        "reason": "quota_exhausted",
    }
    with patch.object(ai_provider, "_ensure_initialized", return_value=None), \
         patch.object(ai_provider, "_current_config", cfg), \
         patch.object(ai_provider, "_save_config_to_disk", return_value=True):
        ok_restore, _ = ai_provider.set_active_provider(
            "anthropic", respect_auto_switch=True
        )
        assert ok_restore is False
        assert cfg["active_provider"] == "openai"

        ok_manual, _ = ai_provider.set_active_provider("anthropic", manual=True)
        assert ok_manual is True
        assert cfg["active_provider"] == "anthropic"
        assert "anthropic" not in cfg["quota_exhausted_providers"]
        assert cfg["auto_switch_state"] == {}


def test_updating_provider_key_reenables_it_without_silently_making_it_primary():
    cfg = _cfg()
    cfg["active_provider"] = "openai"
    cfg["quota_exhausted_providers"]["anthropic"] = {
        "at": 1,
        "error": "credit balance is too low",
    }
    cfg["auto_switch_state"] = {
        "active": True,
        "from": "anthropic",
        "to": "openai",
        "at": 1,
        "reason": "quota_exhausted",
    }
    with patch.object(ai_provider, "_ensure_initialized", return_value=None), \
         patch.object(ai_provider, "_current_config", cfg), \
         patch.object(ai_provider, "_save_config_to_disk", return_value=True):
        ok, _ = ai_provider.update_provider_key("anthropic", "new-claude-key")
    assert ok is True
    assert "anthropic" not in cfg["quota_exhausted_providers"]
    assert cfg["active_provider"] == "openai"
    assert cfg["auto_switch_state"]["to"] == "openai"


def test_legacy_failover_config_is_migrated_to_strict_claude_openai_gemini():
    cfg = _cfg()
    cfg["failover_policy"]["provider_order"] = ["gemini", "openai", "anthropic"]
    cfg["failover_policy"]["adaptive_backup_order"] = True
    cfg["failover_policy"].pop("strict_failover_order", None)
    migrated = ai_provider._migrate_config_models(cfg)
    assert migrated["failover_policy"]["provider_order"][:3] == [
        "anthropic", "openai", "gemini"
    ]
    assert migrated["failover_policy"]["strict_failover_order"] is True
    assert migrated["failover_policy"]["adaptive_backup_order"] is False
