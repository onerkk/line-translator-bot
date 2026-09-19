"""Source-bound material origin/location/part and dimensional attachment.

Indonesian dari, di and bagian/ujung express different relationships. A
direction alone never identifies a plant station or an upstream/downstream
process. Facts feed both the first request and acceptance/learning checks.
Only a fully consumed, declarative report can be rendered locally.
"""
from __future__ import annotations

import re
import unicodedata
from translation_request_cache import memoize
from translation_mentions import mention_spans

BUILD_ID = "2026-09-19.1-material-spatial-relations"
_OBJECT = r"(?:barang|material|bahan|batang)"
_DIRECTION = r"(?:belakang|depan|samping)"
_NUMBER = r"\d+(?:[.,]\d+)?"
_UNIT = r"(?:milimeter|sentimeter|meter|mm|cm|m)"
_SIZE = r"(?:ukuran|diameter|panjang)\s*" + _NUMBER + r"\s*" + _UNIT + r"\b"
_SIZE_RE = re.compile(
    r"\b(?P<role>ukuran|diameter|panjang)\s*(?P<value>" + _NUMBER +
    r")\s*(?P<unit>" + _UNIT + r")\b", re.I)
_MOTION = r"(?:(?:yang\s+)?(?:datang|berasal|dikirim)\s+|yang\s+)?"
_SPATIAL = _MOTION + r"(?:dari|di)\s+(?:arah\s+)?" + _DIRECTION
_RELATION_RE = re.compile(
    r"\b(?P<object>" + _OBJECT + r")(?:\s+(?:ini|itu))?(?:\s+" + _SIZE + r")?\s+" +
    _MOTION + r"(?P<role>dari|di)\s+(?:arah\s+)?(?P<direction>" + _DIRECTION + r")\b", re.I)
_PART_RE = re.compile(
    r"\b(?:bagian|ujung)\s+(?P<direction>" + _DIRECTION + r")\s+(?:dari\s+)?"
    r"(?P<object>" + _OBJECT + r")\b", re.I)
_MANY_RE = re.compile(r"\bbanyak\s+(?:yang|yg)\s+bengkok\b", re.I)
_COMPLETE_RE = re.compile(
    r"\s*" + _OBJECT + r"\s+(?:" + _SPATIAL + r"(?:\s+" + _SIZE + r")?|" +
    _SIZE + r"\s+" + _SPATIAL + r")\s+banyak\s+(?:yang|yg)\s+bengkok\s*[.!。！]?\s*", re.I)
_ZH_OBJECT = r"(?:材料|物料|棒材|料件|工件|鋼材|钢材)"
_ZH_DIRECTIONS = {
    "belakang": r"(?:後面|後方|後邊|後端|后面|后方|后边|后端)",
    "depan": r"(?:前面|前方|前邊|前边|前端)",
    "samping": r"(?:旁邊|旁边|側面|侧面|側邊|侧边|側方|侧方)",
}
_ZH_PARTS = {"belakang": r"(?:後端|后端|尾端|後部|后部|後段|后段)",
             "depan": r"(?:前端|前部|前段)", "samping": r"(?:側面|侧面|側邊|侧边)"}
_ZH_BENT = r"(?:彎曲|弯曲|彎了|弯了|彎掉|弯掉|變彎|变弯|彎的|弯的)"
_ZH_MANY = r"(?:很多|許多|许多|好多|不少|多支|多根|多件)"
_ZH_SIZE_RE = re.compile(r"(?<![\d.,])(?P<value>" + _NUMBER + r")\s*"
                         r"(?P<unit>毫米|公釐|公厘|厘米|公分|公尺|mm\b|cm\b|m\b)", re.I)
_UNIT_CANONICAL = {"mm": "mm", "milimeter": "mm", "毫米": "mm", "公釐": "mm", "公厘": "mm",
                   "cm": "cm", "sentimeter": "cm", "厘米": "cm", "公分": "cm",
                   "m": "m", "meter": "m", "公尺": "m"}
_MEANING = (
    "dari=來源（從…來的），di=所在位置，bagian/ujung=材料部位，三者不可互換。"
    "ukuran 是材料尺寸，不能變成距離端部的長度；未明寫 diameter 不自行補直徑。"
    "banyak yang bengkok 是很多件材料有彎曲，不是單件彎得很嚴重。"
    "後面／前面／旁邊的具體位置未明；不得推定上游、下游、前一道製程或某站別。"
)


