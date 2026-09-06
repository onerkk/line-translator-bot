"""Reminder timing, actual Redis Lua, retries, permissions, and API contracts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import json
import logging
from pathlib import Path
import subprocess
import threading
import urllib.error
import uuid

import fakeredis
from flask import Flask
import pytest

import reminders_web as web
import scheduled_reminders as r

GID = "C" + "1" * 32
UID = "U" + "2" * 32
UID2 = "U" + "3" * 32
NOW = datetime(2026, 9, 6, tzinfo=timezone.utc).timestamp()
DUE = datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp()
CATALOG = {GID: {"name": "研磨 C 班", "members": {UID: "小麥", UID2: "Irwan"}}}


def spec(**changes):
    return {"request_id": str(uuid.uuid4()), "group_id": GID, "local_time": "2026-09-20T08:00",
            "content": "要開班股會議", "mention_mode": "all", "user_ids": [], **changes}


@pytest.fixture(params=["sqlite", "redis"])
def store(request, tmp_path):
    if request.param == "sqlite":
        return r.SQLiteReminderStore(tmp_path / "reminders.db")
    fake = fakeredis.FakeRedis(decode_responses=True)
    instance = r.RedisReminderStore("https://example.invalid", "offline-token")
    instance._command = lambda args: fake.execute_command(*args)
    return instance


@pytest.fixture
def service(store):
    clock = [NOW]
    sent = []
    instance = r.ReminderService(store, lambda: CATALOG, sender=lambda row: sent.append(row), clock=lambda: clock[0])
    return instance, clock, sent


def test_taipei_example_and_restart_resume(service):
    instance, clock, sent = service
    row = instance.create(spec(), "admin")
    assert row["due_at"] == DUE
    assert row["timezone"] == "Asia/Taipei"
    assert not sent
    clock[0] = DUE - 1
    assert instance.run_due()["sent"] == 0
    # A new service has no memory of timers; persisted rows are sufficient.
    resumed_store = r.SQLiteReminderStore(instance.store.path) if instance.store.kind == "sqlite" else instance.store
    resumed = r.ReminderService(resumed_store, lambda: CATALOG, lambda row: sent.append(row), lambda: clock[0])
    clock[0] = DUE + 300
    assert resumed.run_due()["sent"] == 1
    assert len(sent) == 1
    assert resumed.run_due()["sent"] == 0
    assert resumed_store.get(row["id"])["status"] == "sent"


def test_atomic_parallel_claim_does_not_double_send(service):
    instance, clock, sent = service
    instance.create(spec(), "admin")
    clock[0] = DUE
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: instance.run_due(), range(12)))
    assert len(sent) == 1


def test_concurrent_create_retries_only_store_one_reminder(service):
    instance, clock, sent = service
    body = spec()
    with ThreadPoolExecutor(max_workers=5) as pool:
        rows = list(pool.map(lambda _: instance.create(body, "admin"), range(10)))
    assert len({row["id"] for row in rows}) == 1
    assert len(instance.store.list()) == 1
    assert not sent


def test_creation_contention_is_bounded(service, monkeypatch):
    instance, clock, sent = service
    calls = []
    monkeypatch.setattr(instance.store, "compare_swap", lambda *a: calls.append(a) or False)
    with pytest.raises(r.ReminderError, match="尚未確認"):
        instance.create(spec(), "admin")
    assert len(calls) == 4


def test_edit_and_cancel_race_with_stale_admin_page(service):
    instance, clock, sent = service
    row = instance.create(spec(), "admin")
    changed = instance.change(row["id"], spec(revision=row["revision"], local_time="2026-09-21T09:30"))
    with pytest.raises(r.ReminderError, match="已被更新"):
        instance.change(row["id"], {"revision": row["revision"]}, cancel=True)
    cancelled = instance.change(changed["id"], {"revision": changed["revision"]}, cancel=True)
    clock[0] = DUE + 10 * 86400
    assert instance.run_due()["sent"] == 0
    assert cancelled["status"] == "cancelled"
    assert not sent


def test_create_response_loss_is_idempotent_after_due_time(service):
    instance, clock, sent = service
    body = spec(mention_mode="users", user_ids=[UID, UID])
    row = instance.create(body, "admin")
    clock[0] = DUE + 10
    instance.run_due()
    assert instance.create(body, "admin")["id"] == row["id"]
    assert len(sent) == 1
    with pytest.raises(r.ReminderError):
        instance.create(dict(body, content="其他會議"), "admin")


def test_transport_failure_retries_identical_request(service):
    instance, clock, sent = service
    row = instance.create(spec(), "admin")
    attempts = []
    def sender(record):
        attempts.append(record)
        if len(attempts) == 1:
            raise r.DeliveryError("暫時離線")
    instance.sender = sender
    clock[0] = DUE
    assert instance.run_due()["retrying"] == 1
    queued = instance.store.get(row["id"])
    with pytest.raises(r.ReminderError, match="已嘗試派送"):
        instance.change(row["id"], spec(revision=queued["revision"]))
    clock[0] += 31
    assert instance.run_due()["sent"] == 1
    assert attempts[0]["retry_key"] == attempts[1]["retry_key"]
    assert r.build_line_message(attempts[0]) == r.build_line_message(attempts[1])


def test_lost_acknowledgement_uses_lease_and_retry_key(service, monkeypatch):
    instance, clock, sent = service
    row = instance.create(spec(), "admin")
    compare_swap = instance.store.compare_swap
    def fail_ack(previous, current):
        if current["status"] == "sent":
            raise r.StoreUnavailable()
        return compare_swap(previous, current)
    monkeypatch.setattr(instance.store, "compare_swap", fail_ack)
    clock[0] = DUE
    with pytest.raises(r.StoreUnavailable):
        instance.run_due()
    assert instance.store.get(row["id"])["status"] == "sending"
    clock[0] += r.LEASE_SECONDS - 1
    instance.run_due()
    assert len(sent) == 1
    monkeypatch.setattr(instance.store, "compare_swap", compare_swap)
    clock[0] += 2
    instance.run_due()
    assert len(sent) == 2  # LINE receives the same key and acknowledges without re-posting.
    assert sent[0]["retry_key"] == sent[1]["retry_key"]
    assert instance.store.get(row["id"])["status"] == "sent"


def test_old_ambiguous_delivery_stops_before_line_dedup_expires(service):
    instance, clock, sent = service
    row = instance.create(spec(), "admin")
    instance.sender = lambda _: (_ for _ in ()).throw(r.DeliveryError("timeout"))
    clock[0] = DUE
    instance.run_due()
    clock[0] += r.RETRY_WINDOW + 1
    assert instance.run_due()["uncertain"] == 1
    assert instance.store.get(row["id"])["status"] == "uncertain"
    assert not instance.store.due(clock[0])


def test_permanent_failure_is_visible_and_not_retried(service):
    instance, clock, sent = service
    row = instance.create(spec(), "admin")
    instance.sender = lambda _: (_ for _ in ()).throw(r.DeliveryError("指定成員已離開群組", False))
    clock[0] = DUE
    assert instance.run_due()["failed"] == 1
    assert "離開" in instance.store.get(row["id"])["last_error"]
    assert not instance.store.due(clock[0] + 86400)


def test_store_history_paginates_and_cas_cannot_overwrite_claim(service):
    instance, clock, sent = service
    first = instance.create(spec(), "admin")
    clock[0] += 1
    second = instance.create(spec(), "admin")
    assert instance.store.list(limit=1)[0]["id"] == second["id"]
    assert instance.store.list(offset=1, limit=1)[0]["id"] == first["id"]
    current = dict(first, status="sending", lease_until=DUE + 120, revision=2)
    assert instance.store.compare_swap(first, current)
    assert not instance.store.compare_swap(first, dict(first, status="cancelled", revision=2))
    with pytest.raises(r.ReminderError):
        instance.change(first["id"], {"revision":2}, cancel=True)


@pytest.mark.parametrize("changes", [
    {"local_time":"2026-02-30T08:00"}, {"local_time":"2026-09-20T24:00"},
    {"local_time":"2026-09-20T08:00Z"}, {"local_time":"2026-09-06T08:00"},
    {"local_time":"2100-09-20T08:00"}, {"local_time":None},
    {"content":"  "}, {"content":"😀"*751}, {"content":"bad\x00text"}, {"content":"\ud800"},
    {"group_id":"U"+"1"*32}, {"group_id":"C"+"f"*32}, {"group_id":[]},
    {"mention_mode":"unknown"}, {"mention_mode":"users","user_ids":[]},
    {"mention_mode":"users","user_ids":["U"+"f"*32]},
    {"mention_mode":"all","user_ids":[UID]}, {"user_ids":[{}]},
])
def test_reject_invalid_schedule(changes):
    with pytest.raises(r.ReminderError):
        r.validate_spec(spec(**changes), CATALOG, NOW)


def test_real_line_v2_mentions_and_literal_brace_escape():
    from linebot.v3.messaging import PushMessageRequest, TextMessageV2
    row = r.validate_spec(spec(content="{m0} 大家帶資料 {a}\n🙂"), CATALOG, NOW)
    message = r.build_line_message(row)
    assert message["substitution"] == {"m0":{"type":"mention","mentionee":{"type":"all"}}}
    assert "{{m0}} 大家帶資料 {{a}}" in message["text"]
    parsed = TextMessageV2.from_dict(message)
    assert PushMessageRequest(to=GID, messages=[parsed]).to_dict()["messages"] == [message]
    row = r.validate_spec(spec(mention_mode="users", user_ids=[UID,UID2]), CATALOG, NOW)
    message = r.build_line_message(row)
    assert [v["mentionee"]["userId"] for v in message["substitution"].values()] == [UID,UID2]
    assert TextMessageV2.from_dict(message).to_dict() == message


def test_mention_limit_and_worst_case_message_size():
    members = {"U"+format(i,"032x"):str(i) for i in range(21)}
    catalog = {GID:{"name":"group","members":members}}
    with pytest.raises(r.ReminderError):
        r.validate_spec(spec(mention_mode="users", user_ids=list(members)), catalog, NOW)
    row = r.validate_spec(spec(mention_mode="users", user_ids=list(members)[:20], content="{"*1500), catalog, NOW)
    assert r.utf16_units(r.build_line_message(row)["text"]) <= 5000
    row = r.validate_spec(spec(mention_mode="none", content="{literal}"), catalog, NOW)
    assert r.build_line_message(row)["type"] == "text"
    assert r.build_line_message(row)["text"].endswith("{literal}")


@pytest.mark.parametrize("code,ack,retryable", [(409,True,None),(409,False,True),(400,False,False),
                                                (401,False,False),(429,False,True),(503,False,True)])
def test_line_http_acknowledgement_and_retry_classification(monkeypatch, code, ack, retryable):
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "offline-token")
    seen = []
    def request(req, timeout):
        seen.append(req)
        headers = {"x-line-accepted-request-id":"accepted"} if ack else {}
        raise urllib.error.HTTPError(req.full_url, code, "test", headers, io.BytesIO(b"{}"))
    monkeypatch.setattr(r.urllib.request, "urlopen", request)
    row = dict(r.validate_spec(spec(), CATALOG, NOW), retry_key=str(uuid.uuid4()))
    if retryable is None:
        r.send_line_reminder(row)
    else:
        with pytest.raises(r.DeliveryError) as exc:
            r.send_line_reminder(row)
        assert exc.value.retryable is retryable
    payload = json.loads(seen[0].data)
    assert payload["messages"][0]["type"] == "textV2"
    assert payload["notificationDisabled"] is False
    assert dict(seen[0].header_items())["X-line-retry-key"] == row["retry_key"]


def test_missing_or_broken_cloud_never_falls_back_to_temporary_file(monkeypatch, tmp_path):
    for key in ("UPSTASH_REDIS_REST_URL","UPSTASH_REDIS_REST_TOKEN","REMINDERS_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(r.StoreUnavailable):
        r.configured_store()
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://example.invalid")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "offline")
    monkeypatch.setenv("REMINDERS_DB_PATH", str(tmp_path / "must-not-create.db"))
    cloud = r.configured_store()
    assert cloud.kind == "upstash"
    monkeypatch.setattr(r.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(r.StoreUnavailable):
        cloud.get("id")
    assert not (tmp_path / "must-not-create.db").exists()


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    monkeypatch.setattr(r.ReminderWorker, "start", lambda *a, **k: None)
    monkeypatch.setattr(web, "configured_store", lambda: r.SQLiteReminderStore(tmp_path / "api.db"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "offline")
    app = Flask("reminders-test")
    from flask import request
    web.register_reminders(app, authorize=lambda: "admin" if request.headers.get("X-Test-Key") == "secret" else None,
                           catalog=lambda: CATALOG)
    instance = app.extensions["scheduled_reminders"]["service"]()
    instance.clock = lambda: NOW
    instance.sender = lambda _: pytest.fail("Saving must never immediately send a reminder")
    return app.test_client(), {"X-Test-Key":"secret"}, instance


def test_admin_api_create_list_edit_cancel_and_no_live_send(api_client):
    client, headers, instance = api_client
    assert client.get('/api/admin/reminders').status_code == 403
    result = client.post('/api/admin/reminders', headers=headers, json=spec())
    assert result.status_code == 201
    row = result.json["reminder"]
    assert "retry_key" not in row
    result = client.get('/api/admin/reminders', headers=headers)
    assert result.json["groups"][0]["members"][0]["user_id"] == UID
    assert result.json["status"]["timezone"] == "Asia/Taipei"
    assert len(result.json["reminders"]) == 1
    updated = client.put('/api/admin/reminders/'+row["id"], headers=headers,
                         json=spec(revision=row["revision"], content="更新會議內容"))
    assert updated.status_code == 200
    cancelled = client.post('/api/admin/reminders/'+row["id"]+'/cancel', headers=headers,
                            json={"revision":updated.json["reminder"]["revision"]})
    assert cancelled.json["reminder"]["status"] == "cancelled"


def test_admin_save_failure_never_claims_success(api_client, monkeypatch):
    client, headers, instance = api_client
    monkeypatch.setattr(instance.store, "compare_swap", lambda *a: (_ for _ in ()).throw(r.StoreUnavailable()))
    response = client.post('/api/admin/reminders', headers=headers, json=spec())
    assert response.status_code == 503
    assert response.json["ok"] is False
    assert client.post('/api/admin/reminders', headers=headers, json=[]).status_code == 400
    assert client.get('/api/admin/reminders?offset=bad', headers=headers).status_code == 400


def test_cron_requires_separate_secret_and_only_wakes_due_worker(api_client, monkeypatch):
    client, headers, instance = api_client
    monkeypatch.delenv("REMINDERS_CRON_SECRET", raising=False)
    assert client.post('/api/reminders/tick').status_code == 503
    monkeypatch.setenv("REMINDERS_CRON_SECRET", "x"*40)
    assert client.post('/api/reminders/tick', headers=headers).status_code == 403
    calls = []
    monkeypatch.setattr(r.ReminderWorker, "start", lambda self, force=False: calls.append(force))
    response = client.post('/api/reminders/tick', headers={"Authorization":"Bearer "+"x"*40})
    assert response.status_code == 202 and calls[-1] is True
    assert "reminders" not in response.json


def test_existing_manager_apis_also_reject_forged_identity(monkeypatch):
    import app as bot
    monkeypatch.setattr(bot, "ADMIN_KEY", "test-secret")
    monkeypatch.setattr(bot, "admin_users", {UID:{"is_admin":True,"allowed_tabs":["reminders","users"]}})
    forged = {"X-Manager-Id": UID}
    with bot.app.test_request_context(headers=forged):
        assert not bot.check_manager_access("users")
        assert not bot._authorize_reminders()
    signed = dict(forged, **{"X-Manager-Token":web.issue_manager_token(UID,"test-secret")})
    with bot.app.test_request_context(headers=signed):
        assert bot.check_manager_access("reminders")
        assert bot._authorize_reminders() == UID
    bot.admin_users[UID]["allowed_tabs"] = ["users"]
    with bot.app.test_request_context(headers=signed):
        assert not bot._authorize_reminders()
    monkeypatch.setattr(bot,"ADMIN_KEY","")
    with bot.app.test_request_context():
        assert not bot.check_admin_key()


def test_manager_session_expiry_and_wrong_signature(monkeypatch):
    import itsdangerous.timed
    monkeypatch.setattr(itsdangerous.timed.TimestampSigner, "get_timestamp", lambda self: 1000)
    token = web.issue_manager_token(UID, "secret")
    assert web.verified_manager(token, "secret") == UID
    assert web.verified_manager(token, "wrong") is None
    monkeypatch.setattr(itsdangerous.timed.TimestampSigner, "get_timestamp", lambda self: 1000+12*3600+1)
    assert web.verified_manager(token, "secret") is None


def test_google_login_uses_verified_identity_and_issues_session(monkeypatch):
    import base64
    import time
    import app as bot
    monkeypatch.setattr(bot, "ADMIN_KEY", "test-secret")
    monkeypatch.setattr(bot, "GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setattr(bot, "admin_users", {UID:{"is_admin":True,"allowed_tabs":["reminders"],
                                                 "google_email":"approved@example.invalid"}})
    # The token-info response is authoritative, not the decoded client payload.
    claim = base64.urlsafe_b64encode(json.dumps({"email":"unverified@example.invalid"}).encode()).decode().rstrip('=')
    verified = {"aud":"test-client", "iss":"https://accounts.google.com", "exp":time.time()+3600,
                "email_verified":"true", "email":"approved@example.invalid"}
    monkeypatch.setattr(bot.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(verified).encode()))
    client = bot.app.test_client()
    result = client.post('/api/admin/manager-login', json={"credential":"header."+claim+".signature"})
    assert result.status_code == 200
    assert result.json["email"] == "approved@example.invalid"
    assert web.verified_manager(result.json["manager_token"], "test-secret") == UID
    verified["email_verified"] = "false"
    assert client.post('/api/admin/manager-login', json={"credential":"header."+claim+".signature"}).status_code == 403
    assert client.post('/api/admin/manager-login', json=[]).status_code == 400


def test_forked_service_releases_inherited_lock_and_preserves_jobs(api_client, monkeypatch):
    client, headers, instance = api_client
    row = instance.create(spec(), "admin")
    factory = client.application.extensions["scheduled_reminders"]["service"]
    closure = dict(zip(factory.__code__.co_freevars, (cell.cell_contents for cell in factory.__closure__)))
    inherited_lock = closure["service_lock"]
    inherited_lock.acquire()
    try:
        monkeypatch.setattr(web.os, "getpid", lambda: closure["service_pid"] + 1)
        restored = factory()
        assert restored is not instance
        assert restored.store.get(row["id"])["content"] == "要開班股會議"
    finally:
        inherited_lock.release()


def test_reminder_ui_assets_and_tab_permission_match():
    import app as bot
    assert "switchTab('reminders')" in bot.ADMIN_HTML
    assert "['reminders','自訂提醒']" in bot.ADMIN_HTML
    assert "'overview','reminders','groups'" in bot.ADMIN_HTML
    assert 'id="reminder-content"' in bot.ADMIN_HTML
    assert 'type="date"' in bot.ADMIN_HTML and 'type="time"' in bot.ADMIN_HTML
    js = Path(__file__).with_name("static").joinpath("admin_reminders.js")
    result = subprocess.run(["node","--check",str(js)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert '.innerHTML' not in js.read_text()
