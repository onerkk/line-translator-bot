"""Source-bound bilingual work-order summary from OCR and live admin tables.

The vision provider transcribes the sheet; this module makes operational
decisions.  Missing, conflicting and unreadable cells stay unresolved.  In
particular, a historical packaging code may refer to several new methods, and
the storage table may contain overlapping or incomplete length intervals.
"""

from __future__ import annotations

import re
import unicodedata
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from packaging_lookup import find_packaging_matches, normalize_code
from work_order_detection import analyze_work_order_text, resolve_storage_customer


_UNCLEAR = re.compile(r"[?？�]|無法辨識|无法辨识|看不清|不清楚|未辨識|未辨识|裁切|遮擋|遮挡|\b(?:unknown|unreadable|n/?a|null)\b", re.I)
_NUM = re.compile(r"^(?:\d+(?:\.\d+)?|\.\d+)$")
_LABELS = {
    "order": ("訂單編號", "订单编号", "No.Pesan", "Nomor Pesanan"),
    "customer": ("客戶名稱", "客户名称", "Nama Pelanggan", "Customer Name"),
    "recipient": ("收貨人", "收货人", "Penerima Barang", "Consignee"),
    "flow": ("訂單流程", "订单流程", "Alur Pemasangan", "FINAL流程"),
    "diameter_min": ("成品尺寸MIN", "成品尺寸 MIN", "Ukuran MIN produk jadi", "成品尺寸1MIN", "尺寸1MIN"),
    "diameter_max": ("成品尺寸MAX", "成品尺寸 MAX", "Ukuran MAX produk jadi", "成品尺寸1MAX", "尺寸1MAX"),
    "length_min": ("長度MIN", "长度MIN", "長度 MIN", "Panjang MIN",
                   "長度MIN Panjang MIN", "长度MIN Panjang MIN",
                   "成品長度MIN", "成品长度MIN", "成品長度 MIN", "成品长度 MIN"),
    "length_max": ("長度MAX", "长度MAX", "長度 MAX", "Panjang MAX",
                   "長度MAX Panjang MAX", "长度MAX Panjang MAX",
                   "成品長度MAX", "成品长度MAX", "成品長度 MAX", "成品长度 MAX"),
    "paint": ("噴漆位置", "喷漆位置", "Posisi semprot cat", "噴漆", "喷漆"),
    "color": ("顏色", "颜色", "Warna", "噴漆顏色", "喷漆颜色"),
    "packaging": ("包裝代碼", "包装代码", "包裝碼", "包装码", "Kode kemasan"),
    "special": ("特殊備註", "特殊备注", "特殊", "Khusus"),
    "order_note": ("訂單備註", "订单备注", "備註", "备注", "Keterangan Pesanan"),
    "ring_on_form": ("套環", "套环", "Cincin Pelindung"),
}

# Reviewed Indonesian descriptions of the short method labels in the currently
# bundled 24-row packaging table.  Match the source label exactly so an admin
# edit cannot silently inherit a translation of different packaging content.
# The detailed Chinese instruction is always shown; the existing app translator
# supplies its full Indonesian translation when available.
_PACKAGING_SHORT_ID = {
    "3P袋+瓦楞": "Kantong 3P + lembaran bergelombang",
    "3P袋+木條+瓦楞": "Kantong 3P + bilah kayu + lembaran bergelombang",
    "3P袋+木條+瓦楞+小包裝": "Kantong 3P + bilah kayu + lembaran bergelombang + kemasan kecil",
    "3P袋+木箱+瓦楞": "Kantong 3P + peti kayu + lembaran bergelombang",
    "3P袋+木箱+瓦楞+小包裝": "Kantong 3P + peti kayu + lembaran bergelombang + kemasan kecil",
    "3P袋+木箱+瓦楞+網套": "Kantong 3P + peti kayu + lembaran bergelombang + selongsong jaring",
    "3P袋+PE布": "Kantong 3P + kain PE",
    "PC布墊+鋼帶+PE布+膠膜兩層+2條棉繩": "Bantalan kain PC + pita baja + kain PE + dua lapis film plastik + dua tali katun",
    "PC布墊(五處)+鋼帶": "Alas kain PC pada lima bagian + pita baja",
    "3P袋+PE布+紙管": "Kantong 3P + kain PE + tabung kertas",
    "3P袋+PE布+棉繩改EN1492-1吊帶": "Kantong 3P + kain PE; tali katun diganti sling EN 1492-1",
    "3P袋+裸包": "Kantong 3P + kemasan tanpa lapisan luar",
    "PE布+木條": "Kain PE + bilah kayu",
    "PE布+木條+4條棉繩": "Kain PE + bilah kayu + empat tali katun",
    "PE布+木條+棉繩改EN1492-1吊帶": "Kain PE + bilah kayu; tali katun diganti sling EN 1492-1",
    "木箱+紙管": "Peti kayu + tabung kertas",
    "木箱+紙管+內部兩端檔塊+外箱角鐵": "Peti kayu + tabung kertas + balok penahan di kedua ujung bagian dalam + besi siku pada bagian luar peti",
    "木箱+膠膜": "Peti kayu + plastik pembungkus",
    "木箱+膠膜+4條棉繩": "Peti kayu + plastik pembungkus + empat tali katun",
    "木箱+膠膜(小捆)": "Peti kayu + plastik pembungkus untuk ikatan kecil",
    "木箱+膠膜(小捆)+棉繩改EN1492-1吊帶": "Peti kayu + plastik pembungkus untuk ikatan kecil; tali katun diganti sling EN 1492-1",
    "木箱+膠膜+棉繩改EN1492-1吊帶": "Peti kayu + plastik pembungkus; tali katun diganti sling EN 1492-1",
    "扁箱+膠膜": "Peti datar + plastik pembungkus",
    "線架+膠膜": "線架 (istilah pada tabel perlu dikonfirmasi) + plastik pembungkus",
    "膠膜": "Plastik pembungkus",
}

