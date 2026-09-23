"""Regression checks for redacted, durable work-order troubleshooting records."""

import json
import multiprocessing
import os
import re
from pathlib import Path

import pytest

import work_order_diagnostics as diagnostics


_STORAGE = {
    "DACAPO": [["<=3200", "EH25、EH26"], [">3200<=4200", "EG14"], [">4200", "EH31"]],
    "方鉦": [["<=3200", "CUSTOM01"], [">3200<=4200", "CUSTOM02"], [">4200", "CUSTOM03"]],
}
_SHEET = """冷精棒製造指示書
訂單編號：Y1223786-008
客戶名稱：DACAPO
成品尺寸MIN：17.957
成品尺寸MAX：18
長度MIN：6000
長度MAX：6050
訂單流程：CHRAPDGL
噴漆位置：雙邊
包裝代碼：1O
套環：Y
"""


def _simple_record(message_id):
    return {"message_id": str(message_id), "stage": "final", "storage_status": "unknown_length",
            "storage_reason": "unknown_length", "ring_status": "unknown", "ring_reason": "flow"}


def _process_writer(prefix, number):
    for index in range(number):
        assert diagnostics.append_diagnostic(_simple_record(f"{prefix}{index}"))


def test_photo_decision_traces_source_rows_and_ignores_unapproved_text(monkeypatch, tmp_path):
    path = tmp_path / "diagnostics.json"
    monkeypatch.setenv("WORK_ORDER_DIAGNOSTICS_PATH", str(path))
    record = diagnostics.make_diagnostic(
        _SHEET, _STORAGE, {"1O": {"原包裝碼": "7"}}, "123456789", "final", "build7",
        {"length_retry_triggered": False, "length_retry_accepted": False,
         "ring_retry_triggered": True, "ring_retry_accepted": True,
         "photo": "DONT_WRITE_PHOTO", "order": "DONT_WRITE_ORDER"},
    )
    assert record["customer_ocr"] == record["customer_canonical"] == "DACAPO"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", record["timestamp_utc"])
    assert record["flow_ocr"] == "CHRAPDGL"
    assert record["diameter_min"] == "17.957"
    assert record["diameter_max"] == "18"
    assert record["length_min"] == "6000"
    assert record["length_max"] == "6050"
    assert record["storage_rows"] == _STORAGE["DACAPO"]
    assert record["storage_status"] == "ok"
    assert record["storage_reason"] == "unique_area"
    assert record["storage_area"] == "EH31"
    assert record["ring_status"] == "yes"
    assert record["ring_reason"] == "form_y"
    assert record["ring_retry_triggered"] is True
    assert len(record["ocr_sha256"]) == 64
    assert len(record["storage_table_sha256"]) == 64
    assert len(record["packaging_table_sha256"]) == 64
    assert diagnostics.append_diagnostic(record)
    assert diagnostics.load_diagnostics(limit=1, msg_id="123456789") == [record]
    stored = path.read_text(encoding="utf-8")
    assert "Y1223786" not in stored
    assert "冷精棒製造指示書" not in stored
    assert "DONT_WRITE" not in stored
    assert "包裝代碼" not in stored
    assert path.stat().st_mode & 0o777 == 0o600


def test_unknown_and_partially_read_fields_still_record(monkeypatch, tmp_path):
    monkeypatch.setenv("WORK_ORDER_DIAGNOSTICS_PATH", str(tmp_path / "partial.json"))
    record = diagnostics.make_diagnostic(
        "冷精棒製造指示書\n客戶名稱：方鉦\n訂單流程：?\n長度MIN：2500\n長度MAX：?",
        _STORAGE, {}, "short123", "initial", "build7",
        {"length_retry_triggered": True, "length_retry_accepted": False},
    )
    assert record["customer_canonical"] == "方鉦"
    assert record["length_min"] == "2500"
    assert record["length_max"] is None
    assert record["storage_status"] == "unknown_length"
    assert record["storage_reason"] == "missing_or_unreadable_length"
    assert record["storage_rows"] == _STORAGE["方鉦"]
    assert record["ring_status"] == "unknown"
    assert diagnostics.append_diagnostic(record)
    assert diagnostics.load_diagnostics(msg_id="short123")[0]["length_retry_triggered"] is True


