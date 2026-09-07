"""Source-scoped instruction relations, shared by prompts and every delivery gate.

This is a claim parser, not a sentence translation table. It never synthesizes a
whole notice or edits a model's prose. Numbered items are separate scopes: a
correct word elsewhere cannot excuse a reversed action in the current item.
"""
from __future__ import annotations

import re
import unicodedata

BUILD_ID = "2026-09-07.2-notice-equivalent-predicates"
_ITEM = re.compile(r"(?m)^\s*[（(]?(\d+)[）).、．]\s*(?!\d)")
_INVENTORY = r"(?:庫存|库存|存貨|存货)"
_BLOCKED_DECREASE = r"(?:降不下(?:來|来|去)?|減不下(?:來|来|去)?|减不下(?:來|来|去)?|降低不了|(?:無法|无法|不能|不會|不会|一直不)(?:再)?(?:下降|降低|減少|减少)|下不[來来])"
_DECREASE = r"(?:下降|降低|減少|减少|降下[來来去]|降得下[來来去]?)"
_PREVENT = r"(?:不要|別|别|不得|不能)(?:再)?(?:讓|让|使)?|避免"
_STOCK_ID = r"\b(?:stok|persediaan|inventori|inventory)\b"
_REDUCE_ID = r"\b(?:turun(?:-turun)?|berkurang|menurun|dikurangi|menurunkan|mengurangi|pengurangan|penurunan)\b"


def _norm(text):
    return unicodedata.normalize("NFKC", str(text or ""))


def segments(text):
    """Keep numbered item identifiers; prose is a single searchable scope."""
    text = _norm(text)
    marks = list(_ITEM.finditer(text))
    if not marks:
        return [(None, text)]
    out = [(None, text[:marks[0].start()])]
    occurrences = {}
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        number = mark.group(1)
        occurrences[number] = occurrences.get(number, 0) + 1
        key = number if occurrences[number] == 1 else f"{number}#{occurrences[number]}"
        out.append((key, text[mark.end():end]))
    return out


