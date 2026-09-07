"""Keep durable feature state isolated between independent offline scenarios."""
import sys

import pytest


@pytest.fixture(autouse=True)
def isolate_factory_interactions(tmp_path, monkeypatch):
    app = sys.modules.get("app")
    if app is not None and getattr(app, "factory_hub", None):
        from line_factory_store import FeatureStore
        import translation_retry_queue as queue
        monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "factory-outbox.db"))
        monkeypatch.setattr(app.factory_hub, "_store", FeatureStore(path=tmp_path / "factory.db"))
        monkeypatch.setattr(app.factory_hub, "_revision_db", None)
        monkeypatch.setattr(app, "factory_line_settings", {"groups": {}, "stations": []})
