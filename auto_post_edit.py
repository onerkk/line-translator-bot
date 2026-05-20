"""
auto_post_edit.py — LLM-based Automatic Post-Editing v1.0 (2026-05-20)

業界主流 APE(Automatic Post-Editing)架構:
- QE 偵測到低分翻譯 → APE 自動修錯
- TM hit 但是過舊翻譯 → APE 升級
- NMT 預翻品質不夠 → APE 加工

補強現有 post_fix_factory_zh_to_id(規則式)的盲點:
- 規則式只能處理已知 pattern,新誤譯要等規則加入
- LLM-APE 動態理解上下文,可修任何 issue

【觸發條件】(可由 QE 或上游邏輯決定)
1. QE 分數 50-69 → APE
2. round-trip 反譯失敗 → APE
3. 偵測到 known bad patterns 但 reactive sed 無法處理 → APE

【prompt 設計】
給 LLM:原文 + 譯文 + 問題列表 → 要求修正版

【模型】
- 預設 Claude Sonnet 4.6(品質優先,比 Haiku 更精)
- 可配置切換

【參考】
- "GEMBA-MQM + APE" 業界主流 pipeline
- Microsoft Translator Custom APE
"""

import os
import logging
import threading
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 配置 — 雙系統 AI 相容
# 模型 mapping 跟隨當前 active provider 自動選擇
# ═══════════════════════════════════════════════════════════════════
APE_ENABLED = True

# 雙系統模型 mapping(APE 修錯需要好模型,品質優先)
APE_MODEL_BY_PROVIDER = {
    "openai": "gpt-4.1",                # $2/M input, $8/M output
    "anthropic": "claude-sonnet-4-6",   # $3/M input, $15/M output
}

APE_TRIGGER_QE_SCORE = 70  # QE 分數 < 此值觸發 APE
APE_MAX_RETRIES = 1  # APE 最多重試次數


def _resolve_ape_model() -> str:
    """根據當前 active provider 動態選 APE 模型"""
    try:
        import ai_provider
        provider = ai_provider.get_active_provider()
        return APE_MODEL_BY_PROVIDER.get(provider, APE_MODEL_BY_PROVIDER["openai"])
    except Exception:
        return APE_MODEL_BY_PROVIDER["openai"]

_lock = threading.RLock()
_stats = {
    "triggered_by_qe": 0,
    "triggered_by_roundtrip": 0,
    "triggered_by_pattern": 0,
    "successes": 0,
    "no_change": 0,
    "api_errors": 0,
}


# ═══════════════════════════════════════════════════════════════════
# APE prompt 設計(分區 XML,符合 Anthropic 規範)
# ═══════════════════════════════════════════════════════════════════
APE_SYSTEM_PROMPT = """<role>
你是台灣不銹鋼工廠中印雙語譯文修正專家,專修 LLM 翻譯後的錯誤。
</role>

<task>
分析譯文錯誤,輸出修正版。只輸出修正版譯文,不要解釋,不要前綴。
</task>

<rules>
1. 嚴格保留原文意思,不增不刪
2. 工廠術語遵循標準對照(料/品保/工單/料號/爐號/班長/副總/砂輪/異型棒等)
3. 人名、料號、機台代號(BF2/E11/I7 等)、@mention 必須原樣保留
4. 語氣與原文相符:口語對口語,正式對正式
5. 段落結構與原文一致(換行/空行不變)
6. 不要加 "翻譯:"/"Catatan:" 等前綴
7. 不要加任何 emoji(除非原文有)
</rules>

<output_format>
直接輸出修正後譯文,不包任何 tag,不加任何文字。
</output_format>"""


def _build_ape_user_prompt(src_text: str, bad_tgt: str,
                           src_lang: str, tgt_lang: str,
                           issues: Optional[List[str]] = None,
                           qe_score: Optional[int] = None) -> str:
    parts = [
        f"<source_text lang=\"{src_lang}\">{src_text}</source_text>",
        f"<current_translation lang=\"{tgt_lang}\">{bad_tgt}</current_translation>",
    ]
    if qe_score is not None:
        parts.append(f"<qe_score>{qe_score}/100</qe_score>")
    if issues:
        parts.append("<known_issues>")
        for i, issue in enumerate(issues, 1):
            parts.append(f"  {i}. {issue}")
        parts.append("</known_issues>")
    parts.append("\n請修正上述譯文。直接輸出修正版,不要解釋。")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
