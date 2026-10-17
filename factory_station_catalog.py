"""Authoritative ERP catalog and source-owned station-number relationships.

The existing equipment, station names, departments and aliases live in one
editable asset. Retrieval is local and sends only relevant rows to the provider.
Number identity alone never licenses a station: units, specifications and
explicit quantity fields win over operational station syntax. This module
does not translate or replace complete sentences.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import translation_mentions

BUILD_ID = "2026-10-17.1-catalog-abbreviation-retrieval"
_PATH = Path(__file__).with_name("factory_station_data.json")


@lru_cache(maxsize=4)
def _read(path, stamp, size):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported factory station catalog schema")
    for section in ("departments", "names", "equipment"):
        if not isinstance(data.get(section), dict) or not data[section]:
            raise ValueError("Missing factory station catalog: " + section)
    for name, row in data["names"].items():
        if not name or not row.get("id") or not row.get("dept"):
            raise ValueError("Invalid station row: " + name)
        if row.get("no") is not None and not isinstance(row["no"], int):
            raise ValueError("Invalid station number: " + name)
    for code, row in data["equipment"].items():
        if not code or not all(row.get(key) for key in ("station", "zh", "id")):
            raise ValueError("Invalid equipment row: " + code)
    for rule in data.get("aliases", ()):
        if rule.get("canonical_zh") not in data["names"]:
            raise ValueError("Alias has no catalog station")
        for pattern in rule.get("patterns", ()):
            re.compile(pattern)
    for code, row in data.get("equipment_ambiguities", {}).items():
        if code not in data["equipment"] or len(row.get("stations", ())) < 2:
            raise ValueError("Invalid shared equipment-code ambiguity: " + code)
    return data


def catalog():
    stat = _PATH.stat()
    return _read(str(_PATH), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=4)
def _fingerprint(path, stamp, size):
    raw = json.dumps(_read(path, stamp, size), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((BUILD_ID + raw).encode()).hexdigest()


def fingerprint():
    stat = _PATH.stat()
    return _fingerprint(str(_PATH), stat.st_mtime_ns, stat.st_size)


STATION_DEPARTMENTS = catalog()["departments"]
STATION_NAMES = catalog()["names"]
STATION_CODES = catalog()["equipment"]
FACTORY_STATION_ALIAS_RULES = tuple(catalog().get("aliases", ()))

_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d+(?![A-Za-z0-9_]|\.\d)")
_UNITS = re.compile(
    r"^\s*(?:支|件|把|捆|台|個|个|頂|顶|人|箱|包|袋|批|噸|吨|公斤|公克|米|公分|毫米|秒|分(?:鐘|钟)?|小時|小时|天|度|%|℃|"
    r"(?:buah|batang|potong|bundel|unit|pcs|orang|ton|kg|g|t|gr(?:am)?|mm|cm|m|meter|menit|detik|jam|hari|rpm)\b)", re.I)
_QUANTITY_LABEL = re.compile(
    r"(?:數量|数量|總數|总数|重量|長度|长度|直徑|直径|速度|金額|金额)\s*(?:為|为|是|[:：=])?\s*$|"
    r"\b(?:jumlah|sebanyak|sejumlah|berjumlah|berat|panjang|diameter|kecepatan|harga)"
    r"(?:\s+(?:barang|material|produk|adalah|sebanyak|sebesar))?\s*[:=]?\s*$", re.I)
_SPEC = re.compile(r"[#＃]\s*\d+(?:\s*[×xX*]\s*\d+)*")
_CLAUSE = re.compile(r"[。！？!?;；\n]|(?<!\d)\.(?=\s|$)")
_VERBS = {
    "zh": {
        "reject": r"拒收|退件|退料|拒絕|拒绝|退回",
        "inspect": r"檢驗|检验|檢查|检查|驗收|验收|不驗|不验",
        "release": r"放行|過帳|过账",
    },
    "id": {
        "reject": r"\b(?:di\s*tolak|ditolak|menolak|tolak|penolakan)\b",
        "inspect": r"\b(?:di\s*periksa|diperiksa|memeriksa|periksa|pemeriksaan|inspeksi|mengecek|dicek)\b",
        "release": r"\b(?:di-?release|me-?release|release|rilis|dirilis|merilis|posting)\b",
    },
}
_STATION_ID = r"(?:pihak\s+)?(?:(?:stasiun|station|pos|bagian)(?:\s+(?:inspeksi|pemeriksaan|peeling|grinding|polishing))?\s*)?"
_OTHER_ID_LABEL = re.compile(
    r"(?:工單|工单|訂單|订单|料號|料号|爐號|炉号|批號|批号|型號|型号|規格|规格)\s*[:：#]?\s*$|"
    r"\b(?:work\s*order|order|wo|heat|batch|jenis|tipe|type|model|kode\s+(?:material|produk))\s*(?:no\.?\s*)?[:#]?\s*$", re.I)


def _lang(lang):
    return "zh" if str(lang).lower().startswith("zh") else "id" if str(lang).lower().startswith("id") else str(lang)


def _prose(text):
    value = unicodedata.normalize("NFKC", str(text or ""))
    spans = list(translation_mentions.mention_spans(value))
    for span in reversed(spans):
        start, end = span[:2]
        value = value[:start] + " " * (end - start) + value[end:]
    return re.sub(r"https?://\S+|__[A-Z0-9_]+__|[「『\"“][^」』\"”\n]*[」』\"”]",
                  lambda match: " " * len(match.group()), value)


def _is_value(text, start, end):
    before, after = text[max(0, start-48):start], text[end:end+32]
    return bool(_UNITS.search(after) or _QUANTITY_LABEL.search(before) or
                _OTHER_ID_LABEL.search(before) or
                re.search(r"[#×xX*ØøΦφ]\s*$", before) or
                re.match(r"\s*(?:[×xX*/:]|[-/]\s*\d)", after) or
                re.search(r"\d\s*[/:-]\s*$", before))


def _role(text, start, end, lang):
    # Local punctuation bounds prevent another sentence's action owning a code.
    left = _CLAUSE.split(text[:start])[-1][-70:]
    right = _CLAUSE.split(text[end:])[0][:70]
    if lang == "zh":
        if re.search(r"(?:給|给|送(?:到|去|至|往)?|移至|轉(?:到|至)|轉交|转交|交(?:到|給|给)|放行(?:資料|资料)?(?:到|至))\s*$", left):
            return "destination", ""
        if re.search(r"(?:在|位於|位于)\s*$", left):
            return "location", ""
        for event, verb in _VERBS[lang].items():
            # 被480拒收; 480檢驗站拒收; 480都不驗.
            if re.match(r"\s*(?:(?:檢驗|检验|檢查|检查|品管|研磨|拋光|抛光|削皮)?站)?"
                        r"\s*(?:那邊|那边|都|已|還|还|不|未|沒|没|沒有|没有|尚未|先|優先|优先|要|會|会)*\s*(?:" + verb + r")", right):
                return "actor", event
        if re.search(r"(?:站(?:別|别|號|号)?|檢驗站|检验站)\s*$", left) or re.match(r"\s*(?:(?:檢驗|检验)?站)", right):
            return "identifier", ""
    elif lang == "id":
        if re.search(r"\b(?:ke|kepada|untuk)\s+" + _STATION_ID + r"$", left, re.I):
            return "destination", ""
        if re.search(r"\b(?:di|pada|dari)\s+" + _STATION_ID + r"$", left, re.I):
            return "location", ""
        for event, verb in _VERBS[lang].items():
            if re.match(r"\s*(?:(?:sudah|telah|belum|tidak|tak|tdk|blm|akan|masih|semua|melakukan)\s+)*(?:" + verb + r")", right, re.I):
                return "actor", event
            # Chat often splits the passive prefix (di tolak) and omits oleh.
            passive = {"reject": r"\bdi\s*tolak\b", "inspect": r"\b(?:di\s*periksa|dicek)\b"}.get(event)
            if passive and re.search(
                    r"(?:" + passive + r")\s+(?:oleh\s+)?" + _STATION_ID + r"$", left, re.I):
                return "actor", event
        if re.search(r"\boleh\s+" + _STATION_ID + r"$", left, re.I):
            return "actor", ""
        if re.search(r"\b(?:stasiun|station|pos)\s+(?:inspeksi\s+|pemeriksaan\s+)?$", left, re.I):
            return "identifier", ""
    return "", ""


def station_facts(source, lang):
    """Parse station identities only where syntax, catalog and value type agree."""
    lang, text = _lang(lang), _prose(source)
    if lang not in _VERBS:
        return []
    numbers = {}
    for name, row in catalog()["names"].items():
        if row.get("no") is not None:
            numbers.setdefault(str(row["no"]), []).append((name, row))
    # Some ERP numbers are documented only through their equipment rows (420
    # in the existing asset). Do not discard these when no station-name row exists.
    for code, row in catalog()["equipment"].items():
        number = str(row["station"])
        if number not in numbers:
            numbers[number] = [(item["zh"], {"id": item["id"], "dept": ""})
                               for item in catalog()["equipment"].values() if str(item["station"]) == number]
    facts = []
    for match in _NUMBER.finditer(text):
        code = match.group()
        if code not in numbers or _is_value(text, *match.span()):
            continue
        role, event = _role(text, *match.span(), lang)
        if not role:
            continue
        rows = numbers[code]
        facts.append({"code": code, "role": role, "event": event,
                      "names_zh": list(dict.fromkeys(name for name, row in rows)),
                      "names_id": list(dict.fromkeys(row["id"] for name, row in rows)),
                      "departments": list(dict.fromkeys(row["dept"] for name, row in rows)),
                      "start": match.start(), "end": match.end()})
    return facts


def _specs(text):
    return Counter(re.sub(r"\s+", "", match.group()).upper().replace("X", "×").replace("*", "×")
                   for match in _SPEC.finditer(unicodedata.normalize("NFKC", str(text or ""))))


def translation_issues(source, candidate, src, tgt):
    src, tgt = _lang(src), _lang(tgt)
    if (src, tgt) not in {("id", "zh"), ("zh", "id")}:
        return []
    issues, observed = [], station_facts(candidate, tgt)
    expected = station_facts(source, src)
    available = list(observed)
    for fact in expected:
        matching = [row for row in available if row["code"] == fact["code"]]
        roles = [row for row in matching if fact["role"] == "identifier" or row["role"] == fact["role"]]
        linked = [row for row in roles if not fact["event"] or row["event"] == fact["event"]]
        if linked:
            available.remove(linked[0])
            continue
        kind = "action_changed" if roles else "role_changed" if matching else "identity_missing"
        issues.append("factory_station:" + kind + ":" + fact["code"])
        for match in re.finditer(r"(?<!\d)" + re.escape(fact["code"]) + r"(?!\d)", str(candidate)):
            if _is_value(str(candidate), *match.span()):
                issues.append("factory_station:identifier_as_quantity:" + fact["code"])
    expected_codes = {fact["code"] for fact in expected}
    source_prose = _prose(source)
    for fact in observed:
        if fact["code"] in expected_codes:
            continue
        occurrences = list(re.finditer(r"(?<!\d)" + re.escape(fact["code"]) + r"(?!\d)", source_prose))
        if occurrences and all(_is_value(source_prose, *match.span()) for match in occurrences):
            issues.append("factory_station:quantity_as_identifier:" + fact["code"])
    missing_specs = _specs(source) - _specs(candidate)
    issues.extend("factory_station:specification_changed:" + spec for spec in missing_specs)
    return list(dict.fromkeys(issues))


def build_prompt(source, src, tgt):
    """Only source-relevant catalog rows, in a compiler-required tagged block."""
    src, tgt = _lang(src), _lang(tgt)
    if (src, tgt) not in {("id", "zh"), ("zh", "id")}:
        return ""
    text, data = _prose(source), catalog()
    rows = []
    for fact in station_facts(source, src):
        rows.append({key: fact[key] for key in ("code", "role", "event", "names_zh", "names_id", "departments")})
    spans = []
    equipment_spans = []
    for code, row in sorted(data["equipment"].items(), key=lambda item: -len(item[0])):
        for match in re.finditer(r"(?<![A-Za-z0-9_])" + re.escape(code) + r"(?![A-Za-z0-9_])", text, re.I):
            if any(match.start() < end and match.end() > start for start, end in equipment_spans):
                continue
            equipment_spans.append(match.span())
            ambiguity = data.get("equipment_ambiguities", {}).get(code)
            if ambiguity:
                rows.append({"equipment": code, "station_options": ambiguity["stations"],
                             "process_requires_context": True})
            else:
                rows.append({"equipment": code, **row})
            break
    for name, row in sorted(data["names"].items(), key=lambda item: -len(item[0])):
        needle = name if src == "zh" else row["id"]
        pattern = re.escape(needle) if src == "zh" else r"(?<![A-Za-z])" + re.escape(needle) + r"(?![A-Za-z])"
        for match in re.finditer(pattern, text, re.I):
            if any(match.start() < end and match.end() > start for start, end in spans):
                continue
            spans.append(match.span())
            rows.append({"name_zh": name, **row,
                         "process_or_station": name in data.get("process_names", ())})
            break
    # Parenthesized catalog abbreviations are searchable identities in either
    # source language (e.g. an English catalog label used in Chinese chat).
    # Actual equipment rows take precedence over a station abbreviation.
    for name, row in data["names"].items():
        for alias in re.findall(r"[（(]([A-Z][A-Z0-9+-]{1,9})[）)]", row["id"]):
            hits = list(re.finditer(r"(?<![A-Za-z0-9_])"+re.escape(alias)+r"(?![A-Za-z0-9_])", text, re.I))
            if hits and not any(a < h.end() and h.start() < b for h in hits for a,b in equipment_spans):
                rows.append({"catalog_abbreviation":alias,"name_zh":name,**row})
    for rule in data.get("aliases", ()):
        if src == "zh" and any(re.search(pattern, text) for pattern in rule["patterns"]):
            rows.append({"alias_zh": rule["canonical_zh"], "id": rule["canonical_id"]})
    department_spans = []
    for name, target in sorted(data["departments"].items(), key=lambda item: -len(item[0])):
        if src != "zh":
            continue
        for match in re.finditer(re.escape(name), text):
            if any(match.start() < end and match.end() > start for start, end in department_spans):
                continue
            department_spans.append(match.span())
            rows.append({"department_zh": name, "id": target})
            break
    specs = list(_specs(source))
    if not rows and not specs:
        return ""
    return ("<factory_station_catalog>\n" + json.dumps({"facts": rows, "specifications": specs},
            ensure_ascii=False, separators=(",", ":")) +
            "\nUse only these source-grounded ERP identities. Keep each number's actor, destination or location "
            "in its own action/clause; a station identifier never supplies a missing quantity. Explicit units "
            "and quantity fields remain quantities. Equipment codes and specifications stay unchanged; never "
            "calculate a specification as a count or station. A process word is a location only with location "
            "syntax; otherwise translate the operation. Completion belongs to its stated action, not a later "
            "data release. Natural target wording may retain just the code when its role is clear. "
            "For process_requires_context equipment, use the explicit source process or confirmed group context; "
            "otherwise keep the machine code without guessing a process. "
            "Do not add a station number to a name-only source.\n</factory_station_catalog>")
