"""Customer-column mismatch recovery protects work-order storage routing."""

from types import SimpleNamespace

import app
from work_order_customer_recovery import (focused_customer_crop,
                                          merge_confirmed_customer,
                                          needs_customer_retry,
                                          withhold_conflicting_customer)
from work_order_query import extract_work_order_info
from work_order_storage_reference import STORAGE_REFERENCE


INITIAL = """冷精棒製造指示書
訂單編號：Y1223786-008
客戶名稱：方鉦
收貨人：DACAPO
成品尺寸MIN：17.957
成品尺寸MAX：18
長度MIN：6000
長度MAX：6050
訂單流程：CHRAPDGL
噴漆位置：雙邊
顏色：土藍
包裝代碼：1O
"""
RECOVERED = "客戶名稱：DACAPO\n收貨人：DACAPO"


def test_customer_recipient_conflict_never_routes_to_either_area_until_verified():
    original = extract_work_order_info(INITIAL, STORAGE_REFERENCE, {})
    assert original["customer"] == "方鉦"
    assert original["recipient"] == "DACAPO"
    assert original["customer_conflict"] is True
    assert original["storage"] == {"status": "unknown_customer"}
    assert needs_customer_retry(INITIAL, STORAGE_REFERENCE)

    repaired = merge_confirmed_customer(INITIAL, RECOVERED, STORAGE_REFERENCE)
    result = extract_work_order_info(repaired, STORAGE_REFERENCE, {})
    assert result["customer"] == "DACAPO"
    assert result["customer_conflict"] is False
    assert result["storage"] == {
        "status": "ok", "area": "EH31", "customer": "DACAPO",
    }
    assert "客戶名稱：方鉦" not in repaired


def test_unmapped_misread_can_recover_when_the_focused_cells_agree():
    original = INITIAL.replace("客戶名稱：方鉦", "客戶名稱：看不清")
    repaired = merge_confirmed_customer(original, RECOVERED, STORAGE_REFERENCE)
    info = extract_work_order_info(repaired, STORAGE_REFERENCE, {})
    assert info["customer"] == "DACAPO"
    assert info["storage"]["area"] == "EH31"


def test_failed_or_inconsistent_focused_read_fails_closed():
    for retry in (
        "客戶名稱：?\n收貨人：DACAPO",
        "客戶名稱：另一客戶\n收貨人：另一客戶",
        "客戶名稱：DACAPO\n收貨人：方鉦",
    ):
        assert merge_confirmed_customer(INITIAL, retry, STORAGE_REFERENCE) == INITIAL

    pending = withhold_conflicting_customer(INITIAL, STORAGE_REFERENCE)
    result = extract_work_order_info(pending, STORAGE_REFERENCE, {})
    assert result["customer"] is None
    assert result["storage"]["status"] == "unknown_customer"
    assert "EH72" not in str(result["storage"])
    assert "EH31" not in str(result["storage"])


def test_focused_reread_can_confirm_a_legitimate_customer_recipient_difference():
    # The retry reads both labeled cells and confirms the actual customer even
    # when the consignee is a different valid customer name.
    retry = "客戶名稱：方鉦\n收貨人：DACAPO"
    repaired = merge_confirmed_customer(INITIAL, retry, STORAGE_REFERENCE)
    result = extract_work_order_info(repaired, STORAGE_REFERENCE, {})
    assert result["customer"] == "方鉦"
    assert result["storage"]["area"] == "EH72"


def test_customer_crop_targets_header_cells_on_landscape_and_portrait_images():
    from pathlib import Path
    import base64

    from PIL import Image
    import io

    fixture = Path(__file__).parent / "tests" / "fixtures" / "work_order_20260906.jpg"
    photo = base64.b64encode(fixture.read_bytes()).decode("ascii")
    crop = focused_customer_crop(photo)
    assert crop is not None
    with Image.open(io.BytesIO(base64.b64decode(crop))) as image:
        assert image.width > 500 and image.height > 150


def test_app_uses_one_focused_customer_retry_then_emits_the_reference_area(monkeypatch):
    monkeypatch.setattr(app, "_has_ai_capability", lambda *_: True)
    monkeypatch.setattr(app, "track_tokens", lambda *_: None)
    monkeypatch.setattr(app, "_work_order_storage_ocr_diagnostic", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_event_log_write", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "translate", lambda source, *_a, **_k: source)
    calls = []

    def vision(messages, **_options):
        calls.append(messages)
        content = INITIAL if len(calls) == 1 else RECOVERED
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=content))])

    monkeypatch.setattr(app, "_vision_call", vision)
    result = app.ocr_work_order_fields("not-a-real-photo")
    assert len(calls) == 2
    assert "客戶名稱：DACAPO" in result
    assert "客戶名稱：方鉦" not in result
    assert "客戶名稱" in calls[1][0]["content"]
    info = extract_work_order_info(result, app._work_order_storage_lookup(), {})
    assert info["storage"]["area"] == "EH31"
    card = app.format_work_order_cards(result)
    assert "DACAPO" in str(card)
    assert "EH31" in str(card)
    assert "儲區待確認" not in str(card)
