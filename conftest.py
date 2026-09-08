"""Keep durable feature state isolated between independent offline scenarios."""
import sys
import threading
import time

import pytest


def wait_for_webhooks(timeout=5):
    """Integration tests wait for durable completion, separately from HTTP ACK."""
    import translation_retry_queue as queue
    deadline = time.monotonic() + timeout
    while queue.next_ready_delay(include_kinds=("webhook",)) is not None:
        if time.monotonic() > deadline:
            pytest.fail("webhook did not finish: " + str([
                (job["job_key"], job.get("last_error")) for job in queue.list_pending()
                if job["job_kind"] == "webhook"]))
        threading.Event().wait(0.01)


@pytest.fixture(autouse=True)
def isolate_factory_interactions(tmp_path, monkeypatch):
    # Each scenario has separate durable history/settings; its process-local
    # contextual result cache must have the same isolation as the ordinary TM.
    import translation_request_guard
    from collections import OrderedDict
    if hasattr(translation_request_guard, "_context_results"):
        monkeypatch.setattr(translation_request_guard, "_context_results", OrderedDict())
    app = sys.modules.get("app")
    pools = []
    if app is not None and getattr(app, "factory_hub", None):
        from line_factory_store import FeatureStore
        import translation_retry_queue as queue
        monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "factory-outbox.db"))
        monkeypatch.setattr(app.factory_hub, "_store", FeatureStore(path=tmp_path / "factory.db"))
        monkeypatch.setattr(app.factory_hub, "_revision_db", None)
        monkeypatch.setattr(app, "factory_line_settings", {"groups": {}, "stations": []})
        if hasattr(app, "_WEBHOOK_INBOX"):
            from webhook_runtime import WebhookInbox
            from durable_workers import WorkerPool
            inbox = WebhookInbox(app.handler, app._release_webhook_message_claims,
                                 workers=2, context=app.app.app_context)
            monkeypatch.setattr(app, "_WEBHOOK_INBOX", inbox)
            pools.append(inbox.pool)
            for name, kinds, excludes, workers in (
                ("_TEXT_RETRY_POOL", ("text", "variant"), (), 2),
                ("_MEDIA_RETRY_POOL", None, ("text", "variant", "webhook"), 1),
            ):
                pool = WorkerPool(name, app._run_scheduled_translation, workers=workers,
                                  include_kinds=kinds, exclude_kinds=excludes,
                                  backoff=app._translation_retry_backoff)
                monkeypatch.setattr(app, name, pool)
                pools.append(pool)
    yield
    # Stop workers before fixtures replace database paths or external mocks.
    # A worker leaking into the next test could mask a real delivery failure.
    for pool in pools:
        pool.stop()
        assert not pool.threads, "background work outlived its test's transports"
