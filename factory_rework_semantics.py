"""Source-scoped material referents and rework actions, without sentence lookup.

Only explicit shop-floor classifiers and painting/rework evidence activate this
frame. Unknown tools, procedures, colours and chemicals are never inferred.
The one local repair fixes a malformed noun phrase; it does not invent a remedy
or replace a model's whole sentence. Other defects use the existing review gate.
"""
from __future__ import annotations

import re
import unicodedata

BUILD_ID = "2026-09-08.1-material-rework-relations"
_PAINT_ZH = r"噴漆|喷漆|噴錯漆|喷错漆|噴錯|喷错|塗裝|涂装"
_WRONG_ZH = r"錯誤|错误|錯了|错了|有誤|有误|不對|不对|噴錯|喷错|塗錯|涂错"
_WASH_ZH = re.compile(r"重洗|重新(?:清)?洗|再(?:清)?洗(?:一次|一遍)?")
_REPAINT_ZH = re.compile(r"重噴|重喷|重新(?:噴漆|喷漆|塗裝|涂装)|再(?:噴|喷)(?:漆|一次)?")
_OBJECT_ZH = re.compile(r"(?P<point>[這这那])(?:一)?(?P<unit>[把捆支根批件])")
_OBJECTS = {"把": ("bundel", "bundel|ikatan"), "捆": ("bundel", "bundel|ikatan"),
            "支": ("batang", "batang"), "根": ("batang", "batang"),
            "批": ("batch", "batch|lot"), "件": ("barang", "barang|benda|komponen")}
_TOOLS_ZH = re.compile(r"(?:刀|傘|伞|刷|椅|鑰匙|钥匙|鉗|钳|扳手|噴槍|喷枪|噴漆槍|喷漆枪)")
_PAINT_ID = re.compile(
    r"\b(?:pengecatan|cat|mengecat|dicat)(?:nya)?\s+(?:dengan\s+)?semprot(?:an|nya)?\b"
    r"|\b(?:disemprot|menyemprot)(?:kan)?\s+(?:dengan\s+)?cat(?:nya)?\b"
    r"|\bpenyemprotan\s+cat(?:nya)?\b", re.I)
_WASH_ID = re.compile(r"\b(?:cuci|dicuci|mencuci|pencucian)(?:nya)?\b(?:\s+(?:sekali|material|bundel|batang|barang|ini|itu|tersebut)){0,4}\s+(?:ulang|lagi|kembali)\b", re.I)
_REPAINT_ID = re.compile(r"\b(?:dicat|mengecat|pengecatan|disemprot|menyemprot)(?:nya)?\b(?:\s+(?:dengan|cat|semprot|semprotan|material|bundel|ini|itu|tersebut|sekali)){0,4}\s+(?:ulang|lagi|kembali)\b", re.I)
_BAD_NOMINAL = re.compile(r"\byang\s+salah\s+pengecatan\s+semprot\b", re.I)
_TARGET_SPLIT = re.compile(
    r"[.!?;\n]+|[,，]\s*(?:sedangkan\s+|sementara\s+)?(?=(?:bundel|ikatan|batang|batch|lot|barang|benda|komponen)\b)", re.I)


def _norm(value):
    return unicodedata.normalize("NFKC", str(value or ""))


def painting_present(target):
    """Recognize the painting concept across noun, active and passive forms."""
    return bool(_PAINT_ID.search(_norm(target)))


def _source_mode(text, action):
    left = re.split(r"[,，;；]", text[max(0, action.start()-14):action.start()])[-1]
    right = text[action.end():action.end()+6]
    left = re.sub(r"不要忘記|不要忘记|別忘(?:了)?|别忘(?:了)?", "記得", left)
    if re.search(r"(?:不要|別|别|禁止|不必|不用|無需|无需)(?:再|先|去)?$", left):
        return "prohibited"
    if re.search(r"(?:還沒|还没|尚未|未曾)(?:有)?$", left):
        return "pending"
    if re.search(r"(?:已經|已经|已)(?:被)?$", left) or re.match(r"(?:好|完|過|过)(?:了|成)?", right):
        return "completed"
    return "instruction"


