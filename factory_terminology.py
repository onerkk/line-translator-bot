"""High-throughput factory terminology retrieval shared by text and OCR flows.

The glossary may contain hundreds or thousands of plant terms, abbreviations,
equipment names and organizational-unit labels.  Sending the entire glossary to
an LLM is slow and reduces translation quality.  This module builds a reusable
longest-match index, retrieves only source-grounded terms, and adds deterministic
organization-unit semantics such as ``一課`` and plant-specific 股別 aliases.

The module does not translate complete sentences and does not contain sentence-
specific patches.  It supplies terminology constraints and OCR recognition hints
that are consumed by the standard translation pipeline.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import glossary_policy as gp_module

FACTORY_TERMINOLOGY_API_VERSION = 1
FACTORY_TERMINOLOGY_BUILD_ID = "2026-09-22.1-packaging-protection-senses"

_CACHE_LOCK = threading.RLock()
_ENGINE_CACHE: Dict[Tuple[int, int], "FactoryTerminologyEngine"] = {}
_TRIE_END = object()


# A sleeve and a protective ring are separate work-order accessories. The
# shop-floor use of "kondom" is contextual; it must never become an ordinary
# reverse-glossary entry that rewrites personal/health conversations.
_PACKAGING_CONTEXT_RE = re.compile(
    r"工[單单]|包[裝装]|棒材|(?:研磨|[拋抛]光)棒|"
    r"\b(?:work\s*order|book\s*order|lembar\s+kerja|packing|pengemasan|"
    r"batang|bundel|grinding|polishing)\b", re.I,
)
_OTHER_PROTECTION_SENSE_RE = re.compile(
    r"避孕|保[險险]套|安全套|性行[為为]|性[愛爱]|[陰阴]莖|手機|手机|平板|"
    r"\b(?:kontrasepsi|seks(?:ual)?|hubungan\s+intim|kesehatan|hamil|kehamilan|"
    r"penis|HIV|HPV|sifilis|ponsel|handphone|smartphone|"
    r"(?:pabrik|produksi|memproduksi)\s+kondom)\b", re.I,
)
_RING_ZH_RE = re.compile(r"保[護护][環环]|套[環环]")
_SLEEVE_ZH_RE = re.compile(r"保[護护]套|套罩")
_SEXUAL_ZH_RE = re.compile(r"保[險险]套|安全套|避孕套")
_RING_ID_RE = re.compile(r"\bcincin[\s-]+pelindung\b", re.I)
_SLEEVE_ID_RE = re.compile(r"\b(?:selubung|sarung)[\s-]+pelindung\b", re.I)
_KONDOM_RE = re.compile(r"\bkondom(?:\s+pelindung)?\b", re.I)
_TERM_IDENTITY_RE = re.compile(
    r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]+|"
    r"@[^\s,，。;；]+|__[A-Za-z0-9_]+__", re.I,
)
_TERM_QUOTE_RE = re.compile(
    r'''"[^"\n]+"|'[^'\n]+'|“[^”\n]+”|‘[^’\n]+’|「[^」\n]+」|『[^』\n]+』|＂[^＂\n]+＂|\x60[^\x60\n]+\x60'''
)


def _packaging_prose(text, source=None):
    """Mask identities and source-owned quoted literals, retaining offsets."""
    text = str(text or "")
    def quoted(match):
        content = match.group(0)[1:-1]
        # Only control labels are masked in the source. In the target, an
        # actual source quote is immutable even when its surrounding quote
        # style changes. Newly mistranslated quoted nouns remain repairable.
        control = bool(re.fullmatch(r"(?:NO\s+Kondom|[NY-])", content, re.I))
        preserve = control if source is None else content in str(source)
        return " " * len(match.group(0)) if preserve else match.group(0)
    text = _TERM_QUOTE_RE.sub(quoted, text)
    return _TERM_IDENTITY_RE.sub(lambda m: " " * len(m.group(0)), text)


def packaging_term_senses(source, src_lang):
    """Read accessory senses from this source, never from a previous message."""
    prose = _packaging_prose(source)
    if _OTHER_PROTECTION_SENSE_RE.search(prose):
        return set()
    lang = str(src_lang).lower()
    senses = set()
    if lang.startswith("zh"):
        if _RING_ZH_RE.search(prose):
            senses.add("ring")
        if _PACKAGING_CONTEXT_RE.search(prose) and _SLEEVE_ZH_RE.search(prose):
            senses.add("sleeve")
    elif lang.startswith("id"):
        if _RING_ID_RE.search(prose):
            senses.add("ring")
        if (_PACKAGING_CONTEXT_RE.search(prose)
                and (_KONDOM_RE.search(prose) or _SLEEVE_ID_RE.search(prose))):
            senses.add("sleeve")
    return senses


def _packaging_replace(source, candidate, pattern, replacement):
    # Spans come from the masked view, but edits apply to the original bytes.
    # Nothing else (negation, flags, sizes, exceptions, names) is rewritten.
    result = str(candidate)
    for match in reversed(list(pattern.finditer(_packaging_prose(result, source)))):
        result = result[:match.start()] + replacement + result[match.end():]
    return result


def canonicalize_packaging_translation(source, candidate, src_lang, tgt_lang):
    if not candidate:
        return candidate
    src, tgt = str(src_lang).lower(), str(tgt_lang).lower()
    if not ((src.startswith("zh") and tgt.startswith("id"))
            or (src.startswith("id") and tgt.startswith("zh"))):
        return candidate
    senses = packaging_term_senses(source, src)
    # With two different accessories, global noun substitution cannot recover
    # their clause alignment. Guide generation and diagnose; never swap both.
    if len(senses) != 1:
        return candidate
    sense = next(iter(senses))
    result = str(candidate)
    if tgt.startswith("zh"):
        wrong = _SEXUAL_ZH_RE
        replacement = "保護環" if sense == "ring" else "保護套"
        result = _packaging_replace(source, result, wrong, replacement)
        wrong_object = _SLEEVE_ZH_RE if sense == "ring" else _RING_ZH_RE
        result = _packaging_replace(source, result, wrong_object, replacement)
        if re.search(r"\b(?:work\s*order|book\s*order|lembar\s+kerja)\b", _packaging_prose(source), re.I):
            result = _packaging_replace(source, result, re.compile(r"工作[單单]"), "工單")
    else:
        replacement = "Cincin Pelindung" if sense == "ring" else "selubung pelindung"
        result = _packaging_replace(source, result, _KONDOM_RE, replacement)
        wrong_object = _SLEEVE_ID_RE if sense == "ring" else _RING_ID_RE
        result = _packaging_replace(source, result, wrong_object, replacement)
    return result


def packaging_translation_issues(source, candidate, src_lang, tgt_lang):
    src, tgt = str(src_lang).lower(), str(tgt_lang).lower()
    if not ((src.startswith("zh") and tgt.startswith("id"))
            or (src.startswith("id") and tgt.startswith("zh"))):
        return []
    senses = packaging_term_senses(source, src)
    if not senses:
        return []
    prose = _packaging_prose(candidate, source)
    patterns = ({"ring": _RING_ZH_RE, "sleeve": _SLEEVE_ZH_RE}
                if tgt.startswith("zh") else {"ring": _RING_ID_RE, "sleeve": _SLEEVE_ID_RE})
    issues = ["factory_packaging:" + sense + "_missing_or_confused"
              for sense in sorted(senses) if not patterns[sense].search(prose)]
    if (_SEXUAL_ZH_RE if tgt.startswith("zh") else _KONDOM_RE).search(prose):
        issues.append("factory_packaging:sexual_sense")
    return issues


def build_packaging_prompt(source, src_lang, tgt_lang):
    if not ((str(src_lang).startswith("zh") and str(tgt_lang).startswith("id"))
            or (str(src_lang).startswith("id") and str(tgt_lang).startswith("zh"))):
        return ""
    senses = packaging_term_senses(source, src_lang)
    if not senses:
        return ""
    lines = ["<factory_packaging_terms>"]
    if "ring" in senses:
        lines.append("保護環/套環 = Cincin Pelindung (protective ring), not a sleeve or condom.")
    if "sleeve" in senses:
        lines.append("In this work-order/material-packaging source, kondom means 保護套/套罩 "
                     "(protective sleeve); use selubung pelindung in Indonesian, not 保險套 or 保護環.")
    lines.append("Keep source labels such as N, Y and quoted NO Kondom verbatim. "
                 "Preserve the source's must/must-not, size conditions and exceptions; "
                 "never infer a label's instruction from N/Y alone. "
                 "Apply no packaging sense to health/personal text.")
    lines.append("</factory_packaging_terms>")
    return "\n".join(lines)


def canonicalize_equipment_translation(source, candidate, src_lang, tgt_lang):
    """Remove a known leaked equipment scope only when the source is generic.

    Never delete arbitrary codes to make a validator pass. These replacements
    are narrow glossary migrations and preserve every other target clause.
    """
    result = str(candidate or "")
    if (src_lang, tgt_lang) != ("id", "zh") or not result:
        return candidate
    source_codes = gp_module.identity_codes(source)
    for label, surface, generic in gp_module.reverse_scope_repairs():
        pattern = r"(?<![A-Za-z0-9_])" + r"[\s_-]+".join(map(re.escape, surface.split())) + r"(?![A-Za-z0-9_])"
        if (label in result and not gp_module.identity_codes(label).intersection(source_codes)
                and re.search(pattern, str(source or ""), re.I)):
            result = result.replace(label, generic)
    return result


def computer_term_is_unambiguous(source, src_lang, tgt_lang):
    """Use Taiwan's computer term without renaming calculators or quoted UI."""
    return (
        str(src_lang).startswith("id") and str(tgt_lang).startswith("zh")
        and bool(re.search(r"\bkomputer\b", source or "", re.I))
        and not re.search(r"\bkalkulator\b|計算機|计算机|計算器|计算器", source or "", re.I)
    )


