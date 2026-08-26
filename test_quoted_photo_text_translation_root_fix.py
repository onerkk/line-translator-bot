from __future__ import annotations

from types import SimpleNamespace

import app


REPLY_TEXT = (
    "@All 明天出貨客戶，再麻煩到站優先安排包裝。\n\n"
    "拋光、研磨注意一下，生產以本月急單為主，"
    "注意一下月底過機量跟工作效率。"
)
REPLY_TRANSLATION = (
    "@All Untuk pelanggan yang akan dikirim besok, mohon prioritaskan "
    "pengemasan setelah material tiba di stasiun."
)


def _oversized_all_mention():
    return SimpleNamespace(
        mentionees=[SimpleNamespace(
            index=0,
            length=len(REPLY_TEXT),
            type="all",
            user_id=None,
        )]
    )


def test_oversized_all_mention_metadata_cannot_consume_current_text():
    message = SimpleNamespace(mention=_oversized_all_mention())

    normalized, mentions = app.normalize_line_mentions(REPLY_TEXT, message, "group-1")

    assert normalized == REPLY_TEXT
    assert mentions == ["@All"]
    assert not app.is_mention_only_or_name_call(normalized, mentions, "group-1")
    assert app.strip_mentions_for_detect(normalized, mentions).strip().startswith(
        "明天出貨客戶"
    )


def test_image_translation_off_then_quoted_photo_text_is_translated(monkeypatch):
    group_id = "group-photo-reply"
    user_id = "user-supervisor"
    image_id = "image-quoted"
    text_id = "text-current"
    sent = []
    persisted = []
    translated_sources = []

    app._processed_msg_ids.clear()
    monkeypatch.setattr(app, "message_cache", {})
    monkeypatch.setitem(app.group_tracking, group_id, {"name": "研磨班", "joined_at": 1})
    monkeypatch.setitem(app.group_settings, group_id, True)
    monkeypatch.setitem(app.group_skip_users, group_id, set())
    monkeypatch.setitem(app.group_target_lang, group_id, "id")
    monkeypatch.setitem(app.group_img_settings, group_id, False)
    monkeypatch.setitem(app.group_img_ask_settings, group_id, False)
    monkeypatch.setattr(app, "record_user_name", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_group_feature", lambda *_a, **_k: False)
    monkeypatch.setattr(app, "get_group_target_langs", lambda _gid: ["id"])
    monkeypatch.setattr(app, "get_group_tone", lambda *_a, **_k: ("natural", ""))
    monkeypatch.setattr(app, "show_loading", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "mark_as_read", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "detect_language", lambda _text: "zh")
    monkeypatch.setattr(app, "_event_log_write", lambda *_a, **_k: None)
    monkeypatch.setattr(
        app,
        "_has_ai_capability",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("disabled image translation must not enter OCR/vision")
        ),
    )

    image_event = SimpleNamespace(
        message=SimpleNamespace(id=image_id),
        source=SimpleNamespace(group_id=group_id, room_id=None, user_id=user_id),
        reply_token="image-reply-token",
        delivery_context=None,
    )
    app.handle_image(image_event)

    assert app.message_cache[image_id]["media_type"] == "image"
    assert app.message_cache[image_id]["ocr_text"] == ""

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
    monkeypatch.setattr(
        app, "_build_translation_action_quick_reply", lambda *_a, **_k: None
    )
    monkeypatch.setattr(app, "build_quick_reply", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_build_expression_visual_message", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_display_name", lambda *_a, **_k: "小麥")
    monkeypatch.setattr(app, "get_user_picture_url", lambda *_a, **_k: "")
    monkeypatch.setattr(app, "get_sender_object", lambda *_a, **_k: None)
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

    text_event = SimpleNamespace(
        message=SimpleNamespace(
            id=text_id,
            text=REPLY_TEXT,
            quote_token="quote-current",
            quoted_message_id=image_id,
            mark_as_read_token=None,
            mention=_oversized_all_mention(),
        ),
        source=SimpleNamespace(group_id=group_id, room_id=None, user_id=user_id),
        reply_token="text-reply-token",
        delivery_context=None,
    )

    app.handle_message(text_event)

    assert translated_sources == [REPLY_TEXT]
    assert len(persisted) == 1
    assert persisted[0][1]["source_text"] == REPLY_TEXT
    assert persisted[0][1]["quoted_context_message_id"] == image_id
    assert len(sent) == 1
    assert REPLY_TRANSLATION in sent[0]["message_obj"].text
