"""Persistent roster discovery, recurring delivery and authorized stop controls."""
import copy

import pytest

import line_factory_features as factory
from test_line_factory_features import hub, storage, event, GROUP, OTHER, USER, COLLEAGUE
from test_line_ack_commands import command, configure

THIRD = "U" + "c" * 32


def setup(hub, *, complete=True):
    configure(hub, 1, repeat=True)
    def unavailable(group):
        raise RuntimeError("Member list permission unavailable")
    hub.h["_factory_member_ids"] = (lambda group: [USER, COLLEAGUE, THIRD]) if complete else unavailable
    hub.h["_factory_member_count"] = lambda group: 3
    sends, replies = [], []
    hub.reminders.sender = lambda *args: sends.append(copy.deepcopy(args))
    hub.h["_send_reply_with_push_fallback"] = lambda **kw: replies.append(kw["fallback_text"])
    row = command(hub)
    return row, sends, replies


def stored(hub, row):
    return hub.store.get("notice:" + GROUP + ":" + row["token"])


def tick(hub, now):
    hub.reminders.clock = lambda: now
    for _ in range(5):
        hub.reminders.run_due()


def people(sends):
    return [item["mentionee"] for item in sends[-1][1][0]["substitution"].values()]


def test_repeats_on_interval_then_stops_after_everyone_replies(hub):
    row, sends, _ = setup(hub)
    first = row["reminder_due_at"]
    tick(hub, first)
    saved = stored(hub, row)
    assert saved["reminder_count"] == 1
    assert saved["reminder_state"] == "repeat_pending"
    assert saved["next_reminder_at"] == first + 60
    assert {person["userId"] for person in people(sends)} == {COLLEAGUE, THIRD}
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_ack", "token": row["token"]})
    tick(hub, first + 59)
    assert len(sends) == 2
    tick(hub, first + 60)
    assert len(sends) == 3
    assert people(sends) == [{"type": "user", "userId": THIRD}]
    assert sends[1][2] != sends[2][2]
    assert stored(hub, row)["reminder_count"] == 2
    hub.postback(event(uid=THIRD), {"action": "factory_ack", "token": row["token"]})
    tick(hub, first + 120)
    assert len(sends) == 3
    assert stored(hub, row)["reminder_state"] == "no_pending"
    assert stored(hub, row)["wake_at"] is None


def test_unknown_member_later_speaks_and_next_round_mentions_only_them(hub):
    row, sends, _ = setup(hub, complete=False)
    tick(hub, row["reminder_due_at"])
    assert people(sends) == [{"type": "user", "userId": COLLEAGUE}]
    with hub.message_scope(event("到了", uid=THIRD, mid="new-member-message"), "text"):
        pass
    # Same persistent disk, fresh hub/cache: no dependence on process memory.
    fresh = factory.FactoryHub(hub.app, hub.h, hub.store)
    assert THIRD in fresh.known_members(GROUP)
    assert THIRD not in fresh.known_members(OTHER)
    tick(hub, stored(hub, row)["next_reminder_at"])
    assert {person["userId"] for person in people(sends)} == {COLLEAGUE, THIRD}
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_ack", "token": row["token"]})
    tick(hub, stored(hub, row)["next_reminder_at"])
    assert people(sends) == [{"type": "user", "userId": THIRD}]


def test_real_person_mentions_are_remembered_without_guessing_all_ids(hub):
    with hub.message_scope(event("問 @同事", mentions=[{"type": "user", "userId": THIRD, "index": 2, "length": 3}]), "text"):
        pass
    assert hub.known_members(GROUP)[THIRD] == "同事"
    before = set(hub.known_members(GROUP))
    with hub.message_scope(event("@All", mid="all-only", mentions=[{"type": "all", "index": 0, "length": 4}]), "text"):
        pass
    assert set(hub.known_members(GROUP)) == before


def test_join_and_departure_update_the_available_roster(hub):
    hub.member_presence(GROUP, THIRD, left=False)
    assert THIRD in hub.known_members(GROUP)
    hub.member_presence(GROUP, THIRD, left=True)
    data = hub.app.test_client().get("/api/admin/factory/members?group_id=" + GROUP).get_json()
    assert THIRD not in {row["id"] for row in data["members"]}
    hub.member_presence(GROUP, THIRD, left=False)
    data = hub.app.test_client().get("/api/admin/factory/members?group_id=" + GROUP).get_json()
    assert THIRD in {row["id"] for row in data["members"]}


