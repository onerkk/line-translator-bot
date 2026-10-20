"""2026-10-20 root fixes: Claude 5.5 / GPT-6 contract, prompt wrapping, cache split, tone.

Offline only: provider clients are fakes; nothing here measures model quality.
"""
import types

import pytest

import ai_provider
import translation_extras as extras

SOURCE = "@蘇比 sobirin\n蘇比 你今日體檢沒有採集檢，10/21(三)09:00 司機會抽空去收(罐子已給)，請記得先準備好放陰涼處等司機，謝謝"
TARGET = ("蘇比, pemeriksaan kesehatan hari ini belum mengambil sampel. Pada 10/21 (Rabu) pukul 09:00 "
          "sopir akan meluangkan waktu untuk mengambilnya (wadahnya sudah diberikan). Harap siapkan "
          "terlebih dahulu dan letakkan di tempat yang sejuk, lalu tunggu sopir. Terima kasih.")
DIRECT_PROMPT = (
    "<role>Translator for a Taiwan stainless-steel factory chat.</role>\n"
    "<translation_principles>\nDirection: zh->id. Output only one final translation.\n"
    + "Preserve facts. " * 120 +
    "</translation_principles>\n"
    "<translation_preset>Tone: factory preset " + "rule. " * 200 + "</translation_preset>\n"
    "<translation_style>Tone: AUTOMATIC TONE ANALYSIS: dominant_intent=instruction</translation_style>\n"
    "<output_format>Return only the complete target text in the original layout.</output_format>"
)


# ─── model id migration ────────────────────────────────────────────────────

@pytest.mark.parametrize("old,new", [
    ("claude-haiku-4-5-20251001", "claude-haiku-5-5"),
    ("claude-haiku-4-5", "claude-haiku-5-5"),
    ("claude-sonnet-5", "claude-sonnet-5-5"),
    ("claude-sonnet-4-6", "claude-sonnet-5-5"),
    ("claude-haiku-5-5", "claude-haiku-5-5"),
    ("claude-opus-5-5", "claude-opus-5-5"),
])
def test_retired_claude_ids_migrate_forward(old, new):
    assert ai_provider.normalize_anthropic_model(old) == new
    assert ai_provider._resolve_anthropic_model(old) == new


@pytest.mark.parametrize("old,new", [
    ("gpt-5.6-luna", "gpt-6-luna"),
    ("gpt-5.6-terra", "gpt-6.1-sol"),
    ("gpt-5.6-sol", "gpt-6.1-sol"),
    ("gpt-5.4-nano", "gpt-6-luna"),
    ("gpt-5-mini", "gpt-6.1-sol"),
    ("gpt-6-luna", "gpt-6-luna"),
])
def test_openai_tiers_migrate_to_gpt_6(old, new):
    assert ai_provider.normalize_translation_model(old) == new


def test_cross_provider_tier_mapping_lands_on_claude_5_5():
    ai_provider._ensure_initialized()
    assert ai_provider._resolve_anthropic_model("gpt-6-luna") == "claude-haiku-5-5"
    assert ai_provider._resolve_anthropic_model("gpt-6.1-sol") == "claude-sonnet-5-5"
    assert ai_provider._resolve_anthropic_model("gpt-5.6-terra") == "claude-sonnet-5-5"


def test_saved_legacy_config_is_migrated_on_load(tmp_path, monkeypatch):
    cfg = {"anthropic": {"api_key": "", "default_model": "claude-haiku-4-5-20251001"},
           "model_mapping": {"gpt-5.6-luna": "claude-haiku-4-5-20251001"}}
    ai_provider._migrate_config_models(cfg)
    assert cfg["anthropic"]["default_model"] == "claude-haiku-5-5"
    assert all(not v.startswith("claude-haiku-4-5") for v in cfg["model_mapping"].values())


# ─── Claude 5.5 request contract ───────────────────────────────────────────

