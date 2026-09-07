"""Bounded, expiring factory interaction state; atomic on SQLite and Upstash.

Upstash is reused when configured. No background thread, secret, or network
request is created at import time. SQLite is also useful for persistent disks
and offline tests; readiness reports distinguish it from cloud persistence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.request


class StoreError(RuntimeError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_CAS = """
local old = redis.call('GET', KEYS[1])
if (old or '') ~= ARGV[1] then return 0 end
if ARGV[2] == '' then
  redis.call('DEL', KEYS[1]); redis.call('ZREM', KEYS[2], KEYS[1])
else
  redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
  redis.call('ZADD', KEYS[2], ARGV[4], KEYS[1])
  redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[5])
  redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]) + 86400)
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
        if not self.path and not command and (not self.url.startswith("https://") or not self.token):
            raise StoreError("工廠工具儲存設定不完整。")

    def _connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=8)
        db.execute("PRAGMA busy_timeout=8000")
        db.execute("CREATE TABLE IF NOT EXISTS factory_state "
                   "(key TEXT PRIMARY KEY, bucket TEXT NOT NULL, value TEXT NOT NULL, expires REAL NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS factory_expiry ON factory_state(bucket, expires)")
        return db

    def command(self, args):
        if self._override:
            return self._override(args)
        req = urllib.request.Request(self.url, data=encode(args).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + self.token,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=6) as response:
                result = json.loads(response.read(2_000_000))
            if "error" in result or "result" not in result:
                raise ValueError("invalid storage response")
            return result["result"]
        except Exception as exc:
            raise StoreError("工廠工具儲存暫時無法連線，操作尚未確認成功。") from exc

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
        return self.update(key, lambda _: value, ttl)

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
