"""The backend accepts the three A/B/C bands in the storage system export."""

import io
import json
from decimal import Decimal
from types import SimpleNamespace

import openpyxl
import pytest

import app
import work_order_storage_reference
from storage_import import normalize_storage_lookup, parse_storage_ag_grid_rows
from work_order_query import _resolve_storage


HEADERS = ["客戶名稱", "訂單長度", "儲區1", "儲區2", "儲區3", "儲區4"]


def test_ag_grid_band_boundaries_and_repeated_customer_rows():
    rows = [HEADERS, ["方鉦", "A", "EH79", None, None, None],
            ["方鉦", "B", "EH72", None, None, None],
            ["方鉦", "C", "EH72", None, None, None],
            ["常州眾山", "A", "EC47", None, None, None],
            ["常州眾山", "A", "EH28", None, None, None]]
    lookup = parse_storage_ag_grid_rows(rows)
    assert lookup["方鉦"] == [
        ["<=3200", "EH79"], [">3200<=4200", "EH72"], [">4200", "EH72"],
    ]
    for length, expected in [(3200, "EH79"), (3200.01, "EH72"),
                             (4200, "EH72"), (4200.01, "EH72")]:
        result = _resolve_storage("方鉦", (Decimal(str(length)),) * 2, lookup)
        assert (result["status"], result["area"]) == ("ok", expected)
    assert _resolve_storage("方鉦", (Decimal(3199), Decimal(3201)), lookup)["status"] == "ambiguous_mapping"
    assert lookup["常州眾山"] == [["<=3200", "EC47"], ["<=3200", "EH28"]]
    assert _resolve_storage("常州眾山", (Decimal(2000), Decimal(2500)), lookup)["status"] == "ambiguous_mapping"


def test_ag_grid_joins_all_areas_without_losing_order_and_ignores_empty_rows():
    rows = [HEADERS, ["SUNGEUN", "B", "EG14", "EH15", "EG14", None],
            ["", "A", "E01", None, None, None],
            ["空客戶", "A", None, None, None, None]]
    assert parse_storage_ag_grid_rows(rows) == {
        "SUNGEUN": [[">3200<=4200", "EG14、EH15"]],
    }
    assert parse_storage_ag_grid_rows([["客戶", "<=3200"], ["客戶甲", "E01"]]) is None
    with pytest.raises(ValueError, match="儲區1"):
        parse_storage_ag_grid_rows([["客戶名稱", "訂單長度", "儲區2"], ["甲", "A", "E02"]])
    assert parse_storage_ag_grid_rows([HEADERS, ["全形客戶", "Ｃ", "E03"]]) == {
        "全形客戶": [[">4200", "E03"]],
    }


@pytest.mark.parametrize("letter", ["D", "?", "", "3200"])
def test_ag_grid_rejects_unknown_length_code(letter):
    with pytest.raises(ValueError, match="A、B、C"):
        parse_storage_ag_grid_rows([HEADERS, ["客戶", letter, "EH79", None, None, None]])


def test_old_literal_bands_normalize_without_changing_admin_area():
    original = {"方鉦": [["A", "ADMIN-1"], ["b", "ADMIN-2"],
                       ["Ｃ", "EH72"], [">4000", "OTHER"]]}
    normalized = normalize_storage_lookup(original)
    assert normalized == {"方鉦": [["<=3200", "ADMIN-1"],
                                 [">3200<=4200", "ADMIN-2"],
                                 [">4200", "EH72"], [">4000", "OTHER"]]}
    assert original["方鉦"][0] == ["A", "ADMIN-1"]


def test_old_in_memory_table_works_for_work_order_and_qry(monkeypatch):
    live = {"方鉦": [["A", "CUSTOM01"], ["B", "CUSTOM02"], ["C", "CUSTOM03"]]}
    monkeypatch.setattr(app, "STORAGE_LOOKUP", live)
    result = _resolve_storage("方鉦", (Decimal(2500), Decimal(2550)),
                              app._work_order_storage_lookup())
    assert (result["status"], result["area"]) == ("ok", "CUSTOM01")
    query = app.handle_qry_command("/qry 方鉦")
    assert "3200以下（含） → CUSTOM01" in query
    assert "3200～4200 → CUSTOM02" in query
    assert "超過4200 → CUSTOM03" in query
    assert live["方鉦"][0][0] == "A"


def _post_storage_rows(monkeypatch, rows, reference=None):
    """Exercise the upload route without keeping a generated Excel artifact."""
    committed = []
    monkeypatch.setattr(
        work_order_storage_reference,
        "STORAGE_REFERENCE",
        {} if reference is None else reference,
    )
    monkeypatch.setattr(app, "STORAGE_LOOKUP", {"原客戶": [["<=3200", "OLD"]]})
    monkeypatch.setattr(app, "check_manager_access", lambda *_: True)
    monkeypatch.setattr(app, "protected_name_inventory", lambda: {
        "names": [], "storage_names": [], "storage_count": 0, "count": 0,
    })
    monkeypatch.setattr(app, "rebuild_customer_names", lambda: {})
    monkeypatch.setattr(app, "commit_storage_to_github",
                        lambda payload: committed.append(json.loads(payload)) or True)
    fake_sheet = SimpleNamespace(iter_rows=lambda **_: iter(rows))
    monkeypatch.setattr(openpyxl, "load_workbook", lambda *_args, **_kw: SimpleNamespace(active=fake_sheet))
    response = app.app.test_client().post(
        "/api/admin/storage/upload",
        data={"file": (io.BytesIO(b"mocked excel"), "storage.xlsx")},
        content_type="multipart/form-data",
    )
    return response, committed


def test_admin_upload_accepts_ag_grid_and_persists_numeric_conditions(monkeypatch):
    rows = [HEADERS, ["方鉦", "A", "EH79", None, None, None],
            ["方鉦", "B", "EH72", None, None, None],
            ["方鉦", "C", "EH72", None, None, None],
            ["SUNGEUN", "B", "EG14", "EH15", None, None]]
    response, committed = _post_storage_rows(monkeypatch, rows)
    assert response.status_code == 200
    assert response.get_json()["count"] == 2
    assert committed == [app.STORAGE_LOOKUP]
    assert app.STORAGE_LOOKUP["SUNGEUN"] == [[">3200<=4200", "EG14、EH15"]]
    assert _resolve_storage("方鉦", (Decimal(3200), Decimal(3200)),
                            app._work_order_storage_lookup())["area"] == "EH79"


def test_admin_upload_restores_omitted_verified_customer_before_persisting(monkeypatch):
    reference = {
        "方鉦": [["<=3200", "EH79"], [">3200<=4200", "EH72"], [">4200", "EH72"]],
    }
    rows = [HEADERS, ["新客戶", "A", "NEW01", None, None, None]]
    response, committed = _post_storage_rows(monkeypatch, rows, reference)

    assert response.status_code == 200
    assert response.get_json()["count"] == 2
    assert app.STORAGE_LOOKUP["方鉦"] == reference["方鉦"]
    assert committed[0]["方鉦"] == reference["方鉦"]


def test_admin_upload_rejects_unrecognized_letter_before_mutation(monkeypatch):
    response, committed = _post_storage_rows(monkeypatch, [HEADERS, ["方鉦", "D", "EH79"]])
    assert response.status_code == 400
    assert "A、B、C" in response.get_json()["error"]
    assert app.STORAGE_LOOKUP == {"原客戶": [["<=3200", "OLD"]]}
    assert committed == []
