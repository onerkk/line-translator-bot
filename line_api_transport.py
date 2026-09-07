"""Reuse synchronous LINE SDK connections within one worker thread.

The generated SDK creates a fresh urllib3 pool for every ApiClient. Keeping
one entered client per thread lets replies, push retries and profile lookups
reuse established connections. Credentials, configuration, factory or process
changes replace the client. No request, timeout or retry policy is changed.
"""
from contextlib import contextmanager
import os
import threading


class _ClientSlot:
    def __init__(self, key, factory, configuration):
        self.key = key
        self.owner = factory(configuration)
        self.client = self.owner.__enter__()

    def close(self):
        owner, self.owner = self.owner, None
        if owner is None:
            return
        try:
            owner.__exit__(None, None, None)
        finally:
            # SDK close() joins its optional async thread pool, but does not
            # explicitly close the synchronous HTTP pool.
            pool = getattr(getattr(self.client, "rest_client", None), "pool_manager", None)
            if pool is not None:
                pool.clear()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class ClientPool:
    def __init__(self):
        self.local = threading.local()

    @contextmanager
    def client(self, factory, configuration):
        key = (os.getpid(), factory, id(configuration),
               getattr(configuration, "host", None),
               getattr(configuration, "access_token", None))
        slot = getattr(self.local, "slot", None)
        if slot is None or slot.key != key:
            if slot is not None:
                slot.close()
                del self.local.slot
            slot = _ClientSlot(key, factory, configuration)
            self.local.slot = slot
        yield slot.client

    def close(self):
        slot = getattr(self.local, "slot", None)
        if slot is not None:
            del self.local.slot
            slot.close()


_pool = ClientPool()
client = _pool.client
