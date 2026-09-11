"""Reported short work requests through real translation guards and delivery.

Only provider HTTP and LINE HTTP are substituted by the imported fixtures.
No translation shortcut or production example is added for these sentences.
"""
import pytest
import time

import app
import ai_provider
import translation_retry_queue as queue
import webhook_runtime
from durable_workers import WorkerPool
from test_translation_notice_availability import runtime, event, delivered_text, clean_translation_context
from test_translation_instruction_cost_quality import offline_transport, response


TRANSLATE_OPENAI = app.translate_openai


@pytest.mark.parametrize("source,target", [
    ("幫忙今日點檢", "Tolong bantu lakukan pemeriksaan hari ini."),
    ("幫忙今日點檢", "Tolong lakukan pengecekan harian hari ini."),
    ("幫忙今日點檢", "Mohon bantu inspeksi hari ini."),
    ("請協助今天的例行檢查", "Tolong bantu pemeriksaan rutin hari ini."),
    ("麻煩明天檢查設備", "Tolong periksa peralatan besok."),
    ("今日點檢已完成", "Pemeriksaan hari ini sudah selesai."),
    ("不要套環", "Jangan pasang cincin pelindung."),
    ("獎助學金申請書9/24前繳回", "Formulir pengajuan beasiswa harap dikembalikan sebelum 9/24."),
])
def test_short_work_request_reaches_delivery(runtime, offline_transport, monkeypatch, source, target):
    calls = []
    def dispatch(provider, **kwargs):
        calls.append((provider, kwargs))
        return response(target)
    monkeypatch.setattr(app, "translate_openai", TRANSLATE_OPENAI)
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    message = event(source)
    app.handle_message(message)
    assert target in delivered_text(runtime)
    assert len(calls) == 1
    assert not queue.has_pending_source("notice-group", message.message.id)


def test_recovery_can_use_unspent_reply_when_push_is_unavailable(runtime, offline_transport, monkeypatch):
    """Short AI outage must not discard the still-valid LINE reply capability."""
    target = "Tolong bantu lakukan pemeriksaan hari ini."
    recovering = [False]
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(provider)
        if not recovering[0]:
            raise TimeoutError("temporary provider outage")
        return response(target)
    monkeypatch.setattr(app, "translate_openai", TRANSLATE_OPENAI)
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    message = event("幫忙今日點檢")
    message.timestamp = int(time.time() * 1000)
    # Some LINE accounts cannot push (e.g. exhausted push-message quota),
    # while the webhook's unused reply token can still deliver this response.
    runtime.push_down = True
    app.handle_message(message)
    assert runtime.sends == []
    job = queue.list_pending()[0]
    assert job["payload"]["source_text"] == message.message.text
    assert queue.reschedule(job["job_key"], delay_seconds=0)
    assert queue.claim_job(job["job_key"], owner="recovered-worker")
    recovering[0] = True
    assert app._run_translation_retry_job(queue.get(job["job_key"]), "recovered-worker")
    assert len(runtime.sends) == 1 and runtime.sends[0][0] == "reply"
    assert runtime.sends[0][1].reply_token == message.reply_token
    assert target in delivered_text(runtime)
    assert queue.was_delivered(job["job_key"])
    assert len(calls) == 4  # three failed transports, one completed generation


def run_pending(key):
    assert queue.reschedule(key, delay_seconds=0)
    assert queue.claim_job(key, owner="recovered-worker")
    return app._run_translation_retry_job(queue.get(key), "recovered-worker")


@pytest.mark.parametrize("age,event_age,expected", [
    (0, 0, "reply"),
    (50, 50, "reply"),
    (55, 55, "push"),
    (65, 65, "push"),
    (0, 1201, "push"),  # redelivery cannot renew a 20-minute-old event
    (-30, 0, "push"),  # a backwards clock never creates a new token lifetime
])
def test_worker_time_does_not_renew_reply_window(runtime, age, event_age, expected):
    message = event("幫忙今日點檢")
    now = time.time()
    message.timestamp = int((now - event_age) * 1000)
    runtime.provider_down = True
    with webhook_runtime.timing_scope(now - age):
        app.handle_message(message)
    key = "notice-group:notice-message"
    assert queue.get(key)["payload"]["reply_window"]["received_at"] == now - age
    runtime.provider_down = False
    runtime.provider_result = "Tolong bantu lakukan pemeriksaan hari ini."
    assert run_pending(key)
    assert len(runtime.sends) == 1 and runtime.sends[0][0] == expected


def test_failed_reply_is_not_replayed_and_translation_is_not_regenerated(runtime, monkeypatch):
    runtime.provider_result = "Tolong bantu lakukan pemeriksaan hari ini."
    runtime.reply_down = runtime.push_down = True
    message = event("幫忙今日點檢")
    message.timestamp = int(time.time() * 1000)
    with pytest.raises(TimeoutError):
        app.handle_message(message)
    key = "notice-group:notice-message"
    assert queue.get(key)["payload"]["delivery"]["attempted"]
    runtime.reply_down = runtime.push_down = False
    assert run_pending(key)
    assert len(runtime.sends) == 1 and runtime.sends[0][0] == "push"
    assert len(runtime.generations) == 1


