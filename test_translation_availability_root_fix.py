import types

import app
import factory_translation_policy as policy


SCREENSHOT_SOURCE = (
    "@All Harap perhatikan personel pemoles. Sebelum produksi, periksa apakah ada "
    "kebutuhan untuk catatan khusus. Jika Anda tidak mengerti, tanyakan monitor sekarang."
)
SCREENSHOT_CANDIDATE = (
    "@All 請研磨人員注意。生產前，請確認是否有特殊註記需求。"
    "不清楚的話，現在就詢問現場負責人。"
)


def test_legacy_fail_closed_environment_no_longer_blocks_delivery(monkeypatch):
    monkeypatch.setenv("FACTORY_TRANSLATION_FAIL_CLOSED", "1")
    monkeypatch.delenv("FACTORY_BLOCK_UNVERIFIED_DELIVERY", raising=False)

    assert policy.require_verified_for_cache("id", "zh") is True
    assert policy.fail_closed("id", "zh") is False
    assert policy.block_unverified_delivery("id", "zh") is False


def test_screenshot_message_is_delivered_even_when_factory_guard_disagrees(monkeypatch):
    bad_report = types.SimpleNamespace(
        ok=False,
        issues=["ambiguous_reverse_glossary:monitor"],
        hard_issues=["ambiguous_reverse_glossary:monitor"],
    )
    monkeypatch.setattr(app, "_factory_guard_report", lambda *_a, **_k: bad_report)
    monkeypatch.setattr(app, "_factory_exact_fallback", lambda *_a, **_k: None)
    monkeypatch.setattr(
        app.tqg_module,
        "ensure_delivery_safe_translation",
        lambda *_a, **_k: {
            "ok": False,
            "text": SCREENSHOT_CANDIDATE,
            "issues": ["ambiguous_reverse_glossary:monitor"],
        },
    )

    actual = app._final_delivery_guard(
        SCREENSHOT_SOURCE,
        SCREENSHOT_CANDIDATE,
        "id",
        "zh",
    )

    assert actual == SCREENSHOT_CANDIDATE
    assert "無法" not in actual
    assert "belum dapat diterjemahkan" not in actual.lower()
    assert getattr(app._tl, "delivery_degraded", False) is True


def test_validation_failure_still_prevents_cache_pollution(monkeypatch):
    monkeypatch.setattr(app, "is_translation_acceptable", lambda *_a, **_k: False)
    monkeypatch.setattr(app, "translation_cache", {})

    app.cache_set(SCREENSHOT_SOURCE, "id", "zh", SCREENSHOT_CANDIDATE)

    assert app.translation_cache == {}


def test_empty_primary_pipeline_uses_emergency_nmt_and_does_not_emit_failure(monkeypatch):
    monkeypatch.setattr(app, "_translate_core", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "translate_google", lambda *_a, **_k: SCREENSHOT_CANDIDATE)
    monkeypatch.setattr(app.nmt_module, "nmt_translate", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_factory_exact_fallback", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_auto_tone_emoji_enabled", lambda *_a, **_k: False)

    actual = app.translate(SCREENSHOT_SOURCE, "id", "zh")

    assert actual
    assert SCREENSHOT_CANDIDATE in actual
    assert "暫時無法完成安全翻譯" not in actual
    assert "belum dapat diterjemahkan dengan aman" not in actual.lower()
    assert app._get_translation_outcome()["status"] == "delivered"



def test_all_broadcast_mention_never_swallows_following_indonesian_words():
    protected, mapping = app.protect_mentions(SCREENSHOT_SOURCE)

    assert protected.startswith("__MENTION_0__ Harap perhatikan")
    assert mapping == {"__MENTION_0__": "@All"}


def test_emergency_fallback_preserves_exact_all_boundary(monkeypatch):
    monkeypatch.setattr(app, "_translate_core", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "translate_google", lambda *_a, **_k: SCREENSHOT_CANDIDATE)
    monkeypatch.setattr(app.nmt_module, "nmt_translate", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_factory_exact_fallback", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_auto_tone_emoji_enabled", lambda *_a, **_k: False)

    actual = app.translate(SCREENSHOT_SOURCE, "id", "zh")

    assert actual.count("@All") == 1
    assert "Harap perhatikan" not in actual
    assert actual.startswith("@All 請研磨人員注意")

