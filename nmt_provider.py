"""
nmt_provider.py — Hybrid NMT + LLM Translation Provider v1.0 (2026-05-20)

業界主流 hybrid 翻譯架構:
- 短句 / 結構簡單 / 無工廠術語 → NMT(Google Translate / DeepL)
- 長句 / 口語 / 含 glossary 命中 / 含 emoji / 含敬稱 → LLM(Claude / GPT)
- 可選:NMT 預翻 → LLM post-edit(品質提升 + 成本下降)

【為什麼用 NMT】
- Google NMT $20 / 1M chars,vs Claude Sonnet $3-15 / 1M tokens
- Google NMT 延遲 < 100ms,vs LLM 1-3s
- 短句直譯品質 NMT 已夠用
- 短句佔工廠群組訊息 60-70%

【支援的 NMT】
- Google Cloud Translation API v2 (REST,簡單 API key)
- DeepL API(可選,品質高但價貴,印尼語支援次要)

【決策樹 (route_to_nmt_or_llm)】
- 訊息長度 < 30 字
- AND 無 emoji(emoji 表情通常含語境)
- AND 無 @mention
- AND 無已知工廠術語(會破壞 NMT 品質)
- AND 無口語標記(啦/喔/嘛/醬/咧/蛤)
- AND 無數字 + 量詞混合(短量詞回覆 NMT 易錯)
→ NMT
否則 → LLM

【官方文件】
- Google Cloud Translation: https://cloud.google.com/translate/docs/reference/rest/v2/translate
- DeepL API: https://developers.deepl.com/docs/api-reference/translate
"""

import os
import json
import logging
import threading
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════
NMT_PROVIDER = os.environ.get("NMT_PROVIDER", "google").lower()  # "google" | "deepl" | "none"
NMT_SHORT_THRESHOLD = 30  # 字數 < 此值才考慮 NMT
NMT_TIMEOUT = 10  # API timeout 秒
NMT_POST_EDIT = False  # NMT 翻完是否再過 LLM 精修(預設關,需要時開)

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
def should_use_nmt(text: str, src_lang: str, tgt_lang: str,
                   factory_glossary: Optional[set] = None) -> bool:
    """決定是否走 NMT(否則走 LLM)
    
    Args:
        text: 原文
        src_lang, tgt_lang: 語言碼
        factory_glossary: 工廠術語集合(若 text 命中任一,走 LLM 避免 NMT 誤譯)
    
    Returns: True = NMT,False = LLM
    """
    if NMT_PROVIDER == "none":
        return False
    if not text or not text.strip():
        return False
    text = text.strip()
    
    # 1. 太長 → LLM
    if len(text) >= NMT_SHORT_THRESHOLD:
        return False
    
    # 2. 含 emoji → LLM(NMT 不會適當處理 emoji 語境)
    if any(ord(c) > 0x1F000 for c in text):
        return False
    
    # 3. 含 @mention → LLM
    if "@" in text or "__MENTION_" in text or "__CUST_" in text:
        return False
    
    # 4. 含工廠術語 → LLM
    if factory_glossary:
        for term in factory_glossary:
            if term and len(term) >= 2 and term in text:
                return False
    
    # 5. 台灣口語標記 → LLM
    tw_colloquial = ["啦", "喔", "哦", "嘛", "醬", "降", "咧", "蛤", "厚", "ㄏㄏ", "QQ", "3Q", "感溫", "傻眼", "母湯", "出包"]
    if any(c in text for c in tw_colloquial):
        return False
    
    # 6. 印尼口語縮寫 → LLM
    id_colloquial = ["bgt", "gak", "udh", "udah", "gimana", "ngapain", "gw", "lu", "dong", "nih", "sih", "lho"]
    if any(c in text.lower() for c in id_colloquial):
        return False
    
    # 7. 含數字+中文量詞(短回覆易誤譯)
    import re
    if re.search(r"\d+[把支台個件批捆噸公斤kg噸支]", text):
        return False
    
    # 8. 含 placeholder
    if "__" in text:
        return False
    
    # 通過全部過濾 → 走 NMT
    return True


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
    return {
        "provider": NMT_PROVIDER,
        "short_threshold": NMT_SHORT_THRESHOLD,
        "post_edit": NMT_POST_EDIT,
    }


def nmt_record_llm_route():
    """app.py 路由到 LLM 時 call,供統計"""
    with _lock:
        _stats["route_to_llm"] += 1