def test_reply_rejected_during_recovery_falls_back_and_does_not_consume_ai_again(runtime):
    message = event("幫忙今日點檢")
    message.timestamp = int(time.time() * 1000)
    runtime.provider_down = True
    app.handle_message(message)
    runtime.provider_down = False
    runtime.provider_result = "Tolong bantu lakukan pemeriksaan hari ini."
    runtime.reply_down = runtime.push_down = True
    key = "notice-group:notice-message"
    with pytest.raises(TimeoutError):
        run_pending(key)
    assert queue.get(key)["payload"]["delivery"]["attempted"]
    assert queue.reschedule(key, owner="recovered-worker", delay_seconds=0)
    generated = len(runtime.generations)
    runtime.reply_down = runtime.push_down = False
    assert run_pending(key)
    assert len(runtime.generations) == generated
    assert len(runtime.sends) == 1 and runtime.sends[0][0] == "push"


def test_accepted_reply_survives_followup_write_error_without_duplicate_push(runtime, monkeypatch):
    original = app.factory_hub.delivery_accepted
    def unavailable(*args, **kwargs):
        raise RuntimeError("followup write temporarily unavailable")
    monkeypatch.setattr(app.factory_hub, "delivery_accepted", unavailable)
    with pytest.raises(RuntimeError):
        app.handle_message(event())
    assert len(runtime.sends) == 1 and runtime.sends[0][0] == "reply"
    monkeypatch.setattr(app.factory_hub, "delivery_accepted", original)
    assert run_pending("notice-group:notice-message")
    assert len(runtime.sends) == 1 and len(runtime.generations) == 1


def test_signed_webhook_outage_redelivery_and_worker_recovery(runtime, offline_transport, monkeypatch):
    """Real SDK parser, HTTP ACK, source journal, live queue worker and guards."""
    from linebot.v3.webhook import WebhookHandler
    from linebot.v3.webhooks import MessageEvent, TextMessageContent
    from test_webhook_latency_recovery import SECRET, signed, wait_until
    group = "Ctest"
    monkeypatch.setitem(app.group_settings, group, True)
    monkeypatch.setitem(app.group_target_lang, group, "id")
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("LINE_WEBHOOK_ASYNC", "0")
    monkeypatch.setattr(app, "translate_openai", TRANSLATE_OPENAI)
    target = "Tolong bantu lakukan pemeriksaan hari ini."
    calls, recovering = [], [False]
    def dispatch(provider, **kwargs):
        calls.append(provider)
        if not recovering[0]:
            raise TimeoutError()
        return response(target)
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    handler = WebhookHandler(SECRET)
    handler.add(MessageEvent, message=TextMessageContent)(app.handle_message)
    inbox = webhook_runtime.WebhookInbox(handler, app._release_webhook_message_claims,
                                         context=app.app.app_context)
    monkeypatch.setattr(app, "_WEBHOOK_INBOX", inbox)
    runtime.push_down = True
    client = app.app.test_client()
    body, signature = signed("1234", "幫忙今日點檢")
    first = client.post("/callback", data=body, headers={"X-Line-Signature": signature})
    assert first.status_code == 503 and runtime.sends == []
    # LINE redelivery must not create a new generation/job while one is pending.
    body, signature = signed("1234", "幫忙今日點檢", redelivery=True)
    again = client.post("/callback", data=body, headers={"X-Line-Signature": signature})
    assert again.status_code == 503
    assert calls == ["anthropic", "openai", "gemini"]
    jobs = queue.list_pending()
    assert len(jobs) == 1 and jobs[0]["payload"]["source_text"] == "幫忙今日點檢"
    key = jobs[0]["job_key"]
    assert queue.reschedule(key, delay_seconds=0)
    recovering[0] = True
    pool = WorkerPool("recovered-text", app._run_scheduled_translation, workers=2, include_kinds=("text",))
    try:
        pool.ensure_started()
        wait_until(lambda: queue.was_delivered(key))
    finally:
        pool.stop()
    assert target in delivered_text(runtime)
    assert len(runtime.sends) == 1 and runtime.sends[0][0] == "reply"
    assert calls == ["anthropic", "openai", "gemini", "anthropic"]
    completed = client.post("/callback", data=body, headers={"X-Line-Signature": signature})
    assert completed.status_code == 200 and len(runtime.sends) == 1 and len(calls) == 4


def test_public_health_identifies_recovery_code_without_live_provider_probe(runtime):
    result = app.app.test_client().get("/health")
    assert result.status_code == 200
    value = result.get_json()
    assert value["glossary_policy_build"] == f"policy-v{app.gp_module.POLICY_VERSION}"
    assert value["provider_error_policy_build"] == ai_provider.ERROR_POLICY_BUILD_ID
    assert value["reply_recovery_build"] == app.line_reply_window.BUILD_ID
    assert runtime.sends == [] and runtime.generations == []


def test_authorized_admin_health_uses_the_actual_glossary_version(runtime, offline_transport, monkeypatch):
    monkeypatch.setattr(app, "check_admin_key", lambda: True)
    result = app.app.test_client().get("/admin/health-check?format=json")
    assert result.status_code == 200
    value = result.get_json()
    assert value["translation_runtime"]["glossary_policy_build"] == f"policy-v{app.gp_module.POLICY_VERSION}"
    assert runtime.sends == [] and runtime.generations == []


def test_admin_health_still_requires_authorization(runtime, monkeypatch):
    monkeypatch.setattr(app, "check_admin_key", lambda: False)
    monkeypatch.setattr(app, "ADMIN_KEY", "offline-secret")
    result = app.app.test_client().get("/admin/health-check?format=json")
    assert result.status_code == 403
