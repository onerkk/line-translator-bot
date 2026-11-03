"""Diagnostics for untranslated messages survive redeploys (Upstash) and show Taiwan time."""
import json

from test_translation_notice_availability import runtime  # noqa: F401

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


def test_admin_can_read_diagnostics_from_line(runtime, monkeypatch):
    from test_translation_notice_availability import event, delivered_text
    monkeypatch.setattr(app, "is_group_admin", lambda uid: True)
    gid = event("/診斷").source.group_id
    monkeypatch.setattr(app, "_diag_kv_read", lambda n: [
        {"ts": 0, "type": "translation_outcome", "data": {"status": "failed", "reason": "empty_or_rejected_translation",
                                                         "group_id": gid, "text": "十一月目標量4000噸"}},
        {"ts": 0, "type": "translation_outcome", "data": {"status": "failed", "reason": "x", "group_id": "other"}}])
    app.handle_message(event("/診斷"))
    text = delivered_text(runtime)
    assert "🩺" in text and "empty_or_rejected_translation" in text and "十一月目標量4000噸" in text and " x" not in text


def test_reasoning_effort_gets_a_matching_output_budget(monkeypatch):
    from test_translation_instruction_cost_quality import response
    seen = {}
    def create(**kw):
        seen.update(kw); return response("Tes")
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    app._tl.__dict__.clear(); app._tl.tone, app._tl.group_id = "factory", "Cx"
    monkeypatch.setattr(app, "_translation_needs_upgrade_model", lambda _t: True)
    app.translate("十一月目標量4000噸，20號前一天排2個", "zh", "id")
    assert seen.get("reasoning_effort") == "medium" and seen.get("max_completion_tokens", 0) >= 2048 + 384


NOTICE = ("十一月目標量4000噸，異型群組還是在，這個月又新開一個削皮棒追蹤群組，暫時比照上個月異型棒優先安排處理。\n\n"
          "11、12月要排特休的找時間可以在班表上畫時間，20號前盡量一天排2個就好，20號後盡量一天排1個，"
          "避免臨時還有病假或其他突發事情，導致人力不足。")
NOTICE_ID = ("Target bulan November 4.000 ton. Grup batang profil khusus masih ada, dan bulan ini dibuka lagi grup "
             "pelacakan batang peeling. Untuk sementara prioritaskan penanganannya seperti batang profil khusus bulan lalu.\n\n"
             "Yang ingin mengambil cuti tahunan di bulan November dan Desember, silakan tandai waktunya di jadwal shift. "
             "Sebelum tanggal 20 usahakan maksimal 2 orang per hari, setelah tanggal 20 usahakan maksimal 1 orang per hari, "
             "agar kalau mendadak ada yang sakit atau urusan lain, tenaga kerja tidak kurang.")


def test_indonesian_thousands_separator_is_not_a_missing_quantity(monkeypatch):
    """Real case: '4000噸' rendered '4.000 ton' was rejected as a missing quantity
    and the whole notice was silently never translated (three times)."""
    from test_translation_instruction_cost_quality import response
    assert app._delivery_validation_issues(NOTICE, NOTICE_ID, "zh", "id") == []
    assert app._delivery_validation_issues(NOTICE, NOTICE_ID.replace("4.000", "4,000"), "zh", "id") == []
    assert app._delivery_validation_issues(NOTICE, NOTICE_ID.replace("4.000", "400"), "zh", "id")  # real loss still caught
    assert app._delivery_validation_issues("Target 4.000 ton, sisa 12.500 kg", "目標 4000 噸，剩 12500 kg", "id", "zh") == []
    assert app._delivery_validation_issues("Berat 2,5 ton", "重量 2.5 噸", "id", "zh") == []
    monkeypatch.setattr(app.ai.chat.completions, "create", lambda **kw: response(NOTICE_ID))
    app._tl.__dict__.clear(); app._tl.tone, app._tl.group_id = "factory", "Cx"
    assert app.translate(NOTICE, "zh", "id").startswith("Target bulan November 4.000 ton")


def test_failure_diagnostics_carry_the_rejection_reason(monkeypatch):
    seen = []
    monkeypatch.setattr(app, "_event_log_write", lambda t, d: seen.append(d))
    app._tl.__dict__.clear(); app._tl.group_id = "Cx"
    issues = app._delivery_validation_issues(NOTICE, NOTICE_ID.replace("4.000", "400"), "zh", "id")
    assert issues
    app._set_translation_outcome("failed", "empty_or_rejected_translation")
    assert seen[-1]["issues"] and seen[-1]["candidate"].startswith("Target bulan November 400")


def test_indonesian_scale_words_count_as_the_same_quantity():
    assert app._delivery_validation_issues(NOTICE, NOTICE_ID.replace("4.000 ton", "4 ribu ton"), "zh", "id") == []
    assert app._delivery_validation_issues(NOTICE, NOTICE_ID.replace("4.000 ton", "3 ribu ton"), "zh", "id")