def test_write_whitelist_filters_credentials_even_for_direct_call(monkeypatch, tmp_path):
    path = tmp_path / "diagnostics.json"
    monkeypatch.setenv("WORK_ORDER_DIAGNOSTICS_PATH", str(path))
    raw = {
        **_simple_record("safe123"), "line_key": "SECRETLINETOKEN", "ocr_text": "SECRET_OCR",
        "customer_ocr": "DACAPO\nSECRET_OCR", "flow_ocr": "CHRAPDGL\nSECRET_OCR",
        "storage_rows": [["<=3200", "CUSTOM01"], ["order: Y1223786-008", "SECRET_OCR"]],
        "storage_table_sha256": "bad hash", "ring_retry_triggered": "true",
    }
    assert diagnostics.append_diagnostic(raw)
    data = json.loads(path.read_text(encoding="utf-8"))[0]
    assert "SECRET" not in json.dumps(data)
    assert data["customer_ocr"] is None
    assert data["flow_ocr"] is None
    assert data["ring_retry_triggered"] is None
    assert data["storage_rows"] == [["<=3200", "CUSTOM01"]]
    assert data["storage_table_sha256"] is None
    assert not diagnostics.append_diagnostic({"message_id": "unsafe\n123", "secret": "ignored"})
    assert diagnostics.load_diagnostics(msg_id="unsafe\n123") == []


def test_misplaced_order_id_is_not_saved_as_customer_or_flow(monkeypatch, tmp_path):
    path = tmp_path / "wrong_column.json"
    monkeypatch.setenv("WORK_ORDER_DIAGNOSTICS_PATH", str(path))
    raw = ("冷精棒製造指示書\n客戶名稱：Y1223786-008\n"
           "訂單流程：Y1223786-008\n長度MIN：1223786008\n")
    record = diagnostics.make_diagnostic(raw, _STORAGE, {}, "safe-msg", "initial", "build7")
    assert record["customer_ocr"] is None
    assert record["flow_ocr"] is None
    assert record["length_min"] is None
    assert diagnostics.append_diagnostic(record)
    assert "Y1223786-008" not in path.read_text(encoding="utf-8")
    assert "1223786008" not in path.read_text(encoding="utf-8")


def test_bad_recording_path_is_reported_in_health(monkeypatch, tmp_path):
    monkeypatch.setenv("WORK_ORDER_DIAGNOSTICS_PATH", str(tmp_path))
    assert diagnostics.diagnostics_health()["writable"] is False
    assert diagnostics.append_diagnostic(_simple_record("safe-msg")) is False


def test_bounded_retention_and_corrupt_file_do_not_break_caller(monkeypatch, tmp_path):
    path = tmp_path / "diag.json"
    monkeypatch.setenv("WORK_ORDER_DIAGNOSTICS_PATH", str(path))
    path.write_text("broken", encoding="utf-8")
    assert diagnostics.load_diagnostics() == []
    for index in range(503):
        assert diagnostics.append_diagnostic(_simple_record(str(index)))
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 500
    assert diagnostics.load_diagnostics(limit=100)[0]["message_id"] == "502"
    assert diagnostics.load_diagnostics(limit=500)[-1]["message_id"] == "3"
    assert diagnostics.load_diagnostics(msg_id="0") == []
    assert diagnostics.load_diagnostics(msg_id="101")[0]["message_id"] == "101"


@pytest.mark.skipif(os.name != "posix", reason="flock is required for worker serialization")
def test_two_workers_append_without_lost_records(monkeypatch, tmp_path):
    monkeypatch.setenv("WORK_ORDER_DIAGNOSTICS_PATH", str(tmp_path / "workers.json"))
    workers = [multiprocessing.get_context("fork").Process(target=_process_writer, args=(prefix, 18))
               for prefix in ("one", "two")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)
        assert worker.exitcode == 0
    saved = diagnostics.load_diagnostics(limit=100)
    assert len(saved) == 36
    assert len({row["message_id"] for row in saved}) == 36


def test_io_failure_cannot_block_translation(monkeypatch, tmp_path):
    monkeypatch.setenv("WORK_ORDER_DIAGNOSTICS_PATH", str(tmp_path))  # directory, not file
    assert diagnostics.append_diagnostic(_simple_record("123")) is False
    assert diagnostics.load_diagnostics() == []