def canonicalize_computer_translation(source, candidate, src_lang, tgt_lang):
    if computer_term_is_unambiguous(source, src_lang, tgt_lang):
        return str(candidate or "").replace("計算機", "電腦").replace("计算机", "電腦")
    return candidate


# A process action is not an immutable English station/equipment label.
# Match terminology, not whole messages, and allow Indonesian inflection.
_ROUGH_POLISH_ZH_RE = re.compile(r"粗[ \t]*(?:拋|抛)(?:[ \t]*光)?")
_ROUGH_POLISH_ID_RE = re.compile(
    r"(?<![\w])(?:poles|di[- \t]*poles|memoles|pemolesan)"
    r"[ \t]+(?:secara[ \t]+)?kasar(?![\w])", re.I,
)
_ROUGH_POLISH_EN_RE = re.compile(
    r"(?<![\w])(?P<action>(?:melakukan|dilakukan|lakukan)[ \t]+)?"
    r"(?P<passive>di[- \t]*)?"
    r"(?:rough|coarse)[- \t]+polish(?:ing)?(?![\w])", re.I,
)


def canonicalize_process_translation(source, candidate, src_lang, tgt_lang):
    """Localize known English action leakage without rewriting the sentence.

    Never infer a missing process or replace another grade/process. An English
    phrase explicitly present in the source can be a quoted label: leave that
    mixed case to normal translation and immutable-literal validation.
    """
    if (str(src_lang).startswith("zh") and str(tgt_lang).startswith("id")
            and _ROUGH_POLISH_ZH_RE.search(source or "")
            and not _ROUGH_POLISH_EN_RE.search(source or "")):
        def localize(match):
            action = (match.group("action") or "").strip()
            # English nominal polishing often follows lakukan. Indonesian
            # poles is already a verb: keep its imperative/active/passive form
            # instead of producing the awkward double verb "lakukan poles".
            if action.lower() == "melakukan":
                replacement = "memoles kasar"
            elif action.lower() == "dilakukan":
                replacement = "dilakukan pemolesan kasar"
            else:
                replacement = "dipoles kasar" if match.group("passive") else "poles kasar"
            if action[:1].isupper():
                replacement = replacement[:1].upper() + replacement[1:]
            return replacement
        return _ROUGH_POLISH_EN_RE.sub(localize, candidate or "")
    return candidate


