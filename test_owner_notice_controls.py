"""Management belongs to the notice sender/admin, independently of recipients."""
import copy
import json

import pytest

from test_line_factory_features import hub, storage, event, GROUP, OTHER, USER, COLLEAGUE
from test_ack_recipient_scope import begin, tagged, THIRD
from test_ack_repeat_controls import stored, tick

ADMIN = "U" + "9" * 32


@pytest.mark.parametrize("action", ["factory_receipts", "factory_stop"])
@pytest.mark.parametrize("role", ["sender", "admin", "bootstrap", "recipient", "outsider", "anonymous", "foreign_sender", "foreign_admin"])
def test_only_sender_or_bot_admin_can_manage_notice(hub, role, action):
    row, sent, replies, _ = begin(hub, [tagged("@Adi", COLLEAGUE)])
    hub.h["admin_users"] = {ADMIN: {"is_admin": role in {"admin", "foreign_admin"}, "allowed_tabs": []}}
    if role == "bootstrap":
        hub.h["is_group_admin"] = lambda uid: uid == ADMIN
    uid = {"sender": USER, "recipient": COLLEAGUE, "outsider": THIRD, "anonymous": "", "foreign_sender": USER}.get(role, ADMIN)
    group = OTHER if role.startswith("foreign_") else GROUP
    before, members = stored(hub, row), hub.known_members(GROUP)
    assert hub.postback(event(uid=uid, group=group), {"action": action, "token": row["token"], "admin": "true", "user_id": USER})
    saved = stored(hub, row)
    allowed = role in {"sender", "admin", "bootstrap"}
    if not allowed:
        assert saved == before and replies == []
    elif action == "factory_stop":
        assert saved["reminder_state"] == "stopped" and saved["stopped_by"] == uid
        assert saved["wake_at"] is None and replies == []
    else:
        assert uid in saved["status_views"] and len(replies) == 1
        message = replies[0]["message_obj"].to_dict()
        assert message["type"] == "text" and "substitution" not in message
        assert row["original"] not in message["text"] and row["translated"] not in message["text"]
        assert "指定成員未回覆" in message["text"] and "Adi" in message["text"]
        assert saved["wake_at"] == before["wake_at"]
        data = hub.app.test_client().get("/api/admin/factory/receipts?group_id=" + GROUP).json["notices"][0]
        assert uid in data["status_views"]
    assert saved["responses"] == {} and len(sent) == 1 and hub.known_members(GROUP) == members


@pytest.mark.parametrize("action", ["factory_receipts", "factory_stop"])
def test_role_revocation_during_atomic_update_cannot_commit_or_reply(hub, monkeypatch, action):
    row, sent, replies, _ = begin(hub, [tagged("@Adi", COLLEAGUE)])
    hub.h["admin_users"] = {ADMIN: {"is_admin": True}}
    before, original, conflicts = stored(hub, row), hub.store.compare_swap, []
    key = "notice:" + GROUP + ":" + row["token"]
    def compare(name, previous, current, ttl):
        if name == key and not conflicts:
            conflicts.append(True)
            hub.h["admin_users"][ADMIN]["is_admin"] = False
            return False
        return original(name, previous, current, ttl)
    monkeypatch.setattr(hub.store, "compare_swap", compare)
    hub.postback(event(uid=ADMIN), {"action": action, "token": row["token"]})
    assert conflicts and stored(hub, row) == before and replies == [] and len(sent) == 1


def test_sender_management_never_counts_as_an_ack_and_recipient_ack_stays_silent(hub):
    row, sent, replies, _ = begin(hub, [tagged("@Adi", COLLEAGUE)])
    hub.postback(event(uid=USER), {"action": "factory_receipts", "token": row["token"]})
    assert len(replies) == 1 and stored(hub, row)["responses"] == {}
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_ack", "token": row["token"]})
    assert len(replies) == 1 and stored(hub, row)["responses"][COLLEAGUE]["status"] == "understood"
    tick(hub, row["reminder_due_at"])
    assert len(sent) == 1 and stored(hub, row)["reminder_state"] == "no_pending"


def test_new_notice_footer_and_quick_reply_remove_help_and_explanatory_text(hub):
    from line_message_ui import notice_footer
    row, _, _, _ = begin(hub, [tagged("@Adi", COLLEAGUE)])
    doc = copy.deepcopy(hub.menu.document())
    doc["default"]["items"].append({"id": "legacy-help", "type": "builtin", "action": "factory_help",
                                    "label": "❓ 說明/Jelaskan", "enabled": True, "contexts": ["text", "image"]})
    import line_quick_reply
    hub.h[line_quick_reply.KEY] = doc
    footer = hub._notice_footer(row["token"], row)
    def nodes(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from nodes(child)
        elif isinstance(value, list):
            for child in value:
                yield from nodes(child)
    assert not any(n.get("type") == "text" for n in nodes(footer))
    assert "factory_ack" in json.dumps(footer) and "factory_help" not in json.dumps(footer)
    assert "factory_help" not in json.dumps(notice_footer(row["token"], [("了解", "factory_ack"), ("說明", "factory_help")]))
    for preview in (False, True):
        actions = hub.menu.actions(GROUP, row, row["token"], preview=preview)
        assert "factory_help" not in json.dumps(actions)


def test_legacy_sender_metadata_can_still_manage_notice(hub):
    row, _, replies, _ = begin(hub, [tagged("@Adi", COLLEAGUE)])
    key = "notice:" + GROUP + ":" + row["token"]
    hub.store.update(key, lambda r: {k: v for k, v in r.items() if k != "sender_id"})
    hub.postback(event(uid=USER), {"action": "factory_stop", "token": row["token"]})
    assert stored(hub, row)["reminder_state"] == "stopped" and replies == []
