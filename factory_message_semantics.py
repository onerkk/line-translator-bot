"""Bidirectional source-relation semantics for factory chat translation.

Glossary enforcement can prove that isolated words and numbers are present, but
it cannot prove that the target keeps their source roles.  This module extracts
small, compositional source frames for relations that are especially dangerous
on the shop floor: equipment-to-reading comparisons, reporting with a leader's
ID, movement-to-a-location followed by inspection, short attendance/departure
events whose omitted human actor must not be replaced by a vehicle, supervisory
alerts whose organization/location nouns stand for people, and machine-guard
safety instructions whose omitted Chinese subjects must remain attached to the
guard.

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


FACTORY_MESSAGE_SEMANTICS_API_VERSION = 3
FACTORY_MESSAGE_SEMANTICS_BUILD_ID = "2026-08-31.1-operational-discourse-and-flow"

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

# Short Indonesian shop-floor reports frequently omit prepositions and use
# colloquial spellings.  Keep the machine identity, leak relation and shift
# actor attached to the right event instead of accepting a merely word-level
# rendering.
_MACHINE_OIL_ID_RE = re.compile(
    r"(?<![a-z])(?:(?:minyak|oli)\s+mesin|oli)(?![a-z])",
    re.I,
)
_OIL_LEAK_ID_RE = re.compile(
    r"(?<![a-z])(?:menetes|netes|tetes|bocor|merembes|rembes)(?![a-z])",
    re.I,
)
_NIGHT_SHIFT_PERSON_ID_RE = re.compile(
    r"(?<![a-z])(?:orang|karyawan|operator|personel|pekerja)\s+"
    r"(?:shift\s+)?(?:malam|malem)(?![a-z])",
    re.I,
)
_TRASH_DISPOSAL_ID_RE = re.compile(
    r"(?<![a-z])(?:membuang|buang)\s+(?:sampah|limbah)(?![a-z])",
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

# In terse shop-floor Chinese, ``削皮需要G8G9台車`` is an organization and
# ownership relation.  ``削皮`` is the receiving production section, while G8
# and G9 are two factory-unit abbreviations whose trolleys are being requested.
# A lexical translator tends to read 削皮 as the physical action "peel skin"
# and glue G8G9 onto 台車 as a model/load label.  Parse those roles from the
# source before translation so the compact spelling never loses the unit split
# or reverses the relation.
_ZH_PEELING_RECEIVER_RE = re.compile(
    r"削皮(?:那一站|這一站|这一站|那站|這站|这站|那邊|那边|這邊|这边|"
    r"股|站|部門|部门|區|区)?(?!棒|機|机|作業|作业|製程|制程|加工)",
    re.I,
)
_ZH_FACTORY_UNIT_TROLLEY_RE = re.compile(
    r"(?P<unit_expr>(?<![A-Za-z0-9])G\s*\d{1,2}"
    r"(?:(?:\s*G\s*\d{1,2})|"
    r"(?:\s*(?:、|/|／|,|，|和|與|与|跟|及|&|\+)\s*(?:G\s*)?\d{1,2}))*"
    r")\s*(?:的)?\s*(?P<trolley>台[車车])",
    re.I,
)
_ZH_TROLLEY_NEED_RE = re.compile(
    r"需要|需用|缺少|想借|要借|借用|需|要|缺",
    re.I,
)
_ZH_TROLLEY_REQUEST_RE = re.compile(
    r"(?:麻煩|麻烦|拜託|拜托)(?:幫忙|帮忙|協助|协助|支援)?(?:一下|了|啦|喔|哦)?"
    r"|(?:請|请)(?:幫忙|帮忙|協助|协助|支援)?(?:一下)?"
    r"|(?:幫忙|帮忙|協助|协助|支援)(?:一下)?",
    re.I,
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

# Chinese shop-floor chat routinely omits 人/人員 when the surrounding syntax
# already makes a human actor obvious.  Two high-impact examples are
# ``抓到二股滑手機`` (someone from the section was caught using a phone; the
# section itself did not use it) and ``點名進來了`` (the attendance checker
# entered; the abstract attendance procedure did not start).  A glossary cannot
# solve either relation: forcing 二股 and 點名 to their canonical nouns actually
# makes a fluent role swap more likely.  These maps and clause parsers resolve
# the actor before translation and are deliberately compositional across roles,
# units, conduct, movement, timing, modality and alert recipients.
_ZH_SUPERVISOR_ROLE_ID = {
    "冷抽一股股長": "kepala bagian Cold Drawing 1",
    "冷抽二股股長": "kepala bagian Cold Drawing 2",
    "一股股長": "kepala bagian Cold Drawing 1",
    "二股股長": "kepala bagian Cold Drawing 2",
    "削皮股股長": "kepala bagian Peeling",
    "研磨股股長": "kepala bagian Grinding",
    "處長": "kepala divisi",
    "处长": "kepala divisi",
    "課長": "kepala seksi",
    "课长": "kepala seksi",
    "股長": "kepala bagian",
    "股长": "kepala bagian",
    "班長": "kepala regu",
    "班长": "kepala regu",
    "主管": "atasan",
}
_ZH_FACTORY_UNIT_ID = {
    "冷抽一股": "Bagian Cold Drawing 1",
    "第一股": "Bagian Cold Drawing 1",
    "一股": "Bagian Cold Drawing 1",
    "冷抽二股": "Bagian Cold Drawing 2",
    "第二股": "Bagian Cold Drawing 2",
    "二股": "Bagian Cold Drawing 2",
    "削皮股": "Bagian Peeling",
    "研磨股": "Bagian Grinding",
    "一課": "Seksi 1",
    "一课": "Seksi 1",
}
_ZH_HUMAN_CONDUCT_ID = {
    "使用手機": "menggunakan ponsel",
    "使用手机": "menggunakan ponsel",
    "滑手機": "menggunakan ponsel",
    "滑手机": "menggunakan ponsel",
    "玩手機": "menggunakan ponsel",
    "玩手机": "menggunakan ponsel",
    "看手機": "menggunakan ponsel",
    "看手机": "menggunakan ponsel",
    "睡覺": "tidur",
    "睡觉": "tidur",
    "抽菸": "merokok",
    "抽烟": "merokok",
    "吸菸": "merokok",
    "吸烟": "merokok",
    "聊天": "mengobrol",
    "休息": "beristirahat",
}
_ZH_ROLE_PATTERN = "(?:" + "|".join(
    re.escape(term)
    for term in sorted(_ZH_SUPERVISOR_ROLE_ID, key=len, reverse=True)
) + ")"
_ZH_UNIT_PATTERN = "(?:" + "|".join(
    re.escape(term)
    for term in sorted(_ZH_FACTORY_UNIT_ID, key=len, reverse=True)
) + ")"
_ZH_CONDUCT_PATTERN = "(?:" + "|".join(
    re.escape(term)
    for term in sorted(_ZH_HUMAN_CONDUCT_ID, key=len, reverse=True)
) + ")"
_ZH_OBSERVED_CONDUCT_RE = re.compile(
    r"^(?P<recent_before>剛剛|刚刚|剛才|刚才|剛|刚)?"
    r"(?P<observer>" + _ZH_ROLE_PATTERN + r")"
    r"(?P<recent_after>剛剛|刚刚|剛才|刚才|剛|刚)?"
    r"(?P<speech>說|说|表示|提到|告知)?"
    r"(?P<observation>抓到|捉到|逮到|看到|看見|看见|發現|发现|注意到)?"
    r"(?P<unit>" + _ZH_UNIT_PATTERN + r")"
    r"(?:那邊|那边|裡|里|內|内)?(?:的)?"
    r"(?P<person>有人|有人員|有人员|有員工|有员工|有同仁|"
    r"人員|人员|員工|员工|同仁|作業員|作业员|操作員|操作员|的人)?"
    r"(?:在|正在)?(?P<conduct>" + _ZH_CONDUCT_PATTERN + r")"
    r"(?:了|啦|喔|哦)?$",
    re.I,
)
_ZH_SUPERVISOR_MOVEMENT_RE = re.compile(
    r"^(?P<timing_before>晚點|晚点|稍後|稍后|等等|等一下|待會|待会|"
    r"過一會|过一会)?"
    r"(?P<actor>" + _ZH_ROLE_PATTERN + r")?"
    r"(?P<timing_after>晚點|晚点|稍後|稍后|等等|等一下|待會|待会|"
    r"過一會|过一会)?"
    r"(?P<uncertainty>應該|应该|可能|也許|也许|大概|或許|或许)?"
    r"(?P<repeat_before>還|还|再)?(?P<future>會|会)?(?P<repeat_after>再)?"
    r"(?P<motion>到現場|到现场|下來|下来|進來|进来|過來|过来|來|来)"
    r"(?P<inspection>巡視|巡视|巡查|檢查|检查|看看|看一下|看)?"
    r"(?P<aspect>了|啦|喔|哦)?$",
    re.I,
)
_ZH_ATTENDANCE_CHECKER_MOVEMENT_RE = re.compile(
    r"^(?P<timing_before>晚點|晚点|稍後|稍后|等等|等一下|待會|待会)?"
    r"(?:點名|点名)(?:的)?(?:人|人員|人员|同仁|主管|人員)?"
    r"(?P<timing_after>晚點|晚点|稍後|稍后|等等|等一下|待會|待会)?"
    r"(?P<uncertainty>應該|应该|可能|也許|也许|大概)?"
    r"(?P<future>會|会)?(?P<motion>下來|下来|進來|进来|過來|过来|來|来|到了|到)"
    r"(?P<aspect>了|啦|喔|哦)?$",
    re.I,
)
_ZH_SHOPFLOOR_ALERT_RE = re.compile(
    r"^(?P<repeat>再)?(?:麻煩|麻烦|請|请)?"
    r"(?:(?P<notify>通知|告知|提醒)(?P<notify_recipient>現場|现场|大家|同仁|人員|人员))?"
    r"(?P<recipient>現場|现场|大家|同仁|人員|人员)?(?:再|多)?"
    r"(?P<attention>注意|留意|小心|警覺|警觉)(?:一下|一點|一点|些)?$",
    re.I,
)
_ZH_VEHICLE_BACKLOG_RE = re.compile(
    r"^(?P<today>今天|今日)(?:的)?"
    r"(?P<vehicle>車輛|车辆|貨車|货车|卡車|卡车|車|车)"
    r"(?P<volume>很多|太多|不少|非常多)"
    r"(?:(?P<late>來不及|来不及)(?:處理|处理|完成)?"
    r"(?P<defer>延到|延後到|延后到|改到)(?P<tomorrow>明天|明日))?$",
    re.I,
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

# Operational discourse and material-flow relations.  These expressions parse
# variable names, counts, processes and times from the source.  They are not
# sentence replacements: a deterministic rendering is available only when all
# meaningful source text belongs to one of the extracted slots.
_ZH_CUSTOMER_TOKEN = r"[\u3400-\u9fffA-Za-z0-9_.&+\-]{1,32}"
_ZH_CUSTOMER_ITEM = (
    r"(?:" + _ZH_CUSTOMER_TOKEN
    + r"(?:[ \t]+" + _ZH_CUSTOMER_TOKEN + r"){0,5})"
)
_ZH_REMAINING_CUSTOMERS_RE = re.compile(
    r"(?P<evidence>(?:今天|今日)\s*(?:還|还)?\s*(?:只)?\s*"
    r"剩(?:下|餘|余)?\s*(?P<items>" + _ZH_CUSTOMER_ITEM
    + r"(?:\s*(?:、|，|,|/|／|和|與|与|及)\s*" + _ZH_CUSTOMER_ITEM + r")+))",
    re.I,
)
_ZH_NUMBER_TOKEN = r"(?:\d{1,3}|[零〇一二兩两三四五六七八九十]{1,3})"
_ZH_BUNDLE_AT_PROCESS_RE = re.compile(
    r"(?P<evidence>(?P<count>" + _ZH_NUMBER_TOKEN + r")\s*(?:把|捆)\s*"
    r"(?:正|目前|現在|现在)?\s*在\s*"
    r"(?P<process>包裝|包装|拋光|抛光|研磨|削皮|冷抽|矯直|矫直|酸洗))",
    re.I,
)
_ZH_BUNDLE_TO_PROCESS_RE = re.compile(
    r"(?P<evidence>(?P<count>" + _ZH_NUMBER_TOKEN + r")\s*(?:把|捆)\s*"
    r"(?:會|会|將|将)?\s*(?:陸續|陆续|分批|逐步)\s*"
    r"(?P<process>包裝|包装|拋光|抛光|研磨|削皮|冷抽|矯直|矫直|酸洗)\s*"
    r"(?:過去|过去|送過去|送过去|移過去|移过去))",
    re.I,
)
_ZH_PROCESS_LOCATION_ID = {
    "包裝": "bagian packaging", "包装": "bagian packaging",
    "拋光": "bagian polishing", "抛光": "bagian polishing",
    "研磨": "bagian grinding", "削皮": "Bagian Peeling",
    "冷抽": "bagian cold drawing",
    "矯直": "bagian straightening", "矫直": "bagian straightening",
    "酸洗": "bagian pickling",
}
_ZH_EXPLICIT_PROHIBITION_RE = re.compile(
    r"(?:請|请)?(?:不要|別|别|不可|不能|禁止|勿|請勿|请勿).{0,8}"
    r"(?:亂|乱|隨便|随便)",
    re.I,
)
_ZH_CARELESS_DISPOSAL_RE = re.compile(
    r"(?P<evidence>(?:亂|乱|隨便|随便)(?:丟|丢|扔|倒)(?:垃圾)?)",
    re.I,
)
_ZH_CARELESS_MAINTENANCE_RE = re.compile(
    r"(?P<evidence>(?:亂|乱|隨便|随便)(?:維護|维护|處理|处理))",
    re.I,
)
_ZH_AFTER_DRINKING_RE = re.compile(
    r"(?P<evidence>(?:喝完|飲用完|饮用完|喝了以後|喝了以后|喝完以後|喝完以后))",
    re.I,
)
_ZH_NO_SHORT_MATERIAL_RE = re.compile(
    r"(?P<evidence>(?:沒|没|沒有|没有|無|无)(?:有)?(?:短尺料|短尺材料|短尺))",
    re.I,
)
_ZH_CLOCK_TOKEN = r"(?:\d{1,2}|[零〇一二兩两三四五六七八九十]{1,3})點(?:半|\d{1,2}分)?"
_ZH_MONTH_ORDER_PRIORITY_RE = re.compile(
    r"(?P<evidence>各站(?:要|需|務必|务必)?優先生產(?:本月份|本月|這個月|这个月)(?:份)?訂單)",
    re.I,
)
_ZH_MES_STOP_RE = re.compile(
    r"(?P<evidence>(?:今天|今日)(?P<time>" + _ZH_CLOCK_TOKEN
    + r")後MES系統(?:中止|停止|暫停|暂停)(?:服務|服务|運作|运作))",
    re.I,
)
_ZH_CHANGE_DATA_DEADLINE_RE = re.compile(
    r"(?P<evidence>所有(?:的)?(?:異動|异动|變更|变更)資料(?:都)?在"
    r"(?P<time>" + _ZH_CLOCK_TOKEN + r")(?:左右|前)?完成)",
    re.I,
)
_ZH_PACKAGING_SHIPPING_URGENT_RE = re.compile(
    r"(?P<evidence>(?:包裝|包装)(?:出貨|出货)急單(?:再)?(?:麻煩|麻烦|請|请)?"
    r"(?:幫忙|帮忙)?優先處理)",
    re.I,
)
_ZH_SPECIAL_STATION_ROUTE_RE = re.compile(
    r"(?P<evidence>(?:異型站|异型站|異型包裝站|异型包装站)(?:的)?(?:料|材料)"
    r"(?:再)?(?:麻煩|麻烦|請|请)?(?:幫忙|帮忙)?(?:分流|調撥|调拨|轉|转|移)過來)",
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
    value, typo_count = re.subn(
        r"(?<![a-z])malem(?![a-z])", "malam", value, flags=re.I
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


def _strip_zh_operational_tokens(
    source: Any,
    evidence: Iterable[str],
    support_words: Iterable[str] = (),
) -> str:
    """Return source content not represented by an operational frame.

    Exact extracted spans are removed before small grammatical connectors.  An
    unrelated clause therefore remains visible and prevents a partial direct
    translation from silently discarding it.
    """
    # Builders extract their evidence from NFKC-normalized text.  Normalize the
    # source the same way before removing that evidence; otherwise full-width
    # punctuation (for example Chinese comma -> ASCII comma) can make an exact
    # extracted span impossible to remove and falsely mark a complete frame as
    # incomplete.
    value = unicodedata.normalize("NFKC", _MENTION_RE.sub("", str(source or "")))
    for token in sorted(
        [str(item or "") for item in evidence if str(item or "")],
        key=len,
        reverse=True,
    ):
        value = value.replace(token, "", 1)
    for token in sorted(
        {str(item or "") for item in support_words if str(item or "")},
        key=len,
        reverse=True,
    ):
        value = value.replace(token, "")
    return re.sub(
        r"[\s,，、。.!！?？:：;；()（）\[\]{}]+", "", value
    )


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


def _build_id_zh_machine_oil_leak_frame(source: str, frame: dict) -> dict:
    text = _norm(source)
    codes = list(dict.fromkeys(
        match.group(0).upper() for match in _EQUIPMENT_CODE_ID_RE.finditer(text)
    ))
    oil = _MACHINE_OIL_ID_RE.search(text)
    leak = _OIL_LEAK_ID_RE.search(text)
    if not (codes and oil and leak):
        return frame

    unparsed = _MENTION_RE.sub(" ", text)
    unparsed = _EQUIPMENT_CODE_ID_RE.sub(" ", unparsed)
    unparsed = _MACHINE_OIL_ID_RE.sub(" ", unparsed)
    unparsed = _OIL_LEAK_ID_RE.sub(" ", unparsed)
    unparsed = re.sub(
        r"(?<![a-z])(?:mesin|machine|unit|dari|di|pada|ada|dan)(?![a-z])",
        " ", unparsed, flags=re.I,
    )
    unparsed = re.sub(r"[\s,，。.!！?？:：;；()（）\[\]{}]+", " ", unparsed).strip()
    frame["kind"] = "id_zh_machine_oil_leak"
    frame["slots"].update({
        "equipment_codes": codes,
        "oil_source": oil.group(0).casefold(),
        "leak_source": leak.group(0).casefold(),
    })
    frame["unparsed"] = unparsed
    _claim(
        frame, "oil_leak_equipment", ", ".join(codes),
        "I/E/BF 等代碼是漏油的機台識別碼", "、".join(codes) + " 機台",
    )
    _claim(
        frame, "machine_oil_leak", oil.group(0) + " " + leak.group(0),
        "機台正在滴油／漏油，不是只描述一滴機油", "漏油",
    )
    frame["active"] = True
    frame["complete"] = not unparsed
    return frame


def _build_id_zh_night_shift_trash_frame(source: str, frame: dict) -> dict:
    text = _norm(source)
    person = _NIGHT_SHIFT_PERSON_ID_RE.search(text)
    negative = _NEGATIVE_ID_RE.search(text)
    disposal = _TRASH_DISPOSAL_ID_RE.search(text)
    if not (
        person and negative and disposal
        and person.end() <= negative.start() <= disposal.start()
    ):
        return frame

    unparsed = _MENTION_RE.sub(" ", text)
    for match in sorted(
        (person, negative, disposal), key=lambda item: item.start(), reverse=True
    ):
        unparsed = unparsed[:match.start()] + " " + unparsed[match.end():]
    unparsed = re.sub(
        r"(?<![a-z])(?:shift|yang|para|di|bagian)(?![a-z])",
        " ", unparsed, flags=re.I,
    )
    unparsed = re.sub(r"[\s,，。.!！?？:：;；()（）\[\]{}]+", " ", unparsed).strip()
    negative_term = negative.group("negative").casefold()
    frame["kind"] = "id_zh_night_shift_trash_omission"
    frame["slots"].update({
        "shift_actor": "night_shift_staff",
        "negative_source": negative_term,
        "completion": "not_yet" if negative_term == "belum" else "not_done",
        "trash_source": disposal.group(0).casefold(),
    })
    frame["unparsed"] = unparsed
    _claim(
        frame, "night_shift_human_actor", person.group(0),
        "晚班人員是沒有執行倒垃圾的人", "晚班人員",
    )
    _claim(
        frame, "trash_disposal_negation",
        negative.group(0) + " " + disposal.group(0),
        "倒垃圾這項工作未執行；否定不可遺失", "沒有倒垃圾",
    )
    frame["active"] = True
    frame["complete"] = not unparsed
    return frame


def _build_id_zh_frame(source: str, frame: dict) -> dict:
    oil_leak_frame = _build_id_zh_machine_oil_leak_frame(source, frame)
    if oil_leak_frame.get("active"):
        return oil_leak_frame
    trash_frame = _build_id_zh_night_shift_trash_frame(source, frame)
    if trash_frame.get("active"):
        return trash_frame
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


def _factory_unit_codes(unit_expression: str) -> list[str]:
    """Split compact unit spellings such as G8G9, G8/G9 and G8、9."""
    codes: list[str] = []
    for digits in re.findall(r"\d{1,2}", str(unit_expression or "")):
        code = "G" + digits
        if code not in codes:
            codes.append(code)
    return codes


def _format_id_factory_unit_list(codes: Iterable[str]) -> str:
    values = [str(code).upper() for code in codes if str(code)]
    if not values:
        return ""
    if len(values) == 1:
        return "unit " + values[0]
    if len(values) == 2:
        return "unit " + values[0] + " dan " + values[1]
    return "unit " + ", ".join(values[:-1]) + ", dan " + values[-1]


def _strip_zh_unit_trolley_supported_tokens(
    source: str,
    spans: Iterable[tuple[int, int]],
) -> str:
    value = _MENTION_RE.sub("", str(source or ""))
    # The spans were found after mentions had been removed, so delete them from
    # that same visible string, right-to-left to keep offsets stable.
    for start, end in sorted(spans, reverse=True):
        value = value[:start] + value[end:]
    value = _ZH_TROLLEY_REQUEST_RE.sub("", value)
    for token in ("目前", "現在", "现在", "還", "还"):
        value = value.replace(token, "")
    return re.sub(r"[\s,，、。.!！?？:：;；()（）\[\]{}]+", "", value)


def _build_zh_id_factory_unit_trolley_frame(source: str, frame: dict) -> dict:
    """Bind a receiving section to trolleys sourced from G-number units.

    The compact source omits 的 and separators, but those omissions do not turn
    the codes into a trolley model.  A direct rendering is allowed only when all
    non-mention text belongs to the receiver/need/ownership/request relation.
    """
    visible = _MENTION_RE.sub("", str(source or ""))
    receiver_match = _ZH_PEELING_RECEIVER_RE.search(visible)
    if not receiver_match:
        return frame
    trolley_match = _ZH_FACTORY_UNIT_TROLLEY_RE.search(
        visible, receiver_match.end()
    )
    if not trolley_match:
        return frame
    bridge = visible[receiver_match.end():trolley_match.start()]
    if len(_compact(bridge)) > 40:
        return frame
    need_match = _ZH_TROLLEY_NEED_RE.search(bridge)
    if not need_match:
        return frame

    codes = _factory_unit_codes(trolley_match.group("unit_expr"))
    if not codes:
        return frame
    need_span = (
        receiver_match.end() + need_match.start(),
        receiver_match.end() + need_match.end(),
    )
    request = bool(_ZH_TROLLEY_REQUEST_RE.search(visible))
    currently = any(term in bridge for term in ("目前", "現在", "现在"))
    still = any(term in bridge for term in ("還", "还"))
    unparsed = _strip_zh_unit_trolley_supported_tokens(
        source,
        (
            receiver_match.span(),
            need_span,
            trolley_match.span(),
        ),
    )

    frame["kind"] = "zh_id_factory_unit_trolley_request"
    frame["slots"].update({
        "receiver_source": receiver_match.group(0),
        "receiver_id": "Bagian Peeling",
        "need_source": need_match.group(0),
        "owner_unit_expression": trolley_match.group("unit_expr"),
        "owner_unit_codes": codes,
        "trolley_source": trolley_match.group("trolley"),
        "request": request,
        "currently": currently,
        "still": still,
    })
    frame["unparsed"] = unparsed
    _claim(
        frame,
        "trolley_receiving_section",
        receiver_match.group(0),
        "削皮在需要台車的主詞位置，是削皮單位／部門，不是剝除表皮的動作",
        "Bagian Peeling",
    )
    _claim(
        frame,
        "factory_unit_trolley_ownership",
        trolley_match.group(0),
        "G8、G9 是兩個工廠單位簡稱；台車來自這些單位，不是名為 G8G9 的台車",
        "troli dari " + _format_id_factory_unit_list(codes),
    )
    _claim(
        frame,
        "trolley_need_relation",
        need_match.group(0),
        "削皮單位需要台車",
        "Bagian Peeling membutuhkan troli",
    )
    if request:
        _claim(
            frame,
            "trolley_request_modality",
            "麻煩／請／幫忙",
            "請對方協助處理台車需求",
            "Mohon bantuannya",
        )
    frame["active"] = True
    frame["complete"] = bool(not unparsed)
    return frame


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


def _format_id_preserved_name_list(names: Iterable[str]) -> str:
    values = [str(name).strip() for name in names if str(name).strip()]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return values[0] + " dan " + values[1]
    return ", ".join(values[:-1]) + ", dan " + values[-1]


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


def _shopfloor_timing_id(raw: str) -> str:
    value = str(raw or "")
    if value in {"等等", "等一下", "待會", "待会", "過一會", "过一会"}:
        return "sebentar lagi"
    if value:
        return "nanti"
    return ""


def _shopfloor_motion_id(raw: str) -> str:
    value = str(raw or "")
    if value in {"下來", "下来"}:
        return "turun"
    if value in {"進來", "进来"}:
        return "masuk"
    if value in {"到現場", "到现场"}:
        return "datang ke lapangan"
    if value in {"過來", "过来", "來", "来", "到了", "到"}:
        return "datang"
    return ""


def _shopfloor_inspection_id(raw: str) -> str:
    value = str(raw or "")
    if value in {"檢查", "检查"}:
        return "memeriksa keadaan"
    if value in {"巡視", "巡视", "巡查"}:
        return "meninjau keadaan"
    if value:
        return "melihat keadaan"
    return ""


def _build_zh_id_shopfloor_agent_frame(source: str, frame: dict) -> dict:
    """Resolve omitted people behind organization, location and procedure nouns.

    This is a clause compositor, not an exact-sentence table.  Each recognized
    clause contributes typed slots.  A provider receives the frame even when an
    extra clause prevents deterministic rendering, while the local fast path is
    allowed only when every visible clause was consumed.
    """
    segments: list[dict[str, Any]] = []
    unparsed: list[str] = []
    last_supervisor = ""

    for clause in _visible_zh_clauses(source):
        compact = _compact(clause)
        if not compact:
            continue

        attendance = _ZH_ATTENDANCE_CHECKER_MOVEMENT_RE.fullmatch(compact)
        if attendance:
            timing_source = str(
                attendance.group("timing_before")
                or attendance.group("timing_after")
                or ""
            )
            movement_source = str(attendance.group("motion") or "")
            segments.append({
                "type": "attendance_checker_movement",
                "source": clause,
                "timing_source": timing_source,
                "timing_id": _shopfloor_timing_id(timing_source),
                "uncertain": bool(attendance.group("uncertainty")),
                "future": bool(attendance.group("future")),
                "completed": bool(
                    attendance.group("aspect")
                    or movement_source in {"到了"}
                ),
                "movement_source": movement_source,
                "movement_id": _shopfloor_motion_id(movement_source),
            })
            continue

        observed = _ZH_OBSERVED_CONDUCT_RE.fullmatch(compact)
        if observed and (observed.group("speech") or observed.group("observation")):
            observer_source = str(observed.group("observer") or "")
            unit_source = str(observed.group("unit") or "")
            conduct_source = str(observed.group("conduct") or "")
            last_supervisor = observer_source
            segments.append({
                "type": "supervisor_observed_person_conduct",
                "source": clause,
                "observer_source": observer_source,
                "observer_id": _ZH_SUPERVISOR_ROLE_ID.get(observer_source, ""),
                "recent": bool(
                    observed.group("recent_before")
                    or observed.group("recent_after")
                ),
                "reported_speech": bool(observed.group("speech")),
                "observation_source": str(observed.group("observation") or ""),
                "unit_source": unit_source,
                "unit_id": _ZH_FACTORY_UNIT_ID.get(unit_source, ""),
                "person_explicit": bool(observed.group("person")),
                "conduct_source": conduct_source,
                "conduct_id": _ZH_HUMAN_CONDUCT_ID.get(conduct_source, ""),
            })
            continue

        vehicle = _ZH_VEHICLE_BACKLOG_RE.fullmatch(compact)
        if vehicle:
            segments.append({
                "type": "vehicle_backlog_defer",
                "source": clause,
                "vehicle_source": str(vehicle.group("vehicle") or ""),
                "volume_source": str(vehicle.group("volume") or ""),
                "not_in_time": bool(vehicle.group("late")),
                "defer_to_tomorrow": bool(vehicle.group("defer")),
            })
            continue

        movement = _ZH_SUPERVISOR_MOVEMENT_RE.fullmatch(compact)
        if movement:
            explicit_actor = str(movement.group("actor") or "")
            actor_source = explicit_actor or last_supervisor
            # A bare ``晚點可能會下來`` does not identify who will move.  It is
            # only safe when a preceding parsed clause supplied the supervisor.
            if actor_source:
                if explicit_actor:
                    last_supervisor = explicit_actor
                timing_source = str(
                    movement.group("timing_before")
                    or movement.group("timing_after")
                    or ""
                )
                movement_source = str(movement.group("motion") or "")
                inspection_source = str(movement.group("inspection") or "")
                segments.append({
                    "type": "supervisor_movement_inspection",
                    "source": clause,
                    "actor_source": actor_source,
                    "actor_id": _ZH_SUPERVISOR_ROLE_ID.get(actor_source, ""),
                    "actor_inherited": not bool(explicit_actor),
                    "timing_source": timing_source,
                    "timing_id": _shopfloor_timing_id(timing_source),
                    "uncertain": bool(movement.group("uncertainty")),
                    "future": bool(movement.group("future")),
                    "repeat": bool(
                        movement.group("repeat_before")
                        or movement.group("repeat_after")
                    ),
                    "completed": bool(movement.group("aspect")),
                    "movement_source": movement_source,
                    "movement_id": _shopfloor_motion_id(movement_source),
                    "inspection_source": inspection_source,
                    "inspection_id": _shopfloor_inspection_id(inspection_source),
                })
                continue

        alert = _ZH_SHOPFLOOR_ALERT_RE.fullmatch(compact)
        if alert:
            recipient_source = str(
                alert.group("notify_recipient")
                or alert.group("recipient")
                or ""
            )
            # A standalone generic 注意一下 has no recoverable recipient or
            # factory role.  It remains on the ordinary translation path.  It
            # becomes part of this frame only when the same message already
            # established the alert context or the clause names a notification
            # action/recipient itself.
            if not (segments or alert.group("notify") or recipient_source):
                unparsed.append(clause)
                continue
            segments.append({
                "type": "shopfloor_alert",
                "source": clause,
                "notify": bool(alert.group("notify")),
                "recipient_source": recipient_source,
                "shopfloor_recipient": recipient_source in {"現場", "现场"},
                "repeat": bool(alert.group("repeat")),
                "attention_source": str(alert.group("attention") or ""),
            })
            continue

        unparsed.append(clause)

    if not segments:
        return frame

    frame["kind"] = "zh_id_shopfloor_agent_roles"
    frame["slots"].update({
        "segments": segments,
        "has_attendance_checker": any(
            item["type"] == "attendance_checker_movement" for item in segments
        ),
        "has_humanized_unit": any(
            item["type"] == "supervisor_observed_person_conduct"
            for item in segments
        ),
        "has_shopfloor_recipient": any(
            item["type"] == "shopfloor_alert"
            and item.get("shopfloor_recipient")
            for item in segments
        ),
    })
    frame["unparsed"] = " | ".join(unparsed)

    for index, segment in enumerate(segments, start=1):
        segment_type = str(segment.get("type") or "")
        evidence = str(segment.get("source") or "")
        if segment_type == "attendance_checker_movement":
            _claim(
                frame,
                f"attendance_checker_actor_{index}",
                evidence,
                "點名搭配進來／下來等人物移動時，是執行點名的人員移動；不是抽象點名程序開始",
                "petugas pengecekan kehadiran + gerakan masuk/turun/datang",
            )
        elif segment_type == "supervisor_observed_person_conduct":
            _claim(
                frame,
                f"organization_member_actor_{index}",
                evidence,
                "股／課是人員所屬單位；滑手機等人類行為的主詞是該單位的人，不是單位本身",
                "seseorang dari " + str(segment.get("unit_id") or ""),
            )
            _claim(
                frame,
                f"supervisor_observation_{index}",
                evidence,
                "保留主管的說話／目擊角色、時間與被發現的人員行為",
                str(segment.get("observer_id") or "")
                + " memergoki seseorang sedang "
                + str(segment.get("conduct_id") or ""),
            )
        elif segment_type == "supervisor_movement_inspection":
            _claim(
                frame,
                f"supervisor_movement_{index}",
                evidence,
                "移動動作的主詞是明示或前句承接的主管；保留時間、可能性、再次與查看目的",
                str(segment.get("actor_id") or "")
                + " " + str(segment.get("movement_id") or ""),
            )
        elif segment_type == "vehicle_backlog_defer":
            _claim(
                frame,
                f"vehicle_workload_{index}",
                evidence,
                "今天車輛數量多；來不及處理的車輛延到明天，不可把很多直接修飾成未處理",
                "kendaraan hari ini banyak; yang tidak sempat ditangani ditunda sampai besok",
            )
        elif segment_type == "shopfloor_alert":
            _claim(
                frame,
                f"shopfloor_people_alert_{index}",
                evidence,
                "現場在通知／注意語境中指現場人員，不是名為『現場部門』的抽象單位",
                "beri tahu personel di lapangan agar waspada",
            )

    frame["active"] = True
    frame["complete"] = not unparsed
    return frame


def _build_zh_id_customer_order_frame(source: str, frame: dict) -> dict:
    """Resolve a remaining-customer list as the customers' orders/material.

    In production chat, ``今天剩 A、B、C`` does not say that the companies
    themselves remain.  The customer names are metonymic references to their
    remaining orders.  Names stay byte-for-byte unchanged while the omitted
    order relation is made explicit in Indonesian.
    """
    visible = unicodedata.normalize("NFKC", _MENTION_RE.sub("", str(source or "")))
    customer_match = _ZH_REMAINING_CUSTOMERS_RE.search(visible)
    if not customer_match:
        return frame
    names = [
        item.strip()
        for item in re.split(r"\s*(?:、|，|,|/|／|和|與|与|及)\s*", customer_match.group("items"))
        if item.strip()
    ]
    if len(names) < 2:
        return frame

    tail = visible[customer_match.end():]
    system_match = re.search(r"(?:包裝|包装)系統", tail, re.I)
    note_match = re.search(r"(?:備註|备注|註記|注记|標記|标记)", tail, re.I)
    deferred_match = re.search(
        r"(?:遞延料|递延料|遞延材料|递延材料|延遲料|延迟料)", tail, re.I
    )
    action_match = re.search(
        r"(?P<evidence>(?:再)?(?:麻煩|麻烦|請|请)?(?:幫忙|帮忙|協助|协助)?"
        r"(?:把)?(?:這些|这些|該|该)?(?:料|材料)?(?:再)?(?:包裝|包装)"
        r"(?:後|后|再|並|并|然後|然后)?(?:辦理|办理)?(?:入庫|入库))",
        tail,
        re.I,
    )
    # A list after 今天剩 is not automatically a customer/order list.  Require
    # production evidence before enabling the metonymy rule so ordinary lists
    # such as 今天剩蘋果、香蕉 cannot be rewritten as customer orders.
    production_context = bool(
        system_match
        or deferred_match
        or action_match
        or re.search(
            r"(?:訂單|订单|工單|工单|出貨|出货|入庫|入库|包裝|包装|客戶|客户)",
            tail,
            re.I,
        )
    )
    if not production_context:
        return frame
    has_packaging_instruction = bool(
        system_match and note_match and deferred_match and action_match
    )
    evidence = [customer_match.group("evidence")]
    for match in (system_match, note_match, deferred_match, action_match):
        if match:
            evidence.append(match.group("evidence") if "evidence" in match.groupdict() else match.group(0))
    unparsed = _strip_zh_operational_tokens(
        source, evidence,
        ("再", "的", "請", "请", "麻煩", "麻烦", "幫忙", "帮忙"),
    )
    frame["kind"] = "zh_id_remaining_customer_orders"
    frame["slots"].update({
        "customer_names": names,
        "today": True,
        "has_packaging_instruction": has_packaging_instruction,
    })
    frame["unparsed"] = unparsed
    _claim(
        frame, "remaining_customer_order_metonymy", customer_match.group("evidence"),
        "客戶名稱在剩餘生產清單中代指這些客戶尚未完成的訂單，不是公司本身留下",
        "pesanan untuk " + ", ".join(names),
    )
    if has_packaging_instruction:
        _claim(
            frame, "deferred_packaging_system_note",
            system_match.group(0) + note_match.group(0) + deferred_match.group(0),
            "包裝系統內被標記為遞延的材料", "material yang ditandai tertunda di sistem packaging",
        )
        _claim(
            frame, "package_then_warehouse",
            action_match.group("evidence"),
            "先協助包裝，再辦理材料入庫；兩個動作與順序都要保留",
            "tolong kemas lalu masukkan ke gudang",
        )
    frame["active"] = True
    frame["complete"] = bool(has_packaging_instruction and not unparsed)
    return frame


def _build_zh_id_deferred_material_flow_frame(source: str, frame: dict) -> dict:
    visible = unicodedata.normalize("NFKC", _MENTION_RE.sub("", str(source or "")))
    urgent = re.search(
        r"(?P<evidence>(?:下午)?急單(?:差不多|快(?:完成|好了)?|即將完成|即将完成)"
        r"(?:完成|好了)?(?:後|后))",
        visible, re.I,
    )
    deferred = re.search(
        r"(?P<evidence>(?:這份|这份)?(?:上面(?:的)?|上述(?:的)?)?"
        r"(?:遞延料|递延料|遞延材料|递延材料|延遲料|延迟料))",
        visible, re.I,
    )
    request = re.search(
        r"(?P<evidence>(?:再)?(?:麻煩|麻烦|請|请)?(?:幫忙|帮忙|協助|协助)"
        r"(?:安排)?(?:處理|处理)(?:一下)?)",
        visible, re.I,
    )
    current = _ZH_BUNDLE_AT_PROCESS_RE.search(visible)
    destination = _ZH_BUNDLE_TO_PROCESS_RE.search(visible)
    if not (deferred and current and destination):
        return frame

    current_count_raw = current.group("count")
    destination_count_raw = destination.group("count")
    current_count = _parse_zh_release_count(current_count_raw)
    destination_count = _parse_zh_release_count(destination_count_raw)
    current_process = current.group("process")
    destination_process = destination.group("process")
    evidence = [
        match.group("evidence")
        for match in (urgent, deferred, request, current, destination)
        if match
    ]
    unparsed = _strip_zh_operational_tokens(
        source, evidence,
        ("這份", "这份", "上面", "上述", "的", "再", "會", "会", "一下"),
    )
    frame["kind"] = "zh_id_deferred_material_process_flow"
    frame["slots"].update({
        "urgent_nearly_done": bool(urgent),
        "deferred_reference": True,
        "request": bool(request),
        "current_count_raw": current_count_raw,
        "current_count": current_count,
        "current_process_source": current_process,
        "current_process_id": _ZH_PROCESS_LOCATION_ID.get(current_process, ""),
        "destination_count_raw": destination_count_raw,
        "destination_count": destination_count,
        "destination_process_source": destination_process,
        "destination_process_id": _ZH_PROCESS_LOCATION_ID.get(destination_process, ""),
        "gradual_movement": True,
    })
    frame["unparsed"] = unparsed
    if urgent:
        _claim(
            frame, "after_urgent_order_nearly_done", urgent.group("evidence"),
            "下午急單接近完成之後才處理後述遞延料",
            "setelah work order mendesak sore ini hampir selesai",
        )
    _claim(
        frame, "deferred_material_reference", deferred.group("evidence"),
        "指向上方所列的遞延材料", "material tertunda yang tercantum di atas",
    )
    if request:
        _claim(
            frame, "deferred_material_handling_request", request.group("evidence"),
            "請對方安排處理遞延材料", "mohon atur penanganannya",
        )
    _claim(
        frame, "bundles_at_current_process", current.group("evidence"),
        "指定捆數目前位於該製程", (
            _format_id_release_count(current_count, current_count_raw)
            + " bundel berada di " + _ZH_PROCESS_LOCATION_ID.get(current_process, "")
        ),
    )
    _claim(
        frame, "bundles_move_to_process", destination.group("evidence"),
        "指定捆數將分批送往該製程；製程名稱是目的地，不是已完成的被動加工",
        (
            _format_id_release_count(destination_count, destination_count_raw)
            + " bundel akan dikirim secara bertahap ke "
            + _ZH_PROCESS_LOCATION_ID.get(destination_process, "")
        ),
    )
    frame["active"] = True
    frame["complete"] = bool(
        urgent and request and current_count is not None
        and destination_count is not None
        and _ZH_PROCESS_LOCATION_ID.get(current_process)
        and _ZH_PROCESS_LOCATION_ID.get(destination_process)
        and not unparsed
    )
    return frame


def _build_zh_id_careless_action_frame(source: str, frame: dict) -> dict:
    visible = unicodedata.normalize("NFKC", _MENTION_RE.sub("", str(source or "")))
    disposal = _ZH_CARELESS_DISPOSAL_RE.search(visible)
    maintenance = _ZH_CARELESS_MAINTENANCE_RE.search(visible)
    if not (disposal or maintenance):
        return frame
    after_drinking = _ZH_AFTER_DRINKING_RE.search(visible)
    no_short = _ZH_NO_SHORT_MATERIAL_RE.search(visible)
    explicit_prohibition = bool(_ZH_EXPLICIT_PROHIBITION_RE.search(visible))
    action = "trash_disposal" if disposal else "short_material_handling" if no_short else "maintenance_unspecified"
    evidence = [
        match.group("evidence")
        for match in (after_drinking, no_short, disposal, maintenance)
        if match
    ]
    unparsed = _strip_zh_operational_tokens(
        source, evidence,
        (
            "請不要", "请不要", "請勿", "请勿", "不要", "別", "别", "不可",
            "不能", "禁止", "勿", "你", "你們", "你们", "他", "他們", "他们",
            "又", "卻", "却", "反而", "竟然", "還", "还", "就", "了", "啦",
        ),
    )
    frame["kind"] = "zh_id_careless_action_speech_act"
    frame["slots"].update({
        "modality": "prohibition" if explicit_prohibition else "observed_complaint",
        "action": action,
        "after_drinking": bool(after_drinking),
        "no_short_material": bool(no_short),
    })
    frame["unparsed"] = unparsed
    _claim(
        frame, "careless_action_modality",
        (disposal or maintenance).group("evidence"),
        (
            "來源有明確禁止詞，因此是命令"
            if explicit_prohibition
            else "來源是在陳述／抱怨已發生的隨意行為，沒有禁止詞；不可擅自改成 jangan 命令"
        ),
        "jangan" if explicit_prohibition else "declarative complaint; no jangan",
    )
    if action == "trash_disposal":
        _claim(
            frame, "careless_disposal_action", disposal.group("evidence"),
            "飲用後物品被隨意丟棄", "dibuang sembarangan",
        )
    elif action == "short_material_handling":
        _claim(
            frame, "short_material_handling_action", maintenance.group("evidence"),
            "明明沒有短尺材料，卻隨意執行短尺材料處理；不是設備 maintenance",
            "penanganan material pendek dilakukan sembarangan",
        )
    frame["active"] = True
    frame["complete"] = bool(
        not unparsed
        and (
            (action == "trash_disposal" and after_drinking)
            or (action == "short_material_handling" and no_short)
        )
    )
    return frame


def _parse_zh_clock(value: str) -> str:
    raw = str(value or "").strip()
    match = re.fullmatch(
        r"(?P<hour>\d{1,2}|[零〇一二兩两三四五六七八九十]{1,3})點"
        r"(?:(?P<half>半)|(?P<minute>\d{1,2})分)?",
        raw,
    )
    if not match:
        return ""
    hour = _parse_zh_release_count(match.group("hour"))
    if hour is None or not 0 <= hour <= 23:
        return ""
    minute = 30 if match.group("half") else int(match.group("minute") or 0)
    if not 0 <= minute <= 59:
        return ""
    return f"{hour}.{minute:02d}"


def _build_zh_id_mes_operational_notice_frame(source: str, frame: dict) -> dict:
    visible = _compact(_MENTION_RE.sub("", str(source or "")))
    priority = _ZH_MONTH_ORDER_PRIORITY_RE.search(visible)
    blue_attention = re.search(
        r"(?P<evidence>(?P<marker>藍色底|蓝色底|藍底|蓝底|藍色底色|蓝色底色)"
        r"(?:的)?(?:訂單|订单)?(?:要|需)?(?:特別|特别)?注意)",
        visible,
        re.I,
    )
    blue = blue_attention.group("marker") if blue_attention else ""
    mes_stop = _ZH_MES_STOP_RE.search(visible)
    deadline = _ZH_CHANGE_DATA_DEADLINE_RE.search(visible)
    urgent = _ZH_PACKAGING_SHIPPING_URGENT_RE.search(visible)
    route = _ZH_SPECIAL_STATION_ROUTE_RE.search(visible)
    matched_segments = sum(bool(item) for item in (priority, mes_stop, deadline, urgent, route))
    if matched_segments < 2:
        return frame
    evidence = [
        match.group("evidence")
        for match in (priority, mes_stop, deadline, urgent, route)
        if match
    ]
    if blue_attention:
        evidence.append(blue_attention.group("evidence"))
    unparsed = visible
    for token in sorted(evidence, key=len, reverse=True):
        unparsed = unparsed.replace(_compact(token), "", 1)
    unparsed = re.sub(r"[、,，。.!！?？:：;；]", "", unparsed)
    stop_time = _parse_zh_clock(mes_stop.group("time") if mes_stop else "")
    deadline_time = _parse_zh_clock(deadline.group("time") if deadline else "")
    frame["kind"] = "zh_id_mes_operational_notice"
    frame["slots"].update({
        "monthly_order_priority": bool(priority),
        "blue_background_attention": bool(blue),
        "mes_stop": bool(mes_stop),
        "mes_stop_time": stop_time,
        "change_data_deadline": bool(deadline),
        "change_data_deadline_time": deadline_time,
        "packaging_shipping_urgent": bool(urgent),
        "special_station_route": bool(route),
    })
    frame["unparsed"] = unparsed
    if priority:
        _claim(
            frame, "monthly_order_production_priority", priority.group("evidence"),
            "各站必須優先生產本月訂單", "semua stasiun memprioritaskan produksi pesanan bulan ini",
        )
    if blue:
        _claim(
            frame, "blue_background_attention", blue,
            "藍色背景的訂單需要特別注意", "pesanan berlatar biru perlu diperhatikan khusus",
        )
    if mes_stop:
        _claim(
            frame, "mes_service_stop", mes_stop.group("evidence"),
            "MES 系統在指定時間後停止服務", f"sistem MES berhenti beroperasi setelah pukul {stop_time}",
        )
    if deadline:
        _claim(
            frame, "change_data_completion_deadline", deadline.group("evidence"),
            "所有異動資料須在指定時間左右完成", f"semua perubahan data diselesaikan sekitar pukul {deadline_time}",
        )
    if urgent:
        _claim(
            frame, "packaging_shipping_urgent_priority", urgent.group("evidence"),
            "包裝與出貨急單要優先處理", "prioritaskan work order mendesak untuk packaging dan pengiriman",
        )
    if route:
        _claim(
            frame, "special_station_material_route", route.group("evidence"),
            "把異型站的材料分流到說話者所在位置", (
                "alihkan material dari Stasiun packing barang bentuk khusus ke sini"
            ),
        )
    frame["active"] = True
    frame["complete"] = bool(
        priority and blue and mes_stop and stop_time and deadline and deadline_time
        and urgent and route and not unparsed
    )
    return frame


def _build_zh_id_frame(source: str, frame: dict) -> dict:
    customer_frame = _build_zh_id_customer_order_frame(source, frame)
    if customer_frame.get("active"):
        return customer_frame
    flow_frame = _build_zh_id_deferred_material_flow_frame(source, frame)
    if flow_frame.get("active"):
        return flow_frame
    careless_frame = _build_zh_id_careless_action_frame(source, frame)
    if careless_frame.get("active"):
        return careless_frame
    mes_frame = _build_zh_id_mes_operational_notice_frame(source, frame)
    if mes_frame.get("active"):
        return mes_frame
    unit_trolley_frame = _build_zh_id_factory_unit_trolley_frame(source, frame)
    if unit_trolley_frame.get("active"):
        return unit_trolley_frame
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
    shopfloor_agent_frame = _build_zh_id_shopfloor_agent_frame(source, frame)
    if shopfloor_agent_frame.get("active"):
        return shopfloor_agent_frame
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
    if frame.get("kind") == "id_zh_machine_oil_leak":
        codes = [str(item) for item in slots.get("equipment_codes") or () if str(item)]
        if not codes:
            return ""
        return _with_mentions(frame, f"{'、'.join(codes)} 機台漏油")

    if frame.get("kind") == "id_zh_night_shift_trash_omission":
        status = "還沒倒垃圾" if slots.get("completion") == "not_yet" else "沒有倒垃圾"
        return _with_mentions(frame, "晚班人員" + status)

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

    if frame.get("kind") == "zh_id_factory_unit_trolley_request":
        receiver = str(slots.get("receiver_id") or "")
        unit_list = _format_id_factory_unit_list(
            slots.get("owner_unit_codes") or ()
        )
        if not receiver or not unit_list:
            return ""
        subject = receiver
        if slots.get("currently"):
            subject = "Saat ini, " + subject
        predicate = "masih membutuhkan" if slots.get("still") else "membutuhkan"
        text = f"{subject} {predicate} troli dari {unit_list}."
        if slots.get("request"):
            text += " Mohon bantuannya."
        return _with_mentions(frame, text)

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

    if frame.get("kind") == "zh_id_shopfloor_agent_roles":
        rendered: list[str] = []
        observation_verbs = {
            "抓到": "memergoki", "捉到": "memergoki", "逮到": "memergoki",
            "看到": "melihat", "看見": "melihat", "看见": "melihat",
            "發現": "mendapati", "发现": "mendapati", "注意到": "melihat",
        }

        def _sentence(value: str) -> str:
            clean = re.sub(r"\s+", " ", str(value or "")).strip()
            if not clean:
                return ""
            return clean[:1].upper() + clean[1:].rstrip(". ") + "."

        for segment in slots.get("segments") or ():
            segment_type = str(segment.get("type") or "")
            if segment_type == "attendance_checker_movement":
                parts: list[str] = []
                timing_id = str(segment.get("timing_id") or "")
                if timing_id:
                    parts.append(timing_id + ",")
                parts.append("petugas pengecekan kehadiran")
                if segment.get("completed"):
                    parts.append("sudah")
                if segment.get("uncertain"):
                    parts.append("mungkin")
                if segment.get("future"):
                    parts.append("akan")
                parts.append(str(segment.get("movement_id") or "datang"))
                rendered.append(_sentence(" ".join(parts)))
            elif segment_type == "supervisor_observed_person_conduct":
                observer = str(segment.get("observer_id") or "")
                unit = str(segment.get("unit_id") or "")
                conduct = str(segment.get("conduct_id") or "")
                recent = " baru saja" if segment.get("recent") else ""
                observation = observation_verbs.get(
                    str(segment.get("observation_source") or ""), ""
                )
                person_conduct = (
                    f"seseorang dari {unit} sedang {conduct}"
                )
                if segment.get("reported_speech"):
                    if observation:
                        text = (
                            f"{observer}{recent} mengatakan bahwa dia {observation} "
                            + person_conduct
                        )
                    else:
                        text = (
                            f"{observer}{recent} mengatakan bahwa "
                            + person_conduct
                        )
                else:
                    text = (
                        f"{observer}{recent} {observation or 'melihat'} "
                        + person_conduct
                    )
                rendered.append(_sentence(text))
            elif segment_type == "vehicle_backlog_defer":
                rendered.append(_sentence("hari ini ada banyak kendaraan"))
                if segment.get("not_in_time") and segment.get("defer_to_tomorrow"):
                    rendered.append(_sentence(
                        "yang tidak sempat ditangani akan ditunda sampai besok"
                    ))
            elif segment_type == "supervisor_movement_inspection":
                timing_id = str(segment.get("timing_id") or "")
                subject = (
                    "dia"
                    if segment.get("actor_inherited")
                    else str(segment.get("actor_id") or "")
                )
                parts = []
                if timing_id:
                    parts.append(timing_id + ",")
                parts.append(subject)
                if segment.get("completed"):
                    parts.append("sudah")
                if segment.get("uncertain"):
                    parts.append("mungkin")
                if segment.get("future"):
                    parts.append("akan")
                parts.append(str(segment.get("movement_id") or "datang"))
                if segment.get("repeat"):
                    parts.append("lagi")
                inspection = str(segment.get("inspection_id") or "")
                if inspection:
                    parts.extend(("untuk", inspection))
                rendered.append(_sentence(" ".join(parts)))
            elif segment_type == "shopfloor_alert":
                if segment.get("notify") and segment.get("shopfloor_recipient"):
                    rendered.append(_sentence(
                        "tolong beri tahu personel di lapangan agar lebih waspada"
                    ))
                elif segment.get("notify"):
                    rendered.append(_sentence(
                        "tolong beri tahu mereka agar lebih waspada"
                    ))
                elif segment.get("shopfloor_recipient"):
                    rendered.append(_sentence(
                        "personel di lapangan harap lebih waspada"
                    ))
                else:
                    rendered.append(_sentence("mohon lebih waspada"))
        if not rendered or any(not item for item in rendered):
            return ""
        return _with_mentions(frame, " ".join(rendered))

    if frame.get("kind") == "zh_id_remaining_customer_orders":
        names = _format_id_preserved_name_list(slots.get("customer_names") or ())
        if not names or not slots.get("has_packaging_instruction"):
            return ""
        text = (
            f"Hari ini hanya tersisa pesanan untuk {names}. "
            "Untuk material yang ditandai tertunda di sistem packaging, "
            "mohon bantu kemas lalu masukkan ke gudang."
        )
        return _with_mentions(frame, text)

    if frame.get("kind") == "zh_id_deferred_material_process_flow":
        current_count = _format_id_release_count(
            slots.get("current_count"), str(slots.get("current_count_raw") or "")
        )
        destination_count = _format_id_release_count(
            slots.get("destination_count"), str(slots.get("destination_count_raw") or "")
        )
        current_process = str(slots.get("current_process_id") or "")
        destination_process = str(slots.get("destination_process_id") or "")
        if not all((current_count, destination_count, current_process, destination_process)):
            return ""
        text = (
            "Setelah work order mendesak sore ini hampir selesai, mohon atur "
            "penanganan material tertunda yang tercantum di atas. "
            f"{current_count.capitalize()} bundel berada di {current_process}. "
            f"{destination_count.capitalize()} bundel akan dikirim secara bertahap "
            f"ke {destination_process}."
        )
        return _with_mentions(frame, text)

    if frame.get("kind") == "zh_id_careless_action_speech_act":
        action = str(slots.get("action") or "")
        modality = str(slots.get("modality") or "")
        if action == "trash_disposal" and slots.get("after_drinking"):
            if modality == "prohibition":
                return _with_mentions(frame, "Setelah minum, jangan dibuang sembarangan.")
            return _with_mentions(frame, "Setelah diminum, malah dibuang sembarangan.")
        if action == "short_material_handling" and slots.get("no_short_material"):
            if modality == "prohibition":
                return _with_mentions(
                    frame,
                    "Jika tidak ada material pendek, jangan lakukan penanganan "
                    "material pendek secara sembarangan."
                )
            return _with_mentions(
                frame,
                "Tidak ada material pendek, tetapi penanganan material pendek "
                "malah dilakukan sembarangan."
            )
        return ""

    if frame.get("kind") == "zh_id_mes_operational_notice":
        stop_time = str(slots.get("mes_stop_time") or "")
        deadline_time = str(slots.get("change_data_deadline_time") or "")
        if not stop_time or not deadline_time:
            return ""
        text = (
            "Semua stasiun harus memprioritaskan produksi pesanan bulan ini; "
            "pesanan berlatar biru perlu mendapat perhatian khusus. "
            f"Hari ini, setelah pukul {stop_time}, sistem MES akan berhenti beroperasi. "
            f"Semua perubahan data harus diselesaikan sekitar pukul {deadline_time}. "
            "Mohon prioritaskan work order mendesak untuk packaging dan pengiriman. "
            "Mohon alihkan material dari Stasiun packing barang bentuk khusus ke sini."
        )
        return _with_mentions(frame, text)

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


def _id_clock_present(text: str, expected: str) -> bool:
    raw = str(expected or "")
    if not raw:
        return True
    try:
        hour_text, minute_text = raw.split(".", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (TypeError, ValueError):
        return False
    hour_words = {
        0: "nol", 1: "satu", 2: "dua", 3: "tiga", 4: "empat",
        5: "lima", 6: "enam", 7: "tujuh", 8: "delapan", 9: "sembilan",
        10: "sepuluh", 11: "sebelas", 12: "dua belas",
    }
    numeric_suffix = (
        r"(?:[.,:]" + f"{minute:02d}" + r")\b"
        if minute
        else r"(?:[.,:]00)?\b"
    )
    numeric = bool(re.search(
        r"\bpukul\s+(?:0?" + re.escape(str(hour)) + r")" + numeric_suffix,
        text,
        re.I,
    ))
    if numeric:
        return True
    word = hour_words.get(hour, "")
    if not word:
        return False
    if minute == 0:
        return bool(re.search(r"\bpukul\s+" + re.escape(word) + r"\b", text, re.I))
    if minute == 30:
        next_word = hour_words.get((hour + 1) % 12, "")
        return bool(
            re.search(r"\bpukul\s+" + re.escape(word) + r"\s+(?:lewat\s+)?tiga\s+puluh\b", text, re.I)
            or (next_word and re.search(r"\bpukul\s+setengah\s+" + re.escape(next_word) + r"\b", text, re.I))
            or re.search(r"\bpukul\s+" + re.escape(word) + r"\s+setengah\b", text, re.I)
        )
    return False


def _normalized_target_clauses(value: Any) -> list[str]:
    """Keep relation checks inside their source-corresponding target clause."""
    return [
        clause
        for clause in (
            _norm(item)
            for item in re.split(
                r"(?:[\n\r!！?？;；]+|(?<!\d)\.(?!\d))",
                str(value or ""),
            )
        )
        if clause
    ]


def validate_translation(frame: Mapping, translation: str) -> tuple[bool, list[str]]:
    """Validate source roles and relations, not merely isolated keywords."""
    if not frame or not frame.get("active"):
        return True, []
    target = str(translation or "").strip()
    if not target:
        return False, ["factory_message_semantics:empty_translation"]
    slots = frame.get("slots") or {}
    issues: list[str] = []

    if frame.get("kind") == "id_zh_machine_oil_leak":
        for code in slots.get("equipment_codes") or ():
            if not re.search(
                r"(?<![A-Za-z0-9])" + re.escape(str(code)) + r"(?![A-Za-z0-9])",
                target,
                re.I,
            ):
                issues.append("factory_message_semantics:oil_leak_equipment_code_missing")
        if not any(term in target for term in ("機台", "机台", "機器", "机器", "設備", "设备")):
            issues.append("factory_message_semantics:oil_leak_machine_actor_missing")
        if not any(term in target for term in ("漏油", "滴油", "滲油", "渗油", "油滲漏", "油渗漏")):
            issues.append("factory_message_semantics:machine_oil_leak_action_missing")
        if any(term in target for term in ("汽油", "柴油")):
            issues.append("factory_message_semantics:machine_oil_type_changed")

    elif frame.get("kind") == "id_zh_night_shift_trash_omission":
        if not any(term in target for term in ("晚班", "夜班")):
            issues.append("factory_message_semantics:night_shift_actor_missing")
        if not any(term in target for term in ("人員", "人员", "員工", "员工", "操作員", "操作员")):
            issues.append("factory_message_semantics:night_shift_human_role_missing")
        if not any(term in target for term in ("沒有", "没有", "沒", "没", "未", "還沒", "还没")):
            issues.append("factory_message_semantics:trash_disposal_negation_missing")
        if not (
            any(term in target for term in ("垃圾", "廢棄物", "废弃物"))
            and any(term in target for term in ("倒", "丟", "丢", "扔", "清運", "清运"))
        ):
            issues.append("factory_message_semantics:trash_disposal_action_missing")

    elif frame.get("kind") == "id_zh_equipment_code_failure":
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

    elif frame.get("kind") == "zh_id_factory_unit_trolley_request":
        low = _norm(target)
        if not _has_phrase(low, ("bagian peeling",)):
            issues.append(
                "factory_message_semantics:trolley_receiving_section_missing"
            )
        if not _has_phrase(low, (
            "membutuhkan", "memerlukan", "butuh", "perlu",
        )):
            issues.append("factory_message_semantics:trolley_need_relation_missing")
        if not _has_phrase(low, ("troli", "trolley")):
            issues.append("factory_message_semantics:trolley_object_missing")

        for code in slots.get("owner_unit_codes") or ():
            if not re.search(
                r"(?<![A-Za-z0-9])" + re.escape(str(code))
                + r"(?![A-Za-z0-9])",
                target,
                re.I,
            ):
                issues.append(
                    "factory_message_semantics:factory_unit_code_missing:"
                    + str(code)
                )
        owner_relation = bool(
            re.search(
                r"\b(?:dari|milik)\s+(?:unit|bagian|seksi|departemen)\b",
                low,
                re.I,
            )
        )
        if not owner_relation:
            issues.append(
                "factory_message_semantics:unit_trolley_ownership_relation_missing"
            )
        if slots.get("request") and not _has_phrase(
            low, ("mohon", "tolong", "harap")
        ):
            issues.append("factory_message_semantics:trolley_request_modality_missing")
        if slots.get("currently") and not _has_phrase(
            low, ("saat ini", "sekarang")
        ):
            issues.append("factory_message_semantics:trolley_current_time_missing")
        if slots.get("still") and not _has_phrase(low, ("masih",)):
            issues.append("factory_message_semantics:trolley_still_aspect_missing")

        if re.search(
            r"\b(?:untuk\s+)?proses\s+(?:peeling|pengupasan|kupas\s+kulit)\b",
            low,
            re.I,
        ) or _has_phrase(low, ("kupas kulit",)):
            issues.append(
                "factory_message_semantics:peeling_section_mistranslated_as_process"
            )
        if _has_phrase(low, ("troli angkut batang",)):
            issues.append(
                "factory_message_semantics:ungrounded_trolley_function_added"
            )

    elif frame.get("kind") == "zh_id_remaining_customer_orders":
        low = _norm(target)
        for name in slots.get("customer_names") or ():
            if str(name) not in target:
                issues.append(
                    "factory_message_semantics:remaining_customer_name_missing:" + str(name)
                )
        if not _has_phrase(low, ("pesanan", "order", "work order")):
            issues.append("factory_message_semantics:customer_order_metonymy_missing")
        if not _has_phrase(low, ("tersisa", "tinggal", "belum selesai")):
            issues.append("factory_message_semantics:remaining_order_state_missing")
        if slots.get("has_packaging_instruction"):
            if not (
                _has_phrase(low, ("sistem",))
                and _has_phrase(low, ("tertunda", "ditunda"))
                and _has_phrase(low, ("catatan", "ditandai", "tercatat"))
            ):
                issues.append("factory_message_semantics:deferred_packaging_note_missing")
            # Bare ``packaging`` in ``sistem packaging`` is a location/noun, not
            # proof that the packaging action was preserved.
            package_match = re.search(
                r"\b(?:kemas|mengemas|dikemas|bungkus|membungkus|dibungkus)\b",
                low,
                re.I,
            )
            warehouse_match = re.search(
                r"\b(?:masuk(?:kan)?|dimasukkan)\s+(?:ke\s+)?gudang\b|\bmasuk\s+gudang\b",
                low,
                re.I,
            )
            if not package_match:
                issues.append("factory_message_semantics:deferred_packaging_action_missing")
            if not warehouse_match:
                issues.append("factory_message_semantics:deferred_warehouse_action_missing")
            if package_match and warehouse_match and package_match.start() > warehouse_match.start():
                issues.append("factory_message_semantics:package_warehouse_sequence_reversed")
            if not _has_phrase(low, ("mohon", "tolong", "harap")):
                issues.append("factory_message_semantics:packaging_help_request_missing")

    elif frame.get("kind") == "zh_id_deferred_material_process_flow":
        low = _norm(target)
        clauses = _normalized_target_clauses(target)
        if slots.get("urgent_nearly_done") and not (
            _has_phrase(low, ("work order mendesak", "order mendesak"))
            and _has_phrase(low, ("hampir selesai", "nyaris selesai"))
            and _has_phrase(low, ("setelah", "sesudah"))
        ):
            issues.append("factory_message_semantics:urgent_order_timing_missing")
        if not _has_phrase(low, ("material tertunda", "material yang tertunda")):
            issues.append("factory_message_semantics:deferred_material_reference_missing")
        if slots.get("request") and not _has_phrase(low, ("mohon", "tolong", "harap")):
            issues.append("factory_message_semantics:deferred_handling_request_missing")
        current_count = _format_id_release_count(
            slots.get("current_count"), str(slots.get("current_count_raw") or "")
        )
        destination_count = _format_id_release_count(
            slots.get("destination_count"), str(slots.get("destination_count_raw") or "")
        )
        if current_count and not _has_phrase(low, (current_count, str(slots.get("current_count")))):
            issues.append("factory_message_semantics:current_bundle_count_missing")
        if destination_count and not _has_phrase(low, (destination_count, str(slots.get("destination_count")))):
            issues.append("factory_message_semantics:destination_bundle_count_missing")
        current_process = str(slots.get("current_process_id") or "")
        if current_process and not _has_phrase(low, (current_process, current_process.replace("bagian ", ""))):
            issues.append("factory_message_semantics:current_bundle_process_missing")
        destination_process = str(slots.get("destination_process_id") or "")
        destination_name = destination_process.replace("bagian ", "")
        current_count_pattern = "|".join(
            re.escape(item)
            for item in dict.fromkeys(
                item for item in (current_count, str(slots.get("current_count") or "")) if item
            )
        )
        destination_count_pattern = "|".join(
            re.escape(item)
            for item in dict.fromkeys(
                item for item in (
                    destination_count,
                    str(slots.get("destination_count") or ""),
                )
                if item
            )
        )
        current_name = current_process.replace("bagian ", "")
        current_binding = bool(
            current_count_pattern
            and current_name
            and any(
                re.search(
                    r"(?<![a-z0-9])(?:" + current_count_pattern + r")(?![a-z0-9])"
                    r".{0,32}\b(?:bundel|ikat)\b.{0,48}"
                    r"\b(?:berada|ada|sedang)\b.{0,40}"
                    r"(?:bagian\s+|stasiun\s+)?" + re.escape(current_name) + r"\b",
                    clause,
                    re.I,
                )
                for clause in clauses
            )
        )
        if not current_binding:
            issues.append("factory_message_semantics:current_count_process_relation_missing")
        movement_to_process = any(
            re.search(
                r"\b(?:dikirim|dialihkan|dipindahkan|dibawa|bergerak)\b",
                clause,
                re.I,
            )
            and re.search(
                r"\b(?:ke|menuju)\s+(?:bagian\s+|stasiun\s+)?"
                + re.escape(destination_name) + r"\b",
                clause,
                re.I,
            )
            for clause in clauses
        )
        if not movement_to_process:
            issues.append("factory_message_semantics:process_destination_relation_missing")
        destination_binding = bool(
            destination_count_pattern
            and destination_name
            and any(
                re.search(
                    r"(?<![a-z0-9])(?:" + destination_count_pattern + r")(?![a-z0-9])"
                    r".{0,32}\b(?:bundel|ikat)\b.{0,64}"
                    r"\b(?:dikirim|dialihkan|dipindahkan|dibawa|bergerak)\b.{0,80}"
                    r"\b(?:ke|menuju)\s+(?:bagian\s+|stasiun\s+)?"
                    + re.escape(destination_name) + r"\b",
                    clause,
                    re.I,
                )
                for clause in clauses
            )
        )
        if not destination_binding:
            issues.append("factory_message_semantics:destination_count_process_relation_missing")
        if not _has_phrase(low, ("secara bertahap", "bertahap", "berangsur-angsur")):
            issues.append("factory_message_semantics:gradual_process_movement_missing")
        if re.search(r"\b(?:dipoles|dipolish|dipoleskan)\b", low, re.I) and not movement_to_process:
            issues.append("factory_message_semantics:process_destination_changed_to_passive_action")

    elif frame.get("kind") == "zh_id_careless_action_speech_act":
        low = _norm(target)
        command_present = bool(re.search(
            r"\b(?:jangan|dilarang|tidak\s+boleh)\b", low, re.I
        ))
        modality = str(slots.get("modality") or "")
        if modality == "observed_complaint" and command_present:
            issues.append("factory_message_semantics:statement_changed_to_prohibition")
        elif modality == "prohibition" and not command_present:
            issues.append("factory_message_semantics:prohibition_modality_missing")
        action = str(slots.get("action") or "")
        if action == "trash_disposal":
            if not re.search(r"\b(?:buang|membuang|dibuang)\b", low, re.I):
                issues.append("factory_message_semantics:careless_disposal_action_missing")
            if not _has_phrase(low, ("sembarangan", "asal")):
                issues.append("factory_message_semantics:careless_disposal_manner_missing")
            if slots.get("after_drinking") and not re.search(
                r"\bsetelah\s+(?:di)?minum\b|\bhabis\s+(?:di)?minum\b",
                low,
                re.I,
            ):
                issues.append("factory_message_semantics:after_drinking_relation_missing")
        elif action == "short_material_handling":
            if not (
                _has_phrase(low, ("tidak ada", "tanpa"))
                and _has_phrase(low, ("material pendek", "batang pendek"))
            ):
                issues.append("factory_message_semantics:no_short_material_state_missing")
            if not (
                _has_phrase(low, ("penanganan", "menangani"))
                and _has_phrase(low, ("material pendek", "batang pendek"))
            ):
                issues.append("factory_message_semantics:short_material_handling_missing")
            if not _has_phrase(low, ("sembarangan", "asal")):
                issues.append("factory_message_semantics:careless_handling_manner_missing")
            if _has_phrase(low, ("maintenance", "pemeliharaan mesin", "perawatan mesin")):
                issues.append("factory_message_semantics:short_handling_changed_to_machine_maintenance")

    elif frame.get("kind") == "zh_id_mes_operational_notice":
        low = _norm(target)
        clauses = _normalized_target_clauses(target)
        if slots.get("monthly_order_priority") and not any(
            _has_phrase(clause, ("semua stasiun", "setiap stasiun"))
            and _has_phrase(clause, ("pesanan bulan ini", "order bulan ini"))
            and _has_phrase(clause, ("prioritas", "prioritaskan", "memprioritaskan"))
            for clause in clauses
        ):
            issues.append("factory_message_semantics:monthly_order_station_priority_missing")
        if slots.get("blue_background_attention") and not any(
            _has_phrase(clause, ("biru",))
            and _has_phrase(
                clause,
                ("perhatian khusus", "diperhatikan khusus", "perhatikan khusus"),
            )
            for clause in clauses
        ):
            issues.append("factory_message_semantics:blue_order_attention_missing")
        if slots.get("mes_stop") and not any(
            _has_phrase(clause, ("sistem mes",))
            and _has_phrase(clause, ("berhenti", "dihentikan", "tidak beroperasi"))
            and _id_clock_present(clause, str(slots.get("mes_stop_time") or ""))
            for clause in clauses
        ):
            issues.append("factory_message_semantics:mes_stop_time_relation_missing")
        if slots.get("change_data_deadline") and not any(
            _has_phrase(clause, ("perubahan data", "data perubahan", "data yang diubah"))
            and _has_phrase(clause, ("selesai", "diselesaikan", "dituntaskan"))
            and _id_clock_present(
                clause, str(slots.get("change_data_deadline_time") or "")
            )
            for clause in clauses
        ):
            issues.append("factory_message_semantics:change_data_deadline_missing")
        if slots.get("packaging_shipping_urgent") and not any(
            _has_phrase(clause, ("work order mendesak", "order mendesak"))
            and _has_phrase(clause, ("packing", "packaging", "pengemasan"))
            and _has_phrase(clause, ("pengiriman", "dikirim"))
            and _has_phrase(clause, ("prioritas", "prioritaskan", "diprioritaskan"))
            for clause in clauses
        ):
            issues.append("factory_message_semantics:packaging_shipping_urgent_priority_missing")
        if slots.get("special_station_route") and not any(
            _has_phrase(clause, ("stasiun packing barang bentuk khusus",))
            and _has_phrase(clause, ("material", "bahan"))
            and _has_phrase(
                clause, ("dialihkan", "dipindahkan", "alihkan", "pindahkan")
            )
            and _has_phrase(clause, ("ke sini", "kemari"))
            for clause in clauses
        ):
            issues.append("factory_message_semantics:special_station_material_route_missing")

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

    elif frame.get("kind") == "zh_id_shopfloor_agent_roles":
        low = _norm(target)
        target_clauses = [
            _norm(clause)
            for clause in re.split(r"[\n.!！?？;；]+", target)
            if _norm(clause)
        ]

        def _movement_present(value: str, haystack: str = "") -> bool:
            text = haystack or low
            if value == "turun":
                return _has_phrase(text, ("turun",))
            if value == "masuk":
                return _has_phrase(text, ("masuk", "memasuki"))
            if value == "datang ke lapangan":
                return bool(
                    _has_phrase(text, ("datang", "masuk"))
                    and _has_phrase(text, ("lapangan", "area kerja", "lokasi"))
                )
            return _has_phrase(text, ("datang", "tiba"))

        for segment in slots.get("segments") or ():
            segment_type = str(segment.get("type") or "")
            if segment_type == "attendance_checker_movement":
                attendance_agent_clauses = [
                    clause for clause in target_clauses
                    if re.search(
                        r"\b(?:petugas|orang|personel|karyawan|pegawai)\b.{0,45}"
                        r"\b(?:pengecekan|pemeriksaan)\s+kehadiran\b",
                        clause,
                        re.I,
                    )
                ]
                if not attendance_agent_clauses:
                    issues.append(
                        "factory_message_semantics:attendance_checker_human_actor_missing"
                    )
                if re.search(
                    r"\b(?:absen|absensi|pengecekan\s+kehadiran|"
                    r"pemeriksaan\s+kehadiran)\b.{0,18}"
                    r"\b(?:dimulai|mulai|berlangsung)\b",
                    low,
                    re.I,
                ):
                    issues.append(
                        "factory_message_semantics:attendance_checker_movement_changed_to_procedure_start"
                    )
                attendance_relation_clauses = [
                    clause for clause in attendance_agent_clauses
                    if _movement_present(
                        str(segment.get("movement_id") or ""), clause
                    )
                ]
                if not attendance_relation_clauses:
                    issues.append(
                        "factory_message_semantics:attendance_checker_movement_missing"
                    )
                attendance_scope = " ".join(
                    attendance_relation_clauses or attendance_agent_clauses
                )
                if segment.get("completed") and not _has_phrase(
                    attendance_scope, ("sudah", "telah")
                ):
                    issues.append(
                        "factory_message_semantics:attendance_checker_completed_aspect_missing"
                    )
                if segment.get("future") and not _has_phrase(
                    attendance_scope, ("akan",)
                ):
                    issues.append(
                        "factory_message_semantics:attendance_checker_future_missing"
                    )
                if segment.get("uncertain") and not _has_phrase(
                    attendance_scope, ("mungkin", "kemungkinan", "diperkirakan")
                ):
                    issues.append(
                        "factory_message_semantics:attendance_checker_uncertainty_missing"
                    )
                if segment.get("timing_id") and not _has_phrase(
                    attendance_scope, (str(segment.get("timing_id")),)
                ):
                    issues.append(
                        "factory_message_semantics:attendance_checker_timing_missing"
                    )

            elif segment_type == "supervisor_observed_person_conduct":
                observer = _norm(segment.get("observer_id"))
                unit = _norm(segment.get("unit_id"))
                conduct = _norm(segment.get("conduct_id"))
                if observer and not _has_phrase(low, (observer,)):
                    issues.append(
                        "factory_message_semantics:supervisor_observer_missing"
                    )
                if unit and not _has_phrase(low, (unit,)):
                    issues.append(
                        "factory_message_semantics:organization_unit_missing"
                    )
                human_affiliation = bool(unit and (
                    re.search(
                        r"\b(?:seseorang|orang|karyawan|personel|operator|"
                        r"pekerja|anggota|pegawai)\b.{0,60}\b(?:dari|di)\s+"
                        + re.escape(unit)
                        + r"\b",
                        low,
                        re.I,
                    )
                    or re.search(
                        r"\b(?:di|dari)\s+" + re.escape(unit)
                        + r"\b.{0,60}\b(?:seseorang|orang|karyawan|personel|"
                        r"operator|pekerja|anggota|pegawai)\b",
                        low,
                        re.I,
                    )
                ))
                if not human_affiliation:
                    issues.append(
                        "factory_message_semantics:organization_member_human_actor_missing"
                    )
                conduct_ok = False
                if conduct == "menggunakan ponsel":
                    conduct_ok = bool(re.search(
                        r"\b(?:menggunakan|memakai|melihat|bermain(?:\s+dengan)?)\s+"
                        r"(?:ponsel|hp|handphone)\b",
                        low,
                        re.I,
                    ))
                elif conduct:
                    conduct_ok = _has_phrase(low, (conduct,))
                if not conduct_ok:
                    issues.append(
                        "factory_message_semantics:observed_human_conduct_missing"
                    )
                if segment.get("observation_source") and not _has_phrase(low, (
                    "memergoki", "mendapati", "melihat", "menemukan",
                )):
                    issues.append(
                        "factory_message_semantics:supervisor_observation_action_missing"
                    )
                if segment.get("reported_speech") and not _has_phrase(low, (
                    "mengatakan", "menyampaikan", "memberi tahu", "melaporkan",
                )):
                    issues.append(
                        "factory_message_semantics:supervisor_reported_speech_missing"
                    )
                if segment.get("recent") and not _has_phrase(
                    low, ("baru saja", "barusan")
                ):
                    issues.append(
                        "factory_message_semantics:supervisor_observation_recency_missing"
                    )
                if unit and conduct_ok and not human_affiliation:
                    issues.append(
                        "factory_message_semantics:organization_promoted_to_human_conduct_actor"
                    )

            elif segment_type == "vehicle_backlog_defer":
                if not _has_phrase(low, ("hari ini",)):
                    issues.append(
                        "factory_message_semantics:vehicle_workload_today_missing"
                    )
                if not _has_phrase(low, ("kendaraan", "truk", "mobil")):
                    issues.append(
                        "factory_message_semantics:vehicle_workload_object_missing"
                    )
                if not _has_phrase(low, ("banyak", "jumlah besar")):
                    issues.append(
                        "factory_message_semantics:vehicle_workload_volume_missing"
                    )
                if segment.get("not_in_time") and not _has_phrase(
                    low, ("tidak sempat", "tidak keburu")
                ):
                    issues.append(
                        "factory_message_semantics:vehicle_not_in_time_relation_missing"
                    )
                if segment.get("defer_to_tomorrow") and not (
                    _has_phrase(low, ("ditunda", "diundur", "dialihkan"))
                    and _has_phrase(low, ("besok",))
                ):
                    issues.append(
                        "factory_message_semantics:vehicle_defer_to_tomorrow_missing"
                    )

            elif segment_type == "supervisor_movement_inspection":
                actor = _norm(segment.get("actor_id"))
                actor_terms = [actor] if actor else []
                if segment.get("actor_inherited"):
                    actor_terms.extend(("dia", "ia", "beliau"))
                movement_clauses = [
                    clause for clause in target_clauses
                    if _movement_present(
                        str(segment.get("movement_id") or ""), clause
                    )
                ]
                if actor_terms and not any(
                    _has_phrase(clause, actor_terms) for clause in movement_clauses
                ):
                    issues.append(
                        "factory_message_semantics:supervisor_movement_actor_missing"
                    )
                if not movement_clauses:
                    issues.append(
                        "factory_message_semantics:supervisor_movement_action_missing"
                    )
                movement_scope = " ".join(movement_clauses)
                if segment.get("timing_id") and not _has_phrase(
                    movement_scope, (str(segment.get("timing_id")),)
                ):
                    issues.append(
                        "factory_message_semantics:supervisor_movement_timing_missing"
                    )
                if segment.get("uncertain") and not _has_phrase(
                    movement_scope, ("mungkin", "kemungkinan", "diperkirakan")
                ):
                    issues.append(
                        "factory_message_semantics:supervisor_movement_uncertainty_missing"
                    )
                if segment.get("future") and not _has_phrase(
                    movement_scope, ("akan",)
                ):
                    issues.append(
                        "factory_message_semantics:supervisor_movement_future_missing"
                    )
                if segment.get("repeat") and not _has_phrase(
                    movement_scope, ("lagi", "kembali")
                ):
                    issues.append(
                        "factory_message_semantics:supervisor_repeat_movement_missing"
                    )
                if segment.get("completed") and not _has_phrase(
                    movement_scope, ("sudah", "telah")
                ):
                    issues.append(
                        "factory_message_semantics:supervisor_movement_completed_aspect_missing"
                    )
                if segment.get("inspection_source") and not _has_phrase(movement_scope, (
                    "melihat", "meninjau", "memeriksa", "mengecek",
                )):
                    issues.append(
                        "factory_message_semantics:supervisor_inspection_purpose_missing"
                    )

            elif segment_type == "shopfloor_alert":
                if not _has_phrase(low, (
                    "waspada", "berhati-hati", "hati-hati", "perhatikan",
                )):
                    issues.append(
                        "factory_message_semantics:shopfloor_alert_attention_missing"
                    )
                if segment.get("notify") and not _has_phrase(low, (
                    "beri tahu", "memberi tahu", "informasikan", "ingatkan",
                )):
                    issues.append(
                        "factory_message_semantics:shopfloor_notification_action_missing"
                    )
                if segment.get("shopfloor_recipient"):
                    people_at_shopfloor = bool(re.search(
                        r"\b(?:personel|karyawan|operator|pekerja|orang|pegawai)\b"
                        r".{0,25}\b(?:lapangan|area\s+kerja|lokasi)\b",
                        low,
                        re.I,
                    ))
                    if not people_at_shopfloor:
                        issues.append(
                            "factory_message_semantics:shopfloor_people_recipient_missing"
                        )
                    if _has_phrase(low, ("bagian lapangan",)):
                        issues.append(
                            "factory_message_semantics:shopfloor_location_mistranslated_as_department"
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
    if frame.get("kind") == "id_zh_machine_oil_leak":
        lines.append(
            "This is a machine oil-leak report. Attach the I/E/BF code to the machine, "
            "and render minyak/oli mesin + menetes/bocor as 機台漏油 or 機台滴油. "
            "Do not reduce the event to a detached noun phrase such as 機油滴漏 that "
            "loses the machine actor."
        )
    elif frame.get("kind") == "id_zh_night_shift_trash_omission":
        lines.append(
            "Orang/karyawan malam means night-shift personnel. Keep them as the human "
            "actor and preserve the negation on the trash-disposal duty. Colloquial "
            "malem/tida are malam/tidak; do not interpret them as names or omit them."
        )
    elif frame.get("kind") == "id_zh_equipment_code_failure":
        lines.append(
            "A source code such as I15 is an equipment/station identifier. Rusak predicates a "
            "functional machine failure: translate the linked claim as I15 機台故障, not as "
            "material/surface damage (損傷) and not as the underspecified I15 損壞."
        )
    elif frame.get("kind") == "zh_id_factory_unit_trolley_request":
        lines.append(
            "This is a factory-unit trolley request. In the subject position before 需要, "
            "削皮 means the receiving organization Bagian Peeling, not the action proses "
            "pengupasan/kupas kulit. Compact G-number spellings such as G8G9, G8/G9 and "
            "G8、G9 denote separate factory-unit abbreviations. Preserve every code and "
            "express the omitted ownership/source relation explicitly as 'troli dari unit "
            "G8 dan G9' (or equivalent with milik). Never glue the codes into a trolley "
            "model, and do not add the unsupported function 'angkut batang'."
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
    elif frame.get("kind") == "zh_id_shopfloor_agent_roles":
        lines.append(
            "Resolve human actors before choosing words. A factory section such as 一股/二股 "
            "cannot literally use a phone, sleep, smoke, chat or rest: when one of those human-only "
            "predicates follows the section, Indonesian must say seseorang/personel dari the "
            "canonical section. Likewise, 點名 followed by 進來/下來/來 is metonymy for the human "
            "attendance checker; it is not an abstract attendance procedure starting. 現場 as the "
            "recipient of 通知/注意 means personel di lapangan, never a department named bagian "
            "lapangan. Preserve the supervisor as the actor across adjacent omitted-subject clauses, "
            "including timing, uncertainty, future, repeat movement and inspection purpose. For a "
            "compressed vehicle-workload clause, keep 'vehicles are many' separate from 'the ones "
            "not completed in time are deferred until tomorrow'."
        )
    elif frame.get("kind") == "zh_id_remaining_customer_orders":
        lines.append(
            "In a production list, 今天剩 + customer names is metonymy for the remaining "
            "orders/material for those customers. Preserve every customer identifier exactly, "
            "but make the omitted noun explicit as pesanan untuk; never say that the companies "
            "themselves remain. For the packaging-system instruction, keep the marked-deferred "
            "state and the ordered actions: package first, then put the material into the warehouse."
        )
    elif frame.get("kind") == "zh_id_deferred_material_process_flow":
        lines.append(
            "Counts with 把 are material bundles. In a clause shaped '三把會陸續拋光過去', "
            "拋光 is the destination section and 過去 is movement: say that three bundles will "
            "be sent gradually to bagian polishing. Do not turn the destination into the passive "
            "action dipoles, do not add a shipment to a customer, and keep the current-location "
            "bundles separate from the bundles that will move."
        )
    elif frame.get("kind") == "zh_id_careless_action_speech_act":
        lines.append(
            "Preserve the source speech act. Chinese 亂/隨便 + action without an explicit "
            "不要/別/禁止 is an observation or complaint about careless conduct, not a new "
            "prohibition; Indonesian must not add jangan/tidak boleh/dilarang. Only an explicit "
            "source prohibition licenses jangan. In the short-material construction, 維護 means "
            "penanganan material pendek, never generic machine maintenance."
        )
    elif frame.get("kind") == "zh_id_mes_operational_notice":
        lines.append(
            "Keep each operational instruction as a separate relation: all stations prioritize "
            "this month's orders; blue-background orders receive special attention; MES stops "
            "after the stated time; all data changes are completed by the stated time; packaging/"
            "shipping urgent orders are prioritized; and material from the canonical Stasiun "
            "packing barang bentuk khusus is diverted to the speaker's location. Never attach "
            "packing to the wrong station or drop either time."
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
    unit_trolley_source = "@法比恩 Fabian 削皮需要G8G9台車 麻煩一下"
    unit_trolley_target = (
        "@法比恩 Fabian Bagian Peeling membutuhkan troli dari unit G8 dan G9. "
        "Mohon bantuannya."
    )
    unit_trolley = build_frame(unit_trolley_source, "zh", "id")
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
    supervisor_alert_source = (
        "處長剛剛說抓到二股滑手機，晚點可能還會下來，再注意一下"
    )
    supervisor_alert_target = (
        "Kepala divisi baru saja mengatakan bahwa dia memergoki seseorang dari "
        "Bagian Cold Drawing 2 sedang menggunakan ponsel. Nanti, dia mungkin "
        "akan turun lagi. Mohon lebih waspada."
    )
    supervisor_alert_bad = (
        "Kepala divisi baru saja mengatakan menemukan Bagian Cold Drawing 2 "
        "bermain ponsel. Nanti mungkin akan turun lagi, harap lebih hati-hati."
    )
    workload_alert_source = (
        "今天的車很多來不及延到明天，處長等等應該會進來看，"
        "通知現場注意一下。"
    )
    workload_alert_target = (
        "Hari ini ada banyak kendaraan. Yang tidak sempat ditangani akan ditunda "
        "sampai besok. Sebentar lagi, kepala divisi mungkin akan masuk untuk "
        "melihat keadaan. Tolong beri tahu personel di lapangan agar lebih waspada."
    )
    attendance_checker_source = "點名進來了"
    attendance_checker_target = "Petugas pengecekan kehadiran sudah masuk."
    attendance_checker_bad = "Absen sudah dimulai."
    oil_leak_source = "i19 minyak mesin menetes"
    oil_leak_target = "I19 機台漏油"
    oil_leak = build_frame(oil_leak_source, "id", "zh")
    night_trash_source = "Orang malem tida membuang sampah"
    night_trash_target = "晚班人員沒有倒垃圾"
    night_trash = build_frame(night_trash_source, "id", "zh")
    customer_order_source = (
        "今天剩柏緯、上銀、津展。"
        "包裝系統備註遞延料再幫忙包裝入庫。"
    )
    customer_order_target = (
        "Hari ini hanya tersisa pesanan untuk 柏緯, 上銀, dan 津展. "
        "Untuk material yang ditandai tertunda di sistem packaging, "
        "mohon bantu kemas lalu masukkan ke gudang."
    )
    customer_order = build_frame(customer_order_source, "zh", "id")
    material_flow_source = (
        "下午急單差不多後，這份上面的遞延料幫忙安排處理一下，"
        "四把在包裝，三把會陸續拋光過去"
    )
    material_flow_target = (
        "Setelah work order mendesak sore ini hampir selesai, mohon atur "
        "penanganan material tertunda yang tercantum di atas. Empat bundel "
        "berada di bagian packaging. Tiga bundel akan dikirim secara bertahap "
        "ke bagian polishing."
    )
    material_flow = build_frame(material_flow_source, "zh", "id")
    short_material_source = "沒短尺亂維護"
    short_material_target = (
        "Tidak ada material pendek, tetapi penanganan material pendek malah "
        "dilakukan sembarangan."
    )
    short_material = build_frame(short_material_source, "zh", "id")
    drink_source = "喝完亂丟"
    drink_target = "Setelah diminum, malah dibuang sembarangan."
    drink = build_frame(drink_source, "zh", "id")
    mes_notice_source = (
        "@All 各站優先生產本月份訂單，藍底特別注意。"
        "今天五點後MES系統中止服務，所有的異動資料都在四點半左右完成。"
        "包裝出貨急單再麻煩優先處理。異型站的料幫忙分流過來"
    )
    mes_notice_target = (
        "@All Semua stasiun harus memprioritaskan produksi pesanan bulan ini; "
        "pesanan berlatar biru perlu mendapat perhatian khusus. Hari ini, "
        "setelah pukul 5.00, sistem MES akan berhenti beroperasi. Semua perubahan "
        "data harus diselesaikan sekitar pukul 4.30. Mohon prioritaskan work order "
        "mendesak untuk packaging dan pengiriman. Mohon alihkan material dari "
        "Stasiun packing barang bentuk khusus ke sini."
    )
    mes_notice = build_frame(mes_notice_source, "zh", "id")
    controls = (
        build_frame("Sip, terima kasih.", "id", "zh"),
        build_frame("Selamat pagi, Pak.", "id", "zh"),
        build_frame("Tolong memberi warna cat biru.", "id", "zh"),
        build_frame("Saya ketua kelas di sekolah.", "id", "zh"),
        build_frame("Katrol rusak.", "id", "zh"),
        build_frame("我先看看情況。", "zh", "id"),
        build_frame("我過去拿工具。", "zh", "id"),
        build_frame("G8G9台車已經滿了。", "zh", "id"),
        build_frame("這批削皮棒需要台車。", "zh", "id"),
        build_frame("這批料削皮需要台車。", "zh", "id"),
        build_frame("這把刀麻煩他們放在架上。", "zh", "id"),
        build_frame("這把材料放不下，先放照片裡的位置。", "zh", "id"),
        build_frame("品保檢驗後有放行。", "zh", "id"),
        build_frame("請他們放下工具。", "zh", "id"),
        build_frame("網路設備幫忙提醒一下。", "zh", "id"),
        build_frame("點名後車輛開走了", "zh", "id"),
        build_frame("點名開車的人到了", "zh", "id"),
        build_frame("點名開始了", "zh", "id"),
        build_frame("二股今天要開會", "zh", "id"),
        build_frame("處長說二股產量增加", "zh", "id"),
        build_frame("滑手機很傷眼", "zh", "id"),
        build_frame("再注意一下", "zh", "id"),
        build_frame("今天剩蘋果、香蕉，晚餐吃掉", "zh", "id"),
        build_frame("I19 minyak mesin baru diganti", "id", "zh"),
        build_frame("Orang malam membuang sampah", "id", "zh"),
        build_frame("沒短尺所以不用維護", "zh", "id"),
        build_frame("四把在包裝，三把已經拋光完成", "zh", "id"),
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
        unit_trolley.get("active") is True
        and unit_trolley.get("complete") is True,
        unit_trolley.get("slots", {}).get("owner_unit_codes") == ["G8", "G9"],
        translate_source_directly(unit_trolley_source, "zh", "id")
        == unit_trolley_target,
        validate_translation(unit_trolley, unit_trolley_target)[0] is True,
        validate_translation(
            unit_trolley,
            "Untuk proses kupas kulit, diperlukan troli angkut batang G8G9. "
            "Mohon bantuannya.",
        )[0] is False,
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
        build_frame(supervisor_alert_source, "zh", "id").get("complete") is True,
        translate_source_directly(supervisor_alert_source, "zh", "id")
        == supervisor_alert_target,
        validate_translation(
            build_frame(supervisor_alert_source, "zh", "id"),
            supervisor_alert_target,
        )[0] is True,
        validate_translation(
            build_frame(supervisor_alert_source, "zh", "id"),
            supervisor_alert_bad,
        )[0] is False,
        build_frame(workload_alert_source, "zh", "id").get("complete") is True,
        translate_source_directly(workload_alert_source, "zh", "id")
        == workload_alert_target,
        validate_translation(
            build_frame(workload_alert_source, "zh", "id"),
            workload_alert_target,
        )[0] is True,
        translate_source_directly(attendance_checker_source, "zh", "id")
        == attendance_checker_target,
        validate_translation(
            build_frame(attendance_checker_source, "zh", "id"),
            attendance_checker_bad,
        )[0] is False,
        oil_leak.get("active") is True and oil_leak.get("complete") is True,
        translate_source_directly(oil_leak_source, "id", "zh")
        == oil_leak_target,
        validate_translation(oil_leak, oil_leak_target)[0] is True,
        validate_translation(oil_leak, "I19 機油滴漏")[0] is False,
        night_trash.get("active") is True
        and night_trash.get("complete") is True,
        translate_source_directly(night_trash_source, "id", "zh")
        == night_trash_target,
        validate_translation(night_trash, night_trash_target)[0] is True,
        customer_order.get("active") is True
        and customer_order.get("complete") is True,
        translate_source_directly(customer_order_source, "zh", "id")
        == customer_order_target,
        validate_translation(customer_order, customer_order_target)[0] is True,
        validate_translation(
            customer_order,
            "Hari ini masih tersisa 柏緯、上銀、津展. Mohon bantu kemas lalu "
            "masukkan ke gudang.",
        )[0] is False,
        material_flow.get("active") is True
        and material_flow.get("complete") is True,
        translate_source_directly(material_flow_source, "zh", "id")
        == material_flow_target,
        validate_translation(material_flow, material_flow_target)[0] is True,
        validate_translation(
            material_flow,
            "Setelah work order mendesak hampir selesai, empat bundel berada di "
            "packaging dan tiga bundel akan dipoles bertahap.",
        )[0] is False,
        short_material.get("active") is True
        and short_material.get("complete") is True,
        translate_source_directly(short_material_source, "zh", "id")
        == short_material_target,
        validate_translation(short_material, short_material_target)[0] is True,
        validate_translation(
            short_material,
            "Tidak ada batang pendek, jangan melakukan maintenance sembarangan.",
        )[0] is False,
        drink.get("active") is True and drink.get("complete") is True,
        translate_source_directly(drink_source, "zh", "id") == drink_target,
        validate_translation(drink, drink_target)[0] is True,
        validate_translation(
            drink, "Setelah minum, jangan buang sembarangan."
        )[0] is False,
        mes_notice.get("active") is True and mes_notice.get("complete") is True,
        translate_source_directly(mes_notice_source, "zh", "id")
        == mes_notice_target,
        validate_translation(mes_notice, mes_notice_target)[0] is True,
        validate_translation(
            mes_notice,
            "@All Semua stasiun prioritaskan pesanan bulan ini. Sistem MES "
            "berhenti pukul 4.30. Mohon prioritaskan bagian packing.",
        )[0] is False,
        validate_translation(
            material_flow,
            "Setelah work order mendesak sore ini hampir selesai, mohon atur "
            "penanganan material tertunda yang tercantum di atas. Tiga bundel "
            "berada di bagian packaging. Empat bundel akan dikirim secara "
            "bertahap ke bagian polishing.",
        )[0] is False,
        validate_translation(
            mes_notice,
            "@All Semua stasiun harus memprioritaskan produksi pesanan bulan ini; "
            "pesanan berlatar biru perlu mendapat perhatian khusus. Hari ini, "
            "setelah pukul 4.30, sistem MES akan berhenti beroperasi. Semua "
            "perubahan data harus diselesaikan sekitar pukul 5.00. Mohon "
            "prioritaskan work order mendesak untuk packaging dan pengiriman. "
            "Mohon alihkan material dari Stasiun packing barang bentuk khusus ke sini.",
        )[0] is False,
        translate_source_directly(
            material_flow_source + "，明天停機保養", "zh", "id"
        ) == "",
        all(not frame.get("active") for frame in controls),
    ]
    return {
        "api_version": FACTORY_MESSAGE_SEMANTICS_API_VERSION,
        "build_id": FACTORY_MESSAGE_SEMANTICS_BUILD_ID,
        "self_test": {"ok": all(checks), "checks": len(checks)},
    }
