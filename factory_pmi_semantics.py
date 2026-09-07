"""Source-scoped PMI inspection predicates, shared by prompts and validation.

PMI identifies a steel-grade inspection workflow in this plant. This module
does not translate free prose or change labels, names, equipment or lot codes.
It verifies explicit action, completion state and inspection/packing order.
"""
from __future__ import annotations

import re
import unicodedata

from factory_instruction_semantics import segments

BUILD_ID = "2026-09-07.3-pmi-predicate-grammar"
_PMI = re.compile(r"(?<![A-Za-z0-9_])PMI(?![A-Za-z0-9_])", re.I)
_ZH_CHECK = re.compile(r"(?:檢驗|檢查|檢測|確認|打|驗|測)(?:材料)?(?:鋼種|材質)")
_ID_CHECK = r"(?:pemeriksaan|memeriksa|diperiksa|periksa|pengecekan|mengecek|dicek|cek|pengujian|menguji|diuji|uji|pengetesan|mengetes|dites|tes|inspeksi|diinspeksi|verifikasi|diverifikasi)"
_ID_SKIP = r"(?:terlewat|dilewatkan|dilewati|diabaikan|dilupakan|melewatkan|melewati|mengabaikan|melupakan|lewatkan|abaikan|lupakan)"
_ID_ACTION = r"(?:" + _ID_CHECK + r"|" + _ID_SKIP + r"|dilakukan|melakukan|lakukan|dilaksanakan|melaksanakan|laksanakan|dijalankan|menjalankan|jalankan)"
_ID_AUX = r"(?:belum|sudah|telah|harus|wajib|mesti|perlu|tidak|tak|boleh|jangan|dilarang|sampai|selalu|tetap|segera|juga|pernah|sempat|benar-benar|untuk|lagi|rutin|kembali|selesai)"
_ID_AFTER = re.compile(r"\s+(?:" + _ID_AUX + r"\s+){0,10}(?:" + _ID_ACTION + r"|selesai)\b", re.I)
_PACK = {"zh": re.compile(r"包裝|打包|(?<=就)包|(?<=再)包"),
         "id": re.compile(r"\b(?:dikemas|kemas|mengemas|pengemasan|packing|dibungkus)\b", re.I)}
_CLAUSE = re.compile(r"[，,;；。.!！\n]")
_CODE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]{1,4}\d{1,4})(?![A-Za-z0-9])")


def _norm(text):
    return unicodedata.normalize("NFKC", str(text or "")).replace("檢驗剛種", "檢驗鋼種").replace("驗剛種", "驗鋼種")


def _anchors(clause, lang):
    spans = [(m.start(), m.end()) for m in _ZH_CHECK.finditer(clause)
             if not re.match(r"(?:標籤|標示|標記|字樣|文字|印字)", clause[m.end():])] if lang == "zh" else []
    for match in _PMI.finditer(clause):
        start, end = match.span()
        before, after = clause[:start], clause[end:]
        if lang == "zh":
            leading = re.search(r"(?:檢驗|檢查|檢測|測試|做|打|驗)\s*$", before)
            trailing = re.match(r"\s*(?:檢驗|檢查|檢測|測試|作業|流程|(?:還|尚)?(?:沒|未)|已|做|要|必須|一定|務必)", after)
        else:
            leading = re.search(r"\b" + _ID_ACTION + r"(?:\s+[a-z]+){0,6}\s*$", before, re.I)
            trailing = _ID_AFTER.match(after)
        if leading or trailing:
            spans.append((leading.start() if leading else start, end))
    # A written PMI name and an adjacent grade-check verb are one predicate.
    merged = []
    for start, end in sorted(set(spans)):
        if merged and start <= merged[-1][1] + (6 if lang == "zh" else 1):
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _mode(clause, start, end, lang):
    before, after = clause[:start], clause[end:]
    if re.search(r"[?？]|是否|有沒有|\bapakah\b", clause, re.I):
        return "question"
    if lang == "zh":
        neg = r"(?:尚未|還未|還沒(?:有)?|沒有|未|沒)(?:完成|進行|做|打|先|再|直接)?\s*$"
        done = r"(?:已經|已|完成)(?:完成|進行|做)?\s*$"
        if re.search(neg, before) or re.match(r"\s*(?:還沒|尚未|未|沒)(?:做|打|驗|完成)", after):
            return "pending"
        if re.search(done, before) or re.match(r"\s*(?:已經|已|做完|完成|完畢|完了)", after):
            return "completed"
        if re.search(r"(?:禁止|不要|不得|不可|不准)(?:做|打|進行)?\s*$", before):
            return "prohibited"
        if re.search(r"(?:一定要?|務必|必須|必需|要|需)(?:做|打|進行)?\s*$", before) or re.match(r"\s*(?:一定要?|務必|必須|必需|要|需)", after):
            return "required"
        if re.search(r"(?:請|请|先|再)\s*$", before):
            return "required"
    else:
        # Scope modifiers to this inspection predicate. An adverb between
        # 'harus' and 'dilakukan' does not make the PMI action disappear.
        gap = r"(?:\s+(?:" + _ID_AUX + r"|" + _ID_ACTION + r"))*\s*$"
        tail = _ID_AFTER.match(after)
        tail_text = tail.group() if tail else ""
        def has(pattern):
            return bool(re.search(r"\b(?:" + pattern + ")" + gap, before, re.I)
                        or re.search(r"\b(?:" + pattern + r")\b", tail_text, re.I))
        if re.search(r"\b" + _ID_SKIP + r"\b", clause[start:end] + tail_text, re.I):
            return "required" if has(r"jangan|dilarang|tidak\s+boleh") else "omitted"
        if has(r"(?:tidak|tak)\s+(?:perlu|harus|wajib|mesti)"):
            return "optional"
        if re.search(r"\b(?:jangan|tidak\s+boleh)\s+(?:melewatkan|melewati|mengabaikan|melupakan)\s*$", before, re.I):
            return "required"
        if has(r"jangan|dilarang|tidak\s+boleh"):
            return "prohibited"
        if has(r"belum|tanpa|tidak|tak"):
            return "pending"
        if has(r"harus|wajib|mesti|perlu|pastikan|harap|mohon|tolong"):
            return "required"
        if has(r"sudah|telah") or re.match(r"\s+(?:dilakukan|selesai)\b", after, re.I):
            return "completed"
        if re.match(r"(?:lakukan|periksa|cek|uji|tes|laksanakan|jalankan)\b", clause[start:end], re.I):
            return "required"
    return "plain"