# 核心 API
# ═══════════════════════════════════════════════════════════════════
def auto_post_edit(src_text: str, bad_tgt: str,
                   src_lang: str, tgt_lang: str,
                   issues: Optional[List[str]] = None,
                   qe_score: Optional[int] = None,
                   trigger: str = "qe",
                   ai_client=None) -> Optional[str]:
    """LLM-based Automatic Post-Editing
    
    Args:
        src_text: 原文
        bad_tgt: 低品質譯文
        src_lang, tgt_lang: 語言碼
        issues: 已知問題列表(可選,通常來自 QE)
        qe_score: QE 分數(可選)
        trigger: 觸發原因 ("qe" | "roundtrip" | "pattern")
        ai_client: ai_provider 或相容介面
    
    Returns: 修正後譯文,或 None(失敗)
    """
    if not APE_ENABLED:
        return None
    if not src_text or not bad_tgt:
        return None
    
    # 統計觸發
    with _lock:
        if trigger == "qe":
            _stats["triggered_by_qe"] += 1
        elif trigger == "roundtrip":
            _stats["triggered_by_roundtrip"] += 1
        elif trigger == "pattern":
            _stats["triggered_by_pattern"] += 1
    
    # Lazy import
    if ai_client is None:
        try:
            import ai_provider
            ai_client_use = ai_provider
        except ImportError:
            return None
    else:
        ai_client_use = ai_client
    
    user_msg = _build_ape_user_prompt(src_text, bad_tgt, src_lang, tgt_lang, issues, qe_score)
    
    try:
        # 動態選 model:跟隨當前 active provider(雙系統相容)
        resolved_model = _resolve_ape_model()
        resp = ai_client_use.chat_complete(
            model=resolved_model,
            messages=[
                {"role": "system", "content": APE_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=1024,
            temperature=0.0,
        )
        content = ""
        if hasattr(resp, "choices") and resp.choices:
            content = (resp.choices[0].message.content or "").strip()
        
        if not content:
            with _lock:
                _stats["api_errors"] += 1
            return None
        
        # 移除常見前綴(防 LLM 不聽話)
        for prefix in ("修正後譯文:", "修正後:", "修正版:", "修正:",
                       "Translation:", "翻譯:", "Catatan:", "註:"):
            if content.startswith(prefix):
                content = content[len(prefix):].strip()
        
        # 如果與原譯文相同,記錄
        if content == bad_tgt.strip():
            with _lock:
                _stats["no_change"] += 1
            logger.info("[APE] no change after edit")
            return content
        
        with _lock:
            _stats["successes"] += 1
        logger.info("[APE] success: trigger=%s old_len=%d new_len=%d",
                    trigger, len(bad_tgt), len(content))
        return content
    
    except Exception as e:
        logger.warning("[APE] failed: %s", e)
        with _lock:
            _stats["api_errors"] += 1
        return None


# ═══════════════════════════════════════════════════════════════════
# 統計 / 配置 API
# ═══════════════════════════════════════════════════════════════════
def ape_stats() -> Dict[str, Any]:
    with _lock:
        s = dict(_stats)
    total = s["triggered_by_qe"] + s["triggered_by_roundtrip"] + s["triggered_by_pattern"]
    if total > 0:
        s["success_rate"] = round(s["successes"] / total, 4)
        s["no_change_rate"] = round(s["no_change"] / total, 4)
    else:
        s["success_rate"] = s["no_change_rate"] = 0
    s["total_triggered"] = total
    s["enabled"] = APE_ENABLED
    # 雙系統:顯示當前用的模型 + mapping
    try:
        import ai_provider
        s["active_provider"] = ai_provider.get_active_provider()
    except Exception:
        s["active_provider"] = "unknown"
    s["model_current"] = _resolve_ape_model()
    s["model_by_provider"] = dict(APE_MODEL_BY_PROVIDER)
    s["trigger_qe_score"] = APE_TRIGGER_QE_SCORE
    return s


def ape_set_config(enabled: Optional[bool] = None,
                   openai_model: Optional[str] = None,
                   anthropic_model: Optional[str] = None,
                   trigger_qe_score: Optional[int] = None) -> Dict[str, Any]:
    global APE_ENABLED, APE_TRIGGER_QE_SCORE
    if enabled is not None:
        APE_ENABLED = bool(enabled)
    if openai_model:
        APE_MODEL_BY_PROVIDER["openai"] = str(openai_model)
    if anthropic_model:
        APE_MODEL_BY_PROVIDER["anthropic"] = str(anthropic_model)
    if trigger_qe_score is not None:
        APE_TRIGGER_QE_SCORE = max(0, min(100, int(trigger_qe_score)))
    return {
        "enabled": APE_ENABLED,
        "model_by_provider": dict(APE_MODEL_BY_PROVIDER),
        "model_current": _resolve_ape_model(),
        "trigger_qe_score": APE_TRIGGER_QE_SCORE,
    }
