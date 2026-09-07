"""Full app boundaries: signed webhook revisions, complete delivery and probes."""
import base64
import copy
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
import app
import ai_provider
import line_factory_features as factory
import line_liff_forms as forms
import line_translation_delivery as framing
import translation_retry_queue as queue
from test_translation_delivery_recovery import runtime, enqueue, run_job, LineError
from test_translation_notice_availability import runtime as notice_runtime, SOURCE, TARGET
from test_line_factory_features import USER, GROUP, event as raw_event


def signed_post(client, event):
    event = {**event, "mode": "active", "deliveryContext": {"isRedelivery": False}}
    event["message"] = {**event["message"], "quoteToken": "quote-" + str(event["timestamp"])}
    raw = json.dumps({"destination": "U" + "f" * 32, "events": [event]}, ensure_ascii=False)
    secret = app.handler.parser.signature_validator.channel_secret
    if isinstance(secret, str): secret = secret.encode()
    signature = base64.b64encode(hmac.new(secret, raw.encode(), hashlib.sha256).digest()).decode()
    return client.post("/callback", data=raw.encode(), headers={"X-Line-Signature": signature, "Content-Type": "application/json"})


def test_station_catalog_uses_actual_equipment_and_station_assets(monkeypatch):
    monkeypatch.setattr(app, "factory_line_settings", {"groups": {}, "stations": []})
    hub = app.factory_hub
    catalog = {row["code"]: row for row in hub.station_catalog(GROUP)}
    assert set(app.STATION_CODES) <= set(catalog)
    assert set(app.STATION_NAMES) <= set(catalog)
    assert catalog["E1-1"]["name_id"] == app.STATION_CODES["E1-1"]["id"]
    assert hub.resolve_station("line-factory:UT手", GROUP)["name_id"] == "Ultrasonic UT manual"
    assert "檢驗站" in catalog["480"]["context"]
    assert "冷抽機" in catalog["420"]["context"]
    assert "包裝" in catalog["490"]["context"] and "秤重" in catalog["490"]["context"]
    for code in ("I5", "I15"):
        assert "需確認製程" in catalog[code]["name_zh"]
        assert "拋光" in catalog[code]["context"] and "研磨" in catalog[code]["context"]
    assert all(not row["sop_zh"] and not row["sop_id"] for row in catalog.values())
    # A lowercase group override must replace the uppercase built-in entry.
    app.factory_line_settings["stations"] = [
        {"code": "i5", "group_id": GROUP, "name_zh": "A 班拋光機", "name_id": "Mesin polishing A"}]
    assert hub.resolve_station("I5", GROUP)["name_zh"] == "A 班拋光機"
    assert sum(row["code"].casefold() == "i5" for row in hub.station_catalog(GROUP)) == 1
    assert "需確認製程" in hub.resolve_station("I5", "other")["name_zh"]


def test_real_signed_edit_webhook_delivers_new_revision_once(notice_runtime):
    state = notice_runtime
    original = raw_event(SOURCE, group="notice-group", uid=USER)
    original["message"]["quoteToken"] = "quote"
    client = app.app.test_client()
    assert signed_post(client, original).status_code == 200
    assert len(state.sends) == 1
    edited = raw_event(SOURCE.replace("短期內", "近期內"), group="notice-group", stamp=200, edited=True)
    assert signed_post(client, edited).status_code == 200
    assert len(state.sends) == 2
    assert any("原文已修改" in m.text for m in state.sends[-1][1].messages if hasattr(m, "text"))
    assert signed_post(client, edited).status_code == 200
    assert signed_post(client, original).status_code == 200
    assert len(state.sends) == 2


