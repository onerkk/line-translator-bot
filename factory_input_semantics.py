"""Data-entry method and permission facts, shared by every translation gate.

Parse linked operations, not isolated words: a scale provides weight data;
an unlocked field is a UI state, not permission to type. Unknown prose stays
on the provider path. This module never substitutes or edits a full sentence.
"""
from __future__ import annotations

import re
import unicodedata

from translation_request_cache import memoize

BUILD_ID = "2026-09-10.10-data-entry-method-and-permission"
_MANUAL_ZH = r"手打|手動(?:輸入|输入|填寫|填写|鍵入|键入)|人工(?:輸入|输入|填寫|填写)"
_MANUAL_ID = r"(?:diinput|input|menginput|memasukkan|dimasukkan|mengetik|diketik|ketik|mengisi|diisi)(?:\s+[a-z-]+){0,5}?\s+(?:manual|tangan)"
_SCALE_ZH = r"(?:磅秤|電子秤|电子秤|秤重設備|称重设备)(?:自動|自动)?(?:收集|取值|取得|讀取|读取|擷取|撷取|采集|採集)"
_METHOD_ID = r"\b(?:timbangan|alat\s+timbang)\b"
_ACQUIRE_ID = r"\b(?:data|berat|nilai|angka|hasil|pembacaan)(?:nya)?\b"
_ORIGIN_ID = re.compile(r"\b(?:dari|melalui|lewat|menggunakan)\s+(?:(?:hasil\s+)?pembacaan\s+)?"
                        r"(?:timbangan|alat\s+timbang)\b|"
                        r"\b(?:data|nilai|hasil\s+(?:pembacaan|penimbangan))\s+(?:timbangan|alat\s+timbang)\b", re.I)


def _norm(text):
    return unicodedata.normalize("NFKC", str(text or ""))


def _scale_source(sentence):
    return any(not re.search(r"\b(?:bukan|tidak)\s*$", sentence[:m.start()], re.I)
               for m in _ORIGIN_ID.finditer(sentence))


def _manual_weight_error(text, lang):
    if lang == 'zh':
        for clause in re.split(r"[，,。;；\n]", text):
            if (re.search(_MANUAL_ZH, clause)
                    and re.search(r"會|会|導致|导致|造成|產生|产生|出現|出现", clause)
                    and re.search(r"(?:異常|异常)重量|重量(?:異常|异常)", clause)
                    and not re.search(r"不會|不会|不造成|不導致|不导致|不產生|不产生", clause)):
                return True
    else:
        for sentence in re.split(r"[.;\n]", text):
            if (re.search(_MANUAL_ID, sentence, re.I)
                    and re.search(r"\b(?:jika|kalau|apabila|bila|menyebabkan|mengakibatkan|akibat)\b", sentence, re.I)
                    and re.search(r"\bberat\b.{0,25}\b(?:tidak\s+normal|abnormal|salah|keliru)\b", sentence, re.I)
                    and not re.search(r"\b(?:tidak|bukan)\s+(?:akan\s+)?(?:mencatat|tercatat|menyebabkan|mengakibatkan|menghasilkan|terjadi|muncul)\b", sentence, re.I)):
                return True
    return False


def _manual_states(text, lang):
    states = []
    pattern = _MANUAL_ZH if lang == "zh" else _MANUAL_ID
    for match in re.finditer(pattern, text, re.I):
        # Commas delimit permission clauses. A prohibition in another clause
        # (e.g. printing) must not validate permission to enter weight manually.
        before = re.split(r"[，,。;；.!?！？\n]", text[:match.start()])[-1]
        after = re.split(r"[，,。;；.!?！？\n]", text[match.end():])[0]
        if lang == "zh":
            if re.search(r"(?:不是|並非|并非).{0,3}(?:禁止|不能|不可|不准)\s*$", before):
                continue  # double-negation meaning needs sentence interpretation
            if re.search(r"嚴禁|严禁|禁止|不得|不能|不准|不可以|不可|不要", before[-16:]):
                states.append("prohibited")
            elif re.search(r"必須|必须|一律要|只能|需要", before[-12:]):
                states.append("required")
            elif re.search(r"可以|允許|允许|准許|准许", before[-12:]):
                states.append("permitted")
            elif re.match(r"(?:是)?(?:禁止|不行|不允許|不允许)", after):
                states.append("prohibited")
        else:
            if re.search(r"\b(?:tidak|bukan)\s+(?:dilarang|diwajibkan)\b", before, re.I):
                continue
            if re.search(r"\b(?:dilarang(?:\s+keras)?|jangan|tidak\s+(?:boleh|diizinkan|diperbolehkan))\b", before, re.I):
                states.append("prohibited")
            elif re.search(r"\b(?:harus|wajib|diwajibkan)\b", before, re.I):
                states.append("required")
            elif re.search(r"\b(?:boleh|diizinkan|diperbolehkan)\b", before, re.I):
                states.append("permitted")
            elif re.match(r"\s+(?:sangat\s+)?dilarang\b", after, re.I):
                states.append("prohibited")
    return states


