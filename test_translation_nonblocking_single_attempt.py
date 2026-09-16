"""Nonblocking delivery and once-only work, as requested on 2026-09-16.

All remote AI, OCR and LINE boundaries are fake. Local pipeline, SQLite leases,
SDK webhook parsing, quality assessment and final framing are real.
"""
import copy
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest
import ai_provider as ai
import app
import factory_translation_policy as policy
import translation_quality_gate as quality
import translation_retry_queue as queue
import webhook_runtime
from durable_workers import WorkerPool
from test_month_end_notice_delivery import SOURCE, TARGET
from test_translation_notice_availability import runtime, clean_translation_context, event, delivered_text
from test_translation_instruction_cost_quality import offline_transport
from test_webhook_latency_recovery import signed, SECRET
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent

ORIGINAL_TRANSLATOR = app.translate_openai


def response(text, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)],
                           model="offline", usage=None)


@pytest.mark.parametrize("candidate", [TARGET, TARGET.replace("143 ton", "134 ton"), "Terjemahan dari penyedia."])
def test_real_provider_to_line_path_never_retranslates_for_quality(runtime, offline_transport, monkeypatch, candidate):
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(provider)
        return response(candidate)
    monkeypatch.setattr(ai, "_dispatch_provider", dispatch)
    monkeypatch.setattr(app, "translate_openai", ORIGINAL_TRANSLATOR)
    app.handle_message(event(SOURCE))
    assert candidate in delivered_text(runtime)
    assert len(calls) == 1
    assert len(runtime.sends) == 1
    assert queue.pending_count() == 0
    app.handle_message(event(SOURCE))
    assert len(calls) == 1


@pytest.mark.parametrize("finish", ["stop", "length", "content_filter"])
def test_provider_diagnostics_cannot_start_another_call(offline_transport, monkeypatch, finish):
    calls = []
    monkeypatch.setattr(ai, "_dispatch_provider", lambda p, **kw: calls.append(p) or response(TARGET, finish))
    result = ai.chat_complete(model="test", messages=[{"role":"user","content":SOURCE}],
                             translation_max_generations=99, response_validator=lambda *_: (False,"arbitrary_old_rule"))
    assert result.choices[0].message.content == TARGET
    assert len(calls) == 1
    assert result._jy_quality_degraded


def test_provider_outage_is_one_attempt_even_with_multiple_routes(offline_transport, monkeypatch):
    calls=[]
    def broken(p, **kw):
        calls.append(p)
        raise TimeoutError("offline outage")
    monkeypatch.setattr(ai, "_dispatch_provider", broken)
    with pytest.raises(TimeoutError):
        ai.chat_complete(model="test", messages=[{"role":"user","content":SOURCE}])
    assert len(calls) == 1


def test_old_environment_cannot_reenable_reviews_or_extra_api_calls(offline_transport, monkeypatch):
    for name, value in {"FACTORY_TRANSLATION_REVIEW_MODE":"always", "FACTORY_ALLOW_ALWAYS_REVIEW":"1",
                        "TRANSLATION_TOTAL_API_ATTEMPTS":"99", "TRANSLATION_TOTAL_GENERATIONS":"99"}.items():
        monkeypatch.setenv(name,value)
    assert policy.review_mode() == "off"
    calls=[]
    monkeypatch.setattr(ai,"_dispatch_provider",lambda p,**kw:calls.append(p) or response(TARGET))
    @ai.translation_request_budget
    def run():
        ai.chat_complete(model="test",messages=[{"role":"user","content":SOURCE}])
        with pytest.raises(RuntimeError, match="already attempted"):
            ai.chat_complete(model="test",messages=[{"role":"user","content":SOURCE}])
    run()
    assert len(calls)==1


def test_quality_helper_never_calls_supplied_reviewer_or_discards_text():
    class Forbidden:
        def chat_complete(self, **kwargs):
            pytest.fail("quality helper must not generate another translation")
    wrong=TARGET.replace("143 ton","134 ton")
    result=quality.gate_and_revise(SOURCE,wrong,"zh","id",critical=True,model="test",
                                   ai_client=Forbidden(),force_review=True,require_review_success=True)
    assert result["text"] == wrong
    assert result["issues"] and not result["cacheable"] and not result["reviewed"]


def test_broken_quality_helpers_keep_text_without_cache(runtime, monkeypatch):
    def broken(*a,**kw):raise RuntimeError("validator unavailable")
    monkeypatch.setattr(app,"_delivery_validation_issues",broken)
    assert app._final_delivery_guard(SOURCE,TARGET,"zh","id")==TARGET
    assert app._tl.cacheable is False


@pytest.mark.parametrize("kind", ["text","image","audio","video","file","variant","webhook"])
def test_background_failure_is_terminal_for_every_translation_kind(tmp_path,monkeypatch,kind):
    monkeypatch.setattr(queue,"DB_PATH",str(tmp_path/'once.db'))
    queue.enqueue("work",{"job_kind":kind},job_kind=kind)
    calls=[]
    pool=WorkerPool("test-once",lambda job,owner:calls.append(job["job_key"]) or False,workers=1)
    try:
        pool.ensure_started()
        deadline=time.monotonic()+3
        while queue.pending_count() and time.monotonic()<deadline:
            time.sleep(.01)
        assert queue.get("work")["status"]=="failed"
        assert calls==["work"]
        assert not queue.enqueue("work",{},job_kind=kind)
        assert queue.claim_due_jobs(owner="again",now=time.time()+1000)==[]
        assert not queue.was_delivered("work")
    finally:pool.stop()


