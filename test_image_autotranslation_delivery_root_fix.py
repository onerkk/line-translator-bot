from __future__ import annotations

from types import SimpleNamespace

import app
import pytest


REPLY_TEXT = (
    "@All 明天出貨客戶，再麻煩到站優先安排包裝。\n\n"
    "拋光、研磨注意一下，生產以本月急單為主，"
    "注意一下月底過機量跟工作效率。"
)
REPLY_TRANSLATION = (
    "@All Untuk pelanggan yang akan dikirim besok, mohon prioritaskan "
    "pengemasan setelah material tiba di stasiun."
)
OCR_TEXT = "製造指示書\n訂單編號：A123\n客戶名稱：測試客戶\n成品尺寸：10"
OCR_TRANSLATION = "Instruksi produksi untuk pesanan A123 milik pelanggan uji."


class DummyTextMessage:
    def __init__(self, text, **kwargs):
        self.text = text
        self.quick_reply = kwargs.get("quick_reply")
        self.sender = None
        self.quote_token = None


class DummyReplyRequest:
    def __init__(self, reply_token, messages):
        self.reply_token = reply_token
        self.messages = messages


class DummyPushRequest:
    def __init__(self, to, messages):
        self.to = to
        self.messages = messages


class DummyApiClient:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_oversized_all_mention_metadata_cannot_consume_reply_text():
    message = SimpleNamespace(
        mention=SimpleNamespace(mentionees=[SimpleNamespace(
            index=0,
            length=len(REPLY_TEXT),
            type="all",
            user_id=None,
        )])
    )

    normalized, mentions = app.normalize_line_mentions(REPLY_TEXT, message, "group-1")

    assert normalized == REPLY_TEXT
    assert mentions == ["@All"]
    assert not app.is_mention_only_or_name_call(normalized, mentions, "group-1")
    assert app.strip_mentions_for_detect(normalized, mentions).strip().startswith("明天出貨客戶")


