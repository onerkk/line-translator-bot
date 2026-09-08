"""Explicit measurement prerequisites and report-location questions.

Source relations supply the same hints and checks at generation, cache and
delivery. No full-sentence translation table or extra model judge is used.
"""
import re
import unicodedata

BUILD_ID = "2026-09-08.1-measurement-sequence-report-question"
_METRICS = {
    "圓度": ("kebulatan / roundness", r"\b(?:kebulatan|roundness|ketidakbulatan|circularity)\b"),
    "圆度": ("kebulatan / roundness", r"\b(?:kebulatan|roundness|ketidakbulatan|circularity)\b"),
    "直徑": ("diameter", r"\bdiameter(?:nya)?\b"),
    "直径": ("diameter", r"\bdiameter(?:nya)?\b"),
    "長度": ("panjang", r"\bpanjang(?:nya)?\b"),
    "长度": ("panjang", r"\bpanjang(?:nya)?\b"),
    "真直度": ("kelurusan / straightness", r"\b(?:kelurusan|straightness)\b"),
}
_MEASURE = r"\b(?:mengukur|diukur|ukur|pengukuran|memeriksa|diperiksa|periksa|pemeriksaan|mengecek|dicek|cek)\b"
_PRODUCE = r"\b(?:produksi|memproduksi|diproduksi|berproduksi)\b"
_DEPARTMENTS = {
    "研發": ("bagian R&D", r"\b(?:r\s*(?:&|dan|and)\s*d|litbang|penelitian\s+dan\s+pengembangan|riset\s+dan\s+pengembangan)\b"),
    "研发": ("bagian R&D", r"\b(?:r\s*(?:&|dan|and)\s*d|litbang|penelitian\s+dan\s+pengembangan|riset\s+dan\s+pengembangan)\b"),
    "品管": ("bagian QC", r"\b(?:qc|kontrol kualitas|pengendalian mutu|pengendalian kualitas)\b"),
}


def build_relations(source):
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(source or "")))
    relations = []
    # Parse only explicit wait/measure/then-produce instructions. A question,
    # a negated wait, or the opposite 'produce first' order is another meaning.
    prerequisite = re.search(
        r"(?P<pause>(?:先|暫時|暂时)?(?:不要|不能|不得|不可以|不可)(?:先生產|先生产|生產|生产)[,，。]*)?"
        r"(?:等待|等)(?P<department>研發|研发|品管)(?:單位|单位|部門|部门|人員|人员)?"
        r"(?:先)?(?:量|量測|量测|測量|测量|檢查|检查)(?:過|过|完|好)?"
        r"(?P<metric>" + "|".join(_METRICS) + r")(?:後|后|完成|完|好)?"
        r"[,，。]*(?:才|再|在)(?:開始|开始|繼續|继续|恢復|恢复)?(?:生產|生产)", compact)
    if prerequisite and not re.search(r"不用等|不必等|不要等|無需等|无需等|要不要|能不能|可不可以|[?？]", compact):
        metric = prerequisite.group("metric")
        department = prerequisite.group("department")
        relations.append({
            "kind": "measurement_before_production", "source_evidence": prerequisite.group(),
            "meaning_zh": "先等待指定單位完成指定項目的量測，再開始／繼續生產；不得顛倒先後或把圓度換成粗糙度；量過不等於已合格放行",
            "required_target_meaning_id": f"tunggu {_DEPARTMENTS[department][0]} mengukur {_METRICS[metric][0]}, baru produksi",
            "metric": metric, "department": department,
            "polarity_evidence": prerequisite.group("pause") or "",
        })
    report = re.search(r"(?:報表|报表)(?:被|是)?(?:放置|放|擺|摆|收)(?:在|到)?(?:哪裡|哪裏|哪里|哪邊|哪边|何處|何处)", compact)
    if report and len(re.findall(r"報表|报表", compact)) == 1:
        process = next((value for zh, value in (("拋光", "polishing"), ("抛光", "polishing"),
                                                ("研磨", "grinding")) if zh in compact), "")
        relations.append({
            "kind": "report_location_question", "source_evidence": report.group(),
            "meaning_zh": "詢問既有報表放置位置，不是命令填表或回答位置；保留報表日期、工序和手寫媒介",
            "required_target_meaning_id": "laporan ... diletakkan/disimpan di mana?" + (" kemarin" if "昨天" in compact else ""),
            "handwritten": bool(re.search(r"手寫|手写", compact)),
            "yesterday": "昨天" in compact, "process": process,
        })
    return relations


