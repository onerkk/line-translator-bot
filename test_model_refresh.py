"""Offline regression tests for the 2026-07-12 OpenAI model policy."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import re

import ai_provider

ROOT = Path(__file__).resolve().parent
CURRENT_CHAT_MODELS = {
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
    "gpt-5.4-mini",
    "gpt-4.1-mini",
    "gpt-5.4-nano",
    "gpt-4.1",
    "gpt-5.4",
    "gpt-5.5",
}


def test_model_migrations():
    cases = {
        "gpt-5-2025-08-07": "gpt-5.6-sol",
        "gpt-5-mini-2025-08-07": "gpt-5.4-mini",
        "gpt-5-nano-2025-08-07": "gpt-5.4-nano",
        "gpt-5-pro-2025-10-06": "gpt-5.5-pro",
        "gpt-4.1-nano": "gpt-5.4-nano",
        "o1": "gpt-5.6-sol",
        "o3-mini": "gpt-5.6-sol",
        "o4-mini": "gpt-5.4-mini",
        "gpt-5.5-mini": "gpt-5.4-mini",
        "gpt-5.5-nano": "gpt-5.4-nano",
    }
    for old, expected in cases.items():
        assert ai_provider.normalize_openai_model(old) == expected


def test_tts_migrations():
    expected = "tts-1"
    assert ai_provider.normalize_tts_model("gpt-4o-mini-tts") == expected
    assert ai_provider.normalize_tts_model("gpt-4o-mini-tts-2025-03-20") == expected
    assert ai_provider.normalize_tts_model("gpt-4o-mini-tts-2025-12-15") == expected
    assert ai_provider.normalize_tts_model("tts-1") == "tts-1"
    assert ai_provider.normalize_tts_model("not-a-tts-model") == expected


def test_unknown_admin_value_falls_back():
    assert ai_provider.normalize_translation_model("not-a-model") == "gpt-5.6-luna"
    assert ai_provider.normalize_vision_model("not-a-model") == "gpt-5.6-terra"


def test_saved_mapping_migration_preserves_custom_target():
    cfg = {
        "model_mapping": {"gpt-5-mini": "claude-sonnet-custom"},
        "gemini_model_mapping": {"gpt-4.1-nano": "gemini-custom"},
    }
    migrated = ai_provider._migrate_config_models(cfg)
    assert migrated["model_mapping"]["gpt-5.6-terra"] == "claude-sonnet-custom"
    assert migrated["gemini_model_mapping"]["gpt-5.4-nano"] == "gemini-custom"


def test_no_deprecated_models_in_html_options():
    app_text = (ROOT / "app.py").read_text(encoding="utf-8")
    option_models = {
        value
        for value in re.findall(r'<option value="([^"]+)"', app_text)
        if value.startswith(("gpt-", "o1", "o3", "o4"))
    }
    assert option_models == CURRENT_CHAT_MODELS


def test_current_defaults_and_deployment_files():
    app_text = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "model_default = ai_provider.DEFAULT_OPENAI_MODEL" in app_text
    assert "model_upgrade = ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL" in app_text
    assert "DEFAULT_OPENAI_VISION_FALLBACK_MODEL" in app_text
    assert "model=TTS_MODEL" in app_text

    docker = (ROOT / "Dockerfile").read_bytes()
    assert b"\x00" not in docker
    docker_text = docker.decode("utf-8")
    assert '"--workers", "1"' in docker_text
    assert '"--threads", "4"' in docker_text
    assert '"--timeout", "180"' in docker_text


def test_openai_parameter_shaping():
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[], model=kwargs["model"], usage=None)

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )

    old_get_client = ai_provider._get_openai_client
    old_config = ai_provider._current_config
    old_mtime = ai_provider._last_config_mtime
    try:
        ai_provider._get_openai_client = lambda: fake_client
        ai_provider._current_config = deepcopy(ai_provider.DEFAULT_CONFIG)
        ai_provider._current_config["claude_features"]["output_translation_tag"] = False
        ai_provider._current_config["openai_features"]["flex_background"] = False
        ai_provider._last_config_mtime = 0

        ai_provider._chat_complete_openai(
            "gpt-5-mini",
            [{"role": "user", "content": "測試"}],
            max_tokens=321,
            temperature=0.2,
            top_p=0.9,
            seed=7,
            stop=["END"],
            logprobs=True,
        )
        shaped = calls[-1]
        assert shaped["model"] == "gpt-5.6-terra"
        assert shaped["max_completion_tokens"] == 321
        assert shaped["reasoning_effort"] == "none"
        for unsupported in ("max_tokens", "temperature", "top_p", "seed", "stop", "logprobs"):
            assert unsupported not in shaped

        ai_provider._chat_complete_openai(
            "gpt-4.1-mini",
            [{"role": "user", "content": "測試"}],
            max_tokens=111,
            temperature=0.1,
        )
        classic = calls[-1]
        assert classic["model"] == "gpt-4.1-mini"
        assert classic["max_tokens"] == 111
        assert classic["temperature"] == 0.1
        assert "reasoning_effort" not in classic
    finally:
        ai_provider._get_openai_client = old_get_client
        ai_provider._current_config = old_config
        ai_provider._last_config_mtime = old_mtime


def run_all():
    tests = [
        test_model_migrations,
        test_tts_migrations,
        test_unknown_admin_value_falls_back,
        test_saved_mapping_migration_preserves_custom_target,
        test_no_deprecated_models_in_html_options,
        test_current_defaults_and_deployment_files,
        test_openai_parameter_shaping,
    ]
    for test in tests:
        test()
    print(f"model refresh tests: OK ({len(tests)} tests)")


if __name__ == "__main__":
    run_all()
