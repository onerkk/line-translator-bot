"""
in_context_learning.py — Dynamic Few-shot from TM v1.0 (2026-05-20)

業界主流 ICL(In-Context Learning):
從 TM 動態抽 top-K 相似翻譯作為 few-shot examples,大幅提升 LLM 一致性。

跟既有 `tm_inject_prompt` / `vector_inject_prompt` 不同:
- 那些是「reference」(讓 LLM 參考)
- ICL 是「demonstration」(明確示範格式),用 Anthropic 多輪 user/assistant 格式

【為什麼有效】
- LLM in-context learning 比 prompt 描述強 5-10 倍(Anthropic 官方研究)
- TM top-K 的「實際翻譯範例」勝過任何 sys_prompt 描述
- 動態抽 → 跟手邊翻譯任務最相關 → 比 hardcode few-shot 強

【官方文件】
- Anthropic Multi-shot Prompting:
  https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/multishot-prompting
- OpenAI Few-shot:GPT-4.1 supports up to 50+ examples

【整合】
- translate() wrapper 先呼叫 lexical/vector TM lookup
- 若 inject mode(score 70-94),build_few_shot_messages(refs) 把 refs 轉成 user/assistant 對
- ai_provider.chat_complete 自動接受 multi-turn messages
"""

import logging
from typing import List, Tuple, Dict, Any, Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════
ICL_ENABLED = True
ICL_MAX_EXAMPLES = 5  # 最多 5 個 few-shot
ICL_MIN_SCORE = 75  # 最低 score(0-100)才入選 few-shot
ICL_PREFER_HUMAN_CORRECTED = True  # 優先用 human_corrected TM 條目

import threading
_lock = threading.RLock()
_stats = {
    "icl_applied": 0,
    "examples_total": 0,
    "human_corrected_used": 0,
}


def build_few_shot_messages(
    references: List[Tuple[int, str, str]],
    src_text: str,
    src_lang: str,
    tgt_lang: str,
) -> List[Dict[str, str]]:
    """把 TM references 轉成 multi-turn few-shot messages
    
    Args:
        references: [(score 0-100, src_text, tgt_text), ...]
        src_text: 當前要翻譯的原文
        src_lang, tgt_lang: 語言碼
    
    Returns: messages list of {"role": "user"|"assistant", "content": "..."}
             最後一筆是當前要翻譯的 src(沒有 assistant 回應,留給 LLM 補)
    """
    if not ICL_ENABLED or not references:
        return []
    
    # Filter 並排序
    eligible = [(s, src, tgt) for s, src, tgt in references if s >= ICL_MIN_SCORE]
    if not eligible:
        return []
    
    # 由低分到高分(讓 LLM 注意力集中在最相關的 → 高分放後面)
    eligible.sort(key=lambda x: x[0])
    examples = eligible[-ICL_MAX_EXAMPLES:]  # 取後 K 條(最高分)
    
    messages = []
    instruction = f"請將以下 {src_lang} 翻譯為 {tgt_lang}。保持工廠術語、placeholder、人名不變。"
    
    for score, ex_src, ex_tgt in examples:
        messages.append({"role": "user", "content": f"{instruction}\n\n{ex_src}"})
        messages.append({"role": "assistant", "content": ex_tgt})
    
    # 最後一筆 — 當前要翻譯的(沒 assistant,留 LLM 補)
    messages.append({"role": "user", "content": f"{instruction}\n\n{src_text}"})
    
    with _lock:
        _stats["icl_applied"] += 1
        _stats["examples_total"] += len(examples)
    
    logger.info("[ICL] applied %d few-shot examples (scores: %s)",
                len(examples), [s for s, _, _ in examples])
    return messages


def should_use_icl(references: Optional[List]) -> bool:
    """判斷是否值得用 ICL(有夠多高分 refs)
    
    若沒有 references 或全部低於 ICL_MIN_SCORE,return False
    """
    if not ICL_ENABLED or not references:
        return False
    eligible = [r for r in references if r[0] >= ICL_MIN_SCORE]
    return len(eligible) >= 1


def icl_stats() -> Dict[str, Any]:
    with _lock:
        s = dict(_stats)
    if s["icl_applied"] > 0:
        s["avg_examples_per_call"] = round(s["examples_total"] / s["icl_applied"], 2)
    else:
        s["avg_examples_per_call"] = 0
    s["enabled"] = ICL_ENABLED
    s["max_examples"] = ICL_MAX_EXAMPLES
    s["min_score"] = ICL_MIN_SCORE
    return s


def icl_set_config(enabled: Optional[bool] = None,
                   max_examples: Optional[int] = None,
                   min_score: Optional[int] = None) -> Dict[str, Any]:
    global ICL_ENABLED, ICL_MAX_EXAMPLES, ICL_MIN_SCORE
    if enabled is not None:
        ICL_ENABLED = bool(enabled)
    if max_examples is not None:
        ICL_MAX_EXAMPLES = max(1, min(20, int(max_examples)))
    if min_score is not None:
        ICL_MIN_SCORE = max(50, min(95, int(min_score)))
    return {
        "enabled": ICL_ENABLED,
        "max_examples": ICL_MAX_EXAMPLES,
        "min_score": ICL_MIN_SCORE,
    }
