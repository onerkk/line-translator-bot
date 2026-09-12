"""Bounded workers claim SQLite jobs only when a worker is available.

No in-memory backlog or speculative model calls. Text, media and webhook
ingress have separate capacity; the existing durable outbox owns deliveries.
"""
import logging
import os
import threading
import uuid
import weakref

import translation_retry_queue as queue

logger = logging.getLogger("app")
_POOLS = weakref.WeakSet()


def _after_fork():
    for pool in list(_POOLS):
        pool._reset_process()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


class WorkerPool:
    def __init__(self, name, run_job, *, workers, include_kinds=None,
                 exclude_kinds=(), backoff=None):
        self.name, self.run_job = name, run_job
        self.workers = max(1, min(int(workers), 12))
        self.filters = dict(include_kinds=include_kinds, exclude_kinds=exclude_kinds)
        self.backoff = backoff or (lambda attempt: min(60, 2 ** min(attempt, 6)))
        self._reset_process()
        _POOLS.add(self)

    def _reset_process(self):
        # Only durable leases cross a process boundary. Inherited threads and
        # their condition lock cannot serve or wake jobs in the child.
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.threads = set()
        self.stopping = False

    def ensure_started(self):
        with self.condition:
            if self.stopping:
                return False
            self.condition.notify_all()
            started = False
            try:
                if queue.next_ready_delay(**self.filters) is None:
                    return False
                slots = min(self.workers, queue.pending_count(**self.filters))
            except Exception:
                # The intent may already be durable. A transient inspection
                # failure after enqueue must not leave it without any worker
                # until the next webhook/restart. The normal worker loop has
                # bounded waits and recovers its own database reads.
                logger.exception("[DurableWorker] lane=%s start inspection failed; scheduling recovery", self.name)
                slots = 1
            while len(self.threads) < slots:
                thread = threading.Thread(target=self._work, name=self.name, daemon=True)
                self.threads.add(thread)
                try:
                    thread.start()
                except BaseException:
                    self.threads.discard(thread)
                    raise
                started = True
            return started

    def _work(self):
        owner = f"{os.getpid()}-{self.name}-{uuid.uuid4().hex}"
        try:
            while not self.stopping:
                # One slot leases one job, so queued jobs cannot expire while
                # waiting behind an unrelated model or media request.
                try:
                    jobs = queue.claim_due_jobs(owner=owner, limit=1, lease_seconds=240, **self.filters)
                except Exception:
                    logger.exception("[DurableWorker] lane=%s queue temporarily unavailable", self.name)
                    with self.condition:
                        self.condition.wait(5.0)
                    continue
                if jobs:
                    job = jobs[0]
                    try:
                        # Each runner maintains its own live lease through send.
                        if self.run_job(job, owner):
                            continue
                        reason = "work_pending"
                    except queue.LeaseLostError:
                        continue  # the replacement owner controls retry/delivery
                    except Exception as exc:
                        reason = type(exc).__name__
                        logger.exception("[DurableWorker] lane=%s attempt=%s failed",
                                         self.name, int(job.get("attempts") or 0) + 1)
                    try:
                        queue.reschedule(job["job_key"], owner=owner,
                                         delay_seconds=self.backoff(int(job.get("attempts") or 0) + 1),
                                         error=reason)
                    except Exception:
                        # Its lease will expire; keep workers alive for recovery.
                        logger.exception("[DurableWorker] lane=%s cannot reschedule yet", self.name)
                        with self.condition:
                            self.condition.wait(5.0)
                    continue
                with self.condition:
                    try:
                        delay = queue.next_ready_delay(**self.filters)
                    except Exception:
                        logger.exception("[DurableWorker] lane=%s cannot inspect queue yet", self.name)
                        self.condition.wait(5.0)
                        continue
                    if delay is None:
                        # Remove atomically with the idle check. An enqueue
                        # racing this exit will start a replacement worker.
                        self.threads.discard(threading.current_thread())
                        return
                    self.condition.wait(max(0.05, min(5.0, delay)))
        except Exception:
            logger.exception("[DurableWorker] lane=%s queue unavailable", self.name)
        finally:
            with self.condition:
                self.threads.discard(threading.current_thread())

    def wake(self):
        with self.condition:
            self.condition.notify_all()

    def stop(self, timeout=5.0):
        """Stop idle workers; running work retains its lease until it finishes."""
        import time
        with self.condition:
            self.stopping = True
            threads = list(self.threads)
            self.condition.notify_all()
        deadline = time.monotonic() + timeout
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
