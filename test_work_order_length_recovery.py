"""Only a confirmed second reading may repair an incomplete work-order length."""

from types import SimpleNamespace

import app
from test_work_order_query import PHOTO_5
from work_order_length_recovery import merge_confirmed_length, needs_length_retry
from work_order_query import extract_work_order_info


FOCUSED = "長度MIN：2500\n長度MAX：2550"


def test_complete_original_never_needs_extra_vision():
    assert not needs_length_retry(PHOTO_5, app.STORAGE_LOOKUP)
    assert merge_confirmed_length(PHOTO_5, FOCUSED, app.STORAGE_LOOKUP) == PHOTO_5


def test_one_unreadable_end_is_recovered_without_affecting_other_fields():
    original = PHOTO_5.replace("長度MAX：2550", "長度MAX：?")
    assert needs_length_retry(original, app.STORAGE_LOOKUP)
    result = merge_confirmed_length(original, FOCUSED, app.STORAGE_LOOKUP)
    info = extract_work_order_info(result, app.STORAGE_LOOKUP, app.PACKAGING_LOOKUP)
    assert info["storage"]["area"] == "EH79"
    assert info["fields"]["length_min"] == "2500"
    assert info["fields"]["length_max"] == "2550"
    assert info["packaging"]["code"] == "9G"
    assert info["paint"]["status"] == "no"
    assert info["ring"]["status"] == "no"


def test_missing_both_ends_recovers_but_conflicting_numeric_original_does_not():
    original = PHOTO_5.replace("長度MIN：2500\n長度MAX：2550\n", "")
    assert extract_work_order_info(merge_confirmed_length(original, FOCUSED, app.STORAGE_LOOKUP),
                                   app.STORAGE_LOOKUP, {})["storage"]["area"] == "EH79"
    conflicting = PHOTO_5.replace("長度MAX：2550", "長度MAX：2600\n長度MIN：?")
    assert needs_length_retry(conflicting, app.STORAGE_LOOKUP)
    assert merge_confirmed_length(conflicting, FOCUSED, app.STORAGE_LOOKUP) == conflicting
    duplicate_conflict = PHOTO_5.replace("長度MAX：2550", "長度MAX：2550\n長度MAX：2600")
    assert needs_length_retry(duplicate_conflict, app.STORAGE_LOOKUP)
    assert merge_confirmed_length(duplicate_conflict, FOCUSED, app.STORAGE_LOOKUP) == duplicate_conflict


def test_ambiguous_second_result_or_changed_admin_rules_cannot_invent_area():
    original = PHOTO_5.replace("長度MAX：2550", "長度MAX：?")
    for retry in ("長度MIN：2500\n長度MAX：?", "長度MIN：2500\n長度MAX：2,550",
                  "長度MIN：2500\n長度MAX：2550\n短邊MIN：2000",
                  "長度MIN：2500\n長度MAX：250"):
        assert merge_confirmed_length(original, retry, app.STORAGE_LOOKUP) == original
    wrong_table = {"方鉦": [[">3200", "EH79"]]}
    assert merge_confirmed_length(original, FOCUSED, wrong_table) == original


def test_ocr_integration_retries_once_only_for_verified_customer_and_missing_length(monkeypatch):
    monkeypatch.setattr(app, "_has_ai_capability", lambda *_: True)
    monkeypatch.setattr(app, "track_tokens", lambda *_: None)
    calls = []

    def vision(messages, **kwargs):
        calls.append((messages, kwargs))
        output = PHOTO_5.replace("長度MAX：2550", "長度MAX：?") if len(calls) == 1 else FOCUSED
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=output))])

    monkeypatch.setattr(app, "_vision_call", vision)
    result = app.ocr_work_order_fields("fake-image")
    assert len(calls) == 2
    assert "長度MIN" in calls[1][0][0]["content"]
    assert extract_work_order_info(result, app.STORAGE_LOOKUP, {})["storage"]["area"] == "EH79"

    calls.clear()

    def already_complete(messages, **kwargs):
        calls.append((messages, kwargs))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=PHOTO_5))])

    monkeypatch.setattr(app, "_vision_call", already_complete)
    assert app.ocr_work_order_fields("fake-image") == PHOTO_5.strip()
    assert len(calls) == 1

    calls.clear()
    unknown_customer = PHOTO_5.replace("客戶名稱：方鉦", "客戶名稱：不在表內")

    def customer_unmapped(messages, **kwargs):
        calls.append((messages, kwargs))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=unknown_customer))])

    monkeypatch.setattr(app, "_vision_call", customer_unmapped)
    assert app.ocr_work_order_fields("fake-image") == unknown_customer.strip()
    assert len(calls) == 1