def _anthropic_call(monkeypatch, model, system, reasoning_effort=None, fast_quality=False,
                    features=None, user=SOURCE):
    recorder = []
    def create(**kw):
        recorder.append(kw)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text=TARGET, citations=None)],
            usage=types.SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                                        cache_creation_input_tokens=0, cache_creation=None),
            stop_reason="end_turn")
    client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    monkeypatch.setattr(ai_provider, "_get_anthropic_client", lambda: client)
    monkeypatch.setattr(ai_provider, "_client_with_limits", lambda c, _t: c)
    ai_provider._ensure_initialized()
    if features:
        monkeypatch.setitem(ai_provider._current_config, "claude_features",
                            {**ai_provider._current_config.get("claude_features", {}), **features})
    ai_provider._chat_complete_anthropic(
        model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
        200, temperature=0.2, reasoning_effort=reasoning_effort, fast_quality=fast_quality)
    assert len(recorder) == 1
    return recorder[0]


def test_haiku_5_5_request_never_sends_sampling_params_or_prefill(monkeypatch):
    kw = _anthropic_call(monkeypatch, "claude-haiku-5-5", DIRECT_PROMPT, reasoning_effort="none",
                         features={"assistant_prefill": True, "assistant_prefill_text": "翻譯:"})
    assert kw["model"] == "claude-haiku-5-5"
    for forbidden in ("temperature", "top_p", "top_k"):
        assert forbidden not in kw
    assert kw["messages"][-1]["role"] == "user"
    assert kw["thinking"] == {"type": "disabled"}
    assert kw["output_config"] == {"effort": "low"}


@pytest.mark.parametrize("effort,thinking,level", [
    ("none", "disabled", "low"),
    ("low", "adaptive", "low"),
    ("medium", "adaptive", "medium"),
])
def test_app_reasoning_effort_reaches_claude_5_5(monkeypatch, effort, thinking, level):
    kw = _anthropic_call(monkeypatch, "claude-haiku-5-5", DIRECT_PROMPT, reasoning_effort=effort)
    assert kw["thinking"]["type"] == thinking
    assert kw["output_config"]["effort"] == level


def test_direct_prompt_is_not_wrapped_a_second_time(monkeypatch):
    kw = _anthropic_call(monkeypatch, "claude-haiku-5-5", DIRECT_PROMPT, reasoning_effort="none")
    system = "".join(b["text"] for b in kw["system"])
    assert system.count("<role>") == 1
    assert system.count("<output_format") == 1
    for stale in ("<thinking_protocol>", "<success_criteria>", "<rules>", "<task>", "<glossary_priority>"):
        assert stale not in system


def test_stable_prefix_includes_group_preset_and_dynamic_tone_is_uncached(monkeypatch):
    kw = _anthropic_call(monkeypatch, "claude-haiku-5-5", DIRECT_PROMPT, reasoning_effort="none")
    cached = [b for b in kw["system"] if b.get("cache_control")]
    uncached = [b for b in kw["system"] if not b.get("cache_control")]
    assert len(cached) == 1 and cached[0]["text"].endswith("</translation_preset>")
    assert "AUTOMATIC TONE ANALYSIS" not in cached[0]["text"]
    assert uncached and "AUTOMATIC TONE ANALYSIS" in uncached[0]["text"]


def test_haiku_5_5_cache_threshold_is_512_tokens():
    assert ai_provider._get_cache_min_tokens("claude-haiku-5-5") == 512
    assert ai_provider._get_cache_min_tokens("claude-sonnet-5-5") == 1024
    assert not ai_provider._supports_prefill("claude-haiku-5-5")
    assert not ai_provider._supports_prefill("claude-sonnet-5-5")


def test_stop_sequences_follow_output_language(monkeypatch):
    kw = _anthropic_call(monkeypatch, "claude-haiku-5-5", DIRECT_PROMPT, reasoning_effort="none")
    assert kw["stop_sequences"][0] == "\nCatatan:"
    assert len(kw["stop_sequences"]) <= 4
    zh = DIRECT_PROMPT.replace("Direction: zh->id", "Direction: id->zh")
    kw = _anthropic_call(monkeypatch, "claude-haiku-5-5", zh, reasoning_effort="none")
    assert kw["stop_sequences"][0] == "\n註:"


def test_glossary_grounding_ignores_terms_mentioned_only_by_the_system_prompt(monkeypatch):
    term = "出料狀況"
    monkeypatch.setattr(ai_provider, "_registered_glossary",
                        {term: {"id": "kondisi hasil produksi mesin", "mode": "hard"}})
    matched = ai_provider._find_relevant_glossary_terms(
        [{"role": "system", "content": "rule about " + term}, {"role": "user", "content": "今天加班"}])
    assert all(t != term for t, _ in matched)
    matched = ai_provider._find_relevant_glossary_terms(
        [{"role": "system", "content": "x"}, {"role": "user", "content": term + "要確認"}])
    assert any(t == term for t, _ in matched)


