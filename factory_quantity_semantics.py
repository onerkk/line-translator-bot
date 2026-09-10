"""Compositional Chinese→Indonesian quantity semantics for factory messages.

This module is deliberately sentence-independent.  It parses number/classifier
atoms and the relations between them (distribution, addition, correction and
half quantities), then exposes the same frame to prompting and deterministic
validation.  A new wording that uses the same semantics is therefore protected
without adding a verified full-sentence replacement.
"""
from __future__ import annotations

import re
from translation_request_cache import memoize
import unicodedata
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

FACTORY_QUANTITY_SEMANTICS_API_VERSION = 1
FACTORY_QUANTITY_SEMANTICS_BUILD_ID = "2026-09-10.10-referent-bound-quantities"


@dataclass(frozen=True)
class ClassifierSpec:
    source: str
    canonical_id: str
    accepted_id: Tuple[str, ...]
    singular_fused_id: Tuple[str, ...] = ()
    category: str = "count"


# These are classifier meanings, not ordinary word translations.  In particular,
# 包 after a number is a counted package (bungkus), while 包 before an object
# quantity can be the verb "to pack" and is not parsed as a classifier atom.
_CLASSIFIERS: Dict[str, ClassifierSpec] = {
    "包": ClassifierSpec("包", "bungkus", ("bungkus",), ("sebungkus",), "package"),
    "雙": ClassifierSpec("雙", "pasang", ("pasang",), ("sepasang",), "pair"),
    "双": ClassifierSpec("双", "pasang", ("pasang",), ("sepasang",), "pair"),
    "把": ClassifierSpec("把", "bundel", ("bundel",), ("sebundel",), "material_bundle"),
    "捆": ClassifierSpec("捆", "bundel", ("bundel",), ("sebundel",), "material_bundle"),
    "支": ClassifierSpec("支", "batang", ("batang",), ("sebatang",), "rod_piece"),
    "根": ClassifierSpec("根", "batang", ("batang",), ("sebatang",), "rod_piece"),
    "台": ClassifierSpec("台", "buah", ("buah",), ("sebuah",), "machine_count"),
    "個": ClassifierSpec("個", "buah", ("buah",), ("sebuah",), "generic_count"),
    "个": ClassifierSpec("个", "buah", ("buah",), ("sebuah",), "generic_count"),
    "件": ClassifierSpec("件", "buah", ("buah", "barang", "potong"), ("sebuah",), "item_count"),
    "頂": ClassifierSpec("頂", "buah", ("buah",), ("sebuah",), "headwear_count"),
    "顶": ClassifierSpec("顶", "buah", ("buah",), ("sebuah",), "headwear_count"),
    "批": ClassifierSpec("批", "lot", ("lot", "batch"), (), "lot_count"),
    "箱": ClassifierSpec("箱", "kotak", ("kotak", "kardus"), ("sekotak",), "box"),
    "盒": ClassifierSpec("盒", "kotak", ("kotak",), ("sekotak",), "box"),
    "袋": ClassifierSpec("袋", "kantong", ("kantong",), ("sekantong",), "bag"),
    "瓶": ClassifierSpec("瓶", "botol", ("botol",), ("sebotol",), "bottle"),
    "罐": ClassifierSpec("罐", "kaleng", ("kaleng",), ("sekaleng",), "can"),
    "片": ClassifierSpec("片", "lembar", ("lembar",), ("selembar",), "sheet"),
    "張": ClassifierSpec("張", "lembar", ("lembar",), ("selembar",), "sheet"),
    "张": ClassifierSpec("张", "lembar", ("lembar",), ("selembar",), "sheet"),
    "套": ClassifierSpec("套", "set", ("set",), ("satu set",), "set"),
    "組": ClassifierSpec("組", "set", ("set",), ("satu set",), "set"),
    "组": ClassifierSpec("组", "set", ("set",), ("satu set",), "set"),
    "卷": ClassifierSpec("卷", "rol", ("rol", "gulung"), ("satu rol", "segulung"), "roll"),
    "桶": ClassifierSpec("桶", "drum", ("drum", "ember"), ("satu drum", "seember"), "container"),
    "顆": ClassifierSpec("顆", "buah", ("buah",), ("sebuah",), "generic_count"),
    "颗": ClassifierSpec("颗", "buah", ("buah",), ("sebuah",), "generic_count"),
    "筆": ClassifierSpec("筆", "catatan", ("catatan", "data", "entri"), (), "record_count"),
    "笔": ClassifierSpec("笔", "catatan", ("catatan", "data", "entri"), (), "record_count"),
}

