"""Actual SQLite/Redis atomic state and Flask routes; LINE/AI transport is fake."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs

from flask import Flask
import pytest
from linebot.v3.messaging import TextMessage, FlexMessage, FlexContainer, QuickReply, QuickReplyItem, PostbackAction

import line_factory_features as factory
from line_factory_store import FeatureStore, StoreError
import line_translation_delivery as delivery
import line_liff_forms as forms
import translation_retry_queue as queue

GROUP = "C" + "1" * 32
OTHER = "C" + "2" * 32
USER = "U" + "a" * 32
COLLEAGUE = "U" + "b" * 32


def event(text="PMI一定要檢測。", *, stamp=100, edited=False, group=GROUP, uid=USER, mid="123", mentions=None):
    return {"type": "messageEdited" if edited else "message", "timestamp": stamp,
            "webhookEventId": "event-" + str(stamp), "replyToken": "reply-" + str(stamp),
            "source": {"type": "group", "groupId": group, "userId": uid},
            "message": {"type": "text", "id": mid, "text": text, "mention": {"mentionees": mentions or []}}}


@pytest.fixture(params=["sqlite", "redis"])
def storage(request, tmp_path):
    if request.param == "sqlite":
        return FeatureStore(path=tmp_path / "factory.db")
    import fakeredis
    redis = fakeredis.FakeRedis(decode_responses=True)
    return FeatureStore(command=lambda args: redis.execute_command(*args))


@pytest.fixture
def hub(storage, monkeypatch, tmp_path):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "queue.db"))
    app = Flask(__name__)
    host = {"factory_line_settings": {"groups": {}, "stations": []},
            "_reminder_catalog": lambda: {GROUP: {"name": "A 班"}, OTHER: {"name": "B 班"}},
            "group_tracking": {GROUP: {"name": "A 班"}},
            "group_user_names": {GROUP: {USER: "管理者", COLLEAGUE: "Adi"}},
            "save_settings": lambda **kwargs: True,
            "check_manager_access": lambda feature: True, "_state_lock": threading.RLock(),
            "LIFF_ID": "1234567890-test", "LINE_CHANNEL_SECRET": "test-secret",
            "_GLOSSARY_JSON": json.dumps({"I5": {"idn": "mesin I5", "note_zh": "內建 I5 說明"}}),
            "ai_provider": SimpleNamespace(get_provider_diagnostics=lambda: {}),
            "VALID_TARGETS": {"zh", "id", "en"}, "serialize_request": lambda key: nullcontext(),
            "_translation_job_scope": lambda: nullcontext(), "_tl": SimpleNamespace(),
            "_is_translation_failure_sentinel": lambda text: False,
            "_translation_cache_asset_fingerprint": lambda: "assets-1",
            "forms_data": {"f1": {"id": "f1", "title_zh": "PMI 表", "status": "active", "target_groups": [GROUP]}},
            "translate": lambda text, src, tgt: "Hasil: " + text}
    hub = factory.FactoryHub(app, host, storage)
    hub.register_routes()
    return hub


def headers(hub, group=GROUP, uid=USER, context=""):
    ticket = parse_qs(urlparse(hub.session_url(group, uid, context)).query)["session"][0]
    return {"X-Factory-Session": ticket}


def notice(hub):
    with hub.message_scope(event(), "text") as active:
        assert active
        payload = {"group_id": GROUP, "user_id": USER, "message_id": "123", "source_text": "PMI一定要檢測。",
                   "factory_event": hub.payload_metadata(), "target_langs": ["id"], "src_lang": "zh"}
        messages = hub.decorate_delivery([TextMessage(text="Pemeriksaan PMI wajib dilakukan.")], payload, "Pemeriksaan PMI wajib dilakukan.")
    qr = messages[-1].quick_reply.items
    token = dict(parse_qs(qr[0].action.data))["token"][0]
    return token, payload, messages


def test_atomic_updates_survive_concurrency_and_are_group_scoped(storage):
    def write(uid):
        def change(row):
            row = row or {};row[uid] = "understood";return row
        storage.update("notice:" + GROUP + ":one", change)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, ["a", "b", "c", "d"]))
    assert storage.get("notice:" + GROUP + ":one") == dict.fromkeys("abcd", "understood")
    assert storage.recent("notice:" + OTHER) == []
    storage.delete("notice:" + GROUP + ":one")
    assert storage.recent("notice:" + GROUP) == []


def test_compare_swap_conflict_does_not_overwrite_current_state(storage):
    storage.put("revision:test", {"n": 2})
    assert not storage.compare_swap("revision:test", {"n": 1}, {"n": 3}, 10)
    assert storage.get("revision:test") == {"n": 2}


def test_edit_and_unsend_invalidate_old_context_and_leases(hub):
    token, payload, _ = notice(hub)
    key = GROUP + ":123"
    queue.enqueue(key, payload)
    assert queue.claim_job(key, owner="old-worker")
    with hub.message_scope(event("PMI明天檢測。", stamp=200, edited=True), "text") as active:
        assert active and not hub.current(payload["factory_event"])
        assert hub.job_key(GROUP, "123", key).startswith(key + ":edit:")
    assert hub.get_context(token, GROUP) is None
    assert queue.get(key) is None
    assert not queue.checkpoint(key, {"done": True}, owner="old-worker")
    with hub.message_scope(event(), "text") as active:
        assert not active  # An old original cannot supersede its edit.
    hub.unsend({"source": {"groupId": GROUP}, "unsend": {"messageId": "123"}})
    assert not hub.store.recent("notice:" + GROUP)
    with hub.message_scope(event(stamp=300, edited=True), "text") as active:
        assert not active


@pytest.mark.parametrize("setting", [{"edit_translation": False}, {"translation_mode": "mentioned"}])
def test_filtered_edit_still_invalidates_old_translation(hub, setting):
    token, payload, _ = notice(hub)
    hub.h["factory_line_settings"]["groups"][GROUP] = setting
    with hub.message_scope(event(stamp=200, edited=True), "text") as active:
        assert not active
    assert not hub.current(payload["factory_event"])
    assert hub.get_context(token) is None


def test_mention_mode_uses_metadata_and_preserves_commands_media(hub):
    hub.h["factory_line_settings"]["groups"][GROUP] = {"translation_mode": "mentioned"}
    for text in ("@機器人 PMI 檢驗", "普通文字"):
        with hub.message_scope(event(text), "text") as active:
            assert not active
    with hub.message_scope(event("@bot PMI", mentions=[{"isSelf": True}]), "text") as active:
        assert active
    with hub.message_scope(event("/factory", mid="command"), "text") as active:
        assert active
        assert hub.current(hub.payload_metadata())
    with hub.message_scope(event("", mid="image"), "image") as active:
        assert active


def test_native_mentions_use_utf16_metadata_and_preserve_braces_and_names():
    source = "😀 @阿迪 {PMI}"
    msg = event(source, mentions=[{"type": "user", "userId": COLLEAGUE, "index": 3, "length": 3}])["message"]
    mentions = factory.native_mentions(msg)
    assert mentions[0]["label"] == "@阿迪"
    converted = factory.text_with_mentions(TextMessage(text=source), mentions)
    assert converted["type"] == "textV2"
    assert converted["substitution"]["person0"]["mentionee"]["userId"] == COLLEAGUE
    assert delivery.without_native_mentions(converted, mentions).text == source
    assert factory.native_mentions(event(source, mentions=[{"type": "user", "userId": COLLEAGUE, "index": 3, "length": 3, "isSelf": True}])["message"]) == []


def test_identical_display_names_keep_distinct_verified_mention_targets():
    mentions = [{"type": "user", "userId": USER, "label": "@Adi"},
                {"type": "user", "userId": COLLEAGUE, "label": "@Adi"}]
    converted = factory.text_with_mentions(TextMessage(text="@Adi @Adi periksa PMI."), mentions)
    assert [item["mentionee"]["userId"] for item in converted["substitution"].values()] == [USER, COLLEAGUE]


def test_flex_and_existing_quick_replies_survive_factory_decoration(hub):
    e = event("@Adi PMI", mentions=[{"type": "user", "userId": COLLEAGUE, "index": 0, "length": 4}])
    flex = FlexMessage(alt_text="PMI", contents=FlexContainer.from_dict({"type": "bubble", "body": {"type": "box", "layout": "vertical", "contents": [{"type": "text", "text": "PMI"}]}}),
                       quick_reply=QuickReply(items=[QuickReplyItem(action=PostbackAction(label="原有按鈕", data="old=1"))]))
    with hub.message_scope(e, "text"):
        payload = {"group_id": GROUP, "message_id": "123", "source_text": "@Adi PMI", "factory_event": hub.payload_metadata()}
        result = hub.decorate_delivery([flex], payload, "Periksa PMI")
    restored = delivery.restore_messages([delivery.message_dict(m) for m in result])
    assert [m.type for m in restored] == ["textV2", "flex"]
    assert restored[-1].contents.to_dict() == flex.contents.to_dict()
    assert restored[-1].quick_reply.items[0].action.data == "old=1"
    assert any("factory_ack" in x.action.data for x in restored[-1].quick_reply.items)


def test_acknowledgements_are_atomic_user_actions_and_replay_cannot_undo(hub, monkeypatch):
    token, _, _ = notice(hub)
    replies = []
    monkeypatch.setattr(hub, "_reply", lambda e, text, **kw: replies.append(text))
    for uid, stamp, action in [(USER, 300, "factory_ack"), (COLLEAGUE, 400, "factory_help"), (COLLEAGUE, 200, "factory_ack")]:
        hub.postback(event(uid=uid, stamp=stamp), {"action": action, "token": token})
    record = hub.store.get("notice:" + GROUP + ":" + token)
    assert record["responses"][USER]["status"] == "understood"
    assert record["responses"][COLLEAGUE]["status"] == "needs_help"
    assert record["roster_basis"] == "known_chat_members"
    hub.postback(event(group=OTHER), {"action": "factory_ack", "token": token})
    assert hub.store.get("notice:" + OTHER + ":" + token) is None


def test_member_sessions_and_admin_permissions(hub, monkeypatch):
    client = hub.app.test_client()
    assert client.get("/api/factory/session").status_code == 403
    assert client.get("/api/factory/session", headers=headers(hub)).json["group_name"] == "A 班"
    monkeypatch.setattr(hub, "_signer", lambda: SimpleNamespace(loads=lambda *a, **k: (_ for _ in ()).throw(factory.BadSignature("expired"))))
    assert client.get("/api/factory/session", headers={"X-Factory-Session": "old"}).status_code == 403
    hub.h["check_manager_access"] = lambda _: False
    assert client.get("/api/admin/factory").status_code == 403


def test_station_overrides_optimistic_save_and_real_qr_png(hub):
    global_row = {"code": "I5", "name_zh": "全廠機台", "name_id": "Global"}
    group_row = {**global_row, "group_id": GROUP, "name_zh": "A 班 I5", "name_id": "Mesin A"}
    hub.update_settings({"stations": [group_row, global_row], "expected_version": hub.settings_version()})
    assert hub.resolve_station("line-factory:I5", GROUP)["name_zh"] == "A 班 I5"
    assert hub.resolve_station("I5", OTHER)["name_zh"] == "全廠機台"
    assert "行為流程" in hub.resolve_station("PMI", GROUP)["context"]
    with pytest.raises(ValueError, match="重新整理"):
        hub.update_settings({"stations": [], "expected_version": "stale"})
    response = hub.app.test_client().get("/api/admin/factory/qr?code=I5&group_id=" + GROUP)
    assert response.mimetype == "image/png" and response.data.startswith(b"\x89PNG\r\n\x1a\n")
    for value in ("https://attacker/PMI", "file:///etc/passwd", "<script>"):
        with pytest.raises(ValueError):
            hub.resolve_station(value, GROUP)


def test_failed_save_rolls_back_and_form_scope_is_validated(hub):
    before = copy.deepcopy(hub.settings())
    hub.h["save_settings"] = lambda **kw: False
    with pytest.raises(StoreError):
        hub.update_settings({"group_id": GROUP, "options": {"sharing": False}, "expected_version": hub.settings_version()})
    assert hub.settings() == before
    with pytest.raises(ValueError, match="群組"):
        hub.update_settings({"stations": [{"code": "PMI", "name_zh": "PMI", "name_id": "PMI", "form_id": "f1"}], "expected_version": hub.settings_version()})


def test_station_translation_cache_contains_station_and_assets(hub):
    seen = []
    hub.h["translate"] = lambda *args: seen.append(factory.station_prompt()) or "Periksa PMI."
    client = hub.app.test_client();h = headers(hub)
    body = {"station": "PMI", "text": "檢查一下", "src": "zh", "tgt": "id"}
    first = client.post("/api/factory/translate", headers=h, json=body)
    assert first.status_code == 200 and not first.json["cached"]
    assert "PMI" in seen[0] and not factory.station_prompt()
    assert client.post("/api/factory/translate", headers=h, json=body).json["cached"]
    assert len(seen) == 1
    hub.h["_translation_cache_asset_fingerprint"] = lambda: "assets-2"
    assert not client.post("/api/factory/translate", headers=h, json=body).json["cached"]
    body["station"] = "I5"
    assert not client.post("/api/factory/translate", headers=h, json=body).json["cached"]
    assert len(seen) == 3


def test_share_uses_current_context_and_never_sends_itself(hub):
    token, _, _ = notice(hub)
    client = hub.app.test_client();h = headers(hub, context=token)
    response = client.get("/api/factory/share", headers=h)
    assert response.status_code == 200 and len(response.json["messages"]) <= 5
    assert response.json["messages"][0]["text"].startswith("PMI一定要檢測。")
    with hub.message_scope(event(stamp=300, edited=True), "text"):
        pass
    assert client.get("/api/factory/share", headers=h).status_code == 400


def test_insight_privacy_schema_cache_and_bounds(hub, monkeypatch):
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return json.dumps({"richMenuId": "richmenu-" + "a" * 32}).encode()
    def transport(req, **kwargs):
        calls.append(req.full_url);return Response()
    monkeypatch.setattr(factory.urllib.request, "urlopen", transport)
    menu = "richmenu-" + "a" * 32
    today = factory.datetime.now(factory.timezone.utc).strftime("%Y%m%d")
    result = hub.insight(menu, today, today, "daily")
    assert result["privacy_limited"] and result["timezone"] == "Asia/Tokyo"
    assert hub.insight(menu, today, today, "daily")["cached"] and len(calls) == 1
    assert "/daily?from=" in calls[0]
    with pytest.raises(ValueError):
        hub.insight(menu, "20260101", "20261231", "daily")


@pytest.mark.parametrize("kind,value,required,valid", [
    ("text", "", True, False), ("text", "檢驗完成", True, True),
    ("number", "NaN", True, False), ("number", "1.5", True, True),
    ("date", "2026-02-30", True, False), ("date", "2026-09-07", True, True),
    ("select", "偽造選項", True, False), ("select", "合格", True, True),
    ("checkbox", "no", True, False), ("checkbox", "yes", True, True),
])
def test_server_validates_form_answers(kind, value, required, valid):
    form = {"fields": [{"id": "f", "type": kind, "required": required, "options": [{"zh": "合格", "id": "Lulus"}]}]}
    if valid:
        assert forms.answers(form, {"answers": {"f": value}}) == {"f": value}
    else:
        with pytest.raises(ValueError):
            forms.answers(form, {"answers": {"f": value}})


def test_form_identity_is_verified_against_line_channel(hub, monkeypatch):
    monkeypatch.setenv("LINE_LOGIN_CHANNEL_ID", "12345")
    def line(url, token=None):
        return {"client_id": "12345", "expires_in": 600} if "verify?" in url else {"userId": USER, "displayName": "LINE name"}
    monkeypatch.setattr(forms, "_get_json", line)
    with hub.app.test_request_context(headers={"Authorization": "Bearer valid"}, json={"user_id": COLLEAGUE}):
        assert forms.actor(hub.h)["user_id"] == USER
    monkeypatch.setattr(forms, "_get_json", lambda *a, **kw: {"client_id": "wrong", "expires_in": 600})
    with hub.app.test_request_context(headers={"Authorization": "Bearer wrong"}):
        with pytest.raises(PermissionError):
            forms.actor(hub.h)


def test_cloud_interaction_outage_does_not_stop_translation_or_revision_guard(hub, monkeypatch):
    def down(*args, **kwargs):
        raise StoreError("cloud unavailable")
    monkeypatch.setattr(hub.store, "get", down)
    monkeypatch.setattr(hub.store, "update", down)
    monkeypatch.setattr(hub.store, "merge_revision", down)
    monkeypatch.setattr(hub.store, "save_interaction", down)
    with hub.message_scope(event(), "text") as active:
        assert active
        metadata = hub.payload_metadata()
        payload = {"group_id": GROUP, "message_id": "123", "source_text": "PMI檢驗", "factory_event": metadata}
        rendered = hub.decorate_delivery([TextMessage(text="Periksa PMI.")], payload, "Periksa PMI.")
    assert len(rendered) == 1 and rendered[0].text == "Periksa PMI."
    assert not rendered[0].quick_reply
    assert hub.current(metadata)
    with hub.message_scope(event(stamp=200, edited=True), "text") as active:
        assert active and not hub.current(metadata)


def test_receipts_distinguish_prepared_from_accepted_delivery(hub):
    token, payload, _ = notice(hub)
    key = "notice:" + GROUP + ":" + token
    assert hub.store.get(key)["delivery_state"] == "prepared"
    hub.delivery_accepted(payload, {"factory_notice_token": token})
    assert hub.store.get(key)["delivery_state"] == "delivered"
