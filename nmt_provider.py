"""
nmt_provider.py — Conservative Hybrid NMT + LLM Router (2026-07-12)

核心原則：
- NMT 只處理明確、低風險、無工廠語意的日常短句。
- 中文↔印尼文採白名單：未命中安全日常句，就交給 LLM。
- 工廠術語、設備代號、命令、否定、數字單位、料號、品質與工安內容一律走 LLM。
- 不以「句子短」推定「語意簡單」，避免短工廠指令被快速模型誤譯。

支援的 NMT：
- Google Cloud Translation API v2
- DeepL API

路由結果可透過 ``nmt_route_reason`` 取得，供效能統計與測試使用。
"""

import os
import json
import logging
import threading
import re
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════
NMT_PROVIDER = os.environ.get("NMT_PROVIDER", "google").lower()  # "google" | "deepl" | "none"
NMT_SHORT_THRESHOLD = 30  # 字數 < 此值才考慮 NMT
NMT_TIMEOUT = 10  # API timeout 秒
NMT_POST_EDIT = False  # NMT 翻完是否再過 LLM 精修(預設關,需要時開)

try:
    import phase_config_store as _pcs
    _saved = _pcs.load_config("nmt")
    if _saved:
        NMT_PROVIDER = _saved.get("provider", NMT_PROVIDER)
        NMT_SHORT_THRESHOLD = _saved.get("short_threshold", NMT_SHORT_THRESHOLD)
        NMT_POST_EDIT = _saved.get("post_edit", NMT_POST_EDIT)
        logger.info("[NMT] loaded persisted config: %s", _saved)
except Exception as _e:
    logger.warning("[NMT] load persisted config failed: %s", _e)

_lock = threading.RLock()
_stats = {
    "route_to_nmt": 0,
    "route_to_llm": 0,
    "nmt_success": 0,
    "nmt_failed": 0,
    "nmt_chars_translated": 0,
    "estimated_cost_usd": 0.0,
}


# Google Translate 計費:$20/M chars,DeepL $25/M chars
NMT_PRICE_PER_M_CHARS = {
    "google": 20.0,
    "deepl": 25.0,
}


# ═══════════════════════════════════════════════════════════════════
# 路由邏輯:NMT vs LLM
# ═══════════════════════════════════════════════════════════════════
# Only genuinely low-risk chat should use NMT.  Factory commands are often
# short, but their negation, equipment codes and local terminology make them
# semantically high-risk.  The router therefore uses an allow-list posture for
# ZH<->ID while remaining more permissive for other language pairs.
_FACTORY_RISK_RE = re.compile(
    r"(?:"
    r"工單|料號|爐號|站別|站號|品保|QC|停機|開機|調機|維修|異常|刮傷|缺陷|不良|"
    r"研磨|無心|削皮|冷抽|退火|酸洗|矯直|倒角|拋光|噴漆|洗料|"
    r"上料|下料|入料|出料|送料|吊料|棒材|盤元|母材|線材|來料|卡料|斷料|混料|"
    r"放行|過帳|退庫|發料|工安|職災|PPE|LOTO|SOP|interlock|bypass|"
    r"\b(?:I\d{1,2}|E\d{1,2}|BF\d+|AP|PM\d+|UT|K\d+)\b|"
    r"\b(?:work\s*order|material|mesin|produksi|operator|kualitas|cacat|rusak|macet|"
    r"berhenti|nyalakan|matikan|perbaikan|release|gudang|stasiun)\b"
    r")",
    re.I,
)
_NEGATION_OR_COMMAND_RE = re.compile(
    r"(?:不要|不得|不能|不可|禁止|務必|必須|先|再|等.+後|確認後|請|麻煩|幫忙|"
    r"\b(?:jangan|tidak\s+boleh|dilarang|wajib|harus|tolong|mohon|sebelum|sesudah)\b)",
    re.I,
)
_DATA_RISK_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:mm|cm|m|kg|g|t|噸|公斤|支|把|台|件|批|捆|°C|℃|%)|"
    r"[<>≤≥±×x]|\b[A-Z]{1,5}[-_/]?\d{2,10}\b)",
    re.I,
)
_SAFE_ZH_CHAT_RE = re.compile(
    r"^(?:早安|午安|晚安|謝謝|感謝|辛苦了|收到|了解|知道了|好|好的|可以|沒問題|"
    r"等一下|稍等|我到了|我先走了|我先回去了|吃飯了嗎|吃飽了嗎|今天加班嗎|明天見|再見|掰掰|"
    r"哈哈|呵呵|沒事|不用客氣|不客氣)[。！!？?~～\s]*$"
)
_SAFE_ID_CHAT_RE = re.compile(
    r"^(?:selamat\s+(?:pagi|siang|sore|malam)|terima\s+kasih|makasih|thanks|ok|oke|siap|"
    r"baik|mengerti|paham|sudah|belum|sebentar|tunggu\s+sebentar|sampai\s+besok|sampai\s+jumpa|"
    r"tidak\s+apa-?apa|sama-?sama|haha+)[.!?~\s]*$",
    re.I,
)


