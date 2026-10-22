"""Root fix 2026-10-22: a protected name after a Latin @mention leaked a raw
``__QG_KEEP_*__`` token into the LINE reply.

Reproduction from the 2026-10-07 screenshot review (``@budi santoso 山多 I5已發料成功``):
``山多`` is a protected name, so the privacy layer masks it; the textual mention
parser then swallowed the mask into ``@budi santoso __QG_KEEP_900_...__``, the
provider never saw the token, and ``restore_mentions`` wrote the raw placeholder
back into the delivered translation.

All provider responses here are fixed test data; nothing measures model quality.
"""
import re
from types import SimpleNamespace

import pytest

import app
import translation_mentions as mentions
import translation_privacy as privacy
import translation_request_guard


def _response(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10, total_tokens=20),
        model="offline", _jy_provider="openai",
    )


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    monkeypatch.setattr(app, "translation_cache", {})
    if hasattr(translation_request_guard, "_context_results"):
        translation_request_guard._context_results.clear()
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(previous)


@pytest.mark.parametrize("text, expected", [
    ("@budi santoso __QG_KEEP_900_DBB44B60__ I5已發料成功", ["@budi santoso"]),
    ("@蘇比 sobirin __QG_KEEP_901_AAAAAAAA__ tolong cek", ["@蘇比 sobirin"]),
    ("@(杰弗) __MENTION_1__ sudah", ["@(杰弗)"]),
    ("__QG_KEEP_900_DBB44B60__ @小麥 cek", ["@小麥"]),
    ("@All __QG_KEEP_900_DBB44B60__ harap", ["@All"]),
])
def test_textual_mention_never_absorbs_pipeline_placeholders(text, expected):
    assert mentions.extract_mentions(text) == expected


def test_mention_parser_keeps_real_latin_display_names():
    assert mentions.extract_mentions("@budi santoso 山多 I5") == ["@budi santoso"]
    assert mentions.extract_mentions("@蘇比 sobirin jika ada") == ["@蘇比 sobirin"]


def _run(monkeypatch, source, body):
    sent = []

    def capture(**kwargs):
        user = kwargs["messages"][-1]["content"]
        sent.append(user)
        echoed = re.findall(r"__MENTION_\d+__|__QG_KEEP_\d{3}_[0-9A-F]{8}__", user)
        return _response(" ".join(echoed) + " " + body)

    monkeypatch.setattr(app.ai.chat.completions, "create", capture)
    app._tl.semantic_contract = app.build_translation_semantic_contract(source, "zh", "id")
    result = app.translate_openai(source, "zh", "id")
    return sent[0], result


def test_protected_name_after_latin_mention_is_restored_not_leaked(monkeypatch):
    monkeypatch.setattr(app, "collect_visible_protected_names", lambda text: ["山多"] if "山多" in text else [])
    user_prompt, result = _run(monkeypatch, "@budi santoso 山多 I5已發料成功",
                               "I5 sudah berhasil mengubah status data menjadi OL.")
    # The privacy placeholder stays in the translatable text for the provider…
    assert "__MENTION_0__ __QG_KEEP_" in user_prompt
    # …and the delivered text carries neither placeholder family.
    assert result == "@budi santoso 山多 I5 sudah berhasil mengubah status data menjadi OL."
    assert "__QG_KEEP_" not in result and "__MENTION_" not in result


def test_restore_order_guard_when_mention_literal_still_carries_mask():
    # Even if a mention literal is built from masked text elsewhere, the final
    # restore pass in translate_openai must unmask it. Exercise the primitive.
    envelope = privacy.mask_sensitive_text("@budi santoso 山多 x", extra_literals=["山多"])
    placeholder = next(iter(envelope.mapping))
    literal = "@budi santoso " + placeholder
    restored = app.restore_mentions("__MENTION_0__ sudah", {"__MENTION_0__": literal})
    assert placeholder in restored
    assert privacy.restore_sensitive_text(restored, envelope) == "@budi santoso 山多 sudah"
