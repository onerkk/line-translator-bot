"""Compact LINE Flex cards for an OCR work order.

The work-order query module alone interprets the source fields.  Rendering
never turns a missing area, paint color or packaging translation into a guess.
Return plain dictionaries so the caller can persist the exact LINE messages
before replying and restore them during a durable retry.
"""

from __future__ import annotations

import json
import re
import unicodedata

from work_order_query import (
    _PACKAGING_DETAIL_EN, _PACKAGING_DETAIL_ID,
    _PACKAGING_SHORT_EN, _PACKAGING_SHORT_ID, _PAINT_CODES,
    _decimal, _verified_paint_color, extract_work_order_info,
)


INK = "#F5FAFC"
MUTED = "#AEC7D0"
TEAL = "#7BE2D3"
AMBER = "#F4C177"
BODY_BG = "#132737"
PANEL_BG = "#1B3949"


def _clean(value, limit=180, *, multiline=False):
    """Keep untrusted OCR/table text readable and inside the LINE JSON limit."""
    raw = unicodedata.normalize("NFKC", str(value or ""))
    raw = re.sub(r"[\u202a-\u202e\u2066-\u2069\u200b-\u200f]", "", raw)
    raw = re.sub(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", " ", raw)
    if multiline:
        raw = "\n".join(re.sub(r"\s+", " ", line).strip() for line in raw.splitlines())
        raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
    else:
        raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) > limit:
        return raw[: max(0, limit - 2)].rstrip() + "…"
    return raw


def _text(value, *, size="sm", color=INK, weight="regular", margin=None,
          max_lines=None):
    out = {"type": "text", "text": value, "size": size, "color": color,
           "weight": weight, "wrap": True}
    if margin:
        out["margin"] = margin
    if max_lines:
        out["maxLines"] = max_lines
    return out


def _box(contents, *, layout="vertical", margin=None, **styles):
    out = {"type": "box", "layout": layout, "contents": contents}
    if margin:
        out["margin"] = margin
    out.update(styles)
    return out


def _divider():
    return {"type": "separator", "margin": "lg", "color": "#355264"}


def _label(zh, idn, en):
    return _text(f"{zh}  ·  {idn} / {en}", color=MUTED, size="sm")


def _section(zh, idn, en, values, *, margin="lg"):
    return _box([_label(zh, idn, en)] + values, margin=margin)


def _observed_number(fields, key):
    raw = fields.get(key)
    number = _decimal(raw)
    return _clean(str(number), 16) if number is not None and number >= 0 else None


def _shown_number(fields, key):
    """Keep the OCR text visible if its numeric punctuation is ambiguous."""
    verified = _observed_number(fields, key)
    if verified:
        return verified
    raw = _clean(fields.get(key), 16)
    return raw + " (待核對)" if raw else "?"


def _dimension_panel(info, kind):
    length = kind == "length"
    fields = info["fields"]
    low = _observed_number(fields, kind + "_min")
    high = _observed_number(fields, kind + "_max")
    conflict = low is not None and high is not None and info[kind] is None
    labels = (("長度", "Panjang", "Length") if length
              else ("成品尺寸", "Ukuran jadi", "Finished size"))

    def cell(side, number):
        source = _clean(fields.get(kind + "_" + side), 16)
        contents = [
            _text("MIN" if side == "min" else "MAX", size="sm",
                  color=("#CBF6EE" if length else MUTED), weight="bold"),
            _text(number or source or "待確認", size="xl" if length else "lg",
                  color=(INK if number else AMBER), weight="bold", margin="sm"),
        ]
        if number:
            contents.append(_text("mm", color=MUTED, size="sm"))
        else:
            contents.append(_text("格式待核對 / Check number" if source
                                  else "Periksa / Verify", color=AMBER, size="sm"))
        return _box(contents, flex=1)

    contents = [_label(*labels),
                _box([cell("min", low), cell("max", high)], layout="horizontal",
                     spacing="md", margin="md")]
    if conflict:
        contents.append(_text("最小值大於最大值，請核對 / MIN > MAX; periksa / Verify",
                              size="sm", color=AMBER, margin="md"))
    return _box(contents, backgroundColor=("#195365" if length else PANEL_BG),
                cornerRadius="14px", paddingAll="14px", margin="lg")


def _storage_text(storage):
    status = storage["status"]
    if status == "ok":
        return _clean(storage.get("area"), 40), False
    messages = {
        "unknown_customer": "客戶未確認 / Pelanggan belum pasti / Confirm customer",
        "no_mapping": "儲區未設定 / Area belum terdaftar / Area not configured",
        "unknown_length": "長度待確認 / Panjang belum jelas / Confirm length",
        "unmapped_length": "此長度無儲區 / Panjang belum terdaftar / Length not mapped",
        "ambiguous_mapping": "儲區規則衝突 / Aturan area bertentangan / Conflicting rules",
        "invalid_mapping": "儲區規則待核對 / Periksa aturan area / Check area rules",
    }
    return messages.get(status, "儲區待核對 / Periksa area / Check area"), True


