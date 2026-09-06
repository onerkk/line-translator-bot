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

SOURCE_UNDERSTANDING_VERSION = "2026-09-06.1-operational-term-evidence"

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
        "menjalan kan": "menjalankan", "menjalankn": "menjalankan",
        "mengguna kan": "menggunakan", "menggunakn": "menggunakan",
        "di bersihkan": "dibersihkan", "di perbaiki": "diperbaiki",
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


@lru_cache(maxsize=512)
def _term_pattern(term):
    escaped = re.escape(term)
    if re.search(r"[a-z]", term, re.I):
        return r"(?<![\w])" + escaped + r"(?![\w])"
    return escaped


_CONCEPT_PATTERNS = {
    name: re.compile("|".join(_term_pattern(term.casefold()) for term in terms))
    for name, terms in _CONCEPTS.items()
}


@lru_cache(maxsize=8192)
def _concepts_cached(value):
    return frozenset(name for name, pattern in _CONCEPT_PATTERNS.items() if pattern.search(value))


def concepts(text):
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return set(_concepts_cached(value))


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


def normalize_known_variants(text, src, *, protected_names=()):
    """Cheap, source-preserving spelling normalization shared by all callers."""
    original = str(text or "")
    if src not in _VARIANTS:
        return original, []
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
    return normalized, changes


def analyze(text, src, *, protected_names=(), glossary=None):
    original = str(text or "")
    normalized, changes = normalize_known_variants(original, src, protected_names=protected_names)
    spans = _protected_ranges(original, protected_names)
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
            "operational_states": operational_states(normalized, src),
            "factory_terms": factory_term_facts(normalized, src, protected_names=protected_names)}


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
    if not analysis or not (analysis.get("changes") or analysis.get("suggestions") or analysis.get("operational_states") or analysis.get("factory_terms")):
        return ""
    data = {"recognized_variants": analysis.get("changes", [])[:12],
            "possible_spellings": analysis.get("suggestions", [])[:4],
            "explicit_operation_states": [s for s in analysis.get("operational_states", []) if s["mode"] != "plain"][:12],
            "plant_term_meanings": analysis.get("factory_terms", [])}
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


# Meanings come from the existing ERP glossary, ID_ZH_HIGH_RISK_TERMS and
# factory role casebook. These are lexical/role facts, never whole-sentence
# replacements. Unrelated laser applications and school roles are excluded.
_OL = r"(?<![A-Za-z0-9_./])(?:meng[- ]?)?OL(?:[- ]?kan)?(?![A-Za-z0-9_./])"
_OL_RE = re.compile(_OL, re.I)
_FACTORY_ID = re.compile(r"\b(?:ID|data|work\s*order|mesin|produksi|stasiun|I\d{1,2}|E\d{1,2}|BF\d+)\b", re.I)
_ONLINE_CONTEXT = re.compile(r"\b(?:game|instagram|facebook|whatsapp|tiktok|office\s+lady)\b|遊戲|臉書|辦公室女郎", re.I)
_LASER_OTHER = re.compile(r"\b(?:pointer|penunjuk|mainan|medis|mata|operasi\s+mata|pemotongan\s+laser|laser\s+(?:cutting|potong)|pengelasan|printer)\b|雷射(?:筆|切割|焊接|手術)|激光(?:筆|切割|焊接)", re.I)
_GAUGE_CONTEXT = re.compile(r"\b(?:kotor|bersih|dibersihkan|baca|terbaca|ukuran|diameter|mikro|produksi|menjalan\s*kan|mesin|I\d{1,2})\b|髒|髒污|清潔|擦拭|讀數|量測|測徑|生產|研磨", re.I)
_EMPLOYEE_NO = re.compile(r"\b(?:no\.?|nomor|nomer|nomor\s+induk)\s*(?:pekerja|pegawai|karyawan)\b", re.I)
_SHIFT_ROLE = re.compile(r"\b(?:ketu(?:a)?\s+kelas|ketua\s+(?:shift|regu)|kepala\s+(?:regu|shift))\b", re.I)
_SCHOOL = re.compile(r"\b(?:sekolah|siswa|murid|guru|kuliah|universitas)\b", re.I)
_CLAUSE_BREAK = re.compile(r"[。！？!?;；\n]|(?<!\d)[,，]|[,，](?!\d)")


def _term_clauses(text):
    # Split coordinated statements only when an explicit new data/equipment
    # subject follows. This keeps each station's polarity attached to its OL.
    parts = re.split(r"\b(?:dan|tetapi|tapi|sedangkan)\s+(?=(?:data|ID|work\s+order|[A-Za-z]{1,3}\d+)\b)|"
                     r"(?:但|而|且)(?=(?:資料|此\s*ID|該\s*ID|[A-Za-z]{1,3}\d+))", str(text), flags=re.I)
    return [clause for part in parts for clause in _CLAUSE_BREAK.split(part) if clause.strip()]


