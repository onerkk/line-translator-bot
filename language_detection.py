"""
language_detection.py — Language Auto-Detection v1.0 (2026-05-20)

業界標準語言偵測,基於 Unicode codepoint 分布。
不依賴第三方 lib(避免 langdetect / pycld 的部署複雜度)。

【支援語言】
- zh (中文,簡體+繁體合併 — bot 透過內容區分)
- id (印尼文)
- en (英文)
- ja (日文)
- ko (韓文)
- th (泰文)
- vi (越南文,latin + diacritic)
- hi (印地文)
- mixed (混合,降為 unknown)

【演算法】
- 對每段文字計算 Unicode block 命中比例
- 主導 block 占比 > 60% → 該語言
- 否則 → unknown

【為什麼自做不用 langdetect】
- langdetect 在短訊息(<10 字)準確度差
- 工廠 LINE 訊息常短(「OK」「了解」)
- Unicode block 對中/日/韓/泰/印地語極可靠
- 印尼/英文/越南都是 latin → 用 keyword + 機率 fallback
"""

import logging
import re
from typing import Optional, Dict, Any, Iterable, List

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Unicode block 範圍(主要 Asian + Latin)
# ═══════════════════════════════════════════════════════════════════
BLOCKS = {
    "cjk": (0x4E00, 0x9FFF),         # CJK 主要漢字
    "cjk_ext": (0x3400, 0x4DBF),     # CJK 擴充 A
    "hiragana": (0x3040, 0x309F),    # 日文平假名(獨家標記:有 → 一定是日文)
    "katakana": (0x30A0, 0x30FF),    # 日文片假名
    "hangul": (0xAC00, 0xD7AF),      # 韓文諺文(獨家)
    "thai": (0x0E00, 0x0E7F),        # 泰文(獨家)
    "devanagari": (0x0900, 0x097F),  # 印地文(獨家)
    "arabic": (0x0600, 0x06FF),      # 阿拉伯文(獨家)
}

# Latin-based 語言關鍵字(印尼 / 英文 / 越南)
ID_KEYWORDS = {
    "yang", "dan", "ini", "itu", "untuk", "dengan", "atau", "tidak", "sudah",
    "akan", "ada", "saya", "kamu", "anda", "kita", "harus", "bisa", "boleh",
    "sangat", "juga", "tapi", "kalau", "kalo", "udah", "belum", "lagi", "sama",
    "dari", "ke", "di", "pada", "oleh", "tentang", "karena", "supaya",
}

EN_KEYWORDS = {
    "the", "and", "is", "are", "was", "were", "have", "has", "had", "for",
    "with", "this", "that", "these", "those", "from", "what", "when", "where",
    "who", "how", "you", "your", "they", "their", "them", "but", "not",
    "would", "could", "should", "will", "can", "may", "might", "must",
    # 常見短語 / 招呼語
    "good", "morning", "afternoon", "evening", "night", "hello", "hi",
    "thanks", "thank", "please", "sorry", "yes", "no", "ok", "okay",
    "everyone", "everybody", "today", "tomorrow", "yesterday",
    "do", "did", "does", "be", "been", "being", "am",
    "now", "later", "then", "here", "there", "out", "in", "on", "at",
    "all", "some", "any", "many", "much", "few", "just",
}

VI_KEYWORDS = {
    "của", "và", "là", "có", "không", "được", "trong", "với", "cho", "thì",
    "khi", "đã", "sẽ", "này", "đó", "tôi", "bạn", "anh", "chị", "em",
    "phải", "nên", "vì", "nếu", "mà", "rồi", "đang", "rất", "cũng",
}

_TECH_ACRONYMS = {
    "AI", "API", "CNC", "ERP", "HMI", "ID", "LINE", "MES", "NG", "OCR",
    "OK", "PLC", "PPE", "QA", "QC", "RPM", "SOP", "TAG", "TIG", "UI",
    "UPS", "URL", "UT", "WIP", "WO",
}
_IDENTITY_PLACEHOLDER_RE = re.compile(
    r"(?:__QG_KEEP_\d{3}_[0-9A-F]{8}__|__MENTION_\d+__|__PERSON_\d+__|__CUST_\d+__)",
    re.I,
)


# ═══════════════════════════════════════════════════════════════════
# 統計
# ═══════════════════════════════════════════════════════════════════
import threading
_lock = threading.RLock()
_stats = {
    "detections": 0,
    "by_language": {},
}


