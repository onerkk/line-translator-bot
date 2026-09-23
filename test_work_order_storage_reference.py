"""Regression for stale or incomplete live storage tables in work-order cards."""

from decimal import Decimal
from types import SimpleNamespace

import app
import pytest
from test_work_order_query import PHOTO_5
from work_order_detection import _key, resolve_storage_customer
from work_order_query import extract_work_order_info, _resolve_storage
from work_order_storage_reference import STORAGE_REFERENCE, effective_work_order_storage_lookup


def _visible(card):
    if isinstance(card, dict):
        return " ".join(
            [str(card.get("text", ""))]
            + [_visible(item) for item in card.values() if isinstance(item, (dict, list))]
        )
    if isinstance(card, list):
        return " ".join(_visible(item) for item in card)
    return ""


def test_verified_reference_contains_the_original_customer_and_short_length_band():
    assert len(STORAGE_REFERENCE) == 398
    assert STORAGE_REFERENCE["方鉦"][0] == ["<=3200", "EH79"]
    assert STORAGE_REFERENCE["俊益"][0] == ["<=3200", "EH79"]
    assert _resolve_storage("方鉦", (Decimal(2500), Decimal(2550)),
                            STORAGE_REFERENCE)["area"] == "EH79"


def test_missing_live_customer_uses_reference_without_mutating_admin_data(monkeypatch):
    live = {"俊益": [["<=3200", "NEW01"]]}
    monkeypatch.setattr(app, "STORAGE_LOOKUP", live)
    monkeypatch.setattr(app, "translate", lambda source, *args, **kwargs: source)
    result = app.format_work_order_cards(PHOTO_5)
    rendered = _visible(result["messages"])
    assert "方鉦" in rendered and "EH79" in rendered
    assert "儲區待確認" not in rendered
    assert app.STORAGE_LOOKUP == live
    assert _resolve_storage("俊益", (Decimal(3000), Decimal(3050)),
                            app._work_order_storage_lookup())["area"] == "NEW01"
    assert "未知客戶" not in app._work_order_storage_lookup()


def test_verifiably_stale_operator_corrected_without_overriding_admin_remap():
    reference = {"方鉦": [["<=3200", "EH79"], [">3200<=4200", "EH72"],
                         [">4200", "EH72"]]}
    stale = {"方鉦": [[">=3200", "EH79"], [">3200<=4200", "EH72"],
                    [">4200", "EH72"]]}
    lookup = effective_work_order_storage_lookup(stale, reference)
    assert lookup["方鉦"][0] == ["<=3200", "EH79"]
    assert stale["方鉦"][0] == [">=3200", "EH79"]
    assert _resolve_storage("方鉦", (Decimal(2500), Decimal(2550)), lookup)["area"] == "EH79"

    custom = {"方鉦": [[">=3200", "NEW01"], [">3200<=4200", "EH72"],
                     [">4200", "EH72"]]}
    assert effective_work_order_storage_lookup(custom, reference)["方鉦"] == custom["方鉦"]
    explicit_absence = {"方鉦": []}
    assert effective_work_order_storage_lookup(explicit_absence, reference)["方鉦"] == []


@pytest.mark.parametrize("live_name", ("sungeun", "SUN GEUN", "ＳＵＮＧＥＵＮ"))
def test_existing_live_customer_case_or_width_variant_overrides_reference(live_name):
    # Reference SUNGEUN maps >4200 to EG33, while the admin has updated it.
    live = {live_name: [[">4200", "CUSTOM"]]}
    merged = effective_work_order_storage_lookup(live)
    assert "SUNGEUN" not in merged or live_name == "SUNGEUN"
    assert len([name for name in merged if _key(name) == "sungeun"]) == 1
    expected = {"status": "ok", "area": "CUSTOM", "customer": live_name}
    assert _resolve_storage("SUNGEUN", (Decimal(6000), Decimal(6050)), merged) == expected
    assert _resolve_storage("SUNGE", (Decimal(6000), Decimal(6050)), merged) == expected
    assert live[live_name] == [[">4200", "CUSTOM"]]


def test_verified_ocr_alias_is_safe_and_canonical_in_card(monkeypatch):
    assert resolve_storage_customer("方钲", STORAGE_REFERENCE) == "方鉦"
    assert resolve_storage_customer("方錠", STORAGE_REFERENCE) is None
    assert resolve_storage_customer("方钲", {"方钲": []}) == "方钲"
    assert resolve_storage_customer("方钲", {"無關客戶": []}) is None
    monkeypatch.setattr(app, "STORAGE_LOOKUP", {"無關客戶": [["<=3200", "E01"]]})
    monkeypatch.setattr(app, "translate", lambda source, *args, **kwargs: source)
    card = app.format_work_order_cards(PHOTO_5.replace("客戶名稱：方鉦", "客戶名稱：方钲"))
    visible = _visible(card["messages"])
    assert "方鉦" in visible and "EH79" in visible
    assert "方钲" not in visible


def test_missing_live_customer_still_allows_confirmed_length_reread(monkeypatch):
    monkeypatch.setattr(app, "STORAGE_LOOKUP", {"其他客戶": [["<=3200", "E01"]]})
    monkeypatch.setattr(app, "_has_ai_capability", lambda *_: True)
    monkeypatch.setattr(app, "track_tokens", lambda *_: None)
    monkeypatch.setattr(app, "_event_log_write", lambda *_: None)
    calls = []

    def vision(*_args, **_kwargs):
        calls.append(1)
        output = (PHOTO_5.replace("長度MAX：2550", "長度MAX：?") if len(calls) == 1
                  else "長度MIN：2500\n長度MAX：2550")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=output))])

    monkeypatch.setattr(app, "_vision_call", vision)
    text = app.ocr_work_order_fields("fake-image")
    assert len(calls) == 2
    assert extract_work_order_info(text, app._work_order_storage_lookup(), {})["storage"]["area"] == "EH79"