def build_relations(source):
    text = _norm(source)
    # Mentions and quoted literal values are not operational evidence.
    text = re.sub(r"__[A-Z0-9_]+__|@[^\s，,。;；]+|[「『\"][^」』\"]*[」』\"]", " ", text)
    parts = re.split(r"[。.!?！？;；\n]+|[,，](?=[這这那](?:一)?[把捆支根批件])", text)
    relations = []
    for part in parts:
        compact = re.sub(r"\s+", "", part)
        if not re.search(_PAINT_ZH, compact):
            continue
        # Alternative questions are not prohibitions merely because they
        # contain 不要. Leave that unresolved modality to source translation.
        if re.search(r"要不要|能不能|可不可以", compact):
            continue
        objects = list(_OBJECT_ZH.finditer(compact))
        # Multiple implicit referents need the model's full context. Do not
        # associate a shared keyword with the wrong material locally.
        if len(objects) != 1:
            continue
        obj = objects[0]
        suffix = compact[obj.end():]
        if _TOOLS_ZH.match(suffix):
            continue
        # 把 before an explicitly named non-material noun is not a bar bundle.
        if obj['unit'] == '把' and not re.match(
                r"材料|棒材|料|的?(?:" + _PAINT_ZH + r")|先|請|请|記得|记得|不要|別|别|已|還|还|重", suffix):
            continue
        wrong = re.search(_WRONG_ZH, compact)
        if not wrong:
            continue
        if re.search(r"不是|並非|并非|沒有|没有|沒|没", compact[max(0,wrong.start()-8):wrong.start()]):
            continue
        actions = [("wash", m) for m in _WASH_ZH.finditer(compact)] + [("repaint", m) for m in _REPAINT_ZH.finditer(compact)]
        if not actions:
            continue
        noun, accepted = _OBJECTS[obj['unit']]
        pointer = "itu" if obj['point'] == "那" else "ini"
        modes = {kind: _source_mode(compact, match) for kind, match in actions}
        relations.append({
            "kind": "material_rework", "source_evidence": part.strip(),
            "noun": noun, "accepted_nouns": accepted, "pointer": pointer,
            "actions": modes,
            "conditional": bool(re.search(r"如果|若|假如", compact)),
            "question": bool(re.search(r"[?？]|是否|要不要|嗎|吗", source)),
            "colour_explicit": bool(re.search(r"顏色|颜色|色", compact)),
            "chemical_explicit": bool(re.search(r"退漆|脫漆|脱漆|酸|溶劑|溶剂|稀釋|稀释", compact)),
            "meaning_zh": f"{obj.group()}指{noun} {pointer}；噴漆錯誤修飾材料。清洗與重新噴漆是不同動作，僅保留原文明示的處理與狀態；不得自行補顏色、退漆藥劑或再噴漆。",
            "required_target_meaning_id": f"{noun} {pointer} yang salah dicat semprot; " + "; ".join(
                f"{'cuci ulang' if kind == 'wash' else 'cat semprot ulang'} ({mode})" for kind, mode in modes.items()),
        })
    return relations


def _referent_scopes(relation, target):
    nouns = relation['accepted_nouns']
    pointer = r"(?:itu|tersebut)" if relation['pointer'] == 'itu' else 'ini'
    # A relative clause may intervene between the noun and its demonstrative.
    referent = re.compile(r"\b(?:" + nouns + r")\b(?:(?!\b(?:bundel|ikatan|batang|batch|lot|barang|benda|komponen)\b)[^.!?;\n]){0,110}?\b" + pointer + r"\b", re.I)
    return [part for part in _TARGET_SPLIT.split(target) if referent.search(part)]


