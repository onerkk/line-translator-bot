"""Keep durable feature state isolated between independent offline scenarios."""
import sys

import pytest


@pytest.fixture(autouse=True)
def isolate_factory_interactions(tmp_path, monkeypatch):
    # Each scenario has separate durable history/settings; its process-local
    # contextual result cache must have the same isolation as the ordinary TM.
    import translation_request_guard
    from collections import OrderedDict
    if hasattr(translation_request_guard, "_context_results"):
        monkeypatch.setattr(translation_request_guard, "_context_results", OrderedDict())
    app = sys.modules.get("app")
    if app is not None and getattr(app, "factory_hub", None):
        from line_factory_store import FeatureStore
        import translation_retry_queue as queue
        monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "factory-outbox.db"))
        monkeypatch.setattr(app.factory_hub, "_store", FeatureStore(path=tmp_path / "factory.db"))
        monkeypatch.setattr(app.factory_hub, "_revision_db", None)
        monkeypatch.setattr(app, "factory_line_settings", {"groups": {}, "stations": []})
