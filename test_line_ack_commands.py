"""Full Flask/LINE SDK and SQLite/Redis regressions (uses requirements.txt)."""
import copy
import json
import time

import pytest
from linebot.v3.messaging import TextMessage

from test_line_factory_features import hub, storage, event, GROUP, OTHER, USER, COLLEAGUE
import line_quick_reply


def configure(hub, minutes=10, repeat=False):
    hub.update_settings({"group_id": GROUP, "expected_version": hub.settings_version(),
                         "expected_ack_version": hub.ack_settings_version(hub.store.get("ack-settings")),
                         "options": {"ack_reminder_enabled": True, "ack_reminder_minutes": minutes,
                                     "ack_reminder_repeat": repeat}})


def command(hub, text="/ack 今天下班前請檢查設備", mid="ack-123"):
    ev = event(text, mid=mid)
    with hub.message_scope(ev, "text"):
        assert hub.command(ev)
    rows = hub.store.recent("notice:" + GROUP)
    return rows[0] if rows else None


def test_regular_messages_never_make_cards_including_migrated_all_mode(hub):
    hub.h["factory_line_settings"]["groups"][GROUP] = {"acknowledgements": "all"}
    for source in ("PMI一定要檢測", "測試"):
        with hub.message_scope(event(source), "text"):
            payload = {"group_id": GROUP, "source_text": source, "factory_event": hub.payload_metadata()}
            messages = hub.decorate_delivery([TextMessage(text="Terjemahan")], payload, "Terjemahan")
        raw = json.dumps([m.to_dict() for m in messages])
        assert "factory_ack" not in raw and "factory_receipts" not in raw
        assert not hub.store.recent("notice:" + GROUP)


def test_command_creates_translated_card_once_and_snapshots_time_and_roster(hub, monkeypatch):
    configure(hub)
    sends = []
    hub.reminders.sender = lambda *args: sends.append(copy.deepcopy(args))
    hub.h["_factory_member_ids"] = lambda group: [USER, COLLEAGUE]
    row = command(hub)
    assert row["delivery_state"] == "delivered"
    assert row["reminder_minutes"] == 10
    assert row["reminder_due_at"] == row["delivered_at"] + 600
    assert row["expected"] == {COLLEAGUE: "Adi"}
    assert row["roster_basis"] == "line_group_members"
    assert "Hasil:" in row["translated"]
    assert row["original"] == "今天下班前請檢查設備"
    assert "factory_ack" in json.dumps(sends[0][1])
    configure(hub, 20)
    command(hub)
    assert len(sends) == 1
    assert hub.store.get("notice:" + GROUP + ":" + row["token"])["reminder_minutes"] == 10
    assert not hub.store.recent("notice:" + OTHER)


def test_alias_usage_and_known_member_fallback(hub):
    replies = []
    hub.h["_send_reply_with_push_fallback"] = lambda **kwargs: replies.append(kwargs["fallback_text"])
    hub.reminders.sender = lambda *args: None
    def unavailable(group):
        raise RuntimeError("unverified account")
    hub.h["_factory_member_ids"] = unavailable
    assert command(hub, "/確認", "empty") is None
    assert "/ack" in replies[0]
    row = command(hub, "/確認 測試", "alias")
    assert row["roster_basis"] == "known_chat_members"
    assert row["original"] == "測試"
    assert row["reminder_state"] == "off"


def test_translation_failure_preserves_thread_context_and_creates_no_notice(hub):
    hub.h["_tl"].group_id = "previous-group"
    hub.h["_tl"].from_image_ocr = True
    old = dict(hub.h["_tl"].__dict__)
    hub.h["translate"] = lambda *args: None
    hub.h["_send_reply_with_push_fallback"] = lambda **kwargs: None
    from line_factory_store import StoreError
    with pytest.raises(StoreError):
        command(hub)
    assert not hub.store.recent("notice:" + GROUP)
    assert hub.h["_tl"].__dict__ == old


def test_reminder_settings_reject_invalid_values_and_stale_versions(hub):
    for value in (0, -1, 10080, 1.5, True, "10", None):
        with pytest.raises(ValueError):
            configure(hub, value)
    configure(hub, 1)
    with pytest.raises(ValueError):
        hub.update_settings({"group_id": GROUP, "expected_version": "stale", "options": {"ack_reminder_minutes": 9}})
    assert hub.options(GROUP)["ack_reminder_minutes"] == 1


def test_saved_all_profile_normalizes_to_command_without_changing_buttons(hub):
    document = copy.deepcopy(hub.menu.document())
    document["default"]["acknowledgements"] = "all"
    hub.h[line_quick_reply.KEY] = document
    result = hub.menu.snapshot(GROUP)
    assert result["profile"]["acknowledgements"] == "command"
    assert result["profile"]["items"] == document["default"]["items"]


def test_hidden_bottom_menu_still_allows_explicit_card(hub):
    doc = copy.deepcopy(hub.menu.document())
    doc["default"]["enabled"] = False
    hub.h[line_quick_reply.KEY] = doc
    hub.h["_factory_member_ids"] = lambda group: [USER, COLLEAGUE]
    sends = []
    hub.reminders.sender = lambda *args: sends.append(args)
    row = command(hub)
    assert row["delivery_state"] == "delivered"
    assert "factory_ack" in json.dumps(sends[0][1])
    assert hub.menu.build(GROUP) is None


def test_shared_reminder_settings_override_stale_worker_settings(hub):
    stale_ack = hub.ack_settings_version(None)
    configure(hub, 1)
    hub.h["factory_line_settings"] = {"groups": {}, "stations": []}
    assert hub.ack_options(GROUP)["ack_reminder_enabled"] is True
    assert hub.ack_options(GROUP)["ack_reminder_minutes"] == 1
    with pytest.raises(ValueError):
        hub.update_settings({"group_id": GROUP, "expected_version": hub.settings_version(),
            "expected_ack_version": stale_ack, "options": {"ack_reminder_enabled": False}})
    hub.h["_factory_member_ids"] = lambda group: [USER, COLLEAGUE]
    hub.reminders.sender = lambda *args: None
    row = command(hub)
    assert row["reminder_minutes"] == 1
    latest = hub.store.get("ack-settings")
    latest["groups"][GROUP]["ack_reminder_enabled"] = False
    hub.store.put("ack-settings", latest)
    key = "notice:" + GROUP + ":" + row["token"]
    hub.store.update(key, lambda current: dict(current, wake_at=time.time() - 1))
    hub.reminders.process(key)
    assert hub.store.get(key)["reminder_state"] == "cancelled"


def test_redis_and_sqlite_due_index_follow_claim_cancel_and_delete(storage):
    now = time.time()
    token = "ack-index-test"
    key = "notice:" + GROUP + ":" + token
    row = {"token": token, "group_id": GROUP, "wake_at": now, "responses": {}}
    storage.save_interaction({"token": token, "group_id": GROUP}, 3600, notice=row)
    assert storage.due_notices(now)[0]["token"] == token
    storage.update(key, lambda r: dict(r, wake_at=now + 120, responses={COLLEAGUE: {"status": "understood"}}))
    storage.save_interaction({"token": token, "group_id": GROUP}, 3600, notice=row)
    assert not storage.due_notices(now)
    assert COLLEAGUE in storage.get(key)["responses"]
    assert storage.due_notices(now + 121)[0]["token"] == token
    storage.update(key, lambda r: dict(r, wake_at=None))
    assert not storage.due_notices(now + 121)
    storage.put(key, row)
    storage.delete(key)
    assert not storage.due_notices(now + 121)