# English method names refer to the same exact source labels.  Unverified new
# admin methods are not assigned an English name by similarity to old codes.
_PACKAGING_SHORT_EN = {
    "3P袋+瓦楞": "3P bags and corrugated sheet",
    "3P袋+木條+瓦楞": "3P bags, wood strips and corrugated sheet",
    "3P袋+木條+瓦楞+小包裝": "3P bags, wood strips, corrugated sheet and small bundles",
    "3P袋+木箱+瓦楞": "3P bags, wooden crate and corrugated sheet",
    "3P袋+木箱+瓦楞+小包裝": "3P bags, wooden crate, corrugated sheet and small bundles",
    "3P袋+木箱+瓦楞+網套": "3P bags, wooden crate, corrugated sheet and mesh sleeves",
    "3P袋+PE布": "3P bags and PE fabric",
    "PC布墊+鋼帶+PE布+膠膜兩層+2條棉繩": "PC fabric pads, steel strapping, PE fabric, two layers of wrapping film, and two cotton ropes",
    "PC布墊(五處)+鋼帶": "PC fabric pads at five points and steel strapping",
    "3P袋+PE布+紙管": "3P bags, PE fabric and paper tubes",
    "3P袋+PE布+棉繩改EN1492-1吊帶": "3P bags and PE fabric; replace cotton ropes with EN 1492-1 lifting slings",
    "3P袋+裸包": "3P bags and packing without an outer layer",
    "PE布+木條": "PE fabric and wood strips",
    "PE布+木條+4條棉繩": "PE fabric, wood strips and four cotton ropes",
    "PE布+木條+棉繩改EN1492-1吊帶": "PE fabric and wood strips; replace cotton ropes with EN 1492-1 lifting slings",
    "木箱+紙管": "Wooden crate and paper tubes",
    "木箱+紙管+內部兩端檔塊+外箱角鐵": "Wooden crate and paper tubes; internal end stops and external angle irons",
    "木箱+膠膜": "Wooden crate and plastic wrapping film",
    "木箱+膠膜+4條棉繩": "Wooden crate, plastic wrapping film and four cotton ropes",
    "木箱+膠膜(小捆)": "Wooden crate and plastic wrapping film for small bundles",
    "木箱+膠膜(小捆)+棉繩改EN1492-1吊帶": "Wooden crate and plastic wrapping film for small bundles; replace cotton ropes with EN 1492-1 lifting slings",
    "木箱+膠膜+棉繩改EN1492-1吊帶": "Wooden crate and plastic wrapping film; replace cotton ropes with EN 1492-1 lifting slings",
    "扁箱+膠膜": "Flat crate and plastic wrapping film",
    "線架+膠膜": "線架 (equipment type needs confirmation) and plastic wrapping film",
    "膠膜": "Plastic wrapping film",
}


def _load_bundled_lookup(filename):
    try:
        path = Path(__file__).with_name(filename)
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # An absent or damaged lookup must leave its values unconfirmed.
        return {}


_PACKAGING_DETAIL_ID = _load_bundled_lookup("packaging_detail_id.json")
_PACKAGING_DETAIL_EN = _load_bundled_lookup("packaging_detail_en.json")
# These codes were read from the user's paint cans and checked against the
# numbered rack.  Unknown OCR codes must remain unknown.
_PAINT_CODES = _load_bundled_lookup("paint_codes_data.json")


def _text(raw):
    return unicodedata.normalize("NFKC", str(raw or "")).strip()


def _label_key(raw):
    return re.sub(r"[\s/():：.·_\-]+", "", _text(raw)).casefold()


_ALIASES = {key: {_label_key(alias) for alias in aliases} for key, aliases in _LABELS.items()}


def _field_of(raw):
    key = _label_key(raw)
    for field, aliases in _ALIASES.items():
        if key in aliases:
            return field
    # A table header may contain both original and Indonesian labels in one
    # cell, e.g. "包裝代碼 / Kode kemasan".  Both halves must refer to the same
    # field; combined position/color headers must stay unresolved.
    parts = [part for part in re.split(r"[/()（）]", _text(raw)) if part.strip()]
    if len(parts) > 1:
        classified = {_field_of(part) for part in parts}
        classified.discard(None)
        if len(classified) == 1 and not any(_label_key(part) in {"min", "max", "minmax", "maxmin"} for part in parts):
            return next(iter(classified))
    return None


