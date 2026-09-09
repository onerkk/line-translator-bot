"""Latency/cost regressions at real delivery, Redis Lua and request boundaries.

AI and LINE transports are fake; no external messages or paid calls are made.
"""
import copy
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import fakeredis
import pytest

import app
import line_factory_features as factory
import line_factory_store as stores
from test_line_factory_features import GROUP, USER, storage, hub, event as raw_event  # noqa: F401
from test_translation_notice_availability import runtime, event, TARGET, delivered_text  # noqa: F401


QUICK_REPLY = app._build_translation_action_quick_reply


def test_notice_storage_does_not_delay_line_with_redundant_reads(runtime, monkeypatch, caplog):
    redis = fakeredis.FakeRedis(decode_responses=True)
    calls = []
    def command(args):
        calls.append((args[0], not bool(runtime.sends)))
        return redis.execute_command(*args)
    monkeypatch.setattr(app.factory_hub, "_store", stores.FeatureStore(command=command))
    monkeypatch.setattr(app, "_build_translation_action_quick_reply", QUICK_REPLY)
    monkeypatch.setattr(app, "_translation_action_cache", {})
    for name in ("group_tracking", "group_settings", "group_skip_users", "group_target_lang"):
        monkeypatch.setitem(getattr(app, name), GROUP, getattr(app, name)["notice-group"])
    monkeypatch.setitem(app.group_user_names, GROUP, {USER: "測試者"})
    message = event()
    message.source.group_id, message.source.user_id = GROUP, USER
    with caplog.at_level(logging.INFO, logger="app"):
        app.handle_message(message)
    assert TARGET in delivered_text(runtime)
    assert len(runtime.generations) == 1
    # Normal translation stores its revision and action context atomically;
    # command-only receipts add no before/after-delivery storage operations.
    assert calls == [("EVAL", True), ("EVAL", True)]
    timing = [r.message for r in caplog.records if "[DeliveryPerf]" in r.message]
    assert len(timing) == 1 and "storage_calls=2" in timing[0] and "sent=none" not in timing[0]
    assert "PMI" not in timing[0] and GROUP not in timing[0]
    # LINE redelivery must not repeat a paid generation or delivery.
    app.handle_message(message)
    assert len(runtime.sends) == len(runtime.generations) == 1
    notices = app.factory_hub.store.recent("notice:" + GROUP)
    assert notices == []
    assert all("factory_ack" not in json.dumps(message.to_dict())
               for _, request, _ in runtime.sends for message in request.messages)
    # A newly posted identical message reuses verified translation, while
    # still getting its own delivery (distinct from webhook redelivery).
    again = event()
    again.message.id = "new-notice-message"
    again.source.group_id, again.source.user_id = GROUP, USER
    app.handle_message(again)
    assert len(runtime.sends) == 2 and len(runtime.generations) == 1
    assert app.factory_hub.store.recent("notice:" + GROUP) == []


