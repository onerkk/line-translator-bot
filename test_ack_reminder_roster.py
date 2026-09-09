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


THIRD = "U" + "c" * 32


def prepare(hub, count=3, *, members=(COLLEAGUE,)):
    configure(hub, 1)
    hub.h["group_user_names"][GROUP] = {USER: "管理者", **{uid: "Adi" if uid == COLLEAGUE else "同事" for uid in members}}
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


def test_author_only_snapshot_delivers_card_but_stops_without_any_known_pending(hub):
    row, sent = prepare(hub, members=())
    assert row["expected"] == {}
    hub.postback(event(uid=USER), {"action": "factory_ack", "token": row["token"]})
    saved = due(hub, row)
    assert len(sent) == 1
    assert sent[0][1][-1]["type"] == "flex"
    assert row["delivery_state"] == "delivered"
    assert row["reminder_state"] == saved["reminder_state"] == "no_pending"
    assert reminders.unknown_member_count(saved) is None
    assert saved["wake_at"] is None
    hub.reminders.run_due()
    assert len(sent) == 1


@pytest.mark.parametrize("count", [None, 3])
def test_incomplete_roster_does_not_report_everyone_understood(hub, count):
    row, sent = prepare(hub, count)
    saved = due(hub, row)
    assert len(sent) == 2
    assert saved["reminder_state"] == "sent_all"
    assert ("名單完整性尚未確認" if count is None else "名單不完整") in hub._receipt_text(saved, "查詢")
    result = hub.app.test_client().get("/api/admin/factory/receipts?group_id=" + GROUP).get_json()
    assert result["notices"][0]["unknown_member_count"] == (1 if count else None)


@pytest.mark.parametrize("recover_api", [False, True])
def test_member_discovered_before_deadline_gets_a_personal_mention(hub, recover_api):
    row, sent = prepare(hub, 3)
    hub.h["group_user_names"][GROUP][THIRD] = "同事"
    if recover_api:
        hub.h["_factory_member_ids"] = lambda group: [USER, COLLEAGUE, THIRD]
    saved = due(hub, row)
    assert {item["userId"] for item in mentionees(sent)} == {COLLEAGUE, THIRD}
    assert saved["expected"] == {COLLEAGUE: "Adi", THIRD: "同事"}
    assert reminders.unknown_member_count(saved) == 0


def test_signed_response_from_previously_unknown_person_prevents_false_all_ping(hub):
    row, sent = prepare(hub, 2, members=())
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_ack", "token": row["token"]})
    saved = due(hub, row)
    assert len(sent) == 1
    assert saved["reminder_state"] == "no_pending"
    assert COLLEAGUE in saved["responses"]
    assert reminders.pending_ids(saved) == []


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
    row, sent = prepare(hub, 4, members=(COLLEAGUE, THIRD))
    def uncertain(*args):
        sent.append(copy.deepcopy(args))
        raise TimeoutError("response lost after possible LINE acceptance")
    hub.reminders.sender = uncertain
    saved = due(hub, row)
    assert saved["reminder_state"] == "retrying"
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_ack", "token": row["token"]})
    hub.h["_factory_member_ids"] = lambda group: [USER, COLLEAGUE, THIRD]
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


def receipt_snapshot(hub, *, complete=False, all_answered=False, count=None):
    members = {"U" + format(i, "032x"): "成員" + str(i) for i in range(1, 14)}
    answers = list(members) if all_answered else list(members)[:5]
    return {"group_id": GROUP, "token": "roster-status-snapshot", "sender_id": USER,
            "sender_name": "發起人", "original": "請確認作業內容", "translated": "Pahami instruksi kerja.",
            "expected": members, "responses": {uid: {"name": members[uid], "status": "understood"} for uid in answers},
            "roster_basis": "line_group_members" if complete else "known_chat_members", "roster_count": count}


@pytest.mark.parametrize("count", [None, 16])
def test_five_replies_eight_pending_never_display_a_zero_pending_warning(hub, count):
    row = receipt_snapshot(hub, count=count)
    text = hub._receipt_text(row, "查詢")
    assert "了解/Paham (5)" in text and "Belum menjawab (8)" in text
    assert "已知 0 人" not in text
    assert "均已回覆" not in text and "已全數回覆" not in text
    if count is None:
        assert "名單完整性尚未確認" in text
        assert "名單不完整" not in text
    else:
        assert "另有 2 人尚未取得身分" in text


@pytest.mark.parametrize("complete", [False, True])
def test_zero_known_pending_does_not_imply_unknown_members_have_answered(hub, complete):
    row = receipt_snapshot(hub, complete=complete, all_answered=True)
    row["reminder_state"] = "no_pending"
    text = hub._receipt_text(row, "查詢")
    assert "已知成員未回覆為 0" in text and "此通知已自動停止提醒" in text
    assert "仍可能提醒全體" not in text and "本次應回覆成員已全數回覆" not in text
    if complete:
        assert "名單完整性尚未確認" not in text
    else:
        assert "名單完整性尚未確認" in text


def test_sender_and_self_mentioned_bot_do_not_need_to_reply_to_stop_reminders(hub):
    configure(hub, 1, repeat=True)
    bot_id = "U" + "f" * 32
    hub.observe_members(event("@bot", mentions=[
        {"type": "user", "userId": bot_id, "isSelf": True, "index": 0, "length": 4}]))
    assert bot_id not in hub.known_members(GROUP)
    hub.h["_factory_member_ids"] = unavailable
    hub.h["_factory_member_count"] = lambda group: 2  # LINE excludes this bot.
    sent = []
    hub.reminders.sender = lambda *args: sent.append(copy.deepcopy(args))
    hub.h["_send_reply_with_push_fallback"] = lambda **kwargs: None
    row = command(hub)
    assert set(row["expected"]) == {COLLEAGUE}
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_ack", "token": row["token"]})
    saved = due(hub, row)
    assert set(saved["responses"]) == {COLLEAGUE}
    assert saved["reminder_state"] == "no_pending" and saved["wake_at"] is None
    assert len(sent) == 1
