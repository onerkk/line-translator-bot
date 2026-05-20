"""
glossary_enforcement.py — Glossary Post-Validation Enforcement v1.0 (2026-05-20)

業界標準術語強制執行(TBX-driven enforcement),補強現有 post_fix_factory_zh_to_id 的盲點。

【現有問題】
- 我們在 prompt 內注入 glossary 提示 LLM
- 但 LLM 可能仍會誤譯(尤其長 prompt 後 attention 衰減)
- post_fix_factory_zh_to_id 是 hardcode 規則,無法跟著 GLOSSARY_LOOKUP 自動成長

【業界做法】
- Lokalise / Phrase / Smartcat 都做 "Glossary Compliance Check"
- 翻完譯文後,掃 source 中所有 glossary 術語
- 對每個命中術語,檢查 target 是否出現 glossary["target_term"]
- 沒出現 → flag violation,可選擇:
  1. 警告(在 ⚠️ 前綴顯示)
  2. 自動修正(透過 LLM rewrite,只改違規部分)
  3. Block 該翻譯不送出

【整合】
- 接在 translate() wrapper 的 LLM 翻譯後、QE 之前
- enforce_glossary(src, tgt, glossary, src_lang, tgt_lang) → (compliant, violations, fixed_tgt)
- 雙向支援(zh→id, id→zh)

【參考】
- ISO 12616 翻譯工作流標準
- Lokalise Glossary Compliance: https://docs.lokalise.com/en/articles/3742990
"""

import logging
import re
from typing import Optional, Dict, Any, List, Tuple, Callable

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════
GE_ENABLED = True
GE_MIN_TERM_LEN = 2  # 太短的術語(<2 字)易誤判,跳過
GE_ACTION = "warn"  # "warn" | "auto_fix" | "block"
GE_MAX_VIOLATIONS_BEFORE_BLOCK = 3  # action=block 時超過此數才 block

# 從持久化載入(覆蓋預設)
try:
    import phase_config_store as _pcs
    _saved = _pcs.load_config("ge")
    if _saved:
        GE_ENABLED = _saved.get("enabled", GE_ENABLED)
        GE_ACTION = _saved.get("action", GE_ACTION)
        GE_MIN_TERM_LEN = _saved.get("min_term_len", GE_MIN_TERM_LEN)
        logger.info("[GE] loaded persisted config: %s", _saved)
except Exception as _e:
    logger.warning("[GE] load persisted config failed: %s", _e)

import threading
_lock = threading.RLock()
_stats = {
    "checks": 0,
    "compliant": 0,
    "violations_found": 0,
    "auto_fixed": 0,
    "blocked": 0,
    "by_term": {},  # 哪些術語最常違規
}


# ═══════════════════════════════════════════════════════════════════
# 核心檢查
# ═══════════════════════════════════════════════════════════════════
def check_glossary_compliance(src_text: str, tgt_text: str,
                              glossary: Dict[str, Any],
                              src_lang: str, tgt_lang: str) -> Tuple[bool, List[Dict[str, str]]]:
    """檢查譯文是否符合 glossary 強制對照
    
    Args:
        src_text: 原文
        tgt_text: 譯文
        glossary: { zh_term: {"idn": "印尼譯", ...} } 或 { zh_term: "印尼譯" }
        src_lang, tgt_lang: 語言碼
    
    Returns:
        (compliant: bool, violations: list of dict)
        violations 元素: {"src_term": str, "expected_tgt": str, "context": str}
    """
    if not src_text or not tgt_text or not glossary:
        return True, []
    
    src_text = src_text.strip()
    tgt_text = tgt_text.strip()
    
    with _lock:
        _stats["checks"] += 1
    
    violations = []
    src_lower = src_text  # 中文不用 lower
    tgt_lower = tgt_text.lower()
    
    # 雙向支援:zh→id 走 zh→idn,id→zh 走 idn→zh
    is_zh_to_id = src_lang.lower().startswith("zh") and tgt_lang.lower().startswith("id")
    is_id_to_zh = src_lang.lower().startswith("id") and tgt_lang.lower().startswith("zh")
    
    if not (is_zh_to_id or is_id_to_zh):
        # 不在主要語言對範圍,不檢查
        return True, []
    
    for term_key, term_value in glossary.items():
        if not term_key or len(term_key) < GE_MIN_TERM_LEN:
            continue
        
        # 統一取 target term
        if isinstance(term_value, dict):
            target_term = term_value.get("idn", "")
        else:
            target_term = str(term_value)
        if not target_term or len(target_term) < GE_MIN_TERM_LEN:
            continue
        
        if is_zh_to_id:
            # zh→id: source 是 zh,要找 term_key(中文);target 是 id,要找 target_term(印尼)
            if term_key in src_text:
                # source 出現中文術語
                # target 應出現對應印尼譯(case-insensitive)
                if target_term.lower() not in tgt_lower:
                    # 違規!
                    violations.append({
                        "src_term": term_key,
                        "expected_tgt": target_term,
                        "context": _extract_context(src_text, term_key, window=10),
                    })
                    with _lock:
                        _stats["by_term"][term_key] = _stats["by_term"].get(term_key, 0) + 1
        elif is_id_to_zh:
            # id→zh: source 是 id,要找 target_term(印尼);target 是 zh,要找 term_key(中文)
            if target_term.lower() in src_text.lower():
                if term_key not in tgt_text:
                    violations.append({
                        "src_term": target_term,
                        "expected_tgt": term_key,
                        "context": _extract_context(src_text, target_term, window=10),
                    })
                    with _lock:
                        _stats["by_term"][term_key] = _stats["by_term"].get(term_key, 0) + 1
    
    compliant = len(violations) == 0
    with _lock:
        if compliant:
            _stats["compliant"] += 1
        _stats["violations_found"] += len(violations)
    
    return compliant, violations