def _translate_known_or_new(source, bundled, callback):
    if not source:
        return None
    translated = bundled.get(source)
    if translated:
        return _clean(translated, 1350, multiline=True)
    if not callable(callback):
        return None
    try:
        return _clean(callback(source), 1350, multiline=True) or None
    except Exception:
        return None


def _package_text(package, translate_zh_to_id, translate_zh_to_en):
    if package["status"] == "ok":
        short = package.get("short") or ""
        code = _clean(package.get("code"), 24)
        old = _clean(package.get("old_code"), 24)
        short_id = _translate_known_or_new(short, _PACKAGING_SHORT_ID, translate_zh_to_id)
        short_en = _translate_known_or_new(short, _PACKAGING_SHORT_EN, translate_zh_to_en)
        rows = [_text(code + ("  ·  舊碼 " + old if old else ""), size="xl",
                      color=TEAL, weight="bold", margin="sm")]
        if short:
            rows.append(_text(_clean(short, 110), margin="sm", weight="bold"))
            if short_id:
                rows.append(_text(_clean(short_id, 145), color=MUTED, margin="sm"))
            if short_en:
                rows.append(_text(_clean(short_en, 145), color=MUTED, margin="sm"))
            if not short_id or not short_en:
                rows.append(_text("譯名待核對 / Periksa terjemahan / Verify translation",
                                  size="sm", color=AMBER, margin="sm"))
        return rows
    if package["status"] == "ambiguous":
        code = _clean(package.get("code"), 24)
        choices = ", ".join(_clean(s, 20) for s in package.get("candidates", [])[:4])
        return [_text(f"{code} · 新碼待確認 / Pastikan kode baru / Verify new code",
                      color=AMBER, weight="bold", margin="sm"),
                _text(choices, color=MUTED, margin="sm") if choices else _text("—")]
    if package["status"] == "not_found":
        return [_text(_clean(package.get("code"), 24) + " · 資料表無此碼 / Kode tak ditemukan / Code not found",
                      color=AMBER, margin="sm")]
    return [_text("代碼待確認 / Periksa kode / Confirm code", color=AMBER, margin="sm")]


def _spray_text(info, paint_codes):
    paint = info["paint"]
    if paint["status"] == "no":
        return [_text("不噴 / Tidak dicat / No spray paint", weight="bold", margin="sm")]
    if paint["status"] not in ("one", "both"):
        return [_text("位置待確認 / Periksa posisi / Confirm position", color=AMBER, margin="sm")]
    side = (("單邊 / Satu sisi / One side") if paint["status"] == "one"
            else ("雙邊 / Kedua sisi / Both sides"))
    rows = [_text(side, weight="bold", margin="sm")]
    code = _clean(paint.get("color_code"), 32)
    if not code:
        rows.append(_text("色碼待確認 / Kode warna belum jelas / Confirm color code",
                          color=AMBER, margin="sm"))
    else:
        color = _verified_paint_color(code, paint_codes)
        translated = (f"  ·  {_clean(color['zh'], 30)} / {_clean(color['id'], 42)} / {_clean(color['en'], 42)}"
                      if color else "  ·  顏色待核對 / Periksa warna / Verify color")
        rows.append(_text("色碼 / Kode / Code  " + code + translated,
                          color=(INK if color else AMBER), margin="sm"))
    return rows


def _ring_text(ring):
    if ring["reason"] == "explicit_note":
        return "依工單備註不套環 / Tanpa cincin sesuai catatan / No ring per note", INK
    if ring["status"] == "yes":
        return "需要套環 / Wajib pakai cincin / Ring required", TEAL
    if ring["status"] == "no":
        return "不需套環 / Tidak perlu cincin / No ring required", INK
    return "套環待確認 / Periksa cincin / Confirm ring", AMBER


def _detail_message(info, translate_zh_to_id, translate_zh_to_en):
    package = info["packaging"]
    if package["status"] != "ok":
        return None
    detail = package.get("detail") or ""
    if not detail or detail == package.get("short"):
        return None
    detail_id = _translate_known_or_new(detail, _PACKAGING_DETAIL_ID, translate_zh_to_id)
    detail_en = _translate_known_or_new(detail, _PACKAGING_DETAIL_EN, translate_zh_to_en)
    code = _clean(package["code"], 24)
    sections = [
        ("中文原文", _clean(detail, 1350, multiline=True)),
        ("BAHASA INDONESIA", detail_id or "翻譯待核對 / Terjemahan perlu diperiksa"),
        ("ENGLISH", detail_en or "Translation needs verification"),
    ]
    content = []
    for index, (name, value) in enumerate(sections):
        if index:
            content.append(_divider())
        content.extend([_text(name, size="sm", color=TEAL, weight="bold",
                              margin="lg" if index else None),
                        _text(value, size="sm", color=INK, margin="sm")])
    bubble = {"type": "bubble", "size": "mega",
              "header": _box([_text("包裝明細  /  PACKAGING", size="sm", color="#C5EEE8", weight="bold"),
                              _text(code, size="xl", weight="bold", margin="sm")],
                             backgroundColor="#195365", paddingAll="18px"),
              "body": _box(content, backgroundColor=BODY_BG, paddingAll="18px")}
    return {"type": "flex",
            "altText": _clean("📦 包裝 " + code + " / Rincian pengemasan / Packaging details", 180),
            "contents": bubble}


