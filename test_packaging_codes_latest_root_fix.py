"""Code/row regressions from user photo tests/fixtures/packaging_codes_20260906.jpg."""

from io import BytesIO
import json
from pathlib import Path

import openpyxl
import pytest

import app
from packaging_lookup import find_packaging_matches, format_packaging_reply, packaging_from_rows


DATA = json.loads(Path(__file__).with_name("packaging_data.json").read_text(encoding="utf-8"))
LEGACY = json.loads((Path(__file__).parent / "tests/fixtures/packaging_legacy_20260714.json").read_text(encoding="utf-8"))
EXPECTED_PAIRS = [
    ("U", "1A"), ("1", "2B"), ("5", "3B"), ("2", "2C"),
    ("4", "3C"), ("3", "4C"), ("G", "1D"), ("Q", "5D"),
    ("V", "6D"), ("8", "1M"), ("K", "1E"), ("E", "1F"),
    ("W", "1N"), ("7", "1O"), ("B", "6G"), ("6", "7H"),
    ("H", "8G"), ("X", "8P"), ("C", "9G"), ("Z", "9Q"),
    ("a", "8Q"), ("H", "8I"), ("J", "8J"), ("R", "8K"),
]


@pytest.fixture(autouse=True)
def current_packaging(monkeypatch):
    monkeypatch.setattr(app, "PACKAGING_LOOKUP", DATA)


def test_uploaded_photo_replaces_wrong_customer_storage_table():
    assert len(DATA) == 24
    assert list(DATA) == [new for _old, new in EXPECTED_PAIRS]
    assert "ABE" not in DATA and "大成" not in DATA
    assert all("儲區1" not in entry for entry in DATA.values())


@pytest.mark.parametrize(("old", "new"), EXPECTED_PAIRS)
def test_every_old_new_code_pair_maps_to_the_correct_row(old, new):
    assert DATA[new]["原包裝碼"] == old
    assert DATA[new]["品保設計(新版)"] == new
    assert [key for key, _entry in find_packaging_matches(new, DATA)] == [new]
    old_matches = [key for key, _entry in find_packaging_matches(old, DATA)]
    assert new in old_matches
    if old != "H":
        assert old_matches == [new]
        assert app.handle_pkg_command("/pkg " + old) == app.handle_pkg_command("/pkg " + new)
    assert DATA[new]["簡稱"] in app.handle_pkg_command("/pkg " + new)


def test_u_and_1a_show_identical_real_packaging_instructions():
    by_old = app.handle_pkg_command("/pkg U")
    assert by_old == app.handle_pkg_command("/pkg 1A")
    assert "舊碼：U｜新版：1A" in by_old
    assert "3P袋+瓦楞" in by_old
    assert "PC布墊" in by_old and "瓦楞板" in by_old
    assert "包裝碼資料尚未上傳" not in by_old


def test_duplicate_h_preserves_both_methods_and_requires_new_code_selection():
    reply = app.handle_pkg_command("/pkg H")
    assert "/pkg 8G" in reply and "/pkg 8I" in reply
    assert "木箱+膠膜" in reply and "扁箱+膠膜" in reply
    assert "YIEH" in reply and "2 種" in reply
    assert "扁箱" not in app.handle_pkg_command("/pkg 8G")
    assert "簡稱：扁箱+膠膜" in app.handle_pkg_command("/pkg 8I")
    assert "簡稱：木箱+膠膜" not in app.handle_pkg_command("/pkg 8I")


@pytest.mark.parametrize(("query", "expected"), [
    ("/pkg u", "1A"), ("/pkg 1a", "1A"), ("/pkg Ｕ", "1A"),
    ("/pkg　１Ａ", "1A"), ("/pkg  1 A ", "1A"), ("/pkg A", "8Q"),
    ("/pkg a", "8Q"), ("/PKG 8i", "8I"), ("/pkg 1", "2B"),
    ("/pkg 8", "1M"), ("/pkg 7", "1O"),
])
def test_case_width_and_spaces_never_change_code_identity(query, expected):
    assert app.handle_pkg_command(query) == app.handle_pkg_command("/pkg " + expected)


@pytest.mark.parametrize("code", ["10", "8L", "O", "I", "9", "1AA", "A1", "AB", "EH32", "ABE", "大成"])
def test_unknown_or_confusable_codes_never_select_partial_matches(code):
    assert "找不到包裝碼" in app.handle_pkg_command("/pkg " + code)


def test_known_sling_changes_and_unreadable_photo_details_are_not_invented():
    for code in ("1M", "1O", "9Q", "8Q"):
        assert "EN1492-1吊帶" in app.handle_pkg_command("/pkg " + code)
    assert "未列厚度數值" in app.handle_pkg_command("/pkg 1O")
    assert "內部兩端檔塊" in app.handle_pkg_command("/pkg 7H")
    assert "字樣不清" not in app.handle_pkg_command("/pkg 7H")
    assert "forYIEH" in app.handle_pkg_command("/pkg 8I")


def test_matching_legacy_methods_supply_explicit_inner_outer_and_tie_fields():
    for code, entry in DATA.items():
        if code == "8G":
            continue  # Legacy H describes the flat box, not this ordinary box.
        reference = LEGACY[entry["原包裝碼"]]
        for field in ("內包裝", "外包裝", "固定繩"):
            assert entry[field] == reference[field], (code, field)
            assert field + "：" + reference[field] in app.handle_pkg_command("/pkg " + code)
        assert "BA設計" not in entry


