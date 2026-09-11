"""Bounded, expiring factory interaction state; atomic on SQLite and Upstash.

Upstash is reused when configured. No background thread, secret, or network
request is created at import time. SQLite is also useful for persistent disks
and offline tests; readiness reports distinguish it from cloud persistence.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import requests


class StoreError(RuntimeError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_IO = ContextVar("factory_storage_io", default=None)


@contextmanager
def measure_storage():
    stats = {"requests": 0, "milliseconds": 0.0, "skipped": 0, "delivered_ms": None,
             "started": time.monotonic()}
    marker = _IO.set(stats)
    try:
        yield stats
    finally:
        _IO.reset(marker)


def mark_delivery():
    stats = _IO.get()
    if stats is not None and stats["delivered_ms"] is None:
        stats["delivered_ms"] = (time.monotonic() - stats["started"]) * 1000


_SCHEDULE = """
local function schedule(key, value)
  local prefix = string.match(key, '^(.*}:)notice:')
  if not prefix then return end
  local row = value and cjson.decode(value) or {}
  local due = row.wake_at
  if type(due) ~= 'number' and type(row.stop_notification) == 'table' then
    due = row.stop_notification.next_attempt_at
  end
  if type(due) == 'number' then
    redis.call('ZADD', prefix .. 'notice-due', due, key)
  else
    redis.call('ZREM', prefix .. 'notice-due', key)
  end
end
"""

_WRITE = _SCHEDULE + """
local function write(key, bucket, value, ttl)
  redis.call('SET', key, value, 'EX', ttl)
  redis.call('ZADD', bucket, tonumber(ARGV[1]) + tonumber(ttl), key)
  redis.call('ZREMRANGEBYSCORE', bucket, '-inf', ARGV[1])
  redis.call('EXPIRE', bucket, tonumber(ttl) + 86400)
  schedule(key, value)
end
"""

_PUT = _WRITE + """
write(KEYS[1], KEYS[2], ARGV[2], ARGV[3])
return 1
"""

_REVISION = _WRITE + """
local oldraw = redis.call('GET', KEYS[1])
local incoming = cjson.decode(ARGV[2])
if oldraw then
  local old = cjson.decode(oldraw)
  if old.cancelled then return oldraw end
  if not incoming.cancelled and old.identity ~= incoming.identity then
    local older = (tonumber(incoming.timestamp) or 0) < (tonumber(old.timestamp) or 0)
    local tied = (tonumber(incoming.timestamp) or 0) == (tonumber(old.timestamp) or 0)
    if not incoming.edited or older or (tied and (incoming.order or '') <= (old.order or '')) then
      return oldraw
    end
  end
  if oldraw == ARGV[2] then return oldraw end
end
write(KEYS[1], KEYS[2], ARGV[2], ARGV[3])
return ARGV[2]
"""

_INTERACTION = _WRITE + """
local source_value = nil
if ARGV[5] == '1' then
  local raw = redis.call('GET', KEYS[3])
  local tokens = raw and cjson.decode(raw).tokens or {}
  local found = false
  for _, token in ipairs(tokens) do if token == ARGV[4] then found = true end end
  if not found then
    table.insert(tokens, ARGV[4])
    source_value = '{"tokens":' .. cjson.encode(tokens) .. '}'
  end
end
local oldnotice = ARGV[6] ~= '' and redis.call('GET', KEYS[5]) or nil
local preserve_context = oldnotice and cjson.decode(oldnotice).notice_command
if not preserve_context then write(KEYS[1], KEYS[2], ARGV[2], ARGV[3]) end
if source_value then write(KEYS[3], KEYS[4], source_value, ARGV[7]) end
if ARGV[6] ~= '' and not oldnotice then write(KEYS[5], KEYS[6], ARGV[6], ARGV[8]) end
return 1
"""


_CAS = _SCHEDULE + """
local old = redis.call('GET', KEYS[1])
if (old or '') ~= ARGV[1] then return 0 end
if ARGV[2] == '' then
  redis.call('DEL', KEYS[1]); redis.call('ZREM', KEYS[2], KEYS[1])
  schedule(KEYS[1], nil)
