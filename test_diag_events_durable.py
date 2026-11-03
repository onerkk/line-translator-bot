"""Diagnostics for untranslated messages survive redeploys (Upstash) and show Taiwan time."""
import json

import app


def test_failed_and_skipped_outcomes_are_kept_durably(monkeypatch):
    store = []
    monkeypatch.setattr(app, "_kv_enabled", lambda: True)
    def fake_kv(args, timeout=8):
        if args[0] == "LPUSH":
            store.insert(0, args[2])
        elif args[0] == "LRANGE":
            return list(store)
        return 1
    monkeypatch.setattr(app, "_kv_command", fake_kv)
    app._diag_kv_append("translation_outcome", {"status": "failed", "reason": "x", "text": "長" * 500})
    app._diag_kv_append("text_skipped", {"reason": "user_in_skip_list", "group_id": "G"})
    entries = app._diag_kv_read(10)
    assert [e["type"] for e in entries] == ["text_skipped", "translation_outcome"]
    assert len(entries[1]["data"]["text"]) == 160          # previews stay short
    assert app._diag_event_wanted("delivery_failed") and not app._diag_event_wanted("webhook_in")


def test_non_delivered_outcome_emits_a_diagnostic_event(monkeypatch):
    seen = []
    monkeypatch.setattr(app, "_event_log_write", lambda t, d: seen.append((t, d)))
    app._tl.group_id = "Cx"
    app._set_translation_outcome("failed", "empty_or_rejected_translation")
    app._set_translation_outcome("delivered", "")
    assert seen == [("translation_outcome", {"status": "failed", "reason": "empty_or_rejected_translation", "group_id": "Cx"})]


def test_times_are_shown_in_taiwan_time():
    assert app._taiwan_time_str(0) == "01/01 08:00:00"


def test_diag_page_requires_admin_key_and_renders(monkeypatch):
    monkeypatch.setattr(app, "ADMIN_KEY", "k")
    monkeypatch.setattr(app, "_kv_enabled", lambda: True)
    monkeypatch.setattr(app, "_diag_kv_read", lambda n: [{"ts": 0, "type": "translation_outcome", "data": {"reason": "<x>"}}])
    client = app.app.test_client()
    assert client.get("/debug/diag-events").status_code == 403
    page = client.get("/debug/diag-events?key=k").get_data(as_text=True)
    assert "01/01 08:00:00" in page and "&lt;x&gt;" in page


def test_legacy_translation_card_alt_text_is_clipped():
    msg = app.build_translation_flex("原文", "x" * 900, "🇹🇼", "🇮🇩")
    assert msg is not None and len(msg.alt_text) <= 400