# ═══════════════════════════════════════════════════════════════════
# 核心偵測
# ═══════════════════════════════════════════════════════════════════
def detect_language(text: str) -> Dict[str, Any]:
    """偵測文本主要語言
    
    Returns: {
        "primary": "zh" | "id" | "en" | "ja" | "ko" | "th" | "vi" | "hi" | "ar" | "unknown",
        "confidence": 0.0-1.0,
        "is_mixed": bool,
        "block_ratios": {"cjk": 0.5, "latin": 0.3, ...} (詳細分布)
    }
    """
    # 防呆:非 str 一律當 unknown
    if not isinstance(text, str):
        return {"primary": "unknown", "confidence": 0.0, "is_mixed": False, "block_ratios": {}}
    if not text or not text.strip():
        return {"primary": "unknown", "confidence": 0.0, "is_mixed": False, "block_ratios": {}}
    
    with _lock:
        _stats["detections"] += 1
    
    # 去除符號跟空白
    cleaned = re.sub(r"[\s\d\W_]+", "", text)
    if not cleaned:
        # 全是符號/數字 → 看 ASCII 字母
        cleaned = re.sub(r"[\s\d]+", "", text)
        if not cleaned:
            return {"primary": "unknown", "confidence": 0.0, "is_mixed": False, "block_ratios": {}}
    
    total = len(cleaned)
    counters = {block_name: 0 for block_name in BLOCKS}
    counters["latin"] = 0
    counters["other"] = 0
    
    for ch in cleaned:
        cp = ord(ch)
        matched_block = None
        for block_name, (lo, hi) in BLOCKS.items():
            if lo <= cp <= hi:
                counters[block_name] += 1
                matched_block = block_name
                break
        if matched_block is None:
            if 0x0041 <= cp <= 0x024F or 0x1E00 <= cp <= 0x1EFF:
                # Latin basic + extended + Vietnamese diacritics
                counters["latin"] += 1
            else:
                counters["other"] += 1
    
    block_ratios = {k: round(v / total, 3) for k, v in counters.items() if v > 0}
    
    # ─── 獨家 Unicode block 判斷(高信心)───
    # 有 Hiragana/Katakana → 日文(中文不會有)
    if counters["hiragana"] > 0 or counters["katakana"] > 0:
        lang = "ja"
        confidence = (counters["hiragana"] + counters["katakana"] + counters["cjk"]) / total
    elif counters["hangul"] > 0:
        lang = "ko"
        confidence = counters["hangul"] / total
    elif counters["thai"] > 0:
        lang = "th"
        confidence = counters["thai"] / total
    elif counters["devanagari"] > 0:
        lang = "hi"
        confidence = counters["devanagari"] / total
    elif counters["arabic"] > 0:
        lang = "ar"
        confidence = counters["arabic"] / total
    elif counters["cjk"] > 0 or counters["cjk_ext"] > 0:
        # CJK 漢字 → 中文(因為日韓已先過濾)
        lang = "zh"
        confidence = (counters["cjk"] + counters["cjk_ext"]) / total
    elif counters["latin"] > 0:
        # Latin → 細分印尼 / 英文 / 越南
        lang, confidence = _classify_latin(text)
    else:
        lang = "unknown"
        confidence = 0.0
    
    # 混合判定:主導 block < 80% 算 mixed(但仍回主導)
    is_mixed = confidence < 0.8
    
    with _lock:
        _stats["by_language"][lang] = _stats["by_language"].get(lang, 0) + 1
    
    return {
        "primary": lang,
        "confidence": round(confidence, 3),
        "is_mixed": is_mixed,
        "block_ratios": block_ratios,
    }


