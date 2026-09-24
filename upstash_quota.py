"""Process-local quota protection shared by the bot's Upstash clients.

No automatic replay of commands: callers retain their existing CAS/retry rules.
A depleted database admits one recovery probe per cooldown, across all threads
and clients using that endpoint. No Redis command is needed to maintain the gate.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request

QUOTA_COOLDOWN_SECONDS = 15 * 60


class QuotaExceeded(RuntimeError):
    code = "upstash_monthly_quota"

    def __init__(self, *, limit=None, usage=None, retry_after=QUOTA_COOLDOWN_SECONDS):
        self.limit, self.usage = limit, usage
        self.retry_after = max(1, math.ceil(retry_after))
        counts = f"（已用 {usage:,}／上限 {limit:,}）" if limit is not None and usage is not None else ""
        super().__init__("Upstash 本月請求額度已用完" + counts +
                         "。雲端資料暫時無法讀寫，提醒無法儲存或派送。"
                         "請在 Upstash 升級原資料庫方案，或等待額度重設。系統已降低重試頻率。")


class RedisCommandError(RuntimeError):
    def __init__(self, detail):
        self.detail = detail
        # Consumers which log exceptions must not log raw server bodies.
        super().__init__("Upstash rejected the command or returned an invalid response")


def check_quota_error(value):
    """Recognize the monthly quota error, never infer quota from HTTP 400 alone."""
    if not isinstance(value, str) or not re.search(r"\bmax requests limit exceeded\b", value, re.I):
        return
    def count(label):
        match = re.search(r"\b" + label + r":\s*([0-9]{1,16})\b", value, re.I)
        return int(match[1]) if match else None
    raise QuotaExceeded(limit=count("Limit"), usage=count("Usage"))


class _Gate:
    def __init__(self):
        self.pid = os.getpid()
        self.lock = threading.Lock()
        self.failure = None
        self.until = 0.0
        self.probing = False

    @contextmanager
    def request(self):
        if self.pid != os.getpid():
            self.__init__()  # Do not inherit a mutex held by another thread at fork.
        with self.lock:
            failure = self.failure
            probe = failure is not None
            if probe:
                remaining = self.until - time.monotonic()
                if remaining > 0 or self.probing:
                    raise QuotaExceeded(limit=self.failure.limit, usage=self.failure.usage,
                                        retry_after=max(1, remaining))
                self.probing = True
        try:
            yield
        except QuotaExceeded as exc:
            with self.lock:
                self.failure = exc
                self.until = time.monotonic() + QUOTA_COOLDOWN_SECONDS
            raise
        except Exception:
            # A failed recovery probe (e.g. a network timeout) is not recovery.
            if probe:
                with self.lock:
                    self.until = time.monotonic() + QUOTA_COOLDOWN_SECONDS
            raise
        else:
            if probe:
                with self.lock:
                    if self.failure is failure:
                        self.failure = None
                        self.until = 0.0
        finally:
            if probe:
                with self.lock:
                    self.probing = False


_gates = {}
_gate_lock = threading.Lock()
_pid = os.getpid()


def quota_guard(url, token):
    global _pid, _gates, _gate_lock
    if _pid != os.getpid():
        _pid, _gates, _gate_lock = os.getpid(), {}, threading.Lock()
    # Credentials never appear in cache keys, exceptions or diagnostics.
    key = hashlib.sha256((url.strip().rstrip("/") + "\0" + token).encode()).digest()
    with _gate_lock:
        gate = _gates.setdefault(key, _Gate())
    return gate.request()


def redis_command(url, token, args, *, timeout=8):
    """One stdlib REST request, with quota gating and strict response handling."""
    with quota_guard(url, token):
        request = urllib.request.Request(
            url, data=json.dumps(args, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            method="POST", headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # Save the server reason for the existing safe UI formatter, since
            # HTTP error bodies are streams and can only be consumed once.
            try:
                payload = json.loads(exc.read(4096).decode("utf-8", errors="replace"))
                exc.upstash_error = payload.get("error", "") if isinstance(payload, dict) else ""
            except Exception:
                exc.upstash_error = ""
            check_quota_error(exc.upstash_error)
            raise
        if not isinstance(payload, dict):
            raise RedisCommandError("Unexpected Redis response")
        if "error" in payload:
            check_quota_error(payload["error"])
            raise RedisCommandError(payload["error"] if isinstance(payload["error"], str) else "Invalid Redis response")
        if "result" not in payload:
            raise RedisCommandError("Missing Redis result")
        return payload["result"]
