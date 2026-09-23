"""The DACAPO photograph remains correct from OCR text through the LINE card.

These fields were transcribed manually from the supplied photograph. The test
injects the observed OCR confusion ``10`` (zero) for the printed ``1O``
(letter O); it does not make an assertion about a live vision provider.
"""

import unicodedata

import pytest

import app
from test_work_order_image_routing import _background


DACAPO_PHOTO_OCR = """冷精棒製造指示書 Petunjuk produksi Cold Finished Bar
訂單編號：Y1223786-008
客戶名稱：DACAPO
收貨人：DACAPO
成品尺寸MIN：17.957
成品尺寸MAX：18
長度MIN：6000
長度MAX：6050
訂單流程：CHRAPDGL
噴漆位置：雙邊
套環：Y
顏色：土藍
包裝代碼：10
特殊備註：
"""


def _visible(value):
    if isinstance(value, dict):
        return "\n".join([str(value.get("text", ""))] + [
            _visible(child) for child in value.values() if isinstance(child, (dict, list))
        ])
    if isinstance(value, list):
        return "\n".join(_visible(child) for child in value)
    return ""


def _card(monkeypatch, ocr_text=DACAPO_PHOTO_OCR):
    monkeypatch.setattr(app, "translate", lambda *_args, **_kwargs: pytest.fail(
        "Bundled 1O packaging translations should not require online translation"))
    result = app.format_work_order_cards(ocr_text)
    assert len(result["messages"]) == 1
    assert result["messages"][0]["type"] == "flex"
    return _visible(result["messages"][0]), result["fallback_text"]


def test_dacapo_photo_displays_verified_storage_packaging_color_and_gl_ring_in_one_card(monkeypatch):
    card, fallback = _card(monkeypatch)
    for expected in (
        "DACAPO", "EH31", "1O（舊碼 7 / kode lama 7）",
        "PE布+木條+棉繩改EN1492-1吊帶", "頭中尾內舖PC布墊",
        "色碼 46 · 土藍 / biru bernuansa tanah",
        "雙邊 / Kedua sisi", "需要套環 / Wajib pakai cincin pelindung",
    ):
        assert expected in card
    assert "1O(舊碼 7 / kode lama 7)" in fallback
    for erroneous in ("查無包裝資料", "套環待確認", "儲區待確認", "顏色待核對"):
        assert erroneous not in card
    for hidden in ("Y1223786-008", "6000", "6050", "17.957", "訂單流程", "成品尺寸"):
        assert hidden not in card and hidden not in fallback


@pytest.mark.parametrize(("printed_ring", "expected"), [
    ("Y", "需要套環 / Wajib pakai cincin pelindung"),
    ("N", "不需套環 / Tidak perlu cincin pelindung"),
    ("?", "工單套環欄位待確認"), (None, "工單套環欄位待確認"),
])
def test_gl_18_ring_uses_the_printed_ring_cell(monkeypatch, printed_ring, expected):
    text = DACAPO_PHOTO_OCR.replace(
        "套環：Y\n", "套環：" + printed_ring + "\n" if printed_ring is not None else "")
    card, fallback = _card(monkeypatch, text)
    assert expected in card
    assert expected in fallback


@pytest.mark.parametrize(("printed_ring", "expected"), [
    ("Y", "需要套環 / Wajib pakai cincin pelindung"),
    ("N", "不需套環 / Tidak perlu cincin pelindung"),
    ("?", "工單套環欄位待確認"), (None, "工單套環欄位待確認"),
])
def test_regular_customer_form_is_not_overridden_by_a_small_size(monkeypatch, printed_ring, expected):
    text = (DACAPO_PHOTO_OCR.replace("成品尺寸MIN：17.957", "成品尺寸MIN：15.8")
            .replace("成品尺寸MAX：18", "成品尺寸MAX：15.9")
            .replace("套環：Y\n", "套環：" + printed_ring + "\n" if printed_ring is not None else ""))
    card, fallback = _card(monkeypatch, text)
    assert expected in card
    assert expected in fallback


def test_form_y_applies_even_for_printed_d_and_no_kondom_overrides_it(monkeypatch):
    packaged = DACAPO_PHOTO_OCR.replace("訂單流程：CHRAPDGL", "訂單流程：CHRAPD")
    card, _ = _card(monkeypatch, packaged)
    assert "需要套環 / Wajib pakai cincin pelindung" in card

    no_kondom = DACAPO_PHOTO_OCR.replace("特殊備註：", "特殊備註：不要黑色套環（NO KONDOM）")
    card, _ = _card(monkeypatch, no_kondom)
    assert "不套環" in card and "需要套環 / Wajib pakai cincin pelindung" not in card


@pytest.mark.parametrize("paint_position", ["N", "不噴"])
def test_dacapo_color_overrides_no_spray_position(monkeypatch, paint_position):
    ocr = DACAPO_PHOTO_OCR.replace("噴漆位置：雙邊", "噴漆位置：" + paint_position)
    card, fallback = _card(monkeypatch, ocr)
    for visible in (card, fallback):
        expected_position = ("工單噴漆位置：N" if paint_position == "N"
                             else "工單位置：不噴")
        assert "要噴漆" in visible
        assert unicodedata.normalize("NFKC", expected_position) in unicodedata.normalize("NFKC", visible)
        assert "色碼 46 · 土藍 / biru bernuansa tanah" in visible
        assert "雙邊 / Kedua sisi" not in visible


def test_dacapo_table_n_and_soil_blue_still_returns_unsprayed_single_card(monkeypatch):
    table = "噴漆位置 | 套環 | 顏色 | 包裝代碼\nN | Y | 土藍 | 1O\n"
    ocr = DACAPO_PHOTO_OCR.replace(
        "噴漆位置：雙邊\n套環：Y\n顏色：土藍\n包裝代碼：10\n", table)
    card, fallback = _card(monkeypatch, ocr)
    for visible in (card, fallback):
        assert "要噴漆" in visible
        assert "工單噴漆位置:N" in unicodedata.normalize("NFKC", visible)
        assert "色碼 46 · 土藍 / biru bernuansa tanah" in visible
        assert "需要套環 / Wajib pakai cincin pelindung" in visible
        assert "1O" in visible and "舊碼 7" in visible


def test_dacapo_uploaded_photo_routes_to_single_work_order_flex(monkeypatch):
    context, sent = _background(monkeypatch, mode="work_order", ocr=DACAPO_PHOTO_OCR)
    monkeypatch.setattr(app, "translate", lambda *_args, **_kwargs: pytest.fail(
        "Bundled 1O packaging translations should not require online translation"))
    app._handle_image_background.__wrapped__(context)
    assert len(sent) == 1
    assert sent[0]["message_obj"].type == "flex"
    assert sent[0]["append_messages"] == []
    visible = _visible(sent[0]["message_obj"].to_dict())
    for expected in ("DACAPO", "EH31", "1O", "舊碼 7", "色碼 46", "需要套環"):
        assert expected in visible
