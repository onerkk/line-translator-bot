"""Real reminder payloads must mention known pending users, never fallback @All."""
import copy
import json
import uuid

import pytest

import line_ack_reminders as reminders
from test_line_factory_features import hub, storage, GROUP, USER, COLLEAGUE
from test_ack_repeat_controls import THIRD, stored, tick
from test_ack_known_zero_stop import start, answer, assert_finished


def recipients(send):
    message = send[1][0]
    assert message["type"] == "textV2"
    assert len(message["substitution"]) <= 20
    people = []
    for key, item in message["substitution"].items():
        assert "{" + key + "}" in message["text"]
        assert item["type"] == "mention" and item["mentionee"]["type"] == "user"
        people.append(item["mentionee"]["userId"])
    assert people and "提醒全體" not in message["text"]
    assert send[1][-1]["type"] == "flex"
    return people


@pytest.mark.parametrize("count", [None, 20])
def test_ten_recipients_answer_and_unlisted_author_is_ignored_with_five_pending(hub, count):
    people = tuple("U" + format(i, "032x") for i in range(1, 16))
    row, sent, _ = start(hub, people=people, count=count)
    # The sender's unlisted tap creates no response; only the ten recipients count.
    for uid in [USER, *people[:10]]:
        answer(hub, row, uid)
    assert len(stored(hub, row)["responses"]) == 10
    assert USER not in stored(hub, row)["responses"]
    tick(hub, row["reminder_due_at"])
    assert set(recipients(sent[-1])) == set(people[10:])
    assert "factory_ack" in json.dumps(sent[-1][1][-1])
    assert reminders.unknown_member_count(stored(hub, row)) == (4 if count else None)
    previous_key = sent[-1][2]
    answer(hub, row, people[10])
    tick(hub, stored(hub, row)["next_reminder_at"])
    assert set(recipients(sent[-1])) == set(people[11:])
    assert sent[-1][2] != previous_key
    for uid in people[11:]:
        answer(hub, row, uid)
    assert_finished(hub, row)
    tick(hub, row["reminder_due_at"] + 3600)
    assert len(sent) == 3


def test_incomplete_roster_batches_all_pending_without_duplicates_and_refilters_each_round(hub):
    people = tuple("U" + format(i, "032x") for i in range(1, 45))
    row, sent, _ = start(hub, people=people)
    hub.reminders.clock = lambda: row["reminder_due_at"]
    hub.reminders.run_due()
    first = recipients(sent[-1])
    assert len(first) == 20 and stored(hub, row)["reminder_state"] == "sending"
    answered_after_send = first[0]
    answered_before_send = next(uid for uid in people if uid not in first)
    answer(hub, row, answered_after_send)
    answer(hub, row, answered_before_send)
    tick(hub, row["reminder_due_at"])
    round_one = [uid for message in sent[1:] for uid in recipients(message)]
    assert len(round_one) == len(set(round_one)) == 43
    assert set(round_one) == set(people) - {answered_before_send}
    saved = stored(hub, row)
    assert saved["reminder_count"] == 1 and saved["reminder_state"] == "repeat_pending"
    boundary = len(sent)
    tick(hub, saved["next_reminder_at"])
    round_two = [uid for message in sent[boundary:] for uid in recipients(message)]
    assert len(round_two) == len(set(round_two)) == 42
    assert set(round_two) == set(people) - {answered_before_send, answered_after_send}
    assert stored(hub, row)["reminder_count"] == 2


def test_answer_after_preparation_but_before_first_send_refilters_without_delaying(hub, monkeypatch):
    row, sent, _ = start(hub, people=(COLLEAGUE, THIRD))
    key = "notice:" + GROUP + ":" + row["token"]
    original_get, fired = hub.store.get, False
    def get(name):
        nonlocal fired
        value = original_get(name)
        if name == key and not fired and (value.get("pending_batch") or {}).get("prepared_only"):
            fired = True
            answer(hub, row)
            return original_get(name)
        return value
    monkeypatch.setattr(hub.store, "get", get)
    tick(hub, row["reminder_due_at"])
    assert fired and len(sent) == 2
    assert recipients(sent[-1]) == [THIRD]
    assert stored(hub, row)["reminder_state"] == "repeat_pending"


def test_departure_retires_an_uncertain_retry_and_next_send_only_mentions_remaining_members(hub):
    row, sent, _ = start(hub, people=(COLLEAGUE, THIRD))
    def timeout(*args):
        sent.append(copy.deepcopy(args))
        raise TimeoutError("unconfirmed acceptance")
    hub.reminders.sender = timeout
    tick(hub, row["reminder_due_at"])
    retry = stored(hub, row)
    hub.member_presence(GROUP, COLLEAGUE, left=True)
    hub.reminders.sender = lambda *args: sent.append(copy.deepcopy(args))
    tick(hub, retry["wake_at"])
    saved = stored(hub, row)
    assert len(sent) == 2 and saved["pending_batch"] is None
    tick(hub, saved["next_reminder_at"])
    assert recipients(sent[-1]) == [THIRD]
    assert sent[-1][2] != sent[-2][2]


@pytest.mark.parametrize("fallback_flag", [False, True])
@pytest.mark.parametrize("expires_soon", [False, True])
def test_legacy_all_retry_is_cancelled_then_replaced_by_individual_mentions(hub, fallback_flag, expires_soon):
    row, sent, _ = start(hub, people=(COLLEAGUE, THIRD), count=17)
    key, old_key = "notice:" + GROUP + ":" + row["token"], str(uuid.uuid4())
    now = row["reminder_due_at"]
    old_batch = {"initial": False, "started_at": now - 16, "key": old_key,
                 "ids": [COLLEAGUE, THIRD], "all_fallback": fallback_flag,
                 "messages": [{"type": "textV2", "text": "{everyone} 請回覆",
                               "substitution": {"everyone": {"type": "mention", "mentionee": {"type": "all"}}}}]}
    # No prepared_only marker: an older version may already have sent this.
    hub.store.update(key, lambda r: dict(r, pending_batch=old_batch, reminder_state="retrying",
        expires_at=now + 30 if expires_soon else r["expires_at"]))
    tick(hub, now)
    saved = stored(hub, row)
    assert len(sent) == 1 and saved["pending_batch"] is None
    assert saved["reminder_retry_retired_at"] == now
    if expires_soon:
        assert saved["reminder_state"] == "cancelled" and saved["wake_at"] is None
        return
    assert saved["next_reminder_at"] == now + 60
    answer(hub, row)
    tick(hub, saved["next_reminder_at"])
    assert len(sent) == 2 and recipients(sent[-1]) == [THIRD]
    assert sent[-1][2] != old_key
