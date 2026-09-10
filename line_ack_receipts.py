"""One private confirmation per notice/member, after a durable acknowledgement.

The notification intent is part of the receipt's atomic write. Failed sends
raise StoreError so the existing signed-webhook recovery queue can resume them.
LINE retry keys and frozen messages prevent duplicates after uncertain sends.
No group reply, AI request, extra poller or separate storage is involved.
"""
from __future__ import annotations

import copy
import time
import uuid

from line_ack_reminders import RETRY_WINDOW, SendError, send_messages
from line_factory_store import StoreError

BUILD_ID = "2026-09-10.5-private-ack-once"
LEASE_SECONDS = 60
PENDING = {"pending", "sending"}


def prepare(notice, uid, now):
    """Call inside the same CAS that first records an understood response."""
    notice.setdefault("receipt_confirmations", {}).setdefault(uid, {
        "state": "pending", "created_at": now, "next_attempt_at": now, "attempts": 0,
        "key": str(uuid.uuid5(uuid.NAMESPACE_URL,
                             "factory-private-ack:" + notice["group_id"] + ":" + notice["token"] + ":" + uid)),
        "messages": [{"type": "text", "text": "✅ 已記錄 / Sudah tercatat\n通知 / Pemberitahuan #" + notice["token"][:6]}],
    })


def send_pending(hub, notice, uid):
    """Only a validated recipient may resume their existing private intent.

    Old acknowledgements have no intent and are never backfilled on a tap.
    The accepted marker stays separate from responses so legacy status changes
    cannot rearm a notification that has already been accepted by LINE.
    """
    pending = notice.get("receipt_confirmations", {}).get(uid, {})
    if pending.get("state") not in PENDING:
        return
    try:
        _send_pending(hub, notice, uid)
    except StoreError:
        raise
    except Exception as exc:
        # SQLite can raise an OperationalError directly. Preserve the signed
        # webhook for these checkpoint failures too, after the ack was saved.
        raise StoreError("回覆已記錄，私人確認狀態等待復原。") from exc


def _send_pending(hub, notice, uid):
    group, token = notice["group_id"], notice["token"]
    current = hub.get_notice(token, group)
    departed = hub.store.get("members-left:" + group) or {}
    if (not current or not hub._notice_recipient(current, uid, departed)
            or current.get("responses", {}).get(uid, {}).get("status") != "understood"
            or hub.ack_options(group)["acknowledgements"] == "off"):
        return
    key, lease, now = "notice:" + group + ":" + token, uuid.uuid4().hex, time.time()
    ttl = max(1, int(float(current["expires_at"]) - now))

    def claim(row):
        item = (row or {}).get("receipt_confirmations", {}).get(uid, {})
        if (item.get("state") not in PENDING or not hub._notice_recipient(row, uid, departed)
                or row.get("responses", {}).get(uid, {}).get("status") != "understood"):
            return row
        if now - item["created_at"] >= RETRY_WINDOW:
            item.update(state="uncertain", lease_id="", next_attempt_at=None)
        elif item.get("next_attempt_at", 0) <= now:
            item.update(state="sending", lease_id=lease, next_attempt_at=now + LEASE_SECONDS)
        return row

    claimed = hub.store.update(key, claim, ttl)
    item = (claimed or {}).get("receipt_confirmations", {}).get(uid, {})
    if (item.get("state") not in PENDING or not hub._notice_recipient(claimed, uid, departed)
            or claimed.get("responses", {}).get(uid, {}).get("status") != "understood"):
        return
    if item.get("lease_id") != lease:
        # Keep this webhook recoverable while another worker owns the send or
        # the previous failure's backoff is in effect. Never issue a second key.
        raise StoreError("私人確認仍在處理，已保留同一筆待辦。")

    def finish(**changes):
        def update(row):
            notification = (row or {}).get("receipt_confirmations", {}).get(uid, {})
            if notification.get("lease_id") == lease:
                notification.update(lease_id="", **changes)
            return row
        return hub.store.update(key, update, max(1, int(float(claimed["expires_at"]) - time.time())))

    # A cancellation or an audience change that won the CAS must not send to
    # someone no longer authorized. Stop-reminder controls do not undo receipts.
    latest = hub.get_notice(token, group)
    departed = hub.store.get("members-left:" + group) or {}
    if (not latest or not hub._notice_recipient(latest, uid, departed)
            or latest.get("responses", {}).get(uid, {}).get("status") != "understood"
            or hub.ack_options(group)["acknowledgements"] == "off"):
        finish(state="cancelled", next_attempt_at=None)
        return
    latest_item = latest.get("receipt_confirmations", {}).get(uid, {})
    if latest_item.get("lease_id") != lease:
        if latest_item.get("state") in PENDING:
            raise StoreError("私人確認已由其他程序接手。")
        return
    try:
        # Push directly to the signed actor's U... ID. Never use the group's
        # reply token or the translation/reply fallback path for private data.
        hub.h.get("_factory_receipt_sender", send_messages)(uid, copy.deepcopy(item["messages"]), item["key"])
    except Exception as exc:
        retryable = not isinstance(exc, SendError) or exc.retryable
        attempts = item.get("attempts", 0) + 1
        finish(state="pending" if retryable else "failed", attempts=attempts,
               next_attempt_at=time.time() + min(300, 15 * 2 ** min(attempts - 1, 5)) if retryable else None,
               last_error="LINE 私人確認尚未接受。" if retryable else "LINE 拒絕私人確認，請檢查額度或權限。")
        if retryable:
            raise StoreError("回覆已記錄，私人確認等待自動重試。") from exc
        hub.app.logger.warning("[FactoryReceiptDM] rejected token=%s", token[:6])
        return
    # HTTP 200/accepted 409 proves API acceptance, not that a phone received or
    # vibrated. A failed checkpoint retains the same lease/key for safe recovery.
    finish(state="accepted", accepted_at=time.time(), next_attempt_at=None, last_error="")