def test_retry_preserves_all_attachments_and_last_controls(runtime):
    api, calls = runtime
    enqueue()
    attachments = [app.TextMessage(text="附件 " + str(i)) for i in range(6)]
    attachments[-1].quick_reply = app.QuickReply(items=[app.QuickReplyItem(action=app.PostbackAction(label="原按鈕", data="old=1"))])
    count = 0
    def fail_second(req, kwargs):
        nonlocal count
        count += 1
        if count == 2: raise LineError(503, "temporary")
    api.on_push = fail_second
    with pytest.raises(LineError):
        app._send_reply_with_push_fallback(reply_token="reply", target_id="group", message_obj=app.TextMessage(text="譯文"),
                                          fallback_text="譯文", append_messages=attachments, job_key="group:msg")
    plan = queue.get("group:msg")["payload"]["delivery"]
    assert len(plan["messages"]) == 7 and plan["next_batch"] == 1
    assert not app._complete_durable_text_job("group:msg")
    api.on_push = None
    assert run_job()
    accepted = calls[0][1].messages + calls[-1][1].messages
    assert [m.text for m in accepted] == ["譯文"] + ["附件 " + str(i) for i in range(6)]
    assert accepted[-1].quick_reply.items[0].action.data == "old=1"


def test_departed_mention_falls_back_to_readable_original_name(runtime):
    api, calls = runtime
    mentions = [{"type": "user", "userId": USER, "label": "@阿迪"}]
    message = app.line_delivery_module.restore_messages([factory.text_with_mentions(app.TextMessage(text="@阿迪 {PMI}"), mentions)])[0]
    def reject(req, kwargs):
        if req.messages[0].type == "textV2": raise LineError(400, "invalid mentionee")
    api.on_push = reject
    app._push_translation_batch("group", [message], framing.retry_key("test", 0), mentions=mentions)
    assert len(calls) == 2 and calls[-1][1].messages[0].text == "@阿迪 {PMI}"


def test_media_retry_captures_revision_before_processing(runtime):
    raw = raw_event("")
    with app.factory_hub.message_scope(raw, "audio"):
        key = app._schedule_media_translation_retry({"group_id": GROUP, "user_id": USER, "message_id": "123"}, "audio", delay_seconds=1)
    row = queue.get(key)
    assert row["payload"]["factory_event"]["message_id"] == "123"
    app.factory_hub.unsend({"source": {"groupId": GROUP}, "unsend": {"messageId": "123"}})
    assert queue.get(key) is None


def test_variant_retry_checks_original_revision_before_ai(runtime, monkeypatch):
    with app.factory_hub.message_scope(raw_event(), "text"):
        metadata = app.factory_hub.payload_metadata()
    payload = enqueue(kind="variant", factory_event=metadata, context={"factory_event": metadata}, mode="simple")
    with app.factory_hub.message_scope(raw_event(stamp=200, edited=True), "text"):
        pass
    monkeypatch.setattr(app, "_execute_translation_variant", lambda *a: pytest.fail("obsolete variant spent AI tokens"))
    assert queue.claim_job("group:msg", owner="test")
    with pytest.raises(factory.SupersededMessage):
        app._run_translation_retry_job(queue.get("group:msg"), "test")


def test_probe_uses_selected_provider_and_rejects_empty_result(monkeypatch):
    monkeypatch.setattr(app, "check_manager_access", lambda _: True)
    monkeypatch.setattr(app, "save_settings", lambda **kw: True)
    monkeypatch.setattr(ai_provider, "get_active_provider", lambda: "anthropic")
    monkeypatch.setattr(ai_provider, "get_provider_diagnostics", lambda *a: {})
    seen, cleared = [], []
    def provider(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Hentikan mesin."))], model="test", _jy_provider="anthropic")
    monkeypatch.setattr(ai_provider, "chat_complete", provider)
    monkeypatch.setattr(ai_provider, "complete_provider_probe", lambda p: cleared.append(p))
    client = app.app.test_client()
    assert client.post("/api/admin/ai-provider/test").json["provider"] == "anthropic"
    assert seen[0]["diagnostic_probe"] == "anthropic" and cleared == ["anthropic"]
    monkeypatch.setattr(ai_provider, "chat_complete", lambda **kw: SimpleNamespace(choices=[]))
    result = client.post("/api/admin/ai-provider/test")
    assert result.status_code == 502 and not result.json["ok"]
    assert cleared == ["anthropic"]


def test_native_speech_uses_updated_client_even_if_text_provider_is_claude(monkeypatch):
    native = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=lambda **kw: "語音內容")))
    seen = []
    monkeypatch.setattr(ai_provider, "_provider_has_key", lambda p: p == "openai")
    monkeypatch.setattr(ai_provider, "get_active_provider", lambda: "anthropic")
    monkeypatch.setattr(ai_provider, "get_native_client", lambda p: seen.append(p) or native)
    monkeypatch.setattr(app, "oai", app._NativeOpenAIProxy())
    assert app._AIProxy._Audio._Transcriptions().create(model="test", file="fake") == "語音內容"
    assert seen == ["openai"]


