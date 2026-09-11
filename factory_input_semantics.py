"""Data-entry method and permission facts, shared by every translation gate.

Parse linked operations, not isolated words: a scale provides weight data;
an unlocked field is a UI state, not permission to type. Unknown prose stays
on the provider path. This module never substitutes or edits a full sentence.
"""
from __future__ import annotations

import re
import unicodedata

from translation_request_cache import memoize

BUILD_ID = "2026-09-11.1-inventory-record-and-verification-order"
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


# Field nouns and UI operations establish a record operation. Warehouse words
# alone do not: a forklift moving rods into a warehouse remains physical work.
_FIELDS = {
    'zh': r'支數|支数|數量|数量|重量|資料|资料|數據|数据',
    'id': r'\b(?:jumlah\s+batang|jumlah|berat|data|nilai)\b',
}
_ENTRY = {
    'zh': r'入庫|入库|登錄|登录|登記|登记|輸入|输入|存入|儲存|储存|按(?:下)?|點擊|点击',
    'id': r'\b(?:input|diinput|menginput|pencatatan|mencatat|dicatat|catat|memasukkan|dimasukkan|masukkan|menyimpan|disimpan|simpan|menekan|tekan|klik|diketik|mengetik|ketik)(?:nya)?\b',
}
_CHECK = {
    'zh': r'檢查|检查|確認|确认|核對|核对|查核',
    'id': r'\b(?:periksa|diperiksa|memeriksa|pemeriksaan|cek|dicek|mengecek|pastikan|memastikan|verifikasi|diverifikasi)\b',
}


def inventory_entry(text, lang):
    """Recognize linked data-entry actions, never a warehouse keyword alone."""
    text = _norm(text)
    for clause in re.split(r'[。;；.!?！？\n]', text):
        if not re.search(_FIELDS[lang], clause, re.I):
            continue
        if lang == 'zh':
            # Explicit physical handling wins over a nearby check of quantity.
            if re.search(r'吊|搬|運送|运送|堆高機|叉車|叉车', clause) and not re.search(
                    r'資料|资料|數據|数据|按|點擊|点击|輸入|输入|登錄|登录', clause):
                continue
            if re.search(r'(?:材料|棒材|物料|成品).{0,6}(?:入庫|入库)', clause) and not re.search(
                    r'資料|资料|數據|数据|按|點擊|点击|輸入|输入|登錄|登录', clause):
                continue
            linked = (re.search(r'(?:' + _FIELDS[lang] + r').{0,8}(?:入庫|入库|存入|儲存|储存|輸入|输入|登錄|登录)', clause)
                      or re.search(r'(?:輸入|输入|登錄|登录|記錄|记录|儲存|储存).{0,12}(?:' + _FIELDS[lang] + r')', clause))
            ui = re.search(r'按|點擊|点击|按鈕|按钮', clause) and re.search(r'入庫|入库|存入|儲存|储存', clause)
            if linked or ui:
                return True
        elif re.search(_ENTRY[lang], clause, re.I):
            # "memasukkan jumlah ... ke gudang" is an underspecified/literal
            # rendering, not proof that the candidate retained record semantics.
            if re.search(r'\b(?:input|diinput|menginput|catat|mencatat|dicatat|pencatatan|sistem|komputer|kolom|data|diketik|mengetik|ketik)(?:nya)?\b', clause, re.I):
                return True
    return False


