"""Custom reminders keep content, native mentions and frozen retries intact."""
import copy
import io
import json

import pytest
from linebot.v3.messaging import FlexMessage, PushMessageRequest, Message

import scheduled_reminders as reminders
from test_scheduled_reminders import store, service, api_client, spec, CATALOG, GID, UID, UID2, NOW, DUE


def card(messages):
    return next(message for message in messages if message["type"] == "flex")


def texts(node):
    if isinstance(node, dict):
        if node.get("type") == "text":
            yield node["text"]
        for child in node.values():
            yield from texts(child)
    elif isinstance(node, list):
        for child in node:
            yield from texts(child)


@pytest.mark.parametrize("mode,users", [("none", []), ("all", []), ("users", [UID, UID2])])
def test_card_preserves_custom_text_and_native_mention_scope(mode, users):
    content = '開班股會議\nRapat bagian\n{m0} <b>literal</b> 🙂\n請帶交班紀錄。'
    row = reminders.validate_spec(spec(mention_mode=mode, user_ids=users, content=content), CATALOG, NOW)
    messages = reminders.build_line_messages(row)
    flex = card(messages)
    assert FlexMessage.from_dict(flex).to_dict() == flex
    assert PushMessageRequest(to=GID, messages=[Message.from_dict(m) for m in messages]).to_dict()["messages"] == messages
    assert content in list(texts(flex))
    assert "2026.09.20" in list(texts(flex)) and "週日 / Minggu" in list(texts(flex))
    assert "08:00" in list(texts(flex)) and "台灣時間 / Waktu Taiwan · UTC+8" in list(texts(flex))
    if mode == "none":
        assert len(messages) == 1 and not any("substitution" in m for m in messages)
        assert "不標註 / Tanpa mention" in list(texts(flex))
    else:
        assert len(messages) == 2 and messages[0]["type"] == "textV2"
        targets = [s["mentionee"] for s in messages[0]["substitution"].values()]
        assert targets == ([{"type": "all"}] if mode == "all" else [{"type": "user", "userId": uid} for uid in users])
        assert content not in messages[0]["text"]  # One copy of the actual reminder.
    assert all("action" not in m for m in messages)


@pytest.mark.parametrize("content", ["字" * 1500, "🙂" * 750, "{" * 1500, ("工單\n" * 375).strip()])
def test_maximum_content_is_not_truncated_or_interpreted_as_mention(content):
    members = {"U" + format(n, "032x"): "member" for n in range(20)}
    row = reminders.validate_spec(spec(mention_mode="users", user_ids=list(members), content=content),
                                   {GID: {"name": "👷" * 1000, "members": members}}, NOW)
    messages = reminders.build_line_messages(row)
    flex = card(messages)
    assert content in list(texts(flex))
    assert len(json.dumps(flex["contents"], ensure_ascii=False).encode()) < 30000
    assert reminders.utf16_units(flex["altText"]) <= 400
    assert all(reminders.utf16_units(text) <= 2000 for text in texts(flex))
    assert len(messages[0]["substitution"]) == 20
    assert not any("maxLines" in str(m) for m in messages)


def test_new_payload_is_committed_before_first_send_and_reused_after_restart(service, monkeypatch):
    instance, clock, _ = service
    row = instance.create(spec(), "admin")
    sent = []
    def timeout(record):
        committed = instance.store.get(row["id"])
        assert committed["status"] == "sending" and committed["delivery_messages"] == reminders.build_line_messages(record)
        sent.append((record["retry_key"], reminders.build_line_messages(record)))
        raise reminders.DeliveryError("offline")
    instance.sender = timeout
    clock[0] = DUE
    assert instance.run_due()["retrying"] == 1
    assert card(sent[0][1])
    def changed_design(*args):
        pytest.fail("a retry rebuilt the card after a deployment")
    monkeypatch.setattr(reminders, "scheduled_reminder_message", changed_design)
    resumed = reminders.ReminderService(instance.store, lambda: CATALOG,
        sender=lambda record: sent.append((record["retry_key"], reminders.build_line_messages(record))), clock=lambda: clock[0])
    clock[0] += 31
    assert resumed.run_due()["sent"] == 1 and sent[0] == sent[1]
    assert resumed.run_due()["sent"] == 0


@pytest.mark.parametrize("status", ["retrying", "sending"])
def test_pre_update_attempt_keeps_exact_original_payload_and_key(service, status):
    instance, clock, sent = service
    row = instance.create(spec(content="{m0} 保持原文"), "admin")
    old = dict(row, status=status, attempts=1, first_attempt_at=DUE,
               next_attempt_at=DUE, lease_until=DUE, updated_at=DUE)
    assert instance.store.compare_swap(row, old)
    expected = [reminders.build_line_message(old)]
    clock[0] = DUE + 1
    assert instance.run_due()["sent"] == 1
    assert reminders.build_line_messages(sent[0]) == expected
    assert sent[0]["retry_key"] == old["retry_key"] and len(expected) == 1


def test_unattempted_old_schedule_gets_new_card_with_latest_edit(service):
    instance, clock, sent = service
    row = instance.create(spec(), "admin")
    changed = instance.change(row["id"], spec(revision=row["revision"], content="改為線上會議", mention_mode="none"))
    assert "delivery_messages" not in changed
    clock[0] = DUE
    assert instance.run_due()["sent"] == 1
    messages = reminders.build_line_messages(sent[0])
    assert len(messages) == 1 and "改為線上會議" in list(texts(card(messages)))


def test_frozen_payload_cannot_be_mutated_through_a_builder_result():
    row = reminders.validate_spec(spec(), CATALOG, NOW)
    row["delivery_messages"] = reminders.build_line_messages(row)
    saved = copy.deepcopy(row)
    first = reminders.build_line_messages(row)
    first[-1]["contents"]["body"]["contents"].clear()
    assert row == saved and reminders.build_line_messages(row) == saved["delivery_messages"]


def test_line_receives_mentions_and_card_in_one_request(service, monkeypatch):
    instance, clock, _ = service
    instance.create(spec(), "admin")
    sent = []
    def urlopen(request, timeout):
        assert timeout == 12
        sent.append(json.loads(request.data))
        response = io.BytesIO(b'{}')
        response.status = 200
        return response
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "offline-test-token")
    monkeypatch.setattr(reminders.urllib.request, "urlopen", urlopen)
    instance.sender = reminders.send_line_reminder
    clock[0] = DUE
    assert instance.run_due()["sent"] == 1
    assert len(sent) == 1 and sent[0]["to"] == GID
    assert [m["type"] for m in sent[0]["messages"]] == ["textV2", "flex"]
    assert sent[0]["notificationDisabled"] is False


def test_admin_api_does_not_expose_frozen_transport_payload(api_client):
    client, headers, instance = api_client
    response = client.post('/api/admin/reminders', headers=headers, json=spec())
    assert response.status_code == 201
    instance.sender = lambda _: None
    instance.clock = lambda: DUE
    assert instance.run_due()["sent"] == 1
    data = client.get('/api/admin/reminders', headers=headers).json
    row = data["reminders"][0]
    assert row["status"] == "sent" and "delivery_messages" not in row and "retry_key" not in row
    assert instance.store.get(row["id"])["delivery_messages"]