def build_work_order_cards(ocr_text, storage_lookup=None, packaging_lookup=None,
                           paint_codes=None, translate_zh_to_id=None,
                           translate_zh_to_en=None):
    """Build one summary Flex and, when available, a separate detail Flex.

    The returned messages are LINE API dictionaries.  The short fallback text
    is useful for audit, notifications and a readable plain-text contingency.
    For the full original wording callers can keep build_work_order_reply.
    """
    info = extract_work_order_info(ocr_text, storage_lookup, packaging_lookup,
                                   paint_codes)
    if not info["is_work_order"]:
        return {"messages": [], "fallback_text": "⚠️ 未確認為工單 / Bukan work order yang jelas / Work order not confirmed."}
    paint_codes = _PAINT_CODES if paint_codes is None else paint_codes
    order = _clean(info.get("order"), 52) or "訂單號待確認 / Confirm order"
    customer = _clean(info.get("customer"), 75) or "客戶待確認 / Pelanggan belum jelas / Confirm customer"
    storage, storage_warn = _storage_text(info["storage"])
    ring, ring_color = _ring_text(info["ring"])

    content = [
        _section("客戶", "Pelanggan", "Customer",
                 [_text(customer, size="lg", weight="bold", margin="sm")], margin=None),
        _section("儲區", "Gudang", "Storage",
                 [_text(storage, size="md" if storage_warn else "lg",
                        color=AMBER if storage_warn else TEAL,
                        weight="bold", margin="sm")]),
        _dimension_panel(info, "length"),
        _dimension_panel(info, "diameter"),
        _divider(),
        _section("噴漆", "Cat semprot", "Spray paint", _spray_text(info, paint_codes)),
        _divider(),
        _section("包裝", "Kemasan", "Packaging",
                 _package_text(info["packaging"], translate_zh_to_id, translate_zh_to_en)),
        _divider(),
        _section("套環", "Cincin pelindung", "Protective ring",
                 [_text(ring, color=ring_color, weight="bold", margin="sm")]),
    ]
    bubble = {"type": "bubble", "size": "mega",
              "header": _box([_text("工單資訊  ·  INFORMASI ORDER", size="sm", color="#C5EEE8", weight="bold"),
                              _text(order, size="xl", weight="bold", margin="sm")],
                             backgroundColor="#195365", paddingAll="18px"),
              "body": _box(content, backgroundColor=BODY_BG, paddingAll="18px")}
    minimum = _shown_number(info["fields"], "length_min")
    maximum = _shown_number(info["fields"], "length_max")
    diameter_min = _shown_number(info["fields"], "diameter_min")
    diameter_max = _shown_number(info["fields"], "diameter_max")
    spray = "；".join(row["text"] for row in _spray_text(info, paint_codes))
    package = info["packaging"]
    pack_code = (_clean(package.get("code"), 24) if package["status"] != "missing"
                 else "?")
    fallback = (f"📋 {order} · {customer}\n"
                f"長度 / Panjang / Length：MIN {minimum} · MAX {maximum} mm\n"
                f"成品尺寸 / Ukuran / Size：MIN {diameter_min} · MAX {diameter_max} mm\n"
                f"儲區 / Gudang / Storage：{storage}\n"
                f"噴漆 / Cat / Spray paint：{spray}\n"
                f"包裝 / Kemasan / Packaging：{pack_code}\n"
                f"套環 / Cincin / Ring：{ring}")
    primary = {"type": "flex",
               "altText": _clean(f"📋 工單 {order}｜長度 MIN {minimum} / MAX {maximum} mm｜{customer}", 200),
               "contents": bubble}
    messages = [primary]
    # A malformed admin description must never hide the confirmed work order.
    try:
        detail = _detail_message(info, translate_zh_to_id, translate_zh_to_en)
        if detail and len(json.dumps(detail["contents"], ensure_ascii=False).encode("utf-8")) < 30000:
            messages.append(detail)
    except Exception:
        pass
    # LINE caps each bubble at 30 KB. This path is only a last resort for
    # pathological source input; the field limits normally keep it far below.
    if len(json.dumps(primary["contents"], ensure_ascii=False).encode("utf-8")) >= 30000:
        return {"messages": [], "fallback_text": fallback}
    return {"messages": messages, "fallback_text": fallback}