def _ol_mode(clause, lang):
    patterns = {
        "id": (
            ("request", r"\bjangan\s+lupa\b"),
            ("prohibited", r"\b(?:jangan|dilarang|tidak\s+boleh)\b"),
            ("unable", r"\b(?:tidak|belum|nggak|gak|tdk|blm)\s+(?:bisa|dapat|berhasil)\b|\bgagal\b"),
            ("pending", r"\b(?:belum|blm)\b"),
            ("negative", r"\b(?:tidak|tdk|gak|nggak)\b"),
            ("completed", r"\b(?:sudah|sdh|telah|berhasil|selesai)\b"),
            ("request", r"\b(?:tolong|mohon|silakan|jangan\s+lupa)\b"),
        ),
        "zh": (
            ("prohibited", r"禁止|不可以|不可(?!以外)|不得|不准|不要|勿"),
            ("unable", r"無法|不能|沒辦法|不成功|失敗|未能|還不能"),
            ("pending", r"尚未|還沒|還未|未發料|沒發料|沒有發料|未轉|未改"),
            ("negative", r"不發料|不轉|不改"),
            ("completed", r"已經|已|完成|成功|發料了|發料完成"),
            ("request", r"請|麻煩|幫忙"),
        ),
    }
    anchor = (_OL_RE.search(clause) if lang == "id" else
              re.search(r"發料|(?:轉|改|設).{0,12}OL|OL", clause, re.I))
    prefix = clause[:anchor.start()] if anchor else clause
    matches = [(m.end(), m.end() - m.start(), -rank, mode)
               for rank, (mode, pattern) in enumerate(patterns.get(lang, ()))
               for m in re.finditer(pattern, prefix, re.I)]
    if matches:
        # Attach the closest aspect/modal to this operation, not to an earlier
        # repair or a later production failure in the same sentence.
        return max(matches)[-1]
    if anchor:
        suffix = clause[anchor.end():anchor.end() + 16]
        for mode, pattern in patterns.get(lang, ()):
            if re.match(r"\s*(?:狀態)?\s*(?:" + pattern + ")", suffix, re.I):
                return mode
    return "plain"


def factory_term_facts(text, lang, *, protected_names=()):
    """Resolve contextual terms with source evidence; retain separate clauses."""
    if lang not in {"zh", "id"}:
        return []
    source = str(text or "")
    # Names, URLs and quoted opaque tokens are not operational prose. Numeric
    # equipment codes remain visible as evidence and are never rewritten here.
    for name in sorted((str(n) for n in protected_names if str(n)), key=len, reverse=True):
        source = re.sub(re.escape(name), " " * len(name), source, flags=re.I)
    source = re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]+|__[A-Z0-9_]+__", " ", source, flags=re.I)
    facts = []
    for clause in _term_clauses(source):
        ol = _OL_RE.search(clause)
        is_action = bool(ol and (
            _FACTORY_ID.search(clause)
            or re.search(r"\b(?:di\s*[- ]?|meng[- ]?)OL(?:[- ]?kan)?\b", clause, re.I)
            or re.search(r"發料|資料|資料狀態|轉為|改為|改成", clause)
        ))
        if is_action and not (_ONLINE_CONTEXT.search(clause) and not re.search(r"\bERP\b|生產資料", clause, re.I)):
            facts.append({"sense": "erp_ol", "evidence": clause.strip(), "mode": _ol_mode(clause, lang),
                          "codes": _CODE.findall(clause),
                          "meaning": "ERP 生產資料發料／轉為 OL（生產中）狀態；不是上網、線上操作或搬運實體材料。保留原文能否、完成狀態及站別。"})
    if lang == "id":
        if re.search(r"\blaser\b", source, re.I) and _GAUGE_CONTEXT.search(source) and not _LASER_OTHER.search(source):
            facts.append({"sense": "laser_gauge", "evidence": "laser", "meaning": "雷射測徑儀；kotor=髒污，不能擅自改為故障或捏造量測值。"})
        if _EMPLOYEE_NO.search(source):
            facts.append({"sense": "employee_number", "evidence": _EMPLOYEE_NO.search(source).group(), "meaning": "工號／員工編號，不是員工人數；ID 料號和員工工號是不同欄位。"})
            if _SHIFT_ROLE.search(source) and not _SCHOOL.search(source):
                facts.append({"sense": "shift_leader", "evidence": _SHIFT_ROLE.search(source).group(), "meaning": "這裡是工廠班長，不是學校班級或課長／股長。保留工號使用者及工號所有者。"})
    return facts