def test_older_claude_models_keep_their_existing_contract(monkeypatch):
    kw = _anthropic_call(monkeypatch, "claude-opus-4-7", DIRECT_PROMPT, reasoning_effort="none")
    assert kw["model"] == "claude-opus-4-7"
    assert "thinking" not in kw  # extended_thinking off by default → legacy path unchanged


# ─── app routing ───────────────────────────────────────────────────────────

def test_short_risky_message_stays_on_routine_tier_with_more_effort(monkeypatch):
    app = pytest.importorskip("app")
    monkeypatch.setattr(app, "model_threshold", 150)
    monkeypatch.setattr(app, "_translation_needs_upgrade_model", lambda _t: True)
    app._tl.__dict__.pop("force_model", None)
    assert app.pick_model("請記得先準備好") == ai_provider.DEFAULT_OPENAI_MODEL
    assert getattr(app._tl, "translation_effort_boost", False) is True
    monkeypatch.setattr(app, "_translation_needs_upgrade_model", lambda _t: False)
    assert app.pick_model("今天加班") == ai_provider.DEFAULT_OPENAI_MODEL
    assert getattr(app._tl, "translation_effort_boost", True) is False
    assert app.pick_model("長公告" * 100) == ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL


def test_gpt_6_family_uses_reasoning_none_for_routine_translation():
    app = pytest.importorskip("app")
    assert app.optimal_reasoning_for_translation("gpt-6-luna") == "none"
    assert app.optimal_reasoning_for_translation("gpt-6.1-sol") == "none"


# ─── tone / decoration ─────────────────────────────────────────────────────

def test_trailing_thanks_does_not_make_an_instruction_a_gratitude_message():
    analysis = extras.analyze_message_tone(SOURCE.split("\n", 1)[1], "zh")
    assert analysis.primary == "instruction"
    assert "gratitude" not in extras.build_tone_prompt_instruction(analysis)


@pytest.mark.parametrize("text", ["謝謝你", "謝謝大家，辛苦了", "Terima kasih ya", "做得好，謝謝"])
def test_pure_thanks_is_still_gratitude(text):
    assert extras.analyze_message_tone(text, "zh").primary == "gratitude"


def test_expanded_sentences_get_at_most_one_marker_and_no_heart():
    decorated = extras.enrich_translation_with_tone_emoji(
        SOURCE.split("\n", 1)[1], TARGET, source_language="zh", intensity="natural")
    assert "💛" not in decorated and "😊" not in decorated
    assert decorated.endswith("Terima kasih. 🙏")
    body = decorated.rsplit("Terima kasih.", 1)[0]
    assert sum(body.count(e) for e in ("📌", "👀", "✅", "🙏")) <= 1
    assert decorated.replace(" 📌", "").replace(" 🙏", "") == TARGET


def test_gratitude_palette_has_no_affection_emoji():
    for name, _patterns, _instr, emoji, *_rest in extras._TONE_RULES:
        if name == "gratitude":
            assert "💛" not in emoji and "🥰" not in emoji


# ─── vision cost on Haiku 5.5 high-resolution tier ─────────────────────────

def test_large_images_are_bounded_to_the_previous_1568px_tier_before_claude():
    import base64, io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (2400, 1800), "white").save(buf, format="JPEG")
    big = base64.b64encode(buf.getvalue()).decode("ascii")
    blocks = ai_provider._convert_openai_content_blocks_to_anthropic(
        [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + big}}, {"type": "text", "text": "讀"}])
    image = next(b for b in blocks if b["type"] == "image")
    decoded = Image.open(io.BytesIO(base64.b64decode(image["source"]["data"])))
    assert max(decoded.size) == 1568 and decoded.size == (1568, 1176)
    buf = io.BytesIO()
    Image.new("RGB", (1200, 900), "white").save(buf, format="PNG")
    small = base64.b64encode(buf.getvalue()).decode("ascii")
    blocks = ai_provider._convert_openai_content_blocks_to_anthropic(
        [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + small}}])
    assert blocks[0]["source"]["data"] == small and blocks[0]["source"]["media_type"] == "image/png"