def _lang(value):
    return str(value).lower().replace("_", "-").split("-")[0]


def _visible(text):
    value = unicodedata.normalize("NFKC", str(text or ""))
    for start, end, _ in reversed(mention_spans(value)):
        value = value[:start] + " " * (end - start) + value[end:]
    return re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]+|__[A-Za-z0-9_]+__|"
                  r'「[^」]*」|“[^”]*”|"[^"\n]*"', " ", value)


def _clauses(value):
    # Preserve decimal separators and commas before a continuing predicate.
    # An explicit next material subject starts a new relation scope.
    return [part.strip() for part in re.split(
        r"[。;；!?！？\n]|(?<!\d)\.|\.(?!\d)|"
        r"\b(?:sedangkan|tetapi|tapi)\b|[，,]\s*(?=(?:從|从|來自|来自|由)|"
        r"(?:barang|material|bahan|batang)\b)|(?:而|但是)(?=從|从|來自|来自|材料|物料)", value, flags=re.I
    ) if part.strip()]


def _size(match):
    return {"role": match["role"].casefold(), "value": match["value"],
            "unit": _UNIT_CANONICAL[match["unit"].casefold()]}


@memoize
def build_facts(source, lang):
    if _lang(lang) != "id":
        return []
    visible = _visible(source)
    # A wrapped short report is still one sentence. Extra sentences are never
    # collapsed into a complete source; only partial facts are extracted there.
    parts = [visible] if _COMPLETE_RE.fullmatch(visible) else _clauses(visible)
    facts = []
    for clause in parts:
        matches = list(_RELATION_RE.finditer(clause)) + list(_PART_RE.finditer(clause))
        if len(matches) != 1:
            continue  # Do not borrow a size/defect from another material subject.
        match = matches[0]
        role = {"dari": "origin", "di": "location"}.get((match.groupdict().get("role") or "").casefold(), "part")
        sizes = list(_SIZE_RE.finditer(clause))
        many = _MANY_RE.search(clause)
        if many and re.search(r"\b(?:tidak|tak|bukan|belum|jangan|tdk)\s*$", clause[:many.start()], re.I):
            many = None
        facts.append({"sense": "material_spatial_relation", "role": role,
                      "direction": match["direction"].casefold(),
                      "complete": bool(_COMPLETE_RE.fullmatch(visible)),
                      "size": _size(sizes[0]) if len(sizes) == 1 else None,
                      "quantifier": "many_pieces" if many else None,
                      "evidence": clause.strip(), "meaning": _MEANING})
    return facts


def _origin_pattern(direction):
    place = _ZH_DIRECTIONS[direction]
    return (r"(?:(?:從|从|由|來自|来自)\s*" + place +
            r"|" + place + r"\s*(?:送來|送来|運來|运来|來的|来的|過來|过来|來料|来料))")


def _relation_present(clause, fact):
    if not re.search(_ZH_OBJECT, clause):
        return False
    origin = re.search(_origin_pattern(fact["direction"]), clause)
    if fact["role"] == "origin":
        # Negated source/location claims cannot satisfy the positive relation.
        return bool(origin and not re.search(r"不是|並非|并非|不在", clause[:origin.start()][-4:]))
    if origin:
        return False
    if fact["role"] == "part":
        return bool(re.search(_ZH_OBJECT + r"(?:的)?\s*" + _ZH_PARTS[fact["direction"]], clause))
    place = _ZH_DIRECTIONS[fact["direction"]]
    return bool(re.search(r"(?:位[於于]|放在|在)\s*" + place, clause) or
                re.search(place + r"(?:\s|的|尺寸|為|为|[\d.,]|mm|cm|公釐|毫米)*" + _ZH_OBJECT, clause, re.I))


def _same_size(match, size):
    return (match["value"].replace(",", ".") == size["value"].replace(",", ".") and
            _UNIT_CANONICAL[match["unit"].casefold()] == size["unit"])


def _many_bent(clause):
    # Restrict the bridge to affirmative predicates, not an arbitrary gap that
    # could hide 沒有/不. Count applies to pieces, not severity of one bend.
    gap = r"(?:\s|的|都|有|已|經|经|是|出現|出现|發生|发生|支|根|件|個|个)*"
    return bool(re.search(_ZH_MANY + gap + r"(?:" + _ZH_OBJECT + r")?" + gap + _ZH_BENT, clause) or
                re.search(r"(?:彎曲|弯曲|變彎|变弯|彎|弯)\s*的" + gap +
                          r"(?:" + _ZH_OBJECT + r")?" + gap + _ZH_MANY, clause))