def _target_modes(pattern, text):
    modes = []
    for action in pattern.finditer(text):
        left = re.split(r"[,;.!?]|\b(?:dan|tetapi|sedangkan|ini|itu|tersebut)\b", text[:action.start()])[-1][-65:]
        left = re.sub(r"\bjangan\s+(?:sampai\s+)?lupa\b", "ingat", left, flags=re.I)
        cues = list(re.finditer(r"\b(?:jangan|dilarang|tidak\s+(?:boleh|perlu|usah)|belum|sudah|telah|selesai|harus|wajib|perlu|ingat|tolong|harap|akan|mesti)\b", left, re.I))
        cue = cues[-1].group().lower() if cues else ''
        if re.fullmatch(r"jangan|dilarang|tidak\s+(?:boleh|perlu|usah)", cue):
            modes.append('prohibited')
        elif cue == 'belum':
            modes.append('pending')
        elif cue in {'sudah', 'telah', 'selesai'}:
            modes.append('completed')
        else:
            modes.append('instruction')
    return modes


def validate_relation(relation, target):
    for part in _referent_scopes(relation, _norm(target).casefold()):
        if not painting_present(part) or _BAD_NOMINAL.search(part):
            continue
        if not re.search(r"\bsalah\b|\bkeliru\b|\bkesalahan\b|\btidak\s+(?:tepat|sesuai)\b", part):
            continue
        if re.search(r"\b(?:tidak|bukan)\s+(?:salah|keliru)\b", part):
            continue
        if not relation['colour_explicit'] and re.search(r"\bwarna\b", part):
            continue
        if not relation['chemical_explicit'] and re.search(r"\b(?:thinner|pelarut|asam|pengelupas)\b", part):
            continue
        if relation['conditional'] and not re.search(r"\b(?:jika|kalau|bila|apabila)\b", part):
            continue
        if relation['question'] and not re.search(r"[?？]|\bapakah\b", target, re.I):
            continue
        observed = {kind: _target_modes(pattern, part) for kind, pattern in [('wash', _WASH_ID), ('repaint', _REPAINT_ID)]}
        if any(observed[kind] != [mode] for kind, mode in relation['actions'].items()):
            continue
        if any(modes for kind, modes in observed.items() if kind not in relation['actions']):
            continue
        return True
    return False


def canonicalize_noun_phrase(source, target):
    """Repair only one explicit referent and one known malformed noun phrase.

    No omitted action, reversed state, extra colour/chemical, number or name is
    repaired. The existing independent validators must still accept the result.
    """
    relations = build_relations(source)
    if len(relations) != 1 or len(_BAD_NOMINAL.findall(target)) != 1:
        return target
    relation = relations[0]
    if relation['conditional'] or relation['question']:
        return target
    # Match only an existing matching noun or a truly headless initial relative
    # phrase (possibly after protected mentions). Never replace a wrong object.
    noun = relation['noun']
    pattern = re.compile(r"(?P<head>\b(?:" + relation['accepted_nouns'] + r")\s+)?(?P<phrase>yang\s+salah\s+pengecatan\s+semprot)(?P<point>\s+(?:ini|itu|tersebut))?\b", re.I)
    match = pattern.search(target)
    if not match:
        return target
    prefix = target[:match.start()]
    if not match['head'] and re.sub(r"__[A-Z0-9_]+__|@[^\s,，]+|\s+", "", prefix):
        return target
    if match['point'] and match['point'].strip().lower() != relation['pointer']:
        return target
    replacement = f"{match['head'].strip() if match['head'] else noun} yang salah dicat semprot {relation['pointer']}"
    if (match['head'] or match['phrase'])[0].isupper():
        replacement = replacement[0].upper() + replacement[1:]
    fixed = target[:match.start()] + replacement + target[match.end():]
    return fixed if validate_relation(relation, fixed) else target
