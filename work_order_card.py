"""Readable Chinese/Indonesian LINE card for a photographed work order.

Work-order extraction and rule decisions live in ``work_order_query``.  This
module shows only the five operational answers requested by the user, including
the complete matching packaging method in the packaging section of one card.
"""

from __future__ import annotations

import json
import re
import unicodedata

from packaging_lookup import normalize_code
from work_order_query import (
    _PACKAGING_DETAIL_ID, _PACKAGING_SHORT_ID, _PAINT_CODES,
    extract_work_order_info,
)


INK = "#F5FAFC"
MUTED = "#AEC7D0"
TEAL = "#7BE2D3"
AMBER = "#F4C177"
BODY_BG = "#132737"
PANEL_BG = "#1B3949"


def _clean(value, limit=180, *, multiline=False):
    """Limit OCR/table content and remove control characters from LINE JSON."""
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


def _text(value, *, size="sm", color=INK, weight="regular", margin=None):
    out = {"type": "text", "text": value, "size": size, "color": color,
           "weight": weight, "wrap": True}
    if margin:
        out["margin"] = margin
    return out


def _box(contents, *, margin=None, **styles):
    out = {"type": "box", "layout": "vertical", "contents": contents}
    if margin:
        out["margin"] = margin
    out.update(styles)
    return out


def _divider():
    return {"type": "separator", "margin": "lg", "color": "#355264"}


def _section(zh, idn, values, *, margin="lg"):
    return _box([_text(f"{zh}  /  {idn}", color=MUTED, size="sm")] + values,
                margin=margin)


def _storage_text(storage):
    if storage.get("status") == "ok":
        return _clean(storage.get("area"), 40), INK
    # An unrecognised customer, missing measurement or conflicting table rule
    # must not silently become an invented area.  The measurement itself is
    # intentionally omitted from this compact presentation.
    return "儲區待確認 / Gudang perlu diperiksa", AMBER


def _translate(source, bundled, callback):
    if not source:
        return None
    translated = bundled.get(source)
    if translated:
        return _clean(translated, 1350, multiline=True)
    if callable(callback):
        try:
            return _clean(callback(source), 1350, multiline=True) or None
        except Exception:
            pass
    return None


def _inconsistent_package(package):
    """The present 1D record names 3P bags but its detailed method does not.

    Restrict this alert to the actual, observed contradiction.  A new admin
    record can resolve it by updating either method; other codes stay intact.
    """
    return (package.get("status") == "ok"
            and normalize_code(package.get("code")) == "1D"
            and "3P袋" in (package.get("short") or "")
            and "3P袋" not in (package.get("detail") or ""))


def _package_rows(package, translate_zh_to_id):
    status = package["status"]
    if status == "ok":
        code = _clean(package.get("code"), 24)
        rows = [_text(code, size="xl", weight="bold", color=TEAL, margin="sm")]
        if _inconsistent_package(package):
            rows.append(_text("原表簡稱與明細不一致，請核對", color=AMBER,
                              weight="bold", margin="sm"))
            rows.append(_text("Nama singkat dan rincian pada tabel tidak cocok; periksa kembali",
                              color=AMBER, margin="sm"))
            return rows
        short = package.get("short") or ""
        if short:
            rows.append(_text(_clean(short, 110), weight="bold", margin="sm"))
            short_id = _translate(short, _PACKAGING_SHORT_ID, translate_zh_to_id)
            rows.append(_text(_clean(short_id, 145) if short_id else
                              "印尼文待核對 / Terjemahan perlu diperiksa",
                              color=MUTED if short_id else AMBER, margin="sm"))
        else:
            rows.append(_text("包裝方式待確認 / Cara pengemasan perlu diperiksa",
                              color=AMBER, margin="sm"))
        return rows
    if status == "ambiguous":
        code = _clean(package.get("code"), 24)
        return [_text(f"{code} · 對應新碼待確認 / Kode baru perlu diperiksa",
                      color=AMBER, weight="bold", margin="sm")]
    if status == "not_found":
        return [_text(_clean(package.get("code"), 24) +
                      " · 查無包裝資料 / Kode kemasan tidak ditemukan",
                      color=AMBER, margin="sm")]
    return [_text("包裝碼待確認 / Kode kemasan perlu diperiksa",
                  color=AMBER, margin="sm")]