def process_translation_issues(source, candidate, src_lang, tgt_lang):
    """Use the same process contract for generation, delivery and old memory."""
    src, tgt = str(src_lang), str(tgt_lang)
    if (src.startswith("zh") and tgt.startswith("id")
            and _ROUGH_POLISH_ZH_RE.search(source or "")):
        issues = []
        if not _ROUGH_POLISH_ID_RE.search(candidate or ""):
            issues.append("factory_process:rough_polish_missing")
        if (_ROUGH_POLISH_EN_RE.search(candidate or "")
                and not _ROUGH_POLISH_EN_RE.search(source or "")):
            issues.append("factory_process:rough_polish_english_leak")
        return issues
    if (src.startswith("id") and tgt.startswith("zh")
            and _ROUGH_POLISH_ID_RE.search(source or "")
            and not _ROUGH_POLISH_ZH_RE.search(candidate or "")):
        return ["factory_process:rough_polish_missing"]
    return []


def build_process_prompt(source, src_lang, tgt_lang):
    src, tgt = str(src_lang), str(tgt_lang)
    if not ((src.startswith("zh") and tgt.startswith("id")
             and _ROUGH_POLISH_ZH_RE.search(source or ""))
            or (src.startswith("id") and tgt.startswith("zh")
                and _ROUGH_POLISH_ID_RE.search(source or ""))):
        return ""
    return (
        "<factory_process_terms>粗拋/粗抛/粗拋光 = poles kasar; "
        "allow dipoles kasar, memoles kasar, pemolesan kasar as grammar requires. "
        "For Indonesian output, use Indonesian for this action; preserve the coarse "
        "process grade, negation, repetition and material object in either direction. "
        "Do not rename equipment/station labels such as Polishing."
        "</factory_process_terms>"
    )

_ZH_NUMERAL_VALUES = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

