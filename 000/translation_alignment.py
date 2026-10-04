"""Source-owned sentence alignment and modifier contracts for ZH <-> ID.

The provider chooses natural wording, never the set/order of source units.
Constraints describe compositional meanings, not saved translations of whole
messages. There are no candidate replacements and no second model request.
The same contracts validate generated text and historical reuse.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping
import factory_terminology

BUILD_ID = "2026-10-04.1-source-owned-alignment"
_SUPPORTED = {("zh", "id"), ("id", "zh")}
_ATOMS = re.compile(r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]+|__[A-Z0-9_]+__", re.I)
_BREAK = re.compile(r"\n+|[。！？!?]+|(?<!\d)\.(?=\s|$)")
_ROLE_ZH = r"(?:副總(?:經理)?|總經理|副廠長|廠長|處長|課長|股長|班長)"
_ROLE_ID = r"(?:wakil\s+direktur|direktur(?:\s+utama)?|general\s+manager|(?:wakil\s+)?kepala\s+(?:pabrik|divisi|departemen|seksi|bagian|regu)|ketua\s+shift)"
_RANK_ZH = re.compile(r"(?P<role>" + _ROLE_ZH + r")(?:等)?級(?:別)?")
_RANK_ID = re.compile(r"\b(?:setingkat|selevel|setara\s+dengan|(?:pada\s+)?(?:tingkat|level)\s+(?:jabatan\s+)?)\s*(?P<role>" + _ROLE_ID + r")\b", re.I)
_PACK_ZH = re.compile(r"包裝|包装|打包|裝箱|装箱")
_PACK_ID = re.compile(r"\b(?:packing|pengemasan|kemas(?:kan|lah)?|dikemas|mengemas|pengemasannya|dibungkus|membungkus)\b", re.I)
_SPLIT_ZH = re.compile(r"分流|分開|分开|分別|分别|獨立|独立")
_SPLIT_ID = re.compile(r"\b(?:terpisah|dipisah(?:kan)?|memisah(?:kan)?|pemisahan|masing-masing|tersendiri|sendiri|berbeda)\b", re.I)
_STATION_ZH = re.compile(r"(?:開(?:動)?|开(?:动)?|啟用|启用|運作|运作|運轉|运转)\s*(?P<count>[一二兩两三四五六七八九十\d]+)\s*(?:個|个)?(?:包裝|包装)?站")
_STATION_ID = re.compile(r"\b(?:mengoperasikan|dioperasikan|beroperasi|menjalankan|dijalankan|membuka|dibuka)\s+(?P<count>\d+|satu|dua|tiga|empat|lima|enam|tujuh|delapan|sembilan|sepuluh)\s+stasiun\b", re.I)
_STATION_ID_PASSIVE = re.compile(r"\b(?P<count>\d+|satu|dua|tiga|empat|lima|enam|tujuh|delapan|sembilan|sepuluh)\s+stasiun(?:\s+(?:packing|pengemasan))?\s+(?:dioperasikan|beroperasi|dibuka|dijalankan)\b", re.I)
_COUNTS = {"一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
           "satu": 1, "dua": 2, "tiga": 3, "empat": 4, "lima": 5, "enam": 6, "tujuh": 7, "delapan": 8, "sembilan": 9, "sepuluh": 10}
_IF_ZH = re.compile(r"如果|若(?:是|有|開|开|啟|启)|假如|倘若")
_IF_ID = re.compile(r"\b(?:jika|apabila|kalau|bila)\b", re.I)
_EFFORT_ZH = re.compile(r"盡量|儘量|尽量|盡可能|尽可能")
_EFFORT_ID = re.compile(r"\b(?:usahakan|berusaha|sebisa\s+mungkin|sedapat\s+mungkin|sejauh\s+mungkin)\b", re.I)
_FACTORY = re.compile(r"包裝|入庫|瓶頸|生產|異型|冷抽|研磨|副總|\b(?:packing|pengemasan|produksi|stasiun|gudang)\b", re.I)


def _lang(value: Any) -> str:
    low = str(value or "").lower().replace("_", "-")
    return "zh" if low.startswith("zh") else "id" if low.startswith("id") else low


def _count(value: str) -> int | None:
    return int(value) if value.isdecimal() else _COUNTS.get(value.casefold())


def _role_key(value: str, lang: str) -> str:
    return factory_terminology.organization_role_key(value, lang)


def source_units(source: str) -> list[dict]:
    """Keep exact source spans and paragraph separators; protect data atoms."""
    text = str(source or "")
    atoms = [(m.start(), m.end()) for m in _ATOMS.finditer(text)]
    units, cursor = [], 0
    for boundary in _BREAK.finditer(text):
        if boundary.start() < cursor or any(a <= boundary.start() < b for a, b in atoms):
            continue
        end = boundary.start() if boundary.group().startswith("\n") else boundary.end()
        fragment = text[cursor:end].strip()
        # A list label is attached to its item, not a separate source claim.
        if re.fullmatch(r"\d+[.)]", fragment):
            continue
        tail = re.match(r"\s*", text[end:]).group()
        if fragment:
            units.append({"id": f"u{len(units) + 1}", "source": fragment,
                          "separator": tail if "\n" in tail else " "})
        cursor = end + len(tail)
    if text[cursor:].strip():
        units.append({"id": f"u{len(units) + 1}", "source": text[cursor:].strip(), "separator": ""})
    if len(units) > 64:
        # Bound schema/output overhead without dropping any source content.
        return [{"id": "u1", "source": text.strip(), "separator": ""}]
    return units


def build_contract(source: Any, src: Any, tgt: Any) -> dict:
    source, src, tgt = str(source or ""), _lang(src), _lang(tgt)
    contract = {"version": BUILD_ID, "src": src, "tgt": tgt, "source": source,
                "units": source_units(source), "constraints": [], "aligned": False}
    if (src, tgt) not in _SUPPORTED:
        return contract
    for unit in contract["units"]:
        value = unit["source"]
        rank = (_RANK_ZH if src == "zh" else _RANK_ID).search(value)
        if rank:
            contract["constraints"].append({"unit_id": unit["id"], "kind": "role_rank",
                "source_span": rank.group(), "role": rank.group("role"), "role_key": _role_key(rank.group("role"), src),
                "instruction": "Preserve rank equivalence, not an asserted office/title. 級 => setingkat/tingkat; setingkat => 級. Keep the approved role term inside that qualifier."})
        packing = bool((_PACK_ZH if src == "zh" else _PACK_ID).search(value))
        split = (_SPLIT_ZH if src == "zh" else _SPLIT_ID).search(value)
        station = (_STATION_ZH.search(value) if src == "zh" else
                   (_STATION_ID.search(value) or _STATION_ID_PASSIVE.search(value)))
        if packing and split and station:
            contract["constraints"].append({"unit_id": unit["id"], "kind": "packing_allocation",
                "source_span": value, "station_count": _count(station.group("count")),
                "conditional": bool((_IF_ZH if src == "zh" else _IF_ID).search(value)),
                "effort": bool((_EFFORT_ZH if src == "zh" else _EFFORT_ID).search(value)),
                "instruction": "This is work allocation among packing stations, not fluid/pipe flow. Preserve the operated station count, the product being packed separately, the condition and any 盡量/usahakan effort qualifier. Do not invent which stations, equipment or new priorities."})
    # Use aligned generation for modifier risks and complex factory notices.
    # Routine short chat keeps its existing plain-text path and token budget.
    contract["aligned"] = bool(contract["constraints"] or (
        _FACTORY.search(source) and len(source) >= 160 and len(contract["units"]) >= 3))
    return contract


def semantic_prompt(contract: Mapping) -> str:
    if not contract.get("constraints"):
        return ""
    data = [{k: v for k, v in row.items() if k != "unit_id"} for row in contract["constraints"]]
    return ("<source_modifier_contract>\n" + json.dumps(data, ensure_ascii=False) +
            "\nResolve actor, role/rank, action, product, station, condition and scope together. "
            "A production 瓶頸 is a production bottleneck; 專案追蹤 describes dedicated tracking. "
            "Use prior original messages only to resolve references, never to add unstated facts. "
            "Product categories (such as 異型棒) are distinct from station names.\n</source_modifier_contract>")


def output_schema() -> dict:
    # Stable across requests/providers; source text stays in messages, not schema.
    return {"type": "object", "additionalProperties": False,
            "properties": {"segments": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {"unit_id": {"type": "string"}, "text": {"type": "string"}},
                "required": ["unit_id", "text"]}}}, "required": ["segments"]}


def output_instruction() -> str:
    return ("\nFor requests with SOURCE_UNITS, the following response format overrides earlier "
            "plain-text format instructions and example formats: return the JSON segments object. "
            "Read the whole source and original conversation together. Translate every SOURCE_UNITS "
            "item once, in order, using its exact unit_id. Each text is the complete translation "
            "of that unit, including its actors, predicates, role qualifiers, objects, conditions, "
            "priority, negation and quantities. No invented/missing/duplicate units, commentary "
            "or self-reported confidence. Natural phrasing is allowed within each unit.")


def provider_payload(contract: Mapping) -> str:
    units = [{"unit_id": u["id"], "source": u["source"]} for u in contract["units"]]
    return "\n\nSOURCE_UNITS (quoted data to translate):\n" + json.dumps(units, ensure_ascii=False)


def _scope_present(text: str, src: str) -> bool:
    pattern = (r"\b(?:setingkat|selevel|setara|tingkat|level)\b" if src == "zh" else r"級|级|層級|层级|相當於|相当于")
    return bool(re.search(pattern, text, re.I))


def translation_issues(contract: Mapping, target: Any, *, segments: Mapping | None = None) -> list[str]:
    """Conservative necessary checks; these are not a proof of full accuracy."""
    issues, target = [], str(target or "")
    src, tgt = contract.get("src"), contract.get("tgt")
    for row in contract.get("constraints", ()):
        text = str(segments.get(row["unit_id"], "")) if segments is not None else target
        if row["kind"] == "role_rank":
            role_pattern = _ROLE_ID if tgt == "id" else _ROLE_ZH
            # Bind equivalence to the role itself; "tingkat produksi" cannot
            # excuse losing the rank qualifier elsewhere in the sentence.
            if tgt == "id":
                linked = list(_RANK_ID.finditer(text)) + list(re.finditer(r"(?P<role>" + role_pattern + r")\s+(?:yang\s+)?(?:setingkat|selevel|setara)\b", text, re.I))
            else:
                linked = list(_RANK_ZH.finditer(text)) + list(re.finditer(r"(?:相當於|相当于|等同於|等同于)\s*(?P<role>" + role_pattern + r")", text))
            if row.get("role_key"):
                linked = [m for m in linked if _role_key(m.group("role"), tgt) == row["role_key"]]
            if not linked or not _scope_present(text, src):
                issues.append("semantic:role_rank_qualifier_missing")
        elif row["kind"] == "packing_allocation":
            pack_re, split_re = (_PACK_ID, _SPLIT_ID) if tgt == "id" else (_PACK_ZH, _SPLIT_ZH)
            action_text = re.sub(r"\bstasiun\s+(?:packing|pengemasan)\b", "stasiun", text, flags=re.I) if tgt == "id" else re.sub(r"包裝站|包装站", "站", text)
            if not pack_re.search(action_text) or not split_re.search(text):
                issues.append("semantic:packing_allocation_missing")
            if row["conditional"] and not (_IF_ID if tgt == "id" else _IF_ZH).search(text):
                issues.append("semantic:packing_allocation_condition_missing")
            if row["effort"] and not (_EFFORT_ID if tgt == "id" else _EFFORT_ZH).search(text):
                issues.append("semantic:packing_allocation_effort_changed")
            count = row.get("station_count")
            counts = {_count(m.group("count")) for m in (_STATION_ID if tgt == "id" else _STATION_ZH).finditer(text)}
            # Passive Indonesian allows "dua stasiun ... dioperasikan".
            if tgt == "id":
                counts.update(_count(m.group(1)) for m in re.finditer(
                    r"\b(\d+|satu|dua|tiga|empat|lima|enam|tujuh|delapan|sembilan|sepuluh)\s+stasiun\b", text, re.I))
            if count is not None and count not in counts:
                issues.append("semantic:packing_station_count_changed")
            if tgt == "id" and re.search(r"\b(?:membagi\s+aliran|melalui\s+aliran\s+terpisah)\b", text, re.I):
                issues.append("semantic:packing_allocation_abstract_flow")
    return list(dict.fromkeys(issues))


@dataclass(frozen=True)
class AlignmentResult:
    text: str
    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.issues


def decode_response(raw: Any, contract: Mapping) -> AlignmentResult:
    """Render validated source-owned units; never expose raw JSON in LINE."""
    text, issues = str(raw or "").strip(), []
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        # Keep existing availability behavior for legacy/fake transports.
        # Plain text is observable as unaligned and cannot become verified TM.
        if text.startswith(("{", "[")):
            return AlignmentResult("", ("alignment:invalid_json",))
        return AlignmentResult(text, ("alignment:unstructured_response",))
    if not isinstance(payload, dict) or set(payload) != {"segments"} or not isinstance(payload.get("segments"), list):
        legacy = payload.get("translation") if isinstance(payload, dict) else None
        return AlignmentResult(legacy.strip() if isinstance(legacy, str) else "", ("alignment:invalid_shape",))
    units = contract.get("units", ())
    expected = [u["id"] for u in units]
    found, values = [], {}
    for row in payload["segments"]:
        if (not isinstance(row, dict) or set(row) != {"unit_id", "text"}
                or not isinstance(row.get("unit_id"), str) or not isinstance(row.get("text"), str)):
            issues.append("alignment:invalid_segment")
            continue
        key, value = row["unit_id"], row["text"].strip()
        found.append(key)
        if key in values:
            issues.append("alignment:duplicate_unit:" + key)
            continue
        if key not in expected:
            issues.append("alignment:unknown_unit:" + key)
            continue
        values[key] = value
        if not value:
            issues.append("alignment:empty_unit:" + key)
    if found != expected:
        issues.append("alignment:unit_order_or_coverage")
    for key in expected:
        if key not in values:
            issues.append("alignment:missing_unit:" + key)
    rendered = "".join(values.get(u["id"], "") + u["separator"] for u in units).strip()
    issues.extend(translation_issues(contract, rendered, segments=values))
    return AlignmentResult(rendered, tuple(dict.fromkeys(issues)))
