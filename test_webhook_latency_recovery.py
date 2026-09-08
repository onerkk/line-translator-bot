"""Real SQLite/SDK/Flask boundaries with blocked fake work, no external traffic."""
import base64
import hashlib
import hmac
import json
import threading
import time
from types import SimpleNamespace

import pytest
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError

import app
import translation_retry_queue as queue
import webhook_runtime as runtime
from durable_workers import WorkerPool

SECRET = "local-test-secret"


def signed(message_id="1001", text="量完再生產", redelivery=False):
    body = json.dumps({"destination": "Ubot", "events": [{
        "type": "message", "mode": "active", "timestamp": int(time.time() * 1000),
        "webhookEventId": "event-" + message_id,
        "deliveryContext": {"isRedelivery": redelivery},
        "replyToken": "reply-" + message_id,
        "source": {"type": "group", "groupId": "Ctest", "userId": "Utest"},
        "message": {"type": "text", "id": message_id, "text": text, "quoteToken": "quote-" + message_id},
    }]}, ensure_ascii=False)
    return body, sign(body)


def sign(body):
    return base64.b64encode(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest()).decode()


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "queue.db"))
    return tmp_path


def wait_until(condition, timeout=3):
    deadline = time.monotonic() + timeout
    wake = threading.Event()
    while time.monotonic() < deadline:
        if condition():
            return
        wake.wait(0.01)
    assert condition()


def test_callback_ack_and_next_webhook_do_not_wait_for_slow_translation(state, monkeypatch):
    started, release, second = threading.Event(), threading.Event(), threading.Event()
    handler = WebhookHandler(SECRET)
    @handler.add(MessageEvent, message=TextMessageContent)
    def handle(event):
        if event.message.id == "1001":
            started.set()
            assert release.wait(3)
        else:
            second.set()
    inbox = runtime.WebhookInbox(handler, lambda body: None, workers=2)
    monkeypatch.setattr(app, "_WEBHOOK_INBOX", inbox)
    try:
        body, sig = signed()
        start = time.monotonic()
        response = app.app.test_client().post("/callback", data=body, headers={"X-Line-Signature": sig})
        assert response.status_code == 200 and time.monotonic() - start < 1
        assert started.wait(1) and not release.is_set()
        assert any(row["job_kind"] == "webhook" for row in queue.list_pending())
        body, sig = signed("1002")
        response = app.app.test_client().post("/callback", data=body, headers={"X-Line-Signature": sig})
        assert response.status_code == 200 and second.wait(1)
    finally:
        release.set()
        wait_until(lambda: queue.pending_count() == 0)
        inbox.pool.stop()


def test_invalid_signature_and_failed_persistence_are_never_acked(state, monkeypatch):
    inbox = runtime.WebhookInbox(WebhookHandler(SECRET), lambda body: None)
    monkeypatch.setattr(app, "_WEBHOOK_INBOX", inbox)
    body, sig = signed()
    response = app.app.test_client().post("/callback", data=body, headers={"X-Line-Signature": "invalid"})
    assert response.status_code == 400 and queue.pending_count() == 0
    with pytest.raises(InvalidSignatureError):
        inbox.accept(body.replace("量完", "未量"), sig)
    def full(*args, **kwargs):
        raise OSError("disk unavailable")
    monkeypatch.setattr(queue, "enqueue", full)
    monkeypatch.setitem(app.app.config, "PROPAGATE_EXCEPTIONS", False)
    response = app.app.test_client().post("/callback", data=body, headers={"X-Line-Signature": sig})
    assert response.status_code == 500


def test_redelivery_reuses_job_and_restart_replays_saved_original_in_order(state, monkeypatch):
    calls = []
    handler = WebhookHandler(SECRET)
    @handler.add(MessageEvent, message=TextMessageContent)
    def handle(event):
        calls.append((event.message.id, event.message.text))
    first = runtime.WebhookInbox(handler, lambda body: None)
    monkeypatch.setattr(first.pool, "ensure_started", lambda: None)
    body, sig = signed()
    payload = json.loads(body)
    payload["events"].append(json.loads(signed("1002", "報表在哪裡")[0])["events"][0])
    body = json.dumps(payload, ensure_ascii=False)
    key = first.accept(body, sign(body))
    payload["events"][0]["deliveryContext"]["isRedelivery"] = True
    redelivered = json.dumps(payload, ensure_ascii=False)
    assert first.accept(redelivered, sign(redelivered)) == key
    assert queue.pending_count() == 1 and calls == []
    # A new worker/inbox instance reads the persisted original, not request globals.
    restarted = runtime.WebhookInbox(handler, lambda body: None, workers=2)
    try:
        restarted.pool.ensure_started()
        wait_until(lambda: queue.was_delivered(key))
        assert calls == [("1001", "量完再生產"), ("1002", "報表在哪裡")]
        restarted.accept(redelivered, sign(redelivered))
        assert queue.pending_count() == 0 and len(calls) == 2
    finally:
        restarted.pool.stop()