def validate_factory_terms(analysis, target, src, tgt):
    if (src, tgt) not in {("id", "zh"), ("zh", "id")}:
        return True, []
    text = str(target or "")
    facts = analysis.get("factory_terms") or []
    issues = []
    for fact in facts:
        sense = fact["sense"]
        if sense == "erp_ol":
            clauses = [c for c in _term_clauses(text) if all(
                re.search(r"(?<![A-Za-z0-9])" + re.escape(code) + r"(?![A-Za-z0-9])", c, re.I)
                for code in fact["codes"]
            )]
            clauses = [c for c in clauses if re.search(r"發料|资料.*OL|資料.*OL|(?:轉|改|設|變).{0,12}OL|OL.{0,8}狀態|OL", c, re.I)] if tgt == "zh" else [c for c in clauses if _OL_RE.search(c)]
            if not clauses:
                issues.append("factory_term:erp_ol:meaning_missing")
                continue
            if tgt == "zh" and all(re.search(r"線上操作|線上作業|上網|網路操作", c)
                and not re.search(r"發料|(?:轉|改|設).{0,12}OL|生產.{0,8}狀態", c, re.I) for c in clauses):
                issues.append("factory_term:erp_ol:internet_meaning")
            mode = fact.get("mode", "plain")
            allowed = {"negative": {"negative", "pending"}, "completed": {"plain", "completed"}}.get(mode, {mode})
            if mode not in {"plain", "request"} and not any(_ol_mode(c, tgt) in allowed for c in clauses):
                issues.append("factory_term:erp_ol:state_changed:" + mode)
        elif sense == "laser_gauge" and tgt == "zh":
            if not re.search(r"(?:雷射|激光|鐳射).{0,4}(?:測徑|量測|測量|量徑|外徑|尺寸)|測徑儀", text):
                issues.append("factory_term:laser_gauge:instrument_missing")
            original = analysis.get("normalized") or analysis.get("original") or ""
            if re.search(r"\bkotor\b", original, re.I) and not re.search(r"髒|髒污|污垢|不乾淨|汙", text):
                issues.append("factory_term:laser_gauge:dirty_condition_missing")
        elif sense == "employee_number" and tgt == "zh":
            if not re.search(r"工號|員工編號|員工號碼|員工代號", text):
                issues.append("factory_term:employee_number:identity_missing")
        elif sense == "shift_leader" and tgt == "zh" and "班長" not in text:
            issues.append("factory_term:shift_leader:role_missing")
    return not issues, list(dict.fromkeys(issues))


_ERP_STATUS_CLAUSE = re.compile(
    r"\s*(?P<subject>(?:ID|data|work\s+order)(?:\s+(?=[A-Za-z0-9/-]*\d)[A-Za-z0-9][A-Za-z0-9/-]*)?(?:\s+(?:ini|itu))?)\s+"
    r"(?P<state>tidak\s+bisa|belum\s+bisa|tidak\s+dapat|belum|sudah|telah|jangan|tidak\s+boleh|bisa|gagal)\s+"
    r"(?:di[ -]*)?OL(?:-?kan)?(?:\s+di\s+(?:(?:mesin|stasiun)\s+)?(?P<station>[A-Z]{1,3}\d{1,3}))?\s*[.!。！]?\s*",
    re.I,
)


def translate_complete_erp_status(text, src, tgt):
    """Render only a fully consumed ERP status clause; free prose uses the LLM."""
    if (src, tgt) != ("id", "zh"):
        return None
    if "\n" in str(text).strip() or "\r" in str(text).strip():
        return None
    match = _ERP_STATUS_CLAUSE.fullmatch(str(text or ""))
    if not match:
        return None
    subject = match["subject"]
    code = re.search(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9/-]*\d)[A-Za-z0-9][A-Za-z0-9/-]*", subject)
    if re.match(r"ID\b", subject, re.I):
        label = "ID " + code.group() if code else ("該 ID" if re.search(r"\bitu\b", subject, re.I) else "此 ID")
        label += " 的資料"
    elif re.match(r"work", subject, re.I):
        label = "工單" + (" " + code.group() if code else "") + "的資料"
    else:
        label = "資料" + (" " + code.group() if code else "")
    state = {
        "tidak bisa": "無法", "tidak dapat": "無法", "belum bisa": "還無法",
        "belum": "尚未", "sudah": "已", "telah": "已", "jangan": "請勿",
        "tidak boleh": "不可", "bisa": "可以", "gagal": "無法",
    }[re.sub(r"\s+", " ", match["state"].casefold())]
    location = "在 " + match["station"] + " " if match["station"] else ""
    # Keep the original OL spelling/case as an immutable control value.
    ol = re.search(r"OL", match.group(), re.I).group()
    return f"{label}{state}{location}發料（{ol}）。"
