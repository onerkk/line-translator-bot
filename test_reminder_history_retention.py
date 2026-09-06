"""Seven-day retention in SQLite and the actual Redis Lua engine."""
from concurrent.futures import ThreadPoolExecutor

import pytest

import scheduled_reminders as r
from test_scheduled_reminders import store, service, api_client, spec, NOW, DUE


@pytest.mark.parametrize("status", sorted(r.TERMINAL))
def test_only_terminal_records_expire_at_seven_days(service, status):
    instance, clock, sent = service
    original = instance.create(spec(), "admin")
    ended = DUE + 30
    finished = dict(original, status=status, updated_at=ended, revision=2)
    assert instance.store.compare_swap(original, finished)
    clock[0] = ended + r.HISTORY_RETENTION_SECONDS - 0.001
    assert instance.store.prune_history(clock[0]) == 0
    assert instance.list()[0]["id"] == original["id"]
    clock[0] += 0.001
    assert instance.run_due()["purged"] == 1
    assert instance.store.get(original["id"]) is None
    assert instance.store.list() == []
    assert instance.store.due(clock[0]) == []
    assert sent == []
    if instance.store.kind == "upstash":
        assert instance.store._command(["HEXISTS", instance.store.keys[0], original["id"]]) == 0
        for key in instance.store.keys[1:]:
            assert instance.store._command(["ZSCORE", key, original["id"]]) is None


def test_retention_starts_when_sent_not_when_created_or_scheduled(service):
    instance, clock, sent = service
    row = instance.create(spec(), "admin")
    clock[0] = DUE + 14 * 86400  # a long host outage; this job has never been sent
    assert instance.run_due()["sent"] == 1
    assert instance.list()[0]["id"] == row["id"]
    clock[0] += r.HISTORY_RETENTION_SECONDS - 1
    instance.run_due()
    assert instance.store.get(row["id"]) is not None
    clock[0] += 1
    assert instance.run_due()["purged"] == 1
    assert len(sent) == 1


@pytest.mark.parametrize("status", sorted(r.ACTIVE))
def test_old_active_records_are_retained(service, status):
    instance, clock, sent = service
    row = instance.create(spec(local_time="2027-09-20T08:00"), "admin")
    active = dict(row, status=status, next_attempt_at=row["due_at"], lease_until=row["due_at"])
    assert instance.store.compare_swap(row, active)
    clock[0] += 30 * 86400
    assert instance.run_due()["purged"] == 0
    assert instance.store.get(row["id"]) == active
    assert instance.list() == [active]
    assert sent == []


def test_legacy_records_expire_without_a_new_timestamp_or_migration_flag(service):
    instance, clock, _ = service
    row = instance.create(spec(), "admin")
    cancelled = instance.change(row["id"], {"revision": row["revision"]}, cancel=True)
    # The old record schema only has updated_at; reopening must still clean it.
    reopened = r.SQLiteReminderStore(instance.store.path) if instance.store.kind == "sqlite" else instance.store
    clock[0] = cancelled["updated_at"] + r.HISTORY_RETENTION_SECONDS
    resumed = r.ReminderService(reopened, instance.catalog, clock=lambda: clock[0])
    assert resumed.run_due()["purged"] == 1
    assert reopened.get(row["id"]) is None


def test_old_future_jobs_do_not_starve_bounded_cleanup_or_pagination(service):
    instance, clock, _ = service
    active_ids, expired_ids = [], []
    for i in range(6):
        clock[0] += 1
        active_ids.append(instance.create(spec(local_time="2027-09-20T08:00"), "admin")["id"])
    for i in range(5):
        clock[0] += 1
        row = instance.create(spec(), "admin")
        instance.change(row["id"], {"revision": row["revision"]}, cancel=True)
        expired_ids.append(row["id"])
    clock[0] += r.HISTORY_RETENTION_SECONDS
    # All expired rows sort before old pending jobs. Pagination counts visible
    # jobs and must reach them even before any cleanup has been performed.
    visible = instance.store.list(limit=3, now=clock[0]) + instance.store.list(offset=3, limit=3, now=clock[0])
    assert [row["id"] for row in visible] == list(reversed(active_ids))
    for _ in range(12):
        assert instance.store.prune_history(clock[0], limit=2) <= 2
    assert all(instance.store.get(key) is None for key in expired_ids)
    assert all(instance.store.get(key) is not None for key in active_ids)


def test_parallel_cleanup_never_resurrects_or_resends_terminal_jobs(service):
    instance, clock, sent = service
    row = instance.create(spec(), "admin")
    cancelled = instance.change(row["id"], {"revision": row["revision"]}, cancel=True)
    clock[0] += r.HISTORY_RETENTION_SECONDS
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: instance.run_due()["purged"], range(8)))
    assert sum(results) == 1
    assert not instance.store.compare_swap(cancelled, dict(cancelled, status="sending"))
    assert instance.store.get(row["id"]) is None
    assert not sent


def test_admin_list_cleans_history_with_worker_disabled_and_keeps_future_jobs(api_client):
    client, headers, instance = api_client
    row = instance.create(spec(), "admin")
    instance.change(row["id"], {"revision": row["revision"]}, cancel=True)
    future = instance.create(spec(local_time="2027-09-20T08:00"), "admin")
    instance.clock = lambda: NOW + r.HISTORY_RETENTION_SECONDS
    response = client.get("/api/admin/reminders", headers=headers)
    assert response.status_code == 200
    assert response.json["status"]["history_retention_days"] == 7
    assert [row["id"] for row in response.json["reminders"]] == [future["id"]]
    assert instance.store.get(row["id"]) is None
