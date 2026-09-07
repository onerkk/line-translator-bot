"""Bounded, request-local reuse of deterministic analysis.

This is not a translation memory. Only explicitly opted-in pure calculations
may use it. Keys contain their complete inputs; mutable results are detached on
every hit. No values, failures or authorization decisions survive a request.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import threading

_STATE = ContextVar('translation_local_analysis', default=None)
_MAX_ENTRIES = 256
_MAX_TEXT = 32768


def _freeze(value):
    if value is None or type(value) in (bool, int, float):
        return (type(value), value)
    if type(value) in (str, bytes):
        if len(value) > _MAX_TEXT:
            raise TypeError('oversize memo input')
        return (type(value), value)
    if type(value) in (list, tuple):
        return (type(value), tuple(_freeze(item) for item in value))
    if type(value) is dict:
        return (dict, tuple((_freeze(k), _freeze(v)) for k, v in value.items()))
    # Iterators must not be consumed to make a key. Unknown mutable objects
    # take the ordinary uncached path instead of an identity-based shortcut.
    raise TypeError('unsupported memo input')


@contextmanager
def scope():
    """Start a fresh scope, including nested translations on the same worker."""
    state = {'owner': threading.get_ident(), 'values': {}}
    token = _STATE.set(state)
    try:
        yield
    finally:
        state['values'].clear()
        _STATE.reset(token)


def scoped(function):
    @wraps(function)
    def run(*args, **kwargs):
        with scope():
            return function(*args, **kwargs)
    return run


def reuse(namespace, inputs, compute):
    state = _STATE.get()
    if state is None or state['owner'] != threading.get_ident():
        return compute()
    try:
        key = (namespace, _freeze(inputs))
        values = state['values']
        if key in values:
            return copy.deepcopy(values[key])
    except (TypeError, ValueError, RecursionError):
        return compute()
    # Exceptions are never cached. Repairs/fallbacks can retry a failed check.
    result = compute()
    try:
        detached = copy.deepcopy(result)
    except (TypeError, ValueError, RecursionError):
        return result
    if len(values) >= _MAX_ENTRIES:
        values.pop(next(iter(values)))
    values[key] = detached
    return result


def memoize(function):
    """Opt in a pure function whose mutable dependencies are explicit inputs."""
    @wraps(function)
    def run(*args, **kwargs):
        return reuse(function, (args, kwargs), lambda: function(*args, **kwargs))
    return run