def verification_order(text, lang):
    """Return an explicit check/entry order, or None for unresolved scope.

    Temporal connectives, not textual word order, define the relationship.
    Questions, negated checks and conflicting relations stay with the model.
    """
    text = _norm(text)
    if re.search(r'[?？]|是否|是不是|\bapakah\b', text, re.I):
        return None
    orders = set()
    for sentence in re.split(r'[。;；.!！？\n]', text):
        checks = list(re.finditer(_CHECK[lang], sentence, re.I))
        entries = list(re.finditer(_ENTRY[lang], sentence, re.I))
        if not checks or not entries:
            continue
        for check in checks:
            prefix = re.split(r'[,，]', sentence[:check.start()])[-1]
            neg = (r'不必|不用|不需|不要|別|别|禁止|尚未|未曾' if lang == 'zh'
                   else r'\b(?:jangan|dilarang|tidak\s+perlu|tanpa|belum)\b')
            if re.search(neg, prefix, re.I):
                # Prohibiting ENTRY until verification means verification is a
                # prerequisite; prohibiting verification itself does not.
                if lang == 'id' and re.search(r'\bjangan\b', prefix, re.I):
                    for entry in entries:
                        if entry.start() < check.start() and re.search(
                                r'\bsebelum\b[^,，]*$', sentence[entry.end():check.start()], re.I):
                            orders.add('before')
                continue
            for entry in entries:
                if entry.start() < check.start():
                    between = sentence[entry.end():check.start()]
                    before = sentence[:entry.start()]
                    if lang == 'zh':
                        # The field may follow the verb: 輸入數量後檢查.
                        temporal = re.match(r'\s*(?:(?:' + _FIELDS['zh'] + r')\s*)?(?:之)?(前|後|后|完)', between)
                        if temporal: orders.add('before' if temporal[1] == '前' else 'after')
                    else:
                        # Indonesian can put the subordinate entry clause first:
                        # Sebelum input ..., periksa ... / Setelah input ..., cek.
                        cue = re.search(r'\b(sebelum|setelah|sesudah)\b[^,，]*$', before, re.I)
                        if cue: orders.add('before' if cue[1].lower() == 'sebelum' else 'after')
                        elif re.search(r'\b(?:lalu|kemudian|baru)\b', between, re.I): orders.add('after')
                else:
                    between = sentence[check.end():entry.start()]
                    if lang == 'zh':
                        if re.search(r'再|才|然後|然后|之後|之后', between): orders.add('before')
                    else:
                        cue = re.search(r'\b(sebelum|setelah|sesudah)\b[^,，]*$', between, re.I)
                        if cue: orders.add('before' if cue[1].lower() == 'sebelum' else 'after')
                        elif re.search(r'\b(?:lalu|kemudian|baru)\b', between, re.I): orders.add('before')
    return next(iter(orders)) if len(orders) == 1 else None


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
    frame['inventory_entry'] = inventory_entry(source, src)
    frame['verification_order'] = verification_order(source, src) if frame['inventory_entry'] else None
    frame['soft_check_request'] = bool(src == 'zh' and re.search(
        r'(?:稍微|稍稍).{0,4}(?:' + _CHECK['zh'] + r')|(?:' + _CHECK['zh'] + r')一下', source))
    # Restrict the domain to actual data entry. 手打 may otherwise describe
    # hammering/food, and manual alone can be a document or physical operation.
    domain = (r"支數|支数|數量|数量|重量|數據|数据|資料|资料|系統|系统|欄位|栏位|磅秤|電子秤|电子秤"
              if src == "zh" else r"\b(?:jumlah|berat|data|sistem|kolom|timbangan)\b")
    if not re.search(domain, source, re.I):
        frame['active'] = frame['soft_check_request']
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
    frame["active"] = bool(frame["manual"] or frame["scale_data"] or frame['manual_weight_error']
                           or frame['inventory_entry'] or frame['soft_check_request'])
    return frame


@memoize
def validate_translation(frame, candidate):
    if not frame or not frame.get("active"):
        return True, []
    text = _norm(candidate)
    issues = []
    if frame.get('inventory_entry'):
        if not inventory_entry(text, frame['tgt']):
            issues.append('factory_input_semantics:inventory_entry_record_missing')
        expected_order = frame.get('verification_order')
        if expected_order and verification_order(text, frame['tgt']) != expected_order:
            issues.append('factory_input_semantics:verification_' + expected_order + '_entry_missing')
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
    if frame.get('inventory_entry'):
        lines.append('The linked quantity/weight/data operation is SYSTEM RECORD ENTRY, not moving a number into a physical warehouse. '
                     'Express 入庫/存入 as recording/saving the stated values in the inventory system '
                     '(mencatat/menyimpan jumlah atau data dalam sistem); preserve any actual button action. '
                     'Do not invent a button label or physical handling step.')
    if frame.get('verification_order'):
        lines.append('Source explicitly places VERIFICATION ' + frame['verification_order'].upper()
                     + ' RECORD ENTRY / button submission. Preserve that temporal relationship even if clauses are reordered.')
    if frame.get('soft_check_request'):
        lines.append('稍微/一下 with 檢查/確認 softens a request (tolong/mohon dicek/diperiksa); '
                     'it does not authorize careless inspection or impose an exact short duration. '
                     'For an omitted object, preserve what is known without inventing a bundle, customer or defect.')
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