def _value(raw):
    value = _text(raw).strip(" \t:：|`*\"'")
    if not value or _UNCLEAR.search(value) or value in {"-", "—", "無", "无"}:
        return None
    if len(value) > 350:
        return None
    return value


def _cells(raw):
    raw = _text(raw)
    if "|" in raw:
        return [part.strip() for part in raw.strip("|").split("|")]
    if "\t" in raw:
        return [part.strip() for part in raw.split("\t")]
    return [raw]


def _header_fields(cells):
    """Pair a standalone MAX with its adjacent, identified length MIN cell."""
    headers = [_field_of(cell) for cell in cells]
    for column, field in enumerate(headers[:-1]):
        if field == "length_min" and headers[column + 1] is None:
            if _label_key(cells[column + 1]) == "max":
                headers[column + 1] = "length_max"
    return headers


_LENGTH_INLINE_MAX = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?\s*(?:mm|毫米|公厘)?)\s*"
    r"(?:[;；,，/／]\s*)?MAX\s*[:：]?\s*"
    r"(\d+(?:[.,]\d+)?\s*(?:mm|毫米|公厘)?)\s*$", re.I,
)


def _length_min_max_pair(minimum, maximum):
    """Accept two explicit values only when their numeric order is valid."""
    lows, highs = _number_candidates(minimum), _number_candidates(maximum)
    return any(0 <= low <= high for low in lows for high in highs)


def _read_fields(ocr_text):
    """Read explicit key/value OCR and adjacent header/value table rows.

    Ambiguous labels/rows are recorded as missing; no movement between columns.
    A repeated field with conflicting values is also treated as missing.
    """
    rows = [_cells(line) for line in ocr_text.splitlines() if line.strip()]
    found = {field: [] for field in _LABELS}
    for index, cells in enumerate(rows):
        # Some OCR providers keep two explicit length cells on one row.  Both
        # labels are required; a standalone MAX could belong to another field.
        if len(cells) == 2:
            labeled = [re.fullmatch(r"\s*([^:：]{1,55})\s*[:：]\s*(.*?)\s*", cell)
                       for cell in cells]
            if all(labeled):
                first, second = (_field_of(match.group(1)) for match in labeled)
                if {first, second} == {"length_min", "length_max"}:
                    for match, field in zip(labeled, (first, second)):
                        found[field].append(_value(match.group(2)))
                    continue
        if len(cells) == 1:
            line = cells[0]
            match = re.match(r"^\s*([^:：]{1,55})\s*[:：]\s*(.*)$", line)
            if match:
                field = _field_of(match.group(1))
                if field:
                    value = _value(match.group(2))
                    if field == "length_min" and value:
                        # OCR often places the form's adjacent MAX column on
                        # the same line, or on the next line as a bare MAX.
                        # Restrict this to an explicitly labelled length MIN;
                        # generic MAX columns also describe finished size.
                        paired = _LENGTH_INLINE_MAX.fullmatch(value)
                        if paired and _length_min_max_pair(*paired.groups()):
                            value = paired.group(1)
                            found["length_max"].append(_value(paired.group(2)))
                        elif index + 1 < len(rows) and len(rows[index + 1]) == 1:
                            adjacent = re.fullmatch(
                                r"\s*MAX\s*[:：]\s*(.*?)\s*", rows[index + 1][0], re.I,
                            )
                            if adjacent and _length_min_max_pair(value, adjacent.group(1)):
                                found["length_max"].append(_value(adjacent.group(1)))
                    if field in {"special", "order_note"} and value is None and index + 1 < len(rows):
                        following = rows[index + 1]
                        if len(following) == 1 and not re.match(r"[^:：]{1,55}[:：]", following[0]):
                            value = _value(following[0])
                    found[field].append(value)
                    continue
        # "長度MIN Panjang MIN | MAX" is a pair of column headers, not
        # "length MIN = MAX".  Only the data row below contains values.
        if (len(cells) == 2 and _field_of(cells[0])
                and not _field_of(cells[1])
                and not (_field_of(cells[0]) == "length_min"
                         and _label_key(cells[1]) == "max")):
            found[_field_of(cells[0])].append(_value(cells[1]))
            continue
        headers = _header_fields(cells)
        if len(cells) < 2 or not any(headers):
            continue
        # A work order has many short bilingual header rows.  The next row
        # must preserve exactly the same column count; otherwise it is unsafe.
        below = rows[index + 1] if index + 1 < len(rows) else []
        if len(below) != len(cells) or all(re.fullmatch(r"[:\-\s]+", c) for c in below):
            for field in headers:
                if field:
                    found[field].append(None)
            continue
        if any(_field_of(cell) for cell in below):
            continue  # Additional bilingual header line; wait for its data.
        for column, field in enumerate(headers):
            if field:
                found[field].append(_value(below[column]))
    result = {}
    for field, values in found.items():
        # An explicit unreadable cell and a second plausible value conflict.
        unique = set(values)
        result[field] = next(iter(unique)) if len(unique) == 1 else None
    return result