def _paint_color(code, lookup):
    """Use only a whole verified code with both requested language labels."""
    key = normalize_code(code)
    if not re.fullmatch(r"[A-Z0-9]{1,12}", key) or not isinstance(lookup, dict):
        return None
    item = lookup.get(key)
    if not isinstance(item, dict):
        return None
    zh, idn = _clean(item.get("zh"), 30), _clean(item.get("id"), 42)
    return (zh, idn) if zh and idn else None


def _spray_rows(info, paint_codes):
    paint = info["paint"]
    if paint["status"] == "no":
        # The order's color cell is irrelevant when the position says no spray.
        return [_text("不噴 / Tidak dicat", weight="bold", margin="sm")]
    if paint["status"] not in ("one", "both"):
        return [_text("噴漆位置待確認 / Posisi cat perlu diperiksa",
                      color=AMBER, margin="sm")]
    side = ("單邊 / Satu sisi" if paint["status"] == "one"
            else "雙邊 / Kedua sisi")
    rows = [_text(side, weight="bold", margin="sm")]
    code = _clean(paint.get("color_code"), 32)
    if not code:
        rows.append(_text("色碼待確認 / Kode warna perlu diperiksa",
                          color=AMBER, margin="sm"))
        return rows
    color = _paint_color(code, paint_codes)
    label = (f"色碼 {code} · {color[0]} / {color[1]}" if color else
             f"色碼 {code} · 顏色待核對 / Warna perlu diperiksa")
    rows.append(_text(label, color=INK if color else AMBER, margin="sm"))
    return rows


def _ring_text(ring):
    if ring["reason"] == "explicit_note":
        return "依備註不套環 / Tanpa cincin sesuai catatan", INK
    if ring["status"] == "yes":
        return "需要套環 / Wajib pakai cincin pelindung", TEAL
    if ring["status"] == "no":
        return "不需套環 / Tidak perlu cincin pelindung", INK
    return "套環待確認 / Perlu memeriksa cincin pelindung", AMBER


def _package_detail_rows(package, translate_zh_to_id):
    """Show original packaging instructions and Indonesian in the same card.

    Each language stays in one bounded text element so LINE can wrap the
    complete method naturally without another chat bubble.
    """
    if package["status"] != "ok":
        return []
    detail = package.get("detail") or ""
    if not detail or detail == package.get("short"):
        return []
    detail_id = _translate(detail, _PACKAGING_DETAIL_ID, translate_zh_to_id)
    return [
        _text("包裝明細 / Rincian pengemasan", size="sm", color=TEAL,
              weight="bold", margin="lg"),
        _text(_clean(detail, 1350, multiline=True), margin="sm"),
        _text(detail_id or "翻譯待核對 / Terjemahan perlu diperiksa",
              margin="md", color=MUTED if detail_id else AMBER),
    ]


def _fallback_from_info(info, paint_codes, *, package_rows=None,
                        paint_rows=None, detail_rows=None):
    """Give LINE a safe five-item text message even if Flex construction fails."""
    if not info["is_work_order"]:
        return "⚠️ 無法確認是工單 / Tidak dapat memastikan ini perintah kerja."
    storage = info["storage"]
    customer = _clean(storage.get("customer") or info.get("customer"), 75)
    customer = customer or "客戶待確認 / Pelanggan perlu diperiksa"
    area, _ = _storage_text(storage)
    package = info["packaging"]
    package_rows = package_rows if package_rows is not None else _package_rows(package, None)
    paint_rows = paint_rows if paint_rows is not None else _spray_rows(info, paint_codes)
    ring, _ = _ring_text(info["ring"])
    lines = [
        "📋 工單重點 / Ringkasan perintah kerja",
        f"客戶 / Pelanggan：{customer}",
        f"儲區 / Gudang：{area}",
        "包裝碼與方式 / Kode dan cara pengemasan：" + "；".join(row["text"] for row in package_rows),
        "噴漆 / Cat semprot：" + "；".join(row["text"] for row in paint_rows),
        f"套環 / Cincin pelindung：{ring}",
    ]
    if package["status"] == "ok" and package.get("detail") and package.get("detail") != package.get("short"):
        if detail_rows:
            lines.extend(row["text"] for row in detail_rows)
        else:
            detail = package["detail"]
            detail_id = _translate(detail, _PACKAGING_DETAIL_ID, None)
            lines.extend(["包裝明細 / Rincian pengemasan：" + _clean(detail, 1350, multiline=True),
                          "印尼文 / Bahasa Indonesia：" +
                          (detail_id or "翻譯待核對 / Terjemahan perlu diperiksa")])
    return _clean("\n".join(lines), 4900, multiline=True)