def test_policy_prompt_forbids_model_generated_failure_notices():
    prompt = policy.build_prompt("請注意研磨人員", "zh", "id")
    assert "Never output an apology" in prompt
    assert "translation-failure notice" in prompt
    assert "must not be described to the user" in prompt


def test_detached_retry_pushes_translation_without_requesting_resend(monkeypatch):
    pushed = []

    class ImmediateThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    class DummyApiClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class DummyMessagingApi:
        def __init__(self, *_args, **_kwargs):
            pass

        def push_message(self, request):
            pushed.append(request)

    class DummyTextMessage:
        def __init__(self, text):
            self.text = text
            self.quote_token = None

    class DummyPushRequest:
        def __init__(self, to, messages):
            self.to = to
            self.messages = messages

    monkeypatch.setattr(app.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(app.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "translate", lambda *_a, **_k: "請研磨人員注意。")
    monkeypatch.setattr(app, "ApiClient", DummyApiClient)
    monkeypatch.setattr(app, "MessagingApi", DummyMessagingApi)
    monkeypatch.setattr(app, "TextMessage", DummyTextMessage)
    monkeypatch.setattr(app, "PushMessageRequest", DummyPushRequest)
    monkeypatch.setattr(app, "_event_log_write", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_stats_inc", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "message_cache", {})

    scheduled = app._schedule_text_translation_retry(
        {
            "group_id": "group-1",
            "message_id": "message-1",
            "quote_token": "quote-1",
        },
        source_text=SCREENSHOT_SOURCE,
        src_lang="id",
        target_langs=["zh"],
        line_mentions=[],
    )

    assert scheduled is True
    assert len(pushed) == 1
    assert pushed[0].to == "group-1"
    assert "請研磨人員注意" in pushed[0].messages[0].text
    assert "重傳" not in pushed[0].messages[0].text
    assert app._TRANSLATION_RETRY_INFLIGHT == set()


def test_emergency_fallback_skips_invalid_first_nmt_and_uses_next_provider(monkeypatch):
    calls = []

    def configured(*_a, **_k):
        calls.append("configured")
        return "禁止 masuk。"

    def public(*_a, **_k):
        calls.append("public")
        return "禁止進入。"

    monkeypatch.setattr(app.nmt_module, "nmt_translate", configured)
    monkeypatch.setattr(app, "translate_google", public)
    monkeypatch.setattr(app, "_factory_exact_fallback", lambda *_a, **_k: None)

    actual = app._emergency_translation_fallback(
        "Jangan masuk.", "id", "zh"
    )

    assert actual == "禁止進入。"
    assert calls == ["configured", "public"]


def test_objective_primary_corruption_uses_emergency_route_before_returning_empty(monkeypatch):
    monkeypatch.setattr(app, "_translate_core", lambda *_a, **_k: "禁止 masuk。")
    monkeypatch.setattr(app.nmt_module, "nmt_translate", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "translate_google", lambda *_a, **_k: "禁止進入。")
    monkeypatch.setattr(app, "_factory_exact_fallback", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_auto_tone_emoji_enabled", lambda *_a, **_k: False)

    actual = app.translate("Jangan masuk.", "id", "zh")

    assert actual == "禁止進入。"
    assert "masuk" not in actual
    assert app._get_translation_outcome()["status"] == "delivered"


def test_exact_screenshot_failure_payload_is_purged_as_legacy_sentinel():
    old_payload = (
        "⚠️ 這則訊息暫時無法完成安全翻譯，系統已記錄，請稍後重傳。\n"
        "Pesan ini belum dapat diterjemahkan dengan aman. "
        "Sistem sudah mencatatnya; silakan kirim ulang nanti."
    )
    assert app._is_translation_failure_sentinel(old_payload) is True