def test_author_stop_announces_once_but_an_ordinary_member_cannot(hub):
    row, sends, replies = setup(hub)
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_stop", "token": row["token"]})
    assert replies == []
    assert not stored(hub, row).get("reminder_stopped_at")
    assert not hub.h["factory_stop_sends"]
    hub.postback(event(uid=USER), {"action": "factory_stop", "token": row["token"]})
    assert stored(hub, row).get("reminder_stopped_at") and replies == []
    assert stored(hub, row)["reminder_state"] == "stopped"
    assert stored(hub, row)["wake_at"] is None
    assert len(hub.h["factory_stop_sends"]) == 1
    tick(hub, row["reminder_due_at"] + 180)
    assert len(sends) == 1
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_ack", "token": row["token"]})
    assert COLLEAGUE in stored(hub, row)["responses"]
    assert stored(hub, row)["reminder_state"] == "stopped"


def test_stop_api_requires_factory_admin_and_cannot_cross_groups(hub):
    row, _, _ = setup(hub)
    client = hub.app.test_client()
    data = {"group_id": GROUP, "token": row["token"], "admin": True}
    hub.h["check_manager_access"] = lambda _: False
    assert client.post("/api/admin/factory/receipts/stop", json=data).status_code == 403
    hub.h["check_manager_access"] = lambda _: True
    assert client.post("/api/admin/factory/receipts/stop", json={**data, "group_id": OTHER}).status_code == 400
    assert not stored(hub, row).get("reminder_stopped_at")
    response = client.post("/api/admin/factory/receipts/stop", json=data)
    assert response.status_code == 200
    assert response.get_json()["reminder_state"] == "stopped"


def test_foreign_or_expired_stop_button_is_silent_without_changing_notice(hub):
    row, _, replies = setup(hub)
    assert hub.postback(event(group=OTHER), {"action": "factory_stop", "token": row["token"]})
    assert replies == []
    assert not stored(hub, row).get("reminder_stopped_at")


def test_stop_during_roster_lookup_prevents_a_new_line_request(hub):
    row, sends, _ = setup(hub)
    def stop_during_lookup(group):
        hub.stop_notice(GROUP, row["token"], USER)
        return [USER, COLLEAGUE, THIRD]
    hub.h["_factory_member_ids"] = stop_during_lookup
    tick(hub, row["reminder_due_at"])
    assert len(sends) == 1
    assert stored(hub, row)["reminder_state"] == "stopped"


def test_group_switch_stops_delivery_even_when_changed_during_lookup(hub):
    row, sends, _ = setup(hub)
    def disable(group):
        doc = hub.store.get("ack-settings")
        doc["groups"][GROUP]["ack_reminder_enabled"] = False
        hub.store.put("ack-settings", doc)
        return [USER, COLLEAGUE, THIRD]
    hub.h["_factory_member_ids"] = disable
    tick(hub, row["reminder_due_at"])
    assert len(sends) == 1
    assert stored(hub, row)["reminder_state"] == "cancelled"


def test_stopping_one_notice_does_not_stop_another(hub):
    row, sends, _ = setup(hub)
    other = command(hub, "/ack 另一項工作", mid="second-task")
    hub.stop_notice(GROUP, row["token"], USER)
    tick(hub, other["reminder_due_at"])
    assert len(sends) == 3  # Two initial cards and the second task's reminder.
    assert stored(hub, other)["reminder_state"] == "repeat_pending"


def test_repeating_personal_mentions_use_new_round_keys_and_same_unchanged_retry_key(hub):
    row, sends, _ = setup(hub, complete=False)
    tick(hub, row["reminder_due_at"])
    assert people(sends) == [{"type": "user", "userId": COLLEAGUE}]
    first_key = sends[-1][2]
    due = stored(hub, row)["next_reminder_at"]
    def uncertain(*args):
        sends.append(copy.deepcopy(args))
        raise TimeoutError("accepted but acknowledgement lost")
    hub.reminders.sender = uncertain
    tick(hub, due)
    saved = stored(hub, row)
    assert saved["reminder_state"] == "retrying"
    assert sends[-1][2] != first_key
    hub.reminders.sender = lambda *args: sends.append(copy.deepcopy(args))
    tick(hub, saved["wake_at"])
    assert sends[-1] == sends[-2]
    assert stored(hub, row)["reminder_count"] == 2


def test_repeating_notice_still_expires(hub):
    row, sends, _ = setup(hub, complete=False)
    tick(hub, row["expires_at"])
    assert len(sends) == 1
    assert stored(hub, row)["reminder_state"] == "cancelled"