def _extract_context(text: str, term: str, window: int = 10) -> str:
    """抓術語前後 window 字當 context"""
    idx = text.find(term)
    if idx < 0:
        return ""
    start = max(0, idx - window)
    end = min(len(text), idx + len(term) + window)
    return text[start:end]


# ═══════════════════════════════════════════════════════════════════
# 自動修正(可選,需 LLM)
# ═══════════════════════════════════════════════════════════════════
def auto_fix_violations(src_text: str, bad_tgt: str,
                        violations: List[Dict[str, str]],
                        src_lang: str, tgt_lang: str,
                        ai_client=None, model: Optional[str] = None) -> Optional[str]:
    """用 LLM 修正術語違規
    
    Args:
        ai_client: ai_provider 介面(雙系統)
        model: 若 None,動態解析(用 APE 同一個 model)
    
    Returns: 修正後譯文 或 None
    """
    if not violations:
        return bad_tgt
    if ai_client is None:
        try:
            import ai_provider
            ai_client = ai_provider
        except ImportError:
            return None
    
    # 動態解析 model(借 APE 的)
    if model is None:
        try:
            import auto_post_edit
            model = auto_post_edit._resolve_ape_model()
        except Exception:
            try:
                provider = ai_client.get_active_provider()
                model = "claude-sonnet-4-6" if provider == "anthropic" else "gpt-4.1"
            except Exception:
                model = "gpt-4.1-mini"
    
    # 建構修正 prompt(分區 XML)
    violation_lines = []
    for v in violations:
        violation_lines.append(f'  - "{v["src_term"]}" 必須翻譯為 "{v["expected_tgt"]}"')
    
    system_prompt = """<role>
你是術語強制對照修正員。修正譯文,確保所有指定術語使用標準對照。
</role>

<rules>
1. 只修正術語違規,其他部分保持原樣
2. 不改變譯文整體結構、語氣、人名、placeholder
3. 不要加註釋、不要加前綴、不要加 emoji
</rules>

<output_format>
直接輸出修正後譯文,不包任何 tag。
</output_format>"""
    
    user_msg = (
        f"<source_text lang=\"{src_lang}\">{src_text}</source_text>\n"
        f"<current_translation lang=\"{tgt_lang}\">{bad_tgt}</current_translation>\n"
        f"<terminology_violations>\n" + "\n".join(violation_lines) + "\n</terminology_violations>\n\n"
        "請修正譯文,確保上述術語對照被嚴格遵守。直接輸出修正版。"
    )
    
    try:
        resp = ai_client.chat_complete(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=1024,
            temperature=0.0,
        )
        content = ""
        if hasattr(resp, "choices") and resp.choices:
            content = (resp.choices[0].message.content or "").strip()
        if not content:
            return None
        # 移除常見前綴
        for prefix in ("修正後:", "修正版:", "Translation:", "翻譯:", "Catatan:"):
            if content.startswith(prefix):
                content = content[len(prefix):].strip()
        with _lock:
            _stats["auto_fixed"] += 1
        logger.info("[GE] auto-fixed %d violations using %s", len(violations), model)
        return content
    except Exception as e:
        logger.warning("[GE] auto_fix failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════
# 統一入口:enforce()
# ═══════════════════════════════════════════════════════════════════
def enforce_glossary(src_text: str, tgt_text: str,
                     glossary: Dict[str, Any],
                     src_lang: str, tgt_lang: str,
                     ai_client=None) -> Dict[str, Any]:
    """統一 enforcement 入口
    
    根據 GE_ACTION 決定行為:
        "warn" — 只回報,不改譯文
        "auto_fix" — 用 LLM 修正
        "block" — 違規數 >= GE_MAX_VIOLATIONS_BEFORE_BLOCK 時把譯文加 ⚠️ 前綴
    
    Returns:
        {
            "compliant": bool,
            "violations": [...],
            "final_text": str (可能跟 tgt_text 不同),
            "action_taken": "ok" | "warned" | "fixed" | "blocked"
        }
    """
    if not GE_ENABLED:
        return {"compliant": True, "violations": [], "final_text": tgt_text, "action_taken": "skipped"}
    
    compliant, violations = check_glossary_compliance(src_text, tgt_text, glossary, src_lang, tgt_lang)
    
    if compliant:
        return {"compliant": True, "violations": [], "final_text": tgt_text, "action_taken": "ok"}
    
    if GE_ACTION == "auto_fix":
        fixed = auto_fix_violations(src_text, tgt_text, violations, src_lang, tgt_lang, ai_client)
        if fixed and fixed != tgt_text:
            # 再驗一次
            ok2, _v2 = check_glossary_compliance(src_text, fixed, glossary, src_lang, tgt_lang)
            return {
                "compliant": ok2,
                "violations": violations,
                "final_text": fixed,
                "action_taken": "fixed",
            }
        # auto_fix 失敗,降級為 warn
    
    if GE_ACTION == "block" and len(violations) >= GE_MAX_VIOLATIONS_BEFORE_BLOCK:
        with _lock:
            _stats["blocked"] += 1
        warning_prefix = f"⚠️ 術語違規({len(violations)} 條):"
        return {
            "compliant": False,
            "violations": violations,
            "final_text": warning_prefix + tgt_text,
            "action_taken": "blocked",
        }
    
    # 預設 warn(不改 final_text,但回報 violations)
    return {
        "compliant": False,
        "violations": violations,
        "final_text": tgt_text,
        "action_taken": "warned",
    }


# ═══════════════════════════════════════════════════════════════════
# 統計 / 配置 API
# ═══════════════════════════════════════════════════════════════════
def ge_stats() -> Dict[str, Any]:
    with _lock:
        s = dict(_stats)
        s["by_term"] = dict(s["by_term"])
    if s["checks"] > 0:
        s["compliance_rate"] = round(s["compliant"] / s["checks"], 4)
    else:
        s["compliance_rate"] = 0
    # Top 違規術語
    top_terms = sorted(s["by_term"].items(), key=lambda x: x[1], reverse=True)[:10]
    s["top_violated_terms"] = [{"term": t, "count": c} for t, c in top_terms]
    s["enabled"] = GE_ENABLED
    s["action"] = GE_ACTION
    s["min_term_len"] = GE_MIN_TERM_LEN
    return s


def ge_set_config(enabled: Optional[bool] = None,
                  action: Optional[str] = None,
                  min_term_len: Optional[int] = None) -> Dict[str, Any]:
    global GE_ENABLED, GE_ACTION, GE_MIN_TERM_LEN
    if enabled is not None:
        GE_ENABLED = bool(enabled)
    if action and action in ("warn", "auto_fix", "block"):
        GE_ACTION = action
    if min_term_len is not None:
        GE_MIN_TERM_LEN = max(1, int(min_term_len))
    cfg = {
        "enabled": GE_ENABLED,
        "action": GE_ACTION,
        "min_term_len": GE_MIN_TERM_LEN,
    }
    try:
        import phase_config_store as _pcs
        _pcs.save_config("ge", cfg)
    except Exception as _e:
        logger.warning("[GE] save persisted config failed: %s", _e)
    return cfg