else
  redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
  redis.call('ZADD', KEYS[2], ARGV[4], KEYS[1])
  redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[5])
  redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]) + 86400)
  schedule(KEYS[1], ARGV[2])
end
return 1
"""

_LIST = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
local keys = redis.call('ZREVRANGE', KEYS[1], 0, tonumber(ARGV[2]) - 1)
local result = {}
for _, key in ipairs(keys) do
  local value = redis.call('GET', key)
  if value then table.insert(result, value) end
end
return result
"""


class FeatureStore:
    def __init__(self, *, path=None, url=None, token=None, namespace="line-factory-v1", command=None):
        self.path = str(path) if path else None
        self.url, self.token = (url or "").rstrip("/"), token or ""
        self.kind = "sqlite" if self.path else "upstash"
        self.prefix = "factory:{" + hashlib.sha256(namespace.encode()).hexdigest()[:20] + "}:"
        self._override = command
        self._http = threading.local()
        self._health_lock = threading.Lock()
        self._unavailable_until = 0.0
        if not self.path and not command and (not self.url.startswith("https://") or not self.token):
            raise StoreError("工廠工具儲存設定不完整。")

    def _connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=8)
        db.execute("PRAGMA busy_timeout=8000")
        db.execute("CREATE TABLE IF NOT EXISTS factory_state "
                   "(key TEXT PRIMARY KEY, bucket TEXT NOT NULL, value TEXT NOT NULL, expires REAL NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS factory_expiry ON factory_state(bucket, expires)")
        db.execute("CREATE INDEX IF NOT EXISTS factory_notice_due ON factory_state "
                   "(json_extract(value,'$.wake_at')) WHERE key LIKE 'notice:%'")
        db.execute("CREATE INDEX IF NOT EXISTS factory_notice_delivery_due ON factory_state "
                   "(COALESCE(json_extract(value,'$.wake_at'), "
                   "json_extract(value,'$.stop_notification.next_attempt_at'))) WHERE key LIKE 'notice:%'")
        return db

    def command(self, args):
        stats = _IO.get()
        with self._health_lock:
            unavailable = time.monotonic() < self._unavailable_until
        if unavailable:
            if stats is not None:
                stats["skipped"] += 1
            raise StoreError("工廠工具儲存正在恢復連線，操作尚未確認成功。")
        started = time.monotonic()
        if stats is not None:
            stats["requests"] += 1
        try:
            if self._override:
                return self._override(args)
            # Per-thread sessions reuse TLS/HTTP connections without sharing
            # mutable session state across Flask worker threads. No SDK retries.
            session = getattr(self._http, "session", None)
            if session is None:
                session = self._http.session = requests.Session()
            with session.post(self.url, data=encode(args).encode(), stream=True,
                              allow_redirects=False, timeout=(2, 3), headers={
                                  "Authorization": "Bearer " + self.token,
                                  "Content-Type": "application/json"}) as response:
                if response.status_code != 200:
                    raise ValueError("invalid storage status")
                raw = response.raw.read(2_000_001, decode_content=True)
                if len(raw) > 2_000_000:
                    raise ValueError("storage response too large")
                result = json.loads(raw)
            if "error" in result or "result" not in result:
                raise ValueError("invalid storage response")
            return result["result"]
        except Exception as exc:
            # One outage must not cost another full timeout at every button /
            # revision stage in this request and every subsequent message.
            with self._health_lock:
                self._unavailable_until = time.monotonic() + 15
            raise StoreError("工廠工具儲存暫時無法連線，操作尚未確認成功。") from exc
        finally:
            if stats is not None:
                stats["milliseconds"] += (time.monotonic() - started) * 1000

    @staticmethod
    def _bucket(key):
        # Notice history is isolated per group; other keys share a type index.
        return ":".join(key.split(":")[:2]) if key.startswith("notice:") else key.split(":")[0]

    def _raw(self, key):
        if self.path:
            db = self._connect()
            try:
                row = db.execute("SELECT value FROM factory_state WHERE key=? AND expires>?",
                                 (key, time.time())).fetchone()
                return row[0] if row else None
            finally:
                db.close()
        return self.command(["GET", self.prefix + key])

    def get(self, key):
        raw = self._raw(key)
        return json.loads(raw) if raw is not None else None

    def compare_swap(self, key, previous, current, ttl):
        before = encode(previous) if previous is not None else ""
        after = encode(current) if current is not None else ""
        ttl = max(1, int(ttl))
        now = time.time()
        if not self.path:
            return self.command(["EVAL", _CAS, 2, self.prefix + key,
                                 self.prefix + "index:" + self._bucket(key), before, after,
                                 ttl, now + ttl, now]) == 1
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM factory_state WHERE key=? AND expires>?", (key, now)).fetchone()
            if (row[0] if row else "") != before:
                db.rollback()
                return False
            if current is None:
                db.execute("DELETE FROM factory_state WHERE key=?", (key,))
            else:
                db.execute("INSERT INTO factory_state VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE "
                           "SET value=excluded.value,expires=excluded.expires,bucket=excluded.bucket",
                           (key, self._bucket(key), after, now + ttl))
            db.execute("DELETE FROM factory_state WHERE key IN "
                       "(SELECT key FROM factory_state WHERE expires<=? LIMIT 100)", (now,))
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def update(self, key, change, ttl=604800):
        for _ in range(8):
            previous = self.get(key)
            current = change(copy.deepcopy(previous))
            if current == previous:
                return current
            if self.compare_swap(key, previous, current, ttl):
                return current
        raise StoreError("同時有其他操作更新資料，請重新整理後再試。")

    def put(self, key, value, ttl=604800):
        if value is None:
            return self.delete(key)
        if not self.path:
            self.command(["EVAL", _PUT, 2, self.prefix + key,
                          self.prefix + "index:" + self._bucket(key), time.time(), encode(value), max(1, int(ttl))])
            return value
        return self.update(key, lambda _: value, ttl)

    def merge_revision(self, key, incoming, ttl=2592000):
        """Atomically compare source versions in one cloud round trip."""
        if not self.path:
            raw = self.command(["EVAL", _REVISION, 2, self.prefix + key,
                                self.prefix + "index:" + self._bucket(key),
                                time.time(), encode(incoming), max(1, int(ttl))])
            return json.loads(raw)
        def advance(old):
            if old:
                if old.get("cancelled"):
                    return old
                if not incoming.get("cancelled") and old.get("identity") != incoming.get("identity"):
                    if not incoming.get("edited") or (
                            incoming.get("timestamp", 0), incoming.get("order", "")) <= (
                            old.get("timestamp", 0), old.get("order", "")):
                        return old
            return incoming
        return self.update(key, advance, ttl)

    def save_interaction(self, record, ttl, *, source_key=None, notice=None):
        """Persist button context, source index and initial receipt together.

        Existing acknowledgements are never replaced when a delivery retries.
        JSON records retain canonical encoding for the older CAS update API.
        """
        token = record["token"]
        context_key = "context:" + token
        notice_key = "notice:" + str(record.get("group_id", "")) + ":" + token
        ttl = max(1, int(ttl))
        if not self.path:
            keys = []
            for key in (context_key, source_key or "unused-source", notice_key):
                keys.extend([self.prefix + key, self.prefix + "index:" + self._bucket(key)])
            self.command(["EVAL", _INTERACTION, 6, *keys, time.time(), encode(record), ttl, token,
                          "1" if source_key else "0", encode(notice) if notice else "", 2592000, 604800])
            return record
        now = time.time()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            def read(key):
                row = db.execute("SELECT value FROM factory_state WHERE key=? AND expires>?", (key, now)).fetchone()
                return json.loads(row[0]) if row else None
            def write(key, value, duration):
                db.execute("INSERT INTO factory_state VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE "
                           "SET value=excluded.value,expires=excluded.expires,bucket=excluded.bucket",
                           (key, self._bucket(key), encode(value), now + duration))
            old_notice = read(notice_key) if notice else None
            # A concurrent replay may still hold the original untranslated
            # draft after another worker committed and delivered the notice.
            # Explicit context checkpoints use notice=None and remain writable.
            if not (old_notice and old_notice.get("notice_command")):
                write(context_key, record, ttl)
            if source_key:
                tokens = (read(source_key) or {}).get("tokens", [])
                if token not in tokens:
                    write(source_key, {"tokens": tokens + [token]}, 2592000)
            if notice and old_notice is None:
                write(notice_key, notice, 604800)
            db.execute("DELETE FROM factory_state WHERE key IN "
                       "(SELECT key FROM factory_state WHERE expires<=? LIMIT 100)", (now,))
            db.commit()
            return record
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def delete(self, key):
        return self.update(key, lambda _: None)

    def recent(self, bucket, limit=50):
        limit = max(1, min(200, int(limit)))
        if not self.path:
            return [json.loads(value) for value in self.command(
                ["EVAL", _LIST, 1, self.prefix + "index:" + bucket, time.time(), limit])]
        db = self._connect()
        try:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT value FROM factory_state WHERE bucket=? AND expires>? ORDER BY expires DESC LIMIT ?",
                (bucket, time.time(), limit)).fetchall()]
        finally:
            db.close()

    def notice_page(self, cursor="", limit=40):
        """Visit active stored notices, including rows absent from the due index.

        An empty cursor starts/finishes a pass. Redis SCAN may return an empty
        page with a nonzero cursor or duplicate keys; callers must tolerate both.
        This intentionally does not use the 200-row per-group history limit.
        """
        limit = max(1, min(100, int(limit)))
        if not self.path:
            next_cursor, keys = self.command(
                ["SCAN", cursor or "0", "MATCH", self.prefix + "notice:*", "COUNT", limit])
            values = self.command(["MGET", *keys]) if keys else []
            return ([json.loads(value) for value in values if value],
                    "" if str(next_cursor) == "0" else str(next_cursor))
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT key,value FROM factory_state WHERE key LIKE 'notice:%' "
                "AND key>? AND expires>? ORDER BY key LIMIT ?", (cursor, time.time(), limit)).fetchall()
            return [json.loads(row[1]) for row in rows], rows[-1][0] if len(rows) == limit else ""
        finally:
            db.close()

    def due_notices(self, now, limit=10):
        """Global due index; independent of per-group history display limits."""
        limit = max(1, min(100, int(limit)))
        if not self.path:
            script = """
local keys = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2])
local result = {}
for _, key in ipairs(keys) do
  local raw = redis.call('GET', key)
  if raw then table.insert(result, raw) else redis.call('ZREM', KEYS[1], key) end
end
return result
"""
            return [json.loads(raw) for raw in self.command(
                ['EVAL', script, 1, self.prefix + 'notice-due', now, limit])]
        db = self._connect()
        try:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT value FROM factory_state WHERE key LIKE 'notice:%' AND expires>? "
                "AND COALESCE(json_extract(value,'$.wake_at'), "
                "json_extract(value,'$.stop_notification.next_attempt_at'))<=? "
                "ORDER BY COALESCE(json_extract(value,'$.wake_at'), "
                "json_extract(value,'$.stop_notification.next_attempt_at')) LIMIT ?",
                (now, now, limit))]
        finally:
            db.close()


def configured_store():
    mode = os.environ.get("LINE_FACTORY_STORE", "auto").lower()
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip()
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()
    namespace = os.environ.get("LINE_FACTORY_KEY_PREFIX", "line-factory-v1")
    if mode != "sqlite" and (url or token):
        return FeatureStore(url=url, token=token, namespace=namespace)
    path = os.environ.get("LINE_FACTORY_DB_PATH", "").strip()
    if not path:
        parent = Path(os.environ.get("TRANSLATION_RETRY_DB_PATH", "/tmp/translation_retry.db")).parent
        path = str(parent / "line_factory.db")
    return FeatureStore(path=path, namespace=namespace)
