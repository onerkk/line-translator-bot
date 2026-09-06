"""One-time, durable LINE reminders. No translation/model calls.

Upstash is the source of truth when configured; otherwise an explicitly chosen
SQLite file is required. There is deliberately no ephemeral fallback on errors.
Both stores use compare-and-swap for edit, cancel, claim and acknowledgement.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from line_translation_delivery import utf16_units

TAIPEI = ZoneInfo("Asia/Taipei")
ACTIVE = {"pending", "retrying", "sending"}
LEASE_SECONDS = 120
# LINE retry keys expire after 24 h. Stop before expiry if delivery is uncertain.
RETRY_WINDOW = 23 * 3600
MAX_CONTENT_UNITS = 1500
GROUP_ID = re.compile(r"[CR][0-9a-fA-F]{32}\Z")
USER_ID = re.compile(r"U[0-9a-fA-F]{32}\Z")


class ReminderError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class StoreUnavailable(ReminderError):
    def __init__(self, message="提醒儲存服務暫時無法使用，請稍後重新整理確認。"):
        super().__init__(message, 503)


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _score(record):
    if record["status"] == "sending":
        return record["lease_until"]
    if record["status"] in ACTIVE:
        return record["next_attempt_at"]
    return None


class SQLiteReminderStore:
    kind = "sqlite"

    def __init__(self, path):
        self.path = str(Path(path).expanduser().resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS reminders "
                         "(id TEXT PRIMARY KEY, body TEXT NOT NULL, due REAL, updated REAL NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS reminders_due ON reminders(due)")

    def _connect(self):
        class Connection(sqlite3.Connection):
            def __exit__(self, *args):
                try:
                    return super().__exit__(*args)
                finally:
                    self.close()
        conn = sqlite3.connect(self.path, timeout=5, factory=Connection)
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def get(self, key):
        with self._connect() as conn:
            row = conn.execute("SELECT body FROM reminders WHERE id=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, offset=0, limit=100):
        with self._connect() as conn:
            rows = conn.execute("SELECT body FROM reminders ORDER BY updated DESC,id DESC LIMIT ? OFFSET ?",
                                (limit, offset)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def due(self, now, limit=10):
        with self._connect() as conn:
            rows = conn.execute("SELECT body FROM reminders WHERE due<=? ORDER BY due,id LIMIT ?",
                                (now, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def compare_swap(self, previous, current):
        with self._connect() as conn:
            if previous is None:
                result = conn.execute("INSERT OR IGNORE INTO reminders(id,body,due,updated) VALUES(?,?,?,?)",
                                      (current["id"], _encode(current), _score(current), current["updated_at"]))
            else:
                result = conn.execute("UPDATE reminders SET body=?,due=?,updated=? WHERE id=? AND body=?",
                                      (_encode(current), _score(current), current["updated_at"],
                                       current["id"], _encode(previous)))
            return result.rowcount == 1


# KEYS share a Redis hash tag, so all updates remain in a single slot.
_CAS_LUA = """
local old = redis.call('HGET', KEYS[1], ARGV[1])
if ARGV[2] == '' then
    if old then return 0 end
else
    if not old or old ~= ARGV[2] then return 0 end
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[3])
if ARGV[4] == '' then
    redis.call('ZREM', KEYS[2], ARGV[1])
else
    redis.call('ZADD', KEYS[2], ARGV[4], ARGV[1])
end
redis.call('ZADD', KEYS[3], ARGV[5], ARGV[1])
return 1
"""
_READ_LUA = """
local ids
if ARGV[1] == 'due' then
    ids = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[2], 'LIMIT', 0, ARGV[3])
else
    ids = redis.call('ZREVRANGE', KEYS[3], ARGV[2], ARGV[3])
end
local rows = {}
for _, id in ipairs(ids) do
    local row = redis.call('HGET', KEYS[1], id)
    if row then table.insert(rows, row) end