def build_work_order_fallback(ocr_text, storage_lookup=None, packaging_lookup=None,
                              paint_codes=None):
    """Independent Chinese/Indonesian text fallback for malformed Flex cards."""
    info = extract_work_order_info(ocr_text, storage_lookup, packaging_lookup, paint_codes)
    codes = _PAINT_CODES if paint_codes is None else paint_codes
    try:
        details = (_package_detail_rows(info["packaging"], None)
                   if info["is_work_order"] else [])
        return _fallback_from_info(info, codes, detail_rows=details)
    except Exception:
        return _fallback_from_info(info, codes)


def build_work_order_cards(ocr_text, storage_lookup=None, packaging_lookup=None,
                           paint_codes=None, translate_zh_to_id=None,
                           translate_zh_to_en=None):
    """Return the five answers and the matched Chinese/Indonesian method.

    Preserve the English callback parameter for old callers; this formatter
    never invokes it or includes an English translation in any LINE message.
    """
    info = extract_work_order_info(ocr_text, storage_lookup, packaging_lookup,
                                   paint_codes)
    if not info["is_work_order"]:
        return {"messages": [], "fallback_text":
                "⚠️ 無法確認是工單 / Tidak dapat memastikan ini perintah kerja."}
    codes = _PAINT_CODES if paint_codes is None else paint_codes
    storage = info["storage"]
    # When storage lookup resolves a partial customer name to a unique exact
    # admin entry, show that canonical name next to its actual storage area.
    customer = _clean(storage.get("customer") or info.get("customer"), 75)
    customer = customer or "客戶待確認 / Pelanggan perlu diperiksa"
    area, area_color = _storage_text(storage)
    package = info["packaging"]
    package_rows = _package_rows(package, translate_zh_to_id)
    paint_rows = _spray_rows(info, codes)
    ring, ring_color = _ring_text(info["ring"])
    # Keep the bilingual method immediately below its code and short method.
    # A broken admin translation must still leave the important decisions
    # visible and must not trigger a second LINE message.
    try:
        detail_rows = _package_detail_rows(package, translate_zh_to_id)
    except Exception:
        detail_rows = []
    content = [
        _section("客戶", "Pelanggan",
                 [_text(customer, size="lg", weight="bold", margin="sm")], margin=None),
        _section("儲區", "Gudang",
                 [_text(area, size="lg" if storage["status"] == "ok" else "sm",
                        color=area_color, weight="bold", margin="sm")]),
        _divider(),
        _section("包裝碼與方式", "Kode dan cara pengemasan",
                 package_rows + detail_rows),
        _divider(),
        _section("噴漆", "Cat semprot", paint_rows),
        _divider(),
        _section("套環", "Cincin pelindung",
                 [_text(ring, color=ring_color, weight="bold", margin="sm")]),
    ]
    bubble = {"type": "bubble", "size": "mega",
              "header": _box([_text("工單重點 / Ringkasan perintah kerja",
                                    color="#C5EEE8", weight="bold")],
                             backgroundColor="#195365", paddingAll="16px"),
              "body": _box(content, backgroundColor=BODY_BG, paddingAll="16px")}
    code = (_clean(package.get("code"), 24)
            if package["status"] != "missing" else "待確認")
    primary = {"type": "flex",
               "altText": _clean(f"📋 工單重點 / Ringkasan perintah kerja：{customer} · {area} · 包裝 {code}", 200),
               "contents": bubble}
    fallback = _fallback_from_info(info, codes, package_rows=package_rows,
                                   paint_rows=paint_rows, detail_rows=detail_rows)
    if len(json.dumps(primary["contents"], ensure_ascii=False).encode("utf-8")) >= 30000:
        return {"messages": [], "fallback_text": fallback}
    return {"messages": [primary], "fallback_text": fallback}
