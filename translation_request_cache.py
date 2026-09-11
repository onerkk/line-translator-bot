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
_ATOMIC_TYPES = frozenset((type(None), bool, int, float, complex, str, bytes))


def _detach(value, memo=None):
    """Copy plain analysis data without deepcopy's per-scalar dispatch cost.

    Preserve aliases/cycles and delegate custom types to their deepcopy protocol.
    Never return a mutable cached object to a validator or another caller.
    """
    kind = type(value)
    if kind in _ATOMIC_TYPES:
        return value
    if memo is None:
        memo = {}
    identity = id(value)
    if identity in memo:
        return memo[identity]
    if kind is list:
        result = []
        memo[identity] = result
        result.extend(_detach(item, memo) for item in value)
        return result
    if kind is dict:
        result = {}
        memo[identity] = result
        for key, item in value.items():
            result[_detach(key, memo)] = _detach(item, memo)
        return result
    if kind is tuple:
        items = [_detach(item, memo) for item in value]
        # A tuple -> list -> tuple cycle may have completed this tuple while
        # copying its children. Match deepcopy's cycle/identity semantics.
        if identity in memo:
            return memo[identity]
        result = value if all(a is b for a, b in zip(value, items)) else tuple(items)
        memo[identity] = result
        return result
    return copy.deepcopy(value, memo)


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
            return _detach(values[key])
    except (TypeError, ValueError, RecursionError):
        return compute()
    # Exceptions are never cached. Repairs/fallbacks can retry a failed check.
    result = compute()
    try:
        detached = _detach(result)
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