def _decimal(raw):
    if raw is None:
        return None
    raw = _text(raw)
    # A vision transcription may repeat the mm printed in the length/size
    # heading.  Strip only an exact unit suffix; never accept trailing notes
    # or a partially transcribed range as a confirmed number.
    raw = re.sub(r"\s*(?:mm|毫米|公厘)$", "", raw, flags=re.I).strip()
    # Work orders mix Chinese and Indonesian number styles.  A bare 4.200
    # could mean 4.2 or 4200, and 3,970 could mean 3.970 or 3970.  Neither
    # is safe for a storage or protective-ring decision without confirmation.
    if re.fullmatch(r"\d+\.\d{3}", raw) or re.fullmatch(r"\d{1,3},\d{3}", raw):
        return None
    if re.fullmatch(r"\d+,\d{1,2}", raw):
        raw = raw.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d{1,2}", raw):
        raw = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d+", raw) or re.fullmatch(r"\d{1,3}(?:,\d{3}){2,}", raw):
        raw = raw.replace(",", "")
    elif "," in raw:
        return None
    if not _NUM.fullmatch(raw):
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _range(fields, field):
    low = _decimal(fields.get(field + "_min"))
    high = _decimal(fields.get(field + "_max"))
    if low is None:
        return None
    if high is None:
        # A measured MIN still permits a threshold decision only when the
        # caller also knows MAX.  Never invent a missing upper bound.
        return None
    if low < 0 or high < low:
        return None
    return low, high


def _length_candidates(fields):
    """Enumerate plausible MIN/MAX readings when OCR uses 3-digit separators.

    A value like ``2.500`` could mean 2.5 or 2500.  Both remain candidates;
    the storage answer is released only if *every* valid interpretation yields
    the same unique area.  No ambiguous number is exported as a known length.
    """
    lows = _number_candidates(fields.get("length_min"))
    highs = _number_candidates(fields.get("length_max"))
    return tuple((low, high) for low in lows for high in highs if 0 <= low <= high)


def _parse_storage_rule(rule):
    rule = _text(rule).replace("≤", "<=").replace("≥", ">=")
    pieces = re.findall(r"(<=|>=|<|>)(\d+(?:\.\d+)?)", rule)
    if not pieces or "".join(op + number for op, number in pieces) != rule:
        return None
    return [(op, Decimal(number)) for op, number in pieces]


def _rule_matches(parts, value):
    return all({"<": value < cutoff, "<=": value <= cutoff,
                ">": value > cutoff, ">=": value >= cutoff}[op]
               for op, cutoff in parts)


def _resolve_storage(customer, length, lookup):
    canonical = resolve_storage_customer(customer, lookup or {})
    if not canonical:
        return {"status": "unknown_customer", "area": None, "customer": customer}
    rows = lookup.get(canonical)
    if not isinstance(rows, list) or not rows:
        return {"status": "no_mapping", "area": None, "customer": canonical}
    if not length:
        return {"status": "unknown_length", "area": None, "customer": canonical}
    parsed = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 2 or not _text(row[1]):
            return {"status": "invalid_mapping", "area": None, "customer": canonical}
        rule = _parse_storage_rule(row[0])
        if rule is None:
            return {"status": "invalid_mapping", "area": None, "customer": canonical}
        parsed.append((rule, str(row[1]).strip()))
    low, high = length
    # Check both ends and each threshold and interval between them, including
    # exact boundary points.  One gap or differing code makes lookup uncertain.
    marks = {low, high}
    marks.update(cutoff for parts, _area in parsed for _op, cutoff in parts if low < cutoff < high)
    ordered = sorted(marks)
    samples = ordered + [(left + right) / 2 for left, right in zip(ordered, ordered[1:])]
    areas = []
    for sample in samples:
        matches = {area for parts, area in parsed if _rule_matches(parts, sample)}
        if len(matches) != 1:
            return {"status": "ambiguous_mapping" if matches else "unmapped_length",
                    "area": None, "customer": canonical}
        areas.extend(matches)
    if len(set(areas)) != 1:
        return {"status": "ambiguous_mapping", "area": None, "customer": canonical}
    return {"status": "ok", "area": areas[0], "customer": canonical}


def _storage_for_fields(customer, fields, lookup):
    length = _range(fields, "length")
    if length is not None:
        return _resolve_storage(customer, length, lookup)
    candidates = _length_candidates(fields)
    if not candidates:
        return _resolve_storage(customer, None, lookup)
    results = [_resolve_storage(customer, bounds, lookup) for bounds in candidates]
    if all(row["status"] == "ok" and row["area"] == results[0]["area"] for row in results):
        return results[0]
    # Keep the original unresolved answer when even one possible reading maps
    # differently, has a table gap, or refers to an unknown customer.
    return _resolve_storage(customer, None, lookup)


