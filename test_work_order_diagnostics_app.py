"""Exercise the live OCR-to-card trace and the admin troubleshooting endpoint."""

import base64
import io
import json
import re
from types import SimpleNamespace

import app
from PIL import Image
from work_order_diagnostics import load_diagnostics


INITIAL_OCR = """冷精棒製造指示書
訂單編號：Y1223786-008
客戶名稱：佳東
成品尺寸MIN：?
成品尺寸MAX：?
長度MIN：6000
長度MAX：6050
訂單流程：?
噴漆位置：?
套環：N
顏色：土藍
包裝代碼：1O
特殊備註：
"""
REREAD = "訂單流程：CHRAPL\n成品尺寸MIN：20\n成品尺寸MAX：20"
CELL_REREAD = "噴漆位置：雙邊\n套環：N\n包裝代碼：1O"


def test_app_records_retried_ring_inputs_and_rendered_card_without_raw_order(
        monkeypatch, tmp_path):
    path = tmp_path / "work_order_diagnostics.json"
    monkeypatch.setenv("WORK_ORDER_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(app._tl, "work_order_message_id", "line_msg_123", raising=False)
    monkeypatch.setattr(app, "_has_ai_capability", lambda *_: True)
    monkeypatch.setattr(app, "track_tokens", lambda *_: None)
    monkeypatch.setattr(app, "_work_order_storage_ocr_diagnostic", lambda *_args, **_kw: None)
    monkeypatch.setattr(app, "translate", lambda *_args, **_kw: "")
    calls = []

    def fake_vision(messages, **options):
        calls.append((messages, options))
        content = {1: INITIAL_OCR, 2: REREAD, 3: CELL_REREAD}[len(calls)]
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=content))])

    monkeypatch.setattr(app, "_vision_call", fake_vision)
    image = Image.new("RGB", (400, 900), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    image_base64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    result = app.ocr_work_order_fields(image_base64)
    assert len(calls) == 3
    first_prompt = calls[0][0][0]["content"]
    assert "看見『土藍』就保留『土藍』" in first_prompt
    assert "完整色名或明確列出的別名查詢" in first_prompt
    assert sum(item["type"] == "image_url" for item in calls[0][0][1]["content"]) == 3
    assert "訂單流程：CHRAPL" in result
    assert "成品尺寸MIN：20" in result
    assert "成品尺寸MAX：20" in result
    assert "噴漆位置：雙邊" in result
    assert app.format_work_order_cards(result)["messages"][0]["type"] == "flex"

    # The stages let an operator distinguish failed transcription from a
    # failed reread, and show whether the final rule matched the rendered card.
    records = list(reversed(load_diagnostics(limit=20, msg_id="line_msg_123")))
    assert [record["stage"] for record in records] == [
        "initial", "ring_retry_candidate", "ring_retry_result",
        "cell_retry_candidate", "cell_retry_result",
        "final_ocr", "card_rendered",
    ]
    assert records[0]["ring_status"] == "unknown"
    assert records[2]["ring_status"] == "yes"
    assert records[5]["ring_retry_triggered"] is True
    assert records[5]["ring_retry_accepted"] is True
    assert records[5]["cell_retry_triggered"] is True
    assert records[5]["cell_retry_accepted"] is True
    assert records[-1]["ring_status"] == "yes"
    assert records[-1]["flow_ocr"] == "CHRAPL"
    assert records[0]["timestamp_utc"].endswith("Z")
    assert records[-1]["storage_area"] == "EH70"
    assert all(record["message_id"] == "line_msg_123" for record in records)
    assert len(records[-1]["ocr_sha256"]) == 64
    assert load_diagnostics(msg_id="another_message") == []

    stored = path.read_text(encoding="utf-8")
    assert "Y1223786-008" not in stored
    assert INITIAL_OCR not in stored
    assert "冷精棒製造指示書" not in stored
    assert all("ocr_text" not in record and "order_number" not in record
               for record in json.loads(stored))


def test_admin_endpoint_requires_access_and_exposes_live_storage_with_fingerprints(
        monkeypatch, tmp_path):
    monkeypatch.setenv("WORK_ORDER_DIAGNOSTICS_PATH", str(tmp_path / "empty.json"))
    monkeypatch.setattr(app, "ADMIN_KEY", "work-order-trace-test-key")
    client = app.app.test_client()
    path = "/api/admin/work-order-diagnostics?customer=方鉦&limit=3"
    assert client.get(path).status_code == 403
    assert client.get(path, headers={"X-Admin-Key": "invalid"}).status_code == 403

    response = client.get(path, headers={"X-Admin-Key": app.ADMIN_KEY})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    data = response.get_json()
    assert data["ok"] is True
    assert data["build"] == app._WORK_ORDER_DIAGNOSTIC_BUILD
    assert data["diagnostics_writable"] is True
    assert data["retention"] == 500
    assert data["records"] == []
    assert data["storage"]["live_customer"] == "方鉦"
    assert data["storage"]["effective_customer"] == "方鉦"
    assert data["storage"]["source"] == "live"
    assert data["storage"]["live_rows"] == app.STORAGE_LOOKUP["方鉦"]
    assert data["storage"]["effective_rows"] == app._work_order_storage_lookup()["方鉦"]
    assert data["storage"]["live_rows"][0] == ["<=3200", "EH79"]
    assert all(re.fullmatch(r"[0-9a-f]{16}", data["fingerprints"][name])
               for name in ("app.py", "work_order_query.py", "storage_data.json"))
