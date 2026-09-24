"""Quota exhaustion, request counts, concurrency and recovery without live I/O."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import io
import json
import threading
from types import SimpleNamespace
import urllib.error

import pytest

import upstash_quota as q
import scheduled_reminders as r
from line_factory_store import FeatureStore, StoreError, measure_storage
from test_scheduled_reminders import api_client, service, store, spec, NOW, DUE

URL = "https://quota-test.invalid"
TOKEN = "private-quota-token"
ERROR = "ERR max requests limit exceeded. Limit: 500000, Usage: 500000. See https://private.invalid/details"


@pytest.fixture(autouse=True)
def quota_state(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(q, "_gates", {})
    monkeypatch.setattr(q, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return clock


def http_failure(body=ERROR):
    return urllib.error.HTTPError(URL, 400, "Bad Request", {},
                                  io.BytesIO(json.dumps({"error": body}).encode()))


@pytest.mark.parametrize("http_error", [True, False])
def test_monthly_quota_stops_reads_and_mutations_without_replaying(monkeypatch, quota_state, http_error):
    calls = []
    def send(request, **kwargs):
        calls.append(json.loads(request.data))
        if http_error:
            raise http_failure()
        return io.BytesIO(json.dumps({"error": ERROR}).encode())
    monkeypatch.setattr(q.urllib.request, "urlopen", send)
    client = r.RedisReminderStore(URL, TOKEN)
    for now in (0, 1, 30, 300, 899):
        quota_state[0] = now
        with pytest.raises(r.StoreUnavailable) as failure:
            client._command(["HSET", "jobs", "id", "pending"])
        assert failure.value.code == "upstash_monthly_quota"
        assert failure.value.retry_after == 900 - now
        assert "500,000" in str(failure.value)
        assert "本月請求額度已用完" in str(failure.value)
        assert TOKEN not in str(failure.value) and "private.invalid" not in str(failure.value)
    assert len(calls) == 1
    assert calls[0][0] == "HSET"
    quota_state[0] = 900
    with pytest.raises(r.StoreUnavailable):
        client.get("id")
    assert len(calls) == 2  # One new attempt, no automatic mutation replay.


def test_all_upstash_clients_share_quota_cooldown(monkeypatch):
    import app
    import db_snapshot
    import phase_config_store
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        raise http_failure()
    monkeypatch.setattr(q.urllib.request, "urlopen", send)
    with pytest.raises(r.StoreUnavailable):
        r.RedisReminderStore(URL, TOKEN).get("id")
    for module, url_name, token_name in (
        (app, "_UPSTASH_URL", "_UPSTASH_TOKEN"),
        (db_snapshot, "_URL", "_TOK"),
        (phase_config_store, "_UPSTASH_URL", "_UPSTASH_TOKEN"),
    ):
        monkeypatch.setattr(module, url_name, URL)
        monkeypatch.setattr(module, token_name, TOKEN)
    assert app._kv_command(["GET", "settings"]) is None
    assert phase_config_store._kv_command(["GET", "phase"]) is None
    with pytest.raises(q.QuotaExceeded):
        db_snapshot._cmd(["SET", "snapshot", "body"])
    feature = FeatureStore(url=URL, token=TOKEN)
    monkeypatch.setattr(feature._http, "borrow", lambda *a: pytest.fail("quota must skip HTTP"))
    with measure_storage() as stats:
        with pytest.raises(StoreError) as failure:
            feature.due_notices(NOW)
    assert failure.value.retry_after == 900
    assert stats["requests"] == 0 and stats["skipped"] == 1
    assert len(calls) == 1


def test_factory_http_400_also_opens_shared_gate(monkeypatch):
    feature = FeatureStore(url=URL, token=TOKEN)
    @contextmanager
    def response(*args, **kwargs):
        yield SimpleNamespace(status_code=400, raw=SimpleNamespace(
            read=lambda *a, **k: json.dumps({"error": ERROR}).encode()))
    @contextmanager
    def borrow(*args, **kwargs):
        yield SimpleNamespace(post=response)
    monkeypatch.setattr(feature._http, "borrow", borrow)
    with pytest.raises(StoreError) as failure:
        feature.due_notices(NOW)
    assert failure.value.code == "upstash_monthly_quota"
    monkeypatch.setattr(q.urllib.request, "urlopen", lambda *a, **k: pytest.fail("must share gate"))
    with pytest.raises(r.StoreUnavailable, match="本月請求額度已用完"):
        r.RedisReminderStore(URL, TOKEN).get("id")


def test_recovery_admits_one_probe_then_resumes_real_requests(monkeypatch, quota_state):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise http_failure()
        if len(calls) == 2:
            entered.set()
            assert release.wait(5)
        return io.BytesIO(b'{"result": null}')
    monkeypatch.setattr(q.urllib.request, "urlopen", send)
    client = r.RedisReminderStore(URL, TOKEN)
    with pytest.raises(r.StoreUnavailable):
        client.get("id")
    quota_state[0] = 900
    with ThreadPoolExecutor(max_workers=5) as pool:
        probe = pool.submit(client.get, "id")
        assert entered.wait(5)
        try:
            for _ in range(12):
                with pytest.raises(r.StoreUnavailable):
                    pool.submit(client.get, "id").result(timeout=5)
            assert len(calls) == 2
        finally:
            release.set()
        assert probe.result(timeout=5) is None
    assert client.get("id") is None
    assert len(calls) == 3


def test_failed_recovery_probe_does_not_trigger_request_storm(monkeypatch, quota_state):
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise http_failure()
        raise TimeoutError()
    monkeypatch.setattr(q.urllib.request, "urlopen", send)
    client = r.RedisReminderStore(URL, TOKEN)
    with pytest.raises(r.StoreUnavailable):
        client.get("id")
    quota_state[0] = 900
    with pytest.raises(r.StoreUnavailable, match="逾時"):
        client.get("id")
    for _ in range(10):
        with pytest.raises(r.StoreUnavailable):
            client.get("id")
    assert len(calls) == 2


@pytest.mark.parametrize("reason", ["ERR syntax error", "NOPERM this token is read only",
                                         "ERR max request size exceeded", "ERR max concurrent connections exceeded"])
def test_other_http_400_errors_do_not_lock_out_database(monkeypatch, reason):
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise http_failure(reason)
        return io.BytesIO(b'{"result": null}')
    monkeypatch.setattr(q.urllib.request, "urlopen", send)
    client = r.RedisReminderStore(URL, TOKEN)
    with pytest.raises(r.StoreUnavailable) as failure:
        client.get("id")
    assert failure.value.code != "upstash_monthly_quota"
    assert client.get("id") is None
    assert len(calls) == 2


def test_other_database_or_credentials_not_blocked(monkeypatch):
    calls = []
    def send(request, **kwargs):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise http_failure()
        return io.BytesIO(b'{"result": null}')
    monkeypatch.setattr(q.urllib.request, "urlopen", send)
    with pytest.raises(q.QuotaExceeded):
        q.redis_command(URL, TOKEN, ["GET", "key"])
    assert q.redis_command("https://another.invalid", TOKEN, ["GET", "key"]) is None
    assert q.redis_command(URL, "replacement-token", ["GET", "key"]) is None
    assert len(calls) == 3


def test_admin_get_and_save_report_quota_without_empty_success(api_client, monkeypatch):
    client, headers, service = api_client
    service.store = r.RedisReminderStore(URL, TOKEN)
    calls = []
    def send(*a, **k):
        calls.append(1)
        raise http_failure()
    monkeypatch.setattr(q.urllib.request, "urlopen", send)
    status = client.get("/api/admin/reminders", headers=headers).json["status"]
    assert not status["ready"] and not status["records_available"]
    assert status["code"] == "upstash_monthly_quota" and status["retry_after"] == 900
    assert status["last_error"] == ""
    response = client.post("/api/admin/reminders", headers=headers, json=spec())
    assert response.status_code == 503
    assert not response.json["ok"] and response.json["code"] == status["code"]
    assert len(calls) == 1


def test_background_cleanup_once_per_hour_preserves_due_checks(service, monkeypatch):
    instance, clock, sent = service
    monotonic = [0.0]
    monkeypatch.setattr(r, "time", SimpleNamespace(monotonic=lambda: monotonic[0]))
    due, cleanup = instance.store.due, instance.store.prune_history
    counts = {"due": 0, "cleanup": 0}
    def read(*args, **kwargs):
        counts["due"] += 1
        return due(*args, **kwargs)
    def prune(*args, **kwargs):
        counts["cleanup"] += 1
        return cleanup(*args, **kwargs)
    monkeypatch.setattr(instance.store, "due", read)
    monkeypatch.setattr(instance.store, "prune_history", prune)
    for tick in range(120):
        monotonic[0] = tick * 30
        instance.run_due(force_cleanup=False)
    assert counts == {"due": 120, "cleanup": 1}
    instance.list()  # Admin refresh shares the maintenance schedule.
    assert counts["cleanup"] == 1
    monotonic[0] = 3600
    instance.run_due(force_cleanup=False)
    assert counts == {"due": 121, "cleanup": 2}
    assert not sent


def test_idle_custom_and_ack_queues_use_one_plain_read(monkeypatch):
    import fakeredis
    fake = fakeredis.FakeRedis(decode_responses=True)
    calls = []
    def command(args):
        calls.append(args)
        return fake.execute_command(*args)
    custom = r.RedisReminderStore(URL, TOKEN)
    monkeypatch.setattr(custom, "_command", command)
    factory = FeatureStore(command=command)
    assert custom.due(NOW) == []
    assert factory.due_notices(NOW) == []
    assert [args[0] for args in calls] == ["ZRANGEBYSCORE", "ZRANGEBYSCORE"]


def test_reschedule_between_due_probe_and_atomic_read_does_not_send(service, monkeypatch):
    instance, clock, sent = service
    if instance.store.kind != "upstash":
        return
    row = instance.create(spec(), "admin")
    clock[0] = DUE
    original = instance.store._command
    def command(args):
        result = original(args)
        if args[0] == "ZRANGEBYSCORE" and args[1] == instance.store.keys[1] and result:
            changed = dict(row, due_at=DUE + 3600, next_attempt_at=DUE + 3600, revision=2)
            assert instance.store.compare_swap(row, changed)
        return result
    monkeypatch.setattr(instance.store, "_command", command)
    assert instance.run_due(force_cleanup=False)["sent"] == 0
    assert not sent


def test_visibility_expires_on_time_even_between_cleanups(service, monkeypatch):
    instance, clock, _ = service
    monkeypatch.setattr(r, "time", SimpleNamespace(monotonic=lambda: 0.0))
    row = instance.create(spec(), "admin")
    instance.change(row["id"], {"revision": row["revision"]}, cancel=True)
    clock[0] = NOW + r.HISTORY_RETENTION_SECONDS - 1
    assert len(instance.list()) == 1
    clock[0] += 1
    assert instance.list() == []
    # Physical cleanup may lag; the API must never display expired records.
    assert instance.store.get(row["id"]) is not None


def test_custom_reminder_worker_waits_for_quota_cooldown():
    class EndLoop(BaseException):
        pass
    waits = []
    def unavailable(**kwargs):
        assert kwargs["force_cleanup"] is False
        raise r.StoreUnavailable("quota", retry_after=900)
    def wait(seconds):
        waits.append(seconds)
        raise EndLoop()
    worker = r.ReminderWorker(lambda: SimpleNamespace(run_due=unavailable), SimpleNamespace(warning=lambda *a: None))
    worker._wake = SimpleNamespace(wait=wait)
    with pytest.raises(EndLoop):
        worker._loop()
    assert waits == [900]


def test_ack_worker_waits_for_shared_quota_cooldown():
    from line_ack_reminders import NoticeWorker
    stopped, waits = [False], []
    def unavailable(**kwargs):
        failure = StoreError("quota")
        failure.retry_after = 900
        raise failure
    def wait(seconds):
        waits.append(seconds)
        stopped[0] = True
    worker = NoticeWorker(SimpleNamespace(run_due=unavailable), SimpleNamespace(warning=lambda *a: None))
    worker._stop = SimpleNamespace(is_set=lambda: stopped[0], wait=wait)
    worker._loop()
    assert waits == [900]
