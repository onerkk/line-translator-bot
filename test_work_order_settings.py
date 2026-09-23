"""Admin persistence and interpretation-mode coverage for work orders."""

import app
import work_order_settings


def test_judgment_mode_defaults_to_special_and_normalizes_invalid_storage(monkeypatch):
    monkeypatch.setattr(work_order_settings, "DEFAULT_JUDGMENT_MODE", "special")
    monkeypatch.setattr("phase_config_store.load_config", lambda _key: {})
    assert work_order_settings.get_judgment_mode() == "special"
    monkeypatch.setattr("phase_config_store.load_config", lambda _key: {
        "judgment_mode": "corrupt-value",
    })
    assert work_order_settings.get_judgment_mode() == "special"
    monkeypatch.setattr("phase_config_store.load_config", lambda _key: {
        "judgment_mode": ["normal"],
    })
    assert work_order_settings.get_judgment_mode() == "special"


def test_save_judgment_mode_preserves_other_phase_settings(monkeypatch):
    saved = {}
    monkeypatch.setattr("phase_config_store.load_config", lambda _key: {"other": True})
    monkeypatch.setattr("phase_config_store.save_config",
                        lambda key, config: saved.update({key: config}) or True)
    assert work_order_settings.save_judgment_mode("normal") is True
    assert saved == {"work_order": {"other": True, "judgment_mode": "normal"}}


def test_admin_judgment_endpoint_requires_access_and_persists_valid_mode(monkeypatch):
    monkeypatch.setattr(app, "check_manager_access", lambda *_args: True)
    mode = {"value": "special"}
    monkeypatch.setattr(work_order_settings, "get_judgment_mode", lambda: mode["value"])

    def save(value):
        mode["value"] = value
        return True

    monkeypatch.setattr(work_order_settings, "save_judgment_mode", save)
    client = app.app.test_client()

    get = client.get("/api/admin/work-order-judgment")
    assert get.status_code == 200
    assert get.headers["Cache-Control"] == "no-store"
    assert get.get_json()["judgment_mode"] == "special"

    rejected = client.post("/api/admin/work-order-judgment", json={"judgment_mode": "guess"})
    assert rejected.status_code == 400

    saved = client.post("/api/admin/work-order-judgment", json={"judgment_mode": "normal"})
    assert saved.status_code == 200
    assert saved.get_json()["judgment_mode"] == mode["value"] == "normal"


def test_admin_judgment_endpoint_does_not_claim_unsaved_mode(monkeypatch):
    monkeypatch.setattr(app, "check_manager_access", lambda *_args: True)
    monkeypatch.setattr(work_order_settings, "save_judgment_mode", lambda _mode: False)
    client = app.app.test_client()
    response = client.post("/api/admin/work-order-judgment",
                           json={"judgment_mode": "normal"})
    assert response.status_code == 503
    assert response.get_json()["ok"] is False


def test_admin_judgment_endpoint_denies_users_without_factory_access(monkeypatch):
    monkeypatch.setattr(app, "check_manager_access", lambda *_args: False)
    client = app.app.test_client()
    assert client.get("/api/admin/work-order-judgment").status_code == 403
    assert client.post("/api/admin/work-order-judgment",
                       json={"judgment_mode": "normal"}).status_code == 403


def test_live_card_and_fallback_use_the_saved_mode(monkeypatch):
    import json
    from test_work_order_query import PHOTO_5

    monkeypatch.setattr(work_order_settings, "get_judgment_mode", lambda: "normal")
    source = (PHOTO_5.replace("噴漆位置：不噴", "噴漆位置：N")
              .replace("顏色：N", "顏色：102"))
    card = app.format_work_order_cards(source)
    text = json.dumps(card, ensure_ascii=False)
    fallback = app.format_work_order_query(source)
    assert "不噴 / Tidak dicat" in text
    assert "要噴漆" not in text
    assert "色碼 102 · 白 / putih" in fallback
    assert "要噴漆" not in fallback


def test_factory_admin_ui_exposes_both_modes_and_persistence_action():
    from pathlib import Path

    root = Path(__file__).resolve().parent
    script = (root / "static/admin_factory.js").read_text(encoding="utf-8")
    app_source = (root / "app.py").read_text(encoding="utf-8")
    assert "特殊規格判斷（預設）" in script
    assert "正常判斷（依工單欄位）" in script
    assert "callWorkOrderJudgment('POST'" in script
    assert "work-order-judgment-mode" in script
    assert "/api/admin/work-order-judgment" in app_source