def test_failed_dispatch_releases_claims_and_can_be_recovered(state, monkeypatch):
    calls, released = [], []
    handler = WebhookHandler(SECRET)
    @handler.add(MessageEvent, message=TextMessageContent)
    def handle(event):
        calls.append(event.message.id)
        if len(calls) == 1:
            raise TimeoutError("simulated interrupted handler")
    inbox = runtime.WebhookInbox(handler, released.append)
    monkeypatch.setattr(inbox.pool, "ensure_started", lambda: None)
    body, sig = signed()
    key = inbox.accept(body, sig)
    assert queue.claim_job(key, owner="first")
    with pytest.raises(TimeoutError):
        inbox.run_job(queue.get(key), "first")
    assert released == [body] and not queue.was_delivered(key)
    queue.reschedule(key, owner="first", delay_seconds=0)
    assert queue.claim_job(key, owner="second")
    assert inbox.run_job(queue.get(key), "second") and queue.was_delivered(key)


def test_media_wait_cannot_block_text_recovery_and_busy_slots_do_not_preclaim(state):
    media_started, text_done, release = threading.Event(), threading.Event(), threading.Event()
    def process(job, owner):
        with queue.maintain_lease(job["job_key"], owner=owner):
            if job["job_kind"] == "image":
                media_started.set()
                assert release.wait(3)
            else:
                text_done.set()
            return queue.mark_delivered(job["job_key"], owner=owner)
    media = WorkerPool("test-media", process, workers=1, include_kinds=("image",))
    text = WorkerPool("test-text", process, workers=2, include_kinds=("text",))
    queue.enqueue("image1", {}, job_kind="image")
    queue.enqueue("image2", {}, job_kind="image")
    queue.enqueue("text1", {}, job_kind="text")
    try:
        media.ensure_started()
        assert media_started.wait(1)
        assert queue.get("image2")["status"] == "pending"
        text.ensure_started()
        assert text_done.wait(1) and not release.is_set()
        wait_until(lambda: queue.was_delivered("text1"))
    finally:
        release.set()
        wait_until(lambda: queue.pending_count() == 0)
        media.stop()
        text.stop()


def test_lane_filter_covers_expired_leases_and_does_not_steal_other_lanes(state):
    for kind in ("text", "image", "webhook"):
        queue.enqueue(kind, {}, job_kind=kind)
        assert queue.claim_job(kind, owner="crashed")
    future = time.time() + 300
    assert [j["job_key"] for j in queue.claim_due_jobs(owner="text", now=future, include_kinds=("text",))] == ["text"]
    assert [j["job_key"] for j in queue.claim_due_jobs(owner="media", now=future, exclude_kinds=("text", "webhook"))] == ["image"]
    assert queue.get("webhook")["lease_owner"] == "crashed"
    assert queue.claim_due_jobs(now=future, include_kinds=[]) == []


def test_ingress_queue_ai_and_delivery_timing_are_separate_and_do_not_log_content(state, monkeypatch, caplog):
    import logging
    import line_factory_features as features
    event = SimpleNamespace(timestamp=100000, webhook_event_id="private-event-id")
    clock = [165.0]
    monkeypatch.setattr(runtime.time, "time", lambda: clock[0])
    with runtime.timing_scope(160.0):
        @features.measure_delivery("text")
        def send(event):
            runtime.note_ai_attempt(0.25)
            runtime.note_ai_attempt(0.5)
        with caplog.at_level(logging.INFO, logger="app"):
            send(event)
    record = next(r.message for r in caplog.records if "[DeliveryPerf]" in r.message)
    assert "ingress_ms=60000" in record and "queue_ms=5000" in record
    assert "ai_ms=750" in record and "ai_attempts=2" in record
    assert "private-event-id" not in record and "量完" not in record
    assert runtime.ai_timing() == (0, 0)


@pytest.mark.parametrize("path,mounted,expected", [
    ("/tmp/jobs.db", "/tmp", False),
    ("/var/data/jobs.db", "/var/data", True),
    ("/app/jobs.db", "/", False),
])
def test_render_requires_an_actual_persistent_outbox_mount(monkeypatch, path, mounted, expected):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("LINE_WEBHOOK_ASYNC", raising=False)
    monkeypatch.setattr(queue, "DB_PATH", path)
    monkeypatch.setattr(runtime.os.path, "ismount", lambda p: str(p) == mounted)
    assert runtime.asynchronous_ingress() is expected


def test_render_free_does_not_ack_a_volatile_background_job(state, monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    handler = WebhookHandler(SECRET)
    completed = []
    @handler.add(MessageEvent, message=TextMessageContent)
    def handle(event):
        completed.append(event.message.id)
    inbox = runtime.WebhookInbox(handler, lambda body: None)
    monkeypatch.setattr(app, "_WEBHOOK_INBOX", inbox)
    body, sig = signed()
    result = app.app.test_client().post("/callback", data=body, headers={"X-Line-Signature": sig})
    assert result.status_code == 200 and completed == ["1001"]
    assert queue.pending_count() == 0 and inbox.pool.threads == set()
