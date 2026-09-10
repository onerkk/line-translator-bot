"""One durable group completion card per notice, created by the final real ack.

The final response and frozen delivery intent commit in the same notice CAS.
Signed-webhook recovery resumes pending delivery without asking for another tap.
No AI, profile lookup, additional scheduler, or group reply token is involved.
"""
from __future__ import annotations

import copy
import time
import uuid

from line_ack_reminders import RETRY_WINDOW, SendError, send_messages, tracked_members, tracked_responses
from line_factory_store import StoreError
from line_message_ui import completion_message

BUILD_ID = "2026-09-10.9-all-confirmed-card"
LEASE_SECONDS = 60
PENDING = {"pending", "sending"}


def all_confirmed(notice):
    """An empty list or a departed, unanswered recipient is never an ack."""
    if not notice or notice.get("translation_pending") or notice.get("delivery_state") != "delivered":
        return False
    members = tracked_members(notice)
    return bool(members) and set(members) <= set(tracked_responses(notice))


def prepare(notice, now):
    """Call only inside the CAS that records a new understood response.

    Historical completed notices are deliberately not backfilled on status
    queries, scheduler scans or duplicate taps. Manual stop means stop reminders,
    not discard later valid confirmations of the original target list.
    """
    if notice.get("completion_notification") or not all_confirmed(notice):
        return
    members = sorted(tracked_members(notice))
    notice["completion_notification"] = {
        "state": "pending", "created_at": now, "next_attempt_at": now, "attempts": 0,
        "recipient_ids": members, "recipient_count": len(members),
        "key": str(uuid.uuid5(uuid.NAMESPACE_URL,
                             "factory-all-confirmed:" + notice["group_id"] + ":" + notice["token"])),
        "messages": [completion_message(notice, len(members), now)],
    }


def send_pending(hub, notice, uid):
    if notice.get("completion_notification", {}).get("state") not in PENDING:
        return
    try:
        _send_pending(hub, notice, uid)
    except StoreError:
        raise
    except Exception as exc:
        raise StoreError("全員確認已記錄，完成通知狀態等待復原。") from exc


def _send_pending(hub, notice, uid):
    group, token = notice["group_id"], notice["token"]
    key, lease, now = "notice:" + group + ":" + token, uuid.uuid4().hex, time.time()

    def eligible(row, departed, bot_left):
        item = (row or {}).get("completion_notification", {})
        return bool(row and row.get("expires_at", 0) > time.time()
                    and hub._notice_recipient(row, uid, departed) and all_confirmed(row)
                    and sorted(tracked_members(row)) == item.get("recipient_ids")
                    and bot_left.get("at", 0) < row["created_at"])

    current = hub.get_notice(token, group)
    if not current:
        return
    departed = hub.store.get("members-left:" + group) or {}
    bot_left = hub.store.get("bot-left:" + group) or {}
    allowed = hub.ack_options(group)["acknowledgements"] != "off"

    def claim(row):
        item = (row or {}).get("completion_notification", {})
        if item.get("state") not in PENDING:
            return row
        if not allowed or not eligible(row, departed, bot_left):
            item.update(state="cancelled", lease_id="", next_attempt_at=None)
        elif now - item["created_at"] >= RETRY_WINDOW:
            item.update(state="uncertain", lease_id="", next_attempt_at=None)
        elif item.get("next_attempt_at", 0) <= now:
            item.update(state="sending", lease_id=lease, next_attempt_at=now + LEASE_SECONDS)
        return row

    claimed = hub.store.update(key, claim, max(1, int(current["expires_at"] - now)))
    item = (claimed or {}).get("completion_notification", {})
    if item.get("state") not in PENDING:
        return
    if item.get("lease_id") != lease:
        raise StoreError("全員確認通知仍在處理，已保留同一筆待辦。")

    def finish(**changes):
        def update(row):
            notification = (row or {}).get("completion_notification", {})
            if notification.get("lease_id") == lease:
                notification.update(lease_id="", **changes)
            return row
        return hub.store.update(key, update, max(1, int(claimed["expires_at"] - time.time())))

    # Recheck current revision, recipient list and group permissions after the
    # claim. A manual reminder stop does not undo the completed confirmations.
    latest = hub.get_notice(token, group)
    departed = hub.store.get("members-left:" + group) or {}
    bot_left = hub.store.get("bot-left:" + group) or {}
    if (not eligible(latest, departed, bot_left)
            or hub.ack_options(group)["acknowledgements"] == "off"):
        finish(state="cancelled", next_attempt_at=None)
        return
    latest_item = latest.get("completion_notification", {})
    if latest_item.get("lease_id") != lease:
        if latest_item.get("state") in PENDING:
            raise StoreError("全員確認通知已由其他程序接手。")
        return
    try:
        hub.h.get("_factory_completion_sender", send_messages)(group, copy.deepcopy(item["messages"]), item["key"])
    except Exception as exc:
        retryable = not isinstance(exc, SendError) or exc.retryable
        attempts = item.get("attempts", 0) + 1
        finish(state="pending" if retryable else "failed", attempts=attempts,
               next_attempt_at=time.time() + min(300, 15 * 2 ** min(attempts - 1, 5)) if retryable else None,
               last_error="LINE 完成通知尚未接受。" if retryable else "LINE 拒絕完成通知，請檢查額度或權限。")
        if retryable:
            raise StoreError("全員確認已記錄，完成通知等待自動重試。") from exc
        hub.app.logger.warning("[FactoryCompletion] rejected token=%s", token[:6])
        return
    finish(state="accepted", accepted_at=time.time(), next_attempt_at=None, last_error="")