end
return rows
"""


class RedisReminderStore:
    kind = "upstash"

    def __init__(self, url, token, namespace="linebot-reminders-v1"):
        if not url.startswith("https://") or not token:
            raise StoreUnavailable("請完整設定 Upstash HTTPS 網址與存取權杖。")
        self.url, self.token = url.rstrip("/"), token
        tag = hashlib.sha256(namespace.encode()).hexdigest()[:24]
        self.keys = ["reminders:{" + tag + "}:" + suffix for suffix in ("jobs", "due", "history")]

    def _command(self, args):
        req = urllib.request.Request(self.url, data=_encode(args).encode("utf-8"), method="POST",
                                     headers={"Authorization": "Bearer " + self.token,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                payload = json.loads(response.read())
            if "error" in payload or "result" not in payload:
                raise ValueError("Redis command failed")
            return payload["result"]
        except Exception as exc:
            # Do not expose a connection URL, token, or exception response body.
            raise StoreUnavailable() from exc

    def get(self, key):
        value = self._command(["HGET", self.keys[0], key])
        return json.loads(value) if value is not None else None

    def list(self, offset=0, limit=100):
        values = self._command(["EVAL", _READ_LUA, 3, *self.keys, "list", offset, offset + limit - 1])
        return [json.loads(value) for value in values]

    def due(self, now, limit=10):
        values = self._command(["EVAL", _READ_LUA, 3, *self.keys, "due", now, limit])
        return [json.loads(value) for value in values]

    def compare_swap(self, previous, current):
        score = _score(current)
        return self._command(["EVAL", _CAS_LUA, 3, *self.keys, current["id"],
                              _encode(previous) if previous is not None else "", _encode(current),
                              "" if score is None else score, current["updated_at"]]) == 1


def configured_store():
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip()
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()
    if url or token:
        return RedisReminderStore(url, token, os.environ.get("REMINDERS_KEY_PREFIX", "linebot-reminders-v1"))
    path = os.environ.get("REMINDERS_DB_PATH", "").strip()
    if path:
        return SQLiteReminderStore(path)
    raise StoreUnavailable("尚未設定提醒儲存：請設定 Upstash，或將 REMINDERS_DB_PATH 指向持久磁碟。")


def validate_spec(data, groups, now):
    if not isinstance(data, dict):
        raise ReminderError("請提供提醒資料。")
    gid = data.get("group_id")
    if not isinstance(gid, str) or not GROUP_ID.fullmatch(gid) or gid not in groups:
        raise ReminderError("請選擇機器人已加入的群組。")
    local_time = data.get("local_time")
    if not isinstance(local_time, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", local_time):
        raise ReminderError("請輸入完整日期與時間。")
    try:
        dt = datetime.strptime(local_time, "%Y-%m-%dT%H:%M").replace(tzinfo=TAIPEI)
        due_at = dt.timestamp()
    except (ValueError, OverflowError) as exc:
        raise ReminderError("日期或時間無效。") from exc
    if dt.year > 2099 or due_at <= now:
        raise ReminderError("提醒時間必須晚於現在，且年份不得超過 2099 年（台灣時間）。")
    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ReminderError("請輸入提醒內容。")
    content = content.strip()
    if utf16_units(content) > MAX_CONTENT_UNITS:
        raise ReminderError("提醒內容最多 1500 字元；部分表情符號佔 2 字元。")
    try:
        content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ReminderError("提醒含有無效字元，請重新輸入。") from exc
    if any(ord(c) < 32 and c not in "\n\r\t" for c in content):
        raise ReminderError("提醒含有無法傳送的控制字元。")
    mode = data.get("mention_mode", "none")
    if mode not in ("none", "all", "users"):
        raise ReminderError("請選擇正確的標註方式。")
    users = data.get("user_ids", [])
    if not isinstance(users, list) or any(not isinstance(uid, str) for uid in users):
        raise ReminderError("指定成員格式錯誤。")
    users = list(dict.fromkeys(users))
    known = groups[gid].get("members", {})
    if mode == "users":
        if not 1 <= len(users) <= 20:
            raise ReminderError("請指定 1 至 20 位成員。")
        if any(not USER_ID.fullmatch(uid) or uid not in known for uid in users):
            raise ReminderError("指定成員不在此群組的已知名單，請重新選擇。")
    elif users:
        raise ReminderError("只有指定成員模式可以附上成員名單。")
    return {"group_id": gid, "group_name": str(groups[gid].get("name") or gid),
            "local_time": local_time, "due_at": due_at, "timezone": "Asia/Taipei",
            "content": content, "mention_mode": mode, "user_ids": users,
            "user_names": {uid: str(known[uid]) for uid in users}}


def build_line_message(record):
    content = ("⏰ 自訂提醒\n" + record["local_time"].replace("T", " ") + "（台灣時間）\n")
    mode = record["mention_mode"]
    if mode == "none":
        return {"type": "text", "text": content + record["content"]}
    targets = [{"type": "all"}] if mode == "all" else [
        {"type": "user", "userId": uid} for uid in record["user_ids"]]
    substitutions = {"m" + str(i): {"type": "mention", "mentionee": target}
                     for i, target in enumerate(targets)}
    content += " ".join("{" + key + "}" for key in substitutions) + "\n"
    # TextV2 treats braces as substitution syntax, including braces in user text.
    content += record["content"].replace("{", "{{").replace("}", "}}")
    if utf16_units(content) > 5000 or not 1 <= len(targets) <= 20:
        raise ReminderError("提醒超出 LINE 訊息限制。")
    return {"type": "textV2", "text": content, "substitution": substitutions}


class DeliveryError(Exception):
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


def send_line_reminder(record):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        raise DeliveryError("未設定 LINE 存取權杖。", False)
    payload = {"to": record["group_id"], "messages": [build_line_message(record)],
               "notificationDisabled": False}
    req = urllib.request.Request("https://api.line.me/v2/bot/message/push", method="POST",
                                 data=_encode(payload).encode("utf-8"), headers={
                                     "Authorization": "Bearer " + token,
                                     "Content-Type": "application/json",
                                     "X-Line-Retry-Key": record["retry_key"]})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            if response.status != 200:
                raise DeliveryError("LINE 回應異常，稍後重試。")
    except urllib.error.HTTPError as exc:
        if exc.code == 409 and exc.headers.get("x-line-accepted-request-id"):
            return  # LINE acknowledged this exact request previously.
        explanations = {
            400: "訊息或標註無效，請確認機器人及指定成員仍在群組。",
            401: "LINE 存取權杖無效。", 403: "LINE 權限不足，請檢查帳號與群組。",
            404: "LINE 群組或資源不存在。", 429: "LINE 額度或速率限制，稍後重試。",
        }
        raise DeliveryError("LINE " + str(exc.code) + "：" + explanations.get(exc.code, "傳送尚未確認。"),
                            exc.code == 409 or exc.code == 429 or exc.code >= 500) from exc
    except DeliveryError:
        raise
    except Exception as exc:
        raise DeliveryError("LINE 連線未完成，將使用同一識別碼重試。") from exc


class ReminderService:
    def __init__(self, store, catalog, sender=send_line_reminder, clock=time.time):
        self.store, self.catalog, self.sender, self.clock = store, catalog, sender, clock

    def create(self, data, actor, _races=0):
        if not isinstance(data, dict):
            raise ReminderError("請提供提醒資料。")
        try:
            key = str(uuid.UUID(data.get("request_id", "")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ReminderError("缺少有效的儲存識別碼，請重新整理。") from exc
        previous = self.store.get(key)
        # A lost POST response can be retried even after the scheduled time.
        if previous is not None:
            fields = ("group_id", "local_time", "mention_mode", "user_ids")
            data = dict(data)
            ids = data.get("user_ids", [])
            if isinstance(ids, list) and all(isinstance(uid, str) for uid in ids):
                data["user_ids"] = list(dict.fromkeys(ids))
            if (all(previous.get(k) == data.get(k, [] if k == "user_ids" else "none" if k == "mention_mode" else None)
                    for k in fields) and previous["content"] == str(data.get("content", "")).strip()):
                return previous
            raise ReminderError("此儲存識別碼已使用，請重新整理清單後再操作。", 409)
        now = self.clock()
        spec = validate_spec(data, self.catalog(), now)
        record = dict(spec, id=key, revision=1, created_at=now, updated_at=now,
                      created_by=actor, status="pending", attempts=0, first_attempt_at=0,
                      next_attempt_at=spec["due_at"], lease_until=0, lease_token="",
                      last_error="", sent_at=None, retry_key=str(uuid.uuid5(uuid.NAMESPACE_URL, "line-reminder:" + key)))
        if not self.store.compare_swap(None, record):
            if _races >= 3:
                raise ReminderError("提醒狀態尚未確認，請重新整理清單後再重試。", 409)
            return self.create(data, actor, _races + 1)
        return record

    def change(self, key, data, cancel=False):
        if not isinstance(data, dict):
            raise ReminderError("請提供提醒資料。")
        previous = self.store.get(key)
        if previous is None:
            raise ReminderError("找不到這筆提醒。", 404)
        if type(data.get("revision")) is not int or data["revision"] != previous["revision"]:
            raise ReminderError("提醒已被更新，請重新整理後再操作。", 409)
        if previous["status"] not in {"pending", "retrying"}:
            raise ReminderError("此提醒正在派送或已結束，無法修改或取消。", 409)
        current = dict(previous, updated_at=self.clock(), revision=previous["revision"] + 1)
        if cancel:
            current.update(status="cancelled", lease_until=0, lease_token="")
        else:
            if previous["attempts"]:
                raise ReminderError("已嘗試派送的提醒無法修改內容；請先確認群組收件狀態。", 409)
            current.update(validate_spec(data, self.catalog(), self.clock()))
            current["next_attempt_at"] = current["due_at"]
        if not self.store.compare_swap(previous, current):
            raise ReminderError("提醒狀態已改變，請重新整理後再操作。", 409)
        return current

    def run_due(self, limit=10, budget_seconds=20):
        result = {"sent": 0, "retrying": 0, "failed": 0, "uncertain": 0}
        started = time.monotonic()
        for previous in self.store.due(self.clock(), limit):
            if time.monotonic() - started > budget_seconds:
                break
            now = self.clock()
            if previous["status"] not in ACTIVE or previous["due_at"] > now:
                continue
            if previous["first_attempt_at"] and now - previous["first_attempt_at"] >= RETRY_WINDOW:
                current = dict(previous, status="uncertain", last_error="已超過安全重試時限，請到群組確認是否收到，避免重複提醒。",
                               updated_at=now, revision=previous["revision"] + 1, lease_token="", lease_until=0)
                if self.store.compare_swap(previous, current):
                    result["uncertain"] += 1
                continue
            claimed = dict(previous, status="sending", lease_token=str(uuid.uuid4()),
                           lease_until=now + LEASE_SECONDS, updated_at=now,
                           revision=previous["revision"] + 1, attempts=previous["attempts"] + 1,
                           first_attempt_at=previous["first_attempt_at"] or now)
            if not self.store.compare_swap(previous, claimed):
                continue
            current = dict(claimed, lease_token="", lease_until=0, revision=claimed["revision"] + 1)
            try:
                self.sender(claimed)
                current.update(status="sent", sent_at=self.clock(), last_error="")
            except Exception as exc:
                retryable = not isinstance(exc, DeliveryError) or exc.retryable
                current.update(status="retrying" if retryable else "failed",
                               last_error=str(exc) if isinstance(exc, DeliveryError) else "傳送未確認，稍後重試。",
                               next_attempt_at=self.clock() + min(900, 30 * 2 ** min(claimed["attempts"] - 1, 5)))
            current["updated_at"] = self.clock()
            # If this write fails, the lease expires and the SAME push is retried.
            if self.store.compare_swap(claimed, current):
                result[current["status"]] += 1
        return result


class ReminderWorker:
    """Fork-aware background poller; a cron wake-up never waits for LINE I/O."""
    def __init__(self, service_factory, logger, interval=30):
        self.service_factory, self.logger, self.interval = service_factory, logger, interval
        self._pid = os.getpid()
        self._thread = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self.last_check_at = None
        self.last_error = ""

    def start(self, force=False):
        if not force and os.environ.get("REMINDERS_WORKER_ENABLED", "1") == "0":
            return
        if self._pid != os.getpid():
            self._pid, self._thread = os.getpid(), None
            self._lock, self._wake = threading.Lock(), threading.Event()
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduled-reminders")
                self._thread.start()
            if force:
                self._wake.set()

    def _loop(self):
        while True:
            try:
                self.service_factory().run_due()
                self.last_check_at, self.last_error = time.time(), ""
            except Exception as exc:
                self.last_error = "提醒排程檢查未完成，稍後重試。"
                self.logger.warning("[Reminders] polling failed: %s", type(exc).__name__)
            self._wake.wait(self.interval)
            self._wake.clear()
