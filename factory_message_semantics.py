"""Bidirectional source-relation semantics for factory chat translation.

Glossary enforcement can prove that isolated words and numbers are present, but
it cannot prove that the target keeps their source roles.  This module extracts
small, compositional source frames for relations that are especially dangerous
on the shop floor: equipment-to-reading comparisons, reporting with a leader's
ID, movement-to-a-location followed by inspection, short attendance/departure
events whose omitted human actor must not be replaced by a vehicle, and
machine-guard safety instructions whose omitted Chinese subjects must remain
attached to the guard.

The rules are not sentence replacements.  Values, units, aspect, destination,
objects, production-selection criteria and mentions are read from the current
source.  A direct translation is produced only when every meaningful source
token belongs to a supported slot; otherwise the same frame becomes a provider
prompt and a deterministic completeness check for the ordinary translation
path.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping


FACTORY_MESSAGE_SEMANTICS_API_VERSION = 2
FACTORY_MESSAGE_SEMANTICS_BUILD_ID = "2026-08-26.2-short-event-role-integrity"

_NUMBER = r"\d+(?:[.,]\d+)?"
_MENTION_RE = re.compile(
    r"(?:"
    r"@[Aa][Ll][Ll](?![A-Za-z0-9_.-])"
    r"|@[\u4e00-\u9fff\u3040-\u30ff]+"
    r"(?:\s*[（(][^）)\r\n]{1,48}[）)])?"
    r"(?:\s+(?-i:[A-Z])[A-Za-z0-9_.-]{1,31}){0,2}"
    r"|@[^\s,，。!?！？:：;；]{1,48}"
    r"|__MENTION_\d+__"
    r")",
    re.I,
)

_MONITOR_ID = (
    "layar monitor",
    "monitor display",
    "display monitor",
    "monitor",
)
_HOIST_SCALE_ID = (
    "timbangan gantung elektronik",
    "timbangan elektronik pada crane",
    "timbangan elektronik crane",
    "timbangan katrol",
    "timbangan gantung",
    "timbangan crane",
    "timbangan derek",
    "timbangan tian che",
    "timbangan hoist",
)
_LEADER_ID = (
    "ketu kelas",       # common missing-a typo in shop-floor chat
    "ketua kelas",      # factory-context misuse for the shift leader
    "ketua shift",
    "ketua regu",
    "kepala shift",
    "kepala regu",
)
_REPORT_ID = (
    "lapor",
    "laporan",
    "melapor",
    "melaporkan",
    "laporkan",
)

# Shop-floor chat often spells ``shift`` phonetically as sip/sif/shif.  ``sip``
# is also an ordinary acknowledgement (OK), so it must never be normalized as a
# shift by itself.  A following shift period plus a real clause is the required
# disambiguating evidence.  This keeps "Sip, terima kasih" conversational while
# making "sip pagi tidak ..." a morning-shift production claim.
_SHIFT_ALIAS_ID_RE = re.compile(
    r"(?<![a-z])(?P<shift>shift|shif|sif|sip)\s+"
    r"(?P<period>pagi|siang|sore|malam)(?![a-z])",
    re.I,
)
_SHIFT_CLAUSE_EVIDENCE_ID_RE = re.compile(
    r"(?<![a-z])(?:tidak|tida|tdk|tdak|gak|ga|nggak|ngga|belum|sudah|akan|"
    r"masih|jangan|mesin|material|barang|produksi|operator|cat|pengecatan|"
    r"rusak|selesai|masuk|keluar|kerja|bekerja|melakukan|memberi|mengasih|"
    r"mengecat|menyemprot)(?![a-z])",
    re.I,
)
_SHIFT_PERIOD_ZH = {
    "pagi": "早班",
    "siang": "中班",
    "sore": "小夜班",
    "malam": "夜班",
}
_NEGATIVE_ID_RE = re.compile(
    r"(?<![a-z])(?P<negative>tidak|tida|tdk|tdak|gak|ga|nggak|ngga|belum)(?![a-z])",
    re.I,
)
_PAINT_APPLICATION_ID_RE = re.compile(
    r"(?<![a-z])(?:"
    r"(?:mengasih|memberi|memberikan|kasih)\s+(?:warna\s+cat|cat\s+warna)"
    r"|(?:melakukan\s+)?pengecatan(?:\s+semprot)?"
    r"|(?:melakukan\s+)?penyemprotan\s+cat"
    r"|mengecat(?:\s+dengan\s+semprotan)?"
    r"|menyemprot\s+cat"
    r"|semprot\s+cat"
    r")(?![a-z])",
    re.I,
)
_NEGATED_PAINT_ZH_RE = re.compile(
    r"(?:沒有|没有|沒(?:有|做)?|没(?:有|做)?|未做|尚未|還沒|还没|未執行|未执行|未進行|未进行)"
    r".{0,10}(?:噴漆|喷漆|塗裝|涂装)"
    r"|(?:噴漆|喷漆|塗裝|涂装)(?:作業|作业)?.{0,10}"
    r"(?:沒有做|没有做|沒做|没做|未做|尚未執行|尚未执行|未執行|未执行|尚未完成)",
    re.I,
)

_EQUIPMENT_CODE_ID_RE = re.compile(
    r"(?<![a-z0-9])(?:i\d{1,2}|e\d{1,2}|bf\d+|ap|pm\d+|ut|k\d+)(?![a-z0-9])",
    re.I,
)
_EQUIPMENT_FAILURE_ID_RE = re.compile(
    r"(?<![a-z])(?:rusak|tidak\s+berfungsi|tidak\s+bisa\s+dipakai)(?![a-z])",
    re.I,
)

_ZH_MOTION = (
    "過去", "过去", "去那邊", "去那边", "到那邊", "到那边",
    "去那裡", "去那里", "到那裡", "到那里", "去現場", "去现场",
    "到現場", "到现场",
)
_ZH_INSPECTION = (
    "了解看看", "瞭解看看", "了解一下", "瞭解一下", "確認看看", "确认看看",
    "確認一下", "确认一下", "檢查看看", "检查看看", "檢查一下", "检查一下",
    "查看一下", "看看", "看一下", "查看", "檢查", "检查", "確認", "确认",
    "了解", "瞭解",
)

# Machine guards are engineering controls, not the machine itself.  Chinese
# shop-floor messages often mention the guard once and then omit it in a later
# clause (e.g. 設備護網要蓋上，剛被提醒多台設備沒蓋好).  The later 設備 is a
# location/owner relation: guards on several machines were not restored.  A
# fluent literal translation such as ``beberapa mesin tidak ditutup`` changes
# the safety subject and is therefore rejected by this frame.
_ZH_MACHINE_GUARD_TERMS = (
    "設備護網", "设备护网", "機台護網", "机台护网", "機器護網", "机器护网",
    "安全護網", "安全护网", "設備護罩", "设备护罩", "機台護罩", "机台护罩",
    "機器護罩", "机器护罩", "防護罩", "防护罩", "護網", "护网", "護罩", "护罩",
    "護蓋", "护盖",
)
_ZH_GUARD_CLOSE_RE = re.compile(
    r"(?:蓋上|盖上|蓋好|盖好|蓋回|盖回|關上|关上|關好|关好|關回|关回|"
    r"裝上|装上|裝好|装好|裝回|装回|復位|复位|回復原位|回复原位|恢復原位|恢复原位)",
    re.I,
)
_ZH_GUARD_NOT_CLOSED_RE = re.compile(
    r"(?:沒|没|沒有|没有|未|尚未)(?:有)?(?:蓋|盖|關|关|裝|装|復位|复位)"
    r"(?:上|好|回|回去|到位)?",
    re.I,
)
_ZH_GUARD_REMINDER_RE = re.compile(
    r"(?:幫忙|帮忙|請|请|麻煩|麻烦|協助|协助|再)?(?:大家|同仁|人員|人员)?"
    r"(?:幫忙|帮忙)?提醒|提醒(?:一下|大家|同仁|人員|人员)",
    re.I,
)
_ZH_GUARD_RECENT_REMINDER_RE = re.compile(
    r"(?:剛(?:剛|才)?|刚(?:刚|才)?).{0,8}(?:被提醒|有人提醒|收到提醒)",
    re.I,
)
_ZH_GUARD_EQUIPMENT_SCOPE_RE = re.compile(
    r"(?P<count>多|數|数|好幾|好几|幾|几|\d+|[一二兩两三四五六七八九十]+)"
    r"台(?:設備|设备|機台|机台|機器|机器)",
    re.I,
)
_ZH_DISCIPLINE_LAX_RE = re.compile(
    r"(?:注意|維持|维持|保持|遵守)?(?:工作)?紀律.{0,8}"
    r"(?:不要|不可|不能|別|别)?(?:太)?(?:鬆懈|松懈|散漫|懈怠)",
    re.I,
)
_ZH_ATTENDANCE_EARLY_LEAVE_RE = re.compile(
    r"(?:點名|点名)(?P<modality>不會|不会|不要|不可|不能|別|别)"
    r"(?:太)?早(?:離開|离开|走|下班)",
    re.I,
)

# A Chinese serial-verb message such as ``點名開車走了`` has an omitted human
# actor: somebody attends the roll call and then leaves by driving.  ``開車``
# is a manner/action predicate; 車 is its object, not the actor of ``走``.  A
# general model can produce the fluent but role-reversed ``kendaraan berangkat``
# and can also hallucinate ``lebih dulu``.  This frame records the event roles,
# temporal relation, modality, explicitly grounded priority and source emoji.
# It deliberately does not match ``車輛開走了`` because that source really does
# make the vehicle the departing subject.
_ZH_ATTENDANCE_EVENT_RE = re.compile(
    r"(?:點完名|点完名|點名(?:完成|結束|结束|完)?|点名(?:完成|結束|结束|完)?)",
    re.I,
)
_ZH_PERSON_VEHICLE_DEPARTURE_RE = re.compile(
    r"(?P<actor>我們|我们|你們|你们|他們|他们|她們|她们|我|你|他|她)?"
    r"(?P<connector>就|再|直接)?"
    r"(?P<modality>不要|別|别|不能|不可|準備|准备|將要|将要|會|会|要)?"
    r"(?P<priority>先)?"
    r"(?P<drive>開車|开车|駕車|驾车)"
    r"(?P<connector_after>就|再|直接)?"
    r"(?P<priority_after>先)?"
    r"(?P<departure>離開|离开|回去|回家|出發|出发|離場|离场|走)"
    r"(?P<aspect>了|啦|囉|啰|喽|喔|哦)?",
    re.I,
)
_ZH_EVENT_ACTOR_ID = {
    "我": "saya",
    "我們": "kami",
    "我们": "kami",
    "你": "Anda",
    "你們": "kalian",
    "你们": "kalian",
    "他": "dia",
    "她": "dia",
    "他們": "mereka",
    "他们": "mereka",
    "她們": "mereka",
    "她们": "mereka",
}
_ZH_EVENT_AFTER_RE = re.compile(
    r"^(?:之後|之后|以後|以后|後|后)?(?:就|再|然後|然后|接著|接着)?$",
    re.I,
)
_ZH_EVENT_BEFORE_RE = re.compile(r"^(?:之前|以前|前)(?:就|再)?$", re.I)
_ZH_EVENT_DURING_RE = re.compile(r"^(?:時|时|期間|期间)(?:就|再)?$", re.I)
_EMOJI_BASE = (
    r"[\u2600-\u27BF\U0001F000-\U0001FAFF]"
)
_EMOJI_CLUSTER_RE = re.compile(
    r"(?:[\U0001F1E6-\U0001F1FF]{2}|[#*0-9]\ufe0f?\u20e3|"
    + _EMOJI_BASE
    + r")"
    r"(?:[\ufe0e\ufe0f\U0001F3FB-\U0001F3FF]|\u200d"
    + _EMOJI_BASE
    + r"[\ufe0e\ufe0f\U0001F3FB-\U0001F3FF]*)*"
)

# 「放」is highly polysemous in the factory group.  A bare request such as
# 「這把麻煩他們放一下」does not describe moving the physical bundle: 把 is the
# bundle reference whose ERP record must be released to the next station.  This
# relation must be decided from syntax before a provider sees the sentence; a
# prompt-only rule cannot prevent stale TM/provider output from reverting to the
# everyday meaning "put/place".
_ZH_RELEASE_OBJECT_RE = re.compile(
    r"(?P<deictic>這|这|那|該|该)?"
    r"(?P<count>\d{1,3}|[零〇一二兩两三四五六七八九十]{1,3})?"
    r"(?P<object>把|捆|批|(?:張|张|筆|笔|個|个)?(?:工單|工单|單|单|資料|资料|數據|数据))"
    r"(?=$|[\s,，。.!！?？:：;；()（）\[\]{}]|"
    r"(?:麻煩|麻烦|拜託|拜托|請|请|幫忙|帮忙|幫|帮|協助|协助|叫|讓|让|"
    r"都|全都|先|再|要|需|已經|已经|已|放))",
    re.I,
)
_ZH_RELEASE_REQUEST_RE = re.compile(
    r"(?:麻煩|麻烦|拜託|拜托|請|请|幫忙|帮忙|幫|帮|協助|协助|叫|讓|让)",
    re.I,
)
_ZH_RELEASE_COMPLETED_RE = re.compile(
    r"(?:已經|已经|已|都|全都)?(?:放行|放)(?:完成|好了?|完(?:了)?|了)",
    re.I,
)
_ZH_RELEASE_PHYSICAL_RE = re.compile(
    r"(?:放不下|放不進|放不进|放得下|放得進|放得进|能放就放|不夠放|不够放|"
    r"放在|放到|放進|放进|放入|放下|放回|擺在|摆在|擺到|摆到|"
    r"儲格|储格|儲位|储位|置料|位置|地方|地上|旁邊|旁边|上面|下面|"
    r"架上|桌上|這裡|这里|那裡|那里|照片|圖片|图片|空間|空间)",
    re.I,
)
_ZH_RELEASE_PHYSICAL_OBJECT_RE = re.compile(
    r"(?:工具|刀|剪刀|箱子|紙箱|纸箱|衣服|鞋子|物品|東西|东西|零件)"
    r".{0,10}(?:放|擺|摆)|(?:放|擺|摆).{0,10}"
    r"(?:工具|刀|剪刀|箱子|紙箱|纸箱|衣服|鞋子|物品|東西|东西|零件)",
    re.I,
)
_ZH_RELEASE_QC_RE = re.compile(
    r"(?:品保|品管|品質|质量|QC|檢驗|检验).{0,12}(?:放行|放了|已放)|"
    r"(?:放行|放了|已放).{0,12}(?:品保|品管|品質|质量|QC|檢驗|检验)",
    re.I,
)

# Production-planning notices often combine two linked claims: a backlog caused
# by missed shipping in a recent period, followed by two *alternative* priority
# selectors from the production system.  A literal model can turn the first
# noun phrase into ``material tunda batang kecil polishing`` and collapse the
# two selectors into one material that must satisfy both conditions.  Parse the
# relations before any provider call so recognized notices are both natural and
# immediate, while paraphrases/extra clauses still go through the ordinary
# source-grounded provider path instead of being silently dropped.
_ZH_PROCESS_TO_ID = {
    "拋光": "polishing",
    "抛光": "polishing",
    "研磨": "grinding",
    "削皮": "peeling",
    "冷抽": "cold drawing",
    "矯直": "straightening",
    "矫直": "straightening",
    "酸洗": "pickling",
}
_ZH_SMALL_BAR_TERMS = (
    "小尺寸棒材", "小尺寸棒料", "小徑棒材", "小径棒材", "小尺寸材料", "小棒",
)
_ZH_BACKLOG_PERIOD_RE = re.compile(
    r"(?P<evidence>(?:這|这|近|過去|过去|最近)"
    r"(?P<count>\d{1,2}|[零〇一二兩两三四五六七八九十]{1,3})(?:個|个)?月)",
    re.I,
)
_ZH_SHIPPING_DELAY_TERMS = (
    "來不及出貨", "来不及出货", "未能如期出貨", "未能如期出货",
    "無法如期出貨", "无法如期出货", "沒能如期出貨", "没能如期出货",
    "無法按期出貨", "无法按期出货", "未能按期出貨", "未能按期出货",
)
_ZH_DEFERRED_MATERIAL_TERMS = (
    "遞延材料", "递延材料", "遞延料", "递延料", "延遲材料", "延迟材料",
    "延遲料", "延迟料", "積欠材料", "积欠材料", "積欠料", "积欠料",
)
_ZH_BACKLOG_VOLUME_TERMS = ("非常多", "相當多", "相当多", "很多", "不少", "大量")
_ZH_SYSTEM_TERMS = ("系統上", "系统上", "系統中", "系统中", "系統內", "系统内", "系統", "系统")
_ZH_BLUE_MARK_TERMS = (
    "藍色底", "蓝色底", "藍底", "蓝底", "藍色標示", "蓝色标示",
    "藍色標記", "蓝色标记", "藍色底色", "蓝色底色",
)
_ZH_NOTE_TERMS = ("備註", "备注", "註記", "注记", "標示", "标示", "標記", "标记")
_ZH_PRIORITY_ACTION_TERMS = (
    "優先生產", "优先生产", "優先排產", "优先排产", "先排產", "先排产", "先生產", "先生产",
)
_ZH_MONTH_TOKEN = r"(?:1[0-2]|0?[1-9]|十二|十一|十|[一二三四五六七八九])"
_ZH_DELIVERY_MONTH_RE = re.compile(
    r"(?P<evidence>(?:交期|出貨月份|出货月份|交貨月份|交货月份)"
    r"(?:為|为|是)?(?P<months>" + _ZH_MONTH_TOKEN + r"(?:月)?"
    r"(?:(?:、|,|，|/|及|和|跟|與|与)" + _ZH_MONTH_TOKEN + r"(?:月)?)*))",
    re.I,
)
_ID_MONTH_NAMES = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
    5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
    9: "September", 10: "Oktober", 11: "November", 12: "Desember",
}

_ZH_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "两": 2,
    "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_ID_SMALL_NUMBERS = {
    0: "nol", 1: "satu", 2: "dua", 3: "tiga", 4: "empat", 5: "lima",
    6: "enam", 7: "tujuh", 8: "delapan", 9: "sembilan", 10: "sepuluh",
    11: "sebelas",
}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    # Keep decimal commas/points between digits; remove the same glyphs when
    # they are sentence punctuation.  Indonesian operators commonly write
    # measurements such as ``995,5 kg``.
    text = re.sub(r"(?<!\d)[。．.,，]|[。．.,，](?!\d)", " ", text)
    text = re.sub(r"[!！?？:：;；()（）\[\]{}]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_indonesian_factory_colloquialisms(source: Any) -> tuple[str, int]:
    """Normalize context-dependent shop-floor Indonesian without an API call.

    The important distinction is structural, not lexical: ``sip`` means OK in
    ordinary chat, but ``sip/sif/shif + a shift period + a predicate`` denotes a
    work shift.  Paint-application slang is canonicalized only when it belongs
    to that shift claim, so unrelated messages about choosing or supplying a
    paint colour are not rewritten as production work.
    """
    value = str(source or "")
    if not value:
        return value, 0
    replacements = 0

    def _normalize_shift(match: re.Match[str]) -> str:
        nonlocal replacements
        raw_shift = match.group("shift")
        period = match.group("period")
        # Do not reinterpret a bare acknowledgement such as ``Sip pagi!``.  A
        # predicate/negation in the same clause is mandatory evidence.
        tail = value[match.end():]
        same_clause = re.split(r"[\n.!！?？;；]", tail, maxsplit=1)[0][:160]
        if raw_shift.casefold() != "shift" and not _SHIFT_CLAUSE_EVIDENCE_ID_RE.search(same_clause):
            return match.group(0)
        canonical = f"shift {period.casefold()}"
        if match.group(0).casefold() != canonical:
            replacements += 1
        return canonical

    value = _SHIFT_ALIAS_ID_RE.sub(_normalize_shift, value)
    value, typo_count = re.subn(
        r"(?<![a-z])tida(?![a-z])", "tidak", value, flags=re.I
    )
    replacements += typo_count

    shift_match = _SHIFT_ALIAS_ID_RE.search(value)
    negative_match = _NEGATIVE_ID_RE.search(value)
    paint_match = _PAINT_APPLICATION_ID_RE.search(value)
    if (
        shift_match
        and negative_match
        and paint_match
        and shift_match.end() <= negative_match.start() <= paint_match.start()
        and paint_match.start() - shift_match.end() <= 160
    ):
        canonical_paint = "melakukan pengecatan semprot"
        if paint_match.group(0).casefold() != canonical_paint:
            value = (
                value[:paint_match.start()]
                + canonical_paint
                + value[paint_match.end():]
            )
            replacements += 1
    return value, replacements


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _norm(value))


def _lang_family(value: Any) -> str:
    """Collapse locale/provider aliases without weakening direction checks."""
    lang = _norm(value).replace("_", "-")
    if lang in {"zh", "zh-tw", "zh-cn", "zh-hant", "zh-hans", "chinese"}:
        return "zh"
    if lang in {"id", "id-id", "ind", "indonesian", "bahasa indonesia"}:
        return "id"
    return lang.split("-", 1)[0]


def _has_phrase(text: str, phrases: Iterable[str]) -> bool:
    return any(
        re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", text, re.I)
        for phrase in phrases
    )


def _first_phrase(text: str, phrases: Iterable[str]) -> str:
    for phrase in sorted(set(phrases), key=len, reverse=True):
        if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", text, re.I):
            return phrase
    return ""


def _extract_weight_after(text: str, phrases: Iterable[str]) -> str:
    phrase_pattern = "|".join(re.escape(item) for item in sorted(set(phrases), key=len, reverse=True))
    # Prefer the explicit ``995 kg di <device>`` attachment.  This must run
    # before the post-device form: once chat punctuation is normalized away,
    # the following device's reading can otherwise look adjacent to the first
    # device (``995 kg di monitor, 989 kg di timbangan``).
    match = re.search(
        rf"(?P<value>{_NUMBER})\s*(?:kg|kilogram)\s*"
        rf"(?:pada|di)\s*(?:{phrase_pattern})\b",
        text,
        flags=re.I,
    )
    if match:
        return match.group("value")
    match = re.search(
        rf"(?:di\s+)?(?:{phrase_pattern})\s*"
        rf"(?:menunjukkan|menampilkan|tertera|tertulis|adalah|sebesar|=)?\s*"
        rf"(?P<value>{_NUMBER})\s*(?:kg|kilogram)\b",
        text,
        flags=re.I,
    )
    return match.group("value") if match else ""


def _extract_difference(text: str) -> str:
    patterns = (
        rf"(?:selisih|beda|berbeda)(?:nya|\s+sebesar)?\s*(?P<value>{_NUMBER})\s*(?:kg|kilogram)\b",
        rf"(?P<value>{_NUMBER})\s*(?:kg|kilogram)\s+(?:selisih|beda)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group("value")
    return ""


def _strip_id_supported_tokens(
    source: str, supported_numbers: Iterable[str] = ()
) -> str:
    """Return Indonesian content not represented by the deterministic frame."""
    value = _norm(_MENTION_RE.sub("", str(source or "")))
    phrases = (
        set(_MONITOR_ID)
        | set(_HOIST_SCALE_ID)
        | set(_LEADER_ID)
        | set(_REPORT_ID)
        | {
            "menunjukkan", "menampilkan", "tertera", "tertulis", "adalah",
            "sebesar", "selisihnya", "selisih", "berbeda", "bedanya", "beda",
            "dibandingkan", "dibanding", "antara", "sedangkan", "dengan", "dan",
            "menggunakan", "gunakan", "memakai", "pakai", "sudah", "telah",
            "beratnya", "berat", "nilainya", "nilai", "hasil", "ada",
            "kilogram", "kg", "saya", "aku", "pada", "dari", "di", "id", "nya",
        }
    )
    for phrase in sorted(phrases, key=len, reverse=True):
        value = re.sub(
            r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])",
            " ",
            value,
            flags=re.I,
        )
    # Remove only numeric occurrences already assigned to a source slot.  A
    # blanket numeric deletion could hide an unrelated code/value and make the
    # direct route silently drop it.
    for number in supported_numbers:
        number = str(number or "").strip()
        if number:
            value = re.sub(
                r"(?<!\d)" + re.escape(number) + r"(?!\d)",
                " ",
                value,
                count=1,
            )
    value = re.sub(r"[=/+\-]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _claim(frame: dict, claim_id: str, source_evidence: str, meaning: str, target: str) -> None:
    frame["claims"].append({
        "claim_id": claim_id,
        "source_evidence": source_evidence,
        "meaning": meaning,
        "required_target": target,
    })


def _base_frame(source: str, src_lang: str, tgt_lang: str) -> dict:
    return {
        "active": False,
        "complete": False,
        "kind": "",
        "source": str(source or ""),
        "src_lang": _lang_family(src_lang),
        "tgt_lang": _lang_family(tgt_lang),
        "claims": [],
        "slots": {},
        "mentions": _MENTION_RE.findall(str(source or "")),
        "unparsed": "",
    }


def _build_id_zh_frame(source: str, frame: dict) -> dict:
    text = _norm(source)
    equipment_codes = list(dict.fromkeys(
        match.group(0).upper() for match in _EQUIPMENT_CODE_ID_RE.finditer(text)
    ))
    equipment_failure = _EQUIPMENT_FAILURE_ID_RE.search(text)
    if equipment_codes and equipment_failure:
        unparsed = _MENTION_RE.sub(" ", text)
        unparsed = _EQUIPMENT_CODE_ID_RE.sub(" ", unparsed)
        unparsed = _EQUIPMENT_FAILURE_ID_RE.sub(" ", unparsed)
        unparsed = re.sub(
            r"(?<![a-z])(?:mesin|machine|unit|dan|serta)(?![a-z])|[&/+\-]",
            " ",
            unparsed,
            flags=re.I,
        )
        unparsed = re.sub(
            r"[\s,，。.!！?？:：;；()（）\[\]{}]+", " ", unparsed
        ).strip()
        frame["kind"] = "id_zh_equipment_code_failure"
        frame["slots"].update({
            "equipment_codes": equipment_codes,
            "failure_term": equipment_failure.group(0).casefold(),
        })
        frame["unparsed"] = unparsed
        _claim(
            frame,
            "equipment_identity",
            ", ".join(equipment_codes),
            "I/E/BF/PM/K 等代碼在本廠是機台或站別識別碼",
            "、".join(equipment_codes) + " 機台",
        )
        _claim(
            frame,
            "equipment_failure",
            equipment_failure.group(0),
            "機台功能故障，不是材料或表面損傷",
            "故障",
        )
        frame["active"] = True
        frame["complete"] = not unparsed
        return frame

    shift_match = _SHIFT_ALIAS_ID_RE.search(text)
    shift_period = shift_match.group("period").casefold() if shift_match else ""
    negative_match = _NEGATIVE_ID_RE.search(text)
    paint_match = _PAINT_APPLICATION_ID_RE.search(text)
    shift_paint_claim = bool(
        shift_match
        and negative_match
        and paint_match
        and shift_match.end() <= negative_match.start() <= paint_match.start()
        and paint_match.start() - shift_match.end() <= 160
    )
    if shift_paint_claim:
        frame["kind"] = "id_zh_shift_process_status"
        frame["slots"].update({
            "shift_alias": shift_match.group("shift").casefold(),
            "shift_period": shift_period,
            "shift_target": _SHIFT_PERIOD_ZH[shift_period],
            "negative_term": negative_match.group("negative").casefold(),
            "completion": "not_yet" if negative_match.group("negative").casefold() == "belum" else "not_done",
            "process": "spray_painting",
            "process_source": paint_match.group(0),
        })
        _claim(
            frame,
            "shift_actor",
            shift_match.group(0),
            f"{_SHIFT_PERIOD_ZH[shift_period]}是執行者／責任班別，不是問候語",
            _SHIFT_PERIOD_ZH[shift_period],
        )
        _claim(
            frame,
            "process_negation",
            negative_match.group(0),
            "製程沒有執行；否定不能遺失或翻成缺少供應",
            "沒有",
        )
        _claim(
            frame,
            "spray_painting_process",
            paint_match.group(0),
            "現場噴漆／塗裝作業，不是提供油漆顏色",
            "噴漆",
        )
        supported_spans = sorted(
            (shift_match.span(), negative_match.span(), paint_match.span()),
            reverse=True,
        )
        unparsed = text
        for start, end in supported_spans:
            unparsed = unparsed[:start] + " " + unparsed[end:]
        frame["unparsed"] = re.sub(r"[\s,，。.!！?？:：;；()（）\[\]{}]+", " ", unparsed).strip()
        frame["active"] = True
        frame["complete"] = not frame["unparsed"]
        return frame

    monitor_term = _first_phrase(text, _MONITOR_ID)
    scale_term = _first_phrase(text, _HOIST_SCALE_ID)
    monitor_weight = _extract_weight_after(text, _MONITOR_ID)
    scale_weight = _extract_weight_after(text, _HOIST_SCALE_ID)
    difference = _extract_difference(text)
    report_term = _first_phrase(text, _REPORT_ID)
    leader_term = _first_phrase(text, _LEADER_ID)
    first_person = bool(re.search(r"(?<![a-z])saya(?![a-z])", text, re.I))
    id_relation = bool(
        leader_term
        and re.search(
            r"\bid\b.{0,18}(?:ketu(?:a)?\s+kelas|ketua\s+(?:shift|regu)|kepala\s+shift|kepala\s+regu)"
            r"|(?:ketu(?:a)?\s+kelas|ketua\s+(?:shift|regu)|kepala\s+shift|kepala\s+regu).{0,18}\bid\b",
            text,
            flags=re.I,
        )
    )
    report_completed = bool(
        report_term
        and re.search(r"\b(?:sudah|telah)\b.{0,18}\b(?:lapor|melapor|melaporkan|laporkan)\b", text, re.I)
    )
    weight_context = bool(scale_term and (monitor_term or re.search(r"\b(?:kg|kilogram)\b", text)))

    if not weight_context:
        return frame

    frame["kind"] = "id_zh_weight_display_relation"
    frame["slots"].update({
        "monitor_term": monitor_term,
        "scale_term": scale_term,
        "monitor_weight": monitor_weight,
        "scale_weight": scale_weight,
        "difference": difference,
        "report_term": report_term,
        "first_person": first_person,
        "leader_term": leader_term,
        "leader_id_relation": id_relation,
        "report_completed": report_completed,
    })
    frame["unparsed"] = _strip_id_supported_tokens(
        source, (monitor_weight, scale_weight, difference)
    )

    if monitor_term:
        _claim(frame, "monitor_display", monitor_term, "螢幕顯示的重量", "螢幕顯示")
    if scale_term:
        _claim(
            frame,
            "overhead_crane_scale",
            scale_term,
            "安裝於天車的電子磅秤；不是字面滑輪秤",
            "天車電子磅秤",
        )
    if monitor_weight:
        _claim(frame, "monitor_weight", monitor_weight + " kg", "螢幕重量讀值", monitor_weight + " 公斤")
    if scale_weight:
        _claim(frame, "scale_weight", scale_weight + " kg", "天車電子磅秤讀值", scale_weight + " 公斤")
    if difference:
        _claim(frame, "weight_difference", difference + " kg", "兩個讀值的差值", "相差 " + difference + " 公斤")
    if report_term:
        _claim(frame, "report_action", report_term, "說話者進行回報", "我回報")
    if id_relation:
        _claim(frame, "leader_id", "ID " + leader_term, "使用班長的 ID 回報", "用班長的 ID 回報")

    comparison_complete = bool(
        monitor_term and scale_term and (difference or (monitor_weight and scale_weight))
    )
    report_complete = bool(not report_term or (first_person and (not leader_term or id_relation)))
    frame["active"] = bool(frame["claims"])
    frame["complete"] = bool(
        comparison_complete and report_complete and not frame["unparsed"]
    )
    return frame


def _strip_zh_supported_tokens(source: str) -> str:
    value = str(source or "")
    for token in sorted(
        set(_ZH_MOTION)
        | set(_ZH_INSPECTION)
        | {
            "我", "先", "會", "会", "要", "再", "已", "已經", "已经", "一下", "了", "的", "那邊", "那边",
            "那裡", "那里", "現場", "现场", "情況", "情况", "狀況", "状况",
            "機台", "机台", "設備", "设备", "機器", "机器", "材料", "料件", "棒材",
        },
        key=len,
        reverse=True,
    ):
        value = value.replace(token, "")
    value = _MENTION_RE.sub("", value)
    value = re.sub(r"[\s,，。.!！?？:：;；()（）\[\]{}]+", "", value)
    return value


def _parse_zh_release_count(raw: str) -> int | None:
    token = str(raw or "").strip()
    if not token:
        return None
    if token.isdigit():
        value = int(token)
        return value if 0 <= value <= 999 else None
    if token in _ZH_DIGITS:
        return _ZH_DIGITS[token]
    if "十" in token:
        left, right = token.split("十", 1)
        tens = 1 if not left else _ZH_DIGITS.get(left)
        ones = 0 if not right else _ZH_DIGITS.get(right)
        if tens is not None and ones is not None:
            return tens * 10 + ones
    return None


def _format_id_release_count(value: int | None, raw: str) -> str:
    if value is None:
        return str(raw or "").strip()
    if value in _ID_SMALL_NUMBERS:
        return _ID_SMALL_NUMBERS[value]
    if 12 <= value <= 19:
        return _ID_SMALL_NUMBERS[value - 10] + " belas"
    if 20 <= value <= 99:
        tens, ones = divmod(value, 10)
        result = _ID_SMALL_NUMBERS[tens] + " puluh"
        return result if not ones else result + " " + _ID_SMALL_NUMBERS[ones]
    return str(value)


def _parse_zh_delivery_months(value: str) -> list[int]:
    months: list[int] = []
    for match in re.finditer(_ZH_MONTH_TOKEN, str(value or ""), flags=re.I):
        parsed = _parse_zh_release_count(match.group(0))
        if parsed is not None and 1 <= parsed <= 12 and parsed not in months:
            months.append(parsed)
    return months


def _format_id_month_list(months: Iterable[int]) -> str:
    names = [_ID_MONTH_NAMES.get(int(month), "") for month in months]
    names = [name for name in names if name]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return names[0] + " dan " + names[1]
    return ", ".join(names[:-1]) + ", dan " + names[-1]


def _strip_zh_production_priority_supported_tokens(
    source: str, evidence: Iterable[str]
) -> str:
    value = _MENTION_RE.sub("", str(source or ""))
    for token in sorted(
        {str(item or "") for item in evidence if str(item or "")},
        key=len,
        reverse=True,
    ):
        value = value.replace(token, "", 1)
    # These are grammatical connectors inside the supported relation, not
    # independent claims.  Remove them only after all source-bearing phrases
    # have been removed; an unrelated appended clause therefore remains in
    # ``unparsed`` and blocks the local direct route.
    support_words = {
        "請", "请", "要", "需要", "需", "務必", "务必", "把", "將", "将",
        "其中", "有", "的", "與", "与", "和", "及", "跟", "以及", "還有", "还有",
        "料", "材料", "棒材", "上", "中", "內", "内", "以",
    }
    for token in sorted(support_words, key=len, reverse=True):
        value = value.replace(token, "")
    return re.sub(r"[\s,，、。.!！?？:：;；()（）\[\]{}]+", "", value)


def _release_object_kind(raw: str) -> str:
    token = str(raw or "")
    if token in ("把", "捆"):
        return "bundle"
    if token == "批":
        return "batch"
    if any(term in token for term in ("資料", "资料", "數據", "数据")):
        return "data"
    if any(term in token for term in ("工單", "工单", "單", "单")):
        return "work_order"
    return ""


def _release_delegate(compact: str) -> str:
    for terms, delegate in (
        (("他們", "他们", "她們", "她们"), "third_plural"),
        (("你們", "你们"), "second_plural"),
        (("他", "她"), "third_singular"),
        (("你",), "second_singular"),
    ):
        if any(term in compact for term in terms):
            return delegate
    return ""


def _strip_zh_release_supported_tokens(source: str, object_evidence: str) -> str:
    value = _MENTION_RE.sub("", str(source or ""))
    value = _compact(value)
    if object_evidence:
        value = value.replace(_compact(object_evidence), "", 1)
    tokens = {
        "麻煩", "麻烦", "拜託", "拜托", "請", "请", "幫忙", "帮忙", "幫", "帮",
        "協助", "协助", "叫", "讓", "让", "他們", "他们", "她們", "她们", "他", "她",
        "你們", "你们", "你", "一下", "先", "再", "優先", "优先", "趕快", "赶快",
        "都", "全都", "已經", "已经", "已", "完成", "好了", "好", "完了", "完", "了",
        "要", "需要", "需", "放行", "放",
    }
    for token in sorted(tokens, key=len, reverse=True):
        value = value.replace(token, "")
    return re.sub(r"[\s,，。.!！?？:：;；()（）\[\]{}]+", "", value)


def _build_zh_id_data_release_frame(source: str, frame: dict) -> dict:
    """Classify ERP data release from syntax and reject physical/QC senses.

    Classification order is deliberate: an explicit spatial destination or QC
    actor wins over the generic factory shorthand.  Only then may a bundle,
    batch, work-order or data reference license bare 放 as the colloquial form
    of 放行.  This keeps 「這把麻煩他們放一下」and its paraphrases together while
    leaving 「這把刀放在架上」and「品保放行」to their correct senses.
    """
    visible = _MENTION_RE.sub("", str(source or ""))
    compact = _compact(visible)
    if not compact:
        return frame
    if "放假" in compact or "放料" in compact:
        return frame
    if _ZH_RELEASE_QC_RE.search(compact):
        return frame
    if _ZH_RELEASE_PHYSICAL_RE.search(compact):
        return frame
    if "放行" not in compact and _ZH_RELEASE_PHYSICAL_OBJECT_RE.search(compact):
        return frame

    object_match = _ZH_RELEASE_OBJECT_RE.search(compact)
    explicit_release = "放行" in compact
    completed = bool(_ZH_RELEASE_COMPLETED_RE.search(compact))
    request = bool(_ZH_RELEASE_REQUEST_RE.search(compact) or "放一下" in compact)
    shorthand_action = bool(
        object_match
        and (
            "放一下" in compact
            or completed
            or re.search(r"(?:先|再|優先|优先|趕快|赶快)放", compact)
            or re.search(r"放.{0,8}" + re.escape(object_match.group(0)), compact)
            or re.search(re.escape(object_match.group(0)) + r".{0,16}放", compact)
        )
    )
    # Explicit 放行 is already an ERP workflow verb unless QC won above.  Bare
    # 放 needs a production-record object plus request/completion/imperative
    # syntax; a lone everyday 放 therefore never activates this frame.
    if not explicit_release and not shorthand_action:
        return frame

    object_raw = object_match.group("object") if object_match else ""
    object_kind = _release_object_kind(object_raw)
    object_count_raw = object_match.group("count") if object_match else ""
    object_count = _parse_zh_release_count(object_count_raw)
    deictic = bool(object_match and object_match.group("deictic"))
    delegate = _release_delegate(compact)
    priority = any(term in compact for term in ("先放", "優先放", "优先放"))
    repeat = "再放" in compact
    evidence = object_match.group(0) if object_match else ""
    unparsed = _strip_zh_release_supported_tokens(source, evidence)

    frame["kind"] = "zh_id_erp_data_release"
    frame["slots"].update({
        "explicit_release": explicit_release,
        "completed": completed,
        "request": request or not completed,
        "delegate": delegate,
        "priority": priority,
        "repeat": repeat,
        "object_evidence": evidence,
        "object_kind": object_kind,
        "object_count_raw": object_count_raw,
        "object_count": object_count,
        "object_deictic": deictic,
    })
    _claim(
        frame,
        "erp_data_release_action",
        "放行" if explicit_release else "放／放一下",
        "把對應生產資料放行到下一站；不是把實體物品擺下或放置",
        "release data ke stasiun berikutnya",
    )
    if object_kind:
        object_meaning = {
            "bundle": "來源中的把／捆是棒材捆的資料參照",
            "batch": "來源中的批是該批生產資料的參照",
            "work_order": "來源指定這張工單／這單的資料",
            "data": "來源直接指定這筆資料",
        }[object_kind]
        _claim(
            frame,
            "erp_release_record_object",
            evidence,
            object_meaning,
            "data untuk " + ({
                "bundle": "bundel",
                "batch": "batch",
                "work_order": "work order",
                "data": "data",
            }[object_kind]),
        )
    if request or not completed:
        _claim(
            frame,
            "erp_release_request",
            "麻煩／請／幫／放一下",
            "請求對方執行資料放行",
            "tolong",
        )
    if delegate:
        _claim(
            frame,
            "erp_release_delegate",
            delegate,
            "保留被要求執行放行的人稱",
            {
                "third_plural": "mereka",
                "third_singular": "dia",
                "second_plural": "kalian",
                "second_singular": "Anda/kamu",
            }[delegate],
        )
    if completed:
        _claim(frame, "erp_release_completed", "已／都／放了", "資料放行已完成", "sudah di-release")
    frame["unparsed"] = unparsed
    frame["active"] = True
    # A source-first rendering is allowed only when the referenced record is
    # explicit and every non-mention token belongs to this relation.
    frame["complete"] = bool(object_kind and not unparsed)
    return frame


def _build_zh_id_production_priority_frame(source: str, frame: dict) -> dict:
    """Extract backlog cause and two independent production-priority groups.

    This is intentionally compositional: process, material size, recent-period
    count and delivery months are read from the current source.  The local
    renderer is available only when every meaningful token is accounted for.
    A related sentence with an extra instruction still activates the frame for
    provider prompting/validation, but never loses that extra clause through a
    partial deterministic translation.
    """
    visible = _MENTION_RE.sub("", str(source or ""))
    compact = _compact(visible)
    if not compact:
        return frame

    process_source = next(
        (
            term for term in sorted(_ZH_PROCESS_TO_ID, key=len, reverse=True)
            if term in compact
        ),
        "",
    )
    small_bar_source = next(
        (term for term in _ZH_SMALL_BAR_TERMS if term in compact), ""
    )
    period_match = _ZH_BACKLOG_PERIOD_RE.search(compact)
    period_evidence = period_match.group("evidence") if period_match else ""
    period_count_raw = period_match.group("count") if period_match else ""
    period_count = _parse_zh_release_count(period_count_raw)
    shipping_delay_source = next(
        (term for term in _ZH_SHIPPING_DELAY_TERMS if term in compact), ""
    )
    deferred_source = next(
        (term for term in _ZH_DEFERRED_MATERIAL_TERMS if term in compact), ""
    )
    volume_source = next(
        (term for term in _ZH_BACKLOG_VOLUME_TERMS if term in compact), ""
    )
    system_source = next(
        (term for term in _ZH_SYSTEM_TERMS if term in compact), ""
    )
    blue_source = next(
        (term for term in _ZH_BLUE_MARK_TERMS if term in compact), ""
    )
    note_source = next(
        (term for term in _ZH_NOTE_TERMS if term in compact), ""
    )
    priority_source = next(
        (term for term in _ZH_PRIORITY_ACTION_TERMS if term in compact), ""
    )
    delivery_match = _ZH_DELIVERY_MONTH_RE.search(compact)
    delivery_evidence = delivery_match.group("evidence") if delivery_match else ""
    delivery_months = _parse_zh_delivery_months(
        delivery_match.group("months") if delivery_match else ""
    )

    # Require the distinctive relation shape before claiming the sentence.  A
    # generic note about a blue system row or a standalone delivery-month order
    # must remain outside this specialized frame.
    core_signal = bool(
        process_source
        and small_bar_source
        and priority_source
        and (shipping_delay_source or deferred_source)
        and (blue_source or delivery_months)
    )
    if not core_signal:
        return frame

    evidence = (
        process_source,
        small_bar_source,
        period_evidence,
        shipping_delay_source,
        deferred_source,
        volume_source,
        system_source,
        blue_source,
        note_source,
        delivery_evidence,
        priority_source,
    )
    unparsed = _strip_zh_production_priority_supported_tokens(source, evidence)
    process_id = _ZH_PROCESS_TO_ID.get(process_source, "")

    frame["kind"] = "zh_id_production_backlog_priority"
    frame["slots"].update({
        "process_source": process_source,
        "process_id": process_id,
        "small_bar_source": small_bar_source,
        "backlog_period_evidence": period_evidence,
        "backlog_period_count_raw": period_count_raw,
        "backlog_period_count": period_count,
        "shipping_delay_source": shipping_delay_source,
        "deferred_material_source": deferred_source,
        "backlog_volume_source": volume_source,
        "system_source": system_source,
        "blue_marker_source": blue_source,
        "note_source": note_source,
        "delivery_month_evidence": delivery_evidence,
        "delivery_months": delivery_months,
        "priority_action_source": priority_source,
    })
    if process_source and small_bar_source:
        _claim(
            frame,
            "small_bar_process_scope",
            process_source + small_bar_source,
            "小尺寸棒材屬於指定製程範圍；不可硬拼成不自然的名詞串",
            f"material batang berukuran kecil untuk proses {process_id}",
        )
    if period_count and shipping_delay_source and deferred_source:
        _claim(
            frame,
            "recent_shipping_backlog",
            period_evidence + shipping_delay_source + deferred_source,
            "最近指定月數內未能及時出貨，因而形成遞延材料",
            f"dalam {_format_id_release_count(period_count, period_count_raw)} bulan terakhir; "
            "material tertunda karena tidak sempat dikirim tepat waktu",
        )
    if volume_source:
        _claim(
            frame,
            "backlog_volume",
            volume_source,
            "遞延材料數量很多",
            "banyak material",
        )
    if system_source and blue_source and note_source:
        _claim(
            frame,
            "blue_note_priority_group",
            system_source + blue_source + note_source,
            "第一個優先生產群組：系統中備註欄為藍底的材料",
            "material yang catatannya berlatar biru di sistem",
        )
    if delivery_months:
        _claim(
            frame,
            "delivery_month_priority_group",
            delivery_evidence,
            "第二個、獨立的優先生產群組：交期為指定月份的材料",
            "material dengan jadwal pengiriman bulan "
            + _format_id_month_list(delivery_months),
        )
    if priority_source:
        _claim(
            frame,
            "production_priority_action",
            priority_source,
            "上述兩組材料都要優先生產；兩條件是並列選擇，不可合併成同時滿足",
            "prioritaskan produksi ... serta material ...",
        )

    frame["unparsed"] = unparsed
    frame["active"] = True
    frame["complete"] = bool(
        process_id
        and small_bar_source
        and period_count
        and shipping_delay_source
        and deferred_source
        and volume_source
        and system_source
        and blue_source
        and note_source
        and delivery_months
        and priority_source
        and not unparsed
    )
    return frame


def _guard_scope_to_id(raw: str) -> str:
    token = str(raw or "").strip()
    if not token:
        return ""
    if token in {"多", "數", "数", "好幾", "好几", "幾", "几"}:
        return "beberapa mesin"
    parsed = _parse_zh_release_count(token)
    if parsed is None:
        return "beberapa mesin"
    return _format_id_release_count(parsed, token) + " mesin"


def _visible_zh_clauses(source: str) -> list[str]:
    visible = _MENTION_RE.sub(" ", str(source or ""))
    return [
        re.sub(r"\s+", " ", clause).strip()
        for clause in re.split(r"[\n,，、。.!！?？:：;；]+", visible)
        if re.sub(r"\s+", " ", clause).strip()
    ]


def _build_zh_id_machine_guard_frame(source: str, frame: dict) -> dict:
    """Bind machine-guard actions and states to the guard, not the machine.

    The parser works clause by clause and renders locally only when every
    non-mention clause belongs to a supported safety relation.  Extra text does
    not disappear: it leaves the frame active for provider prompting and
    validation but makes the deterministic route incomplete.
    """
    visible = _MENTION_RE.sub(" ", str(source or ""))
    guard_source = next(
        (
            term for term in sorted(_ZH_MACHINE_GUARD_TERMS, key=len, reverse=True)
            if term in visible
        ),
        "",
    )
    if not guard_source:
        return frame

    segments: list[dict[str, Any]] = []
    unparsed: list[str] = []
    for clause in _visible_zh_clauses(source):
        attendance = _ZH_ATTENDANCE_EARLY_LEAVE_RE.search(clause)
        if attendance:
            raw_modality = attendance.group("modality")
            segments.append({
                "type": "attendance_early_leave",
                "source": clause,
                "modality": (
                    "declarative_future"
                    if raw_modality in {"不會", "不会"}
                    else "prohibition"
                ),
            })
            continue

        if _ZH_DISCIPLINE_LAX_RE.search(clause):
            segments.append({"type": "discipline_not_lax", "source": clause})
            continue

        local_guard = next(
            (term for term in _ZH_MACHINE_GUARD_TERMS if term in clause), ""
        )
        close_action = bool(_ZH_GUARD_CLOSE_RE.search(clause))
        not_closed = bool(_ZH_GUARD_NOT_CLOSED_RE.search(clause))
        reminder_request = bool(_ZH_GUARD_REMINDER_RE.search(clause))
        recent_reminder = bool(_ZH_GUARD_RECENT_REMINDER_RE.search(clause))
        scope_match = _ZH_GUARD_EQUIPMENT_SCOPE_RE.search(clause)

        # A later clause such as 多台設備沒蓋好 inherits the explicit guard
        # subject from an earlier clause.  Without an explicit guard anywhere
        # in the source this function never activates, so ordinary equipment
        # status messages are not reinterpreted as safety-guard statements.
        if not_closed and (local_guard or scope_match):
            raw_scope = scope_match.group("count") if scope_match else ""
            segments.append({
                "type": "guard_not_closed",
                "source": clause,
                "recent_reminder": recent_reminder,
                "scope_raw": raw_scope,
                "scope_id": _guard_scope_to_id(raw_scope),
            })
            continue

        if reminder_request and local_guard:
            segments.append({
                "type": "guard_reminder_close" if close_action else "guard_reminder",
                "source": clause,
                "all_people": any(
                    term in clause for term in ("大家", "同仁", "人員", "人员")
                ),
            })
            continue

        if close_action and local_guard:
            segments.append({
                "type": "guard_close",
                "source": clause,
                "immediate": any(
                    term in clause
                    for term in (
                        "隨手", "随手", "立刻", "立即", "馬上", "马上",
                        "用完", "使用後", "使用后", "開啟後", "开启后", "打開後", "打开后",
                    )
                ),
            })
            continue

        unparsed.append(clause)

    guard_segments = [
        segment for segment in segments
        if str(segment.get("type") or "").startswith("guard_")
    ]
    if not guard_segments:
        return frame

    frame["kind"] = "zh_id_machine_guard_safety"
    frame["slots"].update({
        "guard_source": guard_source,
        "segments": segments,
        "has_guard_close": any(
            segment["type"] in {"guard_close", "guard_reminder_close"}
            for segment in segments
        ),
        "has_guard_reminder": any(
            segment["type"] in {"guard_reminder", "guard_reminder_close"}
            for segment in segments
        ),
        "has_guard_not_closed": any(
            segment["type"] == "guard_not_closed" for segment in segments
        ),
        "has_discipline": any(
            segment["type"] == "discipline_not_lax" for segment in segments
        ),
        "attendance_modality": next(
            (
                segment.get("modality", "")
                for segment in segments
                if segment["type"] == "attendance_early_leave"
            ),
            "",
        ),
    })
    frame["unparsed"] = " | ".join(unparsed)
    _claim(
        frame,
        "machine_guard_identity",
        guard_source,
        "護網／護罩是機械安全防護裝置，不是整台設備，也不是一般網路設備",
        "pelindung mesin / peralatan pengaman mesin",
    )

    seen_claims: set[str] = set()
    for segment in segments:
        segment_type = str(segment.get("type") or "")
        if segment_type in seen_claims:
            continue
        seen_claims.add(segment_type)
        evidence = str(segment.get("source") or "")
        if segment_type == "attendance_early_leave":
            if segment.get("modality") == "declarative_future":
                _claim(
                    frame,
                    "attendance_future_modality",
                    evidence,
                    "不會是將來否定陳述，不可改成不要的命令",
                    "saat pengecekan kehadiran, kita tidak akan meninggalkan tempat terlalu awal",
                )
            else:
                _claim(
                    frame,
                    "attendance_prohibition",
                    evidence,
                    "要求人員點名時不要太早離開",
                    "saat pengecekan kehadiran, jangan meninggalkan tempat terlalu awal",
                )
        elif segment_type == "discipline_not_lax":
            _claim(
                frame,
                "work_discipline_not_lax",
                evidence,
                "工作紀律不可鬆懈或大意；不是物理上的鬆／寬",
                "tetap jaga kedisiplinan dan jangan lengah",
            )
        elif segment_type == "guard_close":
            _claim(
                frame,
                "machine_guard_restore_action",
                evidence,
                "使用後立即把機械護網／護罩裝回或關妥",
                "segera pasang kembali pelindung mesin dengan benar",
            )
        elif segment_type in {"guard_reminder", "guard_reminder_close"}:
            _claim(
                frame,
                "machine_guard_reminder_duty",
                evidence,
                "請對方協助提醒人員把機械防護裝置裝回並確認到位",
                "mohon bantu ingatkan agar pelindung mesin dipasang kembali dengan benar",
            )
        elif segment_type == "guard_not_closed":
            _claim(
                frame,
                "machine_guard_not_closed_state",
                evidence,
                "沒蓋好的是多台設備上的護網／護罩，不是整台機器被關閉",
                "pelindung pada beberapa mesin belum dipasang kembali dengan benar",
            )

    frame["active"] = True
    frame["complete"] = bool(segments and not unparsed)
    return frame


def _extract_emoji_tokens(source: str) -> list[str]:
    """Return source emoji clusters in order so a direct route cannot drop them."""
    return [match.group(0) for match in _EMOJI_CLUSTER_RE.finditer(str(source or ""))]


def _event_prefix_actor(prefix: str) -> tuple[str, str]:
    """Consume only a standalone actor and optional 在 before 點名."""
    value = str(prefix or "")
    if value == "在":
        return "", ""
    for actor in sorted(_ZH_EVENT_ACTOR_ID, key=len, reverse=True):
        if value in {actor, actor + "在", "在" + actor}:
            return actor, ""
    return "", value


def _build_zh_id_attendance_vehicle_departure_frame(
    source: str, frame: dict
) -> dict:
    """Bind attendance, a human driving action and departure as one event.

    The parser consumes roles rather than matching an entire sentence.  It is
    therefore reusable across traditional/simplified Chinese, explicit or
    omitted actors, after/before/during relations, modal variants, departure
    destinations and source emoji.  Unconsumed text keeps the frame active for
    provider validation but prevents the local renderer from dropping content.
    """
    emoji_tokens = _extract_emoji_tokens(source)
    visible = _MENTION_RE.sub("", str(source or ""))
    visible = _EMOJI_CLUSTER_RE.sub("", visible)
    compact = re.sub(
        r"[\s,，、。.!！?？:：;；()（）\[\]{}]+", "", _norm(visible)
    )
    attendance = _ZH_ATTENDANCE_EVENT_RE.search(compact)
    if not attendance:
        return frame
    departure = _ZH_PERSON_VEHICLE_DEPARTURE_RE.search(
        compact, attendance.end()
    )
    if not departure:
        return frame

    prefix = compact[:attendance.start()]
    between = compact[attendance.end():departure.start()]
    suffix = compact[departure.end():]
    prefix_actor, prefix_unparsed = _event_prefix_actor(prefix)
    matched_actor = str(departure.group("actor") or "")
    actor_source = matched_actor or prefix_actor

    unparsed: list[str] = []
    if prefix_unparsed:
        unparsed.append(prefix_unparsed)
    if prefix_actor and matched_actor and prefix_actor != matched_actor:
        unparsed.append(prefix_actor + "/" + matched_actor)

    if _ZH_EVENT_BEFORE_RE.fullmatch(between):
        temporal_relation = "before"
    elif _ZH_EVENT_DURING_RE.fullmatch(between):
        temporal_relation = "during"
    elif _ZH_EVENT_AFTER_RE.fullmatch(between):
        temporal_relation = "after"
    else:
        temporal_relation = "unknown"
        if between:
            unparsed.append(between)

    # A final conversational particle is part of the departure speech act.
    # Any other suffix is a separate claim and must block the direct renderer.
    suffix_unparsed = re.sub(r"^[啊呀吧呢嘛]+$", "", suffix)
    if suffix_unparsed:
        unparsed.append(suffix_unparsed)

    raw_modality = str(departure.group("modality") or "")
    if raw_modality in {"不要", "別", "别"}:
        modality = "prohibition"
    elif raw_modality in {"不能", "不可"}:
        modality = "not_allowed"
    elif raw_modality in {"準備", "准备"}:
        modality = "imminent"
    elif raw_modality in {"將要", "将要", "會", "会"}:
        modality = "future"
    elif raw_modality == "要":
        modality = "intention"
    else:
        modality = "completed" if departure.group("aspect") else "unmarked"

    connector_values = {
        str(departure.group("connector") or ""),
        str(departure.group("connector_after") or ""),
    }
    priority = bool(
        departure.group("priority") or departure.group("priority_after")
    )
    farewell = bool(
        any("👋" in token for token in emoji_tokens)
        or str(departure.group("aspect") or "") in {"啦", "囉", "啰", "喽", "喔", "哦"}
    )

    frame["kind"] = "zh_id_attendance_vehicle_departure"
    frame["slots"].update({
        "attendance_source": attendance.group(0),
        "temporal_relation": temporal_relation,
        "actor_source": actor_source,
        "actor_id": _ZH_EVENT_ACTOR_ID.get(actor_source, ""),
        "drive_source": departure.group("drive"),
        "departure_source": departure.group("departure"),
        "modality": modality,
        "priority": priority,
        "direct": "直接" in connector_values,
        "sequence_then": "再" in connector_values,
        "farewell": farewell,
        "emoji_tokens": emoji_tokens,
    })
    frame["unparsed"] = " | ".join(unparsed)
    _claim(
        frame,
        "attendance_temporal_relation",
        attendance.group(0) + between,
        "點名與離開事件的先後／同時關係必須保留",
        {
            "after": "setelah pengecekan kehadiran selesai",
            "before": "sebelum pengecekan kehadiran",
            "during": "saat pengecekan kehadiran",
        }.get(temporal_relation, "hubungan waktu dengan absensi"),
    )
    if actor_source:
        _claim(
            frame,
            "departure_actor",
            actor_source,
            "明示的人員是開車並離開的行為者",
            _ZH_EVENT_ACTOR_ID.get(actor_source, ""),
        )
    _claim(
        frame,
        "human_drives_and_departs",
        departure.group(0),
        "人員以開車／搭車方式離開；車是交通方式，不可升格為離開的主詞",
        "orang berangkat/pergi dengan mobil",
    )
    if priority:
        _claim(
            frame,
            "grounded_departure_priority",
            "先",
            "來源明寫先離開，目標才可使用 lebih dahulu/dulu",
            "lebih dahulu",
        )
    if emoji_tokens:
        _claim(
            frame,
            "source_emoji_fidelity",
            "".join(emoji_tokens),
            "來源表情符號必須原樣保留且不可漏掉",
            "".join(emoji_tokens),
        )
    frame["active"] = True
    frame["complete"] = bool(
        temporal_relation != "unknown" and not unparsed
    )
    return frame


def _build_zh_id_frame(source: str, frame: dict) -> dict:
    machine_guard_frame = _build_zh_id_machine_guard_frame(source, frame)
    if machine_guard_frame.get("active"):
        return machine_guard_frame
    priority_frame = _build_zh_id_production_priority_frame(source, frame)
    if priority_frame.get("active"):
        return priority_frame
    release_frame = _build_zh_id_data_release_frame(source, frame)
    if release_frame.get("active"):
        return release_frame
    departure_frame = _build_zh_id_attendance_vehicle_departure_frame(
        source, frame
    )
    if departure_frame.get("active"):
        return departure_frame
    compact = _compact(source)
    motion_term = next((term for term in sorted(_ZH_MOTION, key=len, reverse=True) if term in compact), "")
    inspect_term = next((term for term in sorted(_ZH_INSPECTION, key=len, reverse=True) if term in compact), "")
    if not (motion_term and inspect_term):
        return frame

    first_person = "我" in compact and "我們" not in compact and "我们" not in compact
    completed = any(term in compact for term in ("已經過去", "已经过去", "已經到現場", "已经到现场", "已到現場", "已到现场"))
    future = any(term in compact for term in ("會", "会", "要"))
    later = "再" in compact
    explicit_first = "先" in compact
    destination = "location" if any(x in compact for x in ("現場", "现场")) else "there"
    if any(x in compact for x in ("機台", "机台", "設備", "设备", "機器", "机器")):
        obj = "machine"
    elif any(x in compact for x in ("材料", "料件", "棒材")):
        obj = "material"
    elif any(x in compact for x in ("情況", "情况", "狀況", "状况")):
        obj = "situation"
    else:
        obj = "implicit_situation"
    unparsed = _strip_zh_supported_tokens(source)

    frame["kind"] = "zh_id_motion_inspection_relation"
    frame["slots"].update({
        "first_person": first_person,
        "motion_term": motion_term,
        "inspection_term": inspect_term,
        "destination": destination,
        "object": obj,
        "completed": completed,
        "future": future,
        "later": later,
        "explicit_first": explicit_first,
        "first_or_soft": bool(explicit_first or "看看" in inspect_term or "一下" in inspect_term),
    })
    frame["unparsed"] = unparsed
    if first_person:
        _claim(frame, "first_person_actor", "我", "說話者本人執行動作", "Saya")
    _claim(frame, "movement_to_location", motion_term, "先移動到那裡／現場", "ke sana / ke lokasi")
    _claim(frame, "inspection_purpose", inspect_term, "到達後查看或確認", "untuk mengecek / memeriksa")
    if obj == "machine":
        _claim(frame, "inspection_object", "機台／設備", "檢查機台狀況", "kondisi mesin")
    elif obj == "material":
        _claim(frame, "inspection_object", "材料", "檢查材料狀況", "kondisi material")
    else:
        _claim(frame, "inspection_object", "情況／省略的現場情況", "查看現場情況", "situasinya")

    frame["active"] = True
    frame["complete"] = bool(first_person and not unparsed)
    return frame


def build_frame(source: str, src_lang: str, tgt_lang: str) -> dict:
    """Extract source-side semantic relations for either supported direction."""
    frame = _base_frame(source, src_lang, tgt_lang)
    if not str(source or "").strip():
        return frame
    if frame["src_lang"] == "id" and frame["tgt_lang"] == "zh":
        return _build_id_zh_frame(source, frame)
    if frame["src_lang"] == "zh" and frame["tgt_lang"] == "id":
        return _build_zh_id_frame(source, frame)
    return frame


def _with_mentions(frame: Mapping, text: str) -> str:
    mentions = [str(item).strip() for item in frame.get("mentions") or () if str(item).strip()]
    return ((" ".join(mentions) + " ") if mentions else "") + text


def deterministic_translation(frame: Mapping) -> str:
    """Render a complete source frame directly; return an empty string otherwise."""
    if not frame or not frame.get("active") or not frame.get("complete"):
        return ""
    slots = frame.get("slots") or {}
    if frame.get("kind") == "id_zh_equipment_code_failure":
        codes = [str(item) for item in slots.get("equipment_codes") or () if str(item)]
        if not codes:
            return ""
        return _with_mentions(frame, f"{'、'.join(codes)} 機台故障")

    if frame.get("kind") == "id_zh_shift_process_status":
        shift = str(slots.get("shift_target") or "")
        if not shift or slots.get("process") != "spray_painting":
            return ""
        status = "還沒有噴漆" if slots.get("completion") == "not_yet" else "沒有噴漆"
        return _with_mentions(frame, f"{shift}{status}")

    if frame.get("kind") == "zh_id_machine_guard_safety":
        rendered: list[str] = []
        for segment in slots.get("segments") or ():
            segment_type = str(segment.get("type") or "")
            if segment_type == "attendance_early_leave":
                if segment.get("modality") == "declarative_future":
                    rendered.append(
                        "Saat pengecekan kehadiran, kita tidak akan meninggalkan "
                        "tempat terlalu awal."
                    )
                else:
                    rendered.append(
                        "Saat pengecekan kehadiran, jangan meninggalkan tempat terlalu awal."
                    )
            elif segment_type == "discipline_not_lax":
                rendered.append("Tetap jaga kedisiplinan dan jangan lengah.")
            elif segment_type == "guard_close":
                if segment.get("immediate"):
                    rendered.append(
                        "Setelah menggunakan mesin, segera pasang kembali pelindung mesin."
                    )
                else:
                    rendered.append(
                        "Pelindung mesin harus dipasang kembali dengan benar."
                    )
            elif segment_type in {"guard_reminder", "guard_reminder_close"}:
                recipient = "semua orang " if segment.get("all_people") else ""
                rendered.append(
                    "Mohon bantu ingatkan " + recipient
                    + "agar pelindung mesin dipasang kembali dengan benar."
                )
            elif segment_type == "guard_not_closed":
                scope = str(segment.get("scope_id") or "")
                subject = (
                    f"pelindung pada {scope}"
                    if scope
                    else "pelindung mesin"
                )
                prefix = "Saya baru saja diingatkan bahwa " if segment.get("recent_reminder") else ""
                sentence = prefix + subject + " belum dipasang kembali dengan benar."
                rendered.append(sentence[:1].upper() + sentence[1:])
        if not rendered:
            return ""
        return _with_mentions(frame, " ".join(rendered))

    if frame.get("kind") == "zh_id_attendance_vehicle_departure":
        temporal_relation = str(slots.get("temporal_relation") or "")
        introduction = {
            "after": "Setelah pengecekan kehadiran selesai, ",
            "before": "Sebelum pengecekan kehadiran, ",
            "during": "Saat pengecekan kehadiran, ",
        }.get(temporal_relation, "")
        if not introduction:
            return ""

        departure_source = str(slots.get("departure_source") or "")
        if departure_source in {"回家"}:
            verb, tail = "pulang", "dengan mobil"
        elif departure_source in {"回去"}:
            verb, tail = "kembali", "dengan mobil"
        elif departure_source in {"離開", "离开", "離場", "离场"}:
            verb, tail = "meninggalkan lokasi", "dengan mobil"
        else:
            verb, tail = "berangkat", "dengan mobil"

        modality = str(slots.get("modality") or "")
        if modality == "prohibition":
            modal_prefix = "jangan "
        elif modality == "not_allowed":
            modal_prefix = "tidak boleh "
        elif modality in {"future", "intention"}:
            modal_prefix = "akan "
        elif modality == "imminent":
            modal_prefix = "bersiap untuk "
        elif modality == "completed" and not slots.get("farewell"):
            modal_prefix = "sudah "
        else:
            modal_prefix = ""

        actor = str(slots.get("actor_id") or "")
        predicate_parts = [modal_prefix]
        if slots.get("sequence_then"):
            predicate_parts.append("kemudian ")
        if slots.get("direct"):
            predicate_parts.append("langsung ")
        predicate_parts.append(verb)
        if slots.get("priority"):
            predicate_parts.append(" lebih dahulu")
        predicate_parts.append(" " + tail)
        predicate = "".join(predicate_parts)
        sentence = introduction + ((actor + " ") if actor else "") + predicate + "."
        emoji_tokens = [
            str(token) for token in slots.get("emoji_tokens") or () if str(token)
        ]
        if emoji_tokens:
            sentence += " " + "".join(emoji_tokens)
        return _with_mentions(frame, sentence)

    if frame.get("kind") == "zh_id_production_backlog_priority":
        process_id = str(slots.get("process_id") or "")
        period_count = slots.get("backlog_period_count")
        period_raw = str(slots.get("backlog_period_count_raw") or "")
        month_list = _format_id_month_list(slots.get("delivery_months") or ())
        if not process_id or not period_count or not month_list:
            return ""
        count_text = _format_id_release_count(period_count, period_raw)
        text = (
            f"Dalam {count_text} bulan terakhir, banyak material batang berukuran kecil "
            f"untuk proses {process_id} yang tertunda karena tidak sempat dikirim tepat waktu. "
            "Prioritaskan produksi material yang catatannya berlatar biru di sistem serta "
            f"material dengan jadwal pengiriman bulan {month_list}."
        )
        return _with_mentions(frame, text)

    if frame.get("kind") == "zh_id_erp_data_release":
        object_kind = str(slots.get("object_kind") or "")
        count_raw = str(slots.get("object_count_raw") or "")
        count_text = _format_id_release_count(
            slots.get("object_count"), count_raw
        )
        deictic = bool(slots.get("object_deictic"))
        if object_kind == "bundle":
            reference = ((count_text + " ") if count_text else "") + "bundel"
        elif object_kind == "batch":
            reference = ((count_text + " ") if count_text else "") + "batch"
        elif object_kind == "work_order":
            reference = "work order"
        elif object_kind == "data":
            reference = "data"
        else:
            return ""
        if deictic:
            reference += " ini"
        data_object = reference if object_kind == "data" else "data untuk " + reference
        destination = "ke stasiun berikutnya"
        if slots.get("completed"):
            text = data_object[:1].upper() + data_object[1:] + " sudah di-release " + destination
        else:
            action = "release " + data_object + " " + destination
            delegate = slots.get("delegate")
            if delegate == "third_plural":
                text = "Tolong minta mereka " + action
            elif delegate == "third_singular":
                text = "Tolong minta dia " + action
            elif delegate == "second_plural":
                text = "Tolong kalian " + action
            elif delegate == "second_singular":
                text = "Tolong Anda " + action
            else:
                text = "Tolong " + action
        if slots.get("priority"):
            text += " terlebih dahulu"
        if slots.get("repeat"):
            text += " sekali lagi"
        return _with_mentions(frame, text + ".")

    if frame.get("kind") == "id_zh_weight_display_relation":
        parts: list[str] = []
        monitor_weight = str(slots.get("monitor_weight") or "")
        scale_weight = str(slots.get("scale_weight") or "")
        difference = str(slots.get("difference") or "")
        if monitor_weight and scale_weight:
            parts.append(
                f"螢幕顯示 {monitor_weight} 公斤，而天車電子磅秤顯示 {scale_weight} 公斤。"
            )
            if difference:
                parts.append(f"兩者相差 {difference} 公斤。")
        elif difference:
            parts.append(f"螢幕顯示的重量與天車電子磅秤相差 {difference} 公斤。")
        else:
            return ""
        if slots.get("report_term"):
            aspect = "已" if slots.get("report_completed") else ""
            if slots.get("leader_id_relation"):
                parts.append(f"我{aspect}用班長的 ID 回報。")
            else:
                parts.append(f"我{aspect}回報。")
        return _with_mentions(frame, "".join(parts))

    if frame.get("kind") == "zh_id_motion_inspection_relation":
        destination = "ke lokasi" if slots.get("destination") == "location" else "ke sana"
        obj = slots.get("object")
        action = {
            "machine": "memeriksa kondisi mesin",
            "material": "memeriksa kondisi material",
            "situation": "mengecek situasinya",
            "implicit_situation": "mengecek situasinya",
        }.get(obj, "mengecek situasinya")
        if slots.get("completed"):
            text = f"Saya sudah pergi {destination} untuk {action}."
        elif slots.get("later"):
            first = " terlebih dahulu" if slots.get("explicit_first") else ""
            text = f"Nanti saya akan pergi {destination}{first} untuk {action}."
        elif slots.get("future"):
            first = " terlebih dahulu" if slots.get("first_or_soft") else ""
            text = f"Saya akan pergi {destination}{first} untuk {action}."
        else:
            first = " dulu" if slots.get("first_or_soft") else ""
            text = f"Saya {destination}{first} untuk {action}."
        return _with_mentions(frame, text)
    return ""


def _has_any(text: str, terms: Iterable[str]) -> bool:
    low = _norm(text)
    return any(_norm(term) in low for term in terms)


def _number_unit_present_zh(text: str, value: str) -> bool:
    if not value:
        return True
    return bool(re.search(re.escape(value) + r"\s*(?:公斤|kg)", text, flags=re.I))


def _id_delivery_month_present(text: str, month: int) -> bool:
    name = _ID_MONTH_NAMES.get(int(month), "")
    if name and _has_phrase(text, (name,)):
        return True
    return bool(
        re.search(
            r"(?<!\d)" + re.escape(str(int(month))) + r"(?!\d)",
            str(text or ""),
            flags=re.I,
        )
    )


def validate_translation(frame: Mapping, translation: str) -> tuple[bool, list[str]]:
    """Validate source roles and relations, not merely isolated keywords."""
    if not frame or not frame.get("active"):
        return True, []
    target = str(translation or "").strip()
    if not target:
        return False, ["factory_message_semantics:empty_translation"]
    slots = frame.get("slots") or {}
    issues: list[str] = []

    if frame.get("kind") == "id_zh_equipment_code_failure":
        for code in slots.get("equipment_codes") or ():
            if not re.search(
                r"(?<![A-Za-z0-9])" + re.escape(str(code)) + r"(?![A-Za-z0-9])",
                target,
                re.I,
            ):
                issues.append("factory_message_semantics:equipment_code_missing")
        if not any(term in target for term in ("機台", "機器", "設備")):
            issues.append("factory_message_semantics:equipment_role_missing")
        if "故障" not in target:
            issues.append("factory_message_semantics:functional_failure_missing")
        if any(term in target for term in ("損傷", "损伤")):
            issues.append("factory_message_semantics:equipment_mistranslated_as_surface_damage")
        if any(term in target for term in ("損壞", "损坏")) and "故障" not in target:
            issues.append("factory_message_semantics:equipment_failure_wording_ambiguous")

    elif frame.get("kind") == "zh_id_machine_guard_safety":
        low = _norm(target)
        target_clauses = [
            _norm(clause)
            for clause in re.split(r"[\n.!！?？;；]+", target)
            if _norm(clause)
        ]

        def _guard_target_present(value: str) -> bool:
            return bool(re.search(
                r"\b(?:"
                r"pelindung(?:\s+keselamatan)?\s+mesin(?:nya)?"
                r"|pelindung\s+pada\s+(?:beberapa|sejumlah|\w+)\s+mesin"
                r"|(?:peralatan\s+)?pengaman\s+mesin"
                r"|pagar\s+pengaman\s+mesin"
                r")\b",
                value,
                re.I,
            ))

        def _guard_position_action_present(value: str) -> bool:
            return bool(re.search(
                r"\b(?:pasang|memasang|dipasang|terpasang|tutup|menutup|"
                r"ditutup|tertutup|kembali\s+ke\s+posisi)\b",
                value,
                re.I,
            ))

        if not _guard_target_present(low):
            issues.append("factory_message_semantics:machine_guard_term_missing")
        if any(phrase in low for phrase in (
            "pelindung jaring peralatan",
            "jaring pelindung peralatan",
            "jaring peralatan",
        )):
            issues.append("factory_message_semantics:machine_guard_unnatural_literal_term")

        segments = list(slots.get("segments") or ())
        attendance_modality = str(slots.get("attendance_modality") or "")
        if attendance_modality:
            attendance_clauses = [
                clause for clause in target_clauses
                if _has_phrase(clause, (
                    "absen", "absensi", "pengecekan kehadiran", "pemeriksaan kehadiran",
                ))
            ]
            if not attendance_clauses:
                issues.append("factory_message_semantics:attendance_check_missing")
            elif attendance_modality == "declarative_future":
                if not any("tidak akan" in clause for clause in attendance_clauses):
                    issues.append("factory_message_semantics:attendance_future_negation_missing")
                if any(_has_phrase(clause, ("jangan",)) for clause in attendance_clauses):
                    issues.append("factory_message_semantics:attendance_statement_changed_to_command")
            elif not any(
                _has_phrase(clause, ("jangan",)) for clause in attendance_clauses
            ):
                issues.append("factory_message_semantics:attendance_prohibition_missing")

        if slots.get("has_discipline"):
            if not _has_phrase(low, ("disiplin", "kedisiplinan")):
                issues.append("factory_message_semantics:work_discipline_missing")
            if not _has_phrase(low, (
                "lengah", "lalai", "mengendur", "mengendurkan", "kendur",
            )):
                issues.append("factory_message_semantics:discipline_laxness_missing")
            if _has_phrase(low, ("longgar",)):
                issues.append("factory_message_semantics:discipline_mistranslated_as_physical_looseness")

        if slots.get("has_guard_close"):
            if not any(
                _guard_target_present(clause)
                and _guard_position_action_present(clause)
                for clause in target_clauses
            ):
                issues.append("factory_message_semantics:machine_guard_restore_action_missing")

        if slots.get("has_guard_reminder"):
            reminder_clauses = [
                clause for clause in target_clauses
                if re.search(r"\b(?:ingat|ingatkan|mengingatkan|diingatkan)\b", clause, re.I)
            ]
            if not reminder_clauses:
                issues.append("factory_message_semantics:machine_guard_reminder_missing")
            elif not any(
                _guard_target_present(clause)
                and _guard_position_action_present(clause)
                for clause in reminder_clauses
            ):
                issues.append("factory_message_semantics:machine_guard_reminder_object_incomplete")

        if slots.get("has_guard_not_closed"):
            negative_guard_clauses = [
                clause for clause in target_clauses
                if _guard_target_present(clause)
                and re.search(r"\b(?:belum|tidak)\b", clause, re.I)
                and _guard_position_action_present(clause)
            ]
            if not negative_guard_clauses:
                issues.append("factory_message_semantics:machine_guard_not_closed_state_missing")

            # Explicitly catch the fluent but dangerous role swap shown in the
            # incident: ``beberapa mesin tidak ditutup`` makes the machine the
            # closed object even if a different sentence mentions a guard.
            wrong_machine_subject = any(
                re.search(
                    r"\b(?:beberapa|sejumlah|\w+)\s+mesin\s+(?:belum|tidak)\s+"
                    r"(?:di)?tutup",
                    clause,
                    re.I,
                )
                and not _guard_target_present(clause)
                for clause in target_clauses
            )
            if wrong_machine_subject:
                issues.append("factory_message_semantics:machine_replaced_guard_as_closed_subject")

            for segment in segments:
                if segment.get("type") != "guard_not_closed":
                    continue
                scope_id = str(segment.get("scope_id") or "")
                if scope_id and not any(
                    _has_phrase(clause, (scope_id,))
                    for clause in negative_guard_clauses
                ):
                    issues.append("factory_message_semantics:affected_machine_scope_missing")
                if segment.get("recent_reminder") and not _has_phrase(
                    low, ("baru saja", "barusan")
                ):
                    issues.append("factory_message_semantics:recent_reminder_aspect_missing")

    elif frame.get("kind") == "zh_id_attendance_vehicle_departure":
        low = _norm(target)
        temporal_relation = str(slots.get("temporal_relation") or "")
        attendance_terms = (
            r"(?:absen|absensi|pengecekan\s+kehadiran|pemeriksaan\s+kehadiran)"
        )
        temporal_patterns = {
            "after": r"\b(?:setelah|sesudah|selesai|habis)\s+" + attendance_terms + r"\b",
            "before": r"\bsebelum\s+" + attendance_terms + r"\b",
            "during": r"\b(?:saat|ketika|selama)\s+" + attendance_terms + r"\b",
        }
        temporal_pattern = temporal_patterns.get(temporal_relation, "")
        if not temporal_pattern or not re.search(temporal_pattern, low, re.I):
            issues.append(
                "factory_message_semantics:attendance_departure_relation_missing"
            )

        human_vehicle_motion = re.search(
            r"\b(?:"
            r"(?:mengemudi|mengendarai)\s+(?:mobil|kendaraan)"
            r"|(?:berangkat|pergi|pulang|kembali|meninggalkan\s+lokasi)"
            r"(?:\s+(?:lebih\s+(?:dulu|dahulu)|terlebih\s+dahulu|dulu))?\s+"
            r"(?:(?:dengan|naik)\s+(?:mobil|kendaraan)|mengendarai\s+(?:mobil|kendaraan))"
            r")\b",
            low,
            re.I,
        )
        if not human_vehicle_motion:
            issues.append(
                "factory_message_semantics:human_vehicle_departure_missing"
            )

        # 車輛開走了 can legitimately use a vehicle subject. 開車走了 cannot:
        # its omitted subject is a person and 車 remains the transport object.
        if re.search(
            r"\b(?:kendaraan|mobil)(?:nya)?\s+"
            r"(?:(?:sudah|telah|akan)\s+)?(?:langsung\s+)?"
            r"(?:berangkat|pergi|pulang|meninggalkan)\b",
            low,
            re.I,
        ):
            issues.append(
                "factory_message_semantics:vehicle_promoted_to_departure_actor"
            )

        priority_present = bool(re.search(
            r"\b(?:lebih\s+dulu|lebih\s+dahulu|terlebih\s+dahulu|lebih\s+awal)\b",
            low,
            re.I,
        ))
        if slots.get("priority"):
            if not re.search(
                r"\b(?:dulu|dahulu|lebih\s+dulu|lebih\s+dahulu|terlebih\s+dahulu)\b",
                low,
                re.I,
            ):
                issues.append(
                    "factory_message_semantics:grounded_departure_priority_missing"
                )
        elif priority_present:
            issues.append(
                "factory_message_semantics:ungrounded_departure_priority"
            )

        actor_id = str(slots.get("actor_id") or "")
        if actor_id and not _has_phrase(low, (actor_id,)):
            issues.append("factory_message_semantics:departure_actor_missing")

        modality = str(slots.get("modality") or "")
        if modality in {"future", "intention"} and not _has_phrase(low, ("akan",)):
            issues.append("factory_message_semantics:departure_future_modality_missing")
        elif modality == "imminent" and not _has_phrase(
            low, ("bersiap", "akan segera")
        ):
            issues.append("factory_message_semantics:departure_imminence_missing")
        elif modality == "prohibition" and not _has_phrase(low, ("jangan",)):
            issues.append("factory_message_semantics:departure_prohibition_missing")
        elif modality == "not_allowed" and not _has_phrase(
            low, ("tidak boleh", "dilarang")
        ):
            issues.append("factory_message_semantics:departure_not_allowed_missing")

        if slots.get("direct") and not _has_phrase(low, ("langsung",)):
            issues.append("factory_message_semantics:direct_departure_missing")

        for emoji in slots.get("emoji_tokens") or ():
            emoji_text = str(emoji or "")
            if emoji_text and target.count(emoji_text) < str(frame.get("source") or "").count(emoji_text):
                issues.append(
                    "factory_message_semantics:source_emoji_missing:" + emoji_text
                )

    elif frame.get("kind") == "zh_id_production_backlog_priority":
        low = _norm(target)
        process_id = str(slots.get("process_id") or "")
        if process_id and not _has_phrase(low, (process_id,)):
            issues.append("factory_message_semantics:production_process_scope_missing")
        if not _has_phrase(low, (
            "batang berukuran kecil", "batang ukuran kecil", "batang berdiameter kecil",
        )):
            issues.append("factory_message_semantics:small_bar_scope_missing")

        period_count = slots.get("backlog_period_count")
        if period_count:
            period_text = _format_id_release_count(
                period_count, str(slots.get("backlog_period_count_raw") or "")
            )
            if not _has_phrase(low, (
                f"{period_text} bulan terakhir", f"{period_count} bulan terakhir",
            )):
                issues.append("factory_message_semantics:backlog_period_missing")
        if not (
            _has_phrase(low, ("tertunda", "keterlambatan"))
            and _has_phrase(low, ("dikirim", "pengiriman"))
        ):
            issues.append("factory_message_semantics:delayed_shipping_relation_missing")
        if not _has_phrase(low, ("banyak", "dalam jumlah besar")):
            issues.append("factory_message_semantics:backlog_volume_missing")

        blue_note_relation = bool(
            _has_phrase(low, ("sistem",))
            and _has_phrase(low, ("biru",))
            and _has_phrase(low, (
                "catatan", "catatannya", "ditandai", "tanda", "berlatar",
            ))
        )
        if not blue_note_relation:
            issues.append("factory_message_semantics:blue_system_note_relation_missing")
        for month in slots.get("delivery_months") or ():
            if not _id_delivery_month_present(low, int(month)):
                issues.append(
                    "factory_message_semantics:delivery_month_missing:" + str(month)
                )
        if not (
            _has_phrase(low, ("produksi",))
            and _has_phrase(low, (
                "prioritaskan", "memprioritaskan", "diprioritaskan", "prioritas",
            ))
        ):
            issues.append("factory_message_semantics:production_priority_missing")

        # The source joins two eligible sets: blue-note material, plus material
        # due in the named months.  Repeating ``material`` after the connector is
        # the clearest deterministic proof that a model did not collapse this
        # into one item that must satisfy both filters.
        if not re.search(
            r"\b(?:serta|dan\s+juga|maupun|dan)\s+material\b", low, re.I
        ):
            issues.append("factory_message_semantics:priority_groups_collapsed")
        if any(phrase in low for phrase in (
            "material tunda", "batang kecil polishing", "catatan latar biru",
            "tanggal pengiriman bulan",
        )):
            issues.append("factory_message_semantics:unnatural_indonesian_compound")

    elif frame.get("kind") == "zh_id_erp_data_release":
        low = _norm(target)
        release_relation = bool(
            re.search(
                r"\b(?:release|rilis|merilis|me-?release|di-?release|dirilis)\b",
                low,
                re.I,
            )
            and _has_phrase(low, ("data",))
            and _has_phrase(low, (
                "stasiun berikutnya", "proses berikutnya", "tahap berikutnya",
                "untuk dilanjutkan",
            ))
        )
        if not release_relation:
            issues.append("factory_message_semantics:erp_data_release_relation_missing")
        if re.search(
            r"\b(?:meletakkan|menaruh|taruh|letakkan|menempatkan|"
            r"menyimpan|simpan|melepaskan)\b",
            low,
            re.I,
        ):
            issues.append("factory_message_semantics:erp_release_mistranslated_as_physical_placement")

        object_kind = slots.get("object_kind")
        if object_kind == "bundle" and not _has_phrase(low, ("bundel",)):
            issues.append("factory_message_semantics:erp_release_bundle_reference_missing")
        elif object_kind == "batch" and not _has_phrase(low, ("batch", "lot")):
            issues.append("factory_message_semantics:erp_release_batch_reference_missing")
        elif object_kind == "work_order" and not _has_phrase(low, ("work order",)):
            issues.append("factory_message_semantics:erp_release_work_order_reference_missing")
        elif object_kind == "data" and not _has_phrase(low, ("data",)):
            issues.append("factory_message_semantics:erp_release_data_reference_missing")

        count = slots.get("object_count")
        count_raw = str(slots.get("object_count_raw") or "")
        if count is not None:
            expected_count = _format_id_release_count(count, count_raw)
            if not _has_phrase(low, (expected_count, str(count))):
                issues.append("factory_message_semantics:erp_release_object_count_missing")
        if slots.get("object_deictic") and not _has_phrase(low, ("ini",)):
            issues.append("factory_message_semantics:erp_release_deictic_reference_missing")
        if slots.get("request") and not _has_phrase(low, ("tolong", "mohon", "harap")):
            issues.append("factory_message_semantics:erp_release_request_modality_missing")
        delegate_terms = {
            "third_plural": ("mereka",),
            "third_singular": ("dia",),
            "second_plural": ("kalian",),
            "second_singular": ("anda", "kamu"),
        }.get(slots.get("delegate"), ())
        if delegate_terms and not _has_phrase(low, delegate_terms):
            issues.append("factory_message_semantics:erp_release_delegate_missing")
        if slots.get("completed") and not _has_phrase(low, ("sudah", "telah")):
            issues.append("factory_message_semantics:erp_release_completed_aspect_missing")
        if slots.get("priority") and not _has_phrase(low, (
            "terlebih dahulu", "dulu", "prioritas", "diprioritaskan",
        )):
            issues.append("factory_message_semantics:erp_release_priority_missing")
        if slots.get("repeat") and not _has_phrase(low, ("lagi", "sekali lagi")):
            issues.append("factory_message_semantics:erp_release_repeat_missing")

    elif frame.get("kind") == "id_zh_shift_process_status":
        shift_target = str(slots.get("shift_target") or "")
        if shift_target and shift_target not in target:
            issues.append("factory_message_semantics:shift_actor_missing")
        if any(term in target for term in ("早上好", "早安", "上午好")):
            issues.append("factory_message_semantics:shift_mistranslated_as_greeting")
        if not any(term in target for term in ("噴漆", "塗裝", "喷漆", "涂装")):
            issues.append("factory_message_semantics:spray_painting_process_missing")
        if not _NEGATED_PAINT_ZH_RE.search(target):
            issues.append("factory_message_semantics:process_negation_missing")
        if any(term in target for term in (
            "提供油漆", "提供漆", "供應油漆", "供应油漆", "油漆顏色", "油漆颜色",
        )):
            issues.append("factory_message_semantics:paint_action_mistranslated_as_supply")

    elif frame.get("kind") == "id_zh_weight_display_relation":
        if slots.get("monitor_term") and not (
            "螢幕" in target and any(term in target for term in ("顯示", "讀值", "數值"))
        ):
            issues.append("factory_message_semantics:monitor_display_relation_missing")
        if slots.get("scale_term") and "天車電子磅秤" not in target:
            issues.append("factory_message_semantics:overhead_crane_scale_term_missing")
        if any(term in target for term in ("滑輪秤", "滑車秤", "捲揚秤")):
            issues.append("factory_message_semantics:literal_pulley_scale_forbidden")
        if not _number_unit_present_zh(target, str(slots.get("monitor_weight") or "")):
            issues.append("factory_message_semantics:monitor_weight_missing")
        if not _number_unit_present_zh(target, str(slots.get("scale_weight") or "")):
            issues.append("factory_message_semantics:scale_weight_missing")
        difference = str(slots.get("difference") or "")
        if difference and not (
            _number_unit_present_zh(target, difference)
            and any(term in target for term in ("相差", "差距", "差了", "差異"))
        ):
            issues.append("factory_message_semantics:weight_difference_relation_missing")
        if slots.get("monitor_weight") and slots.get("scale_weight"):
            monitor = re.escape(str(slots.get("monitor_weight")))
            scale = re.escape(str(slots.get("scale_weight")))
            relation_ok = bool(
                re.search(rf"螢幕.{{0,35}}{monitor}.{{0,80}}天車電子磅秤.{{0,35}}{scale}", target, re.S)
                or re.search(rf"天車電子磅秤.{{0,35}}{scale}.{{0,80}}螢幕.{{0,35}}{monitor}", target, re.S)
            )
            if not relation_ok:
                issues.append("factory_message_semantics:weight_readings_attached_to_wrong_devices")
        if slots.get("report_term"):
            if not any(term in target for term in ("回報", "報告", "通報")):
                issues.append("factory_message_semantics:report_action_missing")
            if slots.get("first_person") and "我" not in target:
                issues.append("factory_message_semantics:first_person_reporter_missing")
        if slots.get("leader_id_relation"):
            if "班長" not in target or not re.search(r"(?<![A-Za-z])ID(?![A-Za-z])", target, re.I):
                issues.append("factory_message_semantics:leader_id_relation_missing")
        if re.search(r"\b(?:ketu(?:a)?\s+kelas|ketua\s+(?:shift|regu)|kepala\s+(?:shift|regu))\b", target, re.I):
            issues.append("factory_message_semantics:untranslated_leader_role")

    elif frame.get("kind") == "zh_id_motion_inspection_relation":
        low = _norm(target)
        if slots.get("first_person") and not _has_phrase(low, ("saya", "aku")):
            issues.append("factory_message_semantics:first_person_actor_missing")
        if slots.get("completed") and not _has_phrase(low, ("sudah", "telah")):
            issues.append("factory_message_semantics:completed_aspect_missing")
        if slots.get("future") and not _has_phrase(low, ("akan",)):
            issues.append("factory_message_semantics:future_modality_missing")
        if slots.get("later") and not _has_phrase(low, ("nanti", "kemudian")):
            issues.append("factory_message_semantics:later_timing_missing")
        movement_ok = _has_phrase(low, (
            "ke sana", "pergi ke sana", "menuju ke sana", "ke lokasi",
            "pergi ke lokasi", "menuju lokasi", "ke lapangan", "pergi ke lapangan",
        ))
        if not movement_ok:
            issues.append("factory_message_semantics:movement_to_location_missing")
        inspection_ok = _has_phrase(low, (
            "mengecek", "memeriksa", "meninjau", "melihat", "mencari tahu",
        ))
        if not inspection_ok:
            issues.append("factory_message_semantics:inspection_action_missing")
        obj = slots.get("object")
        if obj == "machine" and not _has_phrase(low, ("mesin", "peralatan")):
            issues.append("factory_message_semantics:machine_object_missing")
        elif obj == "material" and not _has_phrase(low, ("material", "bahan")):
            issues.append("factory_message_semantics:material_object_missing")
        elif obj in ("situation", "implicit_situation") and not _has_phrase(
            low, ("situasi", "situasinya", "kondisi", "kondisinya", "keadaan")
        ):
            issues.append("factory_message_semantics:situation_object_missing")

    return not issues, list(dict.fromkeys(issues))


def translate_source_directly(source: str, src_lang: str, tgt_lang: str) -> str:
    """Translate a complete relation frame before TM, NMT or an LLM call."""
    frame = build_frame(source, src_lang, tgt_lang)
    translated = deterministic_translation(frame)
    if not translated:
        return ""
    ok, _issues = validate_translation(frame, translated)
    return translated if ok else ""


def build_prompt(frame: Mapping) -> str:
    if not frame or not frame.get("active"):
        return ""
    lines = ["<factory_message_source_relations>"]
    lines.append(
        "Translate from the source claims below. Preserve actor, action, movement, destination, "
        "equipment, reading-to-device attachment, comparison/difference, unit, reporting recipient "
        "and ID ownership as linked relations; keyword presence alone is insufficient."
    )
    for claim in frame.get("claims") or ():
        lines.append(
            "Claim {claim_id}: source={source_evidence}; meaning={meaning}; target={required_target}.".format(
                **claim
            )
        )
    if frame.get("kind") == "id_zh_equipment_code_failure":
        lines.append(
            "A source code such as I15 is an equipment/station identifier. Rusak predicates a "
            "functional machine failure: translate the linked claim as I15 機台故障, not as "
            "material/surface damage (損傷) and not as the underspecified I15 損壞."
        )
    elif frame.get("kind") == "zh_id_machine_guard_safety":
        lines.append(
            "This is a machine-guard safety relation. 設備護網/護網/護罩 denotes the "
            "engineering guard on a machine; use natural Indonesian 'pelindung mesin' or "
            "'peralatan pengaman mesin', never 'pelindung jaring peralatan'. When a later "
            "clause says 多台設備沒蓋好, the omitted subject is still the guards attached "
            "to those machines: say that pelindung pada beberapa mesin belum dipasang or "
            "ditutup kembali dengan benar. Never say that several machines themselves were "
            "not closed. In a reminder request, make the omitted duty explicit: remind staff "
            "to reinstall/close the machine guard properly. In a discipline clause, 鬆懈 is "
            "lengah/lalai, not physical longgar. Preserve the source modality exactly: 不會 "
            "is a future-negative statement (tidak akan), while 不要 is a prohibition (jangan)."
        )
    elif frame.get("kind") == "zh_id_attendance_vehicle_departure":
        lines.append(
            "This is a short attendance/vehicle-departure event. In 開車走了, the omitted "
            "actor is a person who leaves by car; 車 is the object/means of driving. Never "
            "promote mobil/kendaraan to the departing actor as in 'kendaraan berangkat'. "
            "Keep the attendance timing (setelah/sebelum/saat pengecekan kehadiran), any "
            "explicit person, "
            "modality and source emoji. Use lebih dulu/lebih dahulu only when the Chinese "
            "source explicitly contains 先; do not infer priority from 走了. For a source "
            "with no explicit person, a subject-neutral Indonesian chat clause is safer "
            "than inventing dia/mereka."
        )
    elif frame.get("kind") == "zh_id_production_backlog_priority":
        lines.append(
            "This is a production-planning relation. Render the process and small-bar material "
            "as a natural Indonesian phrase such as 'material batang berukuran kecil untuk proses "
            "polishing', never the word stack 'material tunda batang kecil polishing'. The first "
            "claim says shipping was not completed on time during the stated recent period, which "
            "created a large backlog. The priority clause names two independent eligible groups: "
            "(1) material whose note has a blue background in the system, and (2) material whose "
            "delivery schedule is in each extracted month. Use 'serta material' (or an equally "
            "explicit repeated noun) so the two selectors are not collapsed into one intersection. "
            "Use Indonesian month names for numeric source months."
        )
    elif frame.get("kind") == "zh_id_erp_data_release":
        lines.append(
            "This is an ERP production-data release relation. In bare factory shorthand, a "
            "bundle/batch/work-order reference plus 放/放一下 means release the linked data to "
            "the next station. Indonesian must explicitly say release data ke stasiun berikutnya "
            "and preserve the referenced bundel/batch/work order, request modality, delegate and "
            "completion/priority aspect. Never use meletakkan, menaruh, taruh, menempatkan, "
            "menyimpan or melepaskan for this sense. Spatial/capacity wording and QC actors are "
            "classified separately and do not use this data-release frame."
        )
    elif frame.get("kind") == "id_zh_weight_display_relation":
        lines.append(
            "In this factory context timbangan katrol/gantung/crane is the overhead-crane electronic "
            "scale: translate it as 天車電子磅秤, never 滑輪秤. Ketu/ketua kelas beside report+ID "
            "means the shift leader: 班長; do not leave Indonesian role words in Chinese."
        )
    elif frame.get("kind") == "id_zh_shift_process_status":
        lines.append(
            "In this shop-floor clause, sip/sif/shif before pagi/siang/sore/malam is a phonetic "
            "spelling of shift, not the acknowledgement sip and not a greeting. Pagi is therefore "
            "早班, not 早上好. Mengasih/memberi warna cat under a negated shift status describes "
            "performing spray-painting work; translate the linked claim as 班別沒有噴漆, never as "
            "not supplying/providing a paint colour."
        )
    elif frame.get("kind") == "zh_id_motion_inspection_relation":
        lines.append(
            "Chinese 過去/到現場 is an explicit movement to another location. Indonesian must contain "
            "ke sana/ke lokasi (or an equivalent movement phrase) as well as the inspection purpose; "
            "Saya lihat situasinya alone is incomplete."
        )
    lines.append("</factory_message_source_relations>")
    return " ".join(lines)


def health() -> dict:
    equipment_failure = build_frame("i15 rusak", "id", "zh")
    shift_paint = build_frame(
        "Sip pagi tida mengasih warna cat", "id", "zh"
    )
    discrepancy = build_frame(
        "Kg di layar monitor dengan di timbangan katrol selisih 6 kg. "
        "Saya laporan dengan id Ketu kelas",
        "id", "zh",
    )
    readings = build_frame(
        "Di layar monitor 995 kg sedangkan di timbangan gantung 989 kg",
        "id", "zh",
    )
    movement = build_frame("我過去了了解看看", "zh", "id")
    data_release = build_frame(
        "@小麥（研磨股班長） 這把麻煩他們放一下", "zh", "id"
    )
    production_priority_source = (
        "@All 拋光小棒這兩個月來不及出貨的遞延料很多，"
        "系統上藍底備註跟交期6、7月的料優先生產"
    )
    production_priority = build_frame(
        production_priority_source, "zh", "id"
    )
    production_priority_target = (
        "@All Dalam dua bulan terakhir, banyak material batang berukuran kecil "
        "untuk proses polishing yang tertunda karena tidak sempat dikirim tepat waktu. "
        "Prioritaskan produksi material yang catatannya berlatar biru di sistem serta "
        "material dengan jadwal pengiriman bulan Juni dan Juli."
    )
    reversed_readings = (
        "995 kg di layar monitor, 989 kg di timbangan gantung elektronik"
    )
    current_values = (
        "Monitor menunjukkan 1000 kg, sedangkan timbangan gantung elektronik "
        "994 kg. Saya sudah lapor pakai ID ketua regu."
    )
    machine_guard_source = (
        "@All 點名不會太早離開，注意紀律不要太鬆懈，設備護網要隨手蓋上，"
        "剛剛被提醒多台設備沒蓋好"
    )
    machine_guard_target = (
        "@All Saat pengecekan kehadiran, kita tidak akan meninggalkan tempat terlalu awal. "
        "Tetap jaga kedisiplinan dan jangan lengah. Setelah menggunakan mesin, segera "
        "pasang kembali pelindung mesin. Saya baru saja diingatkan bahwa pelindung "
        "pada beberapa mesin belum dipasang kembali dengan benar."
    )
    machine_guard = build_frame(machine_guard_source, "zh", "id")
    guard_reminder_source = "@法比恩 Fabian 設備護網幫忙提醒一下"
    guard_reminder_target = (
        "@法比恩 Fabian Mohon bantu ingatkan agar pelindung mesin dipasang kembali "
        "dengan benar."
    )
    vehicle_departure_source = "點名開車走了👋"
    vehicle_departure_target = (
        "Setelah pengecekan kehadiran selesai, berangkat dengan mobil. 👋"
    )
    vehicle_departure_bad = (
        "Setelah absensi, kendaraan berangkat lebih dulu."
    )
    vehicle_departure = build_frame(
        vehicle_departure_source, "zh", "id"
    )
    controls = (
        build_frame("Sip, terima kasih.", "id", "zh"),
        build_frame("Selamat pagi, Pak.", "id", "zh"),
        build_frame("Tolong memberi warna cat biru.", "id", "zh"),
        build_frame("Saya ketua kelas di sekolah.", "id", "zh"),
        build_frame("Katrol rusak.", "id", "zh"),
        build_frame("我先看看情況。", "zh", "id"),
        build_frame("我過去拿工具。", "zh", "id"),
        build_frame("這把刀麻煩他們放在架上。", "zh", "id"),
        build_frame("這把材料放不下，先放照片裡的位置。", "zh", "id"),
        build_frame("品保檢驗後有放行。", "zh", "id"),
        build_frame("請他們放下工具。", "zh", "id"),
        build_frame("網路設備幫忙提醒一下。", "zh", "id"),
        build_frame("點名後車輛開走了", "zh", "id"),
        build_frame("點名開車的人到了", "zh", "id"),
    )
    checks = [
        equipment_failure.get("active") is True
        and equipment_failure.get("complete") is True,
        translate_source_directly("i15 rusak", "id", "zh") == "I15 機台故障",
        validate_translation(equipment_failure, "i15 損壞")[0] is False,
        shift_paint.get("active") is True and shift_paint.get("complete") is True,
        translate_source_directly(shift_paint["source"], "id", "zh")
        == "早班沒有噴漆",
        normalize_indonesian_factory_colloquialisms(shift_paint["source"])[0]
        == "shift pagi tidak melakukan pengecatan semprot",
        discrepancy.get("active") is True and discrepancy.get("complete") is True,
        translate_source_directly(
            discrepancy["source"], "id", "zh"
        ) == "螢幕顯示的重量與天車電子磅秤相差 6 公斤。我用班長的 ID 回報。",
        readings.get("active") is True and readings.get("complete") is True,
        translate_source_directly(
            readings["source"], "id", "zh"
        ) == "螢幕顯示 995 公斤，而天車電子磅秤顯示 989 公斤。",
        movement.get("active") is True and movement.get("complete") is True,
        translate_source_directly("我過去了了解看看", "zh", "id")
        == "Saya ke sana dulu untuk mengecek situasinya.",
        data_release.get("active") is True and data_release.get("complete") is True,
        translate_source_directly(data_release["source"], "zh", "id")
        == (
            "@小麥（研磨股班長） Tolong minta mereka release data untuk bundel ini "
            "ke stasiun berikutnya."
        ),
        validate_translation(
            data_release,
            "@小麥 Tolong minta mereka meletakkan bundel ini.",
        )[0] is False,
        production_priority.get("active") is True
        and production_priority.get("complete") is True,
        translate_source_directly(
            production_priority_source, "zh", "id"
        ) == production_priority_target,
        validate_translation(
            production_priority,
            "@All Material tunda batang kecil polishing yang belum sempat dikirim "
            "dalam dua bulan ini banyak. Prioritaskan produksi material dengan catatan "
            "latar biru di sistem dan tanggal pengiriman bulan 6 dan 7.",
        )[0] is False,
        translate_source_directly(reversed_readings, "id-ID", "zh-TW")
        == "螢幕顯示 995 公斤，而天車電子磅秤顯示 989 公斤。",
        translate_source_directly(current_values, "ind", "zh-Hant")
        == "螢幕顯示 1000 公斤，而天車電子磅秤顯示 994 公斤。我已用班長的 ID 回報。",
        translate_source_directly("我過去了了解看看", "zh-TW", "id-ID")
        == "Saya ke sana dulu untuk mengecek situasinya.",
        translate_source_directly(
            discrepancy["source"] + " Besok mesin dihentikan.", "id", "zh"
        ) == "",
        translate_source_directly(readings["source"] + " 77", "id", "zh") == "",
        translate_source_directly(
            "Di layar monitor 995,5 kg sedangkan di timbangan katrol 989,25 kg",
            "id", "zh",
        ) == "螢幕顯示 995,5 公斤，而天車電子磅秤顯示 989,25 公斤。",
        translate_source_directly("我會過去了解看看", "zh", "id")
        == "Saya akan pergi ke sana terlebih dahulu untuk mengecek situasinya.",
        validate_translation(
            build_frame("我會過去了解看看", "zh", "id"),
            "Saya pergi ke sana untuk mengecek situasinya.",
        )[0] is False,
        validate_translation(
            discrepancy,
            "螢幕上的公斤數與滑輪秤相差 6 kg。我已用 Ketu kelas 的 ID 回報。",
        )[0] is False,
        validate_translation(movement, "Saya lihat dulu situasinya.")[0] is False,
        machine_guard.get("active") is True
        and machine_guard.get("complete") is True,
        translate_source_directly(machine_guard_source, "zh", "id")
        == machine_guard_target,
        validate_translation(machine_guard, machine_guard_target)[0] is True,
        validate_translation(
            machine_guard,
            "@All Saat absen, jangan pulang terlalu cepat. Perhatikan disiplin dan "
            "jangan terlalu longgar. Tutup kembali pelindung mesin setelah digunakan. "
            "Baru saja diingatkan bahwa beberapa mesin tidak ditutup dengan benar.",
        )[0] is False,
        translate_source_directly(guard_reminder_source, "zh", "id")
        == guard_reminder_target,
        validate_translation(
            build_frame(guard_reminder_source, "zh", "id"),
            "@法比恩 Fabian Mohon bantu mengingatkan tentang pelindung jaring peralatan.",
        )[0] is False,
        translate_source_directly(
            "@All 點名不要太早離開，設備護網要蓋好", "zh", "id"
        ).startswith(
            "@All Saat pengecekan kehadiran, jangan meninggalkan tempat terlalu awal."
        ),
        vehicle_departure.get("active") is True
        and vehicle_departure.get("complete") is True,
        translate_source_directly(vehicle_departure_source, "zh", "id")
        == vehicle_departure_target,
        validate_translation(
            vehicle_departure, vehicle_departure_target
        )[0] is True,
        validate_translation(
            vehicle_departure, vehicle_departure_bad
        )[0] is False,
        translate_source_directly(
            "我點名後開車離開了", "zh", "id"
        ) == (
            "Setelah pengecekan kehadiran selesai, saya sudah meninggalkan "
            "lokasi dengan mobil."
        ),
        translate_source_directly(
            "點完名他先開車回家了", "zh", "id"
        ) == (
            "Setelah pengecekan kehadiran selesai, dia sudah pulang lebih "
            "dahulu dengan mobil."
        ),
        all(not frame.get("active") for frame in controls),
    ]
    return {
        "api_version": FACTORY_MESSAGE_SEMANTICS_API_VERSION,
        "build_id": FACTORY_MESSAGE_SEMANTICS_BUILD_ID,
        "self_test": {"ok": all(checks), "checks": len(checks)},
    }
