"""A magnified second reading repairs only genuinely missing ring inputs.

The photo transcription below was checked manually against the user's DACAPO
work order.  The tests exercise local merge rules, not a vision service.
"""

import base64
import io
from pathlib import Path
from types import SimpleNamespace

import app
from PIL import Image
import pytest

from test_work_order_ring_photo import PHOTO_DACAPO
from work_order_query import extract_work_order_info
from work_order_ring_recovery import (focused_ring_crop,
                                      merge_confirmed_ring_fields,
                                      needs_ring_retry)


FOCUSED = "訂單流程：CHRAPDGL\n成品尺寸MIN：17.957\n成品尺寸MAX：18"


@pytest.mark.parametrize("printed", ["Y", "N", "?", ""])
def test_missing_flow_and_size_are_recovered_without_using_form_y_n(printed):
    original = (PHOTO_DACAPO.replace("訂單流程：CHRAPDGL", "訂單流程：?")
                .replace("成品尺寸MIN：17.957", "成品尺寸MIN：?")
                .replace("成品尺寸MAX：18", "成品尺寸MAX：?")
                .replace("套環：Y", f"套環：{printed}"))
    assert needs_ring_retry(original)
    result = merge_confirmed_ring_fields(original, FOCUSED)
    assert result != original
    info = extract_work_order_info(result, {}, {})
    assert info["fields"]["flow"] == "CHRAPDGL"
    assert info["fields"]["diameter_min"] == "17.957"
    assert info["fields"]["diameter_max"] == "18"
    assert info["ring"] == {"status": "yes", "reason": "size_rule",
                            "process": "grinding", "threshold": 16}
    assert info["fields"]["ring_on_form"] == (printed if printed in {"Y", "N"} else None)


def test_known_gl_and_size_never_spends_another_vision_call():
    assert not needs_ring_retry(PHOTO_DACAPO)
    assert merge_confirmed_ring_fields(PHOTO_DACAPO, FOCUSED) == PHOTO_DACAPO


def test_missing_one_size_preserves_known_flow_and_other_size():
    original = PHOTO_DACAPO.replace("成品尺寸MAX：18", "成品尺寸MAX：?")
    result = merge_confirmed_ring_fields(original, FOCUSED)
    assert result != original
    assert result.count("訂單流程：CHRAPDGL") == 1
    assert result.count("成品尺寸MIN：17.957") == 1
    assert extract_work_order_info(result, {}, {})["ring"]["status"] == "yes"


def test_missing_flow_but_complete_diameter_is_recoverable():
    original = PHOTO_DACAPO.replace("訂單流程：CHRAPDGL\n", "")
    assert needs_ring_retry(original)
    assert extract_work_order_info(merge_confirmed_ring_fields(original, FOCUSED), {}, {})[
        "ring"]["status"] == "yes"


@pytest.mark.parametrize("retry", [
    "訂單流程：CHRAPDGL\n成品尺寸MIN：17.957\n成品尺寸MAX：?",
    "訂單流程：CHRAPDGL\n成品尺寸MIN：17.957\n成品尺寸MAX：18\n套環：Y",
    "訂單流程：GL\n成品尺寸MIN：17.957\n成品尺寸MAX：18",
    "訂單流程：CHRAPD8L\n成品尺寸MIN：17.957\n成品尺寸MAX：18",
    "訂單流程：CHRAPDGL\n成品尺寸MIN：19\n成品尺寸MAX：18",
    "流程：CHRAPDGL\n成品尺寸MIN：17.957\n成品尺寸MAX：18",
])
def test_unreadable_incomplete_or_unidentified_second_reading_is_ignored(retry):
    original = PHOTO_DACAPO.replace("訂單流程：CHRAPDGL", "訂單流程：?")
    assert merge_confirmed_ring_fields(original, retry) == original


def test_second_reading_must_agree_with_each_readable_original_cell():
    missing_flow = PHOTO_DACAPO.replace("訂單流程：CHRAPDGL", "訂單流程：?")
    assert merge_confirmed_ring_fields(
        missing_flow, FOCUSED.replace("成品尺寸MAX：18", "成品尺寸MAX：19")) == missing_flow
    missing_size = PHOTO_DACAPO.replace("成品尺寸MAX：18", "成品尺寸MAX：?")
    assert merge_confirmed_ring_fields(
        missing_size, FOCUSED.replace("訂單流程：CHRAPDGL", "訂單流程：CHRAPDL")) == missing_size
    assert merge_confirmed_ring_fields(
        missing_size, FOCUSED.replace("成品尺寸MIN：17.957", "成品尺寸MIN：17.95")) == missing_size


