"""Exclusive HTTP-client leases that survive the caller's worker thread.

Only idle clients are shared. Borrowing never waits for another network call:
concurrent callers get separate clients, and at most ``max_idle`` are retained.
There is no speculative request, background thread or retry in this pool.
"""
from contextlib import contextmanager
import os
import threading
import time


BUILD_ID = "2026-09-11.1-cross-worker-connections"


class TransportPool:
    def __init__(self, *, max_idle=8, idle_seconds=120):
        self.max_idle = max(0, int(max_idle))
        self.idle_seconds = max(0.0, float(idle_seconds))
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._key = None
        self._generation = 0
        self._idle = []

    @staticmethod
    def _close(values):
        for value in values:
            try:
                close = getattr(value, "close", None)
                if close:
                    close()
            except Exception:
                # Cleanup must not mask a delivery/transport exception.
                pass

    def _after_fork(self):
        if self._pid == os.getpid():
            return
        # Never acquire a mutex copied from a thread that does not exist in the
        # child. Sockets copied from the parent cannot be borrowed in the child.
        inherited, self._idle = self._idle, []
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._key = None
        self._generation += 1
        self._close(value for _, value in inherited)

    @contextmanager
    def borrow(self, key, create):
        self._after_fork()
        retired, value = [], None
        now = time.monotonic()
        with self._lock:
            if key != self._key:
                retired.extend(item for _, item in self._idle)
                self._idle.clear()
                self._key = key
                self._generation += 1
            available = []
            for returned_at, item in self._idle:
                if now - returned_at >= self.idle_seconds:
                    retired.append(item)
                else:
                    available.append((returned_at, item))
            self._idle = available
            if self._idle:
                _, value = self._idle.pop()
            generation, pid = self._generation, self._pid
        self._close(retired)
        if value is None:
            value = create()  # Network/client initialization holds no pool lock.
        try:
            yield value
        finally:
            self._after_fork()
            with self._lock:
                retain = (pid == self._pid and generation == self._generation
                          and key == self._key and self.idle_seconds > 0
                          and len(self._idle) < self.max_idle)
                if retain:
                    self._idle.append((time.monotonic(), value))
            if not retain:
                self._close((value,))

    def close(self):
        self._after_fork()
        with self._lock:
            retired, self._idle = self._idle, []
            # In-flight owners finish normally, then dispose instead of
            # returning a client invalidated by shutdown/configuration change.
            self._generation += 1
        self._close(value for _, value in retired)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