def validate_relation(relation, target):
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", target or "")).lower()
    if relation["kind"] == "report_location_question":
        if not re.search(r"\b(?:di\s*mana|di sebelah mana|di tempat mana)\b", text):
            return False
        if not re.search(r"\b(?:laporan|lembar laporan|formulir laporan)\b", text):
            return False
        if relation["yesterday"] and (not re.search(r"\bkemarin\b", text) or re.search(r"\b(?:hari ini|besok)\b", text)):
            return False
        if relation["handwritten"] and not re.search(r"\b(?:tulis(?:an)? tangan|ditulis (?:dengan )?tangan)\b", text):
            return False
        if relation["process"] == "polishing" and not re.search(r"\b(?:polishing|pemolesan|pengilapan|poles)\b", text):
            return False
        if relation["process"] == "grinding" and not re.search(r"\b(?:grinding|penggerindaan|gerinda)\b", text):
            return False
        return True

    metric = _METRICS[relation["metric"]][1]
    department = _DEPARTMENTS[relation["department"]][1]
    if not (re.search(metric, text) and re.search(_MEASURE, text) and re.search(department, text)):
        return False
    if not (re.search(_MEASURE + r"[^.!?;,]{0,60}" + metric, text) or
            re.search(metric + r"[^.!?;,]{0,40}" + _MEASURE, text)):
        return False
    # Explicit cancellation of the prerequisite or approval invented from '量過'.
    if re.search(r"\b(?:tanpa|tidak perlu|tak perlu|jangan)\s+(?:lagi\s+|untuk\s+)?" + _MEASURE, text):
        return False
    if re.search(r"\b(?:lulus|disetujui|persetujuan|memenuhi standar|dinyatakan layak)\b", text):
        return False
    if re.search(r"\b(?:baru|kemudian|lalu|setelah itu)\s+(?:jangan|tidak perlu|tidak boleh)\s+" + _PRODUCE, text):
        return False
    # Require a local link between the named department and its measurement.
    if not (re.search(department + r"[^.!?;]{0,55}" + _MEASURE, text) or
            re.search(_MEASURE + r"[^.!?;]{0,60}\boleh\s+(?:bagian\s+|tim\s+)?" + department, text)):
        return False
    productions = list(re.finditer(_PRODUCE, text))
    if not productions:
        return False
    for match in productions:
        start = max(text.rfind(mark, 0, match.start()) for mark in ".!?;") + 1
        prefix = text[start:match.start()]
        tail = text[match.end():]
        # 'production first, measurement later' is not a release prerequisite.
        forbidden = bool(re.search(r"\b(?:jangan|dilarang|tidak boleh|belum boleh|tunda|ditunda|hentikan|dihentikan)\b", prefix))
        if not forbidden and re.match(r"\s*(?:dulu|terlebih dahulu|sekarang)\b", tail):
            return False
    after_measure = re.search(_MEASURE + r"[^.!?;]{0,100}(?:\bbaru\b|\bkemudian\b|\blalu\b|\bsetelah itu\b)[^.!?;]{0,45}" + _PRODUCE, text)
    after_clause = re.search(r"\b(?:setelah|sesudah)\b[^.!?;]{0,100}" + _MEASURE + r"[^.!?;]{0,90}" + _PRODUCE, text)
    production_after = re.search(_PRODUCE + r"[^.!?;]{0,55}\b(?:setelah|sesudah)\b[^.!?;]{0,100}" + _MEASURE, text)
    prohibited_before = re.search(r"\b(?:jangan|tidak boleh|belum boleh)\b[^.!?;]{0,40}" + _PRODUCE + r"[^.!?;]{0,55}\bsebelum\b[^.!?;]{0,100}" + _MEASURE, text)
    # A full stop may separate the completed prerequisite and resumption.
    split_wait = re.search(r"\btunggu\b[^!?;]{0,150}" + _MEASURE + r"[^!?;]{0,90}\.\s*(?:setelah itu|kemudian|baru)[^.!?;]{0,50}" + _PRODUCE, text)
    return bool(after_measure or after_clause or production_after or prohibited_before or split_wait)
