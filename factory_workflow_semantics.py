"""Source-bound shop-floor senses learned from the September 16 screenshots.

No I/O, generation, delivery decisions or exact-sentence translation table.
Diagnostics describe positive evidence of a wrong sense, not a compulsory
target wording. Ambiguous/mixed senses receive prompt advice, not a rewrite.
"""
from __future__ import annotations

import re

BUILD_ID = "2026-09-19.1-station-handoff-scope"
_CJK = r"\u3400-\u9fff"
_ZONE = r"(?:儲區|储区|儲位|储位)"
_SYSTEM = r"存檔|存档|保存|資料|数据|欄位|字段|預設|默认|系統|系统|驗證|验证"
_PHYSICAL = r"空間|空间|放不下|容量|搬運|搬运|卸貨|卸货"
_ASSEMBLY = r"組裝|组装|安裝|安装|釘木箱|钉木箱|修理木箱"
_COUNT = r"[一二兩两三四五六七八九十]|\d+"
_NUMBERS = dict(zip("一二三四五六七八九十", range(1, 11)), 兩=2, 两=2)
_ID_NUMBERS = ("nol", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh")
_MEANINGS = {
    "record_save": ("包裝／入庫存檔是儲存作業資料，不是檔案保管。", "menyimpan data pengemasan / pencatatan masuk gudang, not penyimpanan arsip"),
    "storage_field": ("缺少的是儲区資訊／欄位值，不是實體倉庫空間。", "informasi/kolom lokasi penyimpanan kosong/tidak muncul; not a lack of warehouse space"),
    "printed_storage": ("使用者確認：存檔入庫都沒儲區是 TAG 首次列印沒印出儲區，不代表系統欄位空白或倉庫沒有空間。", "lokasi penyimpanan tidak tercantum pada TAG yang pertama kali dicetak; distinguish printed information from the system field"),
    "storage_update": ("此處維護是手動填寫或更新已知的儲區資料，保留知道儲區才操作的條件。", "mengisi/memperbarui data lokasi penyimpanan secara manual, not mechanical maintenance; preserve the source condition"),
    "pending_crate_packing": ("使用者確認：待裝木箱是成品等待裝入木箱，不是木箱等待安裝；若原文明說原料或其他物件則保留該物件。", "produk jadi yang menunggu dikemas ke dalam peti kayu, not peti kayu yang menunggu dipasang; preserve an explicitly different source object"),
    "station_operation": ("人力安排開幾站指運轉現有工作站，不是設立新站。", "mengoperasikan/menjalankan the stated number of stasiun; keep the staffing, priority and period"),
    "packing_station_operation": ("使用者確認：開三站是開動圓型、異型、削皮三個包裝站；不是站號3，也不是削皮加工站。", "mengoperasikan tiga stasiun packing: packing batang bulat, packing barang bentuk khusus, packing peeling; no need to expand all names if the source only states the count"),
    "intake_quantity": ("使用者確認：資料進入801就算入庫；同段入庫計畫的入幾噸是登錄該噸數成品入庫，不是資料筆數或另外要求實體搬運。原文沒寫801時不增加站號。", "record the stated tonnage of goods as warehouse intake in the system: catat pemasukan gudang ... ton dalam sistem; preserve goods weight, not a count of records; no invented station IDs"),
    "storage_destination": ("使用者確認：此包裝簡語指定本次成品實體吊入的儲區，無論 TAG 印哪個儲區都依當次指定；不是支援機台或永久更改客戶預設。", "angkat dan pindahkan produk jadi ke area penyimpanan specified in THIS source, regardless of the storage location printed on TAG; customer owns the material"),
}

_PRINT_SHORTHAND = r"存[檔档][、，,]?入[庫库](?:都|也)?(?:沒|没|沒有|没有)(?:有)?(?:儲區|储区|儲位|储位)"
_OTHER_STATION = r"研磨站|拋光站|抛光站|檢驗站|检验站|削皮加工站|捷運|地鐵|地铁|鐵路|铁路|新[設设建開开]|擴建|扩建|開幕|开幕"

# These grammars consume the complete operational statement. A station value
# is bound from THIS source; neither 452 nor 480 is a replacement constant.
_HANDOFF_QUESTION = re.compile(
    r"(?:^|\n)[ \t]*(?P<statement>(?:資料|资料|數據|数据)"
    r"(?P<all>都|全部|全)?(?:卡在|卡|停在|留在)\s*(?P<station>\d{3})\s*(?:站)?"
    r"[，,\s]*(?:是)?(?:放不過來|放不过来|放不過去|放不过去|無法放行|无法放行|不能放行)"
    r"[，,\s]*(?:還是|还是|或是)(?:忘記|忘记|忘了|忘)(?:放行|放)(?:了)?[?？。.\s]*)\Z"
)
_PENDING_INSPECTION = re.compile(
    r"\s*(?:資料|资料|數據|数据)?(?:尚未|還沒(?:有)?|还没(?:有)?|沒有|没有|未)放行"
    r"[，,。\s]+(?P<station>\d{3})(?:站)?(?:都)?不(?:檢驗|检验|驗|验)[。.!！\s]*\Z"
)


def handoff_question(source):
    match = _HANDOFF_QUESTION.search(str(source or ""))
    return dict(match.groupdict(), start=match.start("statement")) if match else None


def unresolved_source_ambiguities(source):
    """No automatic 再→在 migration and no unsupervised learning of a guess."""
    binding = handoff_question(source)
    if binding and re.fullmatch(r"\s*(?:材料|料)再包[裝装][，,。\s]*", str(source)[:binding["start"]]):
        return ["factory_workflow:ambiguous_packing_location_or_repeat"]
    return []


def _handoff_issues(source, target):
    found = []
    binding = handoff_question(source)
    if binding:
        if not re.search(r"\b(?:apa(?:kah)?|bisa(?:kah)?)\b|[?？]", target, re.I) or not re.search(r"\batau\b", target, re.I):
            found.append("handoff_alternatives_not_a_question")
        if re.search(r"\blupa\s+(?:meletakkan|menaruh|menempatkan)(?:nya)?\b", target, re.I):
            found.append("erp_handoff_as_placement")
        if not re.search(r"\b(?:release|rilis|dirilis|merilis)\b", target, re.I):
            found.append("erp_handoff_release_missing")
        if not re.search(r"(?<!\d)" + binding["station"] + r"(?!\d)", target):
            found.append("handoff_station_missing")
    pending = _PENDING_INSPECTION.fullmatch(str(source or ""))
    if pending:
        if re.search(r"\bdisetujui\b", target, re.I):
            found.append("erp_release_as_general_approval")
        if re.search(r"\b" + pending["station"] + r"\s+(?:semuanya|buah|batang|bundel)\b", target, re.I):
            found.append("inspection_station_as_quantity")
        if re.search(r"\b(?:tidak|belum)\s+(?:bisa|dapat|mau)\s+(?:di)?(?:periksa|memeriksa|melakukan)\b", target, re.I):
            found.append("inspection_ability_or_intent_added")
    return ["factory_workflow:" + item for item in found]


def _canonicalize_handoff(source, target):
    findings = _handoff_issues(source, target)
    pending = _PENDING_INSPECTION.fullmatch(str(source or ""))
    if pending and findings:
        # 不驗 alone does not identify inability, refusal, or a causal rule.
        return ("Data belum di-release ke stasiun berikutnya.\n"
                f"Stasiun {pending['station']} tidak melakukan pemeriksaan.")
    binding = handoff_question(source)
    if not binding or not findings:
        return target
    prefix = str(source)[:binding["start"]]
    if prefix.strip() and not re.fullmatch(r"\s*(?:材料|料)[在再]包[裝装](?:站)?[，,。\s]*", prefix):
        return target  # More source clauses require model interpretation.
    data_tail = re.search(r"(?<!\w)Data(?:nya)?\b[^\n]*\b(?:tertahan|macet|terhenti|tersangkut)\b[^\n]*"
                          + r"\b" + binding["station"] + r"\b[^\n]*[?.]?\s*\Z", target, re.I)
    if not data_tail:
        return target
    quantifier = " semuanya" if binding["all"] else ""
    fixed = (f"Data{quantifier} masih tertahan di stasiun {binding['station']}. "
             "Apakah datanya tidak bisa di-release ke stasiun berikutnya, atau lupa di-release?")
    return target[:data_tail.start()] + fixed


def _printed_storage_binding(source):
    # Only the complete, confirmed shorthand may be rendered as a whole.
    # A longer message can use the same first-pass fact without losing clauses.
    return bool(re.fullmatch(r"\s*" + _PRINT_SHORTHAND + r"\s*[。.!！]?\s*", str(source or "")))


def _printed_storage_wrong(source, target):
    if not _printed_storage_binding(source) or not target:
        return False
    if re.search(r"\b(?:TAG|label|etiket)\b|\b(?:cetak|dicetak|tercetak|tercetaknya|pencetakan)\b", target, re.I):
        return False
    # Evidence of the reported wrong object, rather than a target vocabulary
    # allowlist. Natural synonyms about a printed label remain untouched.
    return bool(re.search(r"penyimpanan\s+arsip|(?:belum|tidak)\s+memiliki\s+(?:area|lokasi)\s+penyimpanan|"
                          r"kolom\s+lokasi\s+penyimpanan\s+kosong|"
                          r"(?:informasi\s+)?lokasi\s+penyimpanan\w*\s+(?:tidak|belum)\s+(?:muncul|ada|terisi)", target, re.I))


def _printed_storage_translation():
    return ("Baik saat menyimpan data maupun mencatat masuk gudang, lokasi penyimpanan "
            "tidak tercantum pada TAG yang pertama kali dicetak.")


def destination_override(source):
    """Parse the user-confirmed plant shorthand with complete source coverage.

    EC/EG/EH/EI are storage-code families in the supplied storage lookup. This
    exact grammar, not a bare code or a customer's default, establishes sense.
    Additional instructions, numbers, exceptions or negation do not match.
    """
    match = re.fullmatch(
        r"\s*(?P<mention>@All|__MENTION_\d+__)?\s*"
        r"(?P<customer>[\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z0-9 ._-]{0,39}?)"
        r"(?P<time>今晚|明晚|今天|明天)(?:包的|包裝的|包装的)"
        r"[，,\s]*(?:不論|不论|不管)(?:哪一站|哪站|哪一個站|哪一个站)"
        r"[，,\s]*都(?:幫忙|帮忙)\s*(?P<zone>E[CGHI]\d{2})\s*[。.!！]?\s*",
        str(source or ""), re.I,
    )
    if not match or re.search(r"不是|不要|除了|只有|改成|改為|改为", match.group("customer")):
        return None
    return match.groupdict()


def _destination_translation(binding):
    when = {"今晚": "malam ini", "明晚": "besok malam", "今天": "hari ini", "明天": "besok"}[binding["time"]]
    mention = (binding["mention"] + " ") if binding["mention"] else ""
    return (mention + "Untuk material " + binding["customer"].strip() + " yang dikemas " + when +
            ", setelah selesai, angkat dan pindahkan semua produk jadi ke area penyimpanan " +
            binding["zone"] + ", terlepas dari lokasi penyimpanan yang tercetak pada TAG.")


def build_relations(source):
    """Use sentence scopes and explicit objects, never a historical target."""
    relations = []
    handoff = handoff_question(source)
    if handoff:
        relations.append(dict(kind="erp_handoff_question", source_evidence=handoff["statement"],
            meaning_zh="資料仍停留在原文指定站別；詢問無法放行或忘記放行兩種可能，不是實體搬料或已確定原因。",
            required_target_meaning_id="Data tertahan di stasiun " + handoff["station"] +
                "; apakah tidak bisa di-release atau lupa di-release? Preserve alternatives as a question."))
    if _PENDING_INSPECTION.fullmatch(str(source or "")):
        relations.append(dict(kind="pending_release_inspection", source_evidence=str(source),
            meaning_zh="放行指上站生產資料放行；三位站號是檢驗站的主詞，不是數量。不驗未明說不能或不肯，不能補成拒絕或無法。",
            required_target_meaning_id="Data belum di-release. Stasiun [source code] tidak melakukan pemeriksaan; no invented inability or refusal."))
    if unresolved_source_ambiguities(source):
        relations.append(dict(kind="unresolved_packing_spelling", source_evidence=str(source).splitlines()[0],
            meaning_zh="料再包裝可能是重新包裝，也可能是料在包裝站的錯字，使用者尚未確認；不可當成已確認術語或固定再→在規則。",
            required_target_meaning_id="The packing location versus repeat action is unresolved. Do not silently establish a permanent correction or invent a rework instruction."))
    destination = destination_override(source)
    if destination:
        relations.append(_relation("storage_destination", str(source), **destination))
    for raw in re.split(r"[。.!?！？；;\n]+", str(source or "")):
        sentence = re.sub(r"\s+", "", raw)
        if not sentence:
            continue
        kinds = []
        system_zone = bool(re.search(_ZONE, sentence) and re.search(_SYSTEM, sentence))
        if re.search(r"存[檔档]|保存", sentence) and re.search(r"包[裝装]|入[庫库]|" + _ZONE, sentence):
            if not re.search(r"檔案|档案|文獻|文献|紙本|纸本", sentence):
                kinds.append("record_save")
        printed_storage = bool(re.search(_PRINT_SHORTHAND, sentence))
        if printed_storage:
            kinds.append("printed_storage")
        if system_zone and not re.search(_PHYSICAL, sentence):
            if re.search(r"(?:沒|没|無|无).{0,3}" + _ZONE + "|" + _ZONE + r".{0,8}(?:不見|不见|空白|消失|沒顯示|没显示|未顯示|未显示)", sentence):
                if not printed_storage:
                    kinds.append("storage_field")
            # Do not use a nearby storage field to reinterpret machine upkeep.
            if (re.search(r"手[動动](?:填寫|填写|更新|維護|维护)", sentence)
                    and not re.search(r"(?:機台|机台|設備|设备|電機|电机).{0,8}(?:維護|维护)", sentence)):
                kinds.append("storage_update")
        if re.search(r"(?:待|等待|等著|等着)(?:裝|装)(?:入)?木箱", sentence) and not re.search(_ASSEMBLY, sentence):
            kinds.append("pending_crate_packing")
        three_packing = (re.search(r"(?:開|开|運轉|运转)(?:三|3)(?:個|个)?(?:包裝|包装)?站", sentence)
                         and not re.search(_OTHER_STATION, sentence))
        if ((three_packing or re.search(r"人力|人員|人员|排班|優先|优先|生產|生产", sentence))
                and not re.search(r"新[設设建開开]|擴建|扩建|開幕|开幕", sentence)):
            for match in re.finditer(r"(?:開|开|運轉|运转)(" + _COUNT + r")(?:個|个)?(?:包裝|包装)?站", sentence):
                count = match.group(1)
                number = int(count) if count.isdigit() else _NUMBERS[count]
                kind = "packing_station_operation" if three_packing and number == 3 else "station_operation"
                relations.append(_relation(kind, sentence, count=number))
        relations.extend(_relation(kind, sentence) for kind in kinds)
    if re.search(r"入[庫库].{0,6}(?:目標|目标|計[劃画]量|計[畫划])", str(source or "")):
        for match in re.finditer(r"(?:幫忙|帮忙|協助|协助)入\s*(\d+(?:[.,]\d+)?)\s*[噸吨]", str(source)):
            relations.append(_relation("intake_quantity", match.group(), quantity=match.group(1)))
    return relations


def _relation(kind, evidence, **values):
    meaning, target = _MEANINGS[kind]
    return dict(kind=kind, source_evidence=evidence, meaning_zh=meaning,
                required_target_meaning_id=target, **values)


def _count_pattern(number):
    return str(number) + ("|" + _ID_NUMBERS[number] if number < len(_ID_NUMBERS) else "")


def _edits(source, target):
    """Only replace an existing wrong phrase when the source has one sense."""
    relations = build_relations(source)
    kinds = {r["kind"] for r in relations}
    # Whole-target phrase matching would swap independent clauses if the source
    # also explicitly describes physical maintenance/assembly or paper archives.
    mixed = bool(re.search(r"檔案|档案|紙本|纸本|" + _ASSEMBLY + "|" + _PHYSICAL +
                          r"|(?:機台|机台|設備|设备).{0,8}(?:維護|维护)", str(source or "")))
    if mixed:
        return []
    specs = []
    if "record_save" in kinds:
        specs.append(("record_save", r"\bpenyimpanan\s+arsip\b", "penyimpanan data"))
    if "storage_field" in kinds:
        specs.append(("storage_field", r"\b(?P<state>belum|tidak)\s+memiliki\s+(?:area|lokasi)\s+penyimpanan\b",
                      lambda m: m.group("state") + " menampilkan informasi lokasi penyimpanan"))
    if "storage_update" in kinds:
        specs.append(("storage_update", r"\b(?:melakukan|lakukan|melaksanakan|laksanakan)\s+pemeliharaan\s+(?:secara\s+)?manual\b",
                      lambda m: ("memperbarui" if m.group().lower().startswith(("melakukan", "melaksanakan")) else "perbarui") + " data lokasi penyimpanan secara manual"))
        specs.append(("storage_update", r"\bpemeliharaan\s+(?:secara\s+)?manual\b", "pembaruan data lokasi penyimpanan secara manual"))
    if "pending_crate_packing" in kinds:
        packing_object = "material" if re.search(r"原料|材料|半成品", str(source)) else "produk jadi"
        specs.append(("pending_crate_packing", r"\bpeti\s+kayu\s+yang\s+(?:masih\s+)?menunggu\s+(?:untuk\s+)?dipasang\b",
                      packing_object + " yang masih menunggu dikemas ke dalam peti kayu"))
    for relation in relations:
        if relation["kind"] in {"station_operation", "packing_station_operation"}:
            if relation["kind"] == "packing_station_operation":
                specs.append(("packing_station_operation",
                              r"\b(?P<verb>membuka|dibuka|buka|mengoperasikan|dioperasikan|operasikan|menjalankan|dijalankan|beroperasi)\s+(?P<object>(?:3|tiga)\s+stasiun)\b(?=\s*(?:[,.;!?]|$)|\s+(?:sampai|hingga|setiap|secara|lebih)\b)",
                              lambda m: {"membuka": "mengoperasikan", "dibuka": "dioperasikan", "buka": "operasikan"}.get(m.group("verb").lower(), m.group("verb")) + " " + m.group("object") + " packing"))
                specs.append(("packing_station_operation",
                              r"\b(?:3|tiga)\s+stasiun\b(?=\s+(?:akan\s+)?(?:terus\s+)?(?:dioperasikan|beroperasi|dijalankan)\b)",
                              lambda m: m.group() + " packing"))
            specs.append(("station_operation", r"\b(?P<verb>membuka|dibuka|buka)\s+(?P<object>(?:" + _count_pattern(relation["count"]) + r")\s+stasiun)\b",
                          lambda m: {"membuka": "mengoperasikan", "dibuka": "dioperasikan", "buka": "operasikan"}[m.group("verb").lower()] + " " + m.group("object")))
        elif relation["kind"] == "intake_quantity":
            quantity = re.escape(relation["quantity"]).replace(r"\.", "[.,]")
            specs.append(("intake_quantity", r"\b(?P<verb>input|menginput|masukkan|memasukkan)\s+(?:(?:ke|di)\s+gudang\s+)?(?P<amount>" + quantity + r"\s+ton)\b(?:\s+(?:ke|di)\s+gudang\b)?(?!\s+(?:ke|di|dalam|sebagai|pada)\b)",
                          lambda m: ("mencatat" if m.group("verb").lower().startswith("me") else "catat") + " pemasukan gudang sebanyak " + m.group("amount") + " dalam sistem"))
            specs.append(("intake_quantity", r"\bproses\s+pemasukan\s+(?P<amount>" + quantity + r"\s+ton)\s+ke\s+gudang\b",
                          lambda m: "pencatatan masuk gudang untuk " + m.group("amount") + " dalam sistem"))
    edits = []
    for kind, pattern, replacement in specs:
        matches = list(re.finditer(pattern, target, re.I))
        # Multiple mentions can have different referents; leave them to the
        # first-pass prompt. Preserve negation outside the phrase verbatim.
        if len(matches) != 1:
            continue
        match = matches[0]
        if any(match.start() < end and match.end() > start for start, end, _, _ in edits):
            continue
        value = replacement(match) if callable(replacement) else replacement
        if match.group()[0].isupper():
            value = value[0].upper() + value[1:]
        edits.append((match.start(), match.end(), value, kind))
    return edits


def canonicalize(source, candidate):
    result = str(candidate or "")
    if result:
        result = _canonicalize_handoff(source, result)
    if _printed_storage_wrong(source, result):
        return _printed_storage_translation()
    destination = destination_override(source)
    if result and destination and _destination_wrong(destination, result):
        # Only a fully parsed instruction can be rendered from its bound fields;
        # never rebuild a longer notice from partial matching or a remembered ID.
        return _destination_translation(destination)
    for start, end, value, _kind in sorted(_edits(source, result), reverse=True):
        result = result[:start] + value + result[end:]
    return result


def issues(source, candidate):
    # Positive evidence of a recognized wrong sense; synonyms are never rejected
    # merely because they are absent from a phrase allowlist.
    found = ["factory_workflow:" + kind + ":wrong_sense"
             for _, _, _, kind in _edits(source, str(candidate or ""))]
    found.extend(_handoff_issues(source, str(candidate or "")))
    found.extend(unresolved_source_ambiguities(source))
    if _printed_storage_wrong(source, str(candidate or "")):
        found.append("factory_workflow:printed_storage:wrong_sense")
    destination = destination_override(source)
    if destination and _destination_wrong(destination, str(candidate or "")):
        found.append("factory_workflow:storage_destination:wrong_sense")
    return list(dict.fromkeys(found))


def _destination_wrong(binding, target):
    # The screenshot's "bantu EC51" and "di stasiun mana pun" are positive
    # evidence of the two reported wrong senses. Other wordings stay advisory.
    zone = re.escape(binding["zone"])
    return bool(re.search(r"\b(?:bantu|membantu|dukung|mendukung)\s+" + zone + r"\b|\bdi\s+stasiun\s+mana\s*pun\b", target, re.I))


def separate_name_boundaries(candidate, names):
    """Space only outside complete known names, never inside mixed-script IDs."""
    names = sorted({str(n) for n in names if n}, key=lambda n: (-len(n), n))
    if not names or not candidate:
        return candidate
    atomic = list(re.finditer("|".join(map(re.escape, names)), candidate))
    urls = [m.span() for m in re.finditer(r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]+", candidate)]
    positions = set()
    for m in atomic:
        if any(m.start() < end and m.end() > start for start, end in urls):
            continue
        if re.match("[" + _CJK + "]", m.group()) and m.start() and re.match(r"[A-Za-z]", candidate[m.start()-1]):
            positions.add(m.start())
        if re.search("[" + _CJK + "]$", m.group()) and m.end() < len(candidate) and re.match(r"[A-Za-z]", candidate[m.end()]):
            positions.add(m.end())
    for position in sorted(positions, reverse=True):
        candidate = candidate[:position] + " " + candidate[position:]
    return candidate
