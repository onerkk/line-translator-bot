"""Run the repository regressions in temporary state with external I/O blocked.

python run_translation_offline_checks.py
python run_translation_offline_checks.py test_translation_speed_economy.py

The existing SDK connection-reuse regression needs a local loopback server;
only 127.0.0.1/::1 are allowed. No provider or LINE connection is allowed.
"""
import importlib.util
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import socket
import sys
import tempfile
import urllib.parse
import urllib.request
from unittest.mock import patch


class OfflineNetworkBlocked(RuntimeError):
    """Refuse external I/O before HTTP dispatch or DNS resolution."""


def require_loopback(host):
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise OfflineNetworkBlocked("External network disabled in offline regression")


@contextmanager
def offline_network():
    """Block HTTP, DNS and sockets; fake transports and loopback still work."""
    import requests
    import urllib3
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    resolve, create = socket.getaddrinfo, socket.create_connection
    open_url = urllib.request.OpenerDirector.open
    send_request = requests.Session.send
    send_pool = urllib3.connectionpool.HTTPConnectionPool.urlopen

    def guarded(original):
        def run(sock, address, *args, **kwargs):
            require_loopback(address[0] if isinstance(address, tuple) else None)
            return original(sock, address, *args, **kwargs)
        return run

    def local_resolve(host, *args, **kwargs):
        require_loopback(host)
        return resolve(host, *args, **kwargs)

    def local_connection(address, *args, **kwargs):
        require_loopback(address[0])
        return create(address, *args, **kwargs)

    def local_open(opener, target, *args, **kwargs):
        url = target.full_url if isinstance(target, urllib.request.Request) else target
        require_loopback(urllib.parse.urlsplit(url).hostname)
        return open_url(opener, target, *args, **kwargs)

    def local_send(session, prepared, *args, **kwargs):
        require_loopback(urllib.parse.urlsplit(prepared.url).hostname)
        return send_request(session, prepared, *args, **kwargs)

    def local_pool(pool, *args, **kwargs):
        require_loopback(pool.host)
        return send_pool(pool, *args, **kwargs)

    with ExitStack() as stack:
        for obj, name, replacement in (
            (socket.socket, "connect", guarded(connect)),
            (socket.socket, "connect_ex", guarded(connect_ex)),
            (socket, "getaddrinfo", local_resolve),
            (socket, "create_connection", local_connection),
            (urllib.request.OpenerDirector, "open", local_open),
            (requests.Session, "send", local_send),
            (urllib3.connectionpool.HTTPConnectionPool, "urlopen", local_pool),
        ):
            stack.enter_context(patch.object(obj, name, replacement))
        # Support the installed provider SDK's HTTP transport without adding
        # or upgrading production dependencies for this test-only isolation.
        for module_name in ("httpx", "httpx2"):
            if importlib.util.find_spec(module_name):
                module = importlib.import_module(module_name)
                original = module.HTTPTransport.handle_request
                def local_httpx(transport, request, send=original):
                    require_loopback(request.url.host)
                    return send(transport, request)
                stack.enter_context(patch.object(module.HTTPTransport, "handle_request", local_httpx))
        yield


def main():
    repo = Path(__file__).resolve().parent
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    from benchmark_translation_cp import _offline_environment

    with tempfile.TemporaryDirectory(prefix="translation-offline-checks-") as state:
        _offline_environment(state)
        for name in list(os.environ):
            if name.upper().endswith("_PROXY"):
                os.environ.pop(name, None)
        # _offline_environment replaces LINE credentials with explicit dummy
        # values and clears AI/Redis keys before any application module loads.
        assert os.environ["LINE_CHANNEL_ACCESS_TOKEN"] == "offline-cp-probe"
        assert os.environ["LINE_CHANNEL_SECRET"] == "offline-cp-probe"
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        with offline_network():
            import ai_provider
            ai_provider.PROVIDER_CONFIG_PATH = str(Path(state) / "providers.json")
            import pytest
            tests = sys.argv[1:] or sorted(path.name for path in repo.glob("test_*.py"))
            # Pytest 9 ships the subtests fixture itself. Only older pytest
            # versions need the separate plugin; never load both providers.
            plugins = [] if importlib.util.find_spec("_pytest.subtests") else ["-p", "pytest_subtests"]
            return pytest.main(["-q", *plugins, "--tb=short", *tests])


if __name__ == "__main__":
    raise SystemExit(main())
