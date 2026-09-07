"""Real local HTTP keep-alive and worker/credential isolation, with no LINE sends."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace

from linebot.v3.messaging import ApiClient, Configuration, MessagingApi, ReplyMessageRequest, TextMessage

import line_api_transport as transport


def test_sdk_reuses_http_connection_and_rotates_credentials():
    received = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.client_address[1], self.headers.get('Authorization'), body))
            response = b'{"sentMessages":[{"id":"offline-message","quoteToken":"offline-quote"}]}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    pool = transport.ClientPool()
    config = Configuration(host='http://127.0.0.1:'+str(server.server_port), access_token='offline-original')
    request = ReplyMessageRequest(reply_token='offline-reply', messages=[TextMessage(text='測試 / Uji')])
    try:
        for _ in range(3):
            with ApiClient(config) as client:
                MessagingApi(client).reply_message(request, _request_timeout=(1, 2))
            client.rest_client.pool_manager.clear()
        for _ in range(3):
            with pool.client(ApiClient, config) as client:
                MessagingApi(client).reply_message(request, _request_timeout=(1, 2))
        assert len({port for port, _, _ in received[:3]}) == 3
        assert len({port for port, _, _ in received[3:]}) == 1
        config.access_token = 'offline-rotated'
        with pool.client(ApiClient, config) as client:
            MessagingApi(client).reply_message(request, _request_timeout=(1, 2))
        assert received[-1][0] != received[-2][0]
        assert received[-1][1] == 'Bearer offline-rotated'
        assert all(body == request.to_dict() for _, _, body in received)
    finally:
        pool.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_client_lifecycle_isolated_by_thread_and_process(monkeypatch):
    created = []
    class Client:
        def __init__(self, config):
            self.closed = 0
            self.cleared = 0
            self.rest_client = SimpleNamespace(pool_manager=SimpleNamespace(clear=self.clear))
            created.append(self)
        def clear(self): self.cleared += 1
        def __enter__(self): return self
        def __exit__(self, *args): self.closed += 1
    pool = transport.ClientPool()
    config = SimpleNamespace(access_token='offline', host='https://line.invalid')
    barrier = threading.Barrier(2)
    def run():
        try:
            with pool.client(Client, config) as first:
                barrier.wait(timeout=2)
            with pool.client(Client, config) as again:
                assert again is first
                return id(again)
        finally:
            pool.close()
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert len(set(executor.map(lambda _: run(), range(2)))) == 2
    assert all(c.closed == c.cleared == 1 for c in created)
    with pool.client(Client, config) as original:
        pass
    pid = transport.os.getpid()
    monkeypatch.setattr(transport.os, 'getpid', lambda: pid + 1)
    with pool.client(Client, config) as after_fork:
        assert after_fork is not original
    assert original.closed == original.cleared == 1
    pool.close()


def test_different_configuration_or_factory_cannot_reuse_old_client():
    class Client:
        def __init__(self, config): self.config = config
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Replacement(Client): pass
    config = SimpleNamespace(access_token='offline', host='https://line.invalid')
    pool = transport.ClientPool()
    with pool.client(Client, config) as original:
        pass
    config.host = 'https://other.invalid'
    with pool.client(Client, config) as moved:
        assert moved is not original
    with pool.client(Replacement, config) as replaced:
        assert replaced is not moved
    pool.close()