_ORG_SUFFIXES = ("課", "股", "處", "部", "組", "班", "線", "站")
_OCR_PRIORITY_SUFFIXES = (
    "機", "站", "課", "股", "處", "部", "組", "班", "線", "盤", "爐", "表",
    "單", "材", "棒", "包", "箱", "油", "門", "秤", "車", "架", "刀", "輪",
)

# Management titles must be reversible and must not collapse into a nearby
# organizational level.  The groups below are deliberately small: a local
# correction is made only when the source identifies exactly one role in that
# group and the target contains one different, recognized role.  Ambiguous
# multi-role sentences remain with the normal semantic quality gate.
_ORGANIZATION_ROLE_SPECS: Tuple[Tuple[str, str, str, Tuple[str, ...]], ...] = (
    ("deputy_factory_head", "executive", "副廠長", ("Wakil Kepala Pabrik",)),
    ("deputy_director", "executive", "副總", ("Wakil Direktur",)),
    ("division_head", "plant_hierarchy", "處長", ("kepala divisi", "kepala departemen")),
    ("section_head", "plant_hierarchy", "課長", ("kepala seksi",)),
    ("subsection_head", "plant_hierarchy", "股長", ("kepala bagian",)),
    ("shift_head", "plant_hierarchy", "班長", ("kepala regu", "ketua shift")),
)


def _id_phrase_pattern(phrase: str) -> re.Pattern[str]:
    normalized = unicodedata.normalize("NFKC", str(phrase or "")).replace("\u3000", " ")
    tokens = [re.escape(token) for token in normalized.split() if token]
    return re.compile(
        r"(?<![A-Za-z0-9])" + r"\s+".join(tokens) + r"(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


_ORGANIZATION_ID_PATTERNS: Dict[str, Tuple[re.Pattern[str], ...]] = {
    key: tuple(_id_phrase_pattern(surface) for surface in surfaces)
    for key, _group, _zh, surfaces in _ORGANIZATION_ROLE_SPECS
}
_ORGANIZATION_ROLE_BY_KEY = {
    key: {"group": group, "zh": zh, "id": surfaces[0], "surfaces": surfaces}
    for key, group, zh, surfaces in _ORGANIZATION_ROLE_SPECS
}


@dataclass(frozen=True)
class FactoryTermMatch:
    matched_text: str
    source_term: str
    target_term: str
    mode: str
    start: int
    end: int
    priority: int = 50
    note: str = ""
    category: str = ""


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return text.replace("\u3000", " ")


def _list_value(row: Mapping[str, Any], *keys: str) -> List[str]:
    out: List[str] = []
    for key in keys:
        raw = row.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, (list, tuple, set)):
            for item in raw:
                value = _normalize_text(item).strip()
                if value and value not in out:
                    out.append(value)
    return out


def source_aliases(value: Any) -> List[str]:
    row = value if isinstance(value, Mapping) else {}
    return _list_value(row, "aliases_zh", "source_aliases", "aliases")


def target_aliases(value: Any) -> List[str]:
    row = value if isinstance(value, Mapping) else {}
    return _list_value(row, "aliases_id", "target_aliases")


def chinese_number_to_arabic(raw: str) -> Optional[str]:
    token = _normalize_text(raw).strip()
    if not token:
        return None
    if token.isdigit():
        return str(int(token)) if len(token) > 1 else token
    if token in _ZH_NUMERAL_VALUES:
        return str(_ZH_NUMERAL_VALUES[token])
    if "十" in token:
        left, right = token.split("十", 1)
        tens = 1 if left == "" else _ZH_NUMERAL_VALUES.get(left)
        ones = 0 if right == "" else _ZH_NUMERAL_VALUES.get(right)
        if tens is not None and ones is not None:
            return str(tens * 10 + ones)
    if all(ch in _ZH_NUMERAL_VALUES for ch in token):
        # OCR sometimes emits digit-style Chinese numerals, e.g. 一二 -> 12.
        return "".join(str(_ZH_NUMERAL_VALUES[ch]) for ch in token)
    return None


def _normalize_id(value: str) -> str:
    text = _normalize_text(value).casefold().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def _zh_role_keys(text: str, group: str) -> set[str]:
    source = _normalize_text(text)
    return {
        key
        for key, spec in _ORGANIZATION_ROLE_BY_KEY.items()
        if spec["group"] == group and str(spec["zh"]) in source
    }


def _id_role_keys(text: str, group: str) -> set[str]:
    source = _normalize_text(text)
    return {
        key
        for key, spec in _ORGANIZATION_ROLE_BY_KEY.items()
        if spec["group"] == group
        and any(pattern.search(source) for pattern in _ORGANIZATION_ID_PATTERNS[key])
    }


def _replace_id_role(text: str, role_key: str, replacement: str) -> str:
    result = text
    for pattern in _ORGANIZATION_ID_PATTERNS[role_key]:
        result = pattern.sub(replacement, result)
    return result


