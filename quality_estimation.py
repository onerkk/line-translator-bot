"""
quality_estimation.py — LLM-based Quality Estimation v1.0 (2026-05-20)

業界標準的翻譯品質評分系統,補/取代:
- 現有 logprobs 信心度(粗糙近似)
- 現有 round-trip 反譯檢查(成本翻倍但訊號弱)

【做法】
用 Claude Haiku 4.5 / GPT-5-nano 對譯文打分(0-100),評估 4 個維度:
1. Accuracy(準確性):譯文是否傳達原文全部意思
2. Fluency(流暢性):譯文在目標語言是否自然
3. Terminology(術語):工廠術語是否使用正確
4. Tone(語氣):正式/口語是否相符

低分(< 70)→ 觸發重翻,或在 UI 顯示警告(⚠️ 前綴)
分數也存入 TM 的 quality_score 欄位,作為未來路由決策依據

【為什麼用 LLM 評分而不是 COMET】
- COMET 需要訓練好的模型 + GPU
- Anthropic / OpenAI 已是 LLM as judge,業界 2025 主流做法
- 成本可控:Haiku 4.5 ~$1/M tokens,評一筆譯文 ~$0.0001

【參考】
- "LLM-as-a-Judge" 業界 best practice
- Lokalise 用 GPT/Claude 做 QA scoring
- Microsoft "GEMBA-MQM" GPT-based MT quality estimation
"""

import os
import json
import logging
import threading
import re
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 配置 — 雙系統 AI 相容
# 模型 mapping 跟隨當前 active provider 自動選擇
# ═══════════════════════════════════════════════════════════════════
QE_ENABLED = True  # 全局開關

# 雙系統模型 mapping(用便宜模型評分即可,QE 是 LLM-as-judge 不需頂級模型)
QE_MODEL_BY_PROVIDER = {
    "openai": "gpt-4.1-mini",                    # $0.4/M input, $1.6/M output
    "anthropic": "claude-haiku-4-5-20251001",    # $1/M input, $5/M output
}

QE_THRESHOLD_WARN = 70  # 分數 < 此值 → ⚠️ 警告
QE_THRESHOLD_RETRY = 50  # 分數 < 此值 → 自動重翻
QE_MIN_LEN = 10  # 訊息字元 < 此值不評分(短句不值得)
QE_SAMPLE_RATE = 1.0  # 評分採樣率(1.0 = 全部評,0.1 = 10%)


def _resolve_qe_model() -> str:
    """根據當前 active provider 動態選 QE 模型"""
    try:
        import ai_provider
        provider = ai_provider.get_active_provider()
        return QE_MODEL_BY_PROVIDER.get(provider, QE_MODEL_BY_PROVIDER["openai"])
    except Exception:
        return QE_MODEL_BY_PROVIDER["openai"]

_lock = threading.RLock()
_stats = {
    "evaluations": 0,
    "skipped_short": 0,
    "skipped_sampled": 0,
    "scores_sum": 0,
    "warn_count": 0,
    "retry_count": 0,
    "api_errors": 0,
    "score_distribution": {"90+": 0, "70-89": 0, "50-69": 0, "<50": 0},
}


# ═══════════════════════════════════════════════════════════════════
# QE prompt 設計
# ═══════════════════════════════════════════════════════════════════
QE_SYSTEM_PROMPT = """你是專業翻譯品質評估專家,評估台灣不銹鋼工廠中印雙語譯文品質。

評估維度(各 0-25 分,合計 0-100):
1. **Accuracy 準確性**(0-25):譯文是否完整傳達原文意思,沒有遺漏/誤譯
2. **Fluency 流暢性**(0-25):譯文在目標語言是否自然通順
3. **Terminology 術語**(0-25):工廠術語、機台代號、料號是否正確
4. **Tone 語氣**(0-25):正式/口語/緊急程度是否相符原文

**輸出格式**(嚴格 JSON,不要 markdown,不要任何說明):
{"accuracy":整數,"fluency":整數,"terminology":整數,"tone":整數,"total":整數,"issues":["具體問題1","具體問題2"]}

issues 列出具體錯誤(若有),最多 3 條,沒問題就空陣列。"""


