"""Reuse synchronous LINE SDK connections across worker-thread lifetimes.

The generated SDK creates a fresh urllib3 pool for every ApiClient. Keeping
an exclusively leased client lets replies, push retries and profile lookups
reuse connections even when the next webhook runs on a different/new thread.
Concurrent calls never share mutable SDK state. Credentials, configuration,
factory or process changes retire old clients. Request/retry policy is unchanged.
"""
from contextlib import contextmanager
import os
from reusable_transport_pool import TransportPool


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
        self.pool = TransportPool()

    @contextmanager
    def client(self, factory, configuration):
        key = (os.getpid(), factory, id(configuration),
               getattr(configuration, "host", None),
               getattr(configuration, "access_token", None))
        with self.pool.borrow(key, lambda: _ClientSlot(key, factory, configuration)) as slot:
            yield slot.client

    def close(self):
        self.pool.close()


_pool = ClientPool()
client = _pool.client
