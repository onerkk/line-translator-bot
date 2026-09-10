"""Notice creation with real translation guards and persisted failure recovery.

Transport/provider boundaries are offline. SQLite and Redis run the same flow.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import pytest
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent

import app
import line_factory_features as factory
import line_ack_reminders as reminders
import translation_retry_queue as queue
import webhook_runtime as webhooks
from line_factory_store import StoreError
from test_line_factory_features import hub, storage, event, GROUP, USER, COLLEAGUE
from test_line_ack_commands import configure
from test_ack_recipient_scope import command_event, tagged
from test_ack_known_zero_stop import answer
from test_translation_notice_availability import runtime
from test_webhook_latency_recovery import SECRET, sign


BODY = "測試新指令 主要用在重要公告 有看到的請按確認。否則8小時會重新標記未確認的人員。"
TRANSLATED = ("Uji perintah baru, terutama untuk pengumuman penting. Jika sudah melihatnya, "
              "silakan tekan konfirmasi. Jika tidak, anggota yang belum mengonfirmasi "
              "akan ditandai lagi setelah 8 jam.")
LABELS = ["@傑伊諾 Fajar", "@JeffryLatif", "@khoirul", "@( 杰弗 )", "@蘇比 sobirin",
          "@Hasim", "@kampret", "@Martin 馬丁", "@魯帝 RUDI", "@福迪", "@路非",
          "@Irwan 布納萬", "@苏山多", "@伊努滿 Sumertha", "@法比恩 Fabian", "@Agus Sw 鐸多",
          "@巴憂", "@阿馬", "@迪弟 kampret", "@budi santoso 山多"]
IDS = ["U" + format(index, "032x") for index in range(1, 21)]


def screenshot_event(body="@" + BODY, mid="creation-20"):
    text, native = "/確認 ", []
    for label, uid in zip(LABELS, IDS):
        native.append({"type": "user", "userId": uid,
                       "index": len(text.encode("utf-16-le")) // 2,
                       "length": len(label.encode("utf-16-le")) // 2})
        text += label + " "
    raw = event(text + body, mid=mid, mentions=native)
    raw["message"]["quoteToken"] = "offline-quote-token"
    raw.update(mode="active", deliveryContext={"isRedelivery": False})
    return MessageEvent.from_dict(raw)


def setup_notice(hub):
    configure(hub, 480, repeat=True)
    sent, replies = [], []
    hub.reminders.sender = lambda *args: sent.append(copy.deepcopy(args))
    hub.h["_send_reply_with_push_fallback"] = lambda **kw: replies.append(kw)
    return sent, replies


def submit(hub, ev):
    with hub.message_scope(ev, "text"):
        assert hub.command(ev)


def saved(hub):
    return hub.store.recent("notice:" + GROUP)[0]


def tick(hub, now):
    hub.reminders.clock = lambda: now
    hub.reminders.run_due(budget_seconds=30)


def test_screenshot_twenty_native_names_and_unsigned_heading_use_real_pipeline(hub, runtime):
    sent, replies = setup_notice(hub)
    hub.h.update(_tl=app._tl, translate=app.translate, translate_multi=app.translate_multi,
                 detect_language=app.detect_language, strip_mentions_for_detect=app.strip_mentions_for_detect)
    runtime.provider_result = TRANSLATED
    submit(hub, screenshot_event())
    row = saved(hub)
    assert row["delivery_state"] == "delivered" and replies == []
    assert set(row["recipient_ids"]) == set(IDS)
    assert "測試新指令" not in row["translated"]
    assert "Uji perintah baru" in row["translated"] and "8 jam" in row["translated"]
    assert all(label in row["translated"] for label in LABELS)
    assert runtime.generations == [(BODY, "zh", "id")]
    assert len(sent) == 1
    substitutions = sent[0][1][0]["substitution"]
    assert len(substitutions) == 20
    assert {item["mentionee"]["userId"] for item in substitutions.values()} == set(IDS)
    assert row["reminder_due_at"] == row["delivered_at"] + 480 * 60


def test_translation_intent_exists_before_provider_and_resumes_after_restart(hub):
    sent, replies = setup_notice(hub)
    ev = screenshot_event()
    def unavailable(*args):
        row = saved(hub)
        assert row["translation_pending"] and row["translated"] == ""
        assert "initial_messages" not in row and "reminder_due_at" not in row
        return None
    hub.h["translate"] = unavailable
    submit(hub, ev)
    row = saved(hub)
    assert row["translation_pending"] and row["wake_at"] is not None
    assert row["last_error_stage"] == "translation" and not sent and not replies
    fresh = factory.FactoryHub(hub.app, hub.h, hub.store)
    fresh.reminders.sender = hub.reminders.sender
    fresh.h["translate"] = lambda *args: TRANSLATED
    tick(fresh, row["wake_at"])
    row = saved(hub)
    assert row["delivery_state"] == "delivered" and not row["translation_pending"]
    assert set(row["recipient_ids"]) == set(IDS) and len(sent) == 1
    assert fresh.get_context(row["token"], GROUP)["translated"] == row["translated"]
    submit(fresh, ev)
    assert len(sent) == 1


def test_native_name_does_not_select_wrong_source_language(hub):
    setup_notice(hub)
    hub.h["detect_language"] = app.detect_language
    hub.h["strip_mentions_for_detect"] = app.strip_mentions_for_detect
    calls = []
    hub.h["translate"] = lambda text, src, tgt: calls.append((text, src, tgt)) or "請先確認。"
    ev = command_event([tagged("@budi santoso 山多", COLLEAGUE)], suffix="Tolong konfirmasi dulu.")
    submit(hub, ev)
    assert calls[0][1:] == ("id", "zh")


@pytest.mark.parametrize("form", ["__MENTION_{}__", "MENTION_{}", "MENTION {}", "[[MENTION_{}]]",
                                  "__提及_{}__", "提及{}", "__SEBUTAN_{}__", "__sebutan_{}__"])
def test_twenty_identity_indices_cannot_be_partially_replaced(form):
    mapping = {"__MENTION_" + str(i) + "__": label for i, label in enumerate(LABELS)}
    assert app.restore_mentions(" ".join(form.format(i) for i in range(20)), mapping) == " ".join(LABELS)


def test_identity_restoration_never_rewrites_an_inserted_name_or_unknown_index():
    mapping = {"__MENTION_0__": "@MENTION_1", "__MENTION_1__": "@Ada"}
    restored = app.restore_mentions("__MENTION_0__ __MENTION_1__ __MENTION_100__", mapping)
    assert restored == "@MENTION_1 @Ada __MENTION_100__"


def test_visible_name_prefixes_and_duplicates_never_destroy_other_recipients():
    mapping = {"__MENTION_" + str(i) + "__": "@worker" + str(i) for i in range(20)}
    mapping["__MENTION_20__"] = "@worker1"
    visible = " ".join(mapping.values())
    restored = app.restore_mentions(" ".join(mapping), mapping)
    assert restored == visible
    assert app._post_restore_mentions_guard(restored, mapping) == visible
    missing = app.restore_mentions("__MENTION_10__", {
        "__MENTION_1__": "@worker1", "__MENTION_10__": "@worker10"})
    assert missing == "@worker1 @worker10"


def test_line_timeout_freezes_translation_and_retry_key(hub):
    sent, replies = setup_notice(hub)
    calls = []
    hub.h["translate"] = lambda *args: calls.append(args) or TRANSLATED
    attempts = []
    def timeout(*args):
        attempts.append(copy.deepcopy(args))
        raise reminders.SendError("LINE timeout")
    hub.reminders.sender = timeout
    submit(hub, screenshot_event())
    row = saved(hub)
    assert row["last_error_stage"] == "line" and not row["translation_pending"]
    assert len(calls) == 1 and "reminder_due_at" not in row and not replies
    hub.reminders.sender = lambda *args: attempts.append(copy.deepcopy(args))
    tick(hub, row["wake_at"])
    assert attempts[0] == attempts[1] and len(calls) == 1
    row = saved(hub)
    assert row["delivery_state"] == "delivered"
    assert row["reminder_due_at"] == row["delivered_at"] + 28800
    assert row["last_error_stage"] == ""


def test_context_write_outage_resumes_committed_translation_without_regeneration(hub, monkeypatch):
    sent, replies = setup_notice(hub)
    calls = []
    hub.h["translate"] = lambda *args: calls.append(args) or TRANSLATED
    original = hub.sync_notice_context
    def broken(row):
        raise StoreError("offline injected context write failure")
    monkeypatch.setattr(hub, "sync_notice_context", broken)
    submit(hub, screenshot_event())
    row = saved(hub)
    assert row["context_pending"] and not row["translation_pending"]
    assert row["last_error_stage"] == "storage" and not sent and not replies
    monkeypatch.setattr(hub, "sync_notice_context", original)
    tick(hub, row["wake_at"])
    row = saved(hub)
    assert row["delivery_state"] == "delivered" and len(calls) == 1 and len(sent) == 1
    assert hub.get_context(row["token"], GROUP)["translated"] == row["translated"]


def test_late_duplicate_draft_cannot_replace_completed_button_context(hub):
    sent, replies = setup_notice(hub)
    drafts = []
    def translate(*args):
        row = saved(hub)
        drafts.append(hub.store.get("context:" + row["token"]))
        return TRANSLATED
    hub.h["translate"] = translate
    submit(hub, screenshot_event())
    row = saved(hub)
    context = hub.get_context(row["token"], GROUP)
    hub.save_context(row["token"], drafts[0])
    assert hub.get_context(row["token"], GROUP) == context
    assert saved(hub) == row and len(sent) == 1


@pytest.mark.parametrize("cancel", ["stop", "unsend", "edit", "group_off"])
def test_pending_translation_obeys_cancellation_before_any_card_is_sent(hub, cancel):
    sent, replies = setup_notice(hub)
    hub.h["translate"] = lambda *args: None
    ev = screenshot_event()
    submit(hub, ev)
    row = saved(hub)
    if cancel == "stop":
        hub.stop_notice(GROUP, row["token"], USER)
    elif cancel == "unsend":
        hub.unsend({"source": {"groupId": GROUP}, "unsend": {"messageId": ev.message.id}})
    elif cancel == "edit":
        changed = event("/確認 更正公告", stamp=200, edited=True, mid=ev.message.id)
        with hub.message_scope(changed, "text"):
            pass
    else:
        doc = copy.deepcopy(hub.menu.document())
        doc["default"]["acknowledgements"] = "off"
        import line_quick_reply
        hub.h[line_quick_reply.KEY] = doc
    hub.h["translate"] = lambda *args: pytest.fail("Cancelled notice must not call the provider")
    tick(hub, row["wake_at"])
    assert not sent and not replies


def test_twenty_recipients_repeat_at_eight_hours_until_only_pending_members_remain(hub):
    sent, replies = setup_notice(hub)
    hub.h["translate"] = lambda *args: TRANSLATED
    submit(hub, screenshot_event())
    row = saved(hub)
    for uid in IDS[:19]:
        answer(hub, row, uid)
    tick(hub, row["reminder_due_at"])
    assert len(sent) == 2 and not replies
    targets = [item["mentionee"]["userId"] for item in sent[-1][1][0]["substitution"].values()]
    assert targets == [IDS[-1]]
    next_row = saved(hub)
    assert next_row["next_reminder_at"] == next_row["reminded_at"] + 28800
    answer(hub, row, IDS[-1])
    row = saved(hub)
    assert row["reminder_state"] == "no_pending" and row["wake_at"] is None
    tick(hub, next_row["next_reminder_at"])
    assert len(sent) == 2 and not replies


def test_duplicate_preparation_is_claimed_once_and_stop_during_ai_prevents_delivery(hub):
    sent, replies = setup_notice(hub)
    hub.h["translate"] = lambda *args: None
    ev = screenshot_event()
    submit(hub, ev)
    row = saved(hub)
    started, release = threading.Event(), threading.Event()
    calls = []
    def delayed(*args):
        calls.append(args)
        started.set()
        assert release.wait(4)
        return TRANSLATED
    hub.h["translate"] = delayed
    hub.reminders.clock = lambda: row["wake_at"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(hub.reminders.run_due)
        try:
            assert started.wait(3)
            hub.reminders.run_due()
            hub.stop_notice(GROUP, row["token"], USER)
        finally:
            release.set()
        first.result(timeout=4)
    assert len(calls) == 1 and not sent and not replies
    assert saved(hub)["reminder_state"] == "stopped"


def test_slow_translation_recovery_does_not_block_existing_due_reminders(hub):
    sent, replies = setup_notice(hub)
    ev = event("/確認 原有通知", mid="ready")
    submit(hub, ev)
    ready = saved(hub)
    hub.h["translate"] = lambda *args: None
    submit(hub, screenshot_event())
    started, release = threading.Event(), threading.Event()
    def delayed(*args):
        started.set()
        assert release.wait(4)
        return TRANSLATED
    hub.h["translate"] = delayed
    hub.reminders.clock = lambda: ready["reminder_due_at"]
    try:
        hub.reminders.run_due(submit_preparation=hub.reminder_worker._submit_preparation)
        assert started.wait(3)
        assert len(sent) == 2  # Original notice and its reminder were sent.
    finally:
        release.set()
        deadline = time.monotonic() + 4
        while hub.reminder_worker._preparing and time.monotonic() < deadline:
            threading.Event().wait(.01)
    assert not hub.reminder_worker._preparing


def test_callback_retains_retry_or_shared_intent_until_real_card_delivery(hub, runtime, monkeypatch):
    sent, replies = setup_notice(hub)
    hub.h["translate"] = lambda *args: None
    monkeypatch.setattr(app, "factory_hub", hub)
    monkeypatch.setenv("RENDER", "true")
    handler = WebhookHandler(SECRET)
    handler.add(MessageEvent, message=TextMessageContent)(app.handle_message)
    inbox = webhooks.WebhookInbox(handler, app._release_webhook_message_claims)
    monkeypatch.setattr(app, "_WEBHOOK_INBOX", inbox)
    raw = screenshot_event().to_dict()
    body = json.dumps({"destination": "U" + "f" * 32, "events": [raw]})
    client = app.app.test_client()
    first = client.post("/callback", data=body, headers={"X-Line-Signature": sign(body)})
    assert first.status_code == (503 if hub.store.path else 200) and not sent and not replies
    row = saved(hub)
    hub.h["translate"] = lambda *args: TRANSLATED
    hub.store.update("notice:" + GROUP + ":" + row["token"], lambda r: dict(r, wake_at=0))
    if not hub.store.path:
        # Shared storage permits HTTP 200; a fresh worker must recover without
        # depending on another copy of that webhook arriving.
        fresh = factory.FactoryHub(hub.app, hub.h, hub.store)
        fresh.reminders.sender = hub.reminders.sender
        fresh.reminders.run_due()
    second = client.post("/callback", data=body, headers={"X-Line-Signature": sign(body)})
    assert second.status_code == 200 and len(sent) == 1


def test_notice_never_sends_a_translation_with_changed_reminder_hours(hub, runtime):
    sent, replies = setup_notice(hub)
    hub.h.update(_tl=app._tl, translate=app.translate, translate_multi=app.translate_multi,
                 detect_language=app.detect_language)
    runtime.provider_result = TRANSLATED.replace("8 jam", "9 jam")
    submit(hub, screenshot_event())
    row = saved(hub)
    assert row["translation_pending"] and row["last_error_stage"] == "translation", row.get("translated")
    assert not sent and not replies


@pytest.mark.parametrize("candidate,valid", [
    ("Tandai lagi setelah 8 jam.", True), ("Tandai lagi setelah delapan jam.", True),
    ("Tandai lagi setelah 480 menit.", True), ("Tandai lagi setelah 9 jam.", False),
    ("Tandai lagi setelah 18 jam.", False), ("Tandai lagi setelah 8 menit.", False),
    ("Tandai lagi setelah 9 jam. Data asli: 8.", False),
    ("Tandai lagi setelah 9 jam. Data asli: 8 jam.", False),
    ("Tandai lagi setelah dua puluh delapan jam.", False),
])
def test_duration_contract_compares_whole_values_and_units(candidate, valid):
    report = app.tqg_module.validate_translation("8小時後重新點名。", candidate, "zh", "id")
    assert ("duration_value_or_unit_mismatch" not in report.hard_issues) is valid


def test_duration_contract_ignores_clock_forms_and_accepts_reverse_and_decimals():
    validate = app.tqg_module._duration_integrity_issues
    assert validate("Tunggu 8 jam.", "等待八小時。", "id", "zh") == []
    assert validate("等候0.5小時。", "Tunggu 0,5 jam.", "zh", "id") == []
    assert validate("等待30分鐘。", "Wait half hour.", "zh", "en") == []
    assert validate("明天8點開會。", "Rapat besok jam 8.", "zh", "id") == []
    assert validate("Tunggu 8 jam.", "等待十八小時。", "id", "zh")


@pytest.mark.parametrize("feedback_fails", [False, True])
def test_store_outage_before_creation_is_journaled_and_recovers_without_user_resend(hub, runtime, monkeypatch, feedback_fails):
    sent, replies = setup_notice(hub)
    monkeypatch.setattr(app, "factory_hub", hub)
    monkeypatch.setenv("LINE_WEBHOOK_ASYNC", "0")
    monkeypatch.setattr(app.app, "testing", False)
    handler = WebhookHandler(SECRET)
    handler.add(MessageEvent, message=TextMessageContent)(app.handle_message)
    inbox = webhooks.WebhookInbox(handler, app._release_webhook_message_claims)
    monkeypatch.setattr(inbox.pool, "ensure_started", lambda: None)
    monkeypatch.setattr(app, "_WEBHOOK_INBOX", inbox)
    outage = [True]
    original_save = hub.store.save_interaction
    def save(*args, **kwargs):
        if outage[0]:
            raise StoreError("injected initial storage outage")
        return original_save(*args, **kwargs)
    monkeypatch.setattr(hub.store, "save_interaction", save)
    if feedback_fails:
        def broken_reply(**kwargs):
            raise TimeoutError("offline LINE error feedback timeout")
        hub.h["_send_reply_with_push_fallback"] = broken_reply
    calls = []
    hub.h["translate"] = lambda *args: calls.append(args) or TRANSLATED
    body = json.dumps({"destination": "U" + "f" * 32, "events": [screenshot_event().to_dict()]})
    response = app.app.test_client().post("/callback", data=body, headers={"X-Line-Signature": sign(body)})
    assert response.status_code == 500 and not sent and not calls
    pending = [j for j in queue.list_pending() if j["job_kind"] == "webhook"]
    assert len(pending) == 1 and pending[0]["payload"]["body"] == body
    outage[0] = False
    key = pending[0]["job_key"]
    assert queue.claim_job(key, owner="notice-recovery")
    assert inbox.run_job(queue.get(key), "notice-recovery")
    assert saved(hub)["delivery_state"] == "delivered" and len(sent) == 1 and len(calls) == 1
    assert queue.was_delivered(key)
