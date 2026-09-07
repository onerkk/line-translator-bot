"""Bind inventory-record transfers to their object, direction and prohibition.

These are source-derived relations, not translations of complete notices.
Explicit physical locations override inherited record-category context.
"""
from __future__ import annotations

import re

RECORD_ZH = r"(?:帳(?!號|号|戶|户|篷|單|单)|账(?!号|户|单)|資料|资料|紀錄|記錄|记录|建檔|建档)"
PERIOD_ZH = r"(?:非本月|不是本月|不屬於本月|不属于本月)"
_OUT = re.compile(r"(?:移出|轉出|转出|搬出)")
_PHYSICAL = re.compile(r"(?:從|从|到|至|進|进|入|出)(?:去|來|来)?(?:木箱|倉庫|仓库|儲區|储区|現場|现场|廠房|厂房|機台|机台)")
_NEGATIVE = re.compile(r"(?:不要|不可以|不可|不能|不得|別|别|禁止|勿)(?:再|先|把|將|将|讓|让)?")
_RECORD_ID = r"\b(?:data|catatan(?:nya)?|pencatatan(?:nya)?|entri|pembukuan|buku\s+(?:stok|persediaan))\b"
NONCURRENT_ID = (r"\b(?:bukan|selain|di luar)\s+(?:untuk\s+|dari\s+)?(?:(?:pengiriman|periode|produksi|pencatatan)\s+)?bulan\s+(?:ini|berjalan)\b"
    r"|\btidak\s+termasuk\s+bulan\s+ini\b|\bnon[ -]bulan\s+(?:ini|berjalan)\b")
_OUT_ID = r"\b(?:keluarkan|dikeluarkan|mengeluarkan)\b|\b(?:pindahkan|dipindahkan|memindahkan)(?:nya)?\b[^.!?;\n]{0,110}\b(?:keluar|dari)\b"
_IN_ID = r"\b(?:masukkan|dimasukkan|memasukkan|input|diinput|catat|dicatat)\b|\b(?:pindahkan|dipindahkan|memindahkan)(?:nya)?\b[^.!?;\n]{0,110}\bke\b"
_NEG_ID = r"\b(?:jangan|dilarang|tidak\s+boleh|tak\s+boleh)\b"
_CLAUSES = re.compile(r"[^.!?;,\n]+")


def build_transfers(text, intro=""):
    """Read only explicit transfers; inherit categories within this item."""
    relations = []
    record_context = bool(re.search(RECORD_ZH, intro))
    for clause in re.findall(r"[^，,。.!?！？;；\n]+[，,。.!?！？;；\n]?", text):
        compact = re.sub(r"\s+", "", clause)
        for operation in re.finditer(r"移到|移進|移进|移入|轉入|转入|存入|入帳|入账|過帳|过账|移出|轉出|转出|搬出", compact):
            before, after = compact[:operation.start()], compact[operation.end():]
            # 入帳時間 names a timestamp, not a separate instruction to post
            # records. The surrounding sentence may discuss scheduling only.
            if operation.group() in ("入帳", "入账", "過帳", "过账") and re.match(
                    r"時間|时间|時刻|时刻|時點|时点|日期|欄位|栏位|紀錄|記錄|记录|資料|资料", after):
                continue
            physical = bool(_PHYSICAL.search(compact))
            explicit = bool(re.search(RECORD_ZH, before[-24:] + operation.group() + after[:18]))
            period = bool(re.search(PERIOD_ZH, compact))
            # Two bundles may refer to two records when the same item has just
            # discussed accounting and this clause identifies that category.
            inherited = record_context and period and not physical
            if not explicit and not inherited:
                continue
            # An unrelated statement about records must not redefine moving
            # material to/from a warehouse or crate.
            if physical and not re.search(RECORD_ZH + r"(?:先|也|都|暫時|暂时|不要|別|别|不得|不能){0,4}$", before):
                continue
            if re.search(r"帳號|账号|帳戶|账户|帳篷|账单|帳單", compact):
                continue
            direction = "out" if _OUT.fullmatch(operation.group()) else "in"
            negative = bool(_NEGATIVE.search(before[-18:]))
            # Questions and double negations are not affirmative instructions.
            uncertain = bool(re.search(r"不是(?:說|说)?不要|是否|可不可以|能不能", before[-20:]) or re.search(r"嗎|吗|？|\?", clause))
            quantities = re.findall(r"([0-9零〇一二兩两三四五六七八九十]+)捆", compact)
            relations.append({
                "kind": "record_transfer", "source_evidence": clause.strip(),
                "direction": direction,
                "prohibited": negative if not uncertain else None,
                "noncurrent": period,
                "bundle_quantities": quantities,
                "previously_entered": bool(re.search(r"(?:先丟進去|先丢进去|已.{0,4}(?:入|存)|存進去|存进去|丟進去|丢进去)", compact)),
                "meaning_zh": ("移出帳務紀錄／非本月分類中的資料" if direction == "out" else "把材料的紀錄轉入庫存帳；帳不是實體區域或登入帳號")
                    + ("；禁止此動作" if negative and not uncertain else "；保留原文語氣與動作狀態"),
                "required_target_meaning_id": ("jangan " if negative and not uncertain else "")
                    + ("keluarkan/pindahkan data atau catatan dari kategori" if direction == "out" else "pindahkan/masukkan catatan material ke pencatatan stok")
                    + (" bukan untuk bulan ini" if period else ""),
            })
            record_context = True
    return relations


