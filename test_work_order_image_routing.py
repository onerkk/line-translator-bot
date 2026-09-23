"""One uploaded photo triggers exactly the selected group image workflow."""

from types import SimpleNamespace

import app
from test_work_order_query import PHOTO_5


def _background(monkeypatch, *, mode, ocr):
    sent = []
    monkeypatch.setattr(app, "download_line_image", lambda _id: ("ZmFrZQ==", b"\xff\xd8\xffbytes"))
    monkeypatch.setattr(app, "detect_image_mime", lambda _raw: "image/jpeg")
    monkeypatch.setattr(app, "show_loading", lambda _group: None)
    monkeypatch.setattr(app, "_event_log_write", lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "_delivery_checkpoint", lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "_complete_durable_image_job", lambda *_a, **_kw: True)
    monkeypatch.setattr(app, "_stats_inc", lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "track_group_usage", lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "store_work_order_media_context", lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "get_display_name", lambda *_a, **_kw: "工廠")
    monkeypatch.setattr(app, "get_user_picture_url", lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "get_sender_object", lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "_send_reply_with_push_fallback", lambda **kwargs: sent.append(kwargs) or (None, "reply"))
    ctx = {"group_id": "test-group", "user_id": "test-user", "message_id": "test-photo",
           "image_mode": mode, "durable_job_key": "test-job", "reply_token": "test-token",
           "quote_token": "test-quote", "mark_read_setting": False, "is_dm_img": False,
           "wo_setting": True}
    monkeypatch.setattr(app, "ocr_work_order_fields", lambda *_a, **_kw: ocr if mode == "work_order" else (_ for _ in ()).throw(AssertionError("wrong OCR")))
    monkeypatch.setattr(app, "ocr_image_openai", lambda *_a, **_kw: ocr if mode == "translate" else (_ for _ in ()).throw(AssertionError("wrong OCR")))
    return ctx, sent


def test_work_order_mode_reads_fields_and_does_not_translate_whole_photo(monkeypatch):
    ctx, sent = _background(monkeypatch, mode="work_order", ocr=PHOTO_5)
    monkeypatch.setattr(app, "translate", lambda text, src, tgt: "Peti kayu dan plastik pembungkus")
    monkeypatch.setattr(app, "_translate_with_thread_context", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("wrong translation route")))
    app._handle_image_background.__wrapped__(ctx)
    assert len(sent) == 1
    text = sent[0]["fallback_text"]
    assert "客戶 / Pelanggan：方鉦" in text
    assert "包裝代碼 / Kode kemasan：9G" in text
    assert "不噴 / Tidak perlu dicat" in text
    assert "不需套環 / Tidak perlu memakai cincin pelindung" in text
    assert "EH79" not in text  # length does not match the current storage data


def test_translate_mode_translates_even_when_photo_is_work_order(monkeypatch):
    ctx, sent = _background(monkeypatch, mode="translate", ocr=PHOTO_5)
    monkeypatch.setattr(app, "describe_scene_for_context", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("confirmed work order")))
    monkeypatch.setattr(app, "detect_language", lambda *_a: "zh")
    monkeypatch.setattr(app, "_translate_with_thread_context", lambda *_a, **_kw: "Hasil terjemahan")
    monkeypatch.setattr(app, "format_work_order_query", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("wrong lookup route")))
    monkeypatch.setattr(app, "format_storage_for_work_order", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("legacy intercept")))
    monkeypatch.setattr(app, "_store_image_overlay_context", lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "_build_image_translation_action_quick_reply", lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "_build_expression_visual_message", lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "_record_recent_group_message", lambda *_a, **_kw: None)
    app._handle_image_background.__wrapped__(ctx)
    assert len(sent) == 1
    assert "Hasil terjemahan" in sent[0]["fallback_text"]


def test_durable_retry_preserves_work_order_mode(monkeypatch):
    ctx, _sent = _background(monkeypatch, mode="work_order", ocr=PHOTO_5)
    pushed = []
    monkeypatch.setattr(app.translation_retry_queue_module, "checkpoint", lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "_translation_retry_push", lambda *_a: pushed.append(_a[-1]))
    monkeypatch.setattr(app, "_complete_durable_text_job", lambda *_a, **_kw: True)
    monkeypatch.setattr(app, "translate", lambda text, src, tgt: "Peti kayu dan plastik pembungkus")
    job = {"job_key": "test-job", "payload": {"message_id": ctx["message_id"],
           "group_id": ctx["group_id"], "user_id": ctx["user_id"], "image_mode": "work_order"}}
    assert app._translation_retry_image_attempt(job)
    assert len(pushed) == 1
    assert "客戶 / Pelanggan：方鉦" in pushed[0]
    assert "包裝代碼 / Kode kemasan：9G" in pushed[0]