def test_text_reply_to_image_enters_outbox_and_is_delivered(monkeypatch):
    group_id = "group-photo-reply"
    user_id = "user-supervisor"
    image_id = "image-quoted"
    text_id = "text-current"
    sent = []
    persisted = []
    translated_sources = []

    app._processed_msg_ids.clear()
    monkeypatch.setattr(app, "message_cache", {
        image_id: {
            "text": "圖片中的客戶儲位表",
            "ts": 1,
            "tr": {},
            "media_type": "image",
            "ocr_text": "圖片中的客戶儲位表",
            "scene": "",
        }
    })
    monkeypatch.setitem(app.group_tracking, group_id, {"name": "研磨班", "joined_at": 1})
    monkeypatch.setitem(app.group_settings, group_id, True)
    monkeypatch.setitem(app.group_skip_users, group_id, set())
    monkeypatch.setitem(app.group_target_lang, group_id, "id")
    monkeypatch.setattr(app, "record_user_name", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_group_feature", lambda *_a, **_k: False)
    monkeypatch.setattr(app, "get_group_target_langs", lambda _gid: ["id"])
    monkeypatch.setattr(app, "get_group_tone", lambda *_a, **_k: ("natural", ""))
    monkeypatch.setattr(app, "show_loading", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "mark_as_read", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "detect_language", lambda _text: "zh")
    monkeypatch.setattr(
        app,
        "translate_multi",
        lambda source, _src, _targets, _mentions: (
            translated_sources.append(source) or [("id", REPLY_TRANSLATION)]
        ),
    )
    monkeypatch.setattr(app, "format_multi_reply", lambda rows: "🇮🇩 " + rows[0][1])
    monkeypatch.setattr(app, "track_group_usage", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_stats_inc", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_tts_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr(app, "_record_recent_group_message", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_build_translation_action_quick_reply", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "build_quick_reply", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_build_expression_visual_message", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_display_name", lambda *_a, **_k: "小麥")
    monkeypatch.setattr(app, "get_user_picture_url", lambda *_a, **_k: "")
    monkeypatch.setattr(app, "get_sender_object", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_event_log_write", lambda *_a, **_k: None)
    monkeypatch.setattr(
        app,
        "_schedule_text_translation_retry",
        lambda ctx, **payload: persisted.append((ctx, payload)) or "outbox-key",
    )
    monkeypatch.setattr(app, "_complete_durable_text_job", lambda *_a, **_k: True)
    monkeypatch.setattr(
        app,
        "_send_reply_with_push_fallback",
        lambda **kwargs: sent.append(kwargs) or (None, "reply"),
    )

    message = SimpleNamespace(
        id=text_id,
        text=REPLY_TEXT,
        quote_token="quote-current",
        quoted_message_id=image_id,
        mark_as_read_token=None,
        mention=SimpleNamespace(mentionees=[SimpleNamespace(
            index=0,
            length=len(REPLY_TEXT),
            type="all",
            user_id=None,
        )]),
    )
    event = SimpleNamespace(
        message=message,
        source=SimpleNamespace(group_id=group_id, room_id=None, user_id=user_id),
        reply_token="reply-token",
        delivery_context=None,
    )

    app.handle_message(event)

    assert translated_sources == [REPLY_TEXT]
    assert len(persisted) == 1
    assert persisted[0][1]["source_text"] == REPLY_TEXT
    assert persisted[0][1]["quoted_context_message_id"] == image_id
    assert len(sent) == 1
    assert REPLY_TRANSLATION in sent[0]["message_obj"].text


def _run_image_background(
    monkeypatch,
    *,
    work_order_enabled,
    storage_reply,
):
    group_id = "group-image-route"
    message_id = "image-current"
    replies = []
    pushes = []
    translated = []
    completed = []

    class DummyMessagingApi:
        def __init__(self, *_args, **_kwargs):
            pass

        def reply_message(self, request, **_kwargs):
            replies.append(request)
            return SimpleNamespace(sent_messages=[])

        def push_message(self, request, **_kwargs):
            pushes.append(request)
            return SimpleNamespace(sent_messages=[])

    monkeypatch.setitem(app.group_wo_settings, group_id, work_order_enabled)
    monkeypatch.setitem(app.group_target_lang, group_id, "id")
    monkeypatch.setattr(app, "download_line_image", lambda _mid: ("base64", b"\xff\xd8image"))
    monkeypatch.setattr(app, "detect_image_mime", lambda _raw: "image/jpeg")
    monkeypatch.setattr(app, "ocr_image_openai", lambda *_a, **_k: OCR_TEXT)
    monkeypatch.setattr(app, "analyze_work_order", lambda _text: {
        "is_work_order": True,
        "customer": "測試客戶",
        "keyword_count": 4,
    })
    monkeypatch.setattr(app, "format_storage_for_work_order", lambda _customer: storage_reply)
    monkeypatch.setattr(app, "store_work_order_media_context", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "describe_scene_for_context", lambda *_a, **_k: "")
    monkeypatch.setattr(app, "store_media_scene", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_is_factory_reason_ocr_failure_text", lambda _text: False)
    monkeypatch.setattr(app, "detect_language", lambda _text: "zh")
    monkeypatch.setattr(app, "get_group_tone", lambda *_a, **_k: ("natural", ""))
    monkeypatch.setattr(
        app,
        "translate",
        lambda source, src, tgt: translated.append((source, src, tgt)) or OCR_TRANSLATION,
    )
    monkeypatch.setattr(app, "_store_image_overlay_context", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_build_image_translation_action_quick_reply", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_build_expression_visual_message", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "show_loading", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "mark_as_read", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "track_group_usage", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_record_recent_group_message", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_stats_inc", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_display_name", lambda *_a, **_k: "小麥")
    monkeypatch.setattr(app, "get_user_picture_url", lambda *_a, **_k: "")
    monkeypatch.setattr(app, "get_sender_object", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_event_log_write", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_send_background_failure_notice", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_schedule_text_translation_retry", lambda *_a, **_k: True)
    monkeypatch.setattr(
        app,
        "_complete_durable_image_job",
        lambda ctx: completed.append(ctx) or True,
    )
    monkeypatch.setattr(app, "ApiClient", DummyApiClient)
    monkeypatch.setattr(app, "MessagingApi", DummyMessagingApi)
    monkeypatch.setattr(app, "TextMessage", DummyTextMessage)
    monkeypatch.setattr(app, "ReplyMessageRequest", DummyReplyRequest)
    monkeypatch.setattr(app, "PushMessageRequest", DummyPushRequest)

    app._handle_image_background({
        "message_id": message_id,
        "reply_token": "reply-token",
        "quote_token": "quote-token",
        "mark_as_read_token": None,
        "group_id": group_id,
        "user_id": "user-supervisor",
        "tgt": "id",
        "tone_info": ("natural", ""),
        "wo_setting": work_order_enabled,
        "mark_read_setting": False,
        "is_dm_img": False,
        "durable_job_key": "durable-image-key",
    })

    messages = [msg for request in replies + pushes for msg in request.messages]
    return messages, translated, completed


def test_disabled_work_order_feature_falls_through_to_ocr_translation(monkeypatch):
    messages, translated, completed = _run_image_background(
        monkeypatch,
        work_order_enabled=False,
        storage_reply="📋 工單偵測結果",
    )

    assert translated == [(OCR_TEXT, "zh", "id")]
    assert any(OCR_TRANSLATION in message.text for message in messages)
    assert all("工單偵測結果" not in message.text for message in messages)
    assert len(completed) == 1


def test_missing_work_order_lookup_falls_through_to_ocr_translation(monkeypatch):
    messages, translated, completed = _run_image_background(
        monkeypatch,
        work_order_enabled=True,
        storage_reply=None,
    )

    assert translated == [(OCR_TEXT, "zh", "id")]
    assert any(OCR_TRANSLATION in message.text for message in messages)
    assert len(completed) == 1


def test_delivered_work_order_reply_is_the_only_valid_translation_bypass(monkeypatch):
    messages, translated, completed = _run_image_background(
        monkeypatch,
        work_order_enabled=True,
        storage_reply="📋 工單偵測結果",
    )

    assert translated == []
    assert [message.text for message in messages] == ["📋 工單偵測結果"]
    assert len(completed) == 1


def test_durable_image_payload_preserves_disabled_work_order_setting(monkeypatch):
    captured = []
    monkeypatch.setattr(
        app.translation_retry_queue_module,
        "enqueue",
        lambda key, payload, **kwargs: captured.append((key, payload, kwargs)) or True,
    )
    monkeypatch.setattr(app, "_ensure_translation_retry_worker", lambda: True)
    monkeypatch.setattr(app, "_event_log_write", lambda *_a, **_k: None)

    key = app._schedule_image_translation_retry({
        "message_id": "image-setting",
        "group_id": "group-setting",
        "user_id": "user-setting",
        "tgt": "id",
        "wo_setting": False,
    })

    try:
        assert key == "group-setting:image-setting:image"
        assert captured[0][1]["wo_setting"] is False
    finally:
        app._TRANSLATION_RETRY_INFLIGHT.discard(key)


def test_retry_does_not_replace_failed_work_order_delivery_with_generic_translation(monkeypatch):
    translated = []
    monkeypatch.setattr(app, "download_line_image", lambda _mid: ("base64", b"\xff\xd8image"))
    monkeypatch.setattr(app, "detect_image_mime", lambda _raw: "image/jpeg")
    monkeypatch.setattr(app, "ocr_image_openai", lambda *_a, **_k: OCR_TEXT)
    monkeypatch.setattr(app, "_is_factory_reason_ocr_failure_text", lambda _text: False)
    monkeypatch.setattr(app, "get_group_tone", lambda *_a, **_k: ("natural", ""))
    monkeypatch.setattr(app, "analyze_work_order", lambda _text: {
        "is_work_order": True,
        "customer": "測試客戶",
        "keyword_count": 4,
    })
    monkeypatch.setattr(app, "store_work_order_media_context", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "format_storage_for_work_order", lambda _customer: "📋 工單偵測結果")
    monkeypatch.setattr(
        app,
        "_translation_retry_push",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("LINE unavailable")),
    )
    monkeypatch.setattr(
        app,
        "translate",
        lambda *_a, **_k: translated.append(True) or OCR_TRANSLATION,
    )

    with pytest.raises(RuntimeError, match="LINE unavailable"):
        app._translation_retry_image_attempt({
            "job_key": "group-retry:image-retry:image",
            "attempts": 1,
            "payload": {
                "job_kind": "image",
                "message_id": "image-retry",
                "group_id": "group-retry",
                "user_id": "user-retry",
                "tgt": "id",
                "wo_setting": True,
            },
        }, lease_owner="worker-1")

    assert translated == []
