"""Private receipt delivery with real SQLite/Redis CAS and signed webhooks.

Only the external LINE transport is fake. These cases verify the recipient,
durable commit, concurrent taps, transport acceptance and replay after failure.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
import io
import json
import sqlite3
import threading
import time
from types import SimpleNamespace
import urllib.error
import uuid

import pytest

import line_ack_receipts as receipts
import line_ack_reminders as reminders
import line_factory_features as factory
from line_factory_store import StoreError
from test_line_factory_features import hub, storage, event, GROUP, OTHER, USER, COLLEAGUE
from test_ack_known_zero_stop import start, answer, assert_finished
from test_ack_repeat_controls import THIRD, stored, tick
from test_line_ack_commands import command

OUTSIDER = "U" + "9" * 32


@pytest.fixture
def private(hub, monkeypatch):
    state = SimpleNamespace(sent=[], now=None)
    monkeypatch.setattr(receipts, "time", SimpleNamespace(time=lambda: time.time() if state.now is None else state.now))
    hub.h["_factory_receipt_sender"] = lambda *args: state.sent.append(copy.deepcopy(args))
    return state


def notification(hub, row, uid=COLLEAGUE):
    return stored(hub, row).get("receipt_confirmations", {}).get(uid, {})


def params(row, **extra):
    return {"action": "factory_ack", "token": row["token"], **extra}


def test_first_ack_commits_before_one_private_push_and_never_posts_to_group(hub, private):
    row, group_sends, replies = start(hub, people=(COLLEAGUE, THIRD))
    original_sender = hub.h["_factory_receipt_sender"]
    def check_commit(uid, messages, key):
        saved = stored(hub, row)
        assert saved["responses"][uid]["status"] == "understood"
        assert saved["receipt_confirmations"][uid]["state"] == "sending"
        assert uid == COLLEAGUE and uuid.UUID(key)
        assert len(messages) == 1 and messages[0]["type"] == "text"
        assert "✅ 已記錄" in messages[0]["text"] and "Sudah tercatat" in messages[0]["text"]
        assert row["token"][:6] in messages[0]["text"]
        assert row["original"] not in messages[0]["text"]
        assert not any(x in messages[0] for x in ("quickReply", "substitution", "sender"))
        original_sender(uid, messages, key)
    hub.h["_factory_receipt_sender"] = check_commit
    hub.postback(event(uid=COLLEAGUE), params(row, to=OUTSIDER, user_id=USER, group_id=OTHER))
    first = stored(hub, row)
    assert notification(hub, row)["state"] == "accepted"
    assert first["responses"][COLLEAGUE]["at"] <= notification(hub, row)["accepted_at"]
    for stamp in (100, 101, 200, 50, 900):
        assert hub.postback(event(uid=COLLEAGUE, stamp=stamp), params(row))
    assert len(private.sent) == 1 and len(group_sends) == 1 and replies == []
    assert stored(hub, row) == first
    assert first["wake_at"] == row["reminder_due_at"]
    tick(hub, row["reminder_due_at"])
    assert len(group_sends) == 2 and len(private.sent) == 1
    from test_ack_pending_mentions import recipients
    assert recipients(group_sends[-1]) == [THIRD]


def test_each_notice_and_member_has_its_own_once_only_confirmation(hub, private):
    first, group_sends, replies = start(hub, people=(COLLEAGUE, THIRD))
    for uid in (COLLEAGUE, THIRD, COLLEAGUE, THIRD):
        answer(hub, first, uid)
    assert len(private.sent) == 2 and assert_finished(hub, first)
    second = command(hub, "/ack 第二項工作", mid="second-private-notice")
    for row in (second, first, second):
        answer(hub, row)
    assert len(private.sent) == 3 and len({send[2] for send in private.sent}) == 3
    assert [send[0] for send in private.sent] == [COLLEAGUE, THIRD, COLLEAGUE]
    assert replies == [] and len(group_sends) == 2


@pytest.mark.parametrize("uid", [OUTSIDER, USER, "", "invalid"])
@pytest.mark.parametrize("action", ["factory_ack", "factory_help"])
def test_unlisted_clicks_have_no_private_send_or_record_even_if_actor_is_admin(hub, private, monkeypatch, uid, action):
    row, group_sends, replies = start(hub)
    hub.h["admin_users"] = {OUTSIDER: {"is_admin": True}}
    before, members = stored(hub, row), hub.known_members(GROUP)
    writes = []
    original_cas = hub.store.compare_swap
    def cas(*args):
        writes.append(args[0])
        return original_cas(*args)
    monkeypatch.setattr(hub.store, "compare_swap", cas)
    assert hub.postback(event(uid=uid), params(row, action=action))
    assert stored(hub, row) == before and hub.known_members(GROUP) == members
    assert private.sent == replies == writes == [] and len(group_sends) == 1


@pytest.mark.parametrize("reason", ["other_group", "left", "expired", "disabled", "edited"])
def test_invalid_or_obsolete_ack_cannot_create_private_confirmation(hub, private, reason):
    row, group_sends, replies = start(hub)
    group = GROUP
    if reason == "other_group":
        group = OTHER
    elif reason == "left":
        hub.member_presence(GROUP, COLLEAGUE, left=True)
    elif reason == "expired":
        hub.store.update("notice:" + GROUP + ":" + row["token"],
                         lambda r: dict(r, created_at=time.time() - 8 * 86400, expires_at=time.time() - 86400))
    elif reason == "disabled":
        hub.store.put("ack-settings", {"groups": {GROUP: {"acknowledgements": "off"}}})
    else:
        hub.unsend({"source": {"groupId": GROUP}, "unsend": {"messageId": row["factory_event"]["message_id"]}})
    before = hub.store.get("notice:" + GROUP + ":" + row["token"])
    assert hub.postback(event(uid=COLLEAGUE, group=group), params(row))
    assert hub.store.get("notice:" + GROUP + ":" + row["token"]) == before
    assert private.sent == replies == [] and len(group_sends) == 1


def test_failed_receipt_commit_never_sends_a_success_confirmation(hub, private, monkeypatch):
    row, _, replies = start(hub)
    key = "notice:" + GROUP + ":" + row["token"]
    original = hub.store.compare_swap
    def fail(name, previous, current, ttl):
        if name == key and current.get("responses"):
            assert current["receipt_confirmations"][COLLEAGUE]["state"] == "pending"
            raise StoreError("injected receipt commit failure")
        return original(name, previous, current, ttl)
    with monkeypatch.context() as patch:
        patch.setattr(hub.store, "compare_swap", fail)
        with pytest.raises(StoreError):
            answer(hub, row)
    assert stored(hub, row)["responses"] == {} and notification(hub, row) == {}
    assert private.sent == replies == []
    answer(hub, row)
    answer(hub, row)
    assert len(private.sent) == 1


def test_concurrent_first_taps_across_process_instances_push_only_once(hub, private, monkeypatch):
    row, _, replies = start(hub)
    other = factory.FactoryHub(hub.app, hub.h, hub.store)
    key, original = "notice:" + GROUP + ":" + row["token"], hub.store.compare_swap
    barrier, local = threading.Barrier(2), threading.local()
    conflicts = []
    def compare(name, previous, current, ttl):
        if name == key and not getattr(local, "checked", False):
            local.checked = True
            barrier.wait(timeout=5)
        ok = original(name, previous, current, ttl)
        conflicts.append(ok)
        return ok
    monkeypatch.setattr(hub.store, "compare_swap", compare)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(worker.postback, event(uid=COLLEAGUE, stamp=stamp), params(row))
                for worker, stamp in ((hub, 200), (other, 300))]
        assert all(job.result(timeout=10) for job in jobs)
    assert False in conflicts and len(private.sent) == 1 and replies == []
    assert notification(hub, row)["state"] == "accepted" and assert_finished(hub, row)


def test_timeout_keeps_ack_and_retries_same_frozen_message_after_restart(hub, private):
    row, group_sends, replies = start(hub)
    def timeout(*args):
        private.sent.append(copy.deepcopy(args))
        raise TimeoutError("injected ambiguous LINE acceptance")
    hub.h["_factory_receipt_sender"] = timeout
    with pytest.raises(StoreError):
        answer(hub, row)
    first = assert_finished(hub, row)
    assert notification(hub, row)["state"] == "pending"
    assert len(private.sent) == 1 and replies == []
    with pytest.raises(StoreError):
        answer(hub, row)  # Backoff does not start a second send.
    assert len(private.sent) == 1 and stored(hub, row) == first
    private.now = notification(hub, row)["next_attempt_at"] + 0.01
    fresh = factory.FactoryHub(hub.app, hub.h, hub.store)
    fresh.h["_factory_receipt_sender"] = lambda *args: private.sent.append(copy.deepcopy(args))
    answer(fresh, row)
    answer(fresh, row)
    assert private.sent[0] == private.sent[1] and len(private.sent) == 2
    assert notification(hub, row)["state"] == "accepted"
    assert stored(hub, row)["responses"] == first["responses"]
    assert len(group_sends) == 1 and replies == []


def test_send_lease_blocks_a_concurrent_duplicate_and_never_changes_first_response(hub, private):
    row, _, _ = start(hub)
    entered, release = threading.Event(), threading.Event()
    def blocked(*args):
        private.sent.append(copy.deepcopy(args))
        entered.set()
        assert release.wait(5)
    hub.h["_factory_receipt_sender"] = blocked
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(answer, hub, row)
        assert entered.wait(5)
        before = stored(hub, row)
        try:
            with pytest.raises(StoreError):
                answer(hub, row)
            assert stored(hub, row) == before and len(private.sent) == 1
        finally:
            release.set()
        first.result(timeout=5)
    answer(hub, row)
    assert len(private.sent) == 1 and notification(hub, row)["state"] == "accepted"


@pytest.mark.parametrize("failure", [StoreError, sqlite3.OperationalError])
def test_accepted_push_with_failed_checkpoint_recovers_using_identical_key(hub, private, monkeypatch, failure):
    row, _, replies = start(hub)
    original = hub.store.compare_swap
    def fail(name, previous, current, ttl):
        if current and current.get("receipt_confirmations", {}).get(COLLEAGUE, {}).get("state") == "accepted":
            raise failure("injected confirmation checkpoint outage")
        return original(name, previous, current, ttl)
    with monkeypatch.context() as patch:
        patch.setattr(hub.store, "compare_swap", fail)
        with pytest.raises(StoreError):
            answer(hub, row)
    first_response = stored(hub, row)["responses"]
    assert notification(hub, row)["state"] == "sending" and len(private.sent) == 1
    private.now = notification(hub, row)["next_attempt_at"] + 0.01
    answer(hub, row)
    assert len(private.sent) == 2 and private.sent[0] == private.sent[1]
    assert notification(hub, row)["state"] == "accepted"
    assert stored(hub, row)["responses"] == first_response and replies == []


def test_legacy_help_or_management_actions_cannot_rearm_accepted_private_ack(hub, private):
    row, group_sends, replies = start(hub)
    for stamp, action in enumerate(("factory_help", "factory_ack", "factory_help", "factory_ack", "factory_ack"), 100):
        hub.postback(event(uid=COLLEAGUE, stamp=stamp), params(row, action=action))
    saved = notification(hub, row)
    hub.postback(event(uid=USER), params(row, action="factory_receipts"))
    hub.postback(event(uid=USER), params(row, action="factory_stop"))
    answer(hub, row)
    assert len(private.sent) == 1 and notification(hub, row) == saved
    assert len(group_sends) == 1 and len(replies) == 1  # Only the owner's explicit status query.


def test_old_recorded_ack_is_not_retroactively_notified_after_upgrade(hub, private):
    row, _, replies = start(hub)
    old = {"status": "understood", "name": "原姓名", "at": row["created_at"], "event_timestamp": 80}
    hub.store.update("notice:" + GROUP + ":" + row["token"], lambda r: dict(r, responses={COLLEAGUE: old}))
    fresh = factory.FactoryHub(hub.app, hub.h, hub.store)
    answer(fresh, row)
    assert private.sent == replies == [] and notification(hub, row) == {}
    assert stored(hub, row)["responses"][COLLEAGUE] == old


@pytest.mark.parametrize("change", ["recipient_removed", "departed", "disabled", "unsent"])
def test_permission_change_between_claim_and_line_io_blocks_private_send(hub, private, monkeypatch, change):
    row, _, replies = start(hub)
    original = hub.store.update
    fired = []
    def update(key, callback, ttl=604800):
        result = original(key, callback, ttl)
        item = (result or {}).get("receipt_confirmations", {}).get(COLLEAGUE, {})
        if not fired and item.get("state") == "sending":
            fired.append(True)
            if change == "recipient_removed":
                original(key, lambda r: dict(r, expected={}))
            elif change == "departed":
                hub.member_presence(GROUP, COLLEAGUE, left=True)
            elif change == "disabled":
                hub.store.put("ack-settings", {"groups": {GROUP: {"acknowledgements": "off"}}})
            else:
                hub.unsend({"source": {"groupId": GROUP}, "unsend": {"messageId": row["factory_event"]["message_id"]}})
        return result
    monkeypatch.setattr(hub.store, "update", update)
    answer(hub, row)
    assert fired and private.sent == replies == []
    if change != "unsent":
        assert notification(hub, row)["state"] == "cancelled"


def test_receipt_after_manual_stop_notifies_only_actor_without_restarting_group_schedule(hub, private):
    row, group_sends, replies = start(hub)
    hub.stop_notice(GROUP, row["token"], USER)
    answer(hub, row)
    answer(hub, row)
    tick(hub, row["reminder_due_at"] + 3600)
    saved = stored(hub, row)
    assert saved["reminder_state"] == "stopped" and saved["wake_at"] is None
    assert notification(hub, row)["state"] == "accepted"
    assert len(private.sent) == 1 and len(group_sends) == 1 and replies == []


@pytest.mark.parametrize("reason", ["off", "departed", "removed", "expired"])
def test_pending_private_retry_respects_current_permissions_and_expiry(hub, private, reason):
    row, _, replies = start(hub)
    hub.h["_factory_receipt_sender"] = lambda *a: (_ for _ in ()).throw(TimeoutError())
    with pytest.raises(StoreError):
        answer(hub, row)
    key = "notice:" + GROUP + ":" + row["token"]
    private.now = notification(hub, row)["next_attempt_at"] + 1
    hub.h["_factory_receipt_sender"] = lambda *args: private.sent.append(args)
    if reason == "off":
        hub.store.put("ack-settings", {"groups": {GROUP: {"acknowledgements": "off"}}})
    elif reason == "departed":
        hub.member_presence(GROUP, COLLEAGUE, left=True)
    elif reason == "removed":
        hub.store.update(key, lambda r: dict(r, expected={}))
    else:
        hub.store.update(key, lambda r: dict(r, created_at=time.time() - 8 * 86400, expires_at=time.time() - 1))
    before = stored(hub, row)
    answer(hub, row)
    assert stored(hub, row) == before and private.sent == replies == []


def test_removed_help_cannot_cancel_a_pending_once_only_private_confirmation(hub, private):
    row, group_sends, replies = start(hub)
    hub.h["_factory_receipt_sender"] = lambda *a: (_ for _ in ()).throw(TimeoutError())
    with pytest.raises(StoreError):
        answer(hub, row)
    before = stored(hub, row)
    hub.postback(event(uid=COLLEAGUE, stamp=500), params(row, action="factory_help"))
    assert stored(hub, row) == before
    private.now = notification(hub, row)["next_attempt_at"] + 1
    hub.h["_factory_receipt_sender"] = lambda *args: private.sent.append(args)
    answer(hub, row)
    answer(hub, row)
    assert len(private.sent) == 1 and len(group_sends) == 1 and replies == []
    assert stored(hub, row)["responses"][COLLEAGUE]["status"] == "understood"


def test_private_retry_stops_before_line_deduplication_window_expires(hub, private):
    row, _, _ = start(hub)
    def timeout(*args):
        private.sent.append(args)
        raise TimeoutError()
    hub.h["_factory_receipt_sender"] = timeout
    with pytest.raises(StoreError):
        answer(hub, row)
    private.now = notification(hub, row)["created_at"] + receipts.RETRY_WINDOW
    answer(hub, row)
    answer(hub, row)
    assert notification(hub, row)["state"] == "uncertain" and len(private.sent) == 1
    assert_finished(hub, row)


@pytest.mark.parametrize("status", [200, 409, 400, 401, 403, 429, 500])
def test_real_line_push_transport_uses_user_target_retry_key_and_acceptance_semantics(hub, private, monkeypatch, status):
    row, _, replies = start(hub)
    requests = []
    def urlopen(request, timeout):
        requests.append(request)
        assert timeout == 12 and request.full_url == "https://api.line.me/v2/bot/message/push"
        body = json.loads(request.data)
        assert body["to"] == COLLEAGUE and len(body["messages"]) == 1
        assert body.get("notificationDisabled", False) is False
        assert "replyToken" not in body and uuid.UUID(request.get_header("X-line-retry-key"))
        if status == 200:
            response = io.BytesIO(b'{}')
            response.status = 200
            return response
        raise urllib.error.HTTPError(request.full_url, status, "fake LINE status",
                                     {"x-line-accepted-request-id": "already-accepted"} if status == 409 else {}, None)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "offline-test-token")
    monkeypatch.setattr(reminders.urllib.request, "urlopen", urlopen)
    hub.h.pop("_factory_receipt_sender")
    if status in (429, 500):
        with pytest.raises(StoreError):
            answer(hub, row)
        assert notification(hub, row)["state"] == "pending"
    else:
        answer(hub, row)
        answer(hub, row)
        assert notification(hub, row)["state"] == ("accepted" if status in (200, 409) else "failed")
    assert len(requests) == 1 and replies == [] and assert_finished(hub, row)


def test_signed_webhook_failure_is_recovered_without_requesting_another_user_tap(hub, private, monkeypatch):
    import app
    from linebot.v3 import WebhookHandler
    from linebot.v3.webhooks import PostbackEvent
    import webhook_runtime
    import translation_retry_queue as queue
    from test_webhook_latency_recovery import SECRET, sign

    row, group_sends, replies = start(hub)
    monkeypatch.setattr(app, "factory_hub", hub)
    monkeypatch.setattr(app, "_processed_msg_ids", app._collections_dedup.OrderedDict())
    monkeypatch.setenv("LINE_WEBHOOK_ASYNC", "0")
    monkeypatch.setattr(app.app, "testing", False)
    handler = WebhookHandler(SECRET)
    handler.add(PostbackEvent)(app.handle_postback)
    inbox = webhook_runtime.WebhookInbox(handler, app._release_webhook_message_claims)
    monkeypatch.setattr(inbox.pool, "ensure_started", lambda: None)
    monkeypatch.setattr(app, "_WEBHOOK_INBOX", inbox)
    raw = {"type": "postback", "mode": "active", "timestamp": 1000,
           "webhookEventId": "one-private-ack", "replyToken": "group-reply-token",
           "deliveryContext": {"isRedelivery": False},
           "source": {"type": "group", "groupId": GROUP, "userId": COLLEAGUE},
           "postback": {"data": "action=factory_ack&token=" + row["token"]}}
    body = json.dumps({"destination": "U" + "f" * 32, "events": [raw]})
    def timeout(*args):
        private.sent.append(copy.deepcopy(args))
        raise TimeoutError("fake LINE uncertainty")
    hub.h["_factory_receipt_sender"] = timeout
    client = app.app.test_client()
    response = client.post("/callback", data=body, headers={"X-Line-Signature": sign(body)})
    assert response.status_code == 500
    first = stored(hub, row)["responses"]
    assert first[COLLEAGUE]["status"] == "understood" and len(private.sent) == 1
    jobs = [j for j in queue.list_pending() if j["job_kind"] == "webhook"]
    assert len(jobs) == 1 and jobs[0]["payload"]["body"] == body
    private.now = notification(hub, row)["next_attempt_at"] + 1
    hub.h["_factory_receipt_sender"] = lambda *args: private.sent.append(copy.deepcopy(args))
    key = jobs[0]["job_key"]
    assert queue.claim_job(key, owner="private-ack-recovery")
    assert inbox.run_job(queue.get(key), "private-ack-recovery")
    assert queue.was_delivered(key) and notification(hub, row)["state"] == "accepted"
    assert stored(hub, row)["responses"] == first and private.sent[0] == private.sent[1]
    raw["webhookEventId"] = "a-second-physical-tap"
    body = json.dumps({"destination": "U" + "f" * 32, "events": [raw]})
    assert client.post("/callback", data=body, headers={"X-Line-Signature": sign(body)}).status_code == 200
    assert len(private.sent) == 2 and len(group_sends) == 1 and replies == []