def _build_qe_user_prompt(src_text: str, tgt_text: str,
                          src_lang: str, tgt_lang: str) -> str:
    return (
        f"<source_text lang=\"{src_lang}\">\n{src_text}\n</source_text>\n\n"
        f"<translation lang=\"{tgt_lang}\">\n{tgt_text}\n</translation>\n\n"
        "評估上述譯文品質,輸出 JSON。"
    )


# ═══════════════════════════════════════════════════════════════════
# 核心 API
# ═══════════════════════════════════════════════════════════════════
def estimate_quality(src_text: str, tgt_text: str,
                     src_lang: str, tgt_lang: str,
                     ai_client=None) -> Optional[Dict[str, Any]]:
    """評估翻譯品質
    
    Args:
        ai_client: 接受 chat.completions.create() 介面的 client(可用 ai_provider 的 _AIProxy)
                   若 None,lazy import OpenAI / Anthropic
    
    Returns:
        None — 跳過(短句 / 取樣略過 / 全局關閉)
        {
            "total": 0-100,
            "accuracy": 0-25,
            "fluency": 0-25,
            "terminology": 0-25,
            "tone": 0-25,
            "issues": ["..."],
            "action": "ok" | "warn" | "retry"
        }
    """
    if not QE_ENABLED:
        return None
    if not src_text or not tgt_text:
        return None
    
    src_text = src_text.strip()
    tgt_text = tgt_text.strip()
    
    if len(src_text) < QE_MIN_LEN:
        with _lock:
            _stats["skipped_short"] += 1
        return None
    
    # Sampling
    if QE_SAMPLE_RATE < 1.0:
        import random
        if random.random() > QE_SAMPLE_RATE:
            with _lock:
                _stats["skipped_sampled"] += 1
            return None
    
    # 取得 LLM client
    if ai_client is None:
        # Lazy import,優先 ai_provider 的 _AIProxy
        try:
            import ai_provider
            ai_client_use = ai_provider
        except ImportError:
            ai_client_use = None
    else:
        ai_client_use = ai_client
    
    if ai_client_use is None:
        return None
    
    user_msg = _build_qe_user_prompt(src_text, tgt_text, src_lang, tgt_lang)
    
    try:
        # 動態選 model:跟隨當前 active provider
        resolved_model = _resolve_qe_model()
        # 用 chat_complete 介面(ai_provider 提供,雙系統共用)
        resp = ai_client_use.chat_complete(
            model=resolved_model,
            messages=[
                {"role": "system", "content": QE_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=300,
            temperature=0.0,
        )
        # ai_provider 回傳 unified response
        content = ""
        if hasattr(resp, "choices") and resp.choices:
            content = resp.choices[0].message.content or ""
        
        # 解析 JSON(允許 markdown ```json fence)
        content = content.strip()
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            logger.warning("[QE] 無法找到 JSON: %s", content[:200])
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            logger.warning("[QE] JSON parse failed: %s | content=%s", e, content[:200])
            return None
        
        # 標準化
        total = int(data.get("total", 0))
        total = max(0, min(100, total))
        
        accuracy = int(data.get("accuracy", 0))
        fluency = int(data.get("fluency", 0))
        terminology = int(data.get("terminology", 0))
        tone = int(data.get("tone", 0))
        # 若各維度合計與 total 差距大,用各維度合計
        sub_total = accuracy + fluency + terminology + tone
        if abs(sub_total - total) > 5:
            total = sub_total
        
        issues = data.get("issues", []) or []
        if not isinstance(issues, list):
            issues = []
        issues = [str(x)[:200] for x in issues[:3]]
        
        # 決定 action
        if total < QE_THRESHOLD_RETRY:
            action = "retry"
        elif total < QE_THRESHOLD_WARN:
            action = "warn"
        else:
            action = "ok"
        
        # 統計
        with _lock:
            _stats["evaluations"] += 1
            _stats["scores_sum"] += total
            if total >= 90:
                _stats["score_distribution"]["90+"] += 1
            elif total >= 70:
                _stats["score_distribution"]["70-89"] += 1
            elif total >= 50:
                _stats["score_distribution"]["50-69"] += 1
            else:
                _stats["score_distribution"]["<50"] += 1
            if action == "warn":
                _stats["warn_count"] += 1
            elif action == "retry":
                _stats["retry_count"] += 1
        
        logger.info("[QE] score=%d action=%s issues=%d", total, action, len(issues))
        
        return {
            "total": total,
            "accuracy": accuracy,
            "fluency": fluency,
            "terminology": terminology,
            "tone": tone,
            "issues": issues,
            "action": action,
        }
    
    except Exception as e:
        logger.warning("[QE] estimate_quality failed: %s", e)
        with _lock:
            _stats["api_errors"] += 1
        return None


# ═══════════════════════════════════════════════════════════════════
# 統計 / 配置 API
# ═══════════════════════════════════════════════════════════════════
def qe_stats() -> Dict[str, Any]:
    with _lock:
        s = dict(_stats)
        s["score_distribution"] = dict(s["score_distribution"])
    if s["evaluations"] > 0:
        s["avg_score"] = round(s["scores_sum"] / s["evaluations"], 2)
        s["warn_rate"] = round(s["warn_count"] / s["evaluations"], 4)
        s["retry_rate"] = round(s["retry_count"] / s["evaluations"], 4)
    else:
        s["avg_score"] = 0
        s["warn_rate"] = s["retry_rate"] = 0
    s["enabled"] = QE_ENABLED
    # 雙系統:顯示當前用的模型 + mapping
    try:
        import ai_provider
        s["active_provider"] = ai_provider.get_active_provider()
    except Exception:
        s["active_provider"] = "unknown"
    s["model_current"] = _resolve_qe_model()
    s["model_by_provider"] = dict(QE_MODEL_BY_PROVIDER)
    s["thresholds"] = {
        "warn": QE_THRESHOLD_WARN,
        "retry": QE_THRESHOLD_RETRY,
        "min_len": QE_MIN_LEN,
        "sample_rate": QE_SAMPLE_RATE,
    }
    return s


def qe_set_config(enabled: Optional[bool] = None,
                  openai_model: Optional[str] = None,
                  anthropic_model: Optional[str] = None,
                  threshold_warn: Optional[int] = None,
                  threshold_retry: Optional[int] = None,
                  min_len: Optional[int] = None,
                  sample_rate: Optional[float] = None) -> Dict[str, Any]:
    global QE_ENABLED, QE_THRESHOLD_WARN, QE_THRESHOLD_RETRY, QE_MIN_LEN, QE_SAMPLE_RATE
    if enabled is not None:
        QE_ENABLED = bool(enabled)
    if openai_model:
        QE_MODEL_BY_PROVIDER["openai"] = str(openai_model)
    if anthropic_model:
        QE_MODEL_BY_PROVIDER["anthropic"] = str(anthropic_model)
    if threshold_warn is not None:
        QE_THRESHOLD_WARN = max(0, min(100, int(threshold_warn)))
    if threshold_retry is not None:
        QE_THRESHOLD_RETRY = max(0, min(100, int(threshold_retry)))
    if min_len is not None:
        QE_MIN_LEN = max(0, int(min_len))
    if sample_rate is not None:
        QE_SAMPLE_RATE = max(0.0, min(1.0, float(sample_rate)))
    return {
        "enabled": QE_ENABLED,
        "model_by_provider": dict(QE_MODEL_BY_PROVIDER),
        "model_current": _resolve_qe_model(),
        "threshold_warn": QE_THRESHOLD_WARN,
        "threshold_retry": QE_THRESHOLD_RETRY,
        "min_len": QE_MIN_LEN,
        "sample_rate": QE_SAMPLE_RATE,
    }
