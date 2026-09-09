"""Reported empty-roster reminder failure, using the real SQLite/Redis outbox."""
import copy
import threading
from unittest.mock import patch

import pytest

import line_ack_reminders as reminders
from test_line_factory_features import hub, storage, event, GROUP, USER, COLLEAGUE
from test_line_ack_commands import command, configure


def unavailable(group):
    raise RuntimeError("LINE member-list permission unavailable")


def prepare(hub, count=3):
    configure(hub, 1)
    hub.h["group_user_names"][GROUP] = {USER: "管理者"}
    hub.h["_factory_member_ids"] = unavailable
    hub.h["_factory_member_count"] = lambda group: count
    sent = []
    hub.reminders.sender = lambda *args: sent.append(copy.deepcopy(args))
    hub.h["_send_reply_with_push_fallback"] = lambda **kwargs: None
    row = command(hub, "/ack 測試")
    return row, sent


def due(hub, row):
    hub.reminders.clock = lambda: row["reminder_due_at"] + 1
    hub.reminders.run_due()
    return hub.store.get("notice:" + GROUP + ":" + row["token"])


def mentionees(sent):
    return [item["mentionee"] for item in sent[-1][1][0]["substitution"].values()]


def test_reported_author_only_snapshot_still_reminds_the_group_once(hub):
    row, sent = prepare(hub)
    assert row["expected"] == {}
    hub.postback(event(uid=USER), {"action": "factory_ack", "token": row["token"]})
    saved = due(hub, row)
    assert len(sent) == 2
    assert mentionees(sent) == [{"type": "all"}]
    assert sent[-1][1][0]["type"] == "textV2"
    assert "已回覆" in sent[-1][1][0]["text"]
    assert saved["reminder_state"] == "sent_all"
    assert reminders.unknown_member_count(saved) == 2
    assert saved["wake_at"] is None
    hub.reminders.run_due()
    assert len(sent) == 2


@pytest.mark.parametrize("count", [None, 3])
def test_incomplete_roster_does_not_report_everyone_understood(hub, count):
    row, sent = prepare(hub, count)
    saved = due(hub, row)
    assert len(sent) == 2
    assert saved["reminder_state"] == "sent_all"
    assert "名單不完整" in hub._receipt_text(saved, "查詢")
    result = hub.app.test_client().get("/api/admin/factory/receipts?group_id=" + GROUP).get_json()
    assert result["notices"][0]["unknown_member_count"] == (2 if count else None)


@pytest.mark.parametrize("recover_api", [False, True])
def test_member_discovered_before_deadline_gets_a_personal_mention(hub, recover_api):
    row, sent = prepare(hub, 2)
    hub.h["group_user_names"][GROUP][COLLEAGUE] = "Adi"
    if recover_api:
        hub.h["_factory_member_ids"] = lambda group: [USER, COLLEAGUE]
    saved = due(hub, row)
    assert mentionees(sent) == [{"type": "user", "userId": COLLEAGUE}]
    assert saved["expected"] == {COLLEAGUE: "Adi"}
    assert reminders.unknown_member_count(saved) == 0


def test_signed_response_from_previously_unknown_person_prevents_false_all_ping(hub):
    row, sent = prepare(hub, 2)
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_ack", "token": row["token"]})
    saved = due(hub, row)
    assert len(sent) == 1
    assert saved["reminder_state"] == "no_pending"
    assert reminders.unknown_member_count(saved) == 0


def test_complete_roster_mentions_only_unanswered_members(hub):
    other = "U" + "c" * 32
    configure(hub, 1)
    hub.h["_factory_member_ids"] = lambda group: [USER, COLLEAGUE, other]
    hub.h["_factory_member_count"] = lambda group: 3
    sent = []
    hub.reminders.sender = lambda *args: sent.append(copy.deepcopy(args))
    hub.h["_send_reply_with_push_fallback"] = lambda **kwargs: None
    row = command(hub)
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_ack", "token": row["token"]})
    due(hub, row)
    assert mentionees(sent) == [{"type": "user", "userId": other}]


def test_empty_ids_response_is_not_evidence_of_a_complete_roster(hub):
    row, sent = prepare(hub, 3)
    hub.h["_factory_member_ids"] = lambda group: []
    saved = due(hub, row)
    assert mentionees(sent) == [{"type": "all"}]
    assert saved["reminder_state"] == "sent_all"


def test_fallback_retry_keeps_the_frozen_payload_even_if_a_response_arrives(hub):
    row, sent = prepare(hub)
    def uncertain(*args):
        sent.append(copy.deepcopy(args))
        raise TimeoutError("response lost after possible LINE acceptance")
    hub.reminders.sender = uncertain
    saved = due(hub, row)
    assert saved["reminder_state"] == "retrying"
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_ack", "token": row["token"]})
    hub.h["_factory_member_ids"] = lambda group: [USER, COLLEAGUE]
    hub.reminders.sender = lambda *args: sent.append(copy.deepcopy(args))
    hub.reminders.clock = lambda: saved["wake_at"] + 1
    hub.reminders.run_due()
    assert sent[-1] == sent[-2]
    updated = hub.store.get("notice:" + GROUP + ":" + row["token"])
    assert COLLEAGUE in updated["responses"]
    assert updated["reminder_state"] == "sent_all"


def test_scheduler_sends_without_another_webhook_or_admin_refresh(hub, monkeypatch):
    row, sent = prepare(hub)
    accepted = threading.Event()
    hub.reminders.clock = lambda: row["reminder_due_at"] + 1
    def send(*args):
        sent.append(copy.deepcopy(args))
        accepted.set()
    hub.reminders.sender = send
    hub.reminder_worker.interval = .02
    monkeypatch.setenv("FACTORY_ACK_WORKER_ENABLED", "1")
    hub.reminder_worker.start()
    try:
        assert accepted.wait(3), "Reminder worker did not run without incoming traffic"
    finally:
        hub.reminder_worker.stop()
        hub.reminder_worker._thread.join(3)
    assert mentionees(sent) == [{"type": "all"}]


@pytest.mark.parametrize("native", [False, True])
def test_initial_notice_preserves_real_all_mentions_only(hub, native):
    hub.h["_factory_member_ids"] = lambda group: [USER, COLLEAGUE]
    sent = []
    hub.reminders.sender = lambda *args: sent.append(copy.deepcopy(args))
    ev = event("/ack @All 測試", mentions=[{"type": "all", "index": 5, "length": 4}] if native else [])
    with hub.message_scope(ev, "text"):
        assert hub.command(ev)
    messages = sent[0][1]
    if native:
        assert messages[0]["type"] == "textV2"
        assert mentionees(sent) == [{"type": "all"}]
    else:
        assert all(message["type"] != "textV2" for message in messages)
    assert messages[-1]["type"] == "flex"


@pytest.mark.parametrize("group,kind", [(GROUP, "group"), ("R" + "1" * 32, "room")])
def test_count_endpoint_does_not_depend_on_member_list_permission(monkeypatch, group, kind):
    import io
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "offline-test-token")
    def response(request, timeout):
        assert request.full_url == "https://api.line.me/v2/bot/" + kind + "/" + group + "/members/count"
        assert timeout == 4
        return io.BytesIO(b'{"count": 3}')
    with patch.object(reminders.urllib.request, "urlopen", response):
        assert reminders.member_count(group) == 3