def test_key_update_failure_does_not_change_runtime_config(monkeypatch):
    old = {"openai": {"api_key": "old"}, "quota_exhausted_providers": {"openai": {"reason": "quota"}}}
    monkeypatch.setattr(ai_provider, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_provider, "_current_config", copy.deepcopy(old))
    monkeypatch.setattr(ai_provider, "_save_config_to_disk", lambda cfg: False)
    assert not ai_provider.update_provider_key("openai", "new")[0]
    assert ai_provider._current_config == old


def test_verified_form_submission_validation_persistence_and_duplicate(monkeypatch):
    monkeypatch.setattr(forms, "actor", lambda host: {"user_id": USER, "user_name": "LINE name", "picture_url": "", "groups": {GROUP}})
    monkeypatch.setattr(app, "forms_data", {"f1": {"id": "f1", "status": "active", "target_groups": [GROUP],
                                               "fields": [{"id": "pmi", "type": "checkbox", "required": True}]}})
    monkeypatch.setattr(app, "forms_submissions", {})
    monkeypatch.setattr(app, "save_settings", lambda **kw: False)
    client = app.app.test_client()
    assert client.get("/api/liff/forms").json["forms"][0]["status"] == "active"
    assert client.post("/api/liff/form/f1/submit", json={"answers": {"pmi": "no"}}).status_code == 400
    body = {"user_id": "spoofed", "answers": {"pmi": "yes"}}
    assert client.post("/api/liff/form/f1/submit", json=body).status_code == 503
    assert not app.forms_submissions["f1"]
    monkeypatch.setattr(app, "save_settings", lambda **kw: True)
    assert client.post("/api/liff/form/f1/submit", json=body).status_code == 200
    assert set(app.forms_submissions["f1"]) == {USER}
    assert client.post("/api/liff/form/f1/submit", json=body).status_code == 409
    assert client.get("/liff/settings?view=form&id=f1").status_code == 200


def test_liff_primary_redirect_initializes_before_routing(monkeypatch):
    monkeypatch.setattr(app, "LIFF_ID", "12345-test")
    client = app.app.test_client()
    entry = client.get("/liff/settings?liff.state=%3Fview%3Dfactory%26session%3Dtest").get_data(as_text=True)
    assert 'await liff.init' in entry and 'location.replace' in entry
    assert entry.index('await liff.init') < entry.index('location.replace')
    assert "state.origin!==location.origin" in entry
    settings = client.get("/liff/settings?nonce=test").get_data(as_text=True)
    assert '<meta name="liff-id" content="12345-test">' in settings


def test_invalid_webhook_signature_cannot_mutate_revision():
    raw = raw_event()
    body = json.dumps({"events": [raw]})
    response = app.app.test_client().post('/callback', data=body, headers={'X-Line-Signature': 'invalid'})
    assert response.status_code == 400
    assert app.factory_hub.revisions.get(app.factory_hub._rev_key(GROUP, '123')) is None


def test_postback_redelivery_with_new_reply_token_does_not_repeat_action(monkeypatch):
    monkeypatch.setattr(app, '_processed_msg_ids', app._collections_dedup.OrderedDict())
    calls = []
    monkeypatch.setattr(app.factory_hub, 'postback', lambda event, params: calls.append(event) or True)
    first = SimpleNamespace(webhook_event_id='stable-event', reply_token='first-token',
                            postback=SimpleNamespace(data='action=factory_ack&token=example123'),
                            source=SimpleNamespace(group_id=GROUP, room_id=None, user_id=USER))
    app.handle_postback(first)
    second = copy.copy(first);second.reply_token = 'redelivery-token'
    app.handle_postback(second)
    assert len(calls) == 1
