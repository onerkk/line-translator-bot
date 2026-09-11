"""Early-stop cards, real SQLite/Redis CAS, and recovery without another tap."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import json
import threading
import time
import urllib.error
import uuid

import pytest
from linebot.v3.messaging import FlexMessage

import line_ack_reminders as reminders
import line_ack_stopped as stopped
import line_factory_features as factory
from line_factory_store import StoreError
from line_message_ui import stopped_message
from test_line_factory_features import hub, storage, event, GROUP, OTHER, USER, COLLEAGUE
from test_ack_known_zero_stop import start, answer
from test_ack_repeat_controls import THIRD, stored, tick


def item(hub, row):
    return stored(hub, row).get("stop_notification", {})


def stop(hub, row, *, uid=USER, group=GROUP):
    return hub.postback(event(uid=uid, group=group), {"action": "factory_stop", "token": row["token"]})


def test_stop_commits_before_one_compact_group_card_and_keeps_responses(hub):
    row, scheduled, replies = start(hub, people=(COLLEAGUE, THIRD))
    answer(hub, row)
    responses = stored(hub, row)["responses"]
    sent = hub.h["factory_stop_sends"]
    def send(group, messages, key):
        saved = stored(hub, row)
        assert saved["reminder_state"] == "stopped" and saved["reminder_stopped_at"]
        assert saved["wake_at"] is saved["pending_batch"] is saved["next_reminder_at"] is None
        assert saved["stop_notification"]["state"] == "sending"
        assert group == GROUP and uuid.UUID(key)
        assert len(messages) == 1 and FlexMessage.from_dict(messages[0]).to_dict() == messages[0]
        sent.append(copy.deepcopy((group, messages, key)))
    hub.h["_factory_stop_sender"] = send
    # Card creation/delivery must not translate or fetch a profile/roster.
    def forbidden(*args, **kwargs):
        pytest.fail("stop notification attempted AI/profile/roster I/O")
    hub.h.update(translate=forbidden, _factory_member_ids=forbidden, _factory_member_count=forbidden)
    hub._member_name = forbidden
    assert stop(hub, row)
    saved = stored(hub, row)
    assert saved["responses"] == responses and item(hub, row)["state"] == "accepted"
    assert not hub.h["factory_completion_sends"]
    for _ in range(3):
        stop(hub, row)
    assert len(sent) == 1 and stored(hub, row) == saved
    tick(hub, row["reminder_due_at"] + 3600)
    assert len(scheduled) == 1 and replies == []
    assert not hub.store.due_notices(row["expires_at"] - 1)


def test_admin_endpoint_announces_in_original_group_and_preserves_first_actor(hub):
    row, _, _ = start(hub)
    client = hub.app.test_client()
    data = {"group_id": GROUP, "token": row["token"]}
    assert client.post("/api/admin/factory/receipts/stop", json=data,
                       headers={"X-Manager-Id": "first-admin"}).status_code == 200
    saved = stored(hub, row)
    assert saved["stopped_by"] == "first-admin"
    assert client.post("/api/admin/factory/receipts/stop", json=data,
                       headers={"X-Manager-Id": "second-admin"}).status_code == 200
    assert stored(hub, row) == saved
    assert len(hub.h["factory_stop_sends"]) == 1 and hub.h["factory_stop_sends"][0][0] == GROUP


@pytest.mark.parametrize("reason", ["recipient", "outsider", "foreign", "expired", "edited", "admin_denied"])
def test_unauthorized_or_obsolete_stop_cannot_announce_success(hub, reason):
    row, _, replies = start(hub)
    uid, group = USER, GROUP
    if reason in {"recipient", "outsider"}:
        uid = COLLEAGUE if reason == "recipient" else THIRD
    elif reason == "foreign":
        group = OTHER
    elif reason == "expired":
        hub.store.update("notice:" + GROUP + ":" + row["token"],
                         lambda r: dict(r, created_at=time.time() - 8 * 86400, expires_at=time.time() - 86400))
    elif reason == "edited":
        hub.unsend({"source": {"groupId": GROUP}, "unsend": {"messageId": row["factory_event"]["message_id"]}})
    before = stored(hub, row)
    if reason == "admin_denied":
        hub.h["check_manager_access"] = lambda _: False
        response = hub.app.test_client().post("/api/admin/factory/receipts/stop",
                                              json={"group_id": GROUP, "token": row["token"]})
        assert response.status_code == 403
    else:
        stop(hub, row, uid=uid, group=group)
    assert stored(hub, row) == before and not hub.h["factory_stop_sends"] and replies == []


@pytest.mark.parametrize("state", ["all_confirmed", "empty_completed", "old_manual_stop", "not_delivered"])
def test_only_a_new_early_stop_creates_a_card(hub, state):
    row, _, _ = start(hub)
    if state == "all_confirmed":
        answer(hub, row)
        assert len(hub.h["factory_completion_sends"]) == 1
    else:
        changes = {
            "empty_completed": dict(expected={}, reminder_state="no_pending", reminder_completed_at=time.time(), wake_at=None),
            "old_manual_stop": dict(reminder_stopped_at=time.time(), stopped_by="old-admin", reminder_state="stopped", wake_at=None),
            "not_delivered": dict(delivery_state="pending", translation_pending=True),
        }[state]
        hub.store.update("notice:" + GROUP + ":" + row["token"], lambda r: dict(r, **changes))
    stop(hub, row)
    assert not item(hub, row) and not hub.h["factory_stop_sends"]


def test_failed_stop_commit_cannot_send_card(hub, monkeypatch):
    row, _, _ = start(hub)
    original = hub.store.compare_swap
    def fail(key, previous, current, ttl):
        if (current or {}).get("reminder_stopped_at"):
            raise StoreError("stop write failed")
        return original(key, previous, current, ttl)
    with monkeypatch.context() as patch:
        patch.setattr(hub.store, "compare_swap", fail)
        with pytest.raises(StoreError):
            stop(hub, row)
    assert not stored(hub, row).get("reminder_stopped_at") and not hub.h["factory_stop_sends"]
    stop(hub, row)
    assert len(hub.h["factory_stop_sends"]) == 1


def test_signed_stop_tap_recovers_an_uncertain_initial_delivery_without_resending_it(hub):
    row, scheduled, _ = start(hub)
    hub.store.update("notice:" + GROUP + ":" + row["token"], lambda r: dict(
        r, delivery_state="pending", delivered_at=None, reminder_state="retrying",
        lease_id="old-initial-sender", pending_batch={"initial": True}, wake_at=time.time()))
    stop(hub, row)
    saved = stored(hub, row)
    assert saved["delivery_state"] == "delivered" and saved["reminder_state"] == "stopped"
    assert saved["lease_id"] == "" and saved["pending_batch"] is None
    assert item(hub, row)["state"] == "accepted" and len(hub.h["factory_stop_sends"]) == 1
    tick(hub, row["reminder_due_at"] + 60)
    assert len(scheduled) == 1


def test_concurrent_stops_share_one_live_lease_and_card(hub):
    row, _, _ = start(hub)
    entered, release = threading.Event(), threading.Event()
    sent = hub.h["factory_stop_sends"]
    def blocked(*args):
        sent.append(copy.deepcopy(args))
        entered.set()
        assert release.wait(5)
    hub.h["_factory_stop_sender"] = blocked
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(stop, hub, row)
        try:
            assert entered.wait(3)
            pool.submit(stop, hub, row).result(timeout=3)
        finally:
            release.set()
        first.result(timeout=3)
    assert len(sent) == 1 and item(hub, row)["state"] == "accepted"


def test_timeout_recovers_after_restart_without_another_click_or_any_reminder(hub):
    row, scheduled, replies = start(hub)
    sent = []
    def timeout(*args):
        sent.append(copy.deepcopy(args))
        raise TimeoutError("LINE might have accepted this request")
    hub.h["_factory_stop_sender"] = timeout
    response = hub.app.test_client().post("/api/admin/factory/receipts/stop",
                                          json={"group_id": GROUP, "token": row["token"]})
    assert response.status_code == 200  # The stop itself succeeded.
    saved = stored(hub, row)
    assert saved["reminder_state"] == "stopped" and saved["wake_at"] is None
    due = item(hub, row)["next_attempt_at"]
    assert not hub.store.due_notices(due - 1)
    assert [r["token"] for r in hub.store.due_notices(due)] == [row["token"]]
    fresh = factory.FactoryHub(hub.app, hub.h, hub.store)
    fresh.reminders.sender = lambda *a: pytest.fail("stopped notice sent a reminder")
    fresh.h["_factory_stop_sender"] = lambda *args: sent.append(copy.deepcopy(args))
    tick(fresh, due)
    assert len(sent) == 2 and sent[0] == sent[1]
    assert item(hub, row)["state"] == "accepted" and not hub.store.due_notices(row["expires_at"] - 1)
    assert len(scheduled) == 1 and replies == []


def test_accepted_send_with_lost_checkpoint_reuses_payload_and_key(hub, monkeypatch):
    row, _, _ = start(hub)
    original = hub.store.compare_swap
    def fail(key, previous, current, ttl):
        if (current or {}).get("stop_notification", {}).get("state") == "accepted":
            raise StoreError("checkpoint interrupted after LINE accepted")
        return original(key, previous, current, ttl)
    with monkeypatch.context() as patch:
        patch.setattr(hub.store, "compare_swap", fail)
        stop(hub, row)
    assert item(hub, row)["state"] == "sending"
    stop(hub, row)
    sent = hub.h["factory_stop_sends"]
    assert len(sent) == 1
    tick(hub, item(hub, row)["next_attempt_at"])
    assert len(sent) == 2 and sent[0] == sent[1] and item(hub, row)["state"] == "accepted"


@pytest.mark.parametrize("reason", ["disabled", "edited", "bot_left", "expired", "retry_window"])
def test_pending_notification_rechecks_lifecycle_and_stops_retrying(hub, reason):
    row, _, _ = start(hub)
    sent = []
    def timeout(*args):
        sent.append(copy.deepcopy(args))
        raise TimeoutError()
    hub.h["_factory_stop_sender"] = timeout
    stop(hub, row)
    due = item(hub, row)["next_attempt_at"]
    if reason == "disabled":
        hub.store.put("ack-settings", {"groups": {GROUP: {"acknowledgements": "off"}}})
    elif reason == "edited":
        hub.unsend({"source": {"groupId": GROUP}, "unsend": {"messageId": row["factory_event"]["message_id"]}})
    elif reason == "bot_left":
        hub.store.put("bot-left:" + GROUP, {"at": time.time()})
    elif reason == "expired":
        hub.store.update("notice:" + GROUP + ":" + row["token"], lambda r: dict(r, expires_at=due - 1))
    else:
        due = item(hub, row)["created_at"] + stopped.RETRY_WINDOW
    tick(hub, due)
    assert len(sent) == 1
    if reason != "edited":
        assert item(hub, row)["state"] == ("uncertain" if reason == "retry_window" else "cancelled")
    assert not hub.store.due_notices(due + 1)


def test_group_disabled_after_delivery_claim_prevents_send(hub, monkeypatch):
    row, _, _ = start(hub)
    original, changed = hub.store.update, []
    def update(*args, **kwargs):
        result = original(*args, **kwargs)
        if not changed and (result or {}).get("stop_notification", {}).get("state") == "sending":
            changed.append(True)
            hub.store.put("ack-settings", {"groups": {GROUP: {"acknowledgements": "off"}}})
        return result
    monkeypatch.setattr(hub.store, "update", update)
    stop(hub, row)
    assert changed and not hub.h["factory_stop_sends"] and item(hub, row)["state"] == "cancelled"


@pytest.mark.parametrize("status", [200, 409, 400, 429, 500])
def test_stop_uses_real_line_push_protocol_and_retry_acceptance(hub, monkeypatch, status):
    row, _, _ = start(hub)
    requests = []
    def urlopen(request, timeout):
        requests.append(request)
        payload = json.loads(request.data)
        assert payload["to"] == GROUP and len(payload["messages"]) == 1
        assert payload["messages"][0]["type"] == "flex" and "replyToken" not in payload
        assert uuid.UUID(request.get_header("X-line-retry-key")) and timeout == 12
        if status == 200:
            response = io.BytesIO(b'{}')
            response.status = 200
            return response
        raise urllib.error.HTTPError(request.full_url, status, "test", {
            "x-line-accepted-request-id": "already-accepted"} if status == 409 else {}, None)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "offline-test-token")
    monkeypatch.setattr(reminders.urllib.request, "urlopen", urlopen)
    hub.h.pop("_factory_stop_sender")
    stop(hub, row)
    stop(hub, row)
    expected = "accepted" if status in {200, 409} else "pending" if status in {429, 500} else "failed"
    assert item(hub, row)["state"] == expected and len(requests) == 1


def test_card_is_small_bilingual_sdk_valid_and_contains_no_actions_or_work_completion_claim():
    at = datetime(2026, 9, 11, 0, 5, tzinfo=timezone.utc).timestamp()
    message = stopped_message({"token": "abcdefgh", "original": "👷" * 1500,
                               "translated": "large text " * 1000, "sender_name": "name" * 100}, at)
    assert FlexMessage.from_dict(message).to_dict() == message
    encoded = json.dumps(message, ensure_ascii=False)
    assert len(encoded.encode()) < 3500 and message["contents"]["size"] == "kilo"
    for text in ("已停止作業追蹤", "Pemantauan dihentikan", "確認紀錄保留", "#abcdef", "09/11 08:05"):
        assert text in encoded
    assert all(text not in encoded for text in ("全員確認完畢", "作業完成", "100%", "action", "substitution", "image", "large text"))
