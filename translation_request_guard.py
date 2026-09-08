"""Serialize identical cacheable requests while leaving other work concurrent.

The deployed Docker command uses one process with multiple threads. Waiters
re-enter the normal validated cache path after the first request completes;
unverified results/exceptions are never shared. Registry entries are released
only after all waiters leave, so no replacement lock can race an existing one.
"""
from contextlib import contextmanager
from collections import OrderedDict
import threading
import time

_registry_lock = threading.Lock()
_requests = {}
_context_results = OrderedDict()
CONTEXT_RESULT_TTL = 120.0
CONTEXT_RESULT_MAX_ENTRIES = 256
CONTEXT_RESULT_MAX_CHARS = 32768


def get_context_result(key):
    """Read an exact *effective-context* result, never a source-only TM row.

    Callers hold serialize_request(key), validate all source/context/settings
    inputs in the key, and still run their final delivery guards on a hit.
    This process-local store deliberately expires quickly and is not persisted.
    """
    now = time.monotonic()
    with _registry_lock:
        row = _context_results.get(key)
        if row is None:
            return None
        if now - row[1] >= CONTEXT_RESULT_TTL:
            _context_results.pop(key, None)
            return None
        _context_results.move_to_end(key)
        return row[0]


def set_context_result(key, result):
    """Admit only a complete string already approved by the caller's gates."""
    if key is None or not isinstance(result, str) or not result.strip():
        return
    if len(result) > CONTEXT_RESULT_MAX_CHARS:
        return
    with _registry_lock:
        now = time.monotonic()
        for stale in [k for k, row in _context_results.items()
                      if now - row[1] >= CONTEXT_RESULT_TTL]:
            _context_results.pop(stale, None)
        _context_results[key] = (result, now)
        _context_results.move_to_end(key)
        while len(_context_results) > CONTEXT_RESULT_MAX_ENTRIES:
            _context_results.popitem(last=False)


@contextmanager
def serialize_request(key):
    if key is None:
        yield
        return
    with _registry_lock:
        entry = _requests.setdefault(key, [threading.RLock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _registry_lock:
            entry[1] -= 1
            if entry[1] == 0:
                _requests.pop(key, None)