def nmt_route_reason(text: str, src_lang: str, tgt_lang: str,
                     factory_glossary: Optional[set] = None) -> tuple[bool, str]:
    """Return ``(use_nmt, reason)`` for diagnostics and tests."""
    if NMT_PROVIDER == "none":
        return False, "provider_disabled"
    if not text or not text.strip():
        return False, "empty"
    text = text.strip()
    src = (src_lang or "").lower()
    tgt = (tgt_lang or "").lower()

    if len(text) >= NMT_SHORT_THRESHOLD:
        return False, "too_long"
    if any(ord(c) > 0x1F000 for c in text):
        return False, "emoji_context"
    if "@" in text or "__MENTION_" in text or "__CUST_" in text or "__QG_KEEP_" in text:
        return False, "protected_token"
    if factory_glossary:
        for term in factory_glossary:
            if term and len(str(term)) >= 2 and str(term) in text:
                return False, "glossary_term"
    if _FACTORY_RISK_RE.search(text):
        return False, "factory_semantics"
    if _NEGATION_OR_COMMAND_RE.search(text):
        return False, "command_or_negation"
    if _DATA_RISK_RE.search(text):
        return False, "data_or_code"

    tw_colloquial = ("啦", "喔", "哦", "嘛", "醬", "降", "咧", "蛤", "厚", "ㄏㄏ", "QQ", "3Q", "感溫", "傻眼", "母湯", "出包")
    if any(c in text for c in tw_colloquial):
        return False, "taiwan_colloquial"
    id_colloquial = ("bgt", "gak", "udh", "udah", "gimana", "ngapain", "gw", "lu", "dong", "nih", "sih", "lho")
    if any(re.search(r"(?<![a-z])" + re.escape(c) + r"(?![a-z])", text.lower()) for c in id_colloquial):
        return False, "indonesian_colloquial"
    if "__" in text:
        return False, "placeholder"

    # ZH<->ID is the critical production direction: only explicit, harmless
    # conversational phrases are eligible. Unknown short phrases go to LLM.
    if src.startswith("zh") and tgt.startswith("id"):
        return (True, "safe_chat_allowlist") if _SAFE_ZH_CHAT_RE.fullmatch(text) else (False, "not_safe_chat_allowlist")
    if src.startswith("id") and tgt.startswith("zh"):
        return (True, "safe_chat_allowlist") if _SAFE_ID_CHAT_RE.fullmatch(text) else (False, "not_safe_chat_allowlist")

    return True, "generic_low_risk"


def should_use_nmt(text: str, src_lang: str, tgt_lang: str,
                   factory_glossary: Optional[set] = None) -> bool:
    """Conservative NMT routing. True only for low-risk chat."""
    decision, reason = nmt_route_reason(text, src_lang, tgt_lang, factory_glossary)
    logger.debug("[NMT] route=%s reason=%s text=%r", decision, reason, (text or "")[:80])
    return decision


