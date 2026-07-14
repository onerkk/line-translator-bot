"""Additional LINE translation workflows.

This module intentionally contains pure, independently testable helpers for:
- personal language preferences;
- shift-handover summarisation prompts;
- original-image + translated-text comparison images;
- the LIFF turn-by-turn voice interpreter page.

Network/API calls and LINE delivery remain in ``app.py`` so these helpers can be
validated without credentials.
"""

from __future__ import annotations

import html
import io
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence


TRANSLATION_EXTRAS_VERSION = "2026-07-14.8-action-ux-handover-fallback"


SUPPORTED_PERSONAL_LANGS = ("zh", "id", "vi", "th", "tl", "en", "ja", "ko", "hi")

LANGUAGE_LABELS = {
    "auto": "自動偵測",
    "zh": "繁體中文",
    "id": "印尼文",
    "vi": "越南文",
    "th": "泰文",
    "tl": "菲律賓文",
    "en": "英文",
    "ja": "日文",
    "ko": "韓文",
    "hi": "印地文",
}

LANGUAGE_ALIASES = {
    "中文": "zh",
    "繁中": "zh",
    "繁體中文": "zh",
    "chinese": "zh",
    "zh-tw": "zh",
    "印尼": "id",
    "印尼文": "id",
    "印尼語": "id",
    "indonesian": "id",
    "indonesia": "id",
    "bahasa": "id",
    "bahasa indonesia": "id",
    "中文台灣": "zh",
    "mandarin": "zh",
    "cina": "zh",
    "tionghoa": "zh",
    "taiwan": "zh",
    "越南": "vi",
    "越南文": "vi",
    "越南語": "vi",
    "vietnamese": "vi",
    "vietnam": "vi",
    "泰文": "th",
    "泰語": "th",
    "thai": "th",
    "thailand": "th",
    "菲律賓文": "tl",
    "菲律賓語": "tl",
    "他加祿": "tl",
    "tagalog": "tl",
    "filipino": "tl",
    "filipina": "tl",
    "英文": "en",
    "英語": "en",
    "english": "en",
    "inggris": "en",
    "日文": "ja",
    "日語": "ja",
    "japanese": "ja",
    "jepang": "ja",
    "韓文": "ko",
    "韓語": "ko",
    "korean": "ko",
    "korea": "ko",
    "印地文": "hi",
    "印地語": "hi",
    "hindi": "hi",
}


# ---------------------------------------------------------------------------
# Automatic tone detection + sentence-aware expressive translation
# ---------------------------------------------------------------------------

# Flags are language labels in this bot.  They must never count as an existing
# emotional emoji, otherwise a leading 🇹🇼/🇮🇩 would disable the whole feature.
_FLAG_PAIR_RE = re.compile(r"(?:[\U0001F1E6-\U0001F1FF]{2})")
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"  # flags (removed before emotional-emoji checks)
    "\U0001F300-\U0001FAFF"  # symbols, pictographs and supplemental emoji
    "\u2600-\u27BF"          # misc symbols/dingbats
    "]"
)