# A generic Chinese classifier does not determine an Indonesian noun/classifier.
# Bind it to its referent before deciding whether it is a count or a discourse
# determiner. These are lexical categories, never whole-sentence replacements.
_REFERENTS = {
    "problem": (r"問題|问题", ("masalah", "persoalan", "kendala")),
    "reason": (r"原因|理由", ("alasan", "penyebab", "sebab")),
    "method": (r"方法|辦法|办法|方式", ("cara", "metode")),
    "situation": (r"情況|情况|狀況|状况", ("situasi", "kondisi", "keadaan")),
    "request": (r"要求|請求|请求", ("permintaan", "persyaratan", "syarat")),
    "step": (r"步驟|步骤", ("langkah", "tahap")),
    "plan": (r"方案|計畫|计划|計劃|计画", ("rencana", "solusi")),
    "record": (r"資料|资料|數據|数据|紀錄|记录|記錄|纪录", ("catatan", "data", "entri", "rekaman")),
    "report": (r"案件|案例|回報|回报|通報|通报", ("kasus", "laporan", "catatan", "data", "entri")),
}
_ABSTRACT_REFERENTS = set(_REFERENTS) - {"record", "report"}
_NOUN_ADJECTIVE = r"(?:(?:新|嚴重|严重|重要|明顯|明显|不同|異常|异常)(?:的)?)?"

_CLASSIFIER_PATTERN = "|".join(sorted((re.escape(k) for k in _CLASSIFIERS), key=len, reverse=True))
_ZH_NUM_CHARS = "零〇○一二兩两俩三四五六七八九十百千萬万"
_NUMBER_TOKEN = rf"(?:\d+(?:[.,]\d+)?|[{_ZH_NUM_CHARS}]+)"
_EACH_QUANTITY_RE = re.compile(rf"(?P<each>[每各])(?:一)?(?P<each_cls>{_CLASSIFIER_PATTERN})")
_QUANTITY_RE = re.compile(
    rf"(?P<half_prefix>半)(?P<half_cls>{_CLASSIFIER_PATTERN})"
    rf"|(?P<number>{_NUMBER_TOKEN})(?P<classifier>{_CLASSIFIER_PATTERN})(?P<half_suffix>半)?"
)

_DISTRIBUTIVE_RE = re.compile(r"(?:每人|每位|每個人|每个人|各人|各位|一人(?=[一二兩两俩三四五六七八九十半\d]))")
_ADDITION_RE = re.compile(r"(?:又|另加|另外加|外加|再加|加上|追加|多加)")
_CORRECTION_RE = re.compile(r"(?:不是|不對|不对|更正|改成|改為|改为|應為|应为|正確是|正确是|而是|才是)")

_ID_NUMBER_WORDS = {
    0: "nol", 1: "satu", 2: "dua", 3: "tiga", 4: "empat", 5: "lima",
    6: "enam", 7: "tujuh", 8: "delapan", 9: "sembilan", 10: "sepuluh",
    11: "sebelas",
}
_REFERENT_MATCHERS = [(key, re.compile(_NOUN_ADJECTIVE + "(?:" + noun + ")"))
                      for key, (noun, _) in _REFERENTS.items()]
_COUNT_WORD = "(?:" + "|".join(_ID_NUMBER_WORDS.values()) + "|seratus|seribu|belas|puluh|ratus|ribu|juta)"
_COUNT_VALUE = rf"(?:\d+(?:[.,]\d+)?|{_COUNT_WORD}(?:\s+{_COUNT_WORD}){{0,6}}|sebuah)"
_REFERENT_COUNTS = {key: re.compile(rf"\b(?P<number>{_COUNT_VALUE})\s+(?:buah\s+)?(?:"
                                  + "|".join(re.escape(t) for t in terms) + r")\b", re.I)
                    for key, (_, terms) in _REFERENTS.items()}


