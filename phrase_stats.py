"""Per-group, per-person frequently used short replies.

Every person in every group gets their own ranking ("不同群不同人分開").
Only short everyday replies are counted (<= 30 chars, not commands, not
mentions, not pure numbers/IDs), so ordinary chat content is not stored
wholesale.

Storage is quota-aware: counts live in memory and each person's ranking is
written to Upstash as ONE small JSON field (hash per group) at most once every
FLUSH_SECONDS, instead of two Redis commands per chat message. A restart loses
at most the last few minutes of counting.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

MAX_LEN = 30
KEEP_PER_USER = 40
DEFAULT_TOP = 4
FLUSH_SECONDS = 600
_KEY_PREFIX = "line_bot:phrases2:"
_OLD_KEY_PREFIX = "line_bot:phrases:"      # 2026-11-09 layout (one hash per person)

# (group, user) -> {"counts": {phrase: n}, "loaded": bool, "dirty": bool, "last_flush": ts}
_MEMORY: Dict[Tuple[str, str], dict] = {}
_LOCK = threading.Lock()

_COMMAND_RE = re.compile(r"^\s*[/／!！#]")
_MENTION_RE = re.compile(r"@\S+")


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
    if len(raw) > MAX_LEN:
        return None
    if " " not in raw and re.search(r"\d", raw):   # "0730", "45B", "7J576102A": data, not a reply
        return None
    return raw


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


def _entry(group_id: str, user_id: str, kv) -> dict:
    """In-memory entry, loaded from the store once per process (1 command)."""
    key = (group_id, user_id)
    with _LOCK:
        entry = _MEMORY.get(key)
        if entry is None:
            entry = _MEMORY[key] = {"counts": {}, "loaded": kv is None, "dirty": False,
                                    "last_flush": time.time()}
        need_load = not entry["loaded"] and kv is not None
        if need_load:
            entry["loaded"] = True       # claim the load; concurrent callers do not repeat it
    if need_load:
        stored: Dict[str, int] = {}
        try:
            raw = kv(["HGET", _KEY_PREFIX + group_id, user_id])
            if raw:
                data = json.loads(raw)
                stored = {str(k): int(v) for k, v in dict(data).items()}
            else:
                # one-time migration from the first layout
                stored = dict(_pairs_from_hgetall(kv(["HGETALL", f"{_OLD_KEY_PREFIX}{group_id}:{user_id}"])))
        except Exception:
            stored = {}
        with _LOCK:
            for name, count in stored.items():
                entry["counts"][name] = entry["counts"].get(name, 0) + count
            if stored and not raw:
                entry["dirty"] = True
    return entry


def _trim(counts: Dict[str, int]) -> None:
    if len(counts) > KEEP_PER_USER + 10:
        for name, _count in sorted(counts.items(), key=lambda p: p[1])[: len(counts) - KEEP_PER_USER]:
            counts.pop(name, None)


def _flush(group_id: str, user_id: str, entry: dict, kv) -> None:
    with _LOCK:
        payload = json.dumps(entry["counts"], ensure_ascii=False, separators=(",", ":"))
        entry["dirty"] = False
        entry["last_flush"] = time.time()
    kv(["HSET", _KEY_PREFIX + group_id, user_id, payload])


def record(group_id: str, user_id: str, text: str,
           kv: Optional[Callable[[Sequence[str]], object]] = None) -> bool:
    phrase = normalize(text)
    if not group_id or not user_id or not phrase:
        return False
    entry = _entry(group_id, user_id, kv)
    with _LOCK:
        entry["counts"][phrase] = entry["counts"].get(phrase, 0) + 1
        _trim(entry["counts"])
        entry["dirty"] = True
        due = kv is not None and (time.time() - entry["last_flush"]) >= FLUSH_SECONDS
    if due:
        try:
            _flush(group_id, user_id, entry, kv)
        except Exception:
            pass
    return True


def flush_all(kv) -> int:
    """Write every pending ranking (called at shutdown / by tests)."""
    if kv is None:
        return 0
    with _LOCK:
        pending = [(k, e) for k, e in _MEMORY.items() if e.get("dirty")]
    for (group_id, user_id), entry in pending:
        try:
            _flush(group_id, user_id, entry, kv)
        except Exception:
            pass
    return len(pending)


def top(group_id: str, user_id: str, n: int = DEFAULT_TOP,
        kv: Optional[Callable[[Sequence[str]], object]] = None, min_count: int = 2) -> List[str]:
    """Most used replies of this person in this group (used at least twice)."""
    if not group_id or not user_id:
        return []
    entry = _entry(group_id, user_id, kv)
    with _LOCK:
        pairs = list(entry["counts"].items())
    ranked = sorted((p for p in pairs if p[1] >= min_count), key=lambda p: (-p[1], p[0]))
    return [name for name, _count in ranked[: max(0, int(n))]]


def group_token(group_id: str, secret: str) -> str:
    """Opaque per-group token for links (the raw group id is never exposed)."""
    return hmac.new(secret.encode("utf-8"), str(group_id).encode("utf-8"), hashlib.sha256).hexdigest()[:24]