def validate(facts, candidate, tgt):
    if _lang(tgt) != "zh":
        return []
    clauses = _clauses(_visible(candidate))
    issues = []
    for fact in facts:
        if fact.get("sense") != "material_spatial_relation":
            continue
        eligible = clauses
        size = fact.get("size")
        if size:
            # Relation and dimension must belong to the same material clause.
            eligible = [c for c in clauses if len(list(_ZH_SIZE_RE.finditer(c))) == 1
                        and any(_same_size(m, size) for m in _ZH_SIZE_RE.finditer(c))]
            if not eligible:
                issues.append("size_attachment_mismatch")
        related = [c for c in eligible if _relation_present(c, fact)]
        if not related:
            issues.append(fact["role"] + "_missing")
            continue
        if fact.get("quantifier") == "many_pieces" and not any(_many_bent(c) for c in related):
            issues.append("many_pieces_as_degree_or_missing")
        process_scopes = [_visible(candidate)] if fact.get("complete") else related
        for clause in process_scopes:
            if re.search(r"上游|下游|前一道|上一(?:站|道)|後一道|下一(?:站|道)|后一道|站別|站别|"
                         r"\d+\s*(?:站|區|区)|(?:研磨|拋光|抛光|削皮|包裝|包装|檢驗|检验|矯直|矫直)站", clause):
                # Explicit process names in the source require full LLM
                # interpretation; a bare directional fact does not license them.
                if not re.search(r"\b(?:upstream|downstream|stasiun|proses)\b", fact["evidence"], re.I):
                    issues.append("unstated_process_inferred")
        for clause in related:
            if size:
                match = next(m for m in _ZH_SIZE_RE.finditer(clause) if _same_size(m, size))
                before, after = clause[:match.start()], clause[match.end():]
                if re.search(r"(?:距離|距离|端部|尾端|後端|后端|前端)\s*(?:的)?\s*$", before) or re.match(r"\s*(?:處|处|以內|以内|範圍|范围)", after):
                    issues.append("size_as_end_distance")
                if size["role"] == "ukuran" and re.search(r"(?:直徑|直径|長度|长度)\s*(?:為|为|是)?\s*$", before):
                    issues.append("dimension_type_invented")
                if size["role"] in {"diameter", "panjang"}:
                    label = r"直徑|直径" if size["role"] == "diameter" else r"長度|长度|長|长"
                    if not re.search(r"(?:" + label + r")\s*(?:為|为|是)?\s*$", before):
                        issues.append("dimension_type_missing")
    return list(dict.fromkeys("factory_material_relation:" + issue for issue in issues))


def canonicalize(source, candidate, src, tgt):
    if (_lang(src), _lang(tgt)) != ("id", "zh") or not candidate:
        return candidate
    raw = str(source or "")
    # Preserve leading mentions exactly. Other protected names or extra clauses
    # make full consumption fail, so a local repair cannot discard them.
    spans = mention_spans(raw) + [(m.start(), m.end(), m.group()) for m in re.finditer(r"__MENTION_\d+__", raw)]
    prefix_end = 0
    for start, end, _ in sorted(spans):
        if raw[prefix_end:start].strip():
            break
        prefix_end = end
    body = raw[prefix_end:]
    if not _COMPLETE_RE.fullmatch(body):
        return candidate
    facts = build_facts(body, src)
    if len(facts) != 1 or not validate(facts, candidate, tgt):
        return candidate
    fact = facts[0]
    location = {"belakang": "後面", "depan": "前面", "samping": "旁邊"}[fact["direction"]]
    relation = "從" + location + "來的" if fact["role"] == "origin" else "在" + location + "的"
    size = fact["size"]
    dimension = ""
    if size:
        label = {"ukuran": "", "diameter": "直徑 ", "panjang": "長度 "}[size["role"]]
        dimension = " " + label + size["value"] + " " + size["unit"] + " "
    else:
        relation += " "
    noun = "棒材" if re.match(r"\s*batang\b", body, re.I) else "材料"
    prefix = raw[:prefix_end] + " " if prefix_end else ""
    return prefix + relation + dimension + noun + "，很多都有彎曲。"
