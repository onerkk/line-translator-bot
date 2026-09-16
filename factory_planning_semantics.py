"""Clause-bound planning periods and inventory terminology, without paid I/O.

An operating period through month end is not a completion deadline before it.
Likewise warehouse tonnage and the time its records are entered are separate
claims even when they occur in the same notice. No whole-message replacements.
"""
from __future__ import annotations

import re
import unicodedata


def _norm(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text or ""))).strip().lower()


def source_clauses(text):
    return [re.sub(r"\s+", "", c) for c in re.split(r"[，,。.!?！？;；\n]+", str(text or "")) if c.strip()]


def target_sentences(text):
    # Indonesian thousands/decimal punctuation belongs to the number.
    return [c.strip() for c in re.split(r"(?<!\d)[.;](?!\d)|(?<=\d)[.;](?!\d)|[!?\n]+", _norm(text)) if c.strip()]


_MONTH_ZH = re.compile(r"(?:本月|月)(?:底|末)(?:以?前)?")
_END_ID = r"(?:(?:akhir|penghujung)\s+bulan(?:\s+ini)?|bulan\s+ini\s+berakhir)"
_BEFORE_ID = re.compile(r"\bsebelum\s+" + _END_ID + r"\b", re.I)
_UNTIL_ID = re.compile(r"\b(?:sampai(?:\s+dengan)?|hingga)\s+(?:sebelum\s+)?" + _END_ID + r"\b", re.I)
_NUMBERS = {"一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_ID_NUMBERS = ("nol", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh")


def month_end_relations(source):
    relations = []
    for clause in source_clauses(source):
        match = _MONTH_ZH.search(clause)
        if not match:
            continue
        daily = re.search(r"(?:平均)?(?:每[日天]|一天)(\d+(?:[.,]\d+)?)\s*(?:噸|吨)", clause)
        station = re.search(r"(?:開|开|運轉|运转|啟用|启用)([一二兩两三四五六七八九十]|\d+)(?:個|个)?站", clause)
        # These are continuing plans; completion/arrival/leave deadlines retain
        # the strict BEFORE meaning. A bare mention of 月底 is not a deadline.
        period = bool(daily or station or re.search(r"持續|持续|維持|维持|每天|每日", clause))
        strict = "前" in match.group() and not period
        if not strict and not period:
            continue
        if period and not ("前" in match.group() or re.search(r"到|至|直到|截至", clause[:match.start()])):
            continue
        relation = {"source_evidence": clause, "period": period,
                    "tomorrow": bool(re.search(r"明天(?:起|開始|开始)|從明天|从明天", clause))}
        if daily:
            relation["daily_tons"] = daily.group(1)
        if station:
            raw = station.group(1)
            relation["stations"] = int(raw) if raw.isdigit() else _NUMBERS[raw]
        relations.append(relation)
    return relations


def _positive_time(clause, pattern):
    for match in pattern.finditer(clause):
        # Do not accept "not before" or "after ..., not before ..." as evidence.
        prefix = clause[max(0, match.start() - 35):match.start()]
        if not re.search(r"\b(?:bukan|tidak|tak)(?:\s+lagi)?\s*$", prefix):
            return True
    return False


def validate_month_end(relations, target):
    issues = []
    sentences = target_sentences(target)
    for relation in relations:
        candidates = sentences
        if "daily_tons" in relation:
            value = re.escape(relation["daily_tons"]).replace(r"\.", "[.,]")
            candidates = [c for c in candidates if re.search(r"(?<![\d.,])" + value + r"\s*ton\b", c)
                          and re.search(r"\b(?:per\s+hari|setiap\s+hari|sehari|harian)\b", c)]
        if "stations" in relation:
            count = relation["stations"]
            number = str(count) + ("|" + _ID_NUMBERS[count] if count < len(_ID_NUMBERS) else "")
            candidates = [c for c in candidates if re.search(r"\b(?:" + number + r")\s+stasiun\b", c)
                          and re.search(r"\b(?:mengoperasikan|dioperasikan|beroperasi|menjalankan|dijalankan|membuka|dibuka|buka)\b", c)]
        candidates = [c for c in candidates if _positive_time(c, _BEFORE_ID)
                      or (relation["period"] and _positive_time(c, _UNTIL_ID))]
        if not candidates:
            issues.append("factory_semantic_audit:missing_month_end_period" if relation["period"]
                          else "factory_semantic_audit:missing_month_end_deadline")
        elif relation["tomorrow"] and not any(re.search(r"\b(?:mulai(?:\s+dari)?|dari|sejak)\s+besok\b", c) for c in candidates):
            issues.append("factory_semantic_audit:missing_period_start_tomorrow")
    return list(dict.fromkeys(issues))


