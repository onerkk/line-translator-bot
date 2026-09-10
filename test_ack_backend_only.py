"""Receipts and unauthorized management stay silent; reminders keep their due time."""
import copy
import json
import time

import pytest

import line_factory_features as factory
from line_factory_store import StoreError
from test_line_factory_features import hub, storage, event, GROUP, OTHER, USER, COLLEAGUE
from test_ack_recipient_scope import begin, tagged, THIRD, FOURTH
from test_ack_repeat_controls import stored, tick
from test_ack_pending_mentions import recipients

CONTROLS = ("factory_ack", "factory_help", "factory_receipts", "factory_stop")
OUTSIDER = "U" + "9" * 32


@pytest.mark.parametrize("scope", ["mentioned", "all"])
@pytest.mark.parametrize("action", CONTROLS)
def test_outside_audience_has_no_reply_profile_lookup_or_member_record(hub, monkeypatch, scope, action):
    row, sent, replies, _ = begin(hub, [tagged("@Adi", COLLEAGUE)] if scope == "mentioned" else [])
    before, members = stored(hub, row), hub.known_members(GROUP)
    writes = []
    monkeypatch.setattr(hub, "observe_members", lambda ev: writes.append("observed"))
    hub.h["get_display_name"] = lambda *args: writes.append("profile") or "Unexpected"
    actors = (OUTSIDER, "", "invalid") + ((USER,) if action in {"factory_ack", "factory_help"} else ())
    for uid in actors:
        assert hub.postback(event(uid=uid), {"action": action, "token": row["token"]})
    assert stored(hub, row) == before
    assert hub.known_members(GROUP) == members and OUTSIDER not in members
    assert replies == [] and len(sent) == 1 and writes == []


def test_only_scheduled_deadline_posts_and_only_unanswered_members_are_mentioned(hub):
    row, sent, replies, _ = begin(hub, [tagged("@Adi", COLLEAGUE), tagged("@Budi", THIRD), tagged("@Siti", FOURTH)])
    key = "notice:" + GROUP + ":" + row["token"]
    due = row["delivered_at"] + 480 * 60
    hub.store.update(key, lambda r: dict(r, reminder_minutes=480, reminder_due_at=due,
                                       next_reminder_at=due, wake_at=due))
    for uid, action in ((COLLEAGUE, "factory_ack"), (THIRD, "factory_help"),
                        (FOURTH, "factory_receipts"), (FOURTH, "factory_stop"),
                        (COLLEAGUE, "factory_ack"), (OUTSIDER, "factory_ack")):
        hub.postback(event(uid=uid), {"action": action, "token": row["token"]})
        assert len(sent) == 1 and replies == []
        assert stored(hub, row)["wake_at"] == due
    data = hub.app.test_client().get("/api/admin/factory/receipts?group_id=" + GROUP).json["notices"][0]
    assert data["pending_ids"] == [FOURTH]
    assert set(data["responses"]) == {COLLEAGUE, THIRD}
    assert FOURTH not in data["status_views"]
    fresh = factory.FactoryHub(hub.app, hub.h, hub.store)
    fresh.reminders.sender = hub.reminders.sender
    tick(fresh, due - 0.001)
    assert len(sent) == 1
    tick(fresh, due)
    assert len(sent) == 2 and recipients(sent[-1]) == [FOURTH] and replies == []
    fresh.postback(event(uid=FOURTH), {"action": "factory_ack", "token": row["token"]})
    tick(fresh, due + 480 * 60)
    assert len(sent) == 2 and stored(hub, row)["reminder_state"] == "no_pending"


@pytest.mark.parametrize("action", CONTROLS)
def test_expired_cross_group_and_unidentified_controls_are_silent(hub, action):
    row, sent, replies, _ = begin(hub, [tagged("@Adi", COLLEAGUE)])
    before = stored(hub, row)
    for ev, token in ((event(uid=COLLEAGUE, group=OTHER), row["token"]),
                      (event(uid=""), row["token"]), (event(uid=COLLEAGUE), "expired-token")):
        assert hub.postback(ev, {"action": action, "token": token})
    assert stored(hub, row) == before and len(sent) == 1 and replies == []


def test_receipt_storage_failure_is_retryable_without_group_error_message(hub, monkeypatch):
    row, sent, replies, _ = begin(hub, [tagged("@Adi", COLLEAGUE)])
    key = "notice:" + GROUP + ":" + row["token"]
    original = hub.store.compare_swap
    def unavailable(name, *args):
        if name == key:
            raise StoreError("storage temporarily unavailable")
        return original(name, *args)
    with monkeypatch.context() as patch:
        patch.setattr(hub.store, "compare_swap", unavailable)
        with pytest.raises(StoreError):
            hub.postback(event(uid=COLLEAGUE), {"action": "factory_ack", "token": row["token"]})
    assert stored(hub, row)["responses"] == {} and replies == [] and len(sent) == 1
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_ack", "token": row["token"]})
    assert COLLEAGUE in stored(hub, row)["responses"] and replies == []