@dataclass(frozen=True)
class QuantityAtom:
    atom_id: str
    source_text: str
    start: int
    end: int
    value: str
    quantifier: str
    classifier: str
    canonical_id: str
    accepted_id: Tuple[str, ...]
    singular_fused_id: Tuple[str, ...]
    category: str
    referent: str = ""


@dataclass(frozen=True)
class QuantityRelation:
    relation: str
    left_id: str
    right_id: str
    source_connector: str


def _normalise_source(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).replace(" ", "")


def _decimal_string(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _parse_zh_integer(token: str) -> int | None:
    token = str(token or "")
    if not token:
        return None
    if re.fullmatch(r"\d+", token):
        return int(token)
    digits = {"零": 0, "〇": 0, "○": 0, "一": 1, "二": 2, "兩": 2, "两": 2, "俩": 2,
              "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "萬": 10000, "万": 10000}
    if all(ch in digits for ch in token):
        try:
            return int("".join(str(digits[ch]) for ch in token))
        except ValueError:
            return None
    total = 0
    section = 0
    number = 0
    for ch in token:
        if ch in digits:
            number = digits[ch]
            continue
        unit = units.get(ch)
        if unit is None:
            return None
        if unit == 10000:
            section = (section + number) * unit
            total += section
            section = 0
            number = 0
        else:
            if number == 0:
                number = 1
            section += number * unit
            number = 0
    return total + section + number


def _parse_number(token: str, *, half: bool = False) -> Decimal | None:
    if half:
        return Decimal("0.5")
    raw = str(token or "").replace(",", ".")
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        try:
            return Decimal(raw)
        except InvalidOperation:
            return None
    integer = _parse_zh_integer(raw)
    return Decimal(integer) if integer is not None else None


def _looks_like_packaging_verb(text: str, match: re.Match[str]) -> bool:
    """Exclude 包 used as a verb, e.g. 包2把 / 包兩件 / 包裝.

    The quantity regex starts at the numeral, so this check mainly protects
    malformed or overlapping constructs and makes the intended distinction
    explicit for future classifier additions.
    """
    classifier = match.group("classifier") or match.group("half_cls") or ""
    if classifier != "包":
        return False
    start = match.start()
    left = text[max(0, start - 2):start]
    return bool(re.search(r"(?:要|需|先|再|幫|帮)?包$", left))


@memoize
def build_frame(source: Any, src_lang: str = "zh", tgt_lang: str = "id") -> Dict[str, Any]:
    if not str(src_lang or "").lower().startswith("zh") or not str(tgt_lang or "").lower().startswith("id"):
        return {"active": False, "atoms": [], "relations": [], "distributive": False}
    text = _normalise_source(source)
    atoms: List[QuantityAtom] = []
    occupied: List[Tuple[int, int]] = []

    raw_matches: List[Tuple[int, int, str, re.Match[str]]] = []
    for match in _EACH_QUANTITY_RE.finditer(text):
        raw_matches.append((match.start(), match.end(), "each", match))
    for match in _QUANTITY_RE.finditer(text):
        raw_matches.append((match.start(), match.end(), "cardinal", match))
    # Prefer the wider 每(一)量詞 match over its inner 一量詞 match.
    raw_matches.sort(key=lambda row: (row[0], -(row[1] - row[0]), 0 if row[2] == "each" else 1))

    for start, end, mode, match in raw_matches:
        if any(start < old_end and end > old_start for old_start, old_end in occupied):
            continue
        # ``每個人`` is the distributive person marker "everyone", not an
        # implicit quantity atom ``每個`` (= each item).  Treating the prefix as
        # one generic object made list numbering such as ``每個人：1. ...`` look
        # like a missing "satu buah" and triggered a needless AI repair.
        if (mode == "each"
                and match.group("each_cls") in {"個", "个"}
                and text[end:end + 1] == "人"):
            continue
        if mode == "cardinal" and _looks_like_packaging_verb(text, match):
            continue
        if mode == "each":
            classifier = match.group("each_cls")
            value = Decimal("1")
            quantifier = "each"
        else:
            half_prefix = bool(match.group("half_prefix"))
            classifier = match.group("half_cls") if half_prefix else match.group("classifier")
            value = _parse_number(match.group("number") or "", half=half_prefix)
            quantifier = "cardinal"
            if value is not None and match.group("half_suffix"):
                value += Decimal("0.5")
            if (value is not None and value == value.to_integral_value()
                    and start > 0 and text[start - 1] == "第"):
                quantifier = "ordinal"
        spec = _CLASSIFIERS.get(str(classifier or ""))
        if not spec or value is None:
            continue
        referent = ""
        if classifier in {"個", "个", "件", "筆", "笔"}:
            for key, noun_pattern in _REFERENT_MATCHERS:
                noun_match = noun_pattern.match(text, end)
                if noun_match:
                    referent = key
                    end = noun_match.end()
                    break
            # A bare 筆 is a record counter only with an explicit data/reporting
            # context; it is not the pen noun in e.g. 一筆一畫.
            if not referent and classifier in {"筆", "笔"}:
                clause = re.split(r"[，,。;；!?！？\n]", text[:start])[-1]
                if re.search(r"提報|提报|回報|回报|通報|通报|上報|上报", clause):
                    referent = "report"
                elif re.search(r"資料|资料|數據|数据|紀錄|记录|記錄|登錄|登录|存檔|存档", clause):
                    referent = "record"
                else:
                    continue
            if referent:
                terms = _REFERENTS[referent][1]
                spec = ClassifierSpec(classifier, terms[0], terms, (), "referent_count")
                if quantifier == "cardinal" and value == 1 and referent in _ABSTRACT_REFERENTS:
                    prefix = text[max(0, start - 5):start]
                    if re.search(r"另(?:外)?$", prefix):
                        quantifier = "other"
                    elif re.search(r"同$", prefix):
                        quantifier = "same"
                    elif not re.search(r"只有|僅有|仅有|僅|仅|總共|总共|共有?|恰好|正好|加|又|少|多|第", prefix):
                        quantifier = "indefinite"
        atoms.append(QuantityAtom(
            atom_id="",  # assigned after source-order sorting
            source_text=text[start:end],
            start=start,
            end=end,
            value=_decimal_string(value),
            quantifier=quantifier,
            classifier=classifier,
            canonical_id=spec.canonical_id,
            accepted_id=spec.accepted_id,
            singular_fused_id=spec.singular_fused_id,
            category=spec.category,
            referent=referent,
        ))
        occupied.append((start, end))

    atoms.sort(key=lambda atom: (atom.start, atom.end))
    atoms = [QuantityAtom(**{**asdict(atom), "atom_id": f"q{index}"}) for index, atom in enumerate(atoms, 1)]

    relations: List[QuantityRelation] = []
    for left, right in zip(atoms, atoms[1:]):
        connector = text[left.end:right.start]
        if _ADDITION_RE.search(connector):
            relations.append(QuantityRelation("addition", left.atom_id, right.atom_id, connector))

    cardinal_atoms = [atom for atom in atoms if atom.quantifier == "cardinal"]
    if len(cardinal_atoms) >= 2 and _CORRECTION_RE.search(text):
        relations.append(QuantityRelation(
            "correction", cardinal_atoms[0].atom_id, cardinal_atoms[-1].atom_id, "correction"
        ))

    distributive = bool(_DISTRIBUTIVE_RE.search(text))
    structurally_specific = any(a.category not in {"generic_count", "item_count", "machine_count"} for a in atoms)
    active = bool(atoms and (structurally_specific or distributive or relations))
    return {
        "active": active,
        "source": text,
        "atoms": [asdict(atom) for atom in atoms],
        "relations": [asdict(relation) for relation in relations],
        "distributive": distributive,
        "version": FACTORY_QUANTITY_SEMANTICS_BUILD_ID,
    }


def _number_words(n: int) -> str | None:
    if n in _ID_NUMBER_WORDS:
        return _ID_NUMBER_WORDS[n]
    if 12 <= n < 20:
        return _ID_NUMBER_WORDS[n - 10] + r"\s+belas"
    if 20 <= n < 100:
        tens, ones = divmod(n, 10)
        base = _ID_NUMBER_WORDS[tens] + r"\s+puluh"
        return base if ones == 0 else base + r"\s+" + _ID_NUMBER_WORDS[ones]
    if n == 100:
        return "seratus"
    return None


def _value_pattern(value: str) -> str:
    dec = Decimal(str(value))
    if dec == dec.to_integral_value():
        integer = int(dec)
        parts = [re.escape(str(integer))]
        word = _number_words(integer)
        if word:
            parts.append(word)
        return "(?:" + "|".join(parts) + ")"
    if dec % 1 == Decimal("0.5"):
        whole = int(dec)
        numeric = re.escape(str(dec)).replace(r"\.", r"[.,]")
        if whole == 0:
            return rf"(?:0[.,]5|setengah)"
        word = _number_words(whole)
        if word:
            return rf"(?:{numeric}|{word}\s+setengah)"
        return numeric
    return re.escape(str(dec)).replace(r"\.", r"[.,]")


def _atom_patterns(atom: Mapping[str, Any]) -> List[re.Pattern[str]]:
    value = str(atom.get("value") or "")
    unit_terms = [str(x) for x in atom.get("accepted_id", ()) if str(x)]
    unit_pattern = "(?:" + "|".join(re.escape(x) for x in unit_terms) + ")"
    determiner = atom.get("quantifier")
    if determiner in {"other", "same", "indefinite"}:
        suffix = {"other": r"\s+(?:(?:yang|sama\s+sekali)\s+)?(?:lain(?:nya)?|berbeda|terpisah)",
                  "same": r"\s+(?:yang\s+)?sama", "indefinite": ""}[determiner]
        return [re.compile(rf"\b{unit_pattern}{suffix}\b", re.I)]
    if atom.get("quantifier") == "each":
        return [re.compile(rf"(?<![A-Za-z])(?:setiap|tiap)\s+{unit_pattern}(?![A-Za-z])", re.I)]
    number = Decimal(value)
    word = _number_words(int(number)) if number == number.to_integral_value() else None
    if atom.get("quantifier") == "ordinal":
        forms = [rf"ke-?{re.escape(value)}"]
        if word:
            forms.append("ke" + word)
        if number == 1:
            forms.append("pertama")
        ordinal = "(?:" + "|".join(forms) + ")"
        return [re.compile(rf"(?<![A-Za-z]){unit_pattern}\s+(?:yang\s+)?{ordinal}(?![A-Za-z0-9]|\s+(?:belas|puluh|ratus|ribu|juta|miliar|triliun)\b)", re.I)]
    value_pattern = _value_pattern(value)
    # Do not consume a multiplier/fraction as an adjective between count and
    # unit: 'tiga puluh bundel' is thirty, not three bundles.
    number_tail = r"(?:belas|puluh|ratus|ribu|juta|miliar|triliun|setengah|koma)"
    gap = (r"(?:(?:buah|kasus|entri)\s+)?" if atom.get("referent") else
           rf"(?:(?!{number_tail}\b)\w+\s+){{0,2}}?")
    patterns = [re.compile(rf"(?<![A-Za-z0-9.,]){value_pattern}\s+{gap}{unit_pattern}(?![A-Za-z])", re.I)]
    if word and number >= 2:
        # Collective ke- precedes the noun ('ketiga bundel' = all three).
        # The same numeral after the noun is ordinal ('bundel ketiga').
        # Require the complete number phrase adjacent to its unit so a longer
        # collective such as 'ketiga puluh bundel' cannot match three.
        patterns.append(re.compile(rf"(?<![A-Za-z0-9])ke{word}\s+{unit_pattern}(?![A-Za-z])", re.I))
    if Decimal(value) == Decimal("1"):
        if atom.get("referent"):
            patterns.append(re.compile(rf"\bsebuah\s+{unit_pattern}\b", re.I))
        for fused in atom.get("singular_fused_id", ()) or ():
            patterns.append(re.compile(rf"(?<![A-Za-z]){re.escape(str(fused))}(?![A-Za-z])", re.I))
    return patterns


def _find_atom(candidate: str, atom: Mapping[str, Any], start: int = 0) -> Tuple[int, int] | None:
    best: Tuple[int, int] | None = None
    for pattern in _atom_patterns(atom):
        for match in pattern.finditer(candidate or "", pos=max(0, start)):
            # A suffix of a larger spelled-out value isn't a second quantity:
            # 'dua puluh tiga bundel' cannot satisfy a source saying 三把.
            before = (candidate or "")[:match.start()]
            previous = re.search(r"([A-Za-z]+)\s+$", before)
            if (atom.get("quantifier") == "cardinal" and previous
                    and previous.group(1).casefold() in {
                        *(_ID_NUMBER_WORDS.values()), "belas", "puluh", "ratus", "ribu",
                        "juta", "miliar", "triliun", "setengah", "koma",
                    }):
                continue
            if best is None or match.start() < best[0]:
                best = match.span()
            break
    return best


def _referent_count_issues(frame, target):
    """Reject an explicit quantity moved onto a different abstract/data noun.

    Existence/another-issue determiners cannot license '1 buah data' elsewhere.
    Concrete object quantities continue through the existing classifier checks.
    """
    issues = []
    expected = [a for a in frame.get("atoms", ()) if a.get("referent")]
    for key, pattern in _REFERENT_COUNTS.items():
        for match in pattern.finditer(target):
            # The same surface noun (data/catatan) can describe a reported case
            # or a record. Group those categories; unrelated nouns remain bound.
            compatible = {key} if key not in {"record", "report"} else {"record", "report"}
            value = match.group("number").lower()
            if value == 'sebuah':
                value = '1'
            value = str(next((n for n, word in _ID_NUMBER_WORDS.items() if word == value), value))
            candidates = [a for a in expected if a["referent"] in compatible]
            if not any(re.fullmatch(_value_pattern(a['value']), value, re.I) for a in candidates):
                issues.append("quantity_semantics:unsupported_referent_count:" + key + ":" + value)
    return issues


def _atom_map(frame: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {str(atom.get("atom_id")): atom for atom in frame.get("atoms", []) or []}


@memoize
def validate_translation(frame: Mapping[str, Any], candidate: Any) -> Tuple[bool, List[str]]:
    if not frame or not frame.get("active"):
        return True, []
    target = unicodedata.normalize("NFKC", str(candidate or ""))
    low = target.casefold()
    issues: List[str] = []
    atoms = _atom_map(frame)
    found: Dict[str, Tuple[int, int]] = {}

    for atom_id, atom in atoms.items():
        span = _find_atom(target, atom)
        if span is None:
            issues.append(
                f"quantity_semantics:atom_missing:{atom_id}:{atom.get('value')}:{atom.get('canonical_id')}"
            )
        else:
            found[atom_id] = span
    issues.extend(_referent_count_issues(frame, target))

    # Classifier collision checks catch the exact family of error where 包 is
    # rendered as bundel.  They are source-conditioned so a genuine 把/捆 elsewhere
    # in the same message remains valid.
    has_package = any(atom.get("category") == "package" for atom in atoms.values())
    has_bundle = any(atom.get("category") == "material_bundle" for atom in atoms.values())
    if has_package and not has_bundle and re.search(r"\bbundel\b", low):
        issues.append("quantity_semantics:package_mistranslated_as_bundle")
    has_pair = any(atom.get("category") == "pair" for atom in atoms.values())
    if has_pair and not re.search(r"\bpasang\b|\bsepasang\b", low):
        issues.append("quantity_semantics:pair_classifier_missing")

    for relation in frame.get("relations", []) or []:
        left_id = str(relation.get("left_id") or "")
        right_id = str(relation.get("right_id") or "")
        left_span = found.get(left_id)
        right_span = found.get(right_id)
        if not left_span or not right_span:
            continue
        if left_span[0] >= right_span[0]:
            issues.append(f"quantity_semantics:relation_order:{relation.get('relation')}:{left_id}:{right_id}")
            continue
        between = low[left_span[1]:right_span[0]]
        before_left = low[max(0, left_span[0] - 36):left_span[0]]
        if relation.get("relation") == "addition":
            if not re.search(r"\b(?:ditambah|plus|tambahan|tambahkan)\b", between):
                issues.append(f"quantity_semantics:addition_marker_missing:{left_id}:{right_id}")
        elif relation.get("relation") == "correction":
            if not re.search(r"\b(?:bukan|tidak)\b", before_left + low[left_span[0]:left_span[1]]):
                issues.append(f"quantity_semantics:correction_negation_missing:{left_id}")
            if not re.search(r"\b(?:melainkan|tetapi|tapi|seharusnya|yang\s+benar)\b", between):
                issues.append(f"quantity_semantics:correction_contrast_missing:{left_id}:{right_id}")

    if frame.get("distributive"):
        if not re.search(r"\b(?:setiap\s+orang|masing-masing|per\s+orang|satu\s+orang)\b", low):
            issues.append("quantity_semantics:distributive_person_missing")

    issues = list(dict.fromkeys(issues))
    return not issues, issues


def build_prompt(frame: Mapping[str, Any]) -> str:
    if not frame or not frame.get("active"):
        return ""
    atoms = _atom_map(frame)
    lines = [
        "<factory_quantity_semantics>",
        "This is a compositional quantity frame. Keep counts attached to their nouns; never move a count to another clause or invent a data count from 一下 or an indefinite article.",
    ]
    for atom in frame.get("atoms", []) or []:
        if atom.get("referent"):
            lines.append(f"Atom {atom['atom_id']}: source={atom['source_text']}; referent={atom['referent']}; "
                         f"quantifier={atom['quantifier']}; value={atom['value']}; nouns={'/'.join(atom['accepted_id'])}. "
                         + ("Translate other/same/indefinite as a natural determiner, not a mandatory 1 buah."
                            if atom['quantifier'] in {'other', 'same', 'indefinite'} else "Preserve this noun's count."))
            continue
        lines.append(
            f"Atom {atom.get('atom_id')}: source={atom.get('source_text')}; value={atom.get('value')}; "
            f"quantifier={atom.get('quantifier')}; classifier={atom.get('classifier')} "
            f"=> Indonesian classifier {atom.get('canonical_id')}."
        )
    if any(a.get('quantifier') == 'ordinal' for a in frame.get('atoms', ())):
        lines.append("第 marks an ordinal: noun before ordinal (bundel ketiga). Collective ketiga bundel is a count of three, not the third bundle.")
    for relation in frame.get("relations", []) or []:
        if relation.get("relation") == "addition":
            lines.append(
                f"Relation {relation.get('left_id')} + {relation.get('right_id')} is addition. "
                "Use an explicit additive marker such as 'ditambah'; plain 'dan' is too ambiguous."
            )
        elif relation.get("relation") == "correction":
            lines.append(
                f"Relation {relation.get('left_id')} -> {relation.get('right_id')} is a correction. "
                "Express the rejected amount with 'bukan' and the replacement with 'melainkan' or 'tetapi'."
            )
    if frame.get("distributive"):
        lines.append("The quantity is distributed per person; preserve this with 'setiap orang', 'masing-masing', or an equivalent explicit per-person construction.")
    if any(atom.get("category") == "package" for atom in frame.get("atoms", []) or []):
        lines.append("Classifier 包 after a number means a counted package: use 'bungkus'. Never use 'bundel' for 包; 'bundel' is reserved for 把/捆 material bundles.")
    lines.append("</factory_quantity_semantics>")
    return " ".join(lines)


def semantic_issues(source: Any, candidate: Any, src_lang: str = "zh", tgt_lang: str = "id") -> List[str]:
    frame = build_frame(source, src_lang, tgt_lang)
    return validate_translation(frame, candidate)[1]


__all__ = [
    "FACTORY_QUANTITY_SEMANTICS_API_VERSION",
    "FACTORY_QUANTITY_SEMANTICS_BUILD_ID",
    "build_frame",
    "build_prompt",
    "validate_translation",
    "semantic_issues",
]