def build_relations(source):
    relations = []
    for item, text in segments(source):
        compact = re.sub(r"\s+", "", text)
        def add(kind, evidence, meaning, hint, **fields):
            relations.append(dict(kind=kind, item=item, source_evidence=evidence,
                                  meaning_zh=meaning, required_target_meaning_id=hint, **fields))

        for clause in re.split(r"[，,。.!?！？;；\n]", compact):
            m = re.search(_INVENTORY + r".{0,8}?(" + _BLOCKED_DECREASE + ")", clause)
            if m:
                prevention = re.search("(?:" + _PREVENT + r").{0,4}$", clause[:m.start()])
                prevent = bool(prevention)
                add("inventory_change", m.group(),
                    "阻止庫存無法下降，也就是要能降低庫存" if prevent else "陳述庫存目前無法降低",
                    "jangan sampai stok sulit berkurang / pastikan stok bisa berkurang" if prevent else "stok sulit berkurang",
                    state="enable_decrease" if prevent else "blocked_decrease",
                    polarity_evidence=clause[prevention.start():m.end()] if prevention else "")
            else:
                m = re.search(_INVENTORY + r".{0,5}?(" + _DECREASE + ")", clause)
                if m:
                    prevent = bool(re.search(_PREVENT + r".{0,4}$", clause[:m.start()]))
                    enable = bool(re.search(r"(?:要|必須|必须|確保|确保|讓|让).{0,4}$", clause[:m.start()]))
                    if prevent or enable:
                        add("inventory_change", m.group(), "禁止庫存下降" if prevent else "要讓庫存可以降低",
                            "jangan biarkan stok turun" if prevent else "pastikan stok bisa berkurang",
                            state="prevent_decrease" if prevent else "enable_decrease")

        m = re.search(r"(?:跨班(?:別|别)?|(?:對應|对应|因應|因应|各自|所屬|所属)的?班(?:別|别))", compact)
        if m and re.search(r"存|入|資料|资料|帳|账|建檔|建档", compact):
            add("shift_record_scope", m.group(), "建檔或入帳要按所屬工作班別；班別不是學校班級",
                "input data sesuai shift; jangan input lintas shift", cross_prohibited=bool(re.search(_PREVENT + r"跨班", compact)))
        m = re.search(r"非本月|不是本月|不屬於本月|不属于本月", compact)
        if m:
            add("noncurrent_period", m.group(), "非本月是非當月的資料分類，不限定為上個月，也不是人不在本月上班",
                "data/catatan kategori bukan untuk bulan ini / selain bulan berjalan",
                other_period_explicit=bool(re.search(r"上個?月|上个月|下個?月|下个月|前月|次月", compact)))
        m = re.search(r"(?:帳|账|資料|资料|記錄|记录).{0,8}?(?:先)?(?:移出|轉出|转出|搬出)|(?:移出|轉出|转出|搬出)[^，。;；]{0,18}?(?:帳|账|資料|资料|記錄|记录)", compact)
        if m:
            add("record_move_out", m.group(), "把已存入的資料先移出分類，並非刪除帳號或再新增一筆",
                "keluarkan/pindahkan catatan yang sudah dimasukkan", completed=bool(re.search(r"(?:先丟進去|先丢进去|已.{0,4}(?:入|存)|存進去|存进去|丟進去|丢进去)", compact)))
        m = re.search(r"(?:眼色|眼力)(?:要)?(?:好|機靈|机灵)(?:一點|一点|點|点)?|(?:機靈|机灵|識相|识相)一點", compact)
        if m:
            add("situational_awareness", m.group(), "提醒對情況機靈、識相、小心，不是改善外表",
                "lebih peka terhadap situasi / lebih sigap dan hati-hati")
        m = re.search(r"(?:調閱|调阅|調|调|調出|调出|查看|查閱|查阅)(?:一下)?(?:監視器|监视器|監控|监控|CCTV)(?:畫面|画面|錄影|录像)?", compact, re.I)
        if m:
            add("cctv_review", m.group(), "調閱監視器的錄影畫面，不是調整螢幕或攝影機",
                "memeriksa / melihat rekaman CCTV")
        m = re.search(r"(?:釘|钉|盯|查核|追究|查)(?:一下|住)?(?:入庫|入库|入帳|入账|過帳|过账)(?:的)?時間", compact)
        if m:
            prefix = compact[max(0, m.start()-18):m.start()]
            softened = bool(re.search(r"不(?:太|大|會|会)|沒(?:有)?那麼|没(?:有)?那么", prefix))
            add("entry_time_scrutiny", m.group(), "主管查核或追究系統入庫／入帳時間，並非決定存放時間",
                "atasan tidak akan terlalu memeriksa / mempermasalahkan waktu input stok" if softened else "memeriksa waktu input stok",
                softened=softened)
    return relations


def _stock_state(clause):
    """Compose the outer prohibition with the embedded ability/result predicate."""
    if not re.search(_STOCK_ID, clause) or not re.search(_REDUCE_ID, clause):
        return None
    blocked = bool(re.search(r"\b(?:tidak\s+(?:(?:bisa|dapat|kunjung|juga)\s+)?|tak\s+|sulit\s+(?:untuk\s+)?|gagal\s+)(?:turun(?:-turun)?|berkurang|menurun|dikurangi)\b|\b(?:macet|mandek|stagnan)\b", clause))
    prevented = bool(re.search(r"\b(?:jangan|hindari|mencegah|cegah|dilarang|tidak boleh)\b", clause))
    if blocked:
        return "enable_decrease" if prevented else "blocked_decrease"
    if prevented:
        return "prevent_decrease"
    if re.search(r"\b(?:pastikan|harus|wajib|perlu|agar|supaya|usahakan|tetap|bisa|dapat)\b", clause):
        return "enable_decrease"
    return "decrease"