def analyze_code_switching(
    text: str,
    src_lang: str = "",
    tgt_lang: str = "",
    protected_literals: Iterable[str] = (),
) -> Dict[str, Any]:
    """Detect genuine natural-language code switching without another API call.

    ``detect_language`` intentionally chooses one dominant language because the
    LINE router needs a single source code.  This companion profile records the
    secondary natural-language spans so the translator is explicitly told to
    translate them too.  Equipment codes and protected names do not trigger it.
    """
    value = str(text or "")
    for literal in sorted(
        {str(item or "").strip() for item in protected_literals if str(item or "").strip()},
        key=len,
        reverse=True,
    ):
        value = value.replace(literal, " ")
    value = _IDENTITY_PLACEHOLDER_RE.sub(" ", value)

    latin_tokens = re.findall(r"(?<![A-Za-z])([A-Za-zÀ-ÖØ-öø-ÿ]{2,})(?![A-Za-z])", value)
    prose_tokens: List[str] = [
        token for token in latin_tokens
        if token.upper() not in _TECH_ACRONYMS
    ]
    latin_lower = [token.lower() for token in prose_tokens]
    latin_hits = {
        "id": sum(token in ID_KEYWORDS for token in latin_lower),
        "en": sum(token in EN_KEYWORDS for token in latin_lower),
        "vi": sum(token in VI_KEYWORDS for token in latin_lower),
    }
    latin_is_prose = bool(
        len(prose_tokens) >= 2
        and (max(latin_hits.values(), default=0) >= 1 or len(prose_tokens) >= 4)
    )
    latin_language = "unknown"
    if latin_is_prose:
        probe = " ".join(prose_tokens)
        latin_language, _confidence = _classify_latin(probe)

    script_counts = {
        "zh": len(re.findall(r"[\u3400-\u9fff]", value)),
        "ja": len(re.findall(r"[\u3040-\u30ff]", value)),
        "ko": len(re.findall(r"[\uac00-\ud7af]", value)),
        "th": len(re.findall(r"[\u0e00-\u0e7f]", value)),
        "hi": len(re.findall(r"[\u0900-\u097f]", value)),
    }
    languages: List[str] = []
    if script_counts["ja"]:
        languages.append("ja")
    elif script_counts["zh"] >= 2:
        languages.append("zh")
    for lang in ("ko", "th", "hi"):
        if script_counts[lang] >= 2:
            languages.append(lang)
    if latin_is_prose:
        languages.append(latin_language)
    languages = list(dict.fromkeys(lang for lang in languages if lang != "unknown"))

    is_mixed = len(languages) >= 2
    # Two Latin languages cannot be reliably segmented by a local keyword probe;
    # report only script-grounded mixtures and avoid over-directing the model.
    return {
        "is_mixed": is_mixed,
        "languages": languages,
        "source_language": str(src_lang or "").lower(),
        "target_language": str(tgt_lang or "").lower(),
        "latin_language": latin_language,
        "latin_tokens": len(prose_tokens),
        "script_counts": script_counts,
    }


def code_switching_instruction(profile: Dict[str, Any]) -> str:
    """Build a compact provider instruction; returns empty for normal text."""
    if not isinstance(profile, dict) or not profile.get("is_mixed"):
        return ""
    languages = ", ".join(str(item) for item in profile.get("languages", []) if item)
    return (
        "<code_switching>\n"
        "The source intentionally mixes natural-language spans"
        + (" (detected: " + languages + ")" if languages else "")
        + ". Translate every natural-language span into the requested target language, "
        "including a secondary-language span. Preserve only names, technical codes, "
        "approved factory terms and placeholders; do not leave ordinary secondary-language "
        "instructions untranslated. Use one coherent target-language message.\n"
        "</code_switching>"
    )


def _classify_latin(text: str) -> tuple:
    """Latin-based 語言細分:印尼 / 英文 / 越南
    
    用 keyword frequency
    """
    text_lower = text.lower()
    
    # 越南文有 Vietnamese diacritics(ơ, ư, đ, ă, â, ê, ô, etc)
    has_vi_diacritic = bool(re.search(r"[ơưđăâêôỉễếốồớờứừửỡãõẽọẹệìíỳýỵảĩũạụ]", text_lower))
    if has_vi_diacritic:
        return "vi", 0.9
    
    # Tokenize 找關鍵字
    tokens = re.findall(r"\b[a-z]+\b", text_lower)
    if not tokens:
        return "unknown", 0.0
    
    id_hits = sum(1 for t in tokens if t in ID_KEYWORDS)
    en_hits = sum(1 for t in tokens if t in EN_KEYWORDS)
    vi_hits = sum(1 for t in tokens if t in VI_KEYWORDS)
    
    total_hits = id_hits + en_hits + vi_hits
    if total_hits == 0:
        # 完全沒命中 → 短英文預設(LINE 工廠群組以印尼/英文為主)
        # 若 token 數 > 3,印尼比英文常見的可能性大(工廠場景)
        return "en" if len(tokens) <= 2 else "id", 0.4
    
    max_hits = max(id_hits, en_hits, vi_hits)
    confidence = max_hits / max(total_hits, len(tokens))
    if id_hits == max_hits:
        return "id", min(0.95, confidence + 0.2)
    elif en_hits == max_hits:
        return "en", min(0.95, confidence + 0.2)
    else:
        return "vi", min(0.95, confidence + 0.2)


# ═══════════════════════════════════════════════════════════════════
# 統計 API
# ═══════════════════════════════════════════════════════════════════
def ld_stats() -> Dict[str, Any]:
    with _lock:
        s = dict(_stats)
        s["by_language"] = dict(s["by_language"])
    return s