# name, patterns, instruction, emoji palette, placement, confidence, visual mood
_TONE_RULES: tuple[tuple[str, tuple[str, ...], str, tuple[str, ...], str, float, str], ...] = (
    (
        "apology",
        (
            r"抱歉|對不起|不好意思|請原諒|是我的錯|我錯了",
            r"\b(?:maaf|maafkan|mafkan|maf\s+kan|mafin|mohon\s+maaf|minta\s+maaf|mhn\s+maaf)\b",
            r"\b(?:sorry|apologi(?:ze|se)?)\b",
        ),
        "The speaker is apologising. Preserve responsibility, sincerity and any promise to improve; do not turn it into a request.",
        ("🙏", "😔"),
        "suffix",
        0.97,
        "apology",
    ),
    (
        "gratitude",
        (
            r"謝謝|感謝|多謝|辛苦了|麻煩你了|感恩",
            r"\b(?:terima\s+kasih|makasih|trimakasih|thanks?|thank\s+you)\b",
        ),
        "The speaker is expressing thanks or appreciation. Keep the wording warm but natural for a workplace chat.",
        ("🙏", "💛", "😊"),
        "suffix",
        0.96,
        "gratitude",
    ),
    (
        "celebration",
        (
            r"恭喜|成功了|完成了|達成|太好了|終於好了|過關了",
            r"\b(?:selamat|berhasil|selesai|akhirnya\s+selesai|congratulations?)\b",
        ),
        "The speaker is celebrating success or completion. Preserve the positive energy without adding facts.",
        ("🎉", "🥳", "✨"),
        "suffix",
        0.95,
        "celebration",
    ),
    (
        "praise",
        (
            r"做得好|很好|很棒|漂亮|讚|厲害|表現很好|辛苦你了",
            r"\b(?:bagus|bagus\s+sekali|mantap|hebat|kerja\s+bagus|good\s+job|well\s+done)\b",
        ),
        "The speaker is praising someone. Keep it positive, direct and natural rather than overly formal.",
        ("👏", "👍", "🌟"),
        "suffix",
        0.94,
        "praise",
    ),
    (
        "encouragement",
        (
            r"加油|撐住|別灰心|繼續保持|大家辛苦了|你可以的",
            r"\b(?:semangat|jangan\s+menyerah|tetap\s+semangat|keep\s+it\s+up|kamu\s+bisa)\b",
        ),
        "The speaker is encouraging the listener. Keep the message supportive and concise.",
        ("💪", "🙌", "✨"),
        "suffix",
        0.94,
        "encouragement",
    ),
    (
        "joy",
        (
            r"好開心|太開心|心情很好|好好噢|真好|幸福|可愛|喜歡|期待",
            r"\b(?:senang|bahagia|asyik|seru|lucu|suka|tidak\s+sabar)\b",
            r"\b(?:happy|lovely|so\s+nice|excited|cute)\b",
        ),
        "The speaker sounds cheerful, affectionate or excited. Preserve the light and friendly energy without exaggerating it.",
        ("😊", "✨", "🥰", "😄"),
        "suffix",
        0.91,
        "joy",
    ),
    (
        "concern",
        (
            r"還好嗎|沒事吧|注意身體|保重|希望你沒事|早日康復|要小心|擔心",
            r"\b(?:kamu\s+baik-baik\s+saja|tidak\s+apa-apa|jaga\s+kesehatan|semoga\s+lekas\s+sembuh|khawatir)\b",
            r"\b(?:are\s+you\s+okay|take\s+care|get\s+well\s+soon|worried)\b",
        ),
        "The speaker is showing concern. Keep the tone caring and sincere, not clinical.",
        ("🫶", "🙏", "😟"),
        "suffix",
        0.93,
        "concern",
    ),
    (
        "urgent_warning",
        (
            r"危險|警告|禁止|不得|不可|不要靠近|注意安全|立即停|立刻停|緊急|停機|停線|務必|必須遵守",
            r"\b(?:bahaya|peringatan|dilarang|jangan\s+mendekat|hati-hati|segera\s+berhenti|darurat|wajib|stop\s+mesin)\b",
            r"\b(?:danger|warning|prohibited|do\s+not|emergency|must\s+stop)\b",
        ),
        "This is an explicit warning, urgent instruction or safety message. Preserve its force and make the required action unmistakable without sounding abusive.",
        ("⚠️", "🚨"),
        "prefix",
        0.97,
        "warning",
    ),
    (
        "anger",
        (
            r"氣死|火大|很生氣|太扯|到底在幹嘛|講幾次|不要再犯|又來了|受夠了",
            r"\b(?:marah|kesal\s+sekali|jengkel|sudah\s+berapa\s+kali|jangan\s+diulangi)\b",
            r"\b(?:furious|angry|fed\s+up|how\s+many\s+times)\b",
        ),
        "The speaker is clearly angry or fed up. Preserve firmness and dissatisfaction, but do not add insults or profanity.",
        ("😠", "😤"),
        "suffix",
        0.94,
        "anger",
    ),
    (
        "frustration",
        (
            r"怎麼又|搞什麼|太誇張|受不了|真的很煩|怎麼會這樣|很無奈|麻煩死了",
            r"\b(?:aduh|kok\s+bisa|kenapa\s+lagi|menjengkelkan|capek\s+banget|repot\s+sekali)\b",
            r"\b(?:not\s+again|this\s+is\s+ridiculous|so\s+annoying|what\s+a\s+hassle)\b",
        ),
        "The speaker is frustrated or complaining. Preserve the dissatisfaction, but do not intensify it into abuse or hostility.",
        ("😮‍💨", "😓", "😤"),
        "suffix",
        0.91,
        "frustration",
    ),
    (
        "management_pressure",
        (
            r"主管.*(?:看到|巡視|下樓|查)|處長.*(?:詢問|巡視|看到)|被主管看到|被發現聚集|詢問為什麼",
            r"\b(?:atasan|kepala\s+divisi|supervisor).*(?:melihat|turun|berkeliling|menanyakan)|ketahuan\s+berkumpul\b",
        ),
        "This sentence conveys workplace pressure or concern about management scrutiny. Keep it cautious and matter-of-fact, not melodramatic.",
        ("👀", "😓"),
        "suffix",
        0.84,
        "caution",
    ),
    (
        "crowd_report",
        (
            r"(?:很多人|多人|幾位員工|人數).*(?:聚集|集合|集中)|(?:吸菸區|休息區).*(?:聚集|人數)",
            r"\b(?:banyak\s+orang|beberapa\s+karyawan|jumlah\s+orang).*(?:berkumpul|area\s+merokok)|area\s+merokok.*(?:berkumpul|jumlah\s+orang)\b",
            r"\b(?:many\s+people|several\s+employees).*(?:gathered|smoking\s+area)\b",
        ),
        "This is a factual report about people gathering. Keep it factual; use a neutral semantic cue rather than inventing anger or blame.",
        ("👥", "🚭"),
        "suffix",
        0.85,
        "caution",
    ),
    (
        "announcement",
        (
            r"(?:^|[\s：:])公告|通知|提醒大家|請大家注意|請大家多注意",
            r"\b(?:pengumuman|pemberitahuan|informasi\s+untuk\s+semua|harap\s+diperhatikan)\b",
            r"\b(?:announcement|notice|attention\s+everyone)\b",
        ),
        "This is an announcement or group notice. Use clear, organised workplace wording and preserve the original level of formality.",
        ("📣", "📌"),
        "prefix",
        0.91,
        "announcement",
    ),
    (
        "request",
        (
            r"請(?:幫忙|協助|確認|注意|看一下|處理|拿|放|回覆|告知|通知)|麻煩(?:你|大家)?|拜託",
            r"\b(?:tolong|mohon(?!\s+maaf)|harap|bisa\s+tolong)\b",
            r"\b(?:please|could\s+you|would\s+you)\b",
        ),
        "The speaker is making a polite request. Preserve politeness and the exact requested action without weakening it into a vague suggestion.",
        ("🙏", "🙂"),
        "suffix",
        0.89,
        "request",
    ),
    (
        "greeting",
        (
            r"早安|午安|晚安|你好|哈囉|嗨",
            r"\b(?:selamat\s+pagi|selamat\s+siang|selamat\s+sore|selamat\s+malam|halo|hai)\b",
            r"\b(?:good\s+morning|good\s+afternoon|good\s+evening|hello|hi)\b",
        ),
        "This is a greeting. Keep it friendly and natural for the target-language workplace chat.",
        ("👋", "😊"),
        "suffix",
        0.90,
        "greeting",
    ),
)


@dataclass(frozen=True)
class ToneAnalysis:
    """Deterministic, zero-network pragmatic tone signal."""

    primary: str
    confidence: float
    instruction: str
    emoji: str = ""
    placement: str = "none"
    matched_text: str = ""
    is_structured: bool = False
    visual_mood: str = ""
    emoji_choices: tuple[str, ...] = ()

    @property
    def should_decorate(self) -> bool:
        return bool((self.emoji or self.emoji_choices) and self.placement in {"prefix", "suffix"})


@dataclass(frozen=True)
class ExpressionPlan:
    """Result of sentence-aware expression enhancement."""

    text: str
    dominant_tone: str = "neutral"
    visual_mood: str = ""
    decorated_count: int = 0


