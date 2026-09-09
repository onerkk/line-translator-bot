"""Persistent, command-created notices with stoppable reminder rounds.

The notice and its due index are one atomic write on SQLite and Redis. Each
LINE request is frozen before I/O and has a stable retry key, including the
initial card. A signed receipt can update responses without losing a lease.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

LEASE_SECONDS = 120
BUILD_ID = "2026-09-09.ack-pending-mentions.5"
RETRY_WINDOW = 23 * 3600
USER_ID = re.compile(r"U[0-9a-f]{32}\Z")


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


def finish_if_no_pending(notice, departed=None, *, now):
    """Stop reminders when known recipients are done, regardless of roster size.

    Use inside the notice's atomic update. Clearing the lease also prevents an
    in-flight sender from scheduling another round or retry after the last ack.
    The initial card must still be delivered when the known roster is empty.
    """
    if (not notice or notice.get("delivery_state") != "delivered"
            or not notice.get("reminder_minutes") or notice.get("reminder_stopped_at")
            or pending_ids(notice, departed)):
        return notice
    return dict(notice, reminder_state="no_pending", wake_at=None,
                next_reminder_at=None, pending_batch=None, lease_id="", last_error="",
                reminder_completed_at=notice.get("reminder_completed_at") or now)


def member_count(group):
    """Unlike member IDs, LINE exposes the group count to unverified accounts."""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("member count unavailable")
    kind = "group" if group.startswith("C") else "room"
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/" + kind + "/" + group + "/members/count",
        headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=4) as response:
        count = json.load(response).get("count")
    if type(count) is not int or count < 0:
        raise RuntimeError("invalid member count")
    return count


def unknown_member_count(notice, departed=None):
    """None means unknown, not zero. LINE's count excludes the bot itself."""
    count = notice.get("roster_count")
    if type(count) is int:
        known = set(notice.get("expected", {})) | set(notice.get("responses", {}))
        known.add(notice.get("sender_id"))
        known = {uid for uid in known if isinstance(uid, str) and USER_ID.fullmatch(uid)}
        return max(0, count - len(known - set(departed or {})))
    return None if notice.get("roster_basis") == "known_chat_members" else 0


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

    def _refresh_roster(self, key, lease, row, now):
        # Network calls stay outside CAS and the translation response path.
        # Retry a frozen LINE request unchanged while known recipients remain.
        if row.get("roster_checked_at") is not None or row.get("pending_batch"):
            return row
        if not row.get("notice_command") and row.get("roster_basis") != "known_chat_members":
            return row
        group = row["group_id"]
        host = getattr(self.hub, "h", {})
        names = (self.hub.known_members(group) if hasattr(self.hub, "known_members") else
                 dict(host.get("group_user_names", {}).get(group, {})))
        basis, ids, count = row.get("roster_basis", "known_chat_members"), None, None
        try:
            ids = host.get("_factory_member_ids", member_ids)(group)
            if not isinstance(ids, list) or not ids or any(
                    not isinstance(uid, str) or not USER_ID.fullmatch(uid) for uid in ids):
                raise ValueError("incomplete member IDs")
            ids = set(ids)
            basis = "line_group_members"
        except Exception:
            ids = None
        try:
            value = host.get("_factory_member_count", member_count)(group)
            if type(value) is int and value >= 0:
                count = value
        except Exception:
            pass  # Completeness is informational; remind only known pending IDs.
        if ids is not None and hasattr(self.hub, "remember_members"):
            self.hub.remember_members(group, {uid: names.get(uid, "未取得姓名 / Nama belum tersedia") for uid in ids})
        def refresh(current):
            expected = dict(current.get("expected", {}))
            expected.update({uid: str(name) for uid, name in names.items()
                             if isinstance(uid, str) and USER_ID.fullmatch(uid)})
            if ids is not None:
                expected = {uid: expected.get(uid, "未取得姓名 / Nama belum tersedia") for uid in ids}
            expected.pop(current.get("sender_id"), None)
            return dict(current, expected=expected, roster_basis=basis,
                        roster_count=count, roster_checked_at=now)
        return self._update(key, lease, refresh)

    def _complete_round(self, row, now, scope, *, still_pending=True):
        row.update(reminder_count=row.get("reminder_count", 0) + 1,
                   reminded_at=now, last_reminder_scope=scope, lease_id="")
        due = now + row.get("reminder_minutes", 0) * 60
        if (still_pending and row.get("reminder_repeat") and not row.get("reminder_stopped_at")
                and row.get("reminder_minutes", 0) > 0 and due < row["expires_at"]):
            row.update(reminder_state="repeat_pending", wake_at=due, next_reminder_at=due,
                       reminder_round=row.get("reminder_round", 0) + 1,
                       reminder_batch=0, reminded_ids=[], roster_checked_at=None)
        else:
            row.update(reminder_state=("sent_all" if scope == "all" else "sent") if still_pending else "no_pending",
                       wake_at=None, next_reminder_at=None)
        return row

    def _prepare_batch(self, row, initial, departed, now):
        """Called inside CAS retry: recipients come from the latest responses."""
        if not initial and not pending_ids(row, departed):
            return finish_if_no_pending(row, departed, now=now)
        if row.get("pending_batch"):
            return row
        group, token = row["group_id"], row["token"]
        if initial:
            messages, ids, number = row["initial_messages"], [], "initial"
        else:
            done = set(row.get("reminded_ids", []))
            ids = [uid for uid in pending_ids(row, departed) if uid not in done][:20]
            if not ids:
                if done:
                    return self._complete_round(row, row.get("last_batch_sent_at", now), "users",
                                                still_pending=bool(pending_ids(row, departed)))
                return finish_if_no_pending(row, departed, now=now)
            number = "reminder:" + str(row.get("reminder_round", 0)) + ":" + str(row.get("reminder_batch", 0))
            substitutions = {"p" + str(i): {"type": "mention", "mentionee": {"type": "user", "userId": uid}}
                             for i, uid in enumerate(ids)}
            content = ("⏰ 作業確認提醒 / Pengingat konfirmasi #" + token[:6] + "\n" +
                       " ".join("{" + name + "}" for name in substitutions) +
                       "\n尚未回覆的同仁，請按下方「了解」。\n"
                       "Bagi yang belum menjawab, silakan tekan Paham.")
            messages = [{"type": "textV2", "text": content, "substitution": substitutions},
                        self.hub._notice_card(token, self.hub._short(row["original"], 1000), row).to_dict()]
        batch = {"messages": messages, "ids": ids, "initial": initial, "started_at": now,
                 "all_fallback": False, "prepared_only": True,
                 "key": str(uuid.uuid5(uuid.NAMESPACE_URL, "factory-ack:" + group + ":" + token + ":" + number))}
        return dict(row, pending_batch=batch)

    def _ready_batch(self, row, departed, now):
        """Recheck recipients atomically immediately before handing off to LINE.

        A retried request must keep its original content and key. If a target
        has answered/left, or an old release queued @All, retire that request
        and schedule a fresh targeted batch after the configured interval.
        Newly prepared requests that have never reached I/O can be rebuilt now.
        """
        batch = row.get("pending_batch")
        if not batch:
            return row
        if not batch["initial"]:
            row = finish_if_no_pending(row, departed, now=now)
            if not row.get("pending_batch"):
                return row
            everyone = batch.get("all_fallback") or any(
                item.get("mentionee", {}).get("type") == "all"
                for message in batch["messages"]
                for item in (message.get("substitution") or {}).values())
            outdated = everyone or not set(batch["ids"]).issubset(pending_ids(row, departed))
            if outdated:
                row = dict(row, pending_batch=None,
                           reminder_batch=row.get("reminder_batch", 0) + 1)
                if batch.get("prepared_only") is True:
                    row = self._prepare_batch(row, False, departed, now)
                    batch = row.get("pending_batch")
                    if not batch:
                        return row
                else:
                    due = now + row.get("reminder_minutes", 0) * 60
                    due = due if due < row["expires_at"] else None
                    return dict(row, lease_id="", wake_at=due, next_reminder_at=due,
                                reminder_state="pending" if due is not None else "cancelled",
                                roster_checked_at=None, attempts=0, last_error="",
                                reminder_retry_retired_at=now)
        return dict(row, pending_batch=dict(batch, prepared_only=False))

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
        departed = {}
        try:
            group, token = row["group_id"], row["token"]
            options = getattr(self.hub, "ack_options", self.hub.options)(group)
            if row.get("reminder_stopped_at"):
                self._finish(key, lease, reminder_state="stopped", next_reminder_at=None)
                return
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
            initial = row.get("delivery_state") != "delivered"
            departed = {} if initial else self.store.get("members-left:" + group) or {}
            if not initial and not pending_ids(row, departed):
                self._update(key, lease, lambda current: finish_if_no_pending(current, departed, now=now))
                return
            batch = row.get("pending_batch")
            if not batch:
                if not initial:
                    row = self._refresh_roster(key, lease, row, now)
                    if not row or row.get("lease_id") != lease:
                        return
                departed = {} if initial else self.store.get("members-left:" + group) or {}
                row = self._update(key, lease, lambda current: self._prepare_batch(current, initial, departed, now))
                if not row or row.get("lease_id") != lease or not row.get("pending_batch"):
                    return
                batch = row["pending_batch"]
            # Never change this payload after a possibly accepted attempt.
            latest = self.store.get(key)
            if not latest or latest.get("lease_id") != lease or latest.get("reminder_stopped_at"):
                return
            options = getattr(self.hub, "ack_options", self.hub.options)(group)
            if (not self.hub.current(row.get("factory_event")) or options["acknowledgements"] == "off"
                    or (not batch["initial"] and not options["ack_reminder_enabled"])):
                self._finish(key, lease, reminder_state="cancelled")
                return
            departed = {} if batch["initial"] else self.store.get("members-left:" + group) or {}
            latest = self._update(key, lease, lambda current: self._ready_batch(current, departed, now))
            if not latest or latest.get("lease_id") != lease or not latest.get("pending_batch"):
                return
            batch = latest["pending_batch"]
            if now - batch["started_at"] >= RETRY_WINDOW:
                self._finish(key, lease, reminder_state="uncertain", last_error="超過安全重試時限，請到群組確認是否收到。")
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
                    current["last_batch_sent_at"] = accepted
                    # Turning off repeat in the group also finishes existing
                    # repeat notices after this round; stopping is separate.
                    if options.get("ack_reminder_repeat") is False:
                        current["reminder_repeat"] = False
                    if set(pending_ids(current, departed)) - set(current["reminded_ids"]):
                        current.update(reminder_batch=current.get("reminder_batch", 0) + 1,
                                       reminder_state="sending", wake_at=accepted)
                    else:
                        current = self._complete_round(current, accepted, "users",
                                                       still_pending=bool(pending_ids(current, departed)))
                return finish_if_no_pending(current, departed, now=accepted)
            self._update(key, lease, delivered)
            self.hub.app.logger.info("[FactoryAck] accepted kind=%s recipients=%d",
                                     "initial" if batch["initial"] else "users",
                                     len(batch["ids"]))
        except Exception as exc:
            retryable = not isinstance(exc, SendError) or exc.retryable
            def failed(current):
                attempts = current.get("attempts", 0) + 1
                current.update(lease_id="", attempts=attempts,
                               reminder_state="retrying" if retryable else "failed",
                               last_error=str(exc) if isinstance(exc, SendError) else "派送尚未確認，稍後重試。",
                               wake_at=self.clock() + min(300, 15 * 2 ** min(attempts - 1, 5)) if retryable else None)
                return finish_if_no_pending(current, departed, now=self.clock())
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