_TIMING_ZH = re.compile(r"(?:入庫|入库|入帳|入账|過帳|过账|登錄|登入|紀錄|記錄|记录|資料).{0,12}(?:時間|时间)")
_DISTRIBUTION_ZH = re.compile(r"平均|均勻|均匀|分散|不要集中|別集中|别集中|分批|間隔|间隔|(?:別|别|不要).{0,4}擠|(?:別|别|不要).{0,4}挤")
_PHYSICAL_ZH = re.compile(r"貨車|货车|卡車|卡车|司機|司机|到貨|到货|卸貨|卸货|月台|收貨|收货|送貨|送货|倉庫大門")
_RECORD_ID = re.compile(r"\b(?:data|(?:pen|men|di|ter)?catat(?:an)?(?:nya)?|input|diinput|entri|posting|transaksi|pembukuan)\b", re.I)
_DISTRIBUTION_ID = re.compile(r"\b(?:merata|tersebar|bertahap|interval|tidak\s+menumpuk|tidak\s+terpusat)\b", re.I)
_TIME_ID = re.compile(r"\b(?:waktu|jam|jadwal|pencatatan(?:nya)?|posting)\b", re.I)
_INTAKE_ID = re.compile(r"\b(?:(?:pemasukan|penerimaan)\s+(?:(?:barang|material|bahan)\s+)?(?:(?:di|ke|dalam)\s+)?gudang|"
                        r"(?:masuk|dimasukkan|memasukkan|masukkan)\s+(?:ke\s+)?gudang)\b", re.I)


def record_timing_clauses(source):
    return [c for c in source_clauses(source)
            if _TIMING_ZH.search(c) and _DISTRIBUTION_ZH.search(c) and not _PHYSICAL_ZH.search(c)]


def validate_record_timing(source, target):
    if not record_timing_clauses(source):
        return []
    clauses = [c for c in target_sentences(target) if _TIME_ID.search(c)]
    # A donor "data" in the tonnage sentence cannot validate physical timing.
    record_clauses = [c for c in clauses if _RECORD_ID.search(c)]
    if not record_clauses:
        return ["missing_system_record_semantics"]
    if not any(_DISTRIBUTION_ID.search(c) for c in record_clauses):
        return ["missing_distribution_semantics"]
    return []


def warehouse_intake_present(target):
    return bool(_INTAKE_ID.search(_norm(target)))


def canonicalize_record_timing(source, target):
    """Clarify one existing timing noun; never append an omitted instruction.

    Only the source-established record-time sense is eligible. Mixed physical
    timing, multiple possible target spans, and polarity stay untouched.
    """
    if len(record_timing_clauses(source)) != 1:
        return target
    if any(_PHYSICAL_ZH.search(c) and _TIMING_ZH.search(c) for c in source_clauses(source)):
        return target
    spans = []
    for m in re.finditer(r"[^.!?;\n]+", str(target or "")):
        clause = m.group()
        if (_RECORD_ID.search(clause) or not _DISTRIBUTION_ID.search(clause)
                or re.search(r"\b(?:truk|sopir|bongkar|muat|tiba|datang)\b", clause, re.I)):
            continue
        spans.extend((m.start() + n.start(), m.start() + n.end()) for n in re.finditer(
            r"\bwaktu\s+(?:masuk\s+(?:ke\s+)?gudang|pemasukan\s+gudang)\b", clause, re.I))
    if len(spans) != 1:
        return target
    start, end = spans[0]
    replacement = "waktu pencatatan masuk gudang"
    if target[start].isupper():
        replacement = replacement.capitalize()
    return target[:start] + replacement + target[end:]


def repair_hints(source, issues):
    """Source facts, not a second copy of the failed target, guide one repair."""
    hints = []
    names = " ".join(issues)
    if "month_end" in names or "period_start" in names:
        hints.append("Preserve each month-end clause and its start: ongoing daily/station plans use sampai/hingga akhir bulan; completion deadlines use sebelum akhir bulan; 明天開始 means mulai besok.")
    if "system_record" in names or "distribution_semantics" in names:
        hints.append("入庫時間平均 means distribute the times of recording warehouse entries: waktu pencatatan masuk gudang lebih merata. Keep this instruction separate from warehouse tonnage; do not invent station IDs.")
    if "warehouse_intake" in names:
        hints.append("Preserve the warehouse intake target/quantity (target/jumlah pemasukan gudang), all source values, and their ton units.")
    return " ".join(hints)