def _candidate_clauses(text, relation):
    """Bind a target verb to its clause, never to a donor sentence."""
    direction = _OUT_ID if relation["direction"] == "out" else _IN_ID
    for match in _CLAUSES.finditer(text):
        clause = match.group()
        if not re.search(direction, clause, re.I):
            continue
        if relation["direction"] == "in" and not re.search(_RECORD_ID + r"|\bakun\b", clause, re.I):
            continue
        if relation.get("noncurrent") and not re.search(NONCURRENT_ID, clause, re.I):
            continue
        yield match, clause


def validate_transfer(relation, target):
    candidates = list(_candidate_clauses(target, relation))
    if not candidates:
        return False
    for _match, clause in candidates:
        # Physicalizing an inventory account is wrong even if "data" appears
        # elsewhere in the same sentence.
        if re.search(r"\b(?:area|zona|lokasi|wilayah|tempat)\s+(?:akun|rekening)\b|\b(?:hapus|menghapus|dihapus)\b", clause, re.I):
            continue
        if not re.search(_RECORD_ID, clause, re.I):
            continue
        action = re.search(_OUT_ID if relation["direction"] == "out" else _IN_ID, clause, re.I)
        prefix = re.split(r"\b(?:dan|tetapi|sedangkan|namun)\b", clause[:action.start()], flags=re.I)[-1]
        prohibited = relation.get("prohibited")
        if prohibited is not None and bool(re.search(_NEG_ID, prefix, re.I)) != prohibited:
            continue
        if relation.get("previously_entered") and not re.search(
                r"\b(?:sudah|telah)\s+(?:di)?(?:masuk\w*|input\w*|simpan\w*|catat\w*)", clause, re.I):
            continue
        return True
    return False


def canonicalize_transfer_terms(relations, target):
    """Repair only identifiable record-object labels, preserving all prose.

    Do not add/remove negation, quantities, time or clauses. Ambiguous multiple
    matching actions remain for the semantic gate/provider fallback to resolve.
    """
    result = str(target or "")
    for relation in relations:
        if relation.get("kind") == "packing_status_marker":
            result = canonicalize_status_marker(relation, result)
            continue
        if relation.get("kind") != "record_transfer":
            continue
        candidates = list(_candidate_clauses(result, relation))
        if len(candidates) != 1:
            continue
        match, clause = candidates[0]
        fixed = clause
        if relation["direction"] == "in":
            # A literal "account area" maps to an inventory ledger only because
            # the source explicitly names a transfer into 帳.
            if not re.search(r"\b(?:area|zona|lokasi|wilayah|tempat)\s+akun\b", clause, re.I):
                continue
            fixed = re.sub(r"\b(?:area|zona|lokasi|wilayah|tempat)\s+akun\b", "pencatatan stok", fixed, flags=re.I)
            fixed = re.sub(r"\b(memindahkan|pindahkan)nya\b", r"\1 catatannya", fixed, flags=re.I)
            fixed = re.sub(r"\b(memindahkan|pindahkan|masukkan|memasukkan)\s+(material|barang)(?!\w)", r"\1 catatan \2", fixed, flags=re.I)
        elif not re.search(_RECORD_ID, clause, re.I):
            # Keep the candidate's existing bundle quantity/category, changing
            # only the operation's object from the bundles to their records.
            subject = re.match(r"(?P<space>\s*)(?P<count>\d+|satu|dua|tiga|empat|lima|enam|tujuh|delapan|sembilan|sepuluh)\s+bundel\b", clause, re.I)
            if not subject or not relation.get("noncurrent") or not relation.get("bundle_quantities"):
                continue
            fixed = clause[:subject.end("space")] + "Catatan untuk " + clause[subject.end("space"):subject.end("count")].lower() + clause[subject.end("count"):]
        if fixed != clause:
            result = result[:match.start()] + fixed + result[match.end():]
    return result


def build_status_markers(text):
    """A symbol-to-status explanation describes notation, not a crate's front."""
    compact = re.sub(r"\s+", "", text)
    pattern = (r"(?P<label>木箱|紙箱|纸箱)[」\"']?前面(?:有|的)?[「\"']?"
        r"(?P<marker>[*＊★])[」\"']?(?:代表|表示)(?P<state>已經|已经|已|有|尚未|未|還沒|还没|沒有|没有)(?:裝箱|装箱)")
    relations = []
    for match in re.finditer(pattern, compact):
        label_id = "peti kayu" if match['label'] == '木箱' else 'kardus'
        relations.append({
            'kind': 'packing_status_marker', 'source_evidence': match.group(),
            'marker': match['marker'].replace('＊', '*'), 'label_id': label_id,
            'packed': match['state'] in {'已經', '已经', '已', '有'},
            'meaning_zh': '說明標記與裝箱狀態的對應；不是說木箱的前面被裝進木箱。保留符號和已／未裝箱狀態，不增添搬運地點。',
            'required_target_meaning_id': f"tanda {match['marker']} di depan keterangan {label_id} menunjukkan status pengemasan material",
        })
    return relations


