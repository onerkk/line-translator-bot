"""Final acknowledgements, durable once-only group delivery and Flex payloads.

SQLite and Redis run the real atomic store. Only LINE transport is substituted.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import io
import json
import threading
import time
from types import SimpleNamespace
import urllib.error
import uuid

import pytest
from linebot.v3.messaging import FlexMessage

import line_ack_completion as completion
import line_ack_reminders as reminders
import line_factory_features as factory
from line_factory_store import StoreError
from line_message_ui import completion_message
from test_line_factory_features import hub, storage, event, GROUP, USER, COLLEAGUE, OTHER
from test_ack_known_zero_stop import start, answer, assert_finished
from test_ack_repeat_controls import THIRD, stored, tick
from test_ack_recipient_scope import begin, tagged, FOURTH


def item(hub, row):
    return stored(hub, row).get("completion_notification", {})


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=time.time())
    monkeypatch.setattr(completion, "time", SimpleNamespace(time=lambda: state.now))
    return state


def test_final_ack_sends_one_group_card_after_commit_and_stops_reminders(hub):
    row, reminders_sent, replies, _ = begin(hub, [tagged("@Adi", COLLEAGUE), tagged("@Budi", THIRD)])
    sent = hub.h["factory_completion_sends"]
    def send(group, messages, key):
        current = stored(hub, row)
        assert completion.all_confirmed(current)
        assert current["completion_notification"]["state"] == "sending"
        assert group == GROUP and uuid.UUID(key)
        assert len(messages) == 1 and FlexMessage.from_dict(messages[0]).to_dict() == messages[0]
        sent.append(copy.deepcopy((group, messages, key)))
    hub.h["_factory_completion_sender"] = send
    answer(hub, row, COLLEAGUE)
    assert not sent and len(reminders_sent) == 1 and not replies
    answer(hub, row, FOURTH)  # Outside the selected list.
    answer(hub, row, USER)
    assert not sent
    answer(hub, row, THIRD)
    first = assert_finished(hub, row)
    assert item(hub, row)["state"] == "accepted" and item(hub, row)["recipient_count"] == 2
    card = json.dumps(sent[0][1][0], ensure_ascii=False)
    assert "目標人員已全員確認完畢" in card and "Seluruh penerima" in card
    assert "2 / 2" in card and "100%" in card and "UTC+8" in card
    assert all(text not in card for text in ("factory_ack", "factory_help", "factory_stop", "substitution", COLLEAGUE, THIRD))
    fresh = factory.FactoryHub(hub.app, hub.h, hub.store)
    for uid in (COLLEAGUE, THIRD, COLLEAGUE):
        answer(fresh, row, uid)
    tick(fresh, row["reminder_due_at"] + 3600)
    assert len(sent) == 1 and stored(hub, row) == first and not replies


@pytest.mark.parametrize("mode", ["off", "manual_stop", "one_round_sent"])
def test_completion_does_not_depend_on_an_active_reminder_schedule(hub, mode):
    row, _, _ = start(hub)
    key = "notice:" + GROUP + ":" + row["token"]
    if mode == "manual_stop":
        hub.stop_notice(GROUP, row["token"], USER)
    elif mode == "off":
        hub.store.update(key, lambda r: dict(r, reminder_minutes=0, reminder_state="off", wake_at=None))
    else:
        hub.store.update(key, lambda r: dict(r, reminder_repeat=False))
        tick(hub, row["reminder_due_at"])
    answer(hub, row)
    assert len(hub.h["factory_completion_sends"]) == 1
    assert stored(hub, row)["wake_at"] is None
    if mode in {"off", "manual_stop"}:
        assert stored(hub, row)["reminder_state"] == ("off" if mode == "off" else "stopped")


def test_help_and_departure_are_not_confirmations(hub):
    row, _, _ = start(hub, people=(COLLEAGUE, THIRD))
    key = "notice:" + GROUP + ":" + row["token"]
    hub.store.update(key, lambda r: dict(r, responses={THIRD: {"status": "needs_help", "name": "Budi"}}))
    hub.member_presence(GROUP, THIRD, left=True)
    answer(hub, row)
    assert not item(hub, row) and not hub.h["factory_completion_sends"]
    assert not completion.all_confirmed(stored(hub, row))
    hub.member_presence(GROUP, THIRD, left=False)
    answer(hub, row, THIRD)
    assert len(hub.h["factory_completion_sends"]) == 1


def test_no_empty_or_historical_completion_backfill(hub):
    row, _, _ = start(hub, people=())
    empty = stored(hub, row)
    completion.prepare(empty, time.time())
    assert not empty.get("completion_notification")
    row, _, _ = start(hub)
    # Use an existing context with a distinct source ID for this historical row.
    from test_line_ack_commands import command
    row = command(hub, mid="historical-completed")
    key = "notice:" + GROUP + ":" + row["token"]
    hub.store.update(key, lambda r: dict(r, responses={COLLEAGUE: {"status": "understood", "name": "Adi", "at": 1}}))
    answer(hub, row)
    hub._finish_answered_notice(stored(hub, row), {})
    assert not item(hub, row) and not hub.h["factory_completion_sends"]


def test_incomplete_group_roster_is_labelled_as_listed_targets_only(hub):
    row, _, _ = start(hub, count=10)
    answer(hub, row)
    text = json.dumps(hub.h["factory_completion_sends"][0][1], ensure_ascii=False)
    assert "未涵蓋尚未辨識的群組成員" in text
    assert "1 / 1" in text and "Seluruh grup" not in text


@pytest.mark.parametrize("reason", ["other_group", "departed", "expired", "disabled", "edited", "bot_left"])
def test_ineligible_notices_cannot_publish_a_completion(hub, reason):
    row, _, _ = start(hub)
    group = GROUP
    if reason == "other_group":
        group = OTHER
    elif reason == "departed":
        hub.member_presence(GROUP, COLLEAGUE, left=True)
    elif reason == "expired":
        hub.store.update("notice:" + GROUP + ":" + row["token"], lambda r: dict(
            r, created_at=time.time()-8*86400, expires_at=time.time()-86400))
    elif reason == "disabled":
        hub.store.put("ack-settings", {"groups": {GROUP: {"acknowledgements": "off"}}})
    elif reason == "edited":
        hub.unsend({"source": {"groupId": GROUP}, "unsend": {"messageId": row["factory_event"]["message_id"]}})
    else:
        hub.store.put("bot-left:" + GROUP, {"at": time.time()})
    hub.postback(event(uid=COLLEAGUE, group=group), {"action": "factory_ack", "token": row["token"]})
    assert not hub.h["factory_completion_sends"]


def test_failed_commit_never_sends_success(hub, monkeypatch):
    row, _, _ = start(hub)
    original = hub.store.compare_swap
    def fail(key, previous, current, ttl):
        if current and current.get("completion_notification"):
            raise StoreError("injected final receipt commit failure")
        return original(key, previous, current, ttl)
    monkeypatch.setattr(hub.store, "compare_swap", fail)
    with pytest.raises(StoreError):
        answer(hub, row)
    assert not stored(hub, row)["responses"] and not hub.h["factory_completion_sends"]


def test_concurrent_last_recipients_create_one_durable_notification(hub, monkeypatch):
    row, _, _ = start(hub, people=(COLLEAGUE, THIRD))
    other = factory.FactoryHub(hub.app, hub.h, hub.store)
    barrier, local = threading.Barrier(2), threading.local()
    original, conflicts = hub.store.compare_swap, []
    key = "notice:" + GROUP + ":" + row["token"]
    def compare(name, previous, current, ttl):
        if name == key and not getattr(local, "checked", False):
            local.checked = True
            barrier.wait(5)
        result = original(name, previous, current, ttl)
        conflicts.append(result)
        return result
    monkeypatch.setattr(hub.store, "compare_swap", compare)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(answer, who, row, uid) for who, uid in ((hub, COLLEAGUE), (other, THIRD))]
        for job in jobs:
            job.result(10)
    assert False in conflicts and len(hub.h["factory_completion_sends"]) == 1
    assert item(hub, row)["recipient_count"] == 2 and item(hub, row)["state"] == "accepted"


def test_timeout_then_restart_reuses_identical_payload_key_and_original_time(hub, clock):
    row, _, _ = start(hub)
    sent = []
    def timeout(*args):
        sent.append(copy.deepcopy(args))
        raise TimeoutError("accepted but response lost")
    hub.h["_factory_completion_sender"] = timeout
    clock.now = time.time() + 1
    with pytest.raises(StoreError):
        answer(hub, row)
    first = stored(hub, row)
    assert first["completion_notification"]["state"] == "pending"
    assert first["receipt_confirmations"][COLLEAGUE]["state"] == "accepted"
    hub.h["_factory_completion_sender"] = lambda *args: sent.append(copy.deepcopy(args))
    fresh = factory.FactoryHub(hub.app, hub.h, hub.store)
    clock.now = item(hub, row)["next_attempt_at"] + 1
    answer(fresh, row)
    answer(fresh, row)
    assert len(sent) == 2 and sent[0] == sent[1]
    assert item(hub, row)["state"] == "accepted"
    assert stored(hub, row)["responses"] == first["responses"]


def test_private_failure_does_not_block_completion(hub):
    row, _, _ = start(hub)
    hub.h["_factory_receipt_sender"] = lambda *a: (_ for _ in ()).throw(TimeoutError())
    with pytest.raises(StoreError):
        answer(hub, row)
    assert item(hub, row)["state"] == "accepted"
    assert len(hub.h["factory_completion_sends"]) == 1


def test_accepted_send_with_failed_checkpoint_reuses_lease_and_retry_key(hub, clock, monkeypatch):
    row, _, _ = start(hub)
    key = "notice:" + GROUP + ":" + row["token"]
    original = hub.store.compare_swap
    def fail(name, previous, current, ttl):
        if name == key and (current or {}).get("completion_notification", {}).get("state") == "accepted":
            raise StoreError("LINE accepted; local checkpoint interrupted")
        return original(name, previous, current, ttl)
    clock.now = time.time() + 1
    with monkeypatch.context() as patch:
        patch.setattr(hub.store, "compare_swap", fail)
        with pytest.raises(StoreError):
            answer(hub, row)
    assert item(hub, row)["state"] == "sending"
    # A second worker cannot send again while the first lease is still live.
    with pytest.raises(StoreError):
        answer(hub, row)
    assert len(hub.h["factory_completion_sends"]) == 1
    clock.now = item(hub, row)["next_attempt_at"] + 1
    answer(hub, row)
    sent = hub.h["factory_completion_sends"]
    assert sent[0] == sent[1] and item(hub, row)["state"] == "accepted"


def test_group_disabled_between_claim_and_send_is_rechecked(hub, monkeypatch):
    row, _, _ = start(hub)
    original = hub.store.update
    changed = False
    def update(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed and (result or {}).get("completion_notification", {}).get("state") == "sending":
            changed = True
            hub.store.put("ack-settings", {"groups": {GROUP: {"acknowledgements": "off"}}})
        return result
    monkeypatch.setattr(hub.store, "update", update)
    answer(hub, row)
    assert changed and not hub.h["factory_completion_sends"]
    assert item(hub, row)["state"] == "cancelled"


@pytest.mark.parametrize("reason", ["disabled", "edited", "audience_changed", "bot_left", "window_expired"])
def test_pending_delivery_rechecks_lifecycle_before_retry(hub, clock, reason):
    row, _, _ = start(hub)
    sent = []
    def timeout(*args):
        sent.append(args)
        raise TimeoutError()
    hub.h["_factory_completion_sender"] = timeout
    clock.now = time.time() + 1
    with pytest.raises(StoreError):
        answer(hub, row)
    clock.now = item(hub, row)["next_attempt_at"] + 1
    if reason == "disabled":
        hub.store.put("ack-settings", {"groups": {GROUP: {"acknowledgements": "off"}}})
    elif reason == "edited":
        hub.unsend({"source": {"groupId": GROUP}, "unsend": {"messageId": row["factory_event"]["message_id"]}})
    elif reason == "audience_changed":
        hub.store.update("notice:" + GROUP + ":" + row["token"],
                         lambda r: dict(r, expected={**r["expected"], THIRD: "Budi"}))
    elif reason == "bot_left":
        hub.store.put("bot-left:" + GROUP, {"at": time.time()})
    else:
        clock.now = item(hub, row)["created_at"] + completion.RETRY_WINDOW
    answer(hub, row)
    assert len(sent) == 1
    if reason == "window_expired":
        assert item(hub, row)["state"] == "uncertain"


@pytest.mark.parametrize("status", [200, 409, 400, 429, 500])
def test_completion_uses_real_push_protocol_and_accepted_retry_semantics(hub, monkeypatch, status):
    row, _, _ = start(hub)
    requests = []
    def urlopen(request, timeout):
        requests.append(request)
        payload = json.loads(request.data)
        assert payload["to"] == GROUP and payload["messages"][0]["type"] == "flex"
        assert uuid.UUID(request.get_header("X-line-retry-key")) and timeout == 12
        assert "replyToken" not in payload
        if status == 200:
            response = io.BytesIO(b'{}')
            response.status = 200
            return response
        raise urllib.error.HTTPError(request.full_url, status, "test", {
            "x-line-accepted-request-id": "accepted"} if status == 409 else {}, None)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "offline-test-token")
    monkeypatch.setattr(reminders.urllib.request, "urlopen", urlopen)
    hub.h.pop("_factory_completion_sender")
    if status in {429, 500}:
        with pytest.raises(StoreError):
            answer(hub, row)
        assert item(hub, row)["state"] == "pending"
    else:
        answer(hub, row)
        answer(hub, row)
        assert item(hub, row)["state"] == ("accepted" if status in {200, 409} else "failed")
    assert len(requests) == 1


def test_card_bounds_unicode_content_and_uses_taiwan_confirmation_time():
    now = datetime(2026, 9, 10, 0, 5, tzinfo=timezone.utc).timestamp()
    record = {"token": "abcdefgh", "original": "👷材料"*500, "translated": "Periksa TAG "*500,
              "sender_name": "名字"*100, "recipient_scope": "mentioned"}
    message = completion_message(record, 500, now)
    assert FlexMessage.from_dict(message).to_dict() == message
    encoded = json.dumps(message, ensure_ascii=False)
    assert len(encoded.encode()) < 10000
    assert "2026/09/10  08:05" in encoded and "500 / 500" in encoded
    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                assert 0 < len(node["text"].encode("utf-16-le")) <= 4000
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(message)


def test_signed_webhook_recovery_finishes_completion_without_another_tap(hub, clock, monkeypatch):
    import app
    from linebot.v3 import WebhookHandler
    from linebot.v3.webhooks import PostbackEvent
    import webhook_runtime
    import translation_retry_queue as queue
    from test_webhook_latency_recovery import SECRET, sign
    row, _, _ = start(hub)
    monkeypatch.setattr(app, "factory_hub", hub)
    monkeypatch.setattr(app, "_processed_msg_ids", app._collections_dedup.OrderedDict())
    monkeypatch.setenv("LINE_WEBHOOK_ASYNC", "0")
    monkeypatch.setattr(app.app, "testing", False)
    handler = WebhookHandler(SECRET)
    handler.add(PostbackEvent)(app.handle_postback)
    inbox = webhook_runtime.WebhookInbox(handler, app._release_webhook_message_claims)
    monkeypatch.setattr(inbox.pool, "ensure_started", lambda: None)
    monkeypatch.setattr(app, "_WEBHOOK_INBOX", inbox)
    body = json.dumps({"destination": "U" + "f"*32, "events": [{
        "type": "postback", "mode": "active", "timestamp": 1000,
        "webhookEventId": "all-confirmed-recovery", "replyToken": "unused-group-reply",
        "deliveryContext": {"isRedelivery": False},
        "source": {"type": "group", "groupId": GROUP, "userId": COLLEAGUE},
        "postback": {"data": "action=factory_ack&token=" + row["token"]}}]})
    sent = []
    def timeout(*args):
        sent.append(copy.deepcopy(args))
        raise TimeoutError()
    hub.h["_factory_completion_sender"] = timeout
    clock.now = time.time() + 1
    response = app.app.test_client().post("/callback", data=body, headers={"X-Line-Signature": sign(body)})
    assert response.status_code == 500
    jobs = [j for j in queue.list_pending() if j["job_kind"] == "webhook"]
    assert len(jobs) == 1 and jobs[0]["payload"]["body"] == body
    clock.now = item(hub, row)["next_attempt_at"] + 1
    hub.h["_factory_completion_sender"] = lambda *args: sent.append(copy.deepcopy(args))
    key = jobs[0]["job_key"]
    assert queue.claim_job(key, owner="completion-recovery")
    assert inbox.run_job(queue.get(key), "completion-recovery")
    assert queue.was_delivered(key) and item(hub, row)["state"] == "accepted"
    assert sent[0] == sent[1] and len(sent) == 2
