"""Local source interpretation for shop-floor chat; no model/embedding calls.

Exact identity is intentionally separate. Only explicit spelling/colloquial
variants may produce a normalized view. Similarity and inferred spellings are
retrieval evidence, never authority to copy a translation or change plant data.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from functools import lru_cache
from html import escape
import json
import re
from typing import Mapping
import unicodedata

SOURCE_UNDERSTANDING_VERSION = "2026-09-05.1"

# These keep meaning, including negation/aspect. Broader near-synonyms below
# only contribute retrieval features; they do not rewrite the source.
_VARIANTS = {
    "zh": {
        "機臺": "機台", "稱重": "秤重", "秤種": "秤重",
        "秤眾": "秤重", "包裝完畢": "包裝完成", "物料": "材料",
        "檢査": "檢查", "確任": "確認", "標纖": "標籤", "標簽": "標籤",
        "潤滑由": "潤滑油", "不銹鋼": "不鏽鋼", "不锈钢": "不鏽鋼",
    },
    "id": {
        "sdh": "sudah", "udah": "sudah", "udh": "sudah", "blm": "belum",
        "blum": "belum", "tdk": "tidak", "nggak": "tidak", "gak": "tidak",
        "jgn": "jangan", "yg": "yang", "utk": "untuk", "dgn": "dengan",
        "hrs": "harus", "sblm": "sebelum", "stlh": "setelah", "krn": "karena",
        "msn": "mesin", "mesn": "mesin", "tmbang": "timbang", "timbng": "timbang",
        "timbagn": "timbang", "timabang": "timbang", "berpungsi": "berfungsi",
        "tolng": "tolong", "perikasa": "periksa", "priksa": "periksa",
        "packng": "packing", "peking": "packing", "matrial": "material",
    },
}
# Shared concept IDs improve ranking even when the wording has little overlap.
# They deliberately do NOT claim interchangeability of all members.
_CONCEPTS = {
    "weigh": ("秤重", "過磅", "重量", "timbang", "ditimbang", "menimbang", "penimbangan", "berat"),
    "material": ("材料", "物料", "棒材", "material", "bahan", "batang"),
    "machine": ("機台", "機臺", "設備", "機器", "mesin", "peralatan"),
    "pack": ("包裝", "打包", "packing", "kemas", "dikemas", "pengemasan", "bungkus", "dibungkus"),
    "inspect": ("檢查", "確認", "查核", "periksa", "diperiksa", "memeriksa", "pemeriksaan", "cek", "dicek", "pastikan"),
    "grind": ("研磨", "磨光", "grinding", "gerinda", "digerinda", "penggerindaan"),
    "label": ("標籤", "吊牌", "label", "tag"),
    "shift": ("交班", "接班", "班別", "早班", "晚班", "shift", "serah terima"),
    "bundle": ("每把", "逐把", "捆", "bundel", "bundle", "ikatan"),
    "breakdown": ("故障", "異常", "rusak", "kerusakan", "abnormal"),
    "repair": ("維修", "修理", "perbaikan", "diperbaiki", "memperbaiki"),
    "oil": ("漏油", "潤滑油", "oli", "pelumas", "bocor", "kebocoran"),
    "guard": ("護罩", "防護罩", "護蓋", "pelindung", "pengaman", "cover"),
    "warehouse": ("倉庫", "入庫", "gudang", "penyimpanan"),
    "loading": ("上料", "memasukkan", "masuk"),
    "unloading": ("下料", "mengeluarkan", "keluar"),
}
_WORD = re.compile(r"(?<![\w])([A-Za-z]+)(?![\w])")
_IMMUTABLE = re.compile(
    r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]+|@[^\s,，。;；]+|"
    r"__[A-Z0-9_]+__|\b(?=[A-Za-z0-9_/-]*\d)[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*\b",
    re.I,
)


def _term_pattern(term):
    escaped = re.escape(term)
    if re.search(r"[a-z]", term, re.I):
        return r"(?<![\w])" + escaped + r"(?![\w])"
    return escaped


def concepts(text):
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return {name for name, terms in _CONCEPTS.items()
            if any(re.search(_term_pattern(term.casefold()), value) for term in terms)}


def _protected_ranges(text, protected_names=()):
    spans = [m.span() for m in _IMMUTABLE.finditer(text)]
    for name in protected_names or ():
        if str(name or "").strip():
            spans.extend(m.span() for m in re.finditer(re.escape(str(name)), text, re.I))
    return spans


def _overlaps(start, end, spans):
    return any(start < right and end > left for left, right in spans)


@lru_cache(maxsize=2)
def _variant_pattern(src):
    terms = sorted(_VARIANTS.get(src, {}), key=len, reverse=True)
    return re.compile("|".join(_term_pattern(term) for term in terms) or r"(?!)", re.I)


def analyze(text, src, *, protected_names=(), glossary=None):
    original = str(text or "")
    if src not in _VARIANTS:
        return {"original": original, "normalized": original, "changes": [], "suggestions": []}
    # Do not NFKC or casefold the actual data: it can include identity-bearing
    # symbols. Normalization for features/identity is handled separately.
    spans = _protected_ranges(original, protected_names)
    changes = []
    def replace(match):
        value = match.group()
        if _overlaps(*match.span(), spans):
            return value
        replacement = _VARIANTS[src].get(value.casefold() if src == "id" else value, value)
        if replacement == value:
            return value
        changes.append({"source": value, "normalized": replacement})
        return replacement
    normalized = _variant_pattern(src).sub(replace, original)
    suggestions = []
    # Novel Indonesian misspellings are hints only, and require factory
    # context plus exactly one near spelling in the domain vocabulary.
    if src == "id" and concepts(normalized):
        vocabulary = {term for terms in _CONCEPTS.values() for term in terms
                      if re.fullmatch(r"[a-z]{5,}", term)}
        for row in (glossary or {}).values():
            if not isinstance(row, Mapping) or row.get("enabled") is False:
                continue
            target = str(row.get("canonical_idn") or row.get("idn") or "")
            vocabulary.update(w.casefold() for w in _WORD.findall(target) if len(w) >= 5)
        for match in _WORD.finditer(original):
            word = match.group()
            if (len(word) < 5 or not word.islower() or word in vocabulary
                    or word in _VARIANTS["id"] or _overlaps(*match.span(), spans)):
                continue
            near = [v for v in vocabulary if _one_edit(word, v)]
            if len(near) == 1:
                suggestions.append({"source": word, "possible": near[0]})
            if len(suggestions) >= 4:
                break
    return {"original": original, "normalized": normalized,
            "changes": changes, "suggestions": suggestions,
            "operational_states": operational_states(normalized, src)}


def _one_edit(a, b):
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        mismatch = [i for i, pair in enumerate(zip(a, b)) if pair[0] != pair[1]]
        return len(mismatch) == 1 or (len(mismatch) == 2
            and mismatch[1] == mismatch[0] + 1
            and a[mismatch[0]] == b[mismatch[1]] and a[mismatch[1]] == b[mismatch[0]])
    short, long = sorted((a, b), key=len)
    i = next((i for i, (x, y) in enumerate(zip(short, long)) if x != y), len(short))
    return short[i:] == long[i + 1:]


@lru_cache(maxsize=4096)
def normalized_view(text, src):
    # Retrieval-only callers never treat this as an exact-source identity.
    return analyze(text, src)["normalized"]


def build_prompt(analysis):
    if not analysis or not (analysis.get("changes") or analysis.get("suggestions") or analysis.get("operational_states")):
        return ""
    data = {"recognized_variants": analysis.get("changes", [])[:12],
            "possible_spellings": analysis.get("suggestions", [])[:4],
            "explicit_operation_states": [s for s in analysis.get("operational_states", []) if s["mode"] != "plain"][:12]}
    return (
        "<source_understanding>Interpret common shop-floor shorthand and spelling mistakes using context. "
        "Recognized variants preserve meaning. Possible spellings are hypotheses, never automatic replacements. "
        "Do not guess an unclear person, machine/lot code, value, unit, status, negation or direction; "
        "keep that uncertain detail as written while translating the surrounding text. "
        "Do not reproduce these notes in the translation. Evidence: "
        + escape(json.dumps(data, ensure_ascii=False), quote=False)
        + "</source_understanding>"
    )


def match_features(text, src):
    normalized = normalized_view(str(text or ""), src)
    result = {"concept:" + name for name in concepts(normalized)}
    if src == "id":
        for word in _WORD.findall(normalized.casefold()):
            if len(word) >= 5:
                result.update("char:" + word[i:i + 3] for i in range(len(word) - 2))
    return result


def source_differences(query, reference):
    """Compact source-side edits for adaptive prompts, not equivalence proof."""
    tokenize = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9]+|[^\w\s]").findall
    before, after = tokenize(str(reference)), tokenize(str(query))
    edits = []
    for op, a, b, c, d in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if op != "equal":
            edits.append({"reference": " ".join(before[a:b])[:120],
                          "current": " ".join(after[c:d])[:120]})
        if len(edits) >= 6:
            break
    return edits


_ACTIONS = {
    "repair": {"id": r"\b(?:diperbaiki|memperbaiki|perbaikan|perbaiki)\b", "zh": r"修理|維修|修復|修好|待修"},
    "use": {"id": r"\b(?:gunakan|digunakan|menggunakan|pakai|dipakai|dioperasikan|operasikan)\b", "zh": r"使用|操作|運轉|開機|啟動"},
    "weigh": {"id": r"\b(?:timbang|ditimbang|menimbang|penimbangan)\b", "zh": r"秤重|過磅"},
    "pack": {"id": r"\b(?:dikemas|kemas|mengemas|pengemasan|packing|dibungkus|bungkus)\b", "zh": r"包裝|打包|裝箱"},
}
_CODE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]{1,4}\d{1,4})(?![A-Za-z0-9])")
_MODES = {
    "id": {
        "prohibited": r"\b(?:jangan|dilarang|tidak\s+boleh)\b",
        "pending": r"\bbelum\b",
        "not_done": r"\btidak\b",
        "completed": r"\b(?:sudah|telah|selesai)\b",
    },
    "zh": {
        "prohibited": r"禁止|不可以|不可|不准|不得|不要|勿",
        "pending": r"尚未|還未|還沒(?:有)?|未曾|沒有|未|沒",
        "not_done": r"不(?!可|准|要)|無",
        "completed": r"已經|已|完成",
    },
}


def operational_states(text, lang):
    """Extract only explicit local action/status relations, with equipment IDs.

    Questions are deliberately excluded: 'not repaired yet?' can legitimately
    become a positive yes/no question in the other language.
    """
    if lang not in _MODES:
        return []
    states = []
    document_codes = {m.group().upper() for m in _CODE.finditer(text)}
    for clause in re.split(r"[，,;；。!！\n]", text):
        if re.search(r"[?？]|是否|有沒有|\bapakah\b", clause, re.I):
            continue
        occurrences = sorted((m.start(), m.end(), action, m.group())
            for action, patterns in _ACTIONS.items()
            for m in re.finditer(patterns[lang], clause, re.I))
        previous_end = 0
        for start, end, action, spelling in occurrences:
            prefix = clause[previous_end:start]
            previous_end = end
            # A marker must belong to this predicate, not an earlier sentence.
            prefix = re.split(r"\b(?:tetapi|namun|tapi|dan)\b|但是|但|而且|然後|並且", prefix, flags=re.I)[-1]
            prefix = prefix[-50:] if lang == "id" else prefix[-14:]
            mode = "plain"
            hits = []
            for name, pattern in _MODES[lang].items():
                for match in re.finditer(pattern, prefix, re.I):
                    between = prefix[match.end():]
                    if lang == "id":
                        local = re.fullmatch(r"(?:\s+(?:selesai|juga|sempat|pernah|lagi|sepenuhnya))*\s*", between, re.I)
                    else:
                        local = re.fullmatch(r"(?:\s|完成|進行|重新|完全|全部|先|再|直接|隨便|任意|擅自|去)*", between)
                    if local:
                        hits.append((match.start(), name))
            if hits:
                # 'tidak boleh' includes 'tidak'; at equal positions the more
                # specific prohibition must win over unfinished-state markers.
                _, mode = max(hits, key=lambda item: (item[0], item[1] == "prohibited"))
            suffix = clause[end:end + (40 if lang == "id" else 18)]
            suffix = re.sub(r"^\s*(?:mesin\s+)?[A-Za-z]{1,4}\d{1,4}\s*", " ", suffix, flags=re.I)
            if lang == "zh":
                if spelling == "待修" or re.match(r"(?:尚未|還沒|未)(?:完成|修好)", suffix):
                    mode = "pending"
                elif mode == "plain" and re.match(r"(?:完成|完畢|好了|完了|已完成)", suffix):
                    mode = "completed"
            elif mode == "plain":
                if re.match(r"\s+(?:belum|tidak)\s+selesai\b", suffix, re.I):
                    mode = "pending"
                elif re.match(r"\s+(?:(?:sudah|telah)\s+)?selesai\b", suffix, re.I):
                    mode = "completed"
            codes = list(_CODE.finditer(clause[:start]))
            if not codes:
                # Indonesian frequently places the machine after the verb.
                codes = list(_CODE.finditer(clause[end:]))[:1]
            code = codes[-1].group().upper() if codes else (next(iter(document_codes)) if len(document_codes) == 1 else "")
            states.append({"action": action, "mode": mode, "code": code})
    return states


def validate_operational_states(analysis, target, src, tgt):
    source_states = analysis.get("operational_states") or []
    if not source_states:
        return True, []
    target_states = operational_states(target, tgt)
    issues = []
    for fact in source_states:
        if fact["mode"] == "plain":
            continue
        related = [row for row in target_states if row["action"] == fact["action"]
                   and (not fact["code"] or row["code"] == fact["code"])]
        # Other validators check missing/unknown actions. This check protects
        # polarity when the target explicitly contains that same operation.
        if not related:
            continue
        if fact["mode"] in {"pending", "prohibited"}:
            ok = any(row["mode"] == fact["mode"] for row in related)
        elif fact["mode"] == "not_done":
            ok = any(row["mode"] in {"not_done", "pending"} for row in related)
        else:
            ok = any(row["mode"] in {"plain", "completed"} for row in related)
        if not ok:
            issues.append("operation_status_changed:" + fact["action"] + ":" + fact["mode"]
                          + (":" + fact["code"] if fact["code"] else ""))
    return not issues, issues
