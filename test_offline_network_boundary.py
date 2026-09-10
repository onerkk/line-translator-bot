"""Verify the offline runner blocks even DNS and top-level HTTP dispatch."""
import socket
import urllib.request

import importlib
import importlib.util
import requests
import urllib3

import pytest

from run_translation_offline_checks import offline_network, OfflineNetworkBlocked


def test_external_requests_are_rejected_before_http_or_dns(monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: calls.append("DNS"))
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", lambda *a, **kw: calls.append("HTTP"))
    with offline_network():
        with pytest.raises(OfflineNetworkBlocked):
            urllib.request.urlopen("https://offline-test.invalid/private-data")
        with pytest.raises(OfflineNetworkBlocked):
            socket.getaddrinfo("offline-test.invalid", 443)
        with pytest.raises(OfflineNetworkBlocked):
            socket.create_connection(("192.0.2.1", 443))
        with socket.socket() as sock, pytest.raises(OfflineNetworkBlocked):
            sock.connect(("192.0.2.1", 443))
        with pytest.raises(OfflineNetworkBlocked):
            requests.get("https://offline-test.invalid/private-data")
        for name in ("httpx", "httpx2"):
            if importlib.util.find_spec(name):
                module = importlib.import_module(name)
                with module.Client(trust_env=False) as client, pytest.raises(OfflineNetworkBlocked):
                    client.get("https://offline-test.invalid/private-data")
        with pytest.raises(OfflineNetworkBlocked):
            urllib3.PoolManager().request("GET", "https://offline-test.invalid/private-data")
    assert calls == []