def _packaging(code, lookup):
    if not code or not re.fullmatch(r"[A-Z0-9]{1,12}", normalize_code(code)):
        return {"status": "missing", "code": code}
    matches = find_packaging_matches(code, lookup or {})
    # The printed new code 1O (letter O) is easily read as 10 (digit zero) in
    # work-order photos.  Only repair this single observed OCR confusion when
    # the active packaging table contains exactly one verified 1O method and
    # no exact 10 method.  Never collapse O/0 in the general /pkg lookup: a
    # future administrator could define a different, legitimate 10 method.
    if not matches and normalize_code(code) == "10":
        alternatives = find_packaging_matches("1O", lookup or {})
        if len(alternatives) == 1:
            alternative_key, alternative = alternatives[0]
            if (isinstance(alternative, dict)
                    and (normalize_code(alternative_key) == "1O"
                         or normalize_code(alternative.get("品保設計(新版)")) == "1O")
                    and (not alternative.get("品保設計(新版)")
                         or normalize_code(alternative["品保設計(新版)"]) == "1O")):
                matches = alternatives
    if len(matches) > 1:
        return {"status": "ambiguous", "code": code,
                "candidates": [str(key) for key, _entry in matches]}
    if not matches:
        return {"status": "not_found", "code": code}
    key, entry = matches[0]
    if isinstance(entry, str):
        return {"status": "ok", "code": str(key), "requested_code": code,
                "old_code": None, "short": entry, "detail": entry}
    detail = next((str(value).strip() for field, value in entry.items()
                   if "詳細包裝" in field or "详细包装" in field or field == "包裝方式"), "")
    short = next((str(value).strip() for field, value in entry.items()
                  if field in ("簡稱", "简称")), "")
    return {"status": "ok", "code": str(key), "requested_code": code,
            "old_code": _value(entry.get("原包裝碼")), "short": short,
            "detail": detail or short,
            "inner": _value(entry.get("內包裝")),
            "outer": _value(entry.get("外包裝")),
            "cord": _value(entry.get("固定繩"))}


def _paint_reference(raw, lookup):
    """Resolve an exact printed color name only when it has one verified code.

    Forms may print either a rack number or a Chinese color name.  A name is
    never itself a color code, and ambiguous names cannot pick an arbitrary
    entry from an administrator's updated paint table.
    """
    value = _value(raw)
    if not value:
        return None, None
    normalized = normalize_code(value)
    if re.fullmatch(r"[A-Z0-9]{1,12}", normalized):
        return normalized, None
    name = re.sub(r"\s+", "", _text(value))
    matches = {
        normalize_code(code)
        for code, entry in (lookup or {}).items()
        if isinstance(entry, dict)
        and re.fullmatch(r"[A-Z0-9]{1,12}", normalize_code(code))
        and _value(entry.get("zh")) and _value(entry.get("id"))
        and any(re.sub(r"\s+", "", _text(label)) == name
                for label in [_value(entry.get("zh"))]
                + (entry.get("aliases_zh", []) if isinstance(entry.get("aliases_zh", []), list) else []))
    } if isinstance(lookup, dict) else set()
    return (next(iter(matches)) if len(matches) == 1 else None), value


def _paint(fields, lookup):
    raw = _value(fields.get("paint"))
    normalized = re.sub(r"\s+", "", _text(raw)).upper() if raw else ""
    no_values = {
        "不噴", "不喷", "不噴漆", "不喷漆", "N", "NO", "NONE", "NA",
        "TIDAKDISEMPROTCAT", "TIDAKDICAT", "TIDAKADA", "無", "无",
    }
    color_value = _value(fields.get("color"))
    color_key = re.sub(r"[\s._-]+", "", _text(color_value)).upper()
    has_color = bool(color_value and color_key not in no_values)

    # The color cell is the source of truth for whether painting is required.
    # The position cell can specify one/both sides, but N, blank, or unreadable
    # position must not cancel a real color written in the color column.
    if normalized in {"雙邊", "双边", "兩邊", "两边", "雙側", "双侧", "兩端", "两端", "DUASISI", "KEDUAUJUNG"}:
        status = "both"
    elif normalized in {"單邊", "单边", "單側", "单侧", "一端", "SATUSISI", "SATUUJUNG"}:
        status = "one"
    elif has_color:
        status = "color_only"
    elif normalized in no_values:
        return {"status": "no", "color_code": None}
    else:
        return {"status": "unknown", "color_code": None,
                **({"raw_position": raw} if raw else {})}

    code, name = _paint_reference(color_value, lookup) if has_color else (None, None)
    paint = {"status": status, "color_code": code}
    if name:
        paint["color_name"] = name
    return paint


def _verified_paint_color(code, lookup):
    """Resolve a complete, exact code match without guessing OCR fragments."""
    if not code or not isinstance(lookup, dict):
        return None
    entry = lookup.get(normalize_code(code))
    if not isinstance(entry, dict):
        return None
    labels = {lang: _value(entry.get(lang)) for lang in ("zh", "id", "en")}
    return labels if all(labels.values()) else None