def test_legacy_source_resolves_the_reported_missing_details():
    assert "固定繩：2條棉繩" in app.handle_pkg_command("/pkg U")
    assert "外包裝：PE布+膠膜+木條" in app.handle_pkg_command("/pkg E")
    assert "內部兩端檔塊" in app.handle_pkg_command("/pkg 6")
    for code in ("U", "E", "6"):
        assert "待確認" not in app.handle_pkg_command("/pkg " + code)
    assert "每支棒材整支上網套" in app.handle_pkg_command("/pkg 3")
    assert "每隔1.5米舖PC布" in app.handle_pkg_command("/pkg K")
    for code in ("E", "W", "7"):
        assert "上下左右" not in app.handle_pkg_command("/pkg " + code)


def test_legacy_ba_codes_cannot_override_current_photo_codes():
    assert LEGACY["6"]["BA設計"] == "1F"
    assert "PE布+木條" in app.handle_pkg_command("/pkg 1F")
    assert "檔塊" not in app.handle_pkg_command("/pkg 1F")
    assert LEGACY["H"]["BA設計"] == "8G"
    assert "扁箱" not in app.handle_pkg_command("/pkg 8G")
    assert "YIEH" not in app.handle_pkg_command("/pkg 8G")
    assert "固定繩" not in DATA["8G"]  # No supported rope count for this row.
    assert "YIEH" in app.handle_pkg_command("/pkg 8I")
    assert "找不到包裝碼" in app.handle_pkg_command("/pkg 58")


def test_excel_import_indexes_both_columns_and_retains_duplicate_legacy_code():
    rows = [
        ("原包裝碼", "品保設計\n(新版)", "簡稱", "詳細包裝方式說明"),
        ("U", "1A", "3P袋+瓦楞", "U方式"),
        ("H", "8G", "木箱+膠膜", "木箱方式"),
        ("H", "8I", "扁箱+膠膜", "特殊扁箱forYIEH"),
        (1, "2B", "3P袋+木條+瓦楞", "數字舊碼方式"),
    ]
    imported, _header = packaging_from_rows(rows)
    assert len(imported) == 4
    assert format_packaging_reply("/pkg U", imported) == format_packaging_reply("/pkg 1A", imported)
    assert len(find_packaging_matches("H", imported)) == 2
    assert find_packaging_matches("1", imported)[0][0] == "2B"


def test_excel_reimport_of_all_current_rows_preserves_aliases():
    headers = list(dict.fromkeys(field for entry in DATA.values() for field in entry))
    rows = [headers] + [[entry.get(field) for field in headers] for entry in DATA.values()]
    imported, _header = packaging_from_rows(rows)
    assert imported == DATA


@pytest.mark.parametrize("rows", [
    [("客戶名稱", "訂單長度", "儲區1"), ("ABE", ">4200", "EG34")],
    [("code", "儲區1"), ("U", "EG34")],
    [("姓名", "簡稱"), ("U", "3P袋")],
    [("原包裝碼", "品保設計(新版)", "簡稱"), ("U", "1A", "")],
    [("原包裝碼", "品保設計(新版)", "簡稱"), ("U", "1A", "甲"), ("V", "1a", "乙")],
    [("原包裝碼", "簡稱"), ("H", "木箱"), ("H", "扁箱")],
])
def test_wrong_tables_and_conflicting_codes_cannot_replace_packaging(rows):
    with pytest.raises(ValueError):
        packaging_from_rows(rows)


def _xlsx(rows):
    wb = openpyxl.Workbook()
    for row in rows:
        wb.active.append(row)
    data = BytesIO()
    wb.save(data)
    wb.close()
    data.seek(0)
    return data


def test_admin_upload_rejects_storage_sheet_without_mutation_or_commit(monkeypatch):
    monkeypatch.setattr(app, "check_manager_access", lambda *_a: True)
    commits = []
    monkeypatch.setattr(app, "commit_packaging_to_github", lambda data: commits.append(data) or True)
    before = app.PACKAGING_LOOKUP
    response = app.app.test_client().post("/api/admin/packaging/upload", data={
        "file": (_xlsx([("客戶名稱", "訂單長度", "儲區1"), ("ABE", ">4200", "EG34")]), "storage.xlsx")
    })
    assert response.status_code == 400
    assert "儲區表不能匯入" in response.get_json()["error"]
    assert app.PACKAGING_LOOKUP is before
    assert not commits


def test_admin_upload_and_export_keep_both_h_rows(monkeypatch):
    monkeypatch.setattr(app, "check_manager_access", lambda *_a: True)
    commits = []
    monkeypatch.setattr(app, "commit_packaging_to_github", lambda data: commits.append(json.loads(data)) or True)
    rows = [("原包裝碼", "品保設計(新版)", "簡稱"), ("H", "8G", "木箱+膠膜"), ("H", "8I", "扁箱+膠膜")]
    client = app.app.test_client()
    response = client.post("/api/admin/packaging/upload", data={"file": (_xlsx(rows), "packaging.xlsx")})
    assert response.status_code == 200
    assert response.get_json()["count"] == 2
    assert list(commits[0]) == ["8G", "8I"]
    exported = client.get("/api/admin/packaging/json").get_json()
    assert exported == commits[0]
    assert "2 種" in app.handle_pkg_command("/pkg H")