def _looks_structured_or_tabular(text: str) -> bool:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    data_lines = sum(
        1
        for line in lines
        if re.search(r"\d|\t|[|｜]|(?:^|\s)[A-Z]{1,5}\d{1,8}(?:\s|$)", line)
    )
    return data_lines >= max(2, len(lines) // 2)


def _looks_factory_technical(text: str) -> bool:
    return bool(
        re.search(
            r"(?:機台|機器|設備|工單|料號|爐號|站別|站號|品保|品質|重量|尺寸|棒材|材料|"
            r"研磨|冷抽|削皮|退火|酸洗|矯直|拋光|倒角|噴砂|"
            r"\b(?:mesin|work\s+order|material|barang|stasiun|station|qc|quality|"
            r"berat|ukuran|grinding|drawing|annealing|pickling|polishing)\b|"
            r"\d+(?:\.\d+)?\s*(?:kg|g|t|mm|cm|m|%|°c|℃))",
            text or "",
            re.I,
        )
    )


def _looks_dense_technical_unit(text: str) -> bool:
    """Suppress decoration only for data-like units, not ordinary factory prose."""
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return False
    numeric_tokens = len(re.findall(r"\d+(?:\.\d+)?|[A-Z]{1,5}\d{1,8}", text or ""))
    social_cues = bool(re.search(
        r"請|大家|謝謝|抱歉|注意|小心|為什麼|怎麼|希望|辛苦|加油|"
        r"\b(?:tolong|mohon|harap|terima\s+kasih|maaf|kenapa|hati-hati|semangat)\b",
        text or "", re.I,
    ))
    return _looks_factory_technical(text) and numeric_tokens >= 2 and not social_cues


def _strip_flag_emoji(text: str) -> str:
    return _FLAG_PAIR_RE.sub("", text or "")


def _has_emotional_emoji(text: str) -> bool:
    return bool(_EMOJI_RE.search(_strip_flag_emoji(text or "")))


def _stable_pick(options: tuple[str, ...], seed_text: str) -> str:
    if not options:
        return ""
    # Python's hash is process-randomised; this tiny deterministic checksum keeps
    # repeated messages stable across workers and deploys without importing hash libs.
    checksum = sum((idx + 1) * ord(ch) for idx, ch in enumerate(seed_text or ""))
    return options[checksum % len(options)]


def _split_semantic_units(text: str) -> list[str]:
    """Split while retaining punctuation/newlines so re-joining is lossless."""
    source = str(text or "")
    if not source:
        return []
    units: list[str] = []
    pos = 0
    # Sentence punctuation or newline boundary.  Latin full stops are included
    # only when they are not decimal points. Consecutive punctuation stays with
    # the sentence, and blank lines remain their own non-semantic units.
    boundary_re = re.compile(
        r"(?:[。！？!?；;]+[\"'”’）)】》]*)|"
        r"(?:(?<!\d)\.(?!\d)[\"'”’）)】》]*)(?=\s|$)|"
        r"(?:\r?\n+)"
    )
    for match in boundary_re.finditer(source):
        end = match.end()
        if end > pos:
            units.append(source[pos:end])
        pos = end
    if pos < len(source):
        units.append(source[pos:])
    return units


def _semantic_indexes(units: list[str]) -> list[int]:
    return [idx for idx, value in enumerate(units) if value.strip()]


def _analysis_for_question(source: str, structured: bool) -> ToneAnalysis | None:
    if structured or not source.strip():
        return None
    # Question punctuation is reliable. Interrogative words without punctuation
    # are accepted only for short chat-like units.
    has_question = bool(re.search(r"[？?]", source))
    if not has_question and len(source.strip()) <= 70:
        # Do not mistake a *reported* question ("主管詢問為什麼…") for the
        # current speaker asking the reader something.
        reported_question = bool(re.search(
            r"(?:詢問|問到|問了|表示|回報|說).{0,18}(?:為什麼|怎麼|何時|哪裡)|"
            r"\b(?:menanyakan|bertanya|mengatakan|melaporkan).{0,30}"
            r"(?:mengapa|kenapa|bagaimana|kapan|di\s+mana)\b",
            source,
            re.I,
        ))
        has_question = (not reported_question) and bool(re.search(
            r"(?:嗎|呢|要不要|需不需要|可不可以|有沒有|為什麼|怎麼|何時|哪裡)|"
            r"\b(?:apakah|kenapa|mengapa|bagaimana|kapan|di\s+mana|boleh|perlu)\b",
            source, re.I,
        ))
    if not has_question:
        return None
    return ToneAnalysis(
        primary="question",
        confidence=0.88,
        instruction="This is a genuine question. Keep it conversational and preserve uncertainty, curiosity or confirmation-seeking rather than turning it into a statement.",
        emoji="🤔",
        emoji_choices=("🤔", "❓"),
        placement="suffix",
        is_structured=structured,
        visual_mood="question",
    )


def _analysis_for_instruction(source: str, structured: bool) -> ToneAnalysis | None:
    if re.search(
        r"(?:請先|先把|記得|不要忘記|務必|幫我|幫忙|先去|等.+再|要遵守|請立刻|立即分散|不要聚集)|"
        r"\b(?:pastikan|jangan\s+lupa|silakan|harap|tolong|patuhi|segera\s+berpencar)\b",
        source,
        re.I,
    ):
        urgent = bool(re.search(
            r"立即|立刻|務必|必須|不得|不可|不要聚集|請立刻|"
            r"\b(?:segera|wajib|harus|dilarang|jangan)\b",
            source,
            re.I,
        ))
        choices = () if _looks_dense_technical_unit(source) else (
            ("⚠️", "📌", "✅") if urgent else ("📌", "✅", "👀")
        )
        return ToneAnalysis(
            primary="instruction",
            confidence=0.93 if urgent else 0.86,
            instruction=(
                "This is a direct workplace instruction. Preserve who must do what, the order of actions, "
                "urgency and politeness; do not turn it into neutral information."
            ),
            emoji=(choices[0] if choices else ""),
            emoji_choices=choices,
            placement="suffix" if choices else "none",
            is_structured=structured,
            visual_mood="instruction",
        )
    return None



# Functional semantic cues make neutral workplace translations visibly expressive
# without inventing anger, affection or other emotions that are absent from the
# source.  These cues are intentionally multilingual because a single Chinese
# source sentence is often expanded into several Indonesian target sentences.
_SEMANTIC_EXPRESSION_RULES: tuple[
    tuple[str, tuple[str, ...], str, tuple[str, ...], str, float, str], ...
] = (
    (
        "limit_rule",
        (
            r"上限|下限|不得超過|不可超過|不要超過|超過規定|符合(?:規定|標準|重量)|捆包重|包重|重量範圍",
            r"\b(?:batas\s+atas|batas\s+bawah|jangan\s+melebihi|tidak\s+boleh\s+melebihi|sesuai\s+(?:ketentuan|standar)|batas\s+berat)\b",
            r"\b(?:upper\s+limit|lower\s+limit|must\s+not\s+exceed|within\s+the\s+specified\s+limit|weight\s+limit)\b",
        ),
        "This sentence states a measurable limit or compliance rule. Preserve every value and condition exactly.",
        ("⚖️", "📏", "⚠️"),
        "suffix",
        0.95,
        "rule",
    ),
    (
        "confirmation",
        (
            r"確認後|經確認|問過才|詢問後才|核准後|同意後|得到許可",
            r"\b(?:setelah\s+dikonfirmasi|harus\s+dikonfirmasi|konfirmasi|persetujuan|izin\s+terlebih\s+dahulu)\b",
            r"\b(?:after\s+confirmation|confirm(?:ed|ation)?|approval|required\s+permission)\b",
        ),
        "This action requires confirmation or approval before proceeding.",
        ("✅", "🔎", "💬"),
        "suffix",
        0.93,
        "confirmation",
    ),
    (
        "customer_feedback",
        (
            r"客戶(?:反應|反映|抱怨|客訴)|客訴|客戶意見|避免客戶",
            r"\b(?:keluhan\s+pelanggan|pelanggan.*(?:mengeluh|keluhan|komplain)|komplain\s+pelanggan)\b",
            r"\b(?:customer\s+(?:complaint|feedback)|customer.*complain)\b",
        ),
        "This sentence warns about customer feedback or complaints. Keep it factual and preventive.",
        ("💬", "⚠️", "👂"),
        "suffix",
        0.96,
        "caution",
    ),
    (
        "meeting_schedule",
        (
            r"會議|開會|班股會議|會議室集合|集合開會",
            r"\b(?:rapat|ruang\s+rapat|pertemuan|berkumpul\s+untuk\s+rapat)\b",
            r"\b(?:meeting|conference\s+room|team\s+meeting)\b",
        ),
        "This sentence announces a meeting or gathering. Preserve the date, time, place and attendees.",
        ("📅", "⏰", "📍"),
        "suffix",
        0.92,
        "schedule",
    ),
    (
        "work_schedule",
        (
            r"上班|下班|早班|夜班|小夜班|加班|交班|班別|幾點|早上|下午|晚上",
            r"\b(?:masuk\s+kerja|pulang\s+kerja|shift\s+pagi|shift\s+malam|lembur|serah\s+terima|pukul)\b",
            r"\b(?:start\s+work|finish\s+work|day\s+shift|night\s+shift|overtime|handover|at\s+\d{1,2}[:.]\d{2})\b",
        ),
        "This sentence contains a work schedule or time-sensitive instruction.",
        ("⏰", "🕒", "📅"),
        "suffix",
        0.86,
        "schedule",
    ),
    (
        "packaging",
        (
            r"包裝|捆包|捆綁|成捆|打包|包到",
            r"\b(?:kemasan|pengikatan|ikatan|dibundel|bundel|packing)\b",
            r"\b(?:packaging|bundling|bundle|packed)\b",
        ),
        "This sentence concerns packaging or bundling. Keep quantities, limits and exceptions exact.",
        ("📦", "🧰"),
        "suffix",
        0.87,
        "factory",
    ),
    (
        "exception_note",
        (
            r"特例|例外|除非|只有.*才|特殊情況",
            r"\b(?:pengecualian|kecuali|hanya\s+jika|kasus\s+khusus)\b",
            r"\b(?:exception|except\s+when|only\s+if|special\s+case)\b",
        ),
        "This sentence explains an exception or special condition. Preserve the exact boundary of the exception.",
        ("ℹ️", "🔎"),
        "suffix",
        0.85,
        "information",
    ),
    (
        "equipment_notice",
        (
            r"機台|機器|設備|故障|異常|維修|保養",
            r"\b(?:mesin|peralatan|kerusakan|gangguan|perbaikan|pemeliharaan)\b",
            r"\b(?:machine|equipment|fault|maintenance|repair)\b",
        ),
        "This sentence concerns equipment or maintenance. Keep the operational meaning precise.",
        ("🔧", "⚙️"),
        "suffix",
        0.89,
        "equipment",
    ),
    (
        "quality_notice",
        (
            r"品質|品保|檢驗|檢查|不良|規格|標準",
            r"\b(?:kualitas|qc|pemeriksaan|inspeksi|cacat|spesifikasi|standar)\b",
            r"\b(?:quality|inspection|defect|specification|standard)\b",
        ),
        "This sentence concerns quality or inspection. Preserve the acceptance criteria and result.",
        ("🔍", "✅", "📋"),
        "suffix",
        0.89,
        "quality",
    ),
    (
        "workplace_reminder",
        (
            r"請大家|請注意|多注意|記得|盡量|避免|應該|最好|仍要|還是要|需要",
            r"\b(?:mohon|harap|usahakan|hindari|sebaiknya|tetap\s+harus|perlu)\b",
            r"\b(?:please\s+note|remember|try\s+to|avoid|should|still\s+must|need\s+to)\b",
        ),
        "This is a practical workplace reminder. Keep it clear and helpful without inventing emotion.",
        ("📌", "👀", "✅"),
        "suffix",
        0.84,
        "reminder",
    ),
)

_FUNCTIONAL_SAFE_TONES = {
    "urgent_warning", "instruction", "announcement", "management_pressure",
    "crowd_report", "limit_rule", "confirmation", "customer_feedback",
    "meeting_schedule", "work_schedule", "packaging", "exception_note",
    "equipment_notice", "quality_notice", "workplace_reminder",
}


def _analysis_for_semantic_expression(source: str, structured: bool) -> ToneAnalysis | None:
    """Return a non-emotional functional cue for neutral workplace content."""
    if not source.strip():
        return None
    candidates: list[ToneAnalysis] = []
    for name, patterns, instruction, emoji_choices, placement, confidence, visual_mood in _SEMANTIC_EXPRESSION_RULES:
        for pattern in patterns:
            match = re.search(pattern, source, re.I)
            if not match:
                continue
            choices = () if structured else emoji_choices
            candidates.append(ToneAnalysis(
                primary=name,
                confidence=confidence,
                instruction=instruction,
                emoji=(choices[0] if choices else ""),
                emoji_choices=choices,
                placement=(placement if choices else "none"),
                matched_text=match.group(0),
                is_structured=structured,
                visual_mood=visual_mood,
            ))
            break
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.confidence)