_NO_RING = re.compile(r"不要\s*(?:黑色|黑人)?\s*套[環环]|不\s*套[環环]|(?:無需|无需|免)\s*套[環环]|\bNO\s+KONDOM\b", re.I)


def _number_candidates(raw):
    """Retain both readings of three-digit separators for rule decisions."""
    if raw is None:
        return ()
    raw = re.sub(r"\s*(?:mm|毫米|公厘)$", "", _text(raw), flags=re.I).strip()
    if re.fullmatch(r"\d+\.\d{3}", raw) or re.fullmatch(r"\d{1,3},\d{3}", raw):
        return tuple(sorted({Decimal(raw.replace(",", ".")),
                             Decimal(raw.replace(",", "").replace(".", ""))}))
    number = _decimal(raw)
    return (number,) if number is not None else ()


def _ring_flow_process(raw):
    flow = _text(raw).upper()
    if not re.fullmatch(r"[A-Z]+(?:\s+[A-Z]+)*", flow):
        return None
    flow = re.sub(r"\s+", "", flow)
    if flow.endswith("D"):
        return "packaging"
    if flow.endswith("GL"):
        return "grinding"
    if len(flow) >= 2 and flow.endswith("L") and flow[-2] != "G":
        return "polishing"
    return None


def _ring_size_status(fields, cutoff):
    lows = _number_candidates(fields.get("diameter_min"))
    highs = _number_candidates(fields.get("diameter_max"))
    if not lows or not highs:
        return "unknown", "diameter"
    possible = [(low, high) for low in lows for high in highs if 0 <= low <= high]
    if not possible:
        return "unknown", "invalid_diameter"
    if all(low >= cutoff for low, _high in possible):
        return "yes", "jiadong_polishing_20mm"
    if all(high < cutoff for _low, high in possible):
        return "no", "form_n"
    return "unknown", "threshold_crossing"


def _is_jiadong(customer):
    if not customer:
        return False
    value = _text(customer).casefold()
    if "佳東" in value or "佳东" in value:
        return True
    latin = re.sub(r"[^a-z0-9]", "", value)
    return latin == "jiadong"


def _ring(fields, customer=None):
    note = "\n".join(filter(None, (fields.get("special"), fields.get("order_note"))))
    if _NO_RING.search(note):
        return {"status": "no", "reason": "explicit_note", "process": None}

    ring_form = _text(fields.get("ring_on_form")).upper()
    # Jia Dong is the sole customer exception: polishing bars at 20 mm or
    # above always need a ring, even when the form says N.  For all other
    # orders the printed ring cell controls the answer.
    if _is_jiadong(customer):
        process = _ring_flow_process(fields.get("flow"))
        if process == "polishing":
            status, reason = _ring_size_status(fields, Decimal(20))
            if status == "yes":
                return {"status": "yes", "reason": reason, "process": process,
                        "threshold": 20}
            if status == "unknown":
                if ring_form in {"Y", "YES"}:
                    return {"status": "yes", "reason": "form_y", "process": None}
                return {"status": status, "reason": reason, "process": process}
            # Below 20 mm, the Jia Dong exception does not replace the form.
        elif process is None:
            # An N value is only overridden if this is confirmed to be a 20+
            # polishing bar; an unrecognized flow cannot rule out that exception.
            if ring_form in {"Y", "YES"}:
                return {"status": "yes", "reason": "form_y", "process": None}
            return {"status": "unknown", "reason": "flow", "process": None}

    if ring_form in {"Y", "YES"}:
        return {"status": "yes", "reason": "form_y", "process": None}

    if ring_form in {"N", "NO"}:
        process = _ring_flow_process(fields.get("flow")) if _is_jiadong(customer) else None
        return {"status": "no", "reason": "form_n", "process": process}
    return {"status": "unknown", "reason": "ring_field", "process": None}


def extract_work_order_info(ocr_text, storage_lookup=None, packaging_lookup=None, paint_codes=None):
    """Return operational facts; use current admin maps, never OCR example data."""
    analysis = analyze_work_order_text(ocr_text, storage_lookup or {})
    if not analysis["is_work_order"]:
        return {"is_work_order": False}
    fields = _read_fields(ocr_text)
    # The customer column is authoritative; the separately parsed recipient
    # is exposed only as an OCR alignment cross-check and is never substituted.
    customer = analysis["customer"]
    return {
        "is_work_order": True,
        "fields": fields,
        "order": fields.get("order"),
        "customer": customer,
        "recipient": analysis.get("recipient"),
        "customer_conflict": analysis.get("customer_conflict", False),
        "length": _range(fields, "length"),
        "diameter": _range(fields, "diameter"),
        "storage": ({"status": "unknown_customer"} if analysis.get("customer_conflict") else
                    _storage_for_fields(customer, fields, storage_lookup or {})),
        "packaging": _packaging(fields.get("packaging"), packaging_lookup),
        "paint": _paint(fields, _PAINT_CODES if paint_codes is None else paint_codes),
        "ring": _ring(fields, customer),
    }