def canonicalize_organization_translation(
    source_text: str,
    target_text: str,
    src_lang: str,
    tgt_lang: str,
) -> str:
    """Repair an unambiguous management-title drift without another AI call.

    This is source-conditioned rather than a global word replacement.  For
    example, ``股長`` may locally correct ``kepala seksi`` to ``kepala bagian``;
    an Indonesian source that really says ``kepala seksi`` still correctly maps
    to ``課長``.  If a source mentions multiple roles in the same hierarchy,
    local alignment is not assumed and the normal quality gate remains in
    charge.
    """
    if not target_text:
        return target_text
    src = (src_lang or "").lower()
    tgt = (tgt_lang or "").lower()
    if not ((src.startswith("zh") and tgt.startswith("id"))
            or (src.startswith("id") and tgt.startswith("zh"))):
        return target_text

    # Preserve all non-title text byte-for-byte; normalization is used only by
    # the detectors, never as a blanket rewrite of customer names or codes.
    result = str(target_text)
    groups = {str(spec["group"]) for spec in _ORGANIZATION_ROLE_BY_KEY.values()}
    for group in groups:
        if src.startswith("zh"):
            source_roles = _zh_role_keys(source_text, group)
            if len(source_roles) != 1:
                continue
            expected = next(iter(source_roles))
            expected_id = str(_ORGANIZATION_ROLE_BY_KEY[expected]["id"])

            # Normalize accepted aliases of the correct role first.  This makes
            # the glossary literal deterministic without changing sentence
            # structure or making a second provider request.
            result = _replace_id_role(result, expected, expected_id)
            target_roles = _id_role_keys(result, group)
            if expected in target_roles:
                continue
            wrong_roles = target_roles - {expected}
            if len(wrong_roles) == 1:
                result = _replace_id_role(result, next(iter(wrong_roles)), expected_id)
        else:
            source_roles = _id_role_keys(source_text, group)
            if len(source_roles) != 1:
                continue
            expected = next(iter(source_roles))
            expected_zh = str(_ORGANIZATION_ROLE_BY_KEY[expected]["zh"])
            target_roles = _zh_role_keys(result, group)
            if expected in target_roles:
                continue
            wrong_roles = target_roles - {expected}
            if len(wrong_roles) == 1:
                wrong_zh = str(_ORGANIZATION_ROLE_BY_KEY[next(iter(wrong_roles))]["zh"])
                result = result.replace(wrong_zh, expected_zh)
    return result


def _overlaps(start: int, end: int, spans: Sequence[Tuple[int, int]]) -> bool:
    return any(start < old_end and end > old_start for old_start, old_end in spans)