def analyze_message_tone(text: str | None, language: str | None = None) -> ToneAnalysis:
    """Classify pragmatic tone without any additional AI/API request.

    The result is used both to steer wording in the existing translation call and
    to decorate individual translated sentences after semantic validation.
    """
    source = str(text or "").strip()
    structured = _looks_structured_or_tabular(source)
    if not source:
        return ToneAnalysis(
            primary="neutral",
            confidence=1.0,
            instruction="Match the source's neutral tone.",
            is_structured=structured,
        )

    candidates: list[ToneAnalysis] = []
    for name, patterns, instruction, emoji_choices, placement, confidence, visual_mood in _TONE_RULES:
        for pattern in patterns:
            match = re.search(pattern, source, re.I)
            if not match:
                continue
            choices = emoji_choices
            resolved_placement = placement
            if structured and name not in {"urgent_warning", "announcement"}:
                choices = ()
                resolved_placement = "none"
            # Routine factory requests (check a machine, move material, confirm a
            # work order) should stay professional rather than look like pleading.
            # Warm social requests can still use an expression.
            if name == "request" and _looks_factory_technical(source):
                choices = ()
                resolved_placement = "none"
            emoji = choices[0] if choices else ""
            candidates.append(ToneAnalysis(
                primary=name,
                confidence=confidence,
                instruction=instruction,
                emoji=emoji,
                emoji_choices=choices,
                placement=resolved_placement,
                matched_text=match.group(0),
                is_structured=structured,
                visual_mood=visual_mood,
            ))
            break

    question = _analysis_for_question(source, structured)
    if question:
        candidates.append(question)
    instruction = _analysis_for_instruction(source, structured)
    if instruction:
        candidates.append(instruction)
    semantic_expression = _analysis_for_semantic_expression(source, structured)
    if semantic_expression:
        candidates.append(semantic_expression)

    if candidates:
        # Prefer the strongest explicit signal rather than whichever regex happens
        # to be listed first.  Stable insertion order resolves exact ties.
        return max(candidates, key=lambda item: item.confidence)

    # Exclamation/repetition can signal cheerful energy, but only in short chat.
    if len(source) <= 80 and re.search(r"[!！]{1,}|(?:好|很|太).{0,8}(?:好|棒|讚|開心)", source):
        choices = ("😊", "✨", "😄")
        return ToneAnalysis(
            primary="positive",
            confidence=0.74,
            instruction="The sentence has light positive energy. Keep it lively and natural without inventing stronger emotion.",
            emoji=_stable_pick(choices, source),
            emoji_choices=choices,
            placement="suffix",
            is_structured=structured,
            visual_mood="joy",
        )

    return ToneAnalysis(
        primary="neutral",
        confidence=0.65,
        instruction=(
            "No strong interpersonal emotion is explicit. Preserve the source's natural level of formality, "
            "urgency and directness without inventing warmth, anger or politeness."
        ),
        is_structured=structured,
    )


