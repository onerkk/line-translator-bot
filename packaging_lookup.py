"""Deterministic new/legacy packaging-code lookup and validated Excel import.

Each packaging method is stored once, keyed by its new code when available.
Aliases are derived from each row so a duplicate legacy code never overwrites
another method. No translation API or approximate code matching is used.
"""

from __future__ import annotations

import re
import unicodedata


OLD_CODE_FIELD = "原包裝碼"
NEW_CODE_FIELD = "品保設計(新版)"
_DISPLAY_FIELDS = (
    ("簡稱", ("簡稱", "简称")),
    ("詳細包裝方式", ("詳細包裝", "详细包装", "包裝方式", "包装方式")),
    ("內包裝", ("內包裝", "内包装")),
    ("外包裝", ("外包裝", "外包装")),
    ("固定繩", ("固定繩", "固定绳")),
    ("原表待確認", ("原表待確認", "原表待确认")),
)


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def normalize_code(value):
    """Case/width/space tolerance only; 1/I and 0/O remain different codes."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", _text(value))).upper()


def _header(value):
    return re.sub(r"[\s()（）_\-]+", "", _text(value)).lower()


def _code_kind(header):
    key = _header(header)
    if key in {"原包裝碼", "原包装码", "舊包裝碼", "旧包装码", "舊碼", "旧码", "oldcode", "legacycode"}:
        return "old"
    if key in {"品保設計新版", "品保设计新版", "品保設計", "品保设计", "新版包裝碼", "新版包装码", "新版代碼", "新版代码", "新碼", "新码", "newcode"}:
        return "new"
    if key in {"包裝碼", "包装码", "包裝代碼", "包装代码", "代碼", "代码", "代号", "code", "packagingcode"}:
        return "generic"
    return None


def _has_packaging_content(entry):
    return isinstance(entry, dict) and any(
        _text(value) and any(word in _text(header) for word in words)
        for header, value in entry.items()
        for _label, words in _DISPLAY_FIELDS[:-1]
    )


def _codes(key, entry):
    old_code = new_code = ""
    if isinstance(entry, dict):
        for field, value in entry.items():
            kind = _code_kind(field)
            if kind == "old":
                old_code = _text(value)
            elif kind == "new":
                new_code = _text(value)
    aliases = {normalize_code(code) for code in (key, old_code, new_code) if _text(code)}
    return old_code, new_code, aliases


def find_packaging_matches(query, lookup):
    code = normalize_code(query)
    if not code or not isinstance(lookup, dict):
        return []
    matches = []
    for key, entry in lookup.items():
        if not (_has_packaging_content(entry) or isinstance(entry, str) and entry.strip()):
            continue  # A storage/customer table is not packaging data.
        if code in _codes(key, entry)[2]:
            matches.append((key, entry))
    return matches


def _format_entry(key, entry):
    old_code, new_code, _aliases = _codes(key, entry)
    if old_code and new_code:
        lines = ["舊碼：" + old_code + "｜新版：" + new_code]
    elif new_code:
        lines = ["新版代碼：" + new_code]
    else:
        lines = ["包裝碼：" + _text(key)]
    if isinstance(entry, str):
        lines.append(entry)
        return lines
    for label, words in _DISPLAY_FIELDS:
        for field, value in entry.items():
            if _text(value) and any(word in field for word in words):
                lines.append(label + "：" + _text(value))
                break
    return lines


def format_packaging_reply(text, lookup):
    text = unicodedata.normalize("NFKC", _text(text))
    parts = text.split(None, 1)
    if len(parts) < 2 or not normalize_code(parts[1]):
        return (
            "📦 請輸入舊碼或新版代碼 / Masukkan kode lama atau baru\n"
            "範例：/pkg U 或 /pkg 1A（同一種包裝方式）"
        )
    query = parts[1].strip()
    if not lookup:
        return "⚠️ 包裝碼資料尚未上傳\nData kode kemasan belum diupload"
    matches = find_packaging_matches(query, lookup)
    if not matches:
        return ("❌ 找不到包裝碼 / Kode kemasan tidak ditemukan：" + query
                + "\n請輸入完整新／舊代碼，例如 /pkg U 或 /pkg 1A。")
    if len(matches) == 1:
        key, entry = matches[0]
        return "\n".join(["📦 包裝方式 / Cara pengemasan", *_format_entry(key, entry)])
    lines = [
        "📦 代碼 " + query + " 在原表對應 " + str(len(matches)) + " 種包裝方式。",
        "請核對下列方式，使用新版代碼查詢；不會自動選其中一筆。",
        "Kode memiliki beberapa arti; konfirmasikan kode baru.",
    ]
    for key, entry in matches:
        lines.extend(["", "/pkg " + _text(key), *_format_entry(key, entry)])
    return "\n".join(lines)


def packaging_from_rows(rows):
    """Parse a real packaging table; reject wrong sheets and conflicting rows."""
    rows = list(rows)
    if not rows:
        raise ValueError("空的 Excel")
    header = None
    code_cols = {}
    start = 0
    for index, row in enumerate(rows[:10]):
        candidate = [_text(cell) for cell in row]
        kinds = {}
        for col, field in enumerate(candidate):
            kind = _code_kind(field)
            if kind:
                if kind in kinds:
                    raise ValueError("包裝碼欄位重複，請確認 Excel 標題")
                kinds[kind] = col
        if kinds and _has_packaging_content({field: "header" for field in candidate}):
            header, code_cols, start = candidate, kinds, index + 1
            break
    if header is None:
        raise ValueError("請上傳包裝方式表，需含原包裝碼／品保設計(新版)或包裝碼，以及簡稱或包裝方式；儲區表不能匯入此處")
    lookup = {}
    normalized_keys = {}
    for row_number, row in enumerate(rows[start:], start=start + 1):
        if not any(_text(cell) for cell in row):
            continue
        code_values = {kind: _text(row[col]) if col < len(row) else "" for kind, col in code_cols.items()}
        canonical = code_values.get("new") or code_values.get("generic") or code_values.get("old")
        if not canonical:
            raise ValueError("第 " + str(row_number) + " 列缺少包裝碼，未更新資料")
        if any(value and not re.fullmatch(r"[A-Z0-9]{1,12}", normalize_code(value)) for value in code_values.values()):
            raise ValueError("第 " + str(row_number) + " 列包裝碼格式錯誤，未更新資料")
        entry = {field: _text(row[col]) for col, field in enumerate(header)
                 if field and col < len(row) and _text(row[col]) and _code_kind(field) is None}
        if not _has_packaging_content(entry):
            raise ValueError("第 " + str(row_number) + " 列缺少包裝方式，未更新資料")
        if code_values.get("old"):
            entry[OLD_CODE_FIELD] = code_values["old"]
        if code_values.get("new"):
            entry[NEW_CODE_FIELD] = code_values["new"]
        identity = normalize_code(canonical)
        if identity in normalized_keys:
            previous_key = normalized_keys[identity]
            if lookup[previous_key] != entry:
                raise ValueError("包裝碼 " + canonical + " 有不同內容；請提供不同新版代碼，避免覆蓋包裝方式")
            continue  # Repeated identical rows are harmless.
        lookup[canonical] = entry
        normalized_keys[identity] = canonical
    if not lookup:
        raise ValueError("Excel 沒有可用的包裝方式")
    return lookup, header
