"""A durable group card for an explicit early stop, committed with the stop.

The existing notice due index/worker resumes delivery after admin requests,
webhook failures or restarts. Reminder scheduling stays stopped throughout.
"""
from __future__ import annotations

import copy
import time
import uuid

from line_ack_completion import all_confirmed
from line_ack_reminders import RETRY_WINDOW, SendError, send_messages
from line_factory_store import StoreError
from line_message_ui import stopped_message

BUILD_ID = "2026-09-11.2-early-stop-card"
PENDING = {"pending", "sending"}
LEASE_SECONDS = 60


def prepare(notice, now):
    """Only call on the first authorized manual-stop transition, inside CAS."""
    if (notice.get("stop_notification") or notice.get("translation_pending")
            or notice.get("delivery_state") != "delivered" or all_confirmed(notice)
            or notice.get("reminder_completed_at")):
        return
    notice["stop_notification"] = {
        "state": "pending", "created_at": now, "next_attempt_at": now, "attempts": 0,
        "key": str(uuid.uuid5(uuid.NAMESPACE_URL,
                             "factory-tracking-stopped:" + notice["group_id"] + ":" + notice["token"])),
        "messages": [stopped_message(notice, now)],
    }


def send_pending(hub, notice, *, clock=time.time):
    """Claim once, recheck lifecycle, and keep the same payload/key on retry.

    Transport failures do not turn a successfully committed stop into a failed
    admin operation. The atomic due index owns recovery, without another tap.
    """
    if notice.get("stop_notification", {}).get("state") not in PENDING:
        return
    try:
        _send_pending(hub, notice, clock)
    except StoreError:
        raise
    except Exception as exc:
        raise StoreError("已停止追蹤，通知狀態等待復原。") from exc


def _send_pending(hub, notice, clock):
    group, token = notice["group_id"], notice["token"]
    key, now, lease = "notice:" + group + ":" + token, clock(), uuid.uuid4().hex

    def eligible(row):
        return bool(row and row.get("reminder_stopped_at")
                    and row.get("reminder_state") == "stopped"
                    and row.get("expires_at", 0) > clock()
                    and hub.current(row.get("factory_event"))
                    and hub.ack_options(group)["acknowledgements"] != "off"
                    and (hub.store.get("bot-left:" + group) or {}).get("at", 0) < row["created_at"])

    def claim(row):
        item = (row or {}).get("stop_notification", {})
        if item.get("state") not in PENDING or item.get("next_attempt_at", 0) > now:
            return row
        if not eligible(row):
            item.update(state="cancelled", lease_id="", next_attempt_at=None)
        elif now - item["created_at"] >= RETRY_WINDOW:
            item.update(state="uncertain", lease_id="", next_attempt_at=None)
        else:
            item.update(state="sending", lease_id=lease, next_attempt_at=now + LEASE_SECONDS)
        return row

    remaining = max(1, int(notice["expires_at"] - now))
    claimed = hub.store.update(key, claim, remaining)
    item = (claimed or {}).get("stop_notification", {})
    if item.get("lease_id") != lease:
        return

    def finish(**changes):
        def update(row):
            notification = (row or {}).get("stop_notification", {})
            if notification.get("lease_id") == lease:
                notification.update(lease_id="", **changes)
            return row
        hub.store.update(key, update, max(1, int(claimed["expires_at"] - clock())))

    latest = hub.store.get(key)
    if not eligible(latest):
        finish(state="cancelled", next_attempt_at=None)
        return
    if latest.get("stop_notification", {}).get("lease_id") != lease:
        return
    try:
        hub.h.get("_factory_stop_sender", send_messages)(group, copy.deepcopy(item["messages"]), item["key"])
    except Exception as exc:
        retryable = not isinstance(exc, SendError) or exc.retryable
        attempts = item.get("attempts", 0) + 1
        finish(state="pending" if retryable else "failed", attempts=attempts,
               next_attempt_at=clock() + min(300, 15 * 2 ** min(attempts - 1, 5)) if retryable else None,
               last_error="停止追蹤通知等待重試。" if retryable else "LINE 拒絕停止追蹤通知。")
        hub.app.logger.warning("[FactoryStop] notification %s token=%s",
                               "pending" if retryable else "rejected", token[:6])
        return
    finish(state="accepted", accepted_at=clock(), next_attempt_at=None, last_error="")