def test_unlisted_tap_cannot_rebuild_a_missing_notice(hub):
    row, sent, replies, _ = begin(hub, [tagged("@Adi", COLLEAGUE)])
    key = "notice:" + GROUP + ":" + row["token"]
    hub.store.delete(key)
    before = hub.known_members(GROUP)
    for action in CONTROLS:
        hub.postback(event(uid=OUTSIDER), {"action": action, "token": row["token"]})
    assert hub.store.get(key) is None and hub.known_members(GROUP) == before
    assert replies == [] and len(sent) == 1


def test_receipt_recovery_never_exposes_an_initial_resend_to_worker(hub, monkeypatch):
    row, sent, replies, _ = begin(hub, [tagged("@Adi", COLLEAGUE), tagged("@Budi", THIRD)])
    key = "notice:" + GROUP + ":" + row["token"]
    hub.store.delete(key)
    original, checkpoints = hub.store.save_interaction, []
    def checkpoint(*args, **kwargs):
        result = original(*args, **kwargs)
        saved = hub.store.get(key)
        if saved:
            checkpoints.append(copy.deepcopy(saved))
            tick(hub, time.time())  # Worker runs between repair and response CAS.
        return result
    monkeypatch.setattr(hub.store, "save_interaction", checkpoint)
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_ack", "token": row["token"]})
    assert checkpoints and all(r["delivery_state"] == "delivered" for r in checkpoints)
    assert stored(hub, row)["wake_at"] > time.time() and len(sent) == 1 and replies == []


def test_controls_have_no_client_chat_text_and_status_label_describes_backend(hub):
    row, sent, _, _ = begin(hub, [tagged("@Adi", COLLEAGUE)])
    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "postback" and "factory_" in value.get("data", ""):
                assert not any(key in value for key in ("displayText", "text", "inputOption", "fillInText"))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(sent[0][1])
    assert "查看回覆/Status" in json.dumps(sent[0][1], ensure_ascii=False)
    assert "按了解只記錄" not in json.dumps(sent[0][1], ensure_ascii=False)


def test_transport_guard_blocks_nested_notice_feedback_and_resets_for_factory_tools(hub, monkeypatch):
    replies = []
    hub.h["_send_reply_with_push_fallback"] = lambda **kw: replies.append(kw)
    with monkeypatch.context() as patch:
        def nested(ev, params):
            hub._reply(ev, "Old receipt feedback must never reach LINE")
            return True
        patch.setattr(hub, "_postback", nested)
        for action in CONTROLS:
            assert hub.postback(event(uid=COLLEAGUE), {"action": action, "token": "old-notice"})
        assert replies == []
    assert hub.postback(event(uid=COLLEAGUE), {"action": "factory_open"})
    assert len(replies) == 1 and "工廠工具" in replies[0]["fallback_text"]


@pytest.mark.parametrize("action", ["factory_ack", "factory_help", "factory_receipts"])
def test_audience_change_during_cas_cannot_commit_an_unlisted_action(hub, monkeypatch, action):
    row, sent, replies, _ = begin(hub, [tagged("@Adi", COLLEAGUE), tagged("@Budi", THIRD)])
    key = "notice:" + GROUP + ":" + row["token"]
    original, raced = hub.store.compare_swap, []
    def compare(name, previous, current, ttl):
        if name == key and not raced:
            raced.append(True)
            hub.store.update(key, lambda r: dict(r, recipient_ids=[THIRD], expected={THIRD: "Budi"}))
        return original(name, previous, current, ttl)
    monkeypatch.setattr(hub.store, "compare_swap", compare)
    hub.postback(event(uid=COLLEAGUE), {"action": action, "token": row["token"]})
    saved = stored(hub, row)
    assert bool(raced) == (action != "factory_receipts")  # Status is rejected before CAS for a non-manager.
    assert COLLEAGUE not in saved["responses"] and COLLEAGUE not in saved.get("status_views", {})
    assert replies == [] and len(sent) == 1


def test_status_view_is_idempotent_and_does_not_reschedule_or_count_as_ack(hub):
    row, sent, replies, _ = begin(hub, [tagged("@Adi", COLLEAGUE)])
    for stamp in (100, 200, 300):
        hub.postback(event(uid=USER, stamp=stamp), {"action": "factory_receipts", "token": row["token"]})
        saved = stored(hub, row)
        if stamp == 100:
            first = copy.deepcopy(saved)
        assert saved == first and saved["responses"] == {} and saved["wake_at"] == row["wake_at"]
    tick(hub, row["reminder_due_at"])
    assert len(sent) == 2 and recipients(sent[-1]) == [COLLEAGUE] and len(replies) == 3
