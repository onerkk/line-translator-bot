"""Group image handling remains exclusive across admin and command paths."""

import app


GROUP = "C" + "8" * 32
USER = "U" + "8" * 32


def test_admin_switches_between_image_translation_and_work_order_lookup(monkeypatch):
    monkeypatch.setattr(app, "check_manager_access", lambda *_: True)
    monkeypatch.setattr(app, "save_settings", lambda **_: True)
    monkeypatch.setattr(app, "group_img_settings", {})
    monkeypatch.setattr(app, "group_img_ask_settings", {})
    monkeypatch.setattr(app, "group_work_order_lookup_settings", {})
    monkeypatch.setitem(app.group_tracking, GROUP, {"name": "現場群組"})
    client = app.app.test_client()
    url = "/api/admin/groups/settings"

    assert app.get_group_image_mode(GROUP) == "translate"  # old groups stay compatible
    assert client.post(url, json={"group_id": GROUP, "work_order_lookup_on": True}).status_code == 200
    assert app.get_group_image_mode(GROUP) == "work_order"
    assert app.group_img_settings[GROUP] is False
    assert GROUP not in app.group_img_ask_settings
    row = next(row for row in client.get("/api/admin/groups").get_json()["groups"]
               if row["id"] == GROUP)
    assert row["image_mode"] == "work_order"
    assert row["work_order_lookup_on"] is True
    assert row["image_on"] is False

    assert client.post(url, json={"group_id": GROUP, "image_ask_mode": True}).status_code == 200
    assert app.get_group_image_mode(GROUP) == "ask"
    assert not app.group_work_order_lookup_settings.get(GROUP)

    assert client.post(url, json={"group_id": GROUP, "work_order_lookup_on": True}).status_code == 200
    assert client.post(url, json={"group_id": GROUP, "work_order_lookup_on": False}).status_code == 200
    assert app.get_group_image_mode(GROUP) == "off"  # no surprise restart of image translation

    assert client.post(url, json={"group_id": GROUP, "image_on": True}).status_code == 200
    assert app.get_group_image_mode(GROUP) == "translate"


def test_conflicting_admin_payload_is_rejected_without_partial_changes(monkeypatch):
    monkeypatch.setattr(app, "check_manager_access", lambda *_: True)
    monkeypatch.setattr(app, "save_settings", lambda **_: True)
    monkeypatch.setattr(app, "group_img_settings", {})
    monkeypatch.setattr(app, "group_img_ask_settings", {})
    monkeypatch.setattr(app, "group_work_order_lookup_settings", {})
    monkeypatch.setattr(app, "group_settings", {})
    response = app.app.test_client().post("/api/admin/groups/settings", json={
        "group_id": GROUP, "translation_on": False,
        "image_on": True, "work_order_lookup_on": True,
    })
    assert response.status_code == 400
    assert app.get_group_image_mode(GROUP) == "translate"
    assert GROUP not in app.group_settings


def test_commands_switch_exclusively(monkeypatch):
    monkeypatch.setattr(app, "save_settings", lambda **_: True)
    monkeypatch.setattr(app, "is_group_admin", lambda *_: True)
    monkeypatch.setattr(app, "group_img_settings", {})
    monkeypatch.setattr(app, "group_img_ask_settings", {})
    monkeypatch.setattr(app, "group_work_order_lookup_settings", {})
    app.handle_command("/wo on", GROUP, USER)
    assert app.get_group_image_mode(GROUP) == "work_order"
    assert "工單資訊查詢：開啟" in app.handle_command("/status", GROUP, USER)
    app.handle_command("/img ask", GROUP, USER)
    assert app.get_group_image_mode(GROUP) == "ask"
    app.handle_command("/wo on", GROUP, USER)
    app.handle_command("/wo off", GROUP, USER)
    assert app.get_group_image_mode(GROUP) == "off"
    app.handle_command("/img on", GROUP, USER)
    assert app.get_group_image_mode(GROUP) == "translate"
