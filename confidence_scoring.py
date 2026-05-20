"""
confidence_scoring.py — Translation Confidence Scoring v1.0 (2026-05-20)

業界主流翻譯信心評分:抽 LLM 內部訊號(logprobs / stop_reason / token usage)
推算譯文「LLM 自己有多確定」。

【為什麼有用】
- 補強 LLM-based QE(QE 是另一個 LLM 判斷,慢且貴)
- Confidence scoring 是免費的(LLM 翻譯時順便回傳的)
- 低信心訊息 + 高 QE 分數 → 可能 QE 漏判,值得人工複核

【雙系統訊號】
- OpenAI:logprobs=true → 每 token 機率,取平均對數機率 → 譯文信心
- Anthropic:stop_reason="end_turn"(正常)vs "max_tokens"(截斷)vs "stop_sequence"
  + 完成度檢查(output_tokens vs max_tokens 比例)

【整合】
- translate_openai 內加 logprobs hint(若 active=openai)
- 翻完後從 response 抽信心訊號,return 一個 0-1 分數
- 存到 translation_log 跟 TM 的 quality_score

【參考】
- OpenAI logprobs guide:
  https://cookbook.openai.com/examples/using_logprobs
- Anthropic stop_reason:
  https://docs.anthropic.com/en/api/messages
"""

import logging
import math
import threading
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════
CS_ENABLED = True
CS_LOG = []  # in-memory 紀錄 last N(供 dashboard)
CS_LOG_MAX = 100

_lock = threading.RLock()
_stats = {
    "calls": 0,
    "scored_openai": 0,
    "scored_anthropic": 0,
    "low_confidence_count": 0,  # < 0.7
    "sum_confidence": 0.0,
    "no_signal": 0,
}


# ═══════════════════════════════════════════════════════════════════
# OpenAI logprobs → confidence
# ═══════════════════════════════════════════════════════════════════
def score_openai_response(response) -> Optional[float]:
    """從 OpenAI chat completion response 抽 logprobs → confidence
    
    Args:
        response: openai.types.chat.ChatCompletion 物件(或 dict-like)
    
    Returns:
        0-1 浮點(高越好),或 None(無 logprobs 訊號)
    """
    try:
        choices = response.choices if hasattr(response, "choices") else response.get("choices", [])
        if not choices:
            return None
        choice = choices[0]
        logprobs = choice.logprobs if hasattr(choice, "logprobs") else choice.get("logprobs")
        if not logprobs:
            return None
        
        # OpenAI logprobs.content 是 list of {token, logprob, top_logprobs?}
        content = logprobs.content if hasattr(logprobs, "content") else logprobs.get("content", [])
        if not content:
            return None
        
        logprob_values = []
        for item in content:
            lp = item.logprob if hasattr(item, "logprob") else item.get("logprob")
            if lp is not None:
                logprob_values.append(lp)
        
        if not logprob_values:
            return None
        
        # 平均 logprob → exp → probability
        avg_logprob = sum(logprob_values) / len(logprob_values)
        confidence = math.exp(avg_logprob)  # 0-1
        return min(1.0, max(0.0, confidence))
    except Exception as e:
        logger.debug("[CS] OpenAI score failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════
# Anthropic stop_reason → confidence
# ═══════════════════════════════════════════════════════════════════
def score_anthropic_response(response, max_tokens: int = 1024) -> Optional[float]:
    """從 Anthropic Message response 推算 confidence
    
    訊號:
    - stop_reason="end_turn" → 正常完成,1.0
    - stop_reason="stop_sequence" → 0.9
    - stop_reason="max_tokens" → 截斷,0.3(可能話沒講完)
    - stop_reason="tool_use" → 不適用,return None
    - usage.output_tokens 接近 max_tokens(>90%) → 進一步扣分
    """
    try:
        stop_reason = (response.stop_reason if hasattr(response, "stop_reason")
                       else response.get("stop_reason"))
        if not stop_reason:
            return None
        
        # 基礎信心
        if stop_reason == "end_turn":
            base = 1.0
        elif stop_reason == "stop_sequence":
            base = 0.9
        elif stop_reason == "max_tokens":
            base = 0.3
        else:
            return None
        
        # 看 output_tokens 占比
        usage = (response.usage if hasattr(response, "usage")
                 else response.get("usage", {}))
        if usage:
            output_tokens = (usage.output_tokens if hasattr(usage, "output_tokens")
                             else usage.get("output_tokens", 0))
            if max_tokens > 0 and output_tokens > 0:
                fill_ratio = output_tokens / max_tokens
                if fill_ratio > 0.95:
                    # 太接近 max,可能截斷
                    base *= 0.7
                elif fill_ratio > 0.85:
                    base *= 0.85
        
        return min(1.0, max(0.0, base))
    except Exception as e:
        logger.debug("[CS] Anthropic score failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════
# 統一介面(雙系統自動路由)
# ═══════════════════════════════════════════════════════════════════
def score_response(response, max_tokens: int = 1024,
                   provider: Optional[str] = None) -> Optional[float]:
    """雙系統統一 confidence scoring
    
    Args:
        response: OpenAI 或 Anthropic API response
        max_tokens: 用於 Anthropic max_tokens 比例計算
        provider: 若 None,自動偵測(優先 openai logprobs 訊號,fallback anthropic stop_reason)
    
    Returns: 0-1 confidence,或 None
    """
    if not CS_ENABLED:
        return None
    
    with _lock:
        _stats["calls"] += 1
    
    score = None
    
    # 自動偵測(根據 response 結構)
    if provider is None:
        # OpenAI 有 choices[0].logprobs
        # Anthropic 有 stop_reason
        if hasattr(response, "stop_reason") or (isinstance(response, dict) and "stop_reason" in response):
            provider = "anthropic"
        elif hasattr(response, "choices") or (isinstance(response, dict) and "choices" in response):
            provider = "openai"
    
    if provider == "openai":
        score = score_openai_response(response)
        if score is not None:
            with _lock:
                _stats["scored_openai"] += 1
    elif provider == "anthropic":
        score = score_anthropic_response(response, max_tokens=max_tokens)
        if score is not None:
            with _lock:
                _stats["scored_anthropic"] += 1
    
    if score is None:
        with _lock:
            _stats["no_signal"] += 1
        return None
    
    # 統計
    with _lock:
        _stats["sum_confidence"] += score
        if score < 0.7:
            _stats["low_confidence_count"] += 1
    
    return score


# ═══════════════════════════════════════════════════════════════════
# 統計 API
# ═══════════════════════════════════════════════════════════════════
def cs_stats() -> Dict[str, Any]:
    with _lock:
        s = dict(_stats)
    total_scored = s["scored_openai"] + s["scored_anthropic"]
    if total_scored > 0:
        s["avg_confidence"] = round(s["sum_confidence"] / total_scored, 4)
        s["low_confidence_rate"] = round(s["low_confidence_count"] / total_scored, 4)
    else:
        s["avg_confidence"] = 0
        s["low_confidence_rate"] = 0
    s["total_scored"] = total_scored
    s["enabled"] = CS_ENABLED
    return s


def cs_set_config(enabled: Optional[bool] = None) -> Dict[str, Any]:
    global CS_ENABLED
    if enabled is not None:
        CS_ENABLED = bool(enabled)
    return {"enabled": CS_ENABLED}
