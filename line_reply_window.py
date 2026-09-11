"""Keep an unused webhook reply opportunity with its durable translation job.

No timer, network call or token refresh occurs here. Stored receipt times are
never replaced by worker start/retry times. LINE remains the authority on token
validity; an in-window attempt can still fail and use the existing push outbox.
"""
import math
import time

BUILD_ID = "2026-09-11.1-preserve-unused-reply"


def _positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else 0
    except (TypeError, ValueError):
        return 0


def capture(event, received_at):
    token = getattr(event, "reply_token", None)
    event_at = _positive(getattr(event, "timestamp", None)) / 1000
    received_at = _positive(received_at)
    if not isinstance(token, str) or not token or not event_at or not received_at:
        return None
    return {"token": token, "received_at": received_at, "event_at": event_at}


def available(payload, plan, *, now=None):
    """Conservative local eligibility, never a guarantee of server acceptance.

    A message with any previous send attempt keeps its existing push recovery
    route: switching transports after an uncertain acceptance can duplicate it.
    Missing timestamps (legacy jobs) cannot invent a fresh reply window.
    """
    window = payload.get("reply_window")
    if not isinstance(window, dict) or plan.get("attempted") or plan.get("next_batch") or plan.get("round"):
        return None
    current = _positive(time.time() if now is None else now)
    received = _positive(window.get("received_at"))
    occurred = _positive(window.get("event_at"))
    token = window.get("token")
    if not current or not received or not occurred or not isinstance(token, str) or not token:
        return None
    # Reserve a small margin under LINE's one-minute reply window. Redelivery
    # never extends an event beyond its 20-minute ceiling. Clock anomalies fall
    # back to push rather than granting a bogus new reply opportunity.
    if 0 <= current - received < 55 and 0 <= current - occurred < 20 * 60:
        return token
    return None
