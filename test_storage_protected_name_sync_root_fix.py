import copy
import io
import json

import pytest
from openpyxl import Workbook

import app


@pytest.fixture
def isolated_protected_names(monkeypatch):
    original_storage = copy.deepcopy(app.STORAGE_LOOKUP)
    original_groups = copy.deepcopy(app.extra_names_by_group)
    save_calls = []
    committed_storage = []

    monkeypatch.setattr(app, "check_manager_access", lambda _tab=None: True)
    monkeypatch.setattr(
        app,
        "save_settings",
        lambda *args, **kwargs: save_calls.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        app,
        "commit_storage_to_github",
        lambda payload: committed_storage.append(json.loads(payload)) or True,
    )

    try:
        yield save_calls, committed_storage
    finally:
        with app._state_lock:
            app.STORAGE_LOOKUP = original_storage
            app.extra_names_by_group.clear()
            app.extra_names_by_group.update(original_groups)
            app.rebuild_customer_names()


def _storage_workbook_bytes():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["客戶", "<=3200", ">3200<=4200", ">4200"])
    sheet.append(["客戶甲", "A01", None, None])
    sheet.append(["客戶乙", None, "B02", None])
    payload = io.BytesIO()
    workbook.save(payload)
    workbook.close()
    payload.seek(0)
    return payload


def test_name_cleanup_and_inventory_keep_one_copy_per_visible_name(
    isolated_protected_names,
):
    with app._state_lock:
        app.STORAGE_LOOKUP = {
            "儲區客戶": [["<=3200", "A01"]],
            "共同名稱": [[">3200<=4200", "B02"]],
        }
        app.extra_names_by_group.clear()
        app.extra_names_by_group.update({
            "__all__": ["手動人名", "手動人名", " 共同名稱 ", ""],
            "C_TEST": ["群組姓名", "群組姓名"],
        })

    stats = app.rebuild_customer_names()
    inventory = app.protected_name_inventory()

    assert stats["duplicates_removed"] == 3
    assert app.extra_names_by_group["__all__"] == ["手動人名", "共同名稱"]
    assert app.extra_names_by_group["C_TEST"] == ["群組姓名"]
    assert inventory["names"] == ["手動人名", "共同名稱", "儲區客戶"]
    assert inventory["storage_names"] == ["儲區客戶", "共同名稱"]
    assert inventory["storage_count"] == 2
    assert inventory["manual_count"] == 1
    assert inventory["names"].count("共同名稱") == 1
    assert "儲區客戶" in app.CUSTOMER_NAMES
    assert app.collect_visible_protected_names("請處理儲區客戶的材料") == ["儲區客戶"]


def test_storage_upload_automatically_updates_protected_names_and_cleans_duplicates(
    isolated_protected_names,
):
    save_calls, committed_storage = isolated_protected_names
    with app._state_lock:
        app.STORAGE_LOOKUP = {"舊客戶": [["<=3200", "Z99"]]}
        app.extra_names_by_group.clear()
        app.extra_names_by_group.update({
            "__all__": ["手動人名", "手動人名", "客戶乙"],
            "C_TEST": ["群組姓名", "群組姓名"],
        })

    client = app.app.test_client()
    response = client.post(
        "/api/admin/storage/upload",
        data={"file": (_storage_workbook_bytes(), "storage.xlsx")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["count"] == 2
    assert data["protected_added"] == 1
    assert data["storage_protected_count"] == 2
    assert data["duplicates_removed"] == 2
    assert data["protected_count"] == 3
    assert app.STORAGE_LOOKUP == {
        "客戶甲": [["<=3200", "A01"]],
        "客戶乙": [[">3200<=4200", "B02"]],
    }
    assert committed_storage == [app.STORAGE_LOOKUP]
    assert save_calls

    names_response = client.get("/api/admin/names")
    assert names_response.status_code == 200
    inventory = names_response.get_json()
    assert inventory["names"] == ["手動人名", "客戶乙", "客戶甲"]
    assert inventory["names"].count("客戶乙") == 1
    assert inventory["storage_names"] == ["客戶甲", "客戶乙"]
    assert inventory["storage_count"] == 2
    assert inventory["manual_count"] == 1


def test_admin_rejects_duplicate_or_removal_of_storage_managed_name(
    isolated_protected_names,
):
    with app._state_lock:
        app.STORAGE_LOOKUP = {"儲區客戶": [["<=3200", "A01"]]}
        app.extra_names_by_group.clear()
        app.extra_names_by_group["__all__"] = ["手動人名"]
        app.rebuild_customer_names()

    client = app.app.test_client()
    duplicate = client.post(
        "/api/admin/names",
        json={"action": "add", "name": "儲區客戶"},
    ).get_json()
    assert duplicate["ok"] is True
    assert duplicate["added"] is False
    assert duplicate["source"] == "storage"
    assert app.extra_names_by_group["__all__"] == ["手動人名"]

    locked = client.post(
        "/api/admin/names",
        json={"action": "remove", "name": "儲區客戶"},
    ).get_json()
    assert locked["ok"] is False
    assert locked["removed"] is False
    assert locked["locked"] is True
    assert "儲區" in locked["message"]

    first_add = client.post(
        "/api/admin/names",
        json={"action": "add", "name": "新手動姓名"},
    ).get_json()
    second_add = client.post(
        "/api/admin/names",
        json={"action": "add", "name": "新手動姓名"},
    ).get_json()
    assert first_add["added"] is True
    assert second_add["added"] is False
    assert app.extra_names_by_group["__all__"].count("新手動姓名") == 1


def test_admin_ui_explains_storage_sync_and_marks_managed_names():
    assert "儲區客戶會自動同步並去除重複名稱" in app.ADMIN_HTML
    assert "同步客戶名稱至翻譯保護名單（自動去重）" in app.ADMIN_HTML
    assert "📦儲區" in app.ADMIN_HTML
    assert "protected_added" in app.ADMIN_HTML
    assert "loadNames();" in app.ADMIN_HTML
