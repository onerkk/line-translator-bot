"""Source-owned speech acts and operations in terse factory chat.

Extract operation/notation relationships, rather than store whole-message
translations. A requested inscription is not an inscription already present;
permission words are not questions. Unpacking does not specify how surplus is
removed or redistributed. Local repairs only change an unambiguously owned
predicate; uncertain references continue to require original conversation.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

BUILD_ID = "2026-10-13.1-source-owned-chat-operations"
_SUPPORTED = {("zh", "id"), ("id", "zh")}
_ATOM = re.compile(r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]+|__[A-Z0-9_]+__", re.I)
_QUOTE = re.compile(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"\n]*"')
_CLAUSE = re.compile(r"[^。！？!?;；\n]+[。！？!?;；\n]*")
_LABEL = r"(?:[A-Z][A-Z0-9]*(?:[/._-][A-Z0-9]+)*|\d+(?:[A-Z][A-Z0-9]*|[/._-][A-Z0-9]+)(?:[/._-][A-Z0-9]+)*)"
_WRITE_ZH = re.compile(r"(?P<verb>寫上|写上|加寫|加写|填寫|填写|加註|加注|標註|标注|註記|注记|寫|写|填|標示|标示)\s*[「\"']?(?P<label>" + _LABEL + r")(?![A-Za-z0-9/_-])")
_WRITE_ID = re.compile(r"\b(?P<verb>(?i:tulis(?:kan)?|catat(?:kan)?|tambahkan|beri\s+(?:tanda|kode)))\s+[\"']?(?P<label>" + _LABEL + r")(?![A-Za-z0-9/_-])")
_NEG_ZH = re.compile(r"不要|不得|不可以|不可|禁止|別|别|勿")
_NEG_ID = re.compile(r"\b(?:jangan|dilarang|tidak\s+boleh)\b", re.I)
_REQUEST_ZH = re.compile(r"請|请|麻煩|麻烦|幫忙|帮忙|幫我|帮我|需要|必須|必须|(?:^|[，,\s])要|應該|应该|應|应")
_UNCERTAIN = re.compile(r"不確定|不确定|是否|可能|也許|也许|或許|或许|\b(?:mungkin|barangkali|belum\s+pasti)\b", re.I)
_SERIES_ZH = re.compile(r"(?P<series>\d{3})\s*(?:系(?:列)?|系列)")
_SERIES_ID = re.compile(r"\bseri(?:es)?\s+(?P<series>\d{3})(?!\d)", re.I)
_CONDITION_ZH = re.compile(r"如果|假如|倘若|若(?:是|工單|工单|鋼種|钢种|為|为)|只要")
_CONDITION_ID = re.compile(r"\b(?:jika|apabila|kalau|bila|asalkan)\b", re.I)
_SURPLUS_ZH = re.compile(r"超[產产]|超過(?:訂單|订单|允收)|超出(?:訂單|订单)")
_SURPLUS_ID = re.compile(r"\b(?:kelebihan|melebihi|berlebih|overproduksi|overproduction)\b", re.I)
_UNPACK_ZH = re.compile(r"拆(?:開|开|包|捆)?")
_UNPACK_ID = re.compile(r"\b(?:dibongkar|bongkar(?:lah)?|membongkar|pembongkaran|(?:dibuka|buka|membuka)\s+(?:kembali|lagi))\b", re.I)
_SPLIT_ZH = re.compile(r"分(?:成|為|为).{0,6}(?:捆|把|包)|分捆|分裝|分装|分開包裝|分开包装")
_SPLIT_ID = re.compile(r"\b(?:dipisah(?:kan)?|dibagi|dipecah|memisahkan|membagi)\s+menjadi\s+(?:beberapa|\d+)\s+(?:kemasan|bundel|paket)\b", re.I)
_PACK_ZH = re.compile(r"包[裝装]|打包|捆包|拆包|拆捆")
_PACK_ID = re.compile(r"\b(?:packing|kemasan|pengemasan|mengemas|dikemas|bundel)\b", re.I)
_WEIGHT = r"(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>公噸|公吨|噸|吨|公斤|千克|ton|kilogram|kg)(?![A-Za-z0-9])"
_WEIGHT_RE = re.compile(r"(?<![A-Za-z0-9.])" + _WEIGHT, re.I)
_LIFT_ZH = re.compile(r"吊(?!牌|飾|饰)|起重|承重")
_LIFT_ID = re.compile(r"\b(?:angkat|mengangkat|pengangkatan|beban|crane|derek)\b", re.I)
_UPPER_ZH = re.compile(r"(?:(?:不要|不可|不得|不能|不准|禁止)(?:[^，,。！？!?;；\n]{0,12})(?:超過|超过)|不得大於|不得大于|不超過|不超过|上限(?:為|为|是)?|最多|至多)\s*$")
_UPPER_ID = re.compile(r"\b(?:jangan|dilarang|tidak\s+boleh)[^.!?;\n]{0,80}\b(?:lebih\s+dari|melebihi)\s*$|\b(?:maksimal|maksimum|paling\s+banyak|tidak\s+lebih\s+dari)\s*(?:adalah\s*)?$|(?:<=|≤)\s*$", re.I)


def _weight_value(raw):
    return Decimal(raw.replace(",", "."))


def _weight_unit(raw):
    return "ton" if raw.casefold() in {"噸", "吨", "公噸", "公吨", "ton"} else "kg"


def _norm(value):
    return unicodedata.normalize("NFKC", str(value or ""))


def _lang(value):
    value = str(value or "").lower()
    return "zh" if value.startswith("zh") else "id" if value.startswith("id") else value


def is_direct_question(text):
    """Use question syntax, never bare boleh/perlu (permission/necessity).

    Ignore question marks inside URLs, protected atoms and quoted data. A
    reported/quoted question does not turn its surrounding notice into a query.
    """
    source = _ATOM.sub(" ", _norm(text))
    source = _QUOTE.sub(" ", source).strip()
    if not source:
        return False
    if re.search(r"[?？]", source):
        return True
    if len(source) > 120:
        return False
    if re.search(r"(?:詢問|询问|問到|问到|問了|问了|表示|回報|回报|說|说).{0,18}(?:為什麼|为什么|怎麼|怎么|何時|何时|哪裡|哪里)|\b(?:menanyakan|bertanya|mengatakan|melaporkan).{0,30}(?:mengapa|kenapa|bagaimana|kapan|di\s+mana)\b", source, re.I):
        return False
    return bool(re.search(
        r"(?:嗎|吗|呢)(?:[。！!\s]*$)|(?:什麼|什么)(?:[。！!\s]*$)|哪(?:個|个|一種|一种|些)|要不要|需不需要|可不可以|有沒有|有没有|為什麼|为什么|怎麼|怎么|何時|何时|哪裡|哪里|"
        r"\b(?:apakah|kenapa|mengapa|bagaimana|kapan|berapa|di\s+mana)\b|^apa\b|\bapa\s*$|"
        r"^(?:boleh(?:kah)?|bisa(?:kah)?|dapat(?:kah)?)\s+(?:saya|aku|kami)\b",
        source, re.I))


def _literal_pattern(label):
    return re.compile(r"(?<![A-Za-z0-9/_-])" + re.escape(label) + r"(?![A-Za-z0-9/_-])")


def _owned_clauses(candidate, label):
    pattern = _literal_pattern(label)
    return [m for m in _CLAUSE.finditer(str(candidate or "")) if pattern.search(m.group())]


def _unpack_prohibited(text, position, lang):
    prefix = text[max(0, position - 60):position]
    pattern = (r"(?:不要|不得|不可|不用|不需|無需|无需|禁止)(?:再|隨意|随意|任意|擅自|先)*\s*$"
               if lang == "zh" else r"\b(?:jangan|dilarang|tidak\s+(?:boleh|perlu|harus))\s+(?:(?:lagi|sembarangan)\s+)?$")
    return bool(re.search(pattern, prefix, re.I))


def relations(source, src, tgt):
    """Read conservative relationships from one exact source unit."""
    src, tgt = _lang(src), _lang(tgt)
    if (src, tgt) not in _SUPPORTED:
        return []
    value = _norm(source)
    units = [m.group() for m in _CLAUSE.finditer(value) if m.group().strip()]
    if len(units) > 1:
        return [row for unit in units for row in relations(unit, src, tgt)]
    prose = _ATOM.sub(" ", value)
    question = is_direct_question(prose)
    rows = []
    writes = _WRITE_ZH if src == "zh" else _WRITE_ID
    for match in writes.finditer(prose):
        before = prose[:match.start()]
        local = re.split(r"[。！？!?;；\n]", before)[-1]
        if _UNCERTAIN.search(local):
            continue
        neg = _NEG_ZH.search(local[-12:]) if src == "zh" else _NEG_ID.search(local[-30:])
        conditional = bool((_CONDITION_ZH if src == "zh" else _CONDITION_ID).search(before))
        series = list((_SERIES_ZH if src == "zh" else _SERIES_ID).finditer(prose))
        if src == "zh":
            request = bool(_REQUEST_ZH.search(local) or conditional or re.search(r"(?:要|需|應|应|應該|应该)\s*$", local))
            described = bool(re.search(r"(?:已經|已经|已|早就|剛剛|刚刚).{0,5}$", local))
        else:
            # The imperative stem is distinct from tertulis/ditulis/menulis.
            request = not re.search(r"\b(?:sudah|telah|sedang|saya|dia|kami|mereka)\s*$", local, re.I)
            described = not request
        mode = "question" if question else "prohibit" if neg else "instruct" if request and not described else "describe"
        if mode == "describe":
            continue  # No speculative conversion of an ambiguous short statement.
        rows.append({"operation": "annotation", "mode": mode,
                     "label": match["label"], "conditional": conditional,
                     "series": [m["series"] for m in series],
                     "work_order": bool(re.search(r"工[單单]|\bwork\s*order\b", prose, re.I)),
                     "instruction": "Keep the exact written code and its question/instruction/prohibition. A writing instruction must use tulis/catat/tambahkan, not tertulis/tertera (already present). 系 after a grade is its steel family (seri), not a work-order number. Preserve if-conditions and the work-order location. Never treat a leading bare code as a person or infer an equipment identity."})
    approval = (re.search(r"(?P<label>" + _LABEL + r")\s*(?:也可以|也行|也可|也能用)", prose)
                if src == "zh" else re.search(r"(?P<label>" + _LABEL + r")\s+(?:juga\s+)?(?:boleh|bisa|dapat)(?!\w)", prose))
    if approval and not question:
        rows.append({"operation": "notation_approval", "label": approval["label"],
                     "instruction": "This permits this exact notation. It is a statement, not a confirmation question; boleh/bisa/dapat do not authorize a question mark or question emoji. Preserve slash/letter placement."})
    surplus = (_SURPLUS_ZH if src == "zh" else _SURPLUS_ID).search(prose)
    unpack = (_UNPACK_ZH if src == "zh" else _UNPACK_ID).search(prose)
    packing = (_PACK_ZH if src == "zh" else _PACK_ID).search(prose)
    if surplus and unpack and packing:
        split_explicit = bool((_SPLIT_ZH if src == "zh" else _SPLIT_ID).search(prose))
        prohibited = _unpack_prohibited(prose, unpack.start(), src)
        rows.append({"operation": "surplus_unpacking", "split_explicit": split_explicit,
                     "prohibited": prohibited,
                     "instruction": "When the order surplus is excessive, the source asks to dismantle/open the existing packing (bongkar/dibongkar kembali). Preserve the condition and original scope. Unless separately stated, do not specify splitting into several packs, removing bars, scrapping or adding a destination. A later return-to-warehouse/unpacking report is separate evidence, not a new instruction to split every bundle."})
    lift = (_LIFT_ZH if src == "zh" else _LIFT_ID).search(prose)
    if lift:
        for weight in _WEIGHT_RE.finditer(prose):
            before = prose[:weight.start()]
            upper = (_UPPER_ZH if src == "zh" else _UPPER_ID).search(before)
            if upper:
                rows.append({"operation": "lifting_limit", "value": weight["number"],
                             "unit": _weight_unit(weight["unit"]),
                             "instruction": "This is the MAXIMUM permitted lifting weight. Keep the numeric value and its unit together, and keep the prohibition on exceeding it (jangan mengangkat lebih dari / maksimal). Do not change the value, replace tons by kilograms, state a minimum or weaken the safety limit. Decimal comma/point are presentation alternatives; do not calculate a new limit."})
    if src == "zh" and re.search(r"(?:請|请|叫|讓|让)(?:他|她|對方|对方).{0,8}先放\s*[一二兩两三四五六七八九\d]+\s*(?:把|捆)(?=[嗎吗？?。！!\s]*$)", prose):
        rows.append({"operation": "contextual_bundle_release",
                     "instruction": "A third-person request to 先放 a bundle is context-dependent. Resolve 放 from prior ORIGINAL messages/quoted work-order data: ERP handoff uses release data; explicit loading/unloading uses the physical movement verb. A computer photo alone does not prove a physical loading operation. Without decisive evidence, retain the shop-floor word release rather than invent memasukkan, a destination or completed status. Preserve the request, third-person actor, bundle count and priority."})
    # This common typing error is resolvable only with the complete lifting,
    # equipment/personnel and passing-above safety relationship.
    if src == "zh" and re.search(r"天車|天车|起重機|起重机", prose) and re.search(r"嚴禁|严禁|禁止|不得", prose) and re.search(r"(?:設備|设备).{0,12}(?:人員|人员|人).{0,8}(?:上方|上樓方|上楼方).{0,8}(?:經過|经过|通過|通过)", prose):
        rows.append({"operation": "overhead_prohibition",
                     "instruction": "The crane/lifted load must not pass above equipment and personnel. In this explicit relationship 上樓方 is a typo for 上方 (di atas), not upstairs. Preserve the strict prohibition and safety distance; do not add an equipment identity or unspecified sling type."})
    return rows


def validate(rows, candidate, src, tgt):
    src, tgt = _lang(src), _lang(tgt)
    target = _norm(candidate)
    issues = []
    for row in rows:
        op = row["operation"]
        if op in {"annotation", "notation_approval"}:
            clauses = _owned_clauses(target, row["label"])
            if not clauses:
                issues.append("chat_operation:annotation_literal_missing:" + row["label"])
                continue
            if op == "notation_approval":
                if any(is_direct_question(c.group()) or re.search(r"[❓❔🤔]", c.group()) for c in clauses):
                    issues.append("chat_operation:approval_changed_to_question")
                continue
            mode = row["mode"]
            if mode == "question":
                if not any(is_direct_question(c.group()) for c in clauses):
                    issues.append("chat_operation:annotation_question_missing")
                continue
            if tgt == "id":
                write = re.compile(r"\b(?:tulis(?:kan|lah)?|catat(?:kan|lah)?|tambahkan|beri\s+(?:tanda|kode)|(?:harus|perlu|wajib|jangan|tidak\s+boleh)\s+(?:di)?(?:tulis|catat))\b", re.I)
                neg = _NEG_ID
                described = re.compile(r"\b(?:tertulis|tertera|tercantum|(?:sudah|telah|belum|sedang)\s+(?:di)?(?:tulis|catat))\b", re.I)
            else:
                write = re.compile(r"寫|写|填|註記|注记|標註|标注|加註|加注")
                neg = _NEG_ZH
                described = re.compile(r"(?:已經|已经|已|早就).{0,8}(?:寫|写|填|標|标)|寫著|写着|寫有|写有")
            owned = [c.group() for c in clauses]
            if not any(write.search(c) and not described.search(c) for c in owned):
                issues.append("chat_operation:annotation_instruction_changed_to_state")
            if mode == "prohibit" and not any(neg.search(c) for c in owned):
                issues.append("chat_operation:annotation_prohibition_missing")
            if mode == "instruct" and any(neg.search(c) for c in owned):
                issues.append("chat_operation:annotation_polarity_changed")
            if row.get("conditional") and not (_CONDITION_ID if tgt == "id" else _CONDITION_ZH).search(target):
                issues.append("chat_operation:annotation_condition_missing")
            for series in row.get("series", ()):
                pattern = (r"\bseri(?:es)?\s+" + re.escape(series) + r"(?!\d)" if tgt == "id" else re.escape(series) + r"\s*(?:系|系列)")
                if not re.search(pattern, target, re.I):
                    issues.append("chat_operation:grade_series_missing:" + series)
            if row.get("work_order") and not re.search(r"\bwork\s*order\b" if tgt == "id" else r"工[單单]", target, re.I):
                issues.append("chat_operation:annotation_work_order_missing")
        elif op == "surplus_unpacking":
            # A later returned-material report cannot supply the missing action
            # in the earlier excessive-production instruction.
            scope = target.split("\n\n", 1)[0]
            if not (_UNPACK_ID if tgt == "id" else _UNPACK_ZH).search(scope):
                issues.append("chat_operation:surplus_unpacking_action_missing")
            if not row["split_explicit"] and (_SPLIT_ID if tgt == "id" else _SPLIT_ZH).search(scope):
                issues.append("chat_operation:surplus_split_method_invented")
            predicates = list((_UNPACK_ID if tgt == "id" else _UNPACK_ZH).finditer(scope))
            if predicates and not any(_unpack_prohibited(scope, m.start(), tgt) == row.get("prohibited", False) for m in predicates):
                issues.append("chat_operation:surplus_unpacking_polarity_changed")
        elif op == "overhead_prohibition":
            if not (_NEG_ID.search(target) and re.search(r"\bdi\s+atas\b", target, re.I)):
                issues.append("chat_operation:overhead_prohibition_missing")
            if re.search(r"\b(?:lantai\s+atas|naik\s+ke)\b", target, re.I):
                issues.append("chat_operation:overhead_changed_to_upstairs")
        elif op == "lifting_limit":
            weights = [m for m in _WEIGHT_RE.finditer(target)
                       if _weight_value(m["number"]) == _weight_value(row["value"])
                       and _weight_unit(m["unit"]) == row["unit"]]
            if not weights:
                issues.append("chat_operation:lifting_limit_value_or_unit_changed")
            elif not any((_UPPER_ID if tgt == "id" else _UPPER_ZH).search(target[:m.start()]) for m in weights):
                issues.append("chat_operation:lifting_limit_direction_changed")
    return list(dict.fromkeys(issues))


def canonicalize(source, candidate, src, tgt):
    """Narrow predicate repairs, with no guessed physical or ERP operation."""
    src, tgt = _lang(src), _lang(tgt)
    result = str(candidate or "")
    if (src, tgt) != ("zh", "id"):
        return result
    rows = relations(source, src, tgt)
    for row in rows:
        if row["operation"] == "annotation" and row["mode"] == "instruct":
            # Multiple identical labels/clauses do not establish ownership.
            clauses = _owned_clauses(result, row["label"])
            if len(clauses) != 1 or sum(r.get("label") == row["label"] for r in rows) != 1:
                continue
            clause = clauses[0]
            old = clause.group()
            if _NEG_ID.search(old):
                continue
            pattern = re.compile(r"\b(?P<loc>di\s+atasnya\s+)?(?:(?:sudah|telah)\s+)?(?:tertulis|tertera|tercantum|ditulis)\s+[\"']?" + re.escape(row["label"]) + r"[\"']?(?![A-Za-z0-9/_-])", re.I)
            matches = list(pattern.finditer(old))
            if len(matches) != 1:
                continue
            if re.search(r"\b(?:belum|sedang)\s*$", old[:matches[0].start()], re.I):
                continue
            label = row["label"]
            fixed = pattern.sub(lambda m: "tulis " + label + (" di atasnya" if m["loc"] else ""), old, count=1)
            result = result[:clause.start()] + fixed + result[clause.end():]
        elif row["operation"] == "notation_approval":
            clauses = _owned_clauses(result, row["label"])
            if len(clauses) != 1:
                continue
            clause = clauses[0]
            old = clause.group()
            if not re.search(r"\b(?:boleh|bisa|dapat)\b", old, re.I) or _NEG_ID.search(old):
                continue
            fixed = re.sub(r"^\s*apakah\s+", "", old, flags=re.I)
            fixed = re.sub(r"[?？]+", ".", fixed)
            if not re.search(r"[❓❔🤔]", str(source or "")):
                fixed = re.sub(r"[❓❔🤔]\ufe0f?", "", fixed)
            result = result[:clause.start()] + fixed.rstrip() + result[clause.end():]
        elif row["operation"] == "surplus_unpacking" and not row["split_explicit"] and not row.get("prohibited") and not _SPLIT_ZH.search(_norm(source)):
            matches = list(_SPLIT_ID.finditer(result))
            if len(matches) == 1:
                result = _SPLIT_ID.sub("dibongkar kembali", result, count=1)
    return result
