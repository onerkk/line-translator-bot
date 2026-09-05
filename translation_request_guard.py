"""Serialize identical cacheable requests while leaving other work concurrent.

The deployed Docker command uses one process with multiple threads. Waiters
re-enter the normal validated cache path after the first request completes;
unverified results/exceptions are never shared. Registry entries are released
only after all waiters leave, so no replacement lock can race an existing one.
"""
from contextlib import contextmanager
import threading

_registry_lock = threading.Lock()
_requests = {}


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