def validate_relations(relations, translation):
    scopes = dict(segments(translation))
    all_text = _norm(translation).lower()
    issues = []
    for relation in relations:
        # A missing/moved numbered item never borrows another item's evidence.
        text = scopes.get(relation['item'], '') if relation['item'] is not None else all_text
        text = text.lower()
        clauses = [s.strip() for s in re.split(r"[.!?;\n]+", text) if s.strip()]
        kind = relation['kind']
        good = True
        if kind == 'inventory_change':
            inventory_clauses = []
            for clause in clauses:
                inventory_clauses.extend(re.split(r"\b(?:dan|tetapi|sedangkan|sementara)\b", clause))
            states = [_stock_state(c) for c in inventory_clauses]
            states = [s for s in states if s]
            good = relation['state'] in states and all(s == relation['state'] for s in states)
        elif kind == 'shift_record_scope':
            without_contrast = re.sub(r"\bbukan\s+(?:kelas|kursus)(?:\s+sekolah)?\b", "", text)
            good = bool(re.search(r"\bshift\b", text)) and not re.search(r"\b(?:kelas|kursus)\b", without_contrast)
            if relation.get('cross_prohibited'):
                good = good and any(re.search(r"\b(?:jangan|dilarang|tidak boleh)\b", c) and re.search(r"\b(?:lintas|antar|lain|beda|berbeda|menyeberang)\b", c) and 'shift' in c for c in clauses)
        elif kind == 'noncurrent_period':
            good = bool(re.search(r"\b(?:bukan|selain|di luar)\s+(?:untuk\s+|dari\s+)?(?:(?:pengiriman|periode|produksi|pencatatan)\s+)?bulan\s+(?:ini|berjalan)\b|\btidak\s+termasuk\s+bulan\s+ini\b|\bnon[ -]bulan\s+(?:ini|berjalan)\b", text))
            good = good and not re.search(r"\b(?:anda|kamu|kalian)\s+(?:tidak|bukan)\s+(?:berada|bekerja)\b", text)
            if not relation.get("other_period_explicit"):
                good = good and not re.search(r"\bbulan\s+(?:lalu|depan)\b", text)
        elif kind == 'record_move_out':
            good = any(re.search(r"\b(?:data|catatan|pencatatan|entri)\b", c) and re.search(r"\b(?:keluarkan|dikeluarkan|mengeluarkan|pindahkan|dipindahkan|memindahkan)\b", c) and not re.search(r"\b(?:hapus|dihapus|menghapus|akun)\b", c) for c in clauses)
            if relation.get('completed'):
                good = good and any(re.search(r"\b(?:sudah|telah)\s+(?:di)?(?:masuk\w*|input\w*|simpan\w*|catat\w*)", c) and re.search(r"\b(?:data|catatan|entri)\b", c) for c in clauses)
        elif kind == 'situational_awareness':
            good = bool(re.search(r"\b(?:peka|sigap|waspada|tanggap|hati-hati|hati hati|mawas diri|pandai membaca situasi)\b", text)) and not re.search(r"\b(?:terlihat|tampak|kelihatan)\s+lebih\s+baik\b", text)
        elif kind == 'cctv_review':
            good = any(re.search(r"\b(?:rekaman|rekam|video|tayangan)\b", c) and re.search(r"\b(?:cctv|kamera|pengawas|pengawasan|pemantau)\b", c) and re.search(r"\b(?:memeriksa|periksa|melihat|lihat|meninjau|mengecek|cek|mengakses|memutar|menonton|mengevaluasi|mengambil|meminta|membuka|menarik|menelusuri|ditinjau|diperiksa|dilihat|diminta|diambil)\b", c) for c in clauses)
        elif kind == 'entry_time_scrutiny':
            good = any(re.search(r"\b(?:memeriksa|mempermasalahkan|mengawasi|mengecek|mengusut|memantau|menyoroti|mempersoalkan|mempertanyakan|memperhatikan|mencermati|meneliti|cek|periksa)\b", c) and re.search(r"\b(?:waktu|jam)\b", c) and re.search(r"\b(?:input|masuk|pencatatan|stok|gudang)\b", c) for c in clauses)
            if relation.get('softened'):
                good = good and any(re.search(r"\b(?:tidak|tak|jarang)\b", c) and re.search(r"\b(?:terlalu|begitu|begitunya|sering|khusus|secara khusus)\b", c) and re.search(r"\b(?:waktu|jam)\b", c) for c in clauses)
        if not good:
            scope = f"item_{relation['item']}:" if relation['item'] is not None else ''
            issues.append(f"factory_instruction:{scope}{kind}:preserve_{relation.get('state', 'source_action')}")
    return list(dict.fromkeys(issues))
