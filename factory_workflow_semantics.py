"""Source-bound shop-floor senses learned from the September 16 screenshots.

No I/O, generation, delivery decisions or exact-sentence translation table.
Diagnostics describe positive evidence of a wrong sense, not a compulsory
target wording. Ambiguous/mixed senses receive prompt advice, not a rewrite.
"""
from __future__ import annotations

import re

BUILD_ID = "2026-09-16.2-storage-packing-senses"
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
    "storage_update": ("此處維護是手動填寫或更新已知的儲區資料，保留知道儲區才操作的條件。", "mengisi/memperbarui data lokasi penyimpanan secara manual, not mechanical maintenance; preserve the source condition"),
    "pending_crate_packing": ("待裝木箱是等待裝入木箱的包裝工作，不是安裝木箱本身。", "material yang menunggu dikemas ke dalam peti kayu, not peti kayu yang menunggu dipasang"),
    "station_operation": ("人力安排開幾站指運轉現有工作站，不是設立新站。", "mengoperasikan/menjalankan the stated number of stasiun; keep the staffing, priority and period"),
    "intake_quantity": ("同段入庫計畫中的入幾噸是入庫量，不是單獨輸入一個數字。", "pemasukan gudang in ton; keep the quantity and do not invent a station or storage code"),
    "storage_destination": ("使用者確認：此包裝簡語指定本次成品實體吊入的儲區，無論 TAG 印哪個儲區都依當次指定；不是支援機台或永久更改客戶預設。", "angkat dan pindahkan produk jadi ke area penyimpanan specified in THIS source, regardless of the storage location printed on TAG; customer owns the material"),
}


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
        if system_zone and not re.search(_PHYSICAL, sentence):
            if re.search(r"(?:沒|没|無|无).{0,3}" + _ZONE + "|" + _ZONE + r".{0,8}(?:不見|不见|空白|消失|沒顯示|没显示|未顯示|未显示)", sentence):
                kinds.append("storage_field")
            # Do not use a nearby storage field to reinterpret machine upkeep.
            if (re.search(r"手[動动](?:填寫|填写|更新|維護|维护)", sentence)
                    and not re.search(r"(?:機台|机台|設備|设备|電機|电机).{0,8}(?:維護|维护)", sentence)):
                kinds.append("storage_update")
        if re.search(r"(?:待|等待|等著|等着)(?:裝|装)(?:入)?木箱", sentence) and not re.search(_ASSEMBLY, sentence):
            kinds.append("pending_crate_packing")
        if (re.search(r"人力|人員|人员|排班|優先|优先|生產|生产", sentence)
                and not re.search(r"新[設设建開开]|擴建|扩建|開幕|开幕", sentence)):
            for match in re.finditer(r"(?:開|开|運轉|运转)(" + _COUNT + r")(?:個|个)?站", sentence):
                count = match.group(1)
                relations.append(_relation("station_operation", sentence,
                                           count=int(count) if count.isdigit() else _NUMBERS[count]))
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
        specs.append(("pending_crate_packing", r"\bpeti\s+kayu\s+yang\s+(?:masih\s+)?menunggu\s+(?:untuk\s+)?dipasang\b",
                      "material yang masih menunggu dikemas ke dalam peti kayu"))
    for relation in relations:
        if relation["kind"] == "station_operation":
            specs.append(("station_operation", r"\b(?P<verb>membuka|dibuka|buka)\s+(?P<object>(?:" + _count_pattern(relation["count"]) + r")\s+stasiun)\b",
                          lambda m: {"membuka": "mengoperasikan", "dibuka": "dioperasikan", "buka": "operasikan"}[m.group("verb").lower()] + " " + m.group("object")))
        elif relation["kind"] == "intake_quantity":
            quantity = re.escape(relation["quantity"]).replace(r"\.", "[.,]")
            specs.append(("intake_quantity", r"\binput\s+(?P<amount>" + quantity + r"\s+ton)\b(?!\s+(?:ke|di|dalam|sebagai)\b)",
                          lambda m: "proses pemasukan " + m.group("amount") + " ke gudang"))
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
