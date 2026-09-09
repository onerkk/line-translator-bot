"""Persistent, command-created notices and one reminder round per notice.

The notice and its due index are one atomic write on SQLite and Redis. Each
LINE request is frozen before I/O and has a stable retry key, including the
initial card. A signed receipt can update responses without losing a lease.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

LEASE_SECONDS = 120
RETRY_WINDOW = 23 * 3600


class SendError(RuntimeError):
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


def send_messages(group, messages, retry_key):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        raise SendError("未設定 LINE 存取權杖。", False)
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push", method="POST",
        data=json.dumps({"to": group, "messages": messages}, ensure_ascii=False).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                 "X-Line-Retry-Key": retry_key})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            if response.status != 200:
                raise SendError("LINE 尚未確認接受通知。")
    except urllib.error.HTTPError as exc:
        if exc.code == 409 and exc.headers.get("x-line-accepted-request-id"):
            return
        raise SendError("LINE " + str(exc.code) + "：請檢查額度、權限及群組成員。",
                        exc.code in {409, 429} or exc.code >= 500) from exc
    except (TimeoutError, OSError) as exc:
        raise SendError("LINE 連線尚未確認，將使用相同識別碼重試。") from exc


def member_ids(group):
    """The member-list API requires a verified/premium account; caller falls back."""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("member list unavailable")
    kind = "group" if group.startswith("C") else "room"
    base = "https://api.line.me/v2/bot/" + kind + "/" + group + "/members/ids"
    result, cursor, seen = [], "", set()
    for _ in range(20):
        url = base + ("?" + urllib.parse.urlencode({"start": cursor}) if cursor else "")
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.load(response)
        if not isinstance(data.get("memberIds"), list):
            raise RuntimeError("invalid member list")
        result.extend(data["memberIds"])
        cursor = data.get("next", "")
        if not cursor:
            return list(dict.fromkeys(result))
        if cursor in seen:
            break
        seen.add(cursor)
    raise RuntimeError("incomplete member list")


def pending_ids(notice, departed=None):
    responses = notice.get("responses", {})
    return [uid for uid in notice.get("expected", {})
            if uid != notice.get("sender_id") and uid not in responses and uid not in (departed or {})]


class NoticeService:
    def __init__(self, hub, sender=send_messages, clock=time.time):
        self.hub, self.sender, self.clock = hub, sender, clock

    @property
    def store(self):
        return self.hub.store

    def _update(self, key, lease, change):
        def apply(row):
            if row and row.get("lease_id") == lease:
                return change(row)
            return row
        return self.store.update(key, apply)

    def _finish(self, key, lease, **changes):
        return self._update(key, lease, lambda row: dict(row, wake_at=None, lease_id="", **changes))

    def _prepare_batch(self, row, initial, departed, now):
        """Called inside CAS retry: recipients come from the latest responses."""
        if row.get("pending_batch"):
            return row
        group, token = row["group_id"], row["token"]
        if initial:
            messages, ids, number = row["initial_messages"], [], "initial"
        else:
            done = set(row.get("reminded_ids", []))
            ids = [uid for uid in pending_ids(row, departed) if uid not in done][:20]
            if not ids:
                return dict(row, wake_at=None, lease_id="", reminder_state="sent" if done else "no_pending",
                            reminded_at=now if done else None)
            number = "reminder:" + str(row.get("reminder_batch", 0))
            substitutions = {"p" + str(i): {"type": "mention", "mentionee": {"type": "user", "userId": uid}}
                             for i, uid in enumerate(ids)}
            content = ("⏰ 作業確認提醒 / Pengingat konfirmasi #" + token[:6] + "\n" +
                       " ".join("{" + name + "}" for name in substitutions) +
                       "\n尚未回覆，請按下方「了解」或「需要說明」。\n"
                       "Belum menjawab. Silakan pilih Paham atau Perlu penjelasan.")
            messages = [{"type": "textV2", "text": content, "substitution": substitutions},
                        self.hub._notice_card(token, self.hub._short(row["original"], 1000), row).to_dict()]
        batch = {"messages": messages, "ids": ids, "initial": initial, "started_at": now,
                 "key": str(uuid.uuid5(uuid.NAMESPACE_URL, "factory-ack:" + group + ":" + token + ":" + number))}
        return dict(row, pending_batch=batch)

    def run_due(self, limit=10, budget_seconds=20):
        started = time.monotonic()
        for row in self.store.due_notices(self.clock(), limit):
            if time.monotonic() - started > budget_seconds:
                break
            self.process("notice:" + row["group_id"] + ":" + row["token"])

    def process(self, key):
        now, lease = self.clock(), uuid.uuid4().hex
        def claim(row):
            if not row or row.get("wake_at") is None or row["wake_at"] > now:
                return row
            return dict(row, lease_id=lease, wake_at=now + LEASE_SECONDS)
        row = self.store.update(key, claim)
        if not row or row.get("lease_id") != lease:
            return
        try:
            group, token = row["group_id"], row["token"]
            options = getattr(self.hub, "ack_options", self.hub.options)(group)
            bot_left = self.store.get("bot-left:" + group) or {}
            if (row["expires_at"] <= now or not self.hub.current(row.get("factory_event"))
                    or bot_left.get("at", 0) >= row["created_at"]
                    or options["acknowledgements"] == "off"):
                self._finish(key, lease, reminder_state="cancelled")
                return
            initial = row.get("delivery_state") != "delivered"
            if not initial and not options["ack_reminder_enabled"]:
                self._finish(key, lease, reminder_state="cancelled")
                return
            # Signed receipts can update this row while the worker owns a lease.
            row = self.store.get(key)
            if not row or row.get("lease_id") != lease:
                return
            batch = row.get("pending_batch")
            if not batch:
                departed = {} if initial else self.store.get("members-left:" + group) or {}
                row = self._update(key, lease, lambda current: self._prepare_batch(current, initial, departed, now))
                if not row or row.get("lease_id") != lease or not row.get("pending_batch"):
                    return
                batch = row["pending_batch"]
            if now - batch["started_at"] >= RETRY_WINDOW:
                self._finish(key, lease, reminder_state="uncertain", last_error="超過安全重試時限，請到群組確認是否收到。")
                return
            # Never change this payload after a possibly accepted attempt.
            if not self.hub.current(row.get("factory_event")):
                self._finish(key, lease, reminder_state="cancelled")
                return
            self.sender(group, copy.deepcopy(batch["messages"]), batch["key"])
            accepted = self.clock()
            def delivered(current):
                current.update(pending_batch=None, lease_id="", attempts=0, last_error="")
                if batch["initial"]:
                    due = accepted + current.get("reminder_minutes", 0) * 60
                    enabled = bool(current.get("reminder_minutes"))
                    current.update(delivery_state="delivered", delivered_at=accepted,
                                   reminder_due_at=due if enabled else None,
                                   reminder_state="pending" if enabled else "off", wake_at=due if enabled else None)
                else:
                    current["reminded_ids"] = list(dict.fromkeys(current.get("reminded_ids", []) + batch["ids"]))
                    current.update(reminder_batch=current.get("reminder_batch", 0) + 1,
                                   reminder_state="sending", wake_at=accepted)
                return current
            self._update(key, lease, delivered)
        except Exception as exc:
            retryable = not isinstance(exc, SendError) or exc.retryable
            def failed(current):
                attempts = current.get("attempts", 0) + 1
                current.update(lease_id="", attempts=attempts,
                               reminder_state="retrying" if retryable else "failed",
                               last_error=str(exc) if isinstance(exc, SendError) else "派送尚未確認，稍後重試。",
                               wake_at=self.clock() + min(300, 15 * 2 ** min(attempts - 1, 5)) if retryable else None)
                return current
            self._update(key, lease, failed)
            self.hub.app.logger.warning("[FactoryAck] delivery unconfirmed: %s", type(exc).__name__)


class NoticeWorker:
    def __init__(self, service, logger, interval=15):
        self.service, self.logger, self.interval = service, logger, interval
        self._pid = os.getpid()
        self._thread = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.last_check_at = None
        self.last_error = ""

    def start(self):
        if os.environ.get("FACTORY_ACK_WORKER_ENABLED", os.environ.get("REMINDERS_WORKER_ENABLED", "1")) == "0":
            return
        if self._pid != os.getpid():
            self._pid, self._thread = os.getpid(), None
            self._lock, self._stop = threading.Lock(), threading.Event()
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, daemon=True, name="factory-ack-reminders")
                self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.service.run_due()
                self.last_check_at, self.last_error = time.time(), ""
            except Exception as exc:
                self.last_error = "作業確認排程尚未完成檢查。"
                self.logger.warning("[FactoryAck] polling failed: %s", type(exc).__name__)
            self._stop.wait(self.interval)