@memoize
def build_frame(source, src_lang, tgt_lang):
    src, tgt = str(src_lang).lower().split('-')[0], str(tgt_lang).lower().split('-')[0]
    source = _norm(source)
    frame = {"active": False, "src": src, "tgt": tgt, "manual": [],
             "scale_data": False, "automatic": False, "scale_required": False,
             "field_unlocked": False, "manual_weight_error": False, "build": BUILD_ID}
    if (src, tgt) not in {("zh", "id"), ("id", "zh")}:
        return frame
    # Restrict the domain to actual data entry. 手打 may otherwise describe
    # hammering/food, and manual alone can be a document or physical operation.
    domain = (r"重量|數據|数据|資料|资料|系統|系统|欄位|栏位|磅秤|電子秤|电子秤"
              if src == "zh" else r"\b(?:berat|data|sistem|kolom|timbangan)\b")
    if not re.search(domain, source, re.I):
        return frame
    frame["manual"] = _manual_states(source, src)
    frame["manual_weight_error"] = _manual_weight_error(source, src)
    if src == "zh":
        # Negative/exceptional acquisition instructions remain prose, rather
        # than being turned into a positive "obtain from scale" requirement.
        scale = next((m for m in re.finditer(_SCALE_ZH, source)
                      if not re.search(r"禁止|不得|不能|不可|不要|不需|不是|並非|并非",
                                       re.split(r"[，,。;；\n]", source[:m.start()])[-1])), None)
        if scale:
            frame["scale_data"] = True
            frame["automatic"] = bool(re.search(r"自動|自动", scale.group()) or "prohibited" in frame["manual"])
            prefix = re.split(r"[，,。;；\n]", source[:scale.start()])[-1]
            frame["scale_required"] = bool(re.search(r"一律|必須|必须|務必|务必|都要|只能", prefix))
        frame["field_unlocked"] = bool(re.search(
            r"(?:欄位|栏位)[^，,。;；\n]{0,12}(?:沒|没|未|尚未|還沒|还没)(?:有)?[鎖锁]", source))
    else:
        scale_sentences = [sentence for sentence in re.split(r"[.;\n]", source)
                           if _scale_source(sentence)
                           and re.search(_ACQUIRE_ID, sentence, re.I)
                           ]
        frame["scale_data"] = bool(scale_sentences)
        if frame["scale_data"]:
            frame["automatic"] = bool(re.search(r"\botomatis\b", source, re.I))
            frame["scale_required"] = any(re.search(r"\b(?:harus|wajib)\b", s, re.I) for s in scale_sentences)
        frame["field_unlocked"] = bool(re.search(
            r"\b(?:kolom|field)\b.{0,35}\b(?:belum|tidak)\s+(?:di)?kunci\b", source, re.I))
    frame["active"] = bool(frame["manual"] or frame["scale_data"] or frame['manual_weight_error'])
    return frame


@memoize
def validate_translation(frame, candidate):
    if not frame or not frame.get("active"):
        return True, []
    text = _norm(candidate)
    issues = []
    states = _manual_states(text, frame['tgt'])
    for state in set(frame['manual']):
        if states.count(state) < frame['manual'].count(state):
            issues.append("factory_input_semantics:manual_entry_" + state + "_missing")
    # An extra, opposite permission is unsafe even when a correct prohibition
    # also exists elsewhere in the paragraph.
    if states and any(state not in frame['manual'] for state in states):
        issues.append("factory_input_semantics:manual_entry_permission_changed")
    target = build_frame(text, frame['tgt'], frame['src'])
    for fact in ('scale_data', 'field_unlocked', 'manual_weight_error'):
        if frame[fact] and not target[fact]:
            issues.append("factory_input_semantics:" + fact + "_missing")
    if frame['scale_required']:
        if frame['tgt'] == 'id':
            valid = any(re.search(_METHOD_ID, sentence, re.I)
                        and re.search(r"\b(?:harus|wajib|diwajibkan)\b", sentence, re.I)
                        and not re.search(r"\b(?:tidak\s+(?:harus|wajib)|jangan|dilarang)\b", sentence, re.I)
                        for sentence in re.split(r"[.;\n]", text))
        else:
            valid = target['scale_required']
        if not valid:
            issues.append("factory_input_semantics:scale_acquisition_requirement_missing")
    # 'Automatic' is prompt guidance when contrasted with prohibited typing;
    # data obtained directly from a scale is also a faithful natural rendering.
    return not issues, list(dict.fromkeys(issues))


def build_prompt(frame):
    if not frame or not frame.get('active'):
        return ''
    lines = ['<factory_input_relations>',
             'Keep data-entry method, permission, UI state and causal weight errors attached to their own actions. '
             'Use natural grammar; never infer permission from an editable field.']
    if frame['manual']:
        lines.append('Manual DATA ENTRY / 手動輸入: ' + ', '.join(frame['manual'])
                     + '. 手打 in this context means mengetik/menginput secara manual, not hitting by hand.')
    if frame['scale_data']:
        lines.append('磅秤收集 = obtaining WEIGHT DATA from the scale (data berat diperoleh langsung dari timbangan), '
                     'not collecting physical packages. Keep the data/weight subject explicit.')
    if frame['scale_required']:
        lines.append('Obtaining weight through the scale is MANDATORY (harus/wajib).')
    if frame['automatic']:
        lines.append('The scale supplies the value directly/automatically; do not describe manually transcribing its reading.')
    if frame['field_unlocked']:
        lines.append('The field is NOT YET LOCKED (kolom belum dikunci). That state does not cancel the input prohibition.')
    if frame['manual_weight_error']:
        lines.append('Manual entry causes abnormal recorded weight: jika diinput manual, sistem akan mencatat berat yang tidak normal. '
                     'Keep the condition and consequence together; do not turn this into normal weight or reverse the cause.')
    lines.append('</factory_input_relations>')
    return '\n'.join(lines)
