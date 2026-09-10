"""Persist authenticated LINE webhooks before ACK; process outside HTTP workers.

Keep events within a webhook in SDK order (commands, edits and conversation
context depend on that order). Separate webhooks run concurrently. Restart
recovery uses SQLite leases and the translation outbox's existing send receipts.
"""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import hashlib
import json
import time
import threading
import os
from pathlib import Path

import translation_retry_queue as queue
from durable_workers import WorkerPool

BUILD_ID = "2026-09-10.1-unfinished-webhook-recovery"
PROCESS_STARTED = time.monotonic()
_CURRENT = ContextVar("line_webhook_timing", default=None)


def persistent_outbox():
    """Whether the configured local queue survives this host's restarts.

    Render Free replaces its local filesystem on restarts/deploys. Until a real
    persistent mount backs the outbox, keep the original synchronous ACK rule.
    More HTTP threads and the isolated recovery lanes still serve that mode.
    """
    if os.environ.get("RENDER", "").lower() != "true":
        return True
    path = Path(queue.DB_PATH).resolve()
    volatile = (Path("/tmp"), Path("/dev/shm"), Path("/run"))
    if any(path.is_relative_to(parent) for parent in volatile):
        return False
    return any(parent != Path("/") and os.path.ismount(parent) for parent in path.parents)


def asynchronous_ingress():
    return (os.environ.get("LINE_WEBHOOK_ASYNC", "").lower() not in {"0", "false", "off"}
            and persistent_outbox())


class PendingWebhookWork(RuntimeError):
    """Processing returned, but its only remaining copy is on volatile disk."""


def _pending_message_work(events):
    for event in events:
        if event.get("type") not in {"message", "messageEdited"}:
            continue
        source, message = event.get("source") or {}, event.get("message") or {}
        target = source.get("groupId") or source.get("roomId") or source.get("userId")
        if queue.has_pending_source(target, message.get("id")):
            return True
    return False


@contextmanager
def timing_scope(received_at=None):
    parent = _CURRENT.get() or {}
    token = _CURRENT.set({"received_at": float(received_at if received_at is not None else
                                             parent.get("received_at", time.time())),
                          "started_at": time.time(), "ai_ms": 0, "ai_attempts": 0,
                          "lock": threading.Lock()})
    try:
        yield
    finally:
        _CURRENT.reset(token)


def note_ai_attempt(seconds):
    current = _CURRENT.get()
    if current is not None:
        with current["lock"]:
            current["ai_ms"] += round(seconds * 1000)
            current["ai_attempts"] += 1


def ai_timing():
    current = _CURRENT.get() or {}
    return current.get("ai_ms", 0), current.get("ai_attempts", 0)


def event_timing(event=None):
    now = time.time()
    current = _CURRENT.get() or {"received_at": now, "started_at": now}
    timestamp = float(getattr(event, "timestamp", 0) or 0) / 1000
    return {
        "queue_ms": max(0, round((now - current["received_at"]) * 1000)),
        "ingress_ms": max(0, round((current["received_at"] - timestamp) * 1000)) if timestamp else None,
        "event_age_ms": max(0, round((now - timestamp) * 1000)) if timestamp else None,
        "uptime_ms": round((time.monotonic() - PROCESS_STARTED) * 1000),
        "trace": hashlib.sha256(str(getattr(event, "webhook_event_id", "") or
                                    getattr(getattr(event, "message", None), "id", "") or "").encode()).hexdigest()[:12],
    }


class WebhookInbox:
    def __init__(self, handler, release_claims, *, workers=8, context=None):
        self.handler = handler
        self.release_claims = release_claims
        self.context = context
        self.pool = WorkerPool("line-webhook", self.run_job, workers=workers,
                               include_kinds=("webhook",))

    def accept(self, body, signature, *, received_at=None):
        # Verify the exact original bytes before persistence or acknowledgement.
        # The background handler verifies them again using the normal LINE SDK.
        self.handler.parser.parse(body, signature)
        payload = json.loads(body)
        events = payload.get("events") or []
        if not events:
            return None  # LINE verification request
        if not asynchronous_ingress():
            # No newly introduced ACK-before-processing window on ephemeral
            # Render disks. Existing translation outbox retries still apply.
            with self.context() if self.context else nullcontext():
                with timing_scope(received_at):
                    try:
                        self.handler.handle(body, signature)
                        # Some handlers retain empty AI output or failed LINE
                        # sends in the outbox without raising. Waiting for the
                        # handler alone therefore did NOT ensure delivery.
                        # Keep LINE redelivery eligible until these jobs finish
                        # when the local outbox can disappear on Render sleep.
                        if not persistent_outbox() and _pending_message_work(events):
                            raise PendingWebhookWork("translation delivery is still pending")
                    except Exception:
                        self.release_claims(body)
                        raise
            return None
        # Redelivery changes only deliveryContext; it must not create a second
        # queued generation. Preserve the original body/signature for dispatch.
        identity = {"destination": payload.get("destination"), "events": [
            {k: v for k, v in event.items() if k != "deliveryContext"} for event in events
        ]}
        digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False,
                                           sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        key = "webhook:" + digest
        queue.enqueue(key, {"body": body, "signature": signature,
                            "received_at": time.time() if received_at is None else received_at},
                      job_kind="webhook")
        self.pool.ensure_started()
        return key

    def run_job(self, job, owner):
        from contextlib import nullcontext
        payload = job["payload"]
        with queue.maintain_lease(job["job_key"], owner=owner) as check:
            with self.context() if self.context else nullcontext():
                with timing_scope(payload.get("received_at") or job["created_at"]):
                    try:
                        self.handler.handle(payload["body"], payload["signature"])
                    except Exception:
                        self.release_claims(payload["body"])
                        raise
            check()
            return queue.mark_delivered(job["job_key"], owner=owner)
