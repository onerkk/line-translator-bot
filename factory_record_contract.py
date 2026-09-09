"""Source-derived record facts shared by prompting, acceptance and local rendering.

A set of preserved numbers cannot prove a translation correct: the numbers must
still belong to the same fields. Parse only explicit field/value assertions;
questions, comparisons and unparsed prose never qualify for local rendering.
No sentence lookup, inferred units, or facts borrowed from previous translations.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict

from translation_request_cache import memoize

BUILD_ID = "2026-09-09.107-field-value-record-contract"

_LABELS = {
    "zh": {
        "actual_weight": r"實際(?:過磅|秤重)?重量|实际(?:过磅|秤重)?重量|實際重|实际重|實重|实重|過磅重量|过磅重量|磅重",
        "net_weight": r"淨重|净重",
        "gross_weight": r"毛重",
        "tag_value": r"TAG(?:\s*(?:登錄值|登錄重量|記錄重量|重量|重))?|標籤(?:上的)?重量|标签(?:上的)?重量",
    },
    "id": {
        "actual_weight": r"berat\s+(?:(?:yang\s+)?sebenarnya|aktual|riil|asli|nyata)|(?:berat\s+)?hasil\s+(?:penimbangan|timbang)",
        "net_weight": r"(?:berat\s+)?(?:netto|neto)|berat\s+bersih",
        "gross_weight": r"(?:berat\s+)?bruto|berat\s+kotor",
        "tag_value": r"(?:(?:berat|angka|nilai)\s+(?:(?:yang\s+)?(?:tercatat|tertulis|tertera)\s+)?(?:(?:pada|di|dalam)\s+)?)?(?:TAG|label)(?:-nya)?",
    },
}
_ANCHORS = {lang: re.compile("|".join("(?P<" + role + ">" + rule + ")"
                                    for role, rule in rows.items()), re.I)
            for lang, rows in _LABELS.items()}
_NUMBER = re.compile(r"(?<![\w.])[+-]?\d+(?:[.,]\d+)*(?:(?![\w]|[.,]\d)|(?=(?:kg|g|ton)\b))", re.I)
_NUMBER_ZH = re.compile(r"(?<![A-Za-z0-9.])[+-]?\d+(?:[.,]\d+)*(?:(?![A-Za-z0-9]|[.,]\d)|(?=(?:kg|g|ton)\b))", re.I)
_CONNECTORS = {
    "zh": re.compile(r"(?:\s|[:：=,，]|為|为|是|入|寫|写|填寫|填写|填|登錄|登录|登記|登记|記錄|记录|記|记|輸入|输入|顯示|显示)*", re.I),
    "id": re.compile(r"(?:\s|[:=,\-]|nya\b|yang\b|adalah\b|sebesar\b|bernilai\b|tercatat\b|dicatat\b|ditulis\b|tertulis\b|tertera\b|dimasukkan\b|diinput\b|menunjukkan\b|mencantumkan\b|berat\b)*", re.I),
}
_UNIT = re.compile(r"\s*(kilogram|公斤|千克|kg\b|kilogram|公克|gram\b|g\b|噸|吨|ton\b)", re.I)
_UNITS = {"kilogram": "kg", "公斤": "kg", "千克": "kg", "kg": "kg",
          "公克": "g", "gram": "g", "g": "g", "噸": "ton", "吨": "ton", "ton": "ton"}


def _norm(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _lang(value):
    value = str(value or "").lower()
    return "zh" if value.startswith("zh") else value


def _numeric_forms(value):
    # Preserve exact numbers; permit decimal separator localization. Thousands
    # separators are accepted only in complete groups (1,234 or 1.234.567).
    forms = {value, value.replace(",", ".")}
    if re.fullmatch(r"[+-]?\d{1,3}(?:[,.]\d{3})+", value):
        forms.add(re.sub(r"[,.]", "", value))
    return forms


@memoize
def fields(text, lang):
    """Bind a field to its adjacent value, without crossing another field."""
    text, lang = _norm(text), _lang(lang)
    if lang not in _ANCHORS:
        return []
    anchors = list(_ANCHORS[lang].finditer(text))
    rows = []
    for index, anchor in enumerate(anchors):
        end = anchors[index + 1].start() if index + 1 < len(anchors) else len(text)
        tail = text[anchor.end():end]
        number = (_NUMBER_ZH if lang == "zh" else _NUMBER).search(tail)
        if not number or not _CONNECTORS[lang].fullmatch(tail[:number.start()]):
            continue
        unit = _UNIT.match(tail, number.end())
        rows.append({"role": anchor.lastgroup, "value": number.group(),
                     "unit": _UNITS[unit.group(1).lower()] if unit else "",
                     "start": anchor.start(),
                     "end": anchor.end() + (unit.end() if unit else number.end())})
    # Natural coordinated Indonesian also binds ordered values explicitly.
    if lang == "id" and not rows and len(anchors) >= 2:
        tail = text[anchors[-1].end():]
        marker = re.match(r"\s*(?:adalah\s+)?masing-masing\s+", tail, re.I)
        if marker:
            values = list(_NUMBER.finditer(tail, marker.end()))
            if len(values) == len(anchors):
                for anchor, number in zip(anchors, values):
                    unit = _UNIT.match(tail, number.end())
                    rows.append({"role": anchor.lastgroup, "value": number.group(),
                                 "unit": _UNITS[unit.group(1).lower()] if unit else "",
                                 "start": anchor.start(), "end": len(text), "coordinated": True})
    return rows


def _record_category(text, lang):
    """Bare 入非本月 is a plant ledger idiom, not a date of warehouse arrival.

    Explicit physical destinations take precedence. Non-current-month alone
    neither asserts a record transfer nor means last month.
    """
    if lang == "zh":
        match = re.search(r"(?:入|登錄(?:為|到)?|登录(?:为|到)?|歸入|归入)\s*[「『\"“]?非本月", text)
        if not match:
            return False
        clause = re.split(r"[。；;!?？！\n]", text[match.start():])[0]
        if re.search(r"倉庫|仓库|儲區|储区|料架|貨架|货架|搬運|搬运|入庫|入库|入倉|入仓", clause):
            return False
        return True
    return bool(re.search(r"\b(?:kategori|catatan|data)\b", text, re.I)
                and re.search(r"bukan\s+(?:(?:untuk|milik|pada)\s+)?bulan\s+ini", text, re.I)
                and re.search(r"(?:di|me)?masukkan|(?:di|men)?catat|(?:di|meng)?input|tercatat|masuk\s+(?:ke|dalam)|termasuk|dikelompokkan|didaftarkan", text, re.I))


@memoize
def build_frame(source, src_lang, tgt_lang):
    source, src, tgt = _norm(source), _lang(src_lang), _lang(tgt_lang)
    frame = {"active": False, "source": source, "src": src, "tgt": tgt,
             "fields": [], "record_category": False, "tag_unchanged": False,
             "tag_replaced": False, "question": False, "complete_fields": False, "build": BUILD_ID}
    if (src, tgt) not in {("zh", "id"), ("id", "zh")}:
        return frame
    rows = fields(source, src)
    # A bare TAG number can be an identifier. Interpret it as a recorded value
    # only when another explicitly named weight field establishes that domain.
    if any(row["role"] != "tag_value" for row in rows):
        frame["fields"] = rows
    frame["record_category"] = _record_category(source, src)
    if frame["record_category"]:
        if src == "zh":
            frame["tag_unchanged"] = bool(re.search(
                r"(?:還沒|还没|尚未|沒有|没有|未|沒|没)\s*(?:更換|更换|換|换)\s*TAG|TAG.{0,5}(?:還沒|还没|尚未|沒有|没有|未|沒|没)(?:更換|更换|換|换)", source, re.I))
        else:
            frame["tag_unchanged"] = _tag_pending(source)
        if not frame["tag_unchanged"]:
            frame["tag_replaced"] = _tag_done_zh(source) if src == "zh" else _tag_done(source)
    frame["question"] = bool(re.search(r"[?？]|嗎|吗|是否|是不是", source) or (src == "id" and re.search(r"\bapakah\b", source, re.I)))
    frame["active"] = bool(frame["fields"] or frame["record_category"])
    residue = source
    for row in reversed(frame["fields"]):
        residue = residue[:row["start"]] + residue[row["end"]:]
    # Consume the ENTIRE source. No partial translation can replace a message.
    frame["complete_fields"] = bool(len(frame["fields"]) >= 2 and not frame["question"]
                                    and not any(row.get("coordinated") for row in frame["fields"])
                                    and not re.sub(r"[\s,，;；。.]", "", residue))
    return frame


def _tag_pending(text):
    return bool(re.search(r"\b(?:TAG|label)(?:-nya)?\s+(?:(?:masih|itu)\s+)?(?:belum|tidak)\s+(?:(?:pernah|juga)\s+)?(?:di)?ganti\b|\b(?:belum|tidak)\s+(?:mengganti|ganti)\s+(?:TAG|label)\b", text, re.I))


def _tag_done(text):
    return bool(re.search(r"\b(?:TAG|label)(?:-nya)?\s+(?:itu\s+)?(?:sudah|telah)\s+diganti\b|\b(?:sudah|telah)\s+(?:mengganti|ganti)\s+(?:TAG|label)\b", text, re.I))


def _tag_done_zh(text):
    return bool(re.search(
        r"(?:已經|已经|已)\s*(?:更換|更换|換|换)\s*TAG|TAG.{0,3}(?:已經|已经|已)(?:更換|更换|換|换)|(?:更換|更换|換|换)\s*TAG\s*(?:了|完畢|完毕)", text, re.I))


@memoize
def validate_translation(frame, candidate):
    if not frame.get("active"):
        return True, []
    target = _norm(candidate)
    actual = defaultdict(list)
    for row in fields(target, frame["tgt"]):
        actual[row["role"]].append(row)
    expected = defaultdict(list)
    for row in frame["fields"]:
        expected[row["role"]].append(row)
    issues = []
    units_supplied = any(row["unit"] for row in frame["fields"])
    for role, source_rows in expected.items():
        target_rows = actual[role]
        if len(source_rows) != len(target_rows):
            issues.append("record_field_binding:" + role + ":expected=" + ",".join(r["value"] for r in source_rows))
            continue
        for wanted, got in zip(source_rows, target_rows):
            if not (_numeric_forms(wanted["value"]) & _numeric_forms(got["value"])):
                issues.append("record_field_value:" + role + ":expected=" + wanted["value"] + ":got=" + got["value"])
            if wanted["unit"] and wanted["unit"] != got["unit"]:
                issues.append("record_field_unit:" + role + ":expected=" + wanted["unit"])
            elif not units_supplied and got["unit"]:
                issues.append("record_invented_unit:" + role + ":source_does_not_specify_unit")
    if frame["record_category"]:
        if frame["tgt"] == "id":
            if not _record_category(target, "id"):
                issues.append("record_category:non_current_month_is_record_category_not_arrival_date")
            if frame["tag_unchanged"] and not _tag_pending(target):
                issues.append("record_tag_state:TAG_not_yet_replaced")
            if frame.get("tag_replaced") and not _tag_done(target):
                issues.append("record_tag_state:TAG_already_replaced")
        else:
            if not (re.search(r"非本月|不屬於本月|不属于本月", target) and re.search(r"入|登錄|登录|記錄|记录|分類|分类|資料|资料|帳|账", target)):
                issues.append("record_category:preserve_non_current_month_record")
            if frame["tag_unchanged"] and not re.search(r"(?:沒|没|未|沒有|没有).{0,3}(?:換|换)|(?:換|换).{0,3}(?:沒|没)", target):
                issues.append("record_tag_state:TAG_not_yet_replaced")
            if frame.get("tag_replaced") and not _tag_done_zh(target):
                issues.append("record_tag_state:TAG_already_replaced")
    if frame["question"] and not re.search(r"[?？]|\bapakah\b|嗎|吗|是否", target, re.I):
        issues.append("record_question:preserve_question_not_assertion")
    return not issues, list(dict.fromkeys(issues))


def build_prompt(frame):
    if not frame.get("active"):
        return ""
    facts = [{key: row[key] for key in ("role", "value", "unit")} for row in frame["fields"]]
    lines = ["<record_facts>",
             "Translate these current-source facts as natural connected language. Keep each value attached to its field. Do not infer units, dates or missing causes."]
    if facts:
        lines.append(json.dumps(facts, ensure_ascii=False, separators=(",", ":")))
        lines.append("actual_weight = berat aktual / 實重; tag_value = angka yang tercatat pada TAG / TAG 登錄值. These are separate fields, not commands to change a weight.")
    if frame["record_category"]:
        lines.append("入非本月 denotes entry into the plant's non-current-month RECORD CATEGORY (data dimasukkan ke kategori bukan untuk bulan ini). It does not assert when the material physically arrived and does not mean last month. Retain the original subject without inventing new facts.")
    if frame["tag_unchanged"]:
        lines.append("TAG replacement is NOT YET done (TAG-nya belum diganti); preserve this separate predicate.")
    if frame.get("tag_replaced"):
        lines.append("TAG replacement is ALREADY done (TAG-nya sudah diganti); preserve this separate predicate.")
    if frame["question"]:
        lines.append("The source is a question. Preserve uncertainty instead of asserting that the suspected event occurred.")
    lines.append("</record_facts>")
    return "\n".join(lines)


def render_complete(frame):
    """Zero-provider path ONLY for fully parsed explicit field assertions."""
    if not frame.get("complete_fields"):
        return None
    labels = {"id": {"actual_weight": "Berat aktual", "tag_value": "Angka pada TAG tercatat",
                      "net_weight": "Berat bersih", "gross_weight": "Berat kotor"},
              "zh": {"actual_weight": "實重", "tag_value": "TAG 登錄值", "net_weight": "淨重", "gross_weight": "毛重"}}
    parts = []
    rows = frame["fields"]
    for index, row in enumerate(rows):
        parts.append(labels[frame["tgt"]][row["role"]] + " " + row["value"] + (" " + row["unit"] if row["unit"] else ""))
        if index + 1 < len(rows):
            gap = frame["source"][row["end"]:rows[index + 1]["start"]]
            parts.append("\n" * gap.count("\n") if "\n" in gap else ("; " if frame["tgt"] == "id" else "，"))
    result = "".join(parts) + ("." if frame["tgt"] == "id" else "。")
    return result if validate_translation(frame, result)[0] else None