def collect_organization_matches(
    text: str,
    src_lang: str,
    tgt_lang: str,
    glossary: Mapping[str, Any] | None = None,
) -> List[FactoryTermMatch]:
    """Resolve factory organization units without inventing hierarchy labels.

    ``課`` is a stable section level and can be parsed numerically as ``Seksi``.
    ``股`` is plant-specific: in this project it identifies named production
    sections such as ``Bagian Cold Drawing 1`` and must come from the glossary
    or ERP-derived aliases.  It must never be inferred as ``Regu`` or
    ``Subseksi`` solely from a numeral.  ``Regu`` is reserved for 班/工作小組.
    """
    source = _normalize_text(text)
    src = (src_lang or "").lower()
    tgt = (tgt_lang or "").lower()
    matches: List[FactoryTermMatch] = []
    occupied: List[Tuple[int, int]] = []

    if src.startswith("zh") and tgt.startswith("id"):
        # Plant-specific organization names have precedence over generic role
        # parsing.  This lets ERP/glossary data define 一股=Bagian Cold Drawing 1
        # and prevents a generic number+股 rule from overriding the real unit.
        if glossary:
            for item in get_engine(glossary).match_zh(source, limit=80):
                if item.category != "organization":
                    continue
                if _overlaps(item.start, item.end, occupied):
                    continue
                matches.append(item)
                occupied.append((item.start, item.end))

        patterns: Sequence[Tuple[re.Pattern[str], str]] = (
            (re.compile(r"第?(?P<num>[零〇一二兩三四五六七八九十0-9]{1,4})課課長"), "kepala seksi {n}"),
            (re.compile(r"第?(?P<num>[零〇一二兩三四五六七八九十0-9]{1,4})課"), "Seksi {n}"),
        )
        for pattern, target_template in patterns:
            for found in pattern.finditer(source):
                if _overlaps(found.start(), found.end(), occupied):
                    continue
                number = chinese_number_to_arabic(found.group("num"))
                if number is None:
                    continue
                matched = found.group(0)
                matches.append(FactoryTermMatch(
                    matched_text=matched,
                    source_term=matched,
                    target_term=target_template.format(n=number),
                    mode="hard",
                    start=found.start(),
                    end=found.end(),
                    priority=130,
                    category="organization_unit",
                ))
                occupied.append((found.start(), found.end()))

        standalone = (
            ("副廠長", "Wakil Kepala Pabrik", 125),
            ("副總", "Wakil Direktur", 125),
            ("處長", "kepala divisi", 120),
            ("課長", "kepala seksi", 115),
            ("股長", "kepala bagian", 115),
            ("班長", "kepala regu", 110),
        )
        for source_term, target_term, priority in standalone:
            for found in re.finditer(re.escape(source_term), source):
                if _overlaps(found.start(), found.end(), occupied):
                    continue
                matches.append(FactoryTermMatch(
                    matched_text=source_term,
                    source_term=source_term,
                    target_term=target_term,
                    mode="hard",
                    start=found.start(),
                    end=found.end(),
                    priority=priority,
                    category="organization_role",
                ))
                occupied.append((found.start(), found.end()))

    elif src.startswith("id") and tgt.startswith("zh"):
        normalized = _normalize_id(source)
        # Only the stable Seksi hierarchy is parsed generically.  Plant-specific
        # Bagian names are reversed through explicitly safe glossary aliases.
        reverse_patterns: Sequence[Tuple[re.Pattern[str], str]] = (
            (re.compile(r"\bkepala\s+seksi\s+(?P<num>\d{1,3})\b", re.I), "{n}課課長"),
            (re.compile(r"\bseksi\s+(?P<num>\d{1,3})\b", re.I), "{n}課"),
        )
        for pattern, target_template in reverse_patterns:
            for found in pattern.finditer(normalized):
                if _overlaps(found.start(), found.end(), occupied):
                    continue
                matched = found.group(0)
                matches.append(FactoryTermMatch(
                    matched_text=matched,
                    source_term=matched,
                    target_term=target_template.format(n=found.group("num")),
                    mode="hard",
                    start=found.start(),
                    end=found.end(),
                    priority=120,
                    category="organization_unit",
                ))
                occupied.append((found.start(), found.end()))

    ordered = sorted(matches, key=lambda item: (item.start, -(item.end - item.start), -item.priority))
    deduped: List[FactoryTermMatch] = []
    seen_mapping = set()
    for match in ordered:
        key = (match.matched_text.casefold(), match.target_term.casefold())
        if key in seen_mapping:
            continue
        seen_mapping.add(key)
        deduped.append(match)
    return deduped

