"""Known pending == 0 cancels reminders, including old rows and send races.

Real SQLite/Redis CAS and due indexes; only LINE transport is substituted.
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

import line_ack_reminders as reminders
import line_factory_features as factory
from test_line_factory_features import hub, storage, event, GROUP, USER, COLLEAGUE
from test_line_ack_commands import command, configure
from test_ack_repeat_controls import THIRD, stored, tick
from test_ack_reminder_roster import unavailable


def start(hub, *, repeat=True, count=None, people=(COLLEAGUE,)):
    configure(hub, 1, repeat=repeat)
    hub.h["group_user_names"][GROUP] = {USER: "發起人", **{uid: "同事" for uid in people}}
    hub.h["_factory_member_ids"] = unavailable
    hub.h["_factory_member_count"] = lambda group: count
    sent, replies = [], []
    hub.reminders.sender = lambda *args: sent.append(copy.deepcopy(args))
    hub.h["_send_reply_with_push_fallback"] = lambda **kw: replies.append(kw)
    row = command(hub)
    return row, sent, replies


def answer(hub, row, uid=COLLEAGUE):
    hub.postback(event(uid=uid), {"action": "factory_ack", "token": row["token"]})


def assert_finished(hub, row):
    saved = stored(hub, row)
    assert saved["reminder_state"] == "no_pending"
    assert saved["wake_at"] is saved["next_reminder_at"] is saved["pending_batch"] is None
    assert saved["lease_id"] == "" and saved["reminder_completed_at"] > 0
    assert not hub.store.due_notices(saved["expires_at"] - 1)
    return saved


@pytest.mark.parametrize("count", [None, 17])
@pytest.mark.parametrize("repeat", [False, True])
def test_last_known_answer_stops_immediately_without_waiting_for_deadline(hub, count, repeat):
    row, sent, replies = start(hub, repeat=repeat, count=count)
    # Simulate the incomplete roster info already learned by an earlier round.
    hub.store.update("notice:" + GROUP + ":" + row["token"], lambda r: dict(r, roster_count=count))
    answer(hub, row)
    saved = assert_finished(hub, row)  # No scheduler tick or admin refresh.
    assert reminders.unknown_member_count(saved) == (15 if count else None)
    assert replies == []
    # Status clicks also stay silent; the current state is available in admin.
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_receipts", "token": row["token"]})
    data = hub.app.test_client().get("/api/admin/factory/receipts?group_id=" + GROUP).json["notices"][0]
    assert replies == [] and data["reminder_state"] == "no_pending"
    current = stored(hub, row)
    card = json.dumps(hub.reminders._reminder_card(current, {}), ensure_ascii=False)
    assert "此通知已自動停止提醒" in card and "factory_ack" in card
    assert "factory_stop" not in card and "仍可能提醒全體" not in card
    tick(hub, row["reminder_due_at"] + 3600)
    assert len(sent) == 1


def test_positive_pending_keeps_repeating_until_the_final_person_answers(hub):
    people = tuple("U" + format(i, "032x") for i in range(1, 16))
    row, sent, _ = start(hub, count=17, people=people)
    for uid in people[:10]:
        answer(hub, row, uid)
    assert len(reminders.pending_ids(stored(hub, row))) == 5
    tick(hub, row["reminder_due_at"])
    assert stored(hub, row)["reminder_state"] == "repeat_pending"
    for uid in people[10:-1]:
        answer(hub, row, uid)
    assert stored(hub, row)["wake_at"] is not None
    answer(hub, row, people[-1])
    assert_finished(hub, row)
    tick(hub, row["reminder_due_at"] + 3600)
    assert len(sent) == 2


def test_last_reply_cancels_a_frozen_uncertain_personal_retry(hub):
    row, sent, _ = start(hub, count=3)
    def timeout(*args):
        sent.append(copy.deepcopy(args))
        raise TimeoutError("LINE may have accepted this request")
    hub.reminders.sender = timeout
    tick(hub, row["reminder_due_at"])
    retry = stored(hub, row)
    assert retry["reminder_state"] == "retrying"
    assert retry["pending_batch"]["ids"] == [COLLEAGUE]
    assert not retry["pending_batch"]["all_fallback"]
    answer(hub, row)
    assert_finished(hub, row)
    tick(hub, retry["wake_at"] + 3600)
    assert len(sent) == 2
    assert stored(hub, row)["last_error"] == ""


@pytest.mark.parametrize("fails", [False, True])
def test_last_reply_during_send_cannot_be_undone_by_success_or_failure(hub, fails):
    row, sent, _ = start(hub, count=3)
    entered, release = threading.Event(), threading.Event()
    def blocked(*args):
        sent.append(copy.deepcopy(args))
        entered.set()
        assert release.wait(5), "test did not release simulated LINE request"
        if fails:
            raise TimeoutError("late send result")
    hub.reminders.sender = blocked
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(tick, hub, row["reminder_due_at"])
        try:
            assert entered.wait(3)
            answer(hub, row)
            assert_finished(hub, row)
        finally:
            release.set()
        job.result(timeout=5)
    assert_finished(hub, row)
    tick(hub, row["reminder_due_at"] + 3600)
    assert len(sent) == 2  # A request already handed to LINE cannot be recalled.


def test_last_reply_during_member_lookup_prevents_even_a_first_reminder(hub):
    row, sent, _ = start(hub)
    def lookup(group):
        answer(hub, row)
        return [USER, COLLEAGUE, THIRD]
    hub.h["_factory_member_ids"] = lookup
    tick(hub, row["reminder_due_at"])
    assert_finished(hub, row)
    assert len(sent) == 1


@pytest.mark.parametrize("entry", ["worker", "status", "admin"])
def test_existing_zero_pending_notice_stops_without_creating_a_new_notice(hub, entry):
    row, sent, replies = start(hub, count=17)
    key = "notice:" + GROUP + ":" + row["token"]
    # An older release recorded the answer but left an @All round scheduled.
    hub.store.update(key, lambda r: dict(r, reminder_state="repeat_pending", roster_count=17,
        next_reminder_at=row["reminder_due_at"],
        responses={COLLEAGUE: {"status": "understood", "name": "同事", "at": time.time()}}))
    if entry == "worker":
        tick(hub, row["reminder_due_at"])
    elif entry == "status":
        hub.postback(event(uid=COLLEAGUE), {"action": "factory_receipts", "token": row["token"]})
        assert replies == []
    else:
        result = hub.app.test_client().get("/api/admin/factory/receipts?group_id=" + GROUP).get_json()
        assert result["notices"][0]["reminder_state"] == "no_pending"
    assert_finished(hub, row)
    assert len(sent) == 1 and len(hub.store.recent("notice:" + GROUP)) == 1


def test_restart_and_later_member_discovery_do_not_restart_a_completed_notice(hub):
    row, sent, _ = start(hub)
    answer(hub, row)
    fresh = factory.FactoryHub(hub.app, hub.h, hub.store)
    fresh.member_presence(GROUP, THIRD, left=False)
    fresh.reminders.sender = lambda *args: sent.append(copy.deepcopy(args))
    tick(fresh, row["reminder_due_at"] + 3600)
    assert_finished(fresh, row)
    assert THIRD in fresh.known_members(GROUP) and len(sent) == 1


def test_departed_member_does_not_prevent_the_last_known_answer_from_stopping(hub):
    row, sent, _ = start(hub, count=17, people=(COLLEAGUE, THIRD))
    hub.member_presence(GROUP, THIRD, left=True)
    answer(hub, row)
    assert_finished(hub, row)
    tick(hub, row["reminder_due_at"])
    assert len(sent) == 1


def test_initial_card_with_empty_known_roster_retries_before_stopping(hub):
    configure(hub, 1, repeat=True)
    hub.h["group_user_names"][GROUP] = {USER: "發起人"}
    hub.h["_factory_member_ids"] = unavailable
    sent = []
    def uncertain(*args):
        sent.append(copy.deepcopy(args))
        raise TimeoutError("initial card acceptance unknown")
    hub.reminders.sender = uncertain
    hub.h["_send_reply_with_push_fallback"] = lambda **kw: None
    row = command(hub)
    assert row["delivery_state"] != "delivered" and row["reminder_state"] == "retrying"
    hub.reminders.sender = lambda *args: sent.append(copy.deepcopy(args))
    tick(hub, row["wake_at"])
    assert sent[0] == sent[1]
    assert assert_finished(hub, row)["delivery_state"] == "delivered"
    tick(hub, row["wake_at"] + 3600)
    assert len(sent) == 2