def build_tone_prompt_instruction(analysis: ToneAnalysis | None) -> str:
    """Render the local analysis as a compact instruction for the existing call."""
    if not analysis:
        return ""
    return (
        "AUTOMATIC TONE ANALYSIS: "
        f"dominant_intent={analysis.primary}; confidence={analysis.confidence:.2f}. "
        f"{analysis.instruction} "
        "Preserve sentence-level emotional shifts instead of flattening the whole message into one tone. "
        "Use this signal to choose wording only. Do not output a tone label or explanation. "
        "Final emoji/image decoration is handled by the server after validation; do not invent extra emoji."
    )


def _append_emoji_to_unit(unit: str, emoji: str, placement: str) -> str:
    if not emoji or not unit.strip():
        return unit
    leading = unit[: len(unit) - len(unit.lstrip())]
    body = unit[len(leading):]
    if placement == "prefix":
        # Preserve leading language flags and place the expression after them.
        m = re.match(r"((?:[\U0001F1E6-\U0001F1FF]{2}\s*)+)", body)
        if m:
            return leading + m.group(1) + emoji + " " + body[m.end():]
        return leading + emoji + " " + body
    # Chat-natural form: sentence punctuation first, then expression.
    trailing_ws_match = re.search(r"\s*$", body)
    trailing_ws = trailing_ws_match.group(0) if trailing_ws_match else ""
    core = body[:-len(trailing_ws)] if trailing_ws else body
    # CJK chat normally places emoji directly after punctuation; Latin-script
    # languages usually read more naturally with one separating space.
    has_cjk = bool(re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", core))
    latin_count = len(re.findall(r"[A-Za-z]", core))
    cjk_dominant = has_cjk and latin_count < 3
    separator = "" if (not core or core.endswith((" ", "\t")) or cjk_dominant) else " "
    return leading + core + separator + emoji + trailing_ws


def _map_source_unit(source_units: list[str], target_position: int, target_count: int) -> str:
    meaningful = [u for u in source_units if u.strip()]
    if not meaningful:
        return ""
    if target_count <= 1 or len(meaningful) <= 1:
        return meaningful[0]
    mapped = round(target_position * (len(meaningful) - 1) / (target_count - 1))
    return meaningful[max(0, min(mapped, len(meaningful) - 1))]


def _intensity_policy(intensity: str) -> tuple[int, float]:
    value = str(intensity or "natural").strip().lower()
    if value == "balanced":
        value = "natural"
    if value == "subtle":
        return 1, 0.90
    if value == "lively":
        return 6, 0.74
    # Natural is the default: enough expression for a multi-paragraph chat,
    # but still capped so long workplace notices do not become visually noisy.
    return 4, 0.82


def _expression_priority(analysis: ToneAnalysis) -> float:
    """Rank candidates by communicative importance before applying the cap."""
    base = {
        "urgent_warning": 4.0,
        "instruction": 3.8,
        "anger": 3.7,
        "apology": 3.6,
        "gratitude": 3.5,
        "announcement": 3.4,
        "concern": 3.3,
        "question": 3.2,
        "management_pressure": 3.1,
        "crowd_report": 3.0,
        "frustration": 2.9,
        "celebration": 2.8,
        "praise": 2.7,
        "encouragement": 2.7,
        "joy": 2.6,
        "request": 2.5,
        "greeting": 2.4,
        "positive": 2.3,
        "limit_rule": 3.95,
        "confirmation": 3.65,
        "meeting_schedule": 3.55,
        "customer_feedback": 3.45,
        "quality_notice": 3.40,
        "equipment_notice": 3.40,
        "workplace_reminder": 3.25,
        "work_schedule": 3.15,
        "packaging": 3.05,
        "exception_note": 2.95,
    }.get(analysis.primary, 1.0)
    return base + float(analysis.confidence)


def build_expression_plan(
    source_text: str | None,
    translated_text: str | None,
    *,
    analysis: ToneAnalysis | None = None,
    source_language: str | None = None,
    enabled: bool = True,
    mode: str = "smart",
    intensity: str = "natural",
) -> ExpressionPlan:
    """Add sentence-aware expressions without modifying translated wording.

    Modes:
      * smart / emoji: inline sentence expressions
      * visual: no inline emoji (an optional image card can be sent by the app)
      * tone_only: wording guidance only, no decoration
    """
    result = str(translated_text or "")
    source = str(source_text or "")
    normalised_mode = str(mode or "smart").strip().lower()
    if normalised_mode == "visual":
        normalised_mode = "image"
    if normalised_mode not in {"off", "smart", "emoji", "image", "card", "tone_only"}:
        normalised_mode = "smart"
    dominant = analysis or analyze_message_tone(source, source_language)
    base_plan = ExpressionPlan(
        text=result,
        dominant_tone=dominant.primary,
        visual_mood=dominant.visual_mood,
        decorated_count=0,
    )
    if not enabled or not result.strip():
        return base_plan
    if os.environ.get("AUTO_TONE_EMOJI_ENABLED", "1").strip().lower() in {"0", "false", "off", "no"}:
        return base_plan
    if normalised_mode in {"off", "image", "card", "tone_only"}:
        return base_plan
    if _looks_structured_or_tabular(source):
        return base_plan

    source_units = _split_semantic_units(source)
    target_units = _split_semantic_units(result)
    target_indexes = _semantic_indexes(target_units)
    if not target_indexes:
        return base_plan
    max_count, min_confidence = _intensity_policy(intensity)
    tone_scores: dict[str, float] = {}
    visual_scores: dict[str, float] = {}
    candidates: list[tuple[float, int, str, ToneAnalysis]] = []

    for target_pos, unit_index in enumerate(target_indexes):
        target_unit = target_units[unit_index]
        source_unit = _map_source_unit(source_units, target_pos, len(target_indexes))
        if not source_unit.strip():
            continue
        # Existing emotional emoji only suppresses this sentence; flags do not.
        if _has_emotional_emoji(source_unit) or _has_emotional_emoji(target_unit):
            continue

        # Prefer the target sentence's own cue because one source sentence can
        # expand into several translated sentences with different functions
        # (limit, exception, confirmation, customer warning). Fall back to the
        # mapped source cue for languages not covered by local target patterns.
        source_analysis = analyze_message_tone(source_unit, source_language)
        target_analysis = analyze_message_tone(target_unit, None)
        # A non-neutral target cue is more precise than the proportionally mapped
        # source cue. This prevents one long source sentence about packaging and
        # limits from stamping the same icon onto every expanded target sentence.
        if target_analysis.primary != "neutral" and target_analysis.should_decorate:
            unit_analysis = target_analysis
        else:
            unit_analysis = source_analysis

        # Dense technical data may still receive a restrained functional marker
        # such as ⚖️/📋/🔧, but never an invented social emotion.
        if (_looks_dense_technical_unit(source_unit) or _looks_dense_technical_unit(target_unit)) \
                and unit_analysis.primary not in _FUNCTIONAL_SAFE_TONES:
            continue
        tone_scores[unit_analysis.primary] = tone_scores.get(unit_analysis.primary, 0.0) + unit_analysis.confidence
        if unit_analysis.visual_mood:
            visual_scores[unit_analysis.visual_mood] = visual_scores.get(unit_analysis.visual_mood, 0.0) + unit_analysis.confidence
        if not unit_analysis.should_decorate or unit_analysis.confidence < min_confidence:
            continue
        candidates.append((_expression_priority(unit_analysis), unit_index, source_unit, unit_analysis))

    # Select the most meaningful expressions first, then apply them in message
    # order. This prevents an early low-value icon from displacing a later safety
    # warning when the intensity cap is reached.
    selected = sorted(candidates, key=lambda item: item[0], reverse=True)[:max_count]
    selected.sort(key=lambda item: item[1])
    decorated = 0
    used_emoji: set[str] = set()
    for _priority, unit_index, source_unit, unit_analysis in selected:
        target_unit = target_units[unit_index]
        choices = unit_analysis.emoji_choices or ((unit_analysis.emoji,) if unit_analysis.emoji else ())
        if str(intensity or "natural").strip().lower() == "lively" and choices:
            emoji = _stable_pick(choices, source_unit + target_unit + unit_analysis.primary)
        else:
            emoji = unit_analysis.emoji or (choices[0] if choices else "")
        if emoji in used_emoji:
            emoji = next((candidate for candidate in choices if candidate not in used_emoji), emoji)
        if not emoji:
            continue
        target_units[unit_index] = _append_emoji_to_unit(target_unit, emoji, unit_analysis.placement)
        used_emoji.add(emoji)
        decorated += 1

    dominant_tone = max(tone_scores, key=tone_scores.get) if tone_scores else dominant.primary
    visual_mood = max(visual_scores, key=visual_scores.get) if visual_scores else dominant.visual_mood
    return ExpressionPlan(
        text="".join(target_units),
        dominant_tone=dominant_tone,
        visual_mood=visual_mood,
        decorated_count=decorated,
    )


def enrich_translation_with_tone_emoji(
    source_text: str | None,
    translated_text: str | None,
    *,
    analysis: ToneAnalysis | None = None,
    source_language: str | None = None,
    enabled: bool = True,
    mode: str = "smart",
    intensity: str = "natural",
) -> str:
    """Backward-compatible wrapper returning only the decorated text."""
    return build_expression_plan(
        source_text,
        translated_text,
        analysis=analysis,
        source_language=source_language,
        enabled=enabled,
        mode=mode,
        intensity=intensity,
    ).text


def select_expression_visual(
    source_text: str | None,
    *,
    source_language: str | None = None,
    enabled: bool = True,
    mode: str = "smart",
    max_chars: int = 100,
) -> str:
    """Return a safe optional mood-image key for short, non-technical messages.

    Serious warnings, anger, complaints and long workplace notices intentionally
    stay text-only.  This prevents a decorative image from trivialising safety or
    discipline content.  The function makes no network/API call.
    """
    source = str(source_text or "").strip()
    normalised_mode = str(mode or "smart").strip().lower()
    if normalised_mode == "visual":
        normalised_mode = "image"
    if not enabled or normalised_mode not in {"smart", "image", "card"}:
        return ""
    if not source or len(re.sub(r"\s+", "", source)) > max_chars:
        return ""
    if _looks_structured_or_tabular(source) or _looks_dense_technical_unit(source):
        return ""
    if len([u for u in _split_semantic_units(source) if u.strip()]) > 2:
        return ""
    analysis = analyze_message_tone(source, source_language)
    allowed = {
        "joy", "gratitude", "apology", "praise", "encouragement",
        "celebration", "concern", "greeting", "question",
    }
    return analysis.visual_mood if analysis.visual_mood in allowed and analysis.confidence >= 0.88 else ""

def normalize_personal_language(value: str | None) -> str | None:
    """Normalise a language code/name accepted by ``/mylang``."""
    raw = (value or "").strip().lower()
    if not raw:
        return None
    raw = raw.replace("_", "-")
    if raw in ("zh-tw", "zh-hant", "zh-hant-tw"):
        return "zh"
    if raw in SUPPORTED_PERSONAL_LANGS:
        return raw
    return LANGUAGE_ALIASES.get(raw)


@dataclass(frozen=True)
class HandoverEntry:
    timestamp: float
    sender: str
    source_language: str
    source_text: str
    translations: Mapping[str, str]


def compact_handover_entries(
    entries: Iterable[Mapping[str, Any] | HandoverEntry],
    *,
    max_entries: int = 80,
    max_chars: int = 12_000,
) -> list[dict[str, Any]]:
    """Keep the newest useful messages while preserving codes/numbers verbatim."""
    normalised: list[dict[str, Any]] = []
    for item in entries:
        if isinstance(item, HandoverEntry):
            row = {
                "timestamp": item.timestamp,
                "sender": item.sender,
                "source_language": item.source_language,
                "source_text": item.source_text,
                "translations": dict(item.translations),
            }
        elif isinstance(item, Mapping):
            row = {
                "timestamp": float(item.get("timestamp", item.get("ts", 0)) or 0),
                "sender": str(item.get("sender", "") or ""),
                "source_language": str(item.get("source_language", item.get("lang", "")) or ""),
                "source_text": str(item.get("source_text", item.get("text", "")) or "").strip(),
                "translations": dict(item.get("translations", {}) or {}),
            }
        else:
            continue
        if not row["source_text"]:
            continue
        normalised.append(row)

    normalised.sort(key=lambda row: row["timestamp"])
    normalised = normalised[-max(1, max_entries):]

    kept: list[dict[str, Any]] = []
    used = 0
    for row in reversed(normalised):
        packed = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        if kept and used + len(packed) > max_chars:
            break
        kept.append(row)
        used += len(packed)
    kept.reverse()
    return kept


def build_handover_messages(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Build a concise bilingual shift-handover request for one LLM call."""
    payload = json.dumps(list(entries), ensure_ascii=False, separators=(",", ":"))
    system = (
        "You prepare a bilingual shift handover for a stainless-steel factory. "
        "Use only facts present in the supplied messages. Never invent status, causes, owners, deadlines, "
        "or completion. Preserve every equipment code, material/order code, number, unit, time, negation, "
        "and safety restriction exactly. Merge duplicates but keep unresolved contradictions explicit. "
        "Return strict JSON only with keys zh and id. Each value must be a compact plain-text handover with "
        "these headings when applicable: 設備/Peralatan, 品質/Kualitas, 未完成/Belum selesai, "
        "下一班注意/Perhatian shift berikutnya, 工安/Keselamatan. Omit empty sections. "
        "The Indonesian must convey the same facts and strength as the Chinese."
    )
    user = (
        "Summarise the following chronological group messages. Messages may already contain translations; "
        "treat the source_text as primary and translations only as interpretation aids.\n"
        f"<messages>{payload}</messages>"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_handover_response(raw: str | None) -> dict[str, str] | None:
    """Parse JSON first, then accept a clearly separated bilingual response.

    Some provider/model combinations occasionally wrap the requested JSON in
    prose or return two markdown sections.  The handover feature must not become
    unusable merely because formatting differed, so this parser is deliberately
    tolerant while still requiring both Chinese and Indonesian content.
    """
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)

    json_text = text
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        json_text = text[start : end + 1]
    try:
        data = json.loads(json_text)
    except Exception:
        data = None
    if isinstance(data, dict):
        zh = str(data.get("zh", data.get("chinese", "")) or "").strip()
        id_text = str(data.get("id", data.get("indonesian", "")) or "").strip()
        if zh and id_text:
            return {"zh": zh, "id": id_text}

    # Markdown/plain-text fallback: split on the Indonesian section marker.
    marker = re.search(
        r"(?im)^\s*(?:#{1,4}\s*)?(?:🇮🇩\s*)?(?:bahasa\s+indonesia|indonesia|id)\s*[:：-]?\s*$",
        text,
    )
    if marker:
        zh_part = text[: marker.start()]
        id_part = text[marker.end() :]
        zh_part = re.sub(
            r"(?im)^\s*(?:#{1,4}\s*)?(?:🇹🇼\s*)?(?:中文|繁體中文|chinese|zh)\s*[:：-]?\s*$",
            "",
            zh_part,
        ).strip()
        id_part = id_part.strip()
        if zh_part and id_part:
            return {"zh": zh_part, "id": id_part}
    return None


def build_handover_fallback(
    entries: Sequence[Mapping[str, Any]],
    *,
    max_items: int = 24,
) -> dict[str, str] | None:
    """Build a deterministic bilingual handover when the LLM is unavailable.

    This is intentionally a faithful chronological digest rather than an
    invented summary.  It uses translations that were already delivered, so it
    does not make another API call and cannot lose order codes, numbers or safety
    wording through a second transformation.
    """
    rows = compact_handover_entries(entries, max_entries=max_items, max_chars=16000)
    if not rows:
        return None

    zh_lines: list[str] = []
    id_lines: list[str] = []
    seen: set[tuple[str, str]] = set()

    for row in rows[-max(1, max_items):]:
        source = str(row.get("source_text", "") or "").strip()
        source_lang = str(row.get("source_language", "") or "").strip().lower()
        translations = dict(row.get("translations", {}) or {})
        zh_text = source if source_lang == "zh" else str(translations.get("zh", "") or "").strip()
        id_text = source if source_lang == "id" else str(translations.get("id", "") or "").strip()

        # Older log rows can contain just one target translation.  Keep the
        # available side instead of dropping the operational record entirely.
        if not zh_text and source_lang != "id":
            zh_text = source
        if not id_text and source_lang != "zh":
            id_text = source
        if not zh_text and not id_text:
            continue

        pair = (zh_text, id_text)
        if pair in seen:
            continue
        seen.add(pair)

        try:
            ts = float(row.get("timestamp", row.get("ts", 0)) or 0)
            stamp = datetime.fromtimestamp(ts).strftime("%H:%M") if ts > 0 else "--:--"
        except Exception:
            stamp = "--:--"
        sender = str(row.get("sender", "") or "").strip()
        prefix = f"[{stamp}]" + (f" {sender}" if sender else "")
        if zh_text:
            zh_lines.append(f"• {prefix}：{zh_text}")
        if id_text:
            id_lines.append(f"• {prefix}: {id_text}")

    if not zh_lines and not id_lines:
        return None
    if not zh_lines:
        zh_lines = ["• 無可用中文譯文，請參考下方印尼文紀錄。"]
    if not id_lines:
        id_lines = ["• Terjemahan bahasa Indonesia belum tersedia; lihat catatan bahasa Mandarin di atas."]
    return {
        "zh": "最近翻譯紀錄（自動備援）\n" + "\n".join(zh_lines),
        "id": "Catatan terjemahan terbaru (cadangan otomatis)\n" + "\n".join(id_lines),
    }


def _font_candidates(bold: bool = False) -> tuple[str, ...]:
    names = (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    return tuple(path for path in names if os.path.exists(path))


def _load_font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    for path in _font_candidates(bold=bold):
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_pixel(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in (text or "").splitlines() or [""]:
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for ch in paragraph:
            trial = current + ch
            try:
                width = draw.textlength(trial, font=font)
            except Exception:
                width = len(trial) * 16
            if current and width > max_width:
                lines.append(current)
                current = ch
            else:
                current = trial
        lines.append(current)
    return lines


def render_translation_comparison_image(
    image_bytes: bytes,
    source_text: str,
    translated_text: str,
    *,
    source_label: str = "原文",
    target_label: str = "譯文",
    max_image_width: int = 1100,
    jpeg_quality: int = 88,
) -> bytes:
    """Render an original-image + readable translation panel as a JPEG.

    A comparison panel is deliberately used instead of guessing OCR coordinates.
    It preserves the original pixels and makes every translated line auditable.
    """
    if not image_bytes:
        raise ValueError("image_bytes is empty")
    if not translated_text or not translated_text.strip():
        raise ValueError("translated_text is empty")

    from PIL import Image, ImageDraw, ImageOps

    with Image.open(io.BytesIO(image_bytes)) as opened:
        original = ImageOps.exif_transpose(opened).convert("RGB")
    if original.width > max_image_width:
        ratio = max_image_width / float(original.width)
        original = original.resize((max_image_width, max(1, int(original.height * ratio))))

    panel_width = max(420, min(900, original.width))
    margin = 30
    body_font_size = max(22, min(38, panel_width // 24))
    title_font = _load_font(body_font_size + 6, bold=True)
    body_font = _load_font(body_font_size)
    small_font = _load_font(max(16, body_font_size - 6))

    probe = Image.new("RGB", (panel_width, 100), "white")
    probe_draw = ImageDraw.Draw(probe)
    source_lines = _wrap_pixel(probe_draw, source_text.strip(), small_font, panel_width - margin * 2)
    target_lines = _wrap_pixel(probe_draw, translated_text.strip(), body_font, panel_width - margin * 2)
    line_h = body_font_size + 12
    small_h = max(22, body_font_size)
    panel_height = (
        margin + (body_font_size + 12) + 12
        + max(1, len(target_lines)) * line_h
        + 26 + (body_font_size + 6) + 10
        + max(1, len(source_lines)) * small_h
        + margin
    )
    panel_height = max(panel_height, original.height)

    canvas = Image.new("RGB", (original.width + panel_width, panel_height), (245, 247, 250))
    y_image = max(0, (panel_height - original.height) // 2)
    canvas.paste(original, (0, y_image))
    draw = ImageDraw.Draw(canvas)
    x0 = original.width
    draw.rectangle((x0, 0, x0 + panel_width, panel_height), fill=(248, 250, 252))
    draw.rectangle((x0, 0, x0 + 8, panel_height), fill=(42, 157, 143))

    x = x0 + margin
    y = margin
    draw.text((x, y), target_label, font=title_font, fill=(20, 35, 50))
    y += body_font_size + 22
    for line in target_lines:
        draw.text((x, y), line or " ", font=body_font, fill=(10, 20, 30))
        y += line_h

    y += 14
    draw.line((x, y, x0 + panel_width - margin, y), fill=(205, 212, 220), width=2)
    y += 18
    draw.text((x, y), source_label, font=title_font, fill=(80, 90, 105))
    y += body_font_size + 16
    for line in source_lines:
        draw.text((x, y), line or " ", font=small_font, fill=(90, 100, 115))
        y += small_h

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=max(70, min(95, jpeg_quality)), optimize=True)
    return out.getvalue()


def build_interpreter_html(*, liff_id: str = "") -> str:
    """Return a mobile-first, turn-by-turn voice interpreter UI."""
    safe_liff = html.escape(liff_id or "", quote=True)
    options = "".join(
        f'<option value="{code}">{html.escape(label)}</option>'
        for code, label in LANGUAGE_LABELS.items()
        if code != "auto"
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="liff-id" content="{safe_liff}">
<title>即時雙向口譯</title>
<style>
:root{{--bg:#0d1321;--card:#172038;--text:#f7fafc;--muted:#9ca9bd;--accent:#18a999;--danger:#ef5b5b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,"Noto Sans TC",sans-serif}}
main{{max-width:720px;margin:auto;padding:18px}}h1{{font-size:22px;margin:4px 0 6px}}.sub{{color:var(--muted);font-size:13px;margin-bottom:16px}}
.card{{background:var(--card);border-radius:16px;padding:16px;margin-bottom:14px;box-shadow:0 10px 25px #0004}}
.row{{display:grid;grid-template-columns:1fr 54px 1fr;gap:8px;align-items:center}}select,button{{font:inherit;border:0;border-radius:12px}}
select{{width:100%;padding:12px;background:#25304c;color:var(--text)}}button{{cursor:pointer}}#swap{{height:44px;background:#25304c;color:white}}
#record{{width:150px;height:150px;border-radius:50%;display:block;margin:22px auto;background:var(--accent);color:white;font-size:20px;font-weight:700;box-shadow:0 0 0 12px #18a99922}}
#record.recording{{background:var(--danger);box-shadow:0 0 0 12px #ef5b5b22}}#status{{text-align:center;color:var(--muted);min-height:24px}}
.label{{font-size:12px;color:var(--muted);margin-bottom:7px}}.text{{white-space:pre-wrap;line-height:1.6;min-height:46px}}
.actions{{display:flex;gap:8px;margin-top:10px}}.actions button{{padding:10px 14px;background:#25304c;color:white;flex:1}}
small{{color:var(--muted)}}
</style>
<script src="https://static.line-scdn.net/liff/edge/2/sdk.js"></script>
</head>
<body><main>
<h1>🎙️ 即時雙向口譯</h1><div class="sub">按一下開始說話，再按一下停止。系統會辨識、翻譯並播放譯文。</div>
<div class="card"><div class="row"><select id="src">{options}</select><button id="swap">⇄</button><select id="tgt">{options}</select></div>
<button id="record">開始說話</button><div id="status">準備完成</div></div>
<div class="card"><div class="label">辨識原文</div><div class="text" id="transcript">—</div></div>
<div class="card"><div class="label">翻譯結果</div><div class="text" id="translation">—</div><div class="actions"><button id="play">🔊 播放譯文</button><button id="reverse">↩ 交換再說</button></div></div>
<small>此模式採短回合錄音，避免長時間開啟麥克風；設備代號、數字與單位會交由相同翻譯品質管線處理。</small>
<audio id="audio" preload="none"></audio>
</main>
<script>
const NONCE=new URLSearchParams(location.search).get('nonce')||'';
const src=document.getElementById('src'),tgt=document.getElementById('tgt');src.value='zh';tgt.value='id';
let recorder=null,chunks=[],audioUrl='';
async function initLiff(){{try{{const id=document.querySelector('meta[name="liff-id"]').content;if(id&&window.liff)await liff.init({{liffId:id}})}}catch(e){{}}}}
function swap(){{const a=src.value;src.value=tgt.value;tgt.value=a}}document.getElementById('swap').onclick=swap;document.getElementById('reverse').onclick=swap;
document.getElementById('play').onclick=()=>{{if(audioUrl){{const a=document.getElementById('audio');a.src=audioUrl;a.play()}}}};
async function start(){{
 const stream=await navigator.mediaDevices.getUserMedia({{audio:true}});chunks=[];
 const preferred=['audio/webm;codecs=opus','audio/mp4','audio/webm'].find(x=>MediaRecorder.isTypeSupported(x));
 recorder=new MediaRecorder(stream,preferred?{{mimeType:preferred}}:undefined);recorder.ondataavailable=e=>{{if(e.data.size)chunks.push(e.data)}};
 recorder.onstop=async()=>{{stream.getTracks().forEach(t=>t.stop());await send(new Blob(chunks,{{type:recorder.mimeType||'audio/webm'}}))}};
 recorder.start();document.getElementById('record').classList.add('recording');document.getElementById('record').textContent='停止並翻譯';document.getElementById('status').textContent='正在聆聽…';
}}
async function stop(){{if(recorder&&recorder.state==='recording')recorder.stop();document.getElementById('record').classList.remove('recording');document.getElementById('record').textContent='開始說話';document.getElementById('status').textContent='辨識與翻譯中…'}}
document.getElementById('record').onclick=async()=>{{try{{if(recorder&&recorder.state==='recording')await stop();else await start()}}catch(e){{document.getElementById('status').textContent='無法使用麥克風：'+e.message}}}};
async function send(blob){{
 const fd=new FormData();fd.append('audio',blob,'speech.webm');fd.append('nonce',NONCE);fd.append('source',src.value);fd.append('target',tgt.value);fd.append('speak','1');
 try{{const r=await fetch('/api/interpreter/translate',{{method:'POST',body:fd}});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||r.status);
 document.getElementById('transcript').textContent=d.transcript||'—';document.getElementById('translation').textContent=d.translation||'—';audioUrl=d.audio_url||'';
 document.getElementById('status').textContent='完成';if(audioUrl){{const a=document.getElementById('audio');a.src=audioUrl;await a.play().catch(()=>{{}})}}
 }}catch(e){{document.getElementById('status').textContent='失敗：'+e.message}}
}}
initLiff();
</script></body></html>"""
