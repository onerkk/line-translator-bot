"""Per-group, per-person frequently used short replies.

Every person in every group gets their own ranking ("不同群不同人分開").
Only short everyday replies are counted (<= 30 chars, not commands, not
mentions, not pure numbers/IDs), so ordinary chat content is not stored
wholesale. Counts live in the existing Upstash store (one hash per group+person,
trimmed to the 40 most used), with an in-memory fallback for local runs.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import threading
from typing import Callable, Dict, List, Optional, Sequence, Tuple

MAX_LEN = 30
KEEP_PER_USER = 40
DEFAULT_TOP = 4
_KEY_PREFIX = "line_bot:phrases:"

_MEMORY: Dict[str, Dict[str, int]] = {}
_LOCK = threading.Lock()

_COMMAND_RE = re.compile(r"^\s*[/／!！#]")
_MENTION_RE = re.compile(r"@\S+")
_ONLY_CODE_RE = re.compile(r"^[\W\d_A-Za-z]{0,3}[\d\W_]+$")


def normalize(text: str) -> Optional[str]:
    """Return the phrase to count, or None when it is not a reusable reply."""
    original = str(text or "").strip()
    if "\n" in original:                 # multi-line messages are notices, not quick replies
        return None
    raw = re.sub(r"\s+", " ", original)
    if not raw or _COMMAND_RE.match(raw) or "http" in raw.lower():
        return None
    if _MENTION_RE.search(raw):          # replies aimed at one person are not reusable
        return None
    if len(raw) > MAX_LEN or "\n" in raw:
        return None
    if " " not in raw and re.search(r"\d", raw):   # "0730", "45B", "7J576102A": data, not a reply
        return None
    return raw


def _key(group_id: str, user_id: str) -> str:
    return f"{_KEY_PREFIX}{group_id}:{user_id}"


def record(group_id: str, user_id: str, text: str,
           kv: Optional[Callable[[Sequence[str]], object]] = None) -> bool:
    phrase = normalize(text)
    if not group_id or not user_id or not phrase:
        return False
    key = _key(group_id, user_id)
    if kv is not None:
        kv(["HINCRBY", key, phrase, "1"])
        size = kv(["HLEN", key])
        try:
            size = int(size or 0)
        except (TypeError, ValueError):
            size = 0
        if size > KEEP_PER_USER + 10:
            _trim_kv(key, kv)
        return True
    with _LOCK:
        bucket = _MEMORY.setdefault(key, {})
        bucket[phrase] = bucket.get(phrase, 0) + 1
        if len(bucket) > KEEP_PER_USER + 10:
            for name, _count in sorted(bucket.items(), key=lambda kv_: kv_[1])[: len(bucket) - KEEP_PER_USER]:
                bucket.pop(name, None)
    return True


def _pairs_from_hgetall(raw) -> List[Tuple[str, int]]:
    if isinstance(raw, dict):
        items = raw.items()
    else:
        seq = list(raw or [])
        items = zip(seq[0::2], seq[1::2])
    pairs = []
    for name, count in items:
        try:
            pairs.append((str(name), int(count)))
        except (TypeError, ValueError):
            continue
    return pairs


def _trim_kv(key: str, kv) -> None:
    pairs = sorted(_pairs_from_hgetall(kv(["HGETALL", key])), key=lambda p: p[1])
    drop = [name for name, _count in pairs[: max(0, len(pairs) - KEEP_PER_USER)]]
    if drop:
        kv(["HDEL", key, *drop])


def top(group_id: str, user_id: str, n: int = DEFAULT_TOP,
        kv: Optional[Callable[[Sequence[str]], object]] = None, min_count: int = 2) -> List[str]:
    """Most used replies of this person in this group (used at least twice)."""
    if not group_id or not user_id:
        return []
    key = _key(group_id, user_id)
    if kv is not None:
        pairs = _pairs_from_hgetall(kv(["HGETALL", key]))
    else:
        with _LOCK:
            pairs = list(_MEMORY.get(key, {}).items())
    ranked = sorted((p for p in pairs if p[1] >= min_count), key=lambda p: (-p[1], p[0]))
    return [name for name, _count in ranked[: max(0, int(n))]]


def group_token(group_id: str, secret: str) -> str:
    """Opaque per-group token for links (the raw group id is never exposed)."""
    return hmac.new(secret.encode("utf-8"), str(group_id).encode("utf-8"), hashlib.sha256).hexdigest()[:24]
