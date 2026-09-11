"""Offline lifecycle and real loopback HTTP tests; no AI/LINE service calls.

For a paired reproduction, TRANSPORT_BASELINE_DIR may point to the unmodified
line_api_transport.py and line_factory_store.py. The socket-count regression
must fail against the old per-thread implementation. Optional
TRANSPORT_RESULT_FILE records only this synthetic test's measurements.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import socket
import statistics
import threading
import time
from types import SimpleNamespace

import fakeredis
import pytest
from linebot.v3.messaging import ApiClient, Configuration, MessagingApi

import app
import line_api_transport
import line_factory_store
import reusable_transport_pool as pooling
from test_line_factory_features import GROUP, USER
from test_translation_notice_availability import runtime, event, SOURCE, TARGET  # noqa: F401

QUICK_REPLY = app._build_translation_action_quick_reply


def implementation(name):
    baseline = os.environ.get("TRANSPORT_BASELINE_DIR")
    if not baseline:
        return {"line_api_transport": line_api_transport, "line_factory_store": line_factory_store}[name]
    path = Path(baseline) / (name + ".py")
    spec = importlib.util.spec_from_file_location("baseline_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Resource:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def test_idle_bound_expiry_rotation_and_shutdown_wait_for_active_owners(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(pooling.time, "monotonic", lambda: clock[0])
    pool = pooling.TransportPool(max_idle=1, idle_seconds=10)
    with pool.borrow("first", Resource) as active:
        with pool.borrow("first", Resource) as idle:
            assert active is not idle
        with pool.borrow("new-credentials", Resource) as rotated:
            assert idle.closed == 1 and active.closed == 0
            assert rotated is not active
        assert rotated.closed == 0
    assert active.closed == 1
    clock[0] += 10
    with pool.borrow("new-credentials", Resource) as expired:
        assert rotated.closed == 1 and expired is not rotated
        pool.close()
        assert expired.closed == 0
    assert expired.closed == 1
    with pool.borrow("new-credentials", Resource) as one:
        with pool.borrow("new-credentials", Resource) as two:
            pass
    assert two.closed == 0 and one.closed == 1
    pool.close()
    assert two.closed == 1


def test_slow_factory_does_not_hold_pool_lock_and_failed_factory_does_not_poison_pool():
    pool = pooling.TransportPool()
    entered, release = threading.Event(), threading.Event()
    def create():
        entered.set()
        assert release.wait(2)
        return Resource()
    def slow():
        with pool.borrow("same", create) as value:
            return value
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(slow)
            assert entered.wait(1)
            with pool.borrow("same", Resource) as independent:
                assert not independent.closed
            release.set()
            assert future.result(timeout=1) is not independent
        def fail():
            raise ValueError("failed creation")
        with pytest.raises(ValueError, match="failed creation"):
            with pool.borrow("different", fail):
                pytest.fail("failed factory was admitted")
        with pool.borrow("different", Resource) as recovered:
            assert recovered.closed == 0
    finally:
        release.set()
        pool.close()


def test_nested_borrow_and_exception_return_only_after_owner_finishes():
    pool = pooling.TransportPool()
    try:
        with pool.borrow("same", Resource) as outer:
            with pytest.raises(TimeoutError):
                with pool.borrow("same", Resource) as inner:
                    assert outer is not inner
                    raise TimeoutError("HTTP caller failed")
            with pool.borrow("same", Resource) as recovered:
                assert recovered is inner and recovered is not outer
    finally:
        pool.close()
    assert inner.closed == outer.closed == 1


def test_child_replaces_inherited_lock_and_never_reuses_parent_socket(monkeypatch):
    pool = pooling.TransportPool()
    with pool.borrow("same", Resource) as parent:
        pass
    old_lock = pool._lock
    old_lock.acquire()
    pid = os.getpid()
    try:
        monkeypatch.setattr(pooling.os, "getpid", lambda: pid + 1)
        with pool.borrow("same", Resource) as child:
            assert child is not parent
            assert parent.closed == 1 and pool._lock is not old_lock
    finally:
        old_lock.release()
        pool.close()


def test_client_rotation_during_inflight_request_does_not_close_owner():
    created = []
    class Client(Resource):
        def __init__(self, cfg):
            super().__init__()
            self.token = cfg.access_token
            created.append(self)
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
    pool = line_api_transport.ClientPool()
    cfg = SimpleNamespace(host="https://line.invalid", access_token="offline-old")
    try:
        with pool.client(Client, cfg) as old:
            cfg.access_token = "offline-new"
            with pool.client(Client, cfg) as new:
                assert new.token == "offline-new" and old.closed == 0
            assert old.closed == 0
        assert old.closed == 1
        with pool.client(Client, cfg) as reused:
            assert reused is new
    finally:
        pool.close()
    assert all(c.closed == 1 for c in created)


def test_storage_session_is_exclusive_through_response_body_read(monkeypatch):
    barrier = threading.Barrier(3)
    sessions, bodies = [], []
    class Session:
        def __init__(self):
            self.busy = False
            self.closed = 0
            sessions.append(self)
        def close(self): self.closed += 1
        def post(self, url, **kwargs):
            assert not self.busy, "a session was lent while its response was still being read"
            self.busy = True
            bodies.append(json.loads(kwargs["data"]))
            owner = self
            class Response:
                status_code = 200
                def __init__(self): self.raw = SimpleNamespace(read=self.read)
                def read(self, *args, **kwargs):
                    barrier.wait(timeout=2)
                    return b'{"result":null}'
                def __enter__(self): return self
                def __exit__(self, *args): owner.busy = False
            return Response()
    monkeypatch.setattr(line_factory_store.requests, "Session", Session)
    store = line_factory_store.FeatureStore(url="https://storage.invalid", token="offline")
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            assert list(executor.map(store.get, ("one", "two", "three"))) == [None] * 3
        assert len(sessions) == len(bodies) == 3 and all(not s.busy for s in sessions)
    finally:
        store._http.close()
    assert all(s.closed == 1 for s in sessions)


def test_storage_credential_rotation_does_not_reuse_old_session():
    with local_services() as service:
        store = line_factory_store.FeatureStore(url="https://storage.invalid", token="offline-old")
        store.url = service.url + "/redis"
        try:
            for key in ("one", "two"):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    assert executor.submit(store.get, key).result(timeout=2) is None
            assert service.received[0]["port"] == service.received[1]["port"]
            store.token = "offline-new"
            assert store.get("three") is None
            assert service.received[2]["port"] != service.received[1]["port"]
            assert [r["auth"] for r in service.received] == ["Bearer offline-old"] * 2 + ["Bearer offline-new"]
        finally:
            store._http.close()


@contextmanager
def local_services(connection_delay=0):
    received, ports = [], set()
    redis = fakeredis.FakeRedis(decode_responses=True)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args): pass
        def setup(self):
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if connection_delay:
                time.sleep(connection_delay)  # Synthetic setup delay, never a live latency claim.
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            ports.add(self.client_address[1])
            received.append({"port": self.client_address[1], "path": self.path,
                             "auth": self.headers.get("Authorization"), "body": body})
            if self.path == "/redis":
                response = {"result": redis.execute_command(*body)}
            else:
                assert self.path == "/v2/bot/message/reply"
                response = {"sentMessages": [{"id": "offline-reply"}]}
            raw = json.dumps(response, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", received=received, ports=ports)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_full_translation_reuses_real_sockets_across_short_lived_webhook_threads(runtime, monkeypatch):
    line = implementation("line_api_transport")
    stores = implementation("line_factory_store")
    pool = line.ClientPool()
    delay = min(0.5, max(0, float(os.environ.get("TRANSPORT_TEST_CONNECTION_DELAY", "0"))))
    with local_services(delay) as service:
        store = stores.FeatureStore(url="https://storage.invalid", token="offline-storage")
        store.url = service.url + "/redis"  # Loopback only; production still requires HTTPS.
        monkeypatch.setattr(app.factory_hub, "_store", store)
        monkeypatch.setattr(app, "line_api_transport", SimpleNamespace(client=pool.client))
        monkeypatch.setattr(app, "ApiClient", ApiClient)
        monkeypatch.setattr(app, "MessagingApi", MessagingApi)
        monkeypatch.setattr(app, "configuration", Configuration(host=service.url, access_token="offline-line"))
        monkeypatch.setattr(app, "_build_translation_action_quick_reply", QUICK_REPLY)
        monkeypatch.setattr(app, "get_conv_context_enabled", lambda *_: False)
        # Compare six real translation misses. The fixture normally runs its
        # TM-writing background callback synchronously; that would make later
        # iterations cache hits rather than the same amount of model work.
        monkeypatch.setattr(app, "_BG_POST_EXECUTOR", SimpleNamespace(submit=lambda *a, **k: None))
        for name in ("group_tracking", "group_settings", "group_skip_users", "group_target_lang"):
            monkeypatch.setitem(getattr(app, name), GROUP, getattr(app, name)["notice-group"])
        monkeypatch.setitem(app.group_user_names, GROUP, {USER: "測試者"})
        measurements, rendered, translation_ports = [], [], set()
        try:
            for index in range(6):
                app.translation_cache.clear()
                message = event(SOURCE)
                message.source.group_id, message.source.user_id = GROUP, USER
                message.message.id = "transport-" + str(index)
                before = len(service.received)
                start = time.perf_counter()
                # Actual short-lived worker; recycling an OS thread ID must
                # not be confused with keeping its threading.local contents.
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(app.handle_message, message).result(timeout=10)
                measurements.append(round((time.perf_counter() - start) * 1000, 3))
                sent = service.received[before:]
                translation_ports.update(row["port"] for row in sent)
                assert [r["path"] for r in sent] == ["/redis", "/redis", "/v2/bot/message/reply"]
                assert [r["body"][0] for r in sent[:2]] == ["EVAL", "EVAL"]
                assert [r["auth"] for r in sent] == ["Bearer offline-storage"] * 2 + ["Bearer offline-line"]
                value = sent[-1]["body"]
                assert TARGET in "\n".join(row.get("text", "") for row in value["messages"])
                rendered.append(copy.deepcopy(value))
                # Even a second worker / memory-cache loss has working controls
                # immediately: the context write precedes the LINE send.
                checked_tokens = 0
                for item in value["messages"][-1].get("quickReply", {}).get("items", []):
                    from urllib.parse import parse_qs
                    params = parse_qs(item.get("action", {}).get("data", ""))
                    token = (params.get("context_token") or params.get("token") or [""])[0]
                    if token:
                        record = app.factory_hub.get_context(token, GROUP)
                        assert record and record["original"] == SOURCE
                        checked_tokens += 1
                assert checked_tokens > 0
            assert len(runtime.generations) == 6
            data = {"mode": "offline-loopback-http", "live_ai_calls": 0, "live_line_sends": 0,
                    "implementation": "baseline" if os.environ.get("TRANSPORT_BASELINE_DIR") else "updated",
                    "synthetic_connection_setup_ms": round(delay * 1000), "messages": 6,
                    "fake_translation_generations": len(runtime.generations),
                    "translation_request_sequence": ["revision_EVAL", "context_EVAL", "LINE_reply"],
                    "unique_tcp_connections": len(translation_ports), "per_message_ms": measurements,
                    "translation_http_requests": 18, "control_lookup_probes_included_in_latency": False,
                    "warm_median_ms": round(statistics.median(measurements[1:]), 3),
                    "scope": "real handler/SQLite/Redis Lua/SDK over local HTTP; fake AI; synthetic setup delay; async postprocessing excluded",
                    "production_end_to_end_latency_measured": False}
            if os.environ.get("TRANSPORT_RESULT_FILE"):
                Path(os.environ["TRANSPORT_RESULT_FILE"]).write_text(json.dumps(data, indent=2) + "\n")
            # Two reusable clients (storage and LINE), independent of how many
            # worker threads have completed. Previously six workers made twelve.
            assert len(translation_ports) == 2, data
        finally:
            pool.close()
            close = getattr(store._http, "close", None)
            if close:
                close()