def test_atomic_registration_preserves_acknowledgements_and_source_index(storage):
    source = "source-contexts:revision:source-one"
    def record(n):
        return {"token": "token_000" + str(n), "group_id": GROUP, "original": "PMI 檢驗", "expires_at": 10**12}
    def save(n):
        row = record(n)
        storage.save_interaction(row, 1800, source_key=source,
                                 notice={**row, "responses": {}, "delivery_state": "prepared"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(save, range(4)))
    assert set(storage.get(source)["tokens"]) == {record(n)["token"] for n in range(4)}
    key = "notice:" + GROUP + ":" + record(0)["token"]
    storage.update(key, lambda row: {**row, "responses": {USER: "understood"}, "delivery_state": "delivered"})
    save(0)
    assert storage.get(key)["responses"] == {USER: "understood"}
    assert storage.get(key)["delivery_state"] == "delivered"
    assert len(storage.get(source)["tokens"]) == 4
    # Records produced by Lua must remain compatible with subsequent CAS edits.
    storage.update(source, lambda row: {"tokens": row["tokens"] + ["next_token"]})
    assert storage.get(source)["tokens"][-1] == "next_token"
    storage.put("context:temporary", {"a": 1})
    storage.put("context:temporary", None)
    assert storage.get("context:temporary") is None


def test_atomic_revision_merge_rejects_stale_edits_and_preserves_unsend(storage):
    key = "revision:one"
    original = {"identity": "original", "timestamp": 100, "order": "100", "edited": False, "cancelled": False}
    edited = {**original, "identity": "edit", "timestamp": 200, "order": "200", "edited": True}
    assert storage.merge_revision(key, original) == original
    assert storage.merge_revision(key, edited) == edited
    assert storage.merge_revision(key, original) == edited
    assert storage.merge_revision(key, {**edited, "identity": "late", "timestamp": 150}) == edited
    tombstone = {**edited, "cancelled": True}
    assert storage.merge_revision(key, tombstone) == tombstone
    assert storage.merge_revision(key, {**edited, "identity": "newest", "timestamp": 300}) == tombstone
    storage.update(key, lambda row: {**row, "cleanup": True})
    assert storage.get(key)["cleanup"]


def test_cloud_outage_is_not_retried_at_every_delivery_stage(monkeypatch):
    clock, attempted = [0.0], []
    monkeypatch.setattr(stores.time, "monotonic", lambda: clock[0])
    def command(args):
        attempted.append(args)
        if len(attempted) == 1:
            raise TimeoutError("private transport detail")
        return '{"recovered":true}'
    store = stores.FeatureStore(command=command)
    with stores.measure_storage() as stats:
        for _ in range(8):
            with pytest.raises(stores.StoreError) as error:
                store.get("context:one")
            assert "private" not in str(error.value)
        assert len(attempted) == 1 and stats["requests"] == 1 and stats["skipped"] == 7
        clock[0] = 16.0
        assert store.get("context:one") == {"recovered": True}
        assert len(attempted) == 2


def test_cloud_transport_reuses_connection_and_has_bounded_timeouts(monkeypatch):
    sessions, calls = [], []
    class Response:
        status_code = 200
        raw = SimpleNamespace(read=lambda *a, **kw: b'{"result":null}')
        def __enter__(self): return self
        def __exit__(self, *args): return False
    class Session:
        def __init__(self): sessions.append(self)
        def post(self, url, **kwargs):
            calls.append(kwargs)
            return Response()
    monkeypatch.setattr(stores.requests, "Session", Session)
    store = stores.FeatureStore(url="https://storage.invalid", token="offline-test")
    assert store.get("one") is None and store.get("two") is None
    assert len(sessions) == 1 and len(calls) == 2
    assert all(c["timeout"] == (2, 3) and c["allow_redirects"] is False for c in calls)


def test_cloud_context_lookup_does_not_lock_other_chats(monkeypatch):
    monkeypatch.setattr(app, "_translation_action_cache", {})
    def get_context(token, group):
        acquired = app._translation_action_lock.acquire(blocking=False)
        assert acquired, "cloud lookup held the lock for every chat"
        app._translation_action_lock.release()
        return {"group_id": group, "expires_at": 10**12}
    monkeypatch.setattr(app.factory_hub, "get_context", get_context)
    assert app._get_translation_action_context("fresh_context", GROUP)["group_id"] == GROUP


@pytest.mark.parametrize("chat,expected", [(GROUP, "group"), ("R" + "1" * 32, "room"), (USER, "direct")])
def test_profile_without_avatar_is_not_repeated_for_name_and_picture(monkeypatch, chat, expected):
    calls = []
    monkeypatch.setattr(app, "_line_profile_cache", {})
    monkeypatch.setattr(app, "group_user_names", {})
    monkeypatch.setattr(app, "user_pictures", {})
    monkeypatch.setattr(app, "user_languages", {})
    class Client:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def lookup(kind):
        def run(*args, **kwargs):
            assert kwargs["_request_timeout"] == (1, 2)
            calls.append(kind)
            return SimpleNamespace(display_name="Adi", picture_url=None, language="id")
        return run
    api = SimpleNamespace(get_group_member_profile=lookup("group"), get_room_member_profile=lookup("room"),
                          get_profile=lookup("direct"))
    monkeypatch.setattr(app, "ApiClient", Client)
    monkeypatch.setattr(app, "MessagingApi", lambda client: api)
    for _ in range(3):
        app.record_user_name(chat, USER)
        assert app.get_display_name(chat, USER) == "Adi"
        assert app.get_user_picture_url(chat, USER) == ""
    assert calls == [expected]
    assert app.user_languages[USER] == "id"


def test_profile_timeout_does_not_cascade_to_three_endpoints(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_line_profile_cache", {})
    class Client:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def timeout(*args, **kwargs):
        calls.append("group")
        raise TimeoutError("offline")
    api = SimpleNamespace(get_group_member_profile=timeout,
                          get_room_member_profile=lambda *a, **k: pytest.fail("wrong chat endpoint"),
                          get_profile=lambda *a, **k: pytest.fail("timeout cascaded to fallback"))
    monkeypatch.setattr(app, "ApiClient", Client)
    monkeypatch.setattr(app, "MessagingApi", lambda client: api)
    for _ in range(4):
        assert app._get_line_member_profile(GROUP, USER) is None
    assert calls == ["group"]


@pytest.mark.parametrize("notice_requested", [False, True], ids=["translation", "explicit-notice"])
def test_existing_flex_context_is_reused_and_late_media_revision_binds_receipt(hub, monkeypatch, notice_requested):
    with hub.message_scope(raw_event(), "image"):
        metadata = hub.payload_metadata()
    token = "existing_media_context"
    record = hub.save_context(token, {"group_id": GROUP, "msg_id": "123", "original": "PMI 檢驗",
                                      "translated": "Pemeriksaan PMI", "expires_at": 10**12,
                                      "notice_requested": notice_requested})
    hub.h["_translation_action_cache"] = {token: record}
    key = "notice:" + GROUP + ":" + token
    if notice_requested:
        assert hub.store.get(key) is not None
        hub.store.update(key, lambda row: {**row, "responses": {USER: "understood"}})
    else:
        assert hub.store.get(key) is None
    flex = app.FlexMessage(alt_text="PMI", contents=app.FlexContainer.from_dict({
        "type": "bubble", "body": {"type": "box", "layout": "vertical", "contents": [
            {"type": "button", "action": {"type": "postback", "label": "翻譯",
             "data": "action=translation_variant&token=" + token}}]}}))
    payload = {"group_id": GROUP, "message_id": "123", "source_text": "PMI 檢驗", "factory_event": metadata}
    reads = []
    get = hub.store.get
    def recorded_get(name):
        reads.append(name)
        return get(name)
    with monkeypatch.context() as scoped:
        scoped.setattr(hub.store, "get", recorded_get)
        messages = hub.decorate_delivery([flex], payload, "Pemeriksaan PMI")
    assert hub.store.get("context:" + token)["factory_event"] == metadata
    assert hub.h["_translation_action_cache"][token]["factory_event"] == metadata
    assert len(hub.store.recent("context")) == 1
    notices = hub.store.recent("notice:" + GROUP)
    if notice_requested:
        assert len(notices) == 1 and payload["factory_notice_token"] == token
        assert notices[0]["responses"] == {USER: "understood"}
        assert notices[0]["factory_event"] == metadata
        assert key in reads
    else:
        assert notices == [] and "factory_notice_token" not in payload
        assert key not in reads  # No redundant lookup for a nonexistent receipt.
        assert "factory_ack" not in json.dumps([message.to_dict() for message in messages])


def test_remote_revision_is_checked_before_original_after_restart(hub):
    key = hub._rev_key(GROUP, "123")
    with hub.message_scope(raw_event(stamp=200, edited=True), "text") as active:
        assert active
    # Lose only local revision state; the cloud still knows about the edit.
    hub.revisions.delete(key)
    with hub.message_scope(raw_event(), "text") as active:
        assert not active
    hub.unsend({"source": {"groupId": GROUP}, "unsend": {"messageId": "123"}})
    hub.revisions.delete(key)
    with hub.message_scope(raw_event(stamp=300, edited=True), "text") as active:
        assert not active


def test_casebook_reuse_keeps_asset_edits_group_scope_and_results_isolated(monkeypatch):
    source = "PMI一定要檢測。"
    examples = [{"zh": source, "id": "Pemeriksaan PMI wajib dilakukan.", "dir": "zh2id"}]
    glossary = {"PMI": {"idn": "PMI"}}
    monkeypatch.setattr(app, "GLOSSARY_LOOKUP", glossary)
    monkeypatch.setattr(app, "_scoped_translation_casebook_inputs",
                        lambda group: (examples, []) if group == "A" else ([], []))
    retrieve = app.translation_casebook_module.retrieve
    lookups = []
    def search(*args, **kwargs):
        lookups.append(args)
        return retrieve(*args, **kwargs)
    monkeypatch.setattr(app.translation_casebook_module, "retrieve", search)
    with app._translation_job_scope():
        first = app._retrieve_verified_translation_cases(source, "zh", "id", group_id="A")
        assert first
        expected = copy.deepcopy(first)
        first[0]["target"] = "corrupted by another consumer"
        assert app._retrieve_verified_translation_cases(source, "zh", "id", group_id="A") == expected
        assert len(lookups) == 1
        glossary["PMI"]["idn"] = "pemeriksaan PMI"
        app._retrieve_verified_translation_cases(source, "zh", "id", group_id="A")
        assert len(lookups) == 2
        examples[0]["id"] = "Pengujian PMI wajib dilakukan."
        changed = app._retrieve_verified_translation_cases(source, "zh", "id", group_id="A")
        assert changed[0]["target"] == examples[0]["id"] and len(lookups) == 3
        assert app._retrieve_verified_translation_cases(source, "zh", "id", group_id="B") == []
        assert len(lookups) == 4
    with app._translation_job_scope():
        app._retrieve_verified_translation_cases(source, "zh", "id", group_id="A")
        assert len(lookups) == 5


def test_casebook_does_not_reuse_a_reference_for_different_words_or_direction(monkeypatch):
    cases = [{"zh": "PMI一定要檢測。", "id": "Pemeriksaan PMI wajib dilakukan.", "dir": "zh2id"}]
    monkeypatch.setattr(app, "_scoped_translation_casebook_inputs", lambda group: (cases, []))
    with app._translation_job_scope():
        assert app._retrieve_verified_translation_cases("PMI一定要檢測。", "zh", "id")
        assert app._retrieve_verified_translation_cases("今天先吃飯。", "zh", "id") == []
        assert app._retrieve_verified_translation_cases("PMI一定要檢測。", "id", "zh") == []