def test_expired_claim_and_legacy_retry_are_not_run_again(tmp_path,monkeypatch):
    monkeypatch.setattr(queue,"DB_PATH",str(tmp_path/'lease.db'))
    queue.enqueue("work",{})
    assert queue.claim_job("work",owner="crashed")
    assert queue.claim_due_jobs(owner="replacement",now=time.time()+1000)==[]
    assert queue.get("work")["status"]=="failed"
    assert not queue.enqueue("work",{})
    queue.enqueue("old",{})
    with sqlite3.connect(queue.DB_PATH) as db:
        db.execute("UPDATE translation_retry_jobs SET attempts=7 WHERE job_key='old'")
        db.execute("PRAGMA user_version=2")
    assert queue.pending_count()==0
    assert queue.get("old")["status"]=="failed"


@pytest.mark.parametrize("asynchronous",[False,True])
def test_authenticated_webhook_failure_is_not_redelivered_or_requeued(tmp_path,monkeypatch,asynchronous):
    monkeypatch.setattr(queue,"DB_PATH",str(tmp_path/'webhook.db'))
    monkeypatch.setattr(webhook_runtime,"asynchronous_ingress",lambda:asynchronous)
    handler=WebhookHandler(SECRET)
    calls=[]
    @handler.add(MessageEvent,message=TextMessageContent)
    def handle(event):
        calls.append(event.message.text)
        raise TimeoutError("fake processing outage")
    inbox=webhook_runtime.WebhookInbox(handler,lambda _:pytest.fail("must not release for automatic redelivery"),workers=1)
    body,signature=signed(text=SOURCE)
    try:
        key=inbox.accept(body,signature)
        deadline=time.monotonic()+3
        while queue.pending_count() and time.monotonic()<deadline:time.sleep(.01)
        assert queue.get(key)["status"]=="failed"
        inbox.accept(body,signature)
        assert calls==[SOURCE]
        assert queue.pending_count()==0
    finally:inbox.pool.stop()


def test_postprocessing_failure_keeps_the_paid_translation(runtime, monkeypatch):
    runtime.provider_result = TARGET
    original = app.finalize_factory_translation
    def broken_on_paid_result(source, candidate, *args, **kwargs):
        if candidate == TARGET:
            raise RuntimeError('offline postprocessing failure')
        return original(source, candidate, *args, **kwargs)
    monkeypatch.setattr(app, 'finalize_factory_translation', broken_on_paid_result)
    app.handle_message(event(SOURCE))
    assert TARGET in delivered_text(runtime)
    assert len(runtime.generations) == len(runtime.sends) == 1
    assert queue.pending_count() == 0


def test_provider_failure_cannot_return_another_messages_translation(runtime, offline_transport, monkeypatch):
    app._tl.generated_candidate = ('previous unrelated message', 'zh', 'id')
    def broken(*args, **kwargs):
        raise TimeoutError('offline provider unavailable')
    monkeypatch.setattr(ai, '_dispatch_provider', broken)
    assert ORIGINAL_TRANSLATOR(SOURCE, 'zh', 'id') is None


def test_emergency_route_does_not_repeat_an_attempted_nmt(runtime, monkeypatch):
    app._tl.nmt_attempted = True
    monkeypatch.setattr(app.offline_translation_module, 'translate', lambda *_a: None)
    monkeypatch.setattr(app.nmt_module, 'nmt_translate', lambda *_a: pytest.fail('duplicate NMT'))
    monkeypatch.setattr(app, 'translate_google', lambda *_a: pytest.fail('second NMT route'))
    assert app._emergency_translation_fallback(SOURCE, 'zh', 'id') is None


@pytest.mark.parametrize('failure_index', [0, 1, 2])
def test_one_failed_event_does_not_drop_the_rest_of_a_signed_batch(tmp_path, monkeypatch, failure_index):
    from test_webhook_latency_recovery import sign
    monkeypatch.setattr(queue, 'DB_PATH', str(tmp_path / 'batch.db'))
    monkeypatch.setattr(webhook_runtime, 'asynchronous_ingress', lambda: False)
    handler = WebhookHandler(SECRET)
    calls = []
    @handler.add(MessageEvent, message=TextMessageContent)
    def process(event, destination):
        calls.append((event.message.id, destination))
        if event.message.id == str(failure_index):
            raise TimeoutError('offline failure for one event')
    original, _ = signed(text=SOURCE)
    payload = json.loads(original)
    base = payload['events'][0]
    payload['events'] = []
    for i in range(3):
        item = copy.deepcopy(base)
        item['webhookEventId'] = 'event-' + str(i)
        item['message']['id'] = str(i)
        payload['events'].append(item)
    body = json.dumps(payload)
    inbox = webhook_runtime.WebhookInbox(handler, lambda _: pytest.fail('No release/replay'))
    key = inbox.accept(body, sign(body))
    assert [mid for mid, _ in calls] == ['0', '1', '2']
    assert all(destination == payload['destination'] for _, destination in calls)
    assert queue.get(key)['status'] == 'failed'
    inbox.accept(body, sign(body))
    assert len(calls) == 3 and queue.pending_count() == 0