# ═══════════════════════════════════════════════════════════════════
# Google Cloud Translation API v2
# ═══════════════════════════════════════════════════════════════════
def _google_translate(text: str, src: str, tgt: str) -> Optional[str]:
    """Google Cloud Translation API v2 (REST)
    
    需要環境變數 GOOGLE_TRANSLATE_API_KEY 或 GOOGLE_API_KEY
    https://cloud.google.com/translate/docs/reference/rest/v2/translate
    """
    api_key = (os.environ.get("GOOGLE_TRANSLATE_API_KEY")
               or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        logger.warning("[NMT] no GOOGLE_TRANSLATE_API_KEY")
        return None
    
    # Google 語言碼對應(大多直接相容)
    lang_map = {"zh": "zh-TW", "zh-cn": "zh-CN", "zh-hant": "zh-TW", "zh-hans": "zh-CN"}
    g_src = lang_map.get(src.lower(), src)
    g_tgt = lang_map.get(tgt.lower(), tgt)
    
    url = "https://translation.googleapis.com/language/translate/v2"
    body = urllib.parse.urlencode({
        "key": api_key,
        "q": text,
        "source": g_src,
        "target": g_tgt,
        "format": "text",
    }).encode("utf-8")
    
    try:
        req = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=NMT_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        translations = data.get("data", {}).get("translations", [])
        if not translations:
            return None
        return translations[0].get("translatedText", "").strip()
    except urllib.error.HTTPError as e:
        logger.warning("[NMT] Google HTTP error: %s %s", e.code, e.reason)
        return None
    except Exception as e:
        logger.warning("[NMT] Google failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════
# DeepL API
# ═══════════════════════════════════════════════════════════════════
def _deepl_translate(text: str, src: str, tgt: str) -> Optional[str]:
    """DeepL API (https://developers.deepl.com/docs/api-reference/translate)
    
    需要環境變數 DEEPL_API_KEY
    DeepL 印尼語(ID)是 2024 新增,品質次要,優先建議用 Google
    """
    api_key = os.environ.get("DEEPL_API_KEY", "").strip()
    if not api_key:
        return None
    
    # DeepL 用 free 還是 pro endpoint
    base = "https://api-free.deepl.com" if api_key.endswith(":fx") else "https://api.deepl.com"
    url = f"{base}/v2/translate"
    
    # 語言碼:中文 ZH,印尼 ID,英文 EN
    lang_map = {"zh": "ZH", "id": "ID", "en": "EN", "ja": "JA", "ko": "KO",
                "th": "TH", "vi": "VI"}
    d_src = lang_map.get(src.lower(), src.upper())
    d_tgt = lang_map.get(tgt.lower(), tgt.upper())
    
    body = urllib.parse.urlencode({
        "text": text,
        "source_lang": d_src,
        "target_lang": d_tgt,
    }).encode("utf-8")
    
    try:
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"DeepL-Auth-Key {api_key}")
        with urllib.request.urlopen(req, timeout=NMT_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        translations = data.get("translations", [])
        if not translations:
            return None
        return translations[0].get("text", "").strip()
    except Exception as e:
        logger.warning("[NMT] DeepL failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════
# 統一介面
# ═══════════════════════════════════════════════════════════════════
def nmt_translate(text: str, src: str, tgt: str) -> Optional[str]:
    """統一 NMT 呼叫,依 NMT_PROVIDER 路由
    
    Returns: 譯文 str 或 None(失敗)
    """
    if not text or not text.strip():
        return None
    
    with _lock:
        _stats["route_to_nmt"] += 1
    
    if NMT_PROVIDER == "google":
        result = _google_translate(text, src, tgt)
    elif NMT_PROVIDER == "deepl":
        result = _deepl_translate(text, src, tgt)
    else:
        return None
    
    if result:
        with _lock:
            _stats["nmt_success"] += 1
            _stats["nmt_chars_translated"] += len(text)
            price_per_m = NMT_PRICE_PER_M_CHARS.get(NMT_PROVIDER, 20.0)
            _stats["estimated_cost_usd"] = round(
                _stats["nmt_chars_translated"] * price_per_m / 1_000_000, 6
            )
        logger.info("[NMT] %s success: %d chars %s→%s", NMT_PROVIDER, len(text), src, tgt)
    else:
        with _lock:
            _stats["nmt_failed"] += 1
    
    return result


def llm_post_edit(nmt_text: str, src_text: str, src: str, tgt: str,
                  llm_callable=None) -> Optional[str]:
    """LLM post-editing(可選功能,預設關)
    
    把 NMT 翻譯結果送給 LLM 精修。用於:
    - NMT 翻譯結構正確但語氣偏弱
    - 含小量俚語但被 NMT 翻得太正式
    
    Args:
        nmt_text: NMT 翻譯結果
        src_text: 原文
        src, tgt: 語言碼
        llm_callable: 接受 (text, src, tgt) 回傳 str 的函數(通常是 translate_openai)
    
    Returns: 精修後譯文,或 None(失敗時返回 NMT 原文)
    """
    if not NMT_POST_EDIT:
        return nmt_text
    if not llm_callable:
        return nmt_text
    
    edit_prompt = f"以下是初步翻譯,請精修。確保口語自然、術語正確、語氣相符。\n原文({src}):{src_text}\n初譯({tgt}):{nmt_text}\n精修譯文:"
    try:
        refined = llm_callable(edit_prompt, src, tgt)
        return refined if refined else nmt_text
    except Exception as e:
        logger.warning("[NMT] post-edit failed: %s", e)
        return nmt_text


# ═══════════════════════════════════════════════════════════════════
# 統計 / 配置 API
# ═══════════════════════════════════════════════════════════════════
def nmt_stats() -> Dict[str, Any]:
    with _lock:
        s = dict(_stats)
    s["provider"] = NMT_PROVIDER
    s["short_threshold"] = NMT_SHORT_THRESHOLD
    s["post_edit_enabled"] = NMT_POST_EDIT
    s["api_key_available"] = bool(
        os.environ.get("GOOGLE_TRANSLATE_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        if NMT_PROVIDER == "google"
        else os.environ.get("DEEPL_API_KEY")
    )
    return s


def nmt_set_config(provider: Optional[str] = None,
                   short_threshold: Optional[int] = None,
                   post_edit: Optional[bool] = None) -> Dict[str, Any]:
    """動態調整 NMT 配置(後台可調)"""
    global NMT_PROVIDER, NMT_SHORT_THRESHOLD, NMT_POST_EDIT
    if provider is not None:
        if provider in ("google", "deepl", "none"):
            NMT_PROVIDER = provider
    if short_threshold is not None:
        NMT_SHORT_THRESHOLD = max(0, min(200, int(short_threshold)))
    if post_edit is not None:
        NMT_POST_EDIT = bool(post_edit)
    cfg = {
        "provider": NMT_PROVIDER,
        "short_threshold": NMT_SHORT_THRESHOLD,
        "post_edit": NMT_POST_EDIT,
    }
    try:
        import phase_config_store as _pcs
        _pcs.save_config("nmt", cfg)
    except Exception as _e:
        logger.warning("[NMT] save persisted config failed: %s", _e)
    return cfg


def nmt_record_llm_route():
    """app.py 路由到 LLM 時 call,供統計"""
    with _lock:
        _stats["route_to_llm"] += 1