class FactoryTerminologyEngine:
    """Immutable longest-match index for one normalized glossary snapshot.

    A character trie avoids rescanning or sorting the full glossary per request.
    Lookup cost is driven primarily by source-text length and matching prefix
    depth, so a large group of terms sharing the same first character does not
    degrade into a full bucket scan.
    """

    def __init__(self, glossary: Mapping[str, Any] | None):
        self.glossary = gp_module.normalize_glossary(glossary or {})
        self._trie: Dict[Any, Any] = {}
        self._surface_count = 0
        self._trie_nodes = 1
        self._ocr_terms: List[Tuple[int, str]] = []
        self._build()

    def _insert_surface(self, surface: str, canonical_source: str, row: Dict[str, Any]) -> None:
        node = self._trie
        for char in surface:
            child = node.get(char)
            if child is None:
                child = {}
                node[char] = child
                self._trie_nodes += 1
            node = child
        node.setdefault(_TRIE_END, []).append((surface, canonical_source, row))
        self._surface_count += 1

    def _build(self) -> None:
        ocr_candidates: List[Tuple[int, str]] = []
        for source_term, raw_row in self.glossary.items():
            row = gp_module.normalize_entry(source_term, raw_row)
            surfaces = [source_term] + source_aliases(row)
            seen_surface = set()
            for surface in surfaces:
                surface = _normalize_text(surface).strip()
                if not surface or surface in seen_surface:
                    continue
                seen_surface.add(surface)
                self._insert_surface(surface, source_term, row)
            score = int(row.get("priority", 50) or 50)
            category = str(row.get("category") or row.get("domain") or "")
            if row.get("ocr_hint") is True:
                score += 100
            if re.search(r"[A-Za-z0-9]", source_term):
                score += 40
            if source_term.endswith(_OCR_PRIORITY_SUFFIXES):
                score += 15
            if category in {"equipment", "station", "organization", "code", "erp", "process"}:
                score += 30
            if 2 <= len(source_term) <= 20:
                ocr_candidates.append((score, source_term))

        # A single surface may intentionally map to multiple entries. Resolve it
        # deterministically by glossary priority when the source is encountered.
        stack = [self._trie]
        while stack:
            node = stack.pop()
            terminals = node.get(_TRIE_END)
            if terminals:
                terminals.sort(
                    key=lambda item: int(item[2].get("priority", 50) or 50),
                    reverse=True,
                )
            stack.extend(child for key, child in node.items() if key is not _TRIE_END)
        self._ocr_terms = sorted(ocr_candidates, key=lambda item: (-item[0], len(item[1]), item[1]))

    def match_zh(self, text: str, *, limit: int = 80) -> List[FactoryTermMatch]:
        source = _normalize_text(text)
        results: List[FactoryTermMatch] = []
        seen_canonical = set()
        occupied_until = 0
        for position in range(len(source)):
            if position < occupied_until:
                continue
            node = self._trie
            cursor = position
            candidates: List[Tuple[int, str, str, Dict[str, Any]]] = []
            while cursor < len(source):
                node = node.get(source[cursor])
                if node is None:
                    break
                cursor += 1
                for surface, canonical_source, row in node.get(_TRIE_END, ()):
                    candidates.append((cursor, surface, canonical_source, row))
            if not candidates:
                continue
            candidates.sort(
                key=lambda item: (
                    item[0] - position,
                    int(item[3].get("priority", 50) or 50),
                ),
                reverse=True,
            )
            for end, surface, canonical_source, row in candidates:
                canonical_target = gp_module.canonical_target(row)
                mode = gp_module.translation_mode(row)
                if not canonical_target or mode == "disabled":
                    continue
                dedupe_key = (canonical_source, canonical_target)
                if dedupe_key in seen_canonical:
                    continue
                note = str(row.get("note_id") or row.get("note_zh") or "").strip()
                results.append(FactoryTermMatch(
                    matched_text=surface,
                    source_term=canonical_source,
                    target_term=canonical_target,
                    mode=mode,
                    start=position,
                    end=end,
                    priority=int(row.get("priority", 50) or 50),
                    note=note,
                    category=str(row.get("category") or row.get("domain") or ""),
                ))
                occupied_until = end
                seen_canonical.add(dedupe_key)
                break
            if len(results) >= max(1, int(limit or 1)):
                break
        return results

    def ocr_hint(self, *, max_items: int = 100) -> str:
        terms: List[str] = []
        seen = set()
        for _score, term in self._ocr_terms:
            if term in seen:
                continue
            seen.add(term)
            terms.append(term)
            if len(terms) >= max(1, int(max_items or 1)):
                break
        if not terms:
            return ""
        return (
            "廠內詞形辨識提示（僅協助 OCR 辨認原字，不可翻譯、不可依詞庫補寫圖片中不存在的字）:\n"
            + "、".join(terms)
        )

    def health(self) -> Dict[str, Any]:
        return {
            "api_version": FACTORY_TERMINOLOGY_API_VERSION,
            "build_id": FACTORY_TERMINOLOGY_BUILD_ID,
            "glossary_entries": len(self.glossary),
            "surface_buckets": len(self._trie),  # compatibility with prior health readers
            "trie_roots": len(self._trie),
            "trie_nodes": self._trie_nodes,
            "indexed_surfaces": self._surface_count,
            "ocr_candidates": len(self._ocr_terms),
        }


def invalidate_cache() -> None:
    with _CACHE_LOCK:
        _ENGINE_CACHE.clear()


def get_engine(glossary: Mapping[str, Any] | None) -> FactoryTerminologyEngine:
    glossary = glossary or {}
    key = (id(glossary), len(glossary))
    with _CACHE_LOCK:
        engine = _ENGINE_CACHE.get(key)
        if engine is None:
            engine = FactoryTerminologyEngine(glossary)
            _ENGINE_CACHE.clear()  # only the current glossary is useful in production
            _ENGINE_CACHE[key] = engine
        return engine


def collect_applicable_pairs(
    src_text: str,
    glossary: Mapping[str, Any] | None,
    src_lang: str,
    tgt_lang: str,
    *,
    safe_reverse_index: Optional[Mapping[str, Mapping[str, str]]] = None,
    limit: int = 100,
) -> List[Tuple[str, str]]:
    src = (src_lang or "").lower()
    tgt = (tgt_lang or "").lower()
    pairs: List[Tuple[str, str]] = []

    if src.startswith("zh") and tgt.startswith("id"):
        for match in collect_organization_matches(src_text, src, tgt, glossary):
            if match.mode == "hard":
                pairs.append((match.matched_text, match.target_term))
        for match in get_engine(glossary).match_zh(src_text, limit=limit):
            if match.mode == "hard":
                pairs.append((match.matched_text, match.target_term))

    elif src.startswith("id") and tgt.startswith("zh"):
        for match in collect_organization_matches(src_text, src, tgt, glossary):
            if match.mode == "hard":
                pairs.append((match.matched_text, match.target_term))
        normalized = _normalize_id(src_text)
        for norm, row in (safe_reverse_index or {}).items():
            if re.search(r"(?<![a-z0-9])" + re.escape(str(norm)) + r"(?![a-z0-9])", normalized):
                pairs.append((str(row.get("source_term") or norm), str(row.get("target_term") or "")))

    deduped: List[Tuple[str, str]] = []
    seen = set()
    for source_term, target_term in pairs:
        key = (source_term.casefold(), target_term.casefold())
        if source_term and target_term and key not in seen:
            seen.add(key)
            deduped.append((source_term, target_term))
            if len(deduped) >= limit:
                break
    return deduped