def _show_range(bounds):
    return f"{bounds[0]}–{bounds[1]}" if bounds else "待確認 / Perlu diperiksa"


def build_work_order_reply(ocr_text, storage_lookup=None, packaging_lookup=None,
                           paint_codes=None, translate_zh_to_id=None,
                           translate_zh_to_en=None):
    """Compose a LINE text response in Chinese, Indonesian and English.

    Exact source-to-translation records cover all bundled methods offline.
    Translator callbacks are only for new, unrecognized admin descriptions;
    the model never decides work-order rules or storage/code lookups.
    """
    # The LINE app supplies no explicit paint table; use the reviewed cans by
    # default.  An explicit mapping, including {}, is a deliberate override.
    paint_codes = _PAINT_CODES if paint_codes is None else paint_codes
    info = extract_work_order_info(ocr_text, storage_lookup, packaging_lookup, paint_codes)
    if not info["is_work_order"]:
        return "⚠️ 未確認為工單 / Belum dapat dipastikan sebagai work order / Work order not confirmed."
    lines = ["📋 工單資訊 / Informasi work order"]
    lines.append("訂單 / Pesanan：" + (info["order"] or "待確認 / Perlu diperiksa"))
    lines.append("客戶 / Pelanggan：" + (info["customer"] or "待確認 / Perlu diperiksa"))
    storage = info["storage"]
    if storage["status"] == "ok":
        storage_text = storage["area"]
    elif storage["status"] == "unknown_customer":
        storage_text = "客戶尚未確認或儲區表無此客戶 / Pelanggan belum pasti atau tidak ada dalam data gudang"
    elif storage["status"] == "unknown_length":
        storage_text = "長度不明，待確認 / Panjang belum diketahui; perlu diperiksa"
    else:
        storage_text = "儲區資料的長度條件未涵蓋或互相衝突，待核對 / Aturan panjang lokasi penyimpanan tidak cocok atau bertentangan; periksa data"
    lines.append("儲區 / Area penyimpanan：" + storage_text)
    lines.append("成品尺寸 / Ukuran jadi：" + _show_range(info["diameter"]) + (" mm" if info["diameter"] else ""))
    lines.append("長度 / Panjang：" + _show_range(info["length"]) + (" mm" if info["length"] else ""))

    paint = info["paint"]
    color = (_verified_paint_color(paint["color_code"], paint_codes)
             if paint["status"] in ("one", "both", "color_only") else None)
    if paint["status"] == "no":
        lines.append("噴漆 / Pengecatan semprot：不噴 / Tidak perlu dicat")
    elif paint["status"] in ("one", "both"):
        text = ("單邊 / Satu sisi" if paint["status"] == "one"
                else "雙邊 / Kedua sisi")
        lines.append("噴漆 / Pengecatan semprot：" + text)
        raw_code = paint["color_code"]
        lines.append("顏色代碼 / Kode warna：" + (raw_code or "待確認 / Perlu diperiksa"))
        if color:
            lines.append("顏色 / Warna：" + color["zh"] + " / " + color["id"])
    elif paint["status"] == "color_only":
        lines.append("噴漆 / Pengecatan semprot：要噴漆（位置待確認） / Wajib dilakukan pengecatan semprot; posisi perlu dikonfirmasi")
        raw_code = paint["color_code"]
        lines.append("顏色代碼 / Kode warna：" + (raw_code or "待確認 / Perlu diperiksa"))
        if color:
            lines.append("顏色 / Warna：" + color["zh"] + " / " + color["id"])
    else:
        lines.append("噴漆 / Pengecatan semprot：位置待確認 / Posisi perlu diperiksa")

    package = info["packaging"]
    detail_en = None
    if package["status"] == "ok":
        code = package["code"]
        if package.get("old_code"):
            code += "（舊碼 / kode lama " + package["old_code"] + "）"
        lines.append("包裝代碼 / Kode kemasan：" + code)
        short_id = _PACKAGING_SHORT_ID.get(package["short"])
        if package["short"]:
            lines.append("包裝方式 / Ringkasan pengemasan：" + package["short"]
                         + (" / " + short_id if short_id else " / Periksa istilah pada tabel"))
        if package["detail"] and package["detail"] != package["short"]:
            lines.append("原表說明：" + package["detail"])
        translated = _PACKAGING_DETAIL_ID.get(package["detail"])
        if not translated and callable(translate_zh_to_id) and package["detail"]:
            try:
                translated = _value(translate_zh_to_id(package["detail"]))
            except Exception:
                translated = None
        detail_en = _PACKAGING_DETAIL_EN.get(package["detail"])
        if not detail_en and callable(translate_zh_to_en) and package["detail"]:
            try:
                detail_en = _value(translate_zh_to_en(package["detail"]))
            except Exception:
                detail_en = None
        if translated:
            lines.append("Rincian pengemasan: " + translated)
        elif not short_id:
            lines.append("Rincian pengemasan: Periksa penjelasan asli dalam bahasa Mandarin")
        elif package["detail"] != package["short"]:
            lines.append("Rincian pengemasan: Rincian dalam bahasa Mandarin perlu diperiksa")
    elif package["status"] == "ambiguous":
        choices = ", ".join(package["candidates"])
        lines.append("包裝 / Pengemasan：舊碼 " + package["code"] + " 對應多種新版方式 " + choices
                     + "，請確認新版代碼 / Kode lama memiliki beberapa metode; pastikan kode baru")
    elif package["status"] == "not_found":
        lines.append("包裝 / Pengemasan：" + package["code"]
                     + "（資料表無此碼，待核對 / Kode tidak ada dalam data; perlu diperiksa）")
    else:
        lines.append("包裝 / Pengemasan：代碼待確認 / Kode perlu diperiksa")

    ring = info["ring"]
    if ring["status"] == "no" and ring["reason"] == "explicit_note":
        ring_text = "不套環（工單備註）/ Tanpa cincin pelindung (catatan pada work order)"
    elif ring["status"] == "yes":
        ring_text = "需要套環 / Wajib memakai cincin pelindung"
    elif ring["status"] == "no":
        ring_text = "不需套環 / Tidak perlu memakai cincin pelindung"
    elif ring["reason"] == "ring_field":
        ring_text = "工單套環欄位待確認 / Kolom cincin pelindung pada work order perlu diperiksa"
    elif ring["reason"] in {"flow", "diameter", "invalid_diameter", "threshold_crossing"}:
        ring_text = "佳東客戶拋光棒20mm規則待確認 / Periksa aturan batang polishing Jia Dong ukuran 20 mm"
    else:
        ring_text = "工單套環資訊待確認 / Informasi cincin pelindung pada work order perlu diperiksa"
    lines.append("套環 / Cincin pelindung：" + ring_text)

    lines.extend(["", "🇬🇧 English summary",
                  "Order: " + (info["order"] or "To confirm"),
                  "Customer: " + (info["customer"] or "To confirm")])
    if storage["status"] == "ok":
        storage_en = storage["area"]
    elif storage["status"] == "unknown_customer":
        storage_en = "Confirm customer or add it to the storage table"
    elif storage["status"] == "unknown_length":
        storage_en = "Confirm length"
    else:
        storage_en = "Confirm storage table: length range is missing or conflicting"
    lines.append("Storage area: " + storage_en)
    lines.append("Finished size: " + (str(info["diameter"][0]) + "–" + str(info["diameter"][1]) + " mm"
                                       if info["diameter"] else "To confirm"))
    lines.append("Length: " + (str(info["length"][0]) + "–" + str(info["length"][1]) + " mm"
                               if info["length"] else "To confirm"))
    if paint["status"] == "no":
        lines.append("Spray paint: None")  # Color is irrelevant in this case.
    elif paint["status"] in ("one", "both"):
        lines.append("Spray paint: " + ("One side" if paint["status"] == "one" else "Both sides"))
        lines.append("Color code: " + (paint.get("color_code") or "To confirm"))
        if color:
            lines.append("Color: " + color["en"])
    elif paint["status"] == "color_only":
        lines.append("Spray paint: Yes; confirm position")
        lines.append("Color code: " + (paint.get("color_code") or "To confirm"))
        if color:
            lines.append("Color: " + color["en"])
    else:
        lines.append("Spray paint: Confirm position")
    if package["status"] == "ok":
        code_en = package["code"]
        if package.get("old_code"):
            code_en += " (legacy " + package["old_code"] + ")"
        lines.append("Packaging code: " + code_en)
        method_en = _PACKAGING_SHORT_EN.get(package["short"])
        if not method_en and callable(translate_zh_to_en) and package["short"]:
            try:
                method_en = _value(translate_zh_to_en(package["short"]))
            except Exception:
                method_en = None
        lines.append("Packaging method: " + (method_en or "Confirm method from source table"))
        if detail_en and package["detail"] != package["short"]:
            lines.append("Packaging details: " + detail_en)
        elif package["detail"] != package["short"]:
            lines.append("Packaging details: Confirm the original Mandarin instructions")
    elif package["status"] == "ambiguous":
        lines.append("Packaging: Legacy code " + package["code"] + " matches "
                     + ", ".join(package["candidates"]) + "; confirm the new code")
    elif package["status"] == "not_found":
        lines.append("Packaging: Code " + package["code"] + " is not in the table; confirm it")
    else:
        lines.append("Packaging: Confirm the code")
    if ring["status"] == "yes":
        ring_en = "Required"
    elif ring["reason"] == "explicit_note":
        ring_en = "Not required (explicit order note)"
    elif ring["status"] == "no":
        ring_en = "Not required"
    elif ring["reason"] == "ring_field":
        ring_en = "Confirm the ring field on the work order"
    elif ring["reason"] in {"flow", "diameter", "invalid_diameter", "threshold_crossing"}:
        ring_en = "Confirm Jia Dong polishing process and finished size"
    else:
        ring_en = "Confirm the work-order ring information"
    lines.append("Protective ring: " + ring_en)
    return "\n".join(lines)