def test_conflicting_duplicate_first_reading_is_not_silently_replaced():
    conflict = (PHOTO_DACAPO.replace("訂單流程：CHRAPDGL", "訂單流程：CHRAPDGL\n訂單流程：CHRAPDL")
                .replace("成品尺寸MAX：18", "成品尺寸MAX：?"))
    assert needs_ring_retry(conflict)
    assert merge_confirmed_ring_fields(conflict, FOCUSED) == conflict
    partial = PHOTO_DACAPO.replace("訂單流程：CHRAPDGL", "訂單流程：CHRAPD?L")
    assert merge_confirmed_ring_fields(partial, FOCUSED) == partial
    known_and_unknown = PHOTO_DACAPO.replace(
        "訂單流程：CHRAPDGL", "訂單流程：CHRAPDGL\n訂單流程：?")
    assert merge_confirmed_ring_fields(known_and_unknown, FOCUSED) == known_and_unknown


def test_table_column_ambiguity_is_preserved():
    original = (PHOTO_DACAPO.replace("訂單流程：CHRAPDGL", "訂單流程：?")
                + "\n訂單流程 | 成品尺寸MIN\nCHRAPDL | 17")
    assert merge_confirmed_ring_fields(original, FOCUSED) == original


def test_printed_d_needs_no_size_retry_and_special_note_needs_no_retry():
    packaged = (PHOTO_DACAPO.replace("訂單流程：CHRAPDGL", "訂單流程：CHRAPD")
                .replace("成品尺寸MIN：17.957", "成品尺寸MIN：?"))
    assert not needs_ring_retry(packaged)
    no_ring = PHOTO_DACAPO.replace("特殊備註：", "特殊備註：不要黑色套環(NO KONDOM)")
    assert not needs_ring_retry(no_ring)


def test_actual_photo_crop_is_a_valid_magnified_jpeg_and_invalid_images_are_safe():
    photo = (Path(__file__).resolve().parent.parent.parent / "upload" / "02-235509.jpg")
    if not photo.exists():
        photo = Path(__file__).resolve().parent / "tests" / "fixtures" / "work_order_20260906.jpg"
    crop = focused_ring_crop(base64.b64encode(photo.read_bytes()).decode("ascii"))
    assert crop is not None
    with Image.open(io.BytesIO(base64.b64decode(crop))) as image:
        assert image.format == "JPEG"
        assert image.width > 950
        assert image.height > 300
    assert focused_ring_crop("wrong-base64") is None


def test_app_retries_missing_photo_cells_once_and_rule_not_printed_y_decides(monkeypatch):
    monkeypatch.setattr(app, "_has_ai_capability", lambda *_args: True)
    monkeypatch.setattr(app, "track_tokens", lambda *_args: None)
    original = (PHOTO_DACAPO.replace("訂單流程：CHRAPDGL", "訂單流程：?")
                .replace("成品尺寸MIN：17.957", "成品尺寸MIN：?")
                .replace("成品尺寸MAX：18", "成品尺寸MAX：?")
                .replace("套環：Y", "套環：N"))
    calls = []

    def vision(messages, **options):
        calls.append((messages, options))
        response = original if len(calls) == 1 else FOCUSED
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=response))])

    monkeypatch.setattr(app, "_vision_call", vision)
    photo = (Path(__file__).resolve().parent.parent.parent / "upload" / "02-235509.jpg")
    if not photo.exists():
        photo = Path(__file__).resolve().parent / "tests" / "fixtures" / "work_order_20260906.jpg"
    result = app.ocr_work_order_fields(base64.b64encode(photo.read_bytes()).decode("ascii"))
    assert len(calls) == 2
    assert "訂單流程" in calls[1][0][0]["content"]
    assert "Y／N 不可靠" in calls[1][0][0]["content"]
    assert len([part for part in calls[1][0][1]["content"]
                if part["type"] == "image_url"]) == 2
    info = extract_work_order_info(result, {}, {})
    assert info["fields"]["ring_on_form"] == "N"
    assert info["ring"] == {"status": "yes", "reason": "size_rule",
                            "process": "grinding", "threshold": 16}


@pytest.mark.parametrize("original", [
    PHOTO_DACAPO,
    PHOTO_DACAPO.replace("訂單流程：CHRAPDGL", "訂單流程：?")
    .replace("特殊備註：", "特殊備註：不要黑色套環(NO KONDOM)"),
])
def test_app_skips_retry_when_rule_already_resolved(monkeypatch, original):
    monkeypatch.setattr(app, "_has_ai_capability", lambda *_args: True)
    monkeypatch.setattr(app, "track_tokens", lambda *_args: None)
    calls = []

    def vision(messages, **options):
        calls.append(messages)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=original))])

    monkeypatch.setattr(app, "_vision_call", vision)
    assert app.ocr_work_order_fields("fake-image") == original.strip()
    assert len(calls) == 1