def _marker_clauses(relation, target):
    for match in _CLAUSES.finditer(target):
        clause = match.group()
        if relation['marker'] not in clause or relation['label_id'] not in clause.lower():
            continue
        if re.search(r"\b(?:berarti|menandakan|menunjukkan)\b", clause, re.I):
            yield match, clause


def validate_status_marker(relation, target):
    for _match, clause in _marker_clauses(relation, target):
        if re.search(r"\b(?:bagian|sisi)\s+depan\s+(?:peti kayu|kardus)\b", clause, re.I):
            continue
        if not re.search(r"\b(?:tanda|simbol|kode|keterangan|tulisan|kolom|penanda|bertanda)\b", clause, re.I):
            continue
        state = re.search(r"\b(?P<state>sudah|telah|belum|tidak)\s+(?:(?:selesai|pernah)\s+)?(?:dikemas|dimasukkan|dipak|packing)\b", clause, re.I)
        if state and (state['state'].lower() in {'sudah', 'telah'}) == relation['packed']:
            return True
    return False


def canonicalize_status_marker(relation, target):
    candidates = list(_marker_clauses(relation, target))
    if len(candidates) != 1:
        return target
    match, clause = candidates[0]
    prefix = (r"(?P<space>^\s*)(?:bagian|sisi)\s+depan\s+(?P<label>" + re.escape(relation['label_id'])
        + r")\s+yang\s+bertanda\s+" + re.escape(relation['marker'])
        + r"\s+(?P<link>berarti|menandakan|menunjukkan(?:\s+bahwa)?)(?=\s+(?:sudah|telah|belum|tidak)\b)")
    fixed = re.sub(prefix, lambda m: m['space'] + 'Tanda ' + relation['marker']
        + ' di depan keterangan ' + m['label'] + ' ' + m['link'] + ' material', clause, flags=re.I)
    return target[:match.start()] + fixed + target[match.end():]


def build_movement_permissions(text):
    """Check explicit permission/prohibition without guessing the moved object."""
    relations = []
    for clause in re.findall(r'[^，,。.!！？?;；\n]+[，,。.!！？?;；\n]?', text):
        if re.search(r'是否|可不可以|能不能|嗎|吗|[？?]|不是不可以|並非不可以|并非不可以', clause):
            continue
        for match in re.finditer(
                r'(?P<mode>不可以|不可|不准|不得|禁止|可以|允許|允许)(?:先|再|全部|都|直接)?(?P<move>移出|搬出|移入|搬入|轉出|转出|轉入|转入)', clause):
            permitted = match['mode'] in {'可以', '允許', '允许'}
            outward = match['move'].endswith('出')
            relations.append({
                'kind': 'movement_permission', 'source_evidence': match.group(),
                'permitted': permitted, 'direction': 'out' if outward else 'in',
                'meaning_zh': '保留移動方向及允許／禁止；可以不是必須，也不是禁止。此規則不決定移動的是材料或紀錄。',
                'required_target_meaning_id': ('boleh/bisa/dapat ' if permitted else 'jangan/tidak boleh ')
                    + ('dipindahkan keluar' if outward else 'dipindahkan masuk/ke'),
            })
    ambiguous = {direction for direction in {'in', 'out'}
        if len({r['permitted'] for r in relations if r['direction'] == direction}) > 1}
    # Opposite permissions for different objects need object alignment; leave
    # those multi-object clauses to the existing source audit, not a word test.
    return [r for r in relations if r['direction'] not in ambiguous]


def validate_movement_permission(relation, target):
    pattern = _OUT_ID if relation['direction'] == 'out' else _IN_ID
    states = []
    for clause in _CLAUSES.findall(target):
        for action in re.finditer(pattern, clause, re.I):
            prefix = re.split(r'\b(?:dan|tetapi|sedangkan|namun)\b', clause[:action.start()], flags=re.I)[-1]
            blocked = bool(re.search(_NEG_ID + r'|\b(?:tidak|tak)\s+(?:bisa|dapat)\b', prefix, re.I))
            allowed = bool(re.search(r'\b(?:boleh|bisa|dapat|diizinkan)\b', prefix, re.I)) and not blocked
            if blocked or allowed:
                states.append(allowed)
    # Multiple distinct operations cannot lend permission to the wrong object.
    # The existing item scopes separate numbered instructions first.
    return bool(states) and all(state == relation['permitted'] for state in states)