def build_translation_prompt(
    src_text: str,
    glossary: Mapping[str, Any] | None,
    src_lang: str,
    tgt_lang: str,
    *,
    safe_reverse_index: Optional[Mapping[str, Mapping[str, str]]] = None,
    max_items: int = 40,
) -> str:
    src = (src_lang or "").lower()
    tgt = (tgt_lang or "").lower()
    lines: List[str] = []

    process_hint = build_process_prompt(src_text, src, tgt)
    if process_hint:
        lines.append(process_hint)
    packaging_hint = build_packaging_prompt(src_text, src, tgt)
    if packaging_hint:
        lines.append(packaging_hint)

    org_matches = collect_organization_matches(src_text, src, tgt, glossary)
    if org_matches:
        lines.append("<factory_organization_terms>")
        lines.append(
            "These are factory organization levels, not person names. Never romanize Chinese unit labels. "
            "股 units are plant-defined production sections: use the exact glossary/ERP mapping and never infer Regu or Subseksi from a numeral. "
            "In this plant 一股 is Bagian Cold Drawing 1; 一課 is Seksi 1."
        )
        for match in org_matches[:max_items]:
            lines.append(f"[HARD] {match.matched_text} => {match.target_term}")
        lines.append("</factory_organization_terms>")

    if src.startswith("zh"):
        matches = get_engine(glossary).match_zh(src_text, limit=max_items)
        # Dynamic organization parsing and explicit glossary entries can describe
        # the same surface.  Keep the deterministic organization rule once and
        # remove duplicate prompt rows to reduce tokens and model distraction.
        org_keys = {
            (match.matched_text.casefold(), match.target_term.casefold())
            for match in org_matches
        }
        matches = [
            match for match in matches
            if (match.matched_text.casefold(), match.target_term.casefold()) not in org_keys
        ]
        if matches:
            lines.append("<factory_terminology>")
            lines.append(
                "Only source-grounded plant terms are listed. HARD mappings must appear with the same meaning; "
                "SOFT mappings are semantic guidance and may inflect naturally in Indonesian."
            )
            for match in matches:
                if match.mode == "hard":
                    lines.append(f"[HARD] {match.matched_text} => {match.target_term}")
                else:
                    note = re.sub(r"\s+", " ", match.note)[:120]
                    suffix = f" | context: {note}" if note else ""
                    lines.append(f"[SOFT] {match.matched_text} ~= {match.target_term}{suffix}")
            lines.append("</factory_terminology>")
    elif src.startswith("id") and tgt.startswith("zh") and safe_reverse_index:
        pairs = collect_applicable_pairs(
            src_text, glossary, src, tgt,
            safe_reverse_index=safe_reverse_index,
            limit=max_items,
        )
        glossary_pairs = [pair for pair in pairs if pair[0] and pair[1]]
        if glossary_pairs:
            lines.append("<factory_terminology>")
            lines.append("Use only the direction-safe Indonesian-to-Chinese plant terms grounded in this source.")
            for source_term, target_term in glossary_pairs:
                lines.append(f"[HARD] {source_term} => {target_term}")
            lines.append("</factory_terminology>")

    return "\n".join(lines)


def build_ocr_hint(glossary: Mapping[str, Any] | None, *, max_items: int = 100) -> str:
    return get_engine(glossary).ocr_hint(max_items=max_items)


def normalize_ocr_text(text: str) -> str:
    """Apply only lossless/safe spacing normalization before terminology lookup.

    OCR engines often insert spaces between a Chinese numeral and an organization
    suffix (``一 課``) or between ``一 股 股 長``.  Removing those internal spaces is
    deterministic and improves both language detection and terminology matching.
    """
    value = _normalize_text(text).replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(
        r"(?P<num>[零〇一二兩三四五六七八九十0-9])\s+(?P<unit>[課股處部組班線站])",
        r"\g<num>\g<unit>",
        value,
    )
    value = re.sub(r"([課股處部組班線站])\s+(長)", r"\1\2", value)
    value = re.sub(r"([一二兩三四五六七八九十0-9]股)\s*(股長)", r"\1\2", value)
    return value