def _order(text, lang):
    """Return only explicit sequence, not the order words happen to appear."""
    pmi = [span for c in [text] for span in _anchors(c, lang)]
    packs = list(_PACK[lang].finditer(text))
    if not pmi or not packs:
        return ""
    start, end = pmi[0]
    pack = min(packs, key=lambda m: abs(m.start() - start))
    if lang == "zh":
        if pack.start() > end:
            between = text[end:pack.start()]
            if re.search(r"(?:後|完|完成).{0,8}(?:才|再)|(?:，|,)?\s*再", between) or ("先" in text[:start] and re.search(r"再|才", between)):
                return "inspect_before_pack"
        else:
            between = text[pack.end():start]
            if re.search(r"前|之前|以前", between):
                return "inspect_before_pack"
            if re.search(r"後|之後|以後|再|才", between):
                return "pack_before_inspect"
    else:
        if pack.start() > end:
            between = text[end:pack.start()]
            if re.search(r"\bsebelum\b", between, re.I) or re.search(r"\b(?:dulu|dahulu)\b.*\b(?:lalu|kemudian|baru)\b", between, re.I):
                return "inspect_before_pack"
            if re.search(r"\bsetelah\b|\bsesudah\b", between, re.I):
                return "pack_before_inspect"
        else:
            before = text[:pack.start()]
            between = text[pack.end():start]
            if re.search(r"\bsebelum\s*$", before, re.I):
                return "inspect_before_pack"
            if re.search(r"\b(?:setelah|sesudah)\s*$", before, re.I) or re.search(r"\b(?:lalu|kemudian|baru)\b", between, re.I):
                return "pack_before_inspect"
    return ""


def build_facts(source, lang):
    if lang not in {"zh", "id"}:
        return []
    facts = []
    for item, text in segments(_norm(source)):
        for clause in _CLAUSE.split(text):
            for start, end in _anchors(clause, lang):
                facts.append({"sense": "pmi_inspection", "item": item,
                    "evidence": clause.strip(), "mode": _mode(clause, start, end, lang),
                    "codes": sorted({m.group().upper() for m in _CODE.finditer(clause)}),
                    "meaning": "PMI 是檢驗、確認鋼種的作業流程，不是列印或標示鋼種。保留是否已檢驗、未檢驗便包裝及先後順序。"})
        order = _order(text, lang)
        if order:
            facts.append({"sense": "pmi_sequence", "item": item, "order": order,
                "evidence": text.strip(), "meaning": "檢驗與包裝先後順序必須維持原文：" + order})
    return facts


def validate(facts, target, lang):
    facts = [f for f in facts if f["sense"] in {"pmi_inspection", "pmi_sequence"}]
    if not facts:
        return []
    target_facts = build_facts(target, lang)
    target_scopes = dict(segments(_norm(target)))
    issues = []
    for fact in facts:
        related = [f for f in target_facts if f["sense"] == fact["sense"]
                   and (fact["item"] is None or f["item"] == fact["item"])
                   and set(fact.get("codes", ())).issubset(f.get("codes", ()))]
        scope = target_scopes.get(fact["item"], "") if fact["item"] is not None else str(target)
        if fact["sense"] == "pmi_inspection":
            ok = bool(related and _PMI.search(scope))
            if fact["mode"] in {"pending", "completed", "prohibited", "required"}:
                matching_mode = any(f["mode"] == fact["mode"] for f in related)
                if fact["mode"] == "required" and lang == "zh":
                    # Chinese can express an imperative directly ('做PMI檢查')
                    # without adding 要. Do not treat a past/negative clause as
                    # an imperative merely because it contains a check verb.
                    matching_mode = matching_mode or any(f["mode"] == "plain" and re.match(
                        r"(?:檢驗|檢查|檢測|做|驗|測|打)\s*(?:PMI|鋼種|材質)", f["evidence"], re.I
                    ) for f in related)
                ok = ok and matching_mode
            issue = "inspection_" + fact["mode"]
        else:
            ok = any(f["order"] == fact["order"] for f in related)
            issue = fact["order"]
        if not ok:
            issues.append("factory_term:pmi:" + issue + (":item_" + str(fact["item"]) if fact["item"] is not None else ""))
    return list(dict.fromkeys(issues))
