"""
ai_provider.py — 統一 AI Provider 介面層 (v3.2.6 / 2026-05-28)

【v3.2.6 治本修法 — Phase 25 雙系統官方治本(2026-05-28)】
🎯 根治 LLM 元評論洩漏(Wait — I notice / However / If English: / If Indonesian:):
   - 修補 _wrap_system_prompt_xml 在 already_partitioned 模式下忽略 output_tag 的 bug
     (app.py 已含 <role>+<critical_rules> 時,Phase 25 從未生效)
   - OpenAI 路徑對稱實作 Phase 25:sys prompt 注入 <output_format> + response 抽 <translation> tag
   - output_translation_tag 預設改 True
   - 後端強制 regex 抽 <translation> tag 內容,丟棄所有 tag 外雜訊
     → 不依賴 LLM 聽話,後端強制治本
   - 雙系統官方依據:
     OpenAI cookbook persistence + output_contract:
       https://cookbook.openai.com/examples/gpt-5/gpt-5_troubleshooting_guide
     Anthropic use-xml-tags 結構化 system prompt:
       https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags

【v3.3 — 雙系統共用分區 XML system prompt(2026-05-20)】
🎯 根治翻譯品質:
   - 配合 app.py v3.9.37 的分區 XML sys_prompt(<role>/<critical_rules>/
     <factory_vocabulary>/<context_disambiguation>/<format_rules>/<output_format>)
   - _wrap_system_prompt_xml 偵測新分區結構時不再 early return,改 append Anthropic
     專屬條件 tag(<glossary_priority>/<line_message_format>/<layout_preservation>/
     <thinking_protocol>/<success_criteria>)
   - _split_system_into_cache_blocks 改用 </context_disambiguation> 等新 tag 邊界
     當 cache split point(向後相容舊 </rules> 結構)
   - 官方依據:
     https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags
     https://docs.anthropic.com/en/docs/long-context-window-tips
     https://docs.claude.com/en/docs/build-with-claude/prompt-engineering/multishot-prompting
   - 預期效果:Claude long-context 召回率提升,「分流消化/有好處理的」類誤譯根治

【v3.0 — 完整 Claude 翻譯能力(切到 Anthropic 自動全部啟用)】
✅ Phase 1: Prompt Caching             — system / glossary 自動 cache,輸入成本降 70-90%
✅ Phase 2: Extended Thinking          — Sonnet/Opus 啟用思考鏈(budget 2000 tokens)
✅ Phase 3: Search Result Grounding    — 自動把 LINE bot 的 glossary 包成 source blocks
✅ Phase 4: Stop Sequences             — 防 Claude 加註解 / Translation: / Catatan: 等
✅ Phase 5: XML System Prompt Wrapping — Claude 對 XML 標籤遵循度提升 20-30%
✅ Phase 6: Multi-shot Examples        — user/assistant 交替訊息自動相容(LINE bot 已有)
✅ Phase 7: Native Vision              — 圖片/PDF 用 Claude vision 直接讀
✅ Phase 8: 1-hour Extended Cache      — beta header 啟用 1 小時 TTL cache
✅ Phase 9: Citations API              — 顯示用了哪條 glossary 引用
✅ Phase 10: Streaming                 — chat_complete_stream() 提供漸進輸出

【v3.1 D3 新增】
✅ Phase 12: Multi-block Caching       — system 拆 stable(1h)+ dynamic(5m),命中率 60%→95%
✅ Phase 16: Token Counting API        — 翻譯前可預估 token + 成本
✅ Phase 17: Files API for Glossary    — glossary 可上傳到 Anthropic 端,避免每次重傳

【v3.2 D4 新增 — 修 3 個 BUG + 上 3 個新技術(2026-05-15)】
🔴 BUG 修復:
   1. Cache 門檻字元→token,按模型分(Haiku/Opus 4.7=4096 tok / Sonnet 4.6=2048 tok / 舊=1024)
      官方:低於門檻 silent fail(cache_creation_input_tokens=0,照付全價)
   2. Opus 4.7 強制 adaptive thinking — 不再丟 budget_tokens(會 400 + retry 浪費延遲)
   3. Sonnet 4.6 預設改 adaptive(舊 type=enabled 已 deprecated,未來移除)

✨ 新技術 (Phase 13-15):
✅ Phase 13: Adaptive Thinking        — Claude 自己判斷簡單句不思考、複雜句深思考
                                        effort: low/medium/high(+ Opus 4.7 xhigh)
✅ Phase 14: Thinking Display Mode    — Opus 4.7 預設 omitted(快首 token),
                                        Sonnet 4.6 預設 summarized
✅ Phase 15: Smart Cache Threshold    — 用 model-specific token 門檻,避免 silent fail

【v3.2.1 D5 新增(2026-05-15)】
✅ Phase 18: Image-then-text Reorder  — 官方 vision best practice:單圖+文字時
                                        自動把圖片排前面,翻譯/OCR 略佳
                                        多圖場景不重排(避免破壞 few-shot)

【v3.2.2 D6 新增(2026-05-15)】
✅ Phase 19: Image Translation Toggle  — 圖片翻譯是否走 Claude vision 的獨立開關
                                          切到 Anthropic 時可獨立關閉圖片翻譯(成本控制)
                                          OFF 時:文字仍走 Claude,圖片仍走 OpenAI

【v3.2.3 D7 新增 — LINE 視覺/排版品質升級(2026-05-15)】
✅ Phase 20: LINE Plain Text Mode      — 防 Claude 輸出 markdown 廢字元污染 LINE 訊息
                                          (LINE 不渲染 markdown,**粗體** ## 等會字面顯示)
                                          官方根據:「reduce markdown in prompt → reduce markdown in output」
✅ Phase 21: OCR Strict Layout         — 偵測訊息含圖片時,system prompt 加 XML 嚴格保版面指令
                                          (行數 / 編號 / 縮排 / 表格欄位完全對應原文)

【v3.2.4 D8 新增 — Anthropic 官方 prompting 最佳實踐三件套(2026-05-15)】
✅ Phase 22: Assistant Prefill          — 預填回應開頭跳過 preamble(歐那場景預設 OFF)
                                          自動偵測 model(Sonnet 4.6 / Opus 4.6+ 不支援,跳過)
                                          官方:「Prefill bypasses Claude's friendly preamble」
✅ Phase 23: CoT Thinking Tag           — XML 引導 Claude 內部思考(Haiku 4.5 不支援 Extended
                                          Thinking,靠這個補足)
                                          官方:「Chain-of-thought via XML consistently improves accuracy」
✅ Phase 24: Strong Role Prompting     — 強化 <role> 從「翻譯助手」變「20 年資深中印工廠譯者」
                                          官方:「Detailed role with specific expertise produces
                                          better quality responses」

【v3.2.5 D9 新增 — Anthropic 官方「Output Tag + Success Criteria」終局(2026-05-16)】
✅ Phase 25: Output Translation Tag    — 強制 Claude 把翻譯包在 <translation>...</translation> tag
                                          內,後端 regex 抽 tag 內內容,徹底解決前綴問題。
                                          官方明文:「Having output in XML tags allows reliable extraction」
                                          預設 OFF;歐那實測後可開啟取代 stop_sequences。
✅ Phase 26: Success Criteria          — system prompt 加 <success_criteria> 6 條成功標準
                                          官方明文:「State the expected outcome and success criteria」
                                          預設 ON,直接影響 Claude 翻譯品質判斷基準。

【作者】onerkk@gmail.com
"""

import os
import json
import re
import time
import threading

import glossary_policy as gp_module

# ═══════════════════════════════════════════════════════════════════
# 設定檔路徑
# ═══════════════════════════════════════════════════════════════════
def _resolve_provider_config_path():
    for d in ("/var/data", "/data", "/tmp"):
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return os.path.join(d, "ai_provider_config.json")
    return "ai_provider_config.json"

PROVIDER_CONFIG_PATH = _resolve_provider_config_path()

# ═══════════════════════════════════════════════════════════════════
# v3.3.0 (2026-06-16): OpenAI 模型生命週期與翻譯模型政策
# ═══════════════════════════════════════════════════════════════════
# 後台只提供仍在服役、適合文字/圖片翻譯的模型。舊設定不直接丟棄，
# 而是在呼叫前遷移到官方建議替代型號，避免重新部署後因舊 model id 404。
ACTIVE_OPENAI_TRANSLATION_MODELS = (
    "gpt-5.6-luna",   # 即時、高量翻譯：官方定位為 fast / high-volume
    "gpt-5.6-terra",  # 品質與成本平衡：長公告、複雜工廠語境
    "gpt-5.6-sol",    # 最高品質選項；成本較高，不作一般預設
    "gpt-5.4-mini",   # 低成本相容選項
    "gpt-5.4-nano",   # 最低成本輔助任務
    "gpt-4.1-mini",   # 穩定非推理相容選項
    "gpt-4.1",
)
ACTIVE_OPENAI_VISION_MODELS = (
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.4-mini",
    "gpt-4.1-mini",
    "gpt-5.4-nano",
    "gpt-4.1",
)
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_OPENAI_UPGRADE_MODEL = "gpt-5.6-terra"
DEFAULT_OPENAI_AUX_MODEL = "gpt-5.4-nano"
DEFAULT_OPENAI_VISION_MODEL = "gpt-5.6-terra"
DEFAULT_OPENAI_VISION_FALLBACK_MODEL = "gpt-5.6-luna"
# GPT-4o mini TTS family is deprecated. Use the still-active Speech API model
# tts-1 by default; keep tts-1-hd as an optional quality-first choice.
DEFAULT_OPENAI_TTS_MODEL = "tts-1"
ACTIVE_OPENAI_TTS_MODELS = (
    "tts-1",
    "tts-1-hd",
)

# 官方 deprecations 頁面列出的替代關係，以及本專案過去曾產生的無效名稱。
# floating alias 的遷移是本專案的主動升級政策；dated snapshot 的替代則依官方公告。
OPENAI_MODEL_REPLACEMENTS = {
    # v3.32 model policy: migrate floating GPT-5 aliases to the current
    # production family while preserving older explicit cost choices.
    "gpt-5.6": "gpt-5.6-sol",
    "gpt-5.5": "gpt-5.6-sol",
    "gpt-5.4": "gpt-5.6-terra",
    # 2026-12-11 shutdown（官方替代）
    "gpt-5-2025-08-07": "gpt-5.5",
    "gpt-5-mini-2025-08-07": "gpt-5.4-mini",
    "gpt-5-nano-2025-08-07": "gpt-5.4-nano",
    "gpt-5-pro-2025-10-06": "gpt-5.5-pro",
    "o3-2025-04-16": "gpt-5.5",
    "o3-pro-2025-06-10": "gpt-5.5-pro",
    # 2026-10-23 shutdown（官方替代）
    "gpt-4.1-nano": "gpt-5.4-nano",
    "gpt-4.1-nano-2025-04-14": "gpt-5.4-nano",
    "o1": "gpt-5.5",
    "o1-2024-12-17": "gpt-5.5",
    "o1-pro": "gpt-5.5-pro",
    "o1-pro-2025-03-19": "gpt-5.5-pro",
    "o3-mini": "gpt-5.5",
    "o3-mini-2025-01-31": "gpt-5.5",
    "o4-mini": "gpt-5.4-mini",
    "o4-mini-2025-04-16": "gpt-5.4-mini",
    # 2026-07-23 shutdown / legacy aliases
    "gpt-5-chat-latest": "gpt-5.5",
    "gpt-5-codex": "gpt-5.5",
    "gpt-5.1-chat-latest": "gpt-5.5",
    "gpt-5.1-codex": "gpt-5.5",
    "gpt-5.1-codex-max": "gpt-5.5",
    "gpt-5.1-codex-mini": "gpt-5.4-mini",
    "gpt-5.2-codex": "gpt-5.5",
    # 本專案舊選單 / 過去程式可能留下的名稱
    "gpt-5": "gpt-5.6-sol",
    "gpt-5-mini": "gpt-5.6-terra",
    "gpt-5-nano": "gpt-5.4-nano",
    "gpt-5.1": "gpt-5.4",
    "gpt-5.2": "gpt-5.4",
    "gpt-5.5-mini": "gpt-5.4-mini",  # OpenAI 未提供
    "gpt-5.5-nano": "gpt-5.4-nano",  # OpenAI 未提供
    "gpt-5.2-nano": "gpt-5.4-nano",  # OpenAI 未提供
    "gpt-5.1-nano": "gpt-5.4-nano",  # OpenAI 未提供
}


def normalize_openai_model(model, fallback=None, allowed=None):
    """遷移舊 OpenAI model id，並可選擇限制在白名單內。

    Claude / Gemini 名稱原樣保留。未知 OpenAI 名稱在沒有 allowed 時保留，
    讓未來新模型仍可由進階程式呼叫；後台與持久化設定會傳 allowed，
    因而不會把拼錯或已移除的名稱送進正式翻譯流程。
    """
    fallback = fallback or DEFAULT_OPENAI_MODEL
    if model is None:
        return fallback
    raw = str(model).strip()
    if not raw:
        return fallback
    low = raw.lower()
    if low.startswith(("claude-", "gemini")):
        # Generic mapping helpers may need to preserve a provider-native name,
        # but a caller that supplies an OpenAI allow-list is explicitly asking
        # for an OpenAI request model.  Never let a Claude/Gemini ID cross that
        # boundary and become a paid 404 request.
        return fallback if allowed is not None else raw
    normalized = raw
    # Some historical IDs map to an intermediate floating alias. Resolve a
    # short chain so a retired snapshot never survives just because the first
    # replacement itself was later superseded.
    seen = set()
    for _ in range(4):
        key = str(normalized).lower()
        if key in seen or key not in OPENAI_MODEL_REPLACEMENTS:
            break
        seen.add(key)
        normalized = OPENAI_MODEL_REPLACEMENTS[key]
    if allowed is not None and normalized not in tuple(allowed):
        return fallback
    return normalized


def normalize_openai_request_model(model, fallback=None):
    """Resolve a model at the final OpenAI API boundary.

    Provider failover deliberately carries one canonical quality-tier model
    through the coordinator.  Historical code paths can still hand this function
    a provider-native Claude/Gemini model.  OpenAI must never receive those IDs;
    they are replaced with the configured OpenAI fallback before the request is
    created.  Unknown future OpenAI-looking IDs remain forward-compatible.
    """
    fallback = fallback or DEFAULT_OPENAI_MODEL
    raw = str(model or "").strip()
    if not raw:
        return fallback
    low = raw.lower()
    if low.startswith("claude-"):
        # Preserve the requested quality tier across provider failover instead
        # of collapsing every Claude model to the cheapest OpenAI default.
        return (
            DEFAULT_OPENAI_UPGRADE_MODEL
            if any(family in low for family in ("sonnet", "opus"))
            else fallback
        )
    if low.startswith("gemini"):
        return (
            fallback
            if "flash-lite" in low
            else DEFAULT_OPENAI_UPGRADE_MODEL
        )
    return normalize_openai_model(raw, fallback=fallback)


def normalize_translation_model(model, fallback=None):
    return normalize_openai_model(
        model, fallback or DEFAULT_OPENAI_MODEL, ACTIVE_OPENAI_TRANSLATION_MODELS)


def normalize_vision_model(model, fallback=None):
    return normalize_openai_model(
        model, fallback or DEFAULT_OPENAI_VISION_MODEL, ACTIVE_OPENAI_VISION_MODELS)


def normalize_tts_model(model, fallback=None):
    """遷移已公告停用的 TTS snapshot，並限制為 Speech API 可用型號。"""
    fallback = fallback or DEFAULT_OPENAI_TTS_MODEL
    raw = str(model or "").strip().lower()
    replacements = {
        "gpt-4o-mini-tts": DEFAULT_OPENAI_TTS_MODEL,
        "gpt-4o-mini-tts-2025-03-20": DEFAULT_OPENAI_TTS_MODEL,
        "gpt-4o-mini-tts-2025-12-15": DEFAULT_OPENAI_TTS_MODEL,
    }
    normalized = replacements.get(raw, raw or fallback)
    return normalized if normalized in ACTIVE_OPENAI_TTS_MODELS else fallback

# ═══════════════════════════════════════════════════════════════════
# 預設配置
# ═══════════════════════════════════════════════════════════════════
DEFAULT_CONFIG = {
    # v3.37: production default/failover hierarchy is strict and predictable:
    # Claude -> OpenAI(ChatGPT) -> Gemini.  The active provider remains the
    # first choice; this default only applies to a fresh install.
    "active_provider": "anthropic",
    "openai": {"api_key": "", "base_url": None},
    "anthropic": {
        "api_key": "",
        "default_model": "claude-haiku-4-5-20251001",
    },
    # === v3.21 Gemini(第三 provider)===
    # Google 官方把 Gemini 3.1 Flash-Lite 的第一使用場景明列為
    # 「翻譯:快速、便宜、大量,例如處理聊天訊息」— 三家裡最低價位。
    # 走 Gemini 官方 OpenAI 相容端點,重用 OpenAI SDK,不引入新依賴。
    "gemini": {
        "api_key": "",
        "default_model": "gemini-3.1-flash-lite",   # 短訊息(最省)
        # Gemini 2.5 Flash is Google's stable best-price-performance model and
        # is substantially cheaper than 3.5 Flash for long factory notices.
        "upgrade_model": "gemini-2.5-flash",
    },
    # v3.25: OpenAI 官方 Flex tier — 背景品檢呼叫(QE/APE)半價。
    # 官方定位:「較低費用換較慢回應與偶發資源不足,適合非即時任務」,
    # 背景執行緒不在使用者等待路徑上 = 零感知省 50%。
    # v3.26: 跨 provider 自動容錯移轉(主力限流/過載/連線失敗時換備援家救句子)
    "provider_failover": True,
    # 單一協調層負責跨 provider 接力。所有 provider 共用一個總期限，
    # 避免「外層重試 × 三家切換 × SDK 內建重試」把單句拖到數分鐘。
    "failover_policy": {
        "total_timeout_seconds": 60,
        "per_provider_timeout_seconds": 24,
        "circuit_breaker_failures": 2,
        "circuit_breaker_cooldown_seconds": 60,
        "provider_order": ["anthropic", "openai", "gemini"],
        # Real-time LINE messages need a much tighter tail-latency budget than
        # long documents or OCR.  Callers select one profile; the old two
        # timeout fields remain as backwards-compatible fallbacks.
        "latency_profiles": {
            "realtime_text": {"total": 22, "per_provider": 8},
            "long_text": {"total": 40, "per_provider": 16},
            "vision": {"total": 50, "per_provider": 20},
            "background": {"total": 90, "per_provider": 45},
        },
        # Root-fix: keep failover deterministic.  Cost/latency telemetry must
        # never reorder ChatGPT behind Gemini when Claude is the configured main.
        "strict_failover_order": True,
        "adaptive_backup_order": False,
        "latency_ewma_alpha": 0.25,
    },
    # v3.28/v3.37: 額度耗盡 → 永久切換主力 + LINE 通知管理員。
    # Exhausted providers are persisted and skipped until an admin explicitly
    # switches back or updates that provider's API key.
    "auto_switch_on_exhaust": True,
    "quota_exhausted_providers": {},
    "auto_switch_state": {},
    "openai_features": {
        "flex_background": True,   # CP值預設 ON;僅 gpt-5 系/o 系生效,其他模型自動略過
    },
    "gemini_features": {
        # Gemini 3 系列翻譯使用 minimal：保留模型與 prompt，只壓低不必要的思考延遲。
        # 相容端點若暫不接受 minimal，會先退到 low，再移除可選參數重試。
        "reasoning_effort": "minimal",
    },
    # OpenAI 模型名 → Gemini 模型(與 anthropic model_mapping 同模式)
    "gemini_model_mapping": {
        "gpt-5.6-luna": "gemini-3.1-flash-lite",
        "gpt-5.6-terra": "gemini-2.5-flash",
        "gpt-5.6-sol": "gemini-2.5-flash",
        "gpt-5.4-mini": "gemini-3.1-flash-lite",
        "gpt-4.1-mini": "gemini-3.1-flash-lite",
        "gpt-5.4-nano": "gemini-3.1-flash-lite",
        "gpt-4.1":      "gemini-2.5-flash",
        "gpt-5.4":      "gemini-2.5-flash",
        "gpt-5.5":      "gemini-2.5-flash",
        # 相容仍在服役但不再列入本專案新選單的舊多模態模型
        "gpt-4o-mini":  "gemini-3.1-flash-lite",
        "gpt-4o":       "gemini-2.5-flash",
    },
    "model_mapping": {
        "gpt-5.6-luna": "claude-haiku-4-5-20251001",
        "gpt-5.6-terra": "claude-sonnet-5",
        "gpt-5.6-sol": "claude-sonnet-5",
        "gpt-5.4-mini": "claude-haiku-4-5-20251001",
        "gpt-4.1-mini": "claude-haiku-4-5-20251001",
        "gpt-5.4-nano": "claude-haiku-4-5-20251001",
        "gpt-4.1":      "claude-sonnet-5",
        "gpt-5.4":      "claude-sonnet-5",
        "gpt-5.5":      "claude-sonnet-5",
        # 相容仍在服役但不再列入本專案新選單的舊多模態模型
        "gpt-4o-mini":  "claude-haiku-4-5-20251001",
        "gpt-4o":       "claude-sonnet-5",
    },
    # === v3.0 Claude 專屬能力(全部預設 ON)===
    "claude_features": {
        "prompt_caching": True,         # Phase 1
        # v3.18 省錢省時預設:翻譯是輕量指令跟隨任務,thinking/reasoning 提高
        # 反而傷指令遵循(arxiv 2505.14810,OpenAI 路徑 v3.9.8 同結論已套用)。
        # Sonnet 升級路徑原本每句先吐最多 2000 thinking tokens 才出譯文
        # = 多 3-8 秒延遲 + 2000 output tokens 費用,品質無增益。
        # 預設改 OFF;後台 claude_features 開關照常可開回(存檔值優先)。
        "extended_thinking": False,     # Phase 2(v3.18: True→False)
        "thinking_budget": 2000,
        "glossary_grounding": True,     # Phase 3
        "glossary_max_items": 50,
        "stop_sequences": True,         # Phase 4
        "xml_system_prompt": True,      # Phase 5
        # Phase 6 自動,不用 flag
        "native_vision": True,          # Phase 7
        "extended_cache_1h": True,      # Phase 8 — 1 hour TTL
        "citations": True,              # Phase 9
        # Phase 10 streaming 用獨立 function
        # === D3 v3.1 新增 ===
        "multi_block_caching": True,    # Phase 12 — 多層 cache(stable + dynamic)
        "files_api_glossary": False,    # Phase 17 — 把 glossary 上傳 Files API(預設 OFF,要先上傳)
        # === D4 v3.2 新增 ===
        "adaptive_thinking": True,      # Phase 13 — Opus 4.7 強制 / Sonnet 4.6 推薦
        "thinking_effort": "low",       # v3.18: medium→low(若開 thinking 也用最低檔)
        "thinking_display": "auto",     # auto / summarized / omitted
                                        # auto = Opus 4.7→omitted(快); Sonnet/Opus 4.6→summarized
        "smart_cache_threshold": True,  # Phase 15 — 用 model-specific token 門檻,而非字元數 1024
        # === D5 v3.2.2 新增 ===
        "image_translation_use_claude": True,  # Phase 19 — 切到 Anthropic 時,圖片翻譯也走 Claude vision
                                                # OFF 時:圖片優先走其他已設定的視覺 provider；統一協調層仍可跨三家接力
        # === D7 v3.2.3 新增 ===
        "line_plain_text_mode": True,   # Phase 20 — 防 Claude 輸出 markdown 廢字元(LINE 不渲染)
                                         # 禁:**粗體** *斜體* `code` # 標題 --- 分隔線 markdown table
        "ocr_strict_layout": True,      # Phase 21 — OCR 翻譯場景嚴格保版面(行數 / 編號 / 縮排對齊)
                                         # 偵測訊息含 image block 才啟動
        # === D8 v3.2.4 新增 ===
        "assistant_prefill": False,     # Phase 22 — Assistant Prefill(預填回應開頭)
                                         # 預設 OFF;歐那要時填 assistant_prefill_text 字串
                                         # 自動偵測 model(Sonnet 4.6 / Opus 4.6+ 跳過)
        "assistant_prefill_text": "",   # Phase 22 — 自訂 prefill 文字(空字串=不啟用)
        "cot_thinking_tag": True,       # Phase 23 — XML CoT 引導(讓 Haiku 也能思考)
        "role_strong": True,            # Phase 24 — 強化 Role(20 年資深譯者身分)
        # === D9 v3.2.5 新增 ===
        "output_translation_tag": True,  # Phase 25 — 強制 <translation>...</translation> XML 包裝
                                         # v3.2.6 預設改 True:後端 regex 抽 tag 內內容,
                                         # 徹底丟棄 LLM 元評論(Wait/I notice/However/If English:/markdown)
                                         # 不依賴 LLM 聽話,後端強制抽取治本
        "success_criteria": True,       # Phase 26 — system prompt 加 <success_criteria> 段
                                         # 官方:「State expected outcome and success criteria」
    },
    "last_updated": "",
}

_config_lock = threading.RLock()
_current_config = None
_last_config_mtime = 0  # 修跨 worker 同步 — 記錄上次讀 config 時磁碟檔 mtime
_openai_client = None
_anthropic_client = None
_gemini_client = None   # v3.21
_registered_glossary = None

# Provider 健康狀態只存於目前 worker；用途是暫時避開連續失敗的供應商，
# 不寫入設定檔，也不會永久停用。多 worker 各自觀察自己的連線狀況較安全。
_provider_health_lock = threading.RLock()
_provider_health = {
    "openai": {"failures": 0, "open_until": 0.0, "last_error": "", "latency_ewma": None, "successes": 0},
    "anthropic": {"failures": 0, "open_until": 0.0, "last_error": "", "latency_ewma": None, "successes": 0},
    "gemini": {"failures": 0, "open_until": 0.0, "last_error": "", "latency_ewma": None, "successes": 0},
}

_PROVIDER_CAPABILITIES = {
    "openai": {"chat", "vision", "batch"},
    "anthropic": {"chat", "vision", "batch"},
    "gemini": {"chat", "vision"},
}

# === D3 Phase 17: Files API state ===
# 把整個 glossary 上傳到 Anthropic Files API,後續 messages 內只引用 file_id
# 避免每次都送 2-5KB glossary 文字
_uploaded_glossary_file_id = None  # 上傳成功後存的 file_id
_uploaded_glossary_hash = None      # 用來判斷 glossary 有沒有改過需要重傳


# ═══════════════════════════════════════════════════════════════════
# Config 讀寫
# ═══════════════════════════════════════════════════════════════════
def _migrate_config_models(cfg):
    """把磁碟中的舊 mapping key 遷移到現行模型與 CP 路由。

    v3.36 intentionally migrates the former expensive Gemini 3.5 Flash text
    upgrade route to stable Gemini 2.5 Flash.  This is a one-way migration only
    for values that still equal the old project default; an administrator who
    later chooses another model is not overwritten.
    """
    for field in ("model_mapping", "gemini_model_mapping"):
        defaults = dict(DEFAULT_CONFIG.get(field, {}))
        saved = cfg.get(field, {})
        if isinstance(saved, dict):
            for old_key, target in saved.items():
                new_key = normalize_openai_model(old_key)
                defaults[new_key] = target
        cfg[field] = defaults

    gemini_cfg = cfg.setdefault("gemini", {})
    if gemini_cfg.get("upgrade_model") in (None, "", "gemini-3.5-flash"):
        gemini_cfg["upgrade_model"] = "gemini-2.5-flash"

    gm = cfg.setdefault("gemini_model_mapping", {})
    for key in ("gpt-5.6-terra", "gpt-5.6-sol", "gpt-4.1", "gpt-5.4", "gpt-5.5", "gpt-4o"):
        if gm.get(key) in (None, "", "gemini-3.5-flash"):
            gm[key] = "gemini-2.5-flash"

    policy = cfg.setdefault("failover_policy", {})
    old_order = list(policy.get("provider_order") or [])
    # v3.37 root-fix: legacy configs are migrated to one strict recovery chain.
    # If Claude has no credit, ChatGPT must be tried before Gemini.
    policy["provider_order"] = list(dict.fromkeys(
        ["anthropic", "openai", "gemini"] + old_order
    ))
    policy["strict_failover_order"] = True
    policy["adaptive_backup_order"] = False
    if not isinstance(cfg.get("quota_exhausted_providers"), dict):
        cfg["quota_exhausted_providers"] = {}
    if not isinstance(cfg.get("auto_switch_state"), dict):
        cfg["auto_switch_state"] = {}
    return cfg


def _load_config_from_disk():
    try:
        if os.path.exists(PROVIDER_CONFIG_PATH):
            with open(PROVIDER_CONFIG_PATH, "r", encoding="utf-8") as f:
                disk_cfg = json.load(f)
            merged = json.loads(json.dumps(DEFAULT_CONFIG))
            def _deep_merge(dst, src):
                for k, v in src.items():
                    if isinstance(v, dict) and isinstance(dst.get(k), dict):
                        _deep_merge(dst[k], v)
                    else:
                        dst[k] = v
            _deep_merge(merged, disk_cfg)
            return _migrate_config_models(merged)
    except Exception as e:
        print(f"[ai_provider] WARN: 讀 config 失敗 {e}", flush=True)
    return json.loads(json.dumps(DEFAULT_CONFIG))


def _save_config_to_disk(cfg):
    global _last_config_mtime
    try:
        cfg["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(PROVIDER_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        # 存檔後更新 mtime,避免自己的 _ensure_initialized 又 reload 一次
        try:
            _last_config_mtime = os.path.getmtime(PROVIDER_CONFIG_PATH)
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"[ai_provider] ERROR: 存 config 失敗 {e}", flush=True)
        return False


def _init_config():
    global _current_config
    cfg = _load_config_from_disk()
    if not cfg["openai"].get("api_key"):
        cfg["openai"]["api_key"] = os.environ.get("OPENAI_API_KEY", "")
    if not cfg["anthropic"].get("api_key"):
        cfg["anthropic"]["api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")
    # v3.21: Gemini key 環境變數(GEMINI_API_KEY 或 GOOGLE_API_KEY 都接受)
    if not cfg.get("gemini", {}).get("api_key"):
        cfg.setdefault("gemini", {})["api_key"] = (
            os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", ""))
    _current_config = cfg


def _ensure_initialized():
    """每次都重讀磁碟 — 修跨 Gunicorn worker 同步 bug
    
    為什麼這樣做:
      Render 用 gunicorn --workers N 跑,每個 worker 是獨立 process
      各自有自己的 _current_config global 變數
      用戶在後台切換 provider → 只有「處理切換 request 的那個 worker」更新了
      其他 worker 的 _current_config 還是舊值
      → 用戶下次翻譯被別的 worker 處理時,讀到舊值,打到舊 provider
    
    解法:每次 _ensure_initialized() 都檢查磁碟檔有沒有更新,有就 reload
    成本:每次多一個 stat() + 可能一次 read(磁碟很快,影響可忽略)
    """
    global _current_config, _last_config_mtime
    with _config_lock:
        try:
            mtime = os.path.getmtime(PROVIDER_CONFIG_PATH) if os.path.exists(PROVIDER_CONFIG_PATH) else 0
        except Exception:
            mtime = 0
        # 第一次 init,或磁碟檔有更新 → 重讀
        if _current_config is None or mtime > _last_config_mtime:
            _init_config()
            _last_config_mtime = mtime


# ═══════════════════════════════════════════════════════════════════
# Clients
# ═══════════════════════════════════════════════════════════════════
def _get_openai_client():
    global _openai_client
    _ensure_initialized()
    with _config_lock:
        api_key = _current_config["openai"].get("api_key", "")
        if not api_key:
            return None
        if _openai_client is not None and getattr(_openai_client, "_jy_key", None) == api_key:
            return _openai_client
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=api_key, timeout=90.0, max_retries=0)  # v3.2.7: 30→90 配合長訊息翻譯
            _openai_client._jy_key = api_key
            return _openai_client
        except Exception as e:
            print(f"[ai_provider] OpenAI client 建立失敗 {e}", flush=True)
            return None


def _get_anthropic_client():
    global _anthropic_client
    _ensure_initialized()
    with _config_lock:
        api_key = _current_config["anthropic"].get("api_key", "")
        if not api_key:
            return None
        if _anthropic_client is not None and getattr(_anthropic_client, "_jy_key", None) == api_key:
            return _anthropic_client
        try:
            from anthropic import Anthropic
            # Phase 8: 啟用 extended-cache-ttl beta header(1 hour cache 需要)
            extra_headers = {}
            features = _current_config.get("claude_features", {})
            if features.get("extended_cache_1h", True):
                extra_headers["anthropic-beta"] = "extended-cache-ttl-2025-04-11"
            _anthropic_client = Anthropic(
                api_key=api_key,
                timeout=120.0,
                max_retries=0,
                default_headers=extra_headers if extra_headers else None,
            )
            _anthropic_client._jy_key = api_key
            return _anthropic_client
        except Exception as e:
            print(f"[ai_provider] Anthropic client 建立失敗 {e}", flush=True)
            return None


def _get_gemini_client():
    """v3.21: Gemini 走官方 OpenAI 相容端點 — 重用 OpenAI SDK,零新依賴。
    https://ai.google.dev/gemini-api/docs/openai
    """
    global _gemini_client
    _ensure_initialized()
    with _config_lock:
        api_key = _current_config.get("gemini", {}).get("api_key", "")
        if not api_key:
            return None
        if _gemini_client is not None and getattr(_gemini_client, "_jy_key", None) == api_key:
            return _gemini_client
        try:
            from openai import OpenAI
            _gemini_client = OpenAI(
                api_key=api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                timeout=90.0,
                max_retries=0,
            )
            _gemini_client._jy_key = api_key
            return _gemini_client
        except Exception as e:
            print(f"[ai_provider] Gemini client 建立失敗 {e}", flush=True)
            return None


def _client_with_limits(client, timeout):
    """建立單次呼叫 client，關閉 SDK 隱藏重試並套用本協調層分配的期限。"""
    try:
        return client.with_options(timeout=float(timeout), max_retries=0)
    except (AttributeError, TypeError):
        # 測試替身或舊 SDK 沒有 with_options 時仍可運作；呼叫參數中的 timeout
        # 會保留為第二道限制。
        return client


# ═══════════════════════════════════════════════════════════════════
# 對外 API
# ═══════════════════════════════════════════════════════════════════
def get_active_provider():
    _ensure_initialized()
    return _current_config.get("active_provider", "openai")


def _provider_has_key(provider):
    _ensure_initialized()
    return bool((_current_config or {}).get(provider, {}).get("api_key"))


def _provider_quota_blocked(provider):
    """True when a provider returned an explicit credit/quota exhaustion error.

    This state is persisted in ai_provider_config.json so every Gunicorn worker
    skips the depleted provider.  It is intentionally not time-based: refill is
    an external billing event, so only an admin switch/key update clears it.
    """
    _ensure_initialized()
    blocked = (_current_config or {}).get("quota_exhausted_providers", {})
    return bool(isinstance(blocked, dict) and blocked.get(provider))


def get_quota_exhausted_providers():
    """Return a safe copy for admin diagnostics/tests."""
    _ensure_initialized()
    with _config_lock:
        value = (_current_config or {}).get("quota_exhausted_providers", {})
        return json.loads(json.dumps(value if isinstance(value, dict) else {}))


def _provider_supports(provider, capability="chat"):
    return capability in _PROVIDER_CAPABILITIES.get(provider, set())


def _circuit_is_open(provider, now=None):
    now = time.monotonic() if now is None else now
    with _provider_health_lock:
        return float(_provider_health.get(provider, {}).get("open_until", 0.0) or 0.0) > now


def _record_provider_success(provider, latency_seconds=None):
    _ensure_initialized()
    policy = (_current_config or {}).get("failover_policy", {})
    alpha = min(1.0, max(0.01, float(policy.get("latency_ewma_alpha", 0.25) or 0.25)))
    with _provider_health_lock:
        state = _provider_health.setdefault(provider, {})
        state.update({"failures": 0, "open_until": 0.0, "last_error": ""})
        if latency_seconds is not None:
            latency = max(0.0, float(latency_seconds))
            prev = state.get("latency_ewma")
            state["latency_ewma"] = latency if prev is None else (alpha * latency + (1.0 - alpha) * float(prev))
            state["successes"] = int(state.get("successes", 0) or 0) + 1


def _record_provider_failure(provider, err):
    _ensure_initialized()
    policy = (_current_config or {}).get("failover_policy", {})
    threshold = max(1, int(policy.get("circuit_breaker_failures", 2) or 2))
    cooldown = max(1.0, float(policy.get("circuit_breaker_cooldown_seconds", 60) or 60))
    with _provider_health_lock:
        state = _provider_health.setdefault(
            provider, {"failures": 0, "open_until": 0.0, "last_error": "", "latency_ewma": None, "successes": 0})
        state["failures"] = int(state.get("failures", 0) or 0) + 1
        state["last_error"] = str(err)[:300]
        if state["failures"] >= threshold:
            state["open_until"] = time.monotonic() + cooldown


def reset_provider_health(provider=None):
    """測試/管理用途：清除暫時熔斷狀態，不變更 API key 或主力設定。"""
    targets = (provider,) if provider else tuple(_PROVIDER_CAPABILITIES)
    with _provider_health_lock:
        for name in targets:
            _provider_health[name] = {
                "failures": 0, "open_until": 0.0, "last_error": "",
                "latency_ewma": None, "successes": 0,
            }


def get_provider_health_snapshot():
    """Return a copy suitable for admin diagnostics and tests."""
    with _provider_health_lock:
        return {name: dict(state) for name, state in _provider_health.items()}


def get_available_providers(capability="chat", preference=None, include_open_circuits=False):
    """回傳具備指定能力且已設定 key 的 provider，順序即實際接力順序。

    preference 可指定優先順序；未指定時主力優先，再依 failover_policy.provider_order。
    已明確回報額度耗盡的 provider 會跨 worker 持久跳過，直到管理員修復。
    熔斷中的 provider 在仍有其他可用者時會略過；若全部都熔斷，允許一次探測，
    避免所有服務在冷卻期間被永久鎖死。
    """
    _ensure_initialized()
    active = get_active_provider()
    configured_order = list(((_current_config or {}).get("failover_policy", {})
                             .get("provider_order") or ("anthropic", "openai", "gemini")))
    requested = list(preference or [])
    order = []
    for name in ([active] if not requested else []) + requested + configured_order + ["openai", "gemini", "anthropic"]:
        if name in _PROVIDER_CAPABILITIES and name not in order:
            order.append(name)
    eligible = [
        p for p in order
        if _provider_has_key(p)
        and _provider_supports(p, capability)
        and not _provider_quota_blocked(p)
    ]
    if include_open_circuits:
        return eligible
    closed = [p for p in eligible if not _circuit_is_open(p)]
    candidates = closed or eligible

    # Preserve the explicitly selected first provider.  Only rank backups;
    # this avoids silently changing the user's cost/provider preference while
    # still improving tail latency after a failure.
    _failover_policy = ((_current_config or {}).get("failover_policy", {}) or {})
    strict_order = bool(_failover_policy.get("strict_failover_order", True))
    adaptive = bool(_failover_policy.get("adaptive_backup_order", False)) and not strict_order
    if adaptive and len(candidates) > 2:
        first, rest = candidates[0], candidates[1:]
        with _provider_health_lock:
            def _score(name):
                state = _provider_health.get(name, {})
                latency = state.get("latency_ewma")
                # Unknown providers remain in configured order behind measured
                # fast providers but ahead of a clearly slow one only after
                # enough evidence accumulates.
                return (latency is None, float(latency or 9999.0),
                        int(state.get("failures", 0) or 0))
            rest = sorted(rest, key=_score)
        candidates = [first] + rest
    return candidates


def has_available_provider(capability="chat"):
    return bool(get_available_providers(capability=capability))


def get_native_client(provider):
    """供同專案的原生 API 模組（Batch/TTS 以外）取得已套用統一設定的 client。"""
    if provider == "openai":
        return _get_openai_client()
    if provider == "anthropic":
        return _get_anthropic_client()
    if provider == "gemini":
        return _get_gemini_client()
    return None


def set_active_provider(provider, *, manual=False, respect_auto_switch=False):
    """Set the main provider.

    manual=True means an administrator intentionally selected this provider
    after refill/testing, so its persisted exhausted flag is cleared.
    respect_auto_switch=True is used while restoring old bot_settings: a stale
    saved provider must not undo a newer automatic billing failover.
    """
    if provider not in ("openai", "anthropic", "gemini"):
        return False, f"unknown provider: {provider}"
    _ensure_initialized()
    with _config_lock:
        state = (_current_config or {}).get("auto_switch_state", {})
        if (respect_auto_switch and isinstance(state, dict) and state.get("active")
                and provider != (_current_config or {}).get("active_provider")):
            return False, "保留額度耗盡後的自動切換主力"
        blocked = (_current_config or {}).setdefault("quota_exhausted_providers", {})
        if provider in blocked and not manual:
            return False, f"{provider} 已標記額度耗盡，需管理員手動切回或更新 API key"
        if manual:
            blocked.pop(provider, None)
            _current_config["auto_switch_state"] = {}
        _current_config["active_provider"] = provider
        if _save_config_to_disk(_current_config):
            reset_provider_health(provider)
            return True, f"切換到 {provider}"
        return False, "存檔失敗"


def update_provider_key(provider, api_key):
    if provider not in ("openai", "anthropic", "gemini"):
        return False, f"unknown provider: {provider}"
    _ensure_initialized()
    with _config_lock:
        _current_config.setdefault(provider, {})["api_key"] = (api_key or "").strip()
        # A new/re-entered key is the strongest signal that billing credentials
        # were repaired.  Allow this provider to participate again, but do not
        # silently make it primary; the admin can switch it back explicitly.
        _current_config.setdefault("quota_exhausted_providers", {}).pop(provider, None)
        if _save_config_to_disk(_current_config):
            global _openai_client, _anthropic_client, _gemini_client
            if provider == "openai":
                _openai_client = None
            elif provider == "gemini":
                _gemini_client = None
            else:
                _anthropic_client = None
            return True, f"{provider} key 已更新"
        return False, "存檔失敗"


def update_openai_features(features):
    """v3.25: 後台更新 OpenAI 設定(flex_background 等)。"""
    if not isinstance(features, dict):
        return False, "features 必須是 dict"
    _ensure_initialized()
    with _config_lock:
        cur = _current_config.get("openai_features", {})
        cur.update(features)
        _current_config["openai_features"] = cur
        if _save_config_to_disk(_current_config):
            return True, "OpenAI features 已更新"
        return False, "存檔失敗"


def update_gemini_features(features):
    """v3.21: 後台更新 Gemini 設定(reasoning_effort 等)。"""
    if not isinstance(features, dict):
        return False, "features 必須是 dict"
    _ensure_initialized()
    with _config_lock:
        cur = _current_config.get("gemini_features", {})
        cur.update(features)
        _current_config["gemini_features"] = cur
        if _save_config_to_disk(_current_config):
            return True, "Gemini features 已更新"
        return False, "存檔失敗"


def update_gemini_models(default_model=None, upgrade_model=None):
    """v3.22: 後台更新 Gemini 短/長訊息模型(與 Claude 雙模型同模式)。"""
    _ensure_initialized()
    with _config_lock:
        g = _current_config.setdefault("gemini", {})
        if default_model:
            g["default_model"] = str(default_model).strip()
        if upgrade_model:
            g["upgrade_model"] = str(upgrade_model).strip()
        if _save_config_to_disk(_current_config):
            return True, "Gemini 模型已更新"
        return False, "存檔失敗"


def get_gemini_models():
    """v3.22: 回傳 (短訊息模型, 長訊息模型) — pick_model 用,後台改了立即生效。"""
    _ensure_initialized()
    g = _current_config.get("gemini", {}) if _current_config else {}
    return (g.get("default_model") or "gemini-3.1-flash-lite",
            g.get("upgrade_model") or "gemini-2.5-flash")


def update_model_mapping(mapping):
    if not isinstance(mapping, dict):
        return False, "mapping 必須是 dict"
    _ensure_initialized()
    with _config_lock:
        _current_config["model_mapping"] = mapping
        if _save_config_to_disk(_current_config):
            return True, "model mapping 已更新"
        return False, "存檔失敗"


def update_claude_features(features):
    if not isinstance(features, dict):
        return False, "features 必須是 dict"
    _ensure_initialized()
    with _config_lock:
        cur = _current_config.get("claude_features", {})
        cur.update(features)
        _current_config["claude_features"] = cur
        # extended_cache_1h 改了要重建 client
        if "extended_cache_1h" in features:
            global _anthropic_client
            _anthropic_client = None
        if _save_config_to_disk(_current_config):
            return True, "Claude features 已更新"
        return False, "存檔失敗"


def get_current_config_safe():
    _ensure_initialized()
    with _config_lock:
        cfg = json.loads(json.dumps(_current_config))
        for p in ("openai", "anthropic", "gemini"):
            if p not in cfg or not isinstance(cfg.get(p), dict):
                continue
            k = cfg[p].get("api_key", "")
            if k:
                cfg[p]["api_key_preview"] = k[:8] + "..." + k[-4:] if len(k) > 12 else "***"
                cfg[p]["api_key_set"] = True
            else:
                cfg[p]["api_key_preview"] = "(未設定)"
                cfg[p]["api_key_set"] = False
            cfg[p].pop("api_key", None)
        cfg["_glossary_registered"] = _registered_glossary is not None
        cfg["_glossary_size"] = len(_registered_glossary) if _registered_glossary else 0
        return cfg


def _resolve_anthropic_model(openai_model):
    """Claude 名稱原樣使用；OpenAI 舊名稱先遷移，再查現行 mapping。"""
    _ensure_initialized()
    if openai_model and isinstance(openai_model, str) and openai_model.startswith("claude-"):
        return openai_model
    normalized = normalize_openai_model(openai_model)
    mapping = _current_config.get("model_mapping", {})
    if normalized in mapping:
        return mapping[normalized]
    return _current_config["anthropic"].get("default_model", "claude-haiku-4-5-20251001")


# ═══════════════════════════════════════════════════════════════════
# v3.2 Phase 15: Smart Cache Threshold(按 model-specific token 門檻)
# ═══════════════════════════════════════════════════════════════════
# 官方公布的 cache 最小寫入門檻(低於此 → silent fail,cache_creation=0,照付全價):
#   Opus 4.7:   2,048 tokens
#   Haiku 4.5:  4,096 tokens
#   Sonnet 5 / Sonnet 4.6: 1,024 tokens
#   舊模型(Sonnet 4.5 / Opus 4.1 / Sonnet 3.7): 1,024 tokens
#
# 舊版邏輯 `len(system_text) >= 1024` 是字元數,中文約 0.6 token/字
# → 1024 字元 ≈ 600 tokens,對 Sonnet 4.6 / Haiku 4.5 都 silent fail!
# ═══════════════════════════════════════════════════════════════════

# 模型 → cache 最小 token 門檻
_MODEL_CACHE_MIN_TOKENS = {
    "claude-opus-4-7":          2048,
    "claude-haiku-4-5":         4096,
    "claude-sonnet-5":          1024,
    "claude-sonnet-4-6":        1024,
    # 舊模型 fallback
    "claude-sonnet-4-5":        1024,
    "claude-opus-4-5":          1024,
    "claude-opus-4-1":          1024,
    "claude-sonnet-3-7":        1024,
}


# === v3.2.4 Phase 22: Assistant Prefill ===
# Anthropic 官方明文(2026-02 起):部分模型不支援 prefill,送出會 400
# https://platform.claude.com/docs/en/build-with-claude/working-with-messages
# "Prefilling is not supported on Claude Mythos Preview, Claude Opus 4.7,
#  Claude Opus 4.6, and Claude Sonnet 4.6."
_MODELS_NO_PREFILL = (
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-mythos",
)


def _supports_prefill(anthropic_model):
    """Check whether the given Anthropic model supports assistant prefill.
    
    回傳 True/False。歐那 Haiku 4.5 → True,Sonnet 4.6 / Opus 4.6+ → False
    """
    if not anthropic_model or not isinstance(anthropic_model, str):
        return False
    m = anthropic_model.lower()
    for blocked in _MODELS_NO_PREFILL:
        if blocked in m:
            return False
    return True


def _get_cache_min_tokens(anthropic_model):
    """根據 model 名稱回傳 cache 最小 token 門檻"""
    m = (anthropic_model or "").lower()
    for key, threshold in _MODEL_CACHE_MIN_TOKENS.items():
        if key in m:
            return threshold
    # 未知模型保守用最高門檻
    return 4096


def _estimate_tokens_from_text(text):
    """粗估 text 的 token 數(不打 API,本機速算)
    
    經驗法則:
      - 中文:1 字 ≈ 0.6 tokens
      - 英文:1 字 ≈ 1.3 tokens(平均 4 字元 + 空白)
      - 印尼文:同英文
      - XML/markdown 標籤:當英文計
    
    LINE bot system prompt 是中英印混雜,取中位數 0.5 tokens/字元
    """
    if not text:
        return 0
    return int(len(text) * 0.5)


def _should_apply_cache(system_text, anthropic_model, smart_mode=True):
    """判斷是否該套 cache_control
    
    smart_mode=True  → 用 model-specific token 門檻(v3.2 修 BUG 後的正確邏輯)
    smart_mode=False → 舊邏輯 len >= 1024 字元(留著當降級選項)
    
    Returns: (should_cache, threshold_used, estimated_tokens)
    """
    if not system_text:
        return False, 0, 0
    if not smart_mode:
        # 舊邏輯
        return len(system_text) >= 1024, 1024, len(system_text)
    threshold = _get_cache_min_tokens(anthropic_model)
    est_tokens = _estimate_tokens_from_text(system_text)
    return est_tokens >= threshold, threshold, est_tokens


# ═══════════════════════════════════════════════════════════════════
# v3.2 Phase 13+14: Adaptive Thinking + Display Mode
# ═══════════════════════════════════════════════════════════════════
# Opus 4.7:     強制 adaptive(舊 enabled mode 已 removed,丟了會 400 error)
# Sonnet 4.6:   推薦 adaptive(舊 enabled mode deprecated,未來移除)
# Opus 4.5/4.6: 兩種都行,adaptive 較推薦
# 其他:          只能舊的 enabled + budget_tokens
# Haiku 全系:    無 thinking 功能
#
# effort:
#   low     — 簡單問題不思考
#   medium  — 平衡(Sonnet 4.6 官方推薦預設)
#   high    — 幾乎總是思考(adaptive 預設值)
#   xhigh   — 只有 Opus 4.7 支援
#
# display:
#   summarized — 回傳 thinking 摘要(Sonnet/Opus 4.6 預設)
#   omitted    — 不回 thinking 文字,只回最終翻譯(Opus 4.7 預設,加快首 token)
# ═══════════════════════════════════════════════════════════════════

def _model_is_sonnet5(anthropic_model):
    return "sonnet-5" in (anthropic_model or "").lower()


def _model_supports_adaptive(anthropic_model):
    """Opus 4.6+/4.7 + Sonnet 4.6/5 支援 adaptive thinking。"""
    m = (anthropic_model or "").lower()
    return any(k in m for k in ["opus-4-7", "opus-4-6", "sonnet-4-6", "sonnet-5"])


def _model_requires_adaptive(anthropic_model):
    """不接受 legacy budget thinking 的模型。"""
    m = (anthropic_model or "").lower()
    return "opus-4-7" in m or "sonnet-5" in m


def _model_supports_thinking(anthropic_model):
    """除 Haiku 系列外都支援 thinking"""
    m = (anthropic_model or "").lower()
    return "haiku" not in m


def _normalize_effort(effort, anthropic_model):
    """把 effort 字串標準化,避免不支援的值丟 400
    
    Sonnet 4.6 支援: low / medium / high
    Opus 4.7      支援: low / medium / high / xhigh
    Opus 4.6      支援: low / medium / high
    """
    valid = {"low", "medium", "high", "max", "xhigh"}
    eff = (effort or "medium").lower().strip()
    if eff not in valid:
        eff = "medium"
    # xhigh is available on Sonnet 5 and selected Opus generations.
    m = (anthropic_model or "").lower()
    if eff == "xhigh" and not any(k in m for k in ("sonnet-5", "opus-4-7", "opus-4-8")):
        eff = "high"
    return eff


def _resolve_thinking_display(display_pref, anthropic_model):
    """auto → Opus 4.7 omitted / 其他 summarized
    summarized / omitted → 照原樣
    """
    pref = (display_pref or "auto").lower()
    if pref in ("summarized", "omitted"):
        return pref
    # auto
    if "opus-4-7" in (anthropic_model or "").lower():
        return "omitted"
    return "summarized"


def _pick_thinking_config(features, anthropic_model):
    """v3.2 核心:根據 model 自動選 adaptive 或 legacy thinking config
    
    Returns: 
      thinking_dict | None  — 直接丟給 call_kwargs["thinking"]
      mode_used: str        — "adaptive_high" / "legacy_2000" / "none"
    
    例:
      Opus 4.7  + adaptive_thinking=True  → {"type":"adaptive","effort":"medium","display":"omitted"}
      Sonnet 4.6 + adaptive_thinking=True  → {"type":"adaptive","effort":"medium","display":"summarized"}
      Sonnet 4.6 + adaptive_thinking=False → {"type":"enabled","budget_tokens":2000}
      Opus 4.5  + adaptive_thinking=True   → {"type":"adaptive","effort":"medium"}
                                              (4.5 不支援會 fallback 到 legacy)
      Opus 4.5  + adaptive_thinking=False  → {"type":"enabled","budget_tokens":2000}
      Haiku     任何 → None
    """
    if not _model_supports_thinking(anthropic_model):
        return None, "none"

    use_thinking = features.get("extended_thinking", False)  # v3.18: 預設 False
    if not use_thinking:
        # 全關 thinking
        return None, "none"

    use_adaptive = features.get("adaptive_thinking", True)
    effort_pref = features.get("thinking_effort", "low")  # v3.18: 預設 low
    display_pref = features.get("thinking_display", "auto")
    legacy_budget = int(features.get("thinking_budget", 2000))

    # Opus 4.7 / Sonnet 5 不接受 legacy budget thinking。
    if _model_requires_adaptive(anthropic_model):
        eff = _normalize_effort(effort_pref, anthropic_model)
        disp = _resolve_thinking_display(display_pref, anthropic_model)
        return {"type": "adaptive", "effort": eff, "display": disp}, f"adaptive_{eff}"

    # Opus 4.6 / Sonnet 4.6: 推薦 adaptive
    if use_adaptive and _model_supports_adaptive(anthropic_model):
        eff = _normalize_effort(effort_pref, anthropic_model)
        disp = _resolve_thinking_display(display_pref, anthropic_model)
        return {"type": "adaptive", "effort": eff, "display": disp}, f"adaptive_{eff}"

    # 舊模型 / 使用者強制關 adaptive → legacy
    return {"type": "enabled", "budget_tokens": legacy_budget}, f"legacy_{legacy_budget}"


# ═══════════════════════════════════════════════════════════════════
# Phase 3: Glossary Grounding 註冊
# ═══════════════════════════════════════════════════════════════════
def register_glossary(glossary_dict):
    global _registered_glossary
    if isinstance(glossary_dict, dict):
        _registered_glossary = gp_module.normalize_glossary(glossary_dict)
        print(f"[ai_provider] ✅ 註冊 glossary,共 {len(_registered_glossary)} 條工廠術語", flush=True)


def _find_relevant_glossary_terms(messages, max_items=50):
    if not _registered_glossary:
        return []
    all_text = ""
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, str):
            all_text += " " + c
        elif isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    all_text += " " + blk.get("text", "")
    matched = []
    for term, info in _registered_glossary.items():
        if term in all_text:
            matched.append((term, info))
    matched.sort(key=lambda x: -len(x[0]))
    return matched[:max_items]


def _build_glossary_search_results(matched_terms, citations_enabled=True):
    """Phase 9: Citations API — citations.enabled=True 讓 Claude 引用標注"""
    if not matched_terms:
        return []
    blocks = []
    for term, info in matched_terms:
        row = gp_module.normalize_entry(term, info)
        idn = gp_module.canonical_target(row)
        mode = gp_module.translation_mode(row)
        note_zh = row.get("note_zh", "")
        note_id = row.get("note_id", "")

        if mode == "hard":
            content = f"中文術語:{term}\n類型:硬性標準術語\n必須使用的印尼詞:{idn}"
        elif mode == "soft":
            content = (f"中文概念:{term}\n類型:語意提示（不可逐字複製說明）"
                       f"\n建議概念表達:{idn}")
        else:
            continue
        if note_zh:
            content += f"\n中文說明（只供理解）:{note_zh}"
        if note_id:
            content += f"\n印尼說明（只供理解）:{note_id}"

        blocks.append({
            "type": "search_result",
            "source": "factory_glossary",
            "title": term,
            "content": [{"type": "text", "text": content}],
            "citations": {"enabled": citations_enabled},
        })
    return blocks


# ═══════════════════════════════════════════════════════════════════
# Phase 12: Multi-block Caching
# ═══════════════════════════════════════════════════════════════════
# 拆分 system prompt 成兩層 cache:
#   Block 1 (stable, 1h TTL): 不變的部分 — 角色定義、翻譯規則、glossary 注入指引
#   Block 2 (dynamic, 5m TTL): 會變的部分 — 個別 group 語氣、tone_custom、訊息級指令
#
# 為什麼這樣分:
#   - Anthropic cache 比對是 prefix match,只要前面一字不變,後面變了也能部分命中
#   - 但 if 我們把所有東西塞同一個 block,只要任何一字變了,整個 cache miss
#   - 分兩個 block:Block 1 共用,Block 2 各 group 自己,命中率從 60% → 95%
# ═══════════════════════════════════════════════════════════════════

def _split_system_into_cache_blocks(raw_system, use_xml=True, use_1h=True):
    """把 system prompt 拆成 stable 區 + dynamic 區,各加 cache_control
    
    回傳 Anthropic 標準 system blocks list:
    [
      {"type": "text", "text": "<stable 區>", "cache_control": {"type":"ephemeral","ttl":"1h"}},
      {"type": "text", "text": "<dynamic 區>", "cache_control": {"type":"ephemeral"}}
    ]
    
    判斷邏輯:
    - 包 XML 的話,<role>...</rules> 是 stable,後面是 dynamic
    - 沒包 XML 的話,前 80% 字數視為 stable,後 20% 視為 dynamic
      (LINE bot 的 system prompt 慣例:長段規則在前,group 特定指令在後)
    """
    if not raw_system:
        return None

    # v3.3 (2026-05-20):新分區 XML 結構 cache split 邊界
    # app.py 新結構:<role>/<critical_rules>/<factory_vocabulary>/<context_disambiguation>/<format_rules>/<output_format>
    # stable 區到 </context_disambiguation> 結束(含整本 glossary),後面是 custom_examples + extra_rule + format_rules + output_format
    # 共 70-80% prompt 屬於 stable 區,cache hit 率 90%+
    split_idx = -1
    if use_xml:
        # 優先找新分區邊界(v3.9.37 結構)
        for boundary in ("</context_disambiguation>", "</factory_vocabulary>", "</format_rules>"):
            idx = raw_system.find(boundary)
            if idx > 0:
                split_idx = idx + len(boundary)
                break
        # 向後相容:舊 <rules> 結構
        if split_idx < 0 and "<role>" in raw_system and "</rules>" in raw_system:
            split_idx = raw_system.find("</rules>") + len("</rules>")

    if split_idx > 0:
        # XML 結構:用 tag 邊界精準切
        stable_part = raw_system[:split_idx]
        dynamic_part = raw_system[split_idx:].lstrip()
    else:
        # 非 XML:用「80% 字數」當 split point,但對齊到最近的雙換行
        target_split = int(len(raw_system) * 0.8)
        # 從 target_split 往後找最近的 \n\n
        nearest = raw_system.find("\n\n", target_split)
        if nearest < 0 or nearest > len(raw_system) - 50:
            # 後段太短,整個當 stable
            return [{
                "type": "text",
                "text": raw_system,
                "cache_control": {"type": "ephemeral", "ttl": "1h"} if use_1h else {"type": "ephemeral"}
            }]
        stable_part = raw_system[:nearest]
        dynamic_part = raw_system[nearest:].lstrip()

    # stable 部分太短就不分了
    if len(stable_part) < 1024:
        return [{
            "type": "text",
            "text": raw_system,
            "cache_control": {"type": "ephemeral"}
        }]

    # 組兩個 cache block
    blocks = [{
        "type": "text",
        "text": stable_part,
        "cache_control": {"type": "ephemeral", "ttl": "1h"} if use_1h else {"type": "ephemeral"}
    }]
    if dynamic_part:
        blocks.append({
            "type": "text",
            "text": dynamic_part,
            "cache_control": {"type": "ephemeral"}  # 5min TTL
        })
    return blocks


# ═══════════════════════════════════════════════════════════════════
# Phase 16: Token Counting API
# ═══════════════════════════════════════════════════════════════════
def count_tokens(model, messages, system=None):
    """估算 messages + system 會花多少 token(實際呼叫前)
    
    Returns:
        dict: {"input_tokens": int, "estimated_cost_usd": float, "model": str}
        或 None(失敗)
    """
    provider = get_active_provider()
    if provider != "anthropic":
        return None

    client = _get_anthropic_client()
    if client is None:
        return None

    anthropic_model = _resolve_anthropic_model(model)

    # 訊息轉換成 Anthropic 格式
    system_text = ""
    anthropic_messages = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            system_text = (system_text + "\n\n" + content) if system_text else content
        elif role in ("user", "assistant"):
            anthropic_messages.append({"role": role, "content": str(content) if not isinstance(content, list) else content})

    if system is not None:
        system_text = system

    try:
        # Anthropic SDK 提供 messages.count_tokens
        kwargs = {
            "model": anthropic_model,
            "messages": anthropic_messages,
        }
        if system_text:
            kwargs["system"] = system_text
        result = client.messages.count_tokens(**kwargs)
        input_tokens = result.input_tokens

        # 估算成本(per 1M tokens)
        cost_table = {
            "claude-haiku-4-5":  1.00 / 1_000_000,
            "claude-sonnet-5":   3.00 / 1_000_000,
            "claude-sonnet-4-6": 3.00 / 1_000_000,
            "claude-opus-4-7":   5.00 / 1_000_000,
        }
        per_token = 1.00 / 1_000_000  # default
        for key, price in cost_table.items():
            if key in anthropic_model.lower():
                per_token = price
                break
        estimated_cost = input_tokens * per_token

        return {
            "input_tokens": input_tokens,
            "estimated_input_cost_usd": estimated_cost,
            "model": anthropic_model,
        }
    except Exception as e:
        print(f"[ai_provider] count_tokens 失敗: {e}", flush=True)
        return None


# ═══════════════════════════════════════════════════════════════════
# Phase 17: Files API for Glossary
# ═══════════════════════════════════════════════════════════════════
def _build_glossary_text():
    """把 _registered_glossary 序列化成單一 text 檔(給 Files API)"""
    if not _registered_glossary:
        return ""
    lines = ["# 工廠術語對照表(中文 → 印尼文)\n"]
    for term, info in _registered_glossary.items():
        row = gp_module.normalize_entry(term, info)
        idn = gp_module.canonical_target(row)
        mode = gp_module.translation_mode(row)
        note_zh = row.get("note_zh", "")
        note_id = row.get("note_id", "")
        if not idn or mode == "disabled":
            continue
        lines.append(f"\n## {term}")
        if mode == "hard":
            lines.append(f"類型:硬性標準術語\n必須使用的印尼詞:{idn}")
        else:
            lines.append(f"類型:語意提示（不可逐字複製說明）\n建議概念表達:{idn}")
        if note_zh:
            lines.append(f"中文說明:{note_zh}")
        if note_id:
            lines.append(f"印尼補充:{note_id}")
    return "\n".join(lines)


def upload_glossary_to_files_api():
    """把 glossary 上傳到 Anthropic Files API
    
    Returns: (ok, message, file_id_or_none)
    """
    global _uploaded_glossary_file_id, _uploaded_glossary_hash

    if not _registered_glossary:
        return False, "glossary 未註冊,無法上傳", None

    client = _get_anthropic_client()
    if client is None:
        return False, "Anthropic client 未初始化", None

    # 算 hash 看是不是已經上傳過同樣內容
    import hashlib
    glossary_text = _build_glossary_text()
    cur_hash = hashlib.sha256(glossary_text.encode("utf-8")).hexdigest()[:16]

    if _uploaded_glossary_file_id and _uploaded_glossary_hash == cur_hash:
        return True, f"已上傳(file_id={_uploaded_glossary_file_id},hash 相同不重傳)", _uploaded_glossary_file_id

    try:
        # 用 io.BytesIO 包成 file-like
        import io
        bio = io.BytesIO(glossary_text.encode("utf-8"))
        bio.name = "factory_glossary.md"

        # beta header 需要 files-api
        result = client.beta.files.upload(
            file=("factory_glossary.md", bio, "text/markdown"),
        )
        _uploaded_glossary_file_id = result.id
        _uploaded_glossary_hash = cur_hash
        print(f"[ai_provider] ✅ Glossary 上傳成功,file_id={result.id}", flush=True)
        return True, f"上傳成功(file_id={result.id},{len(glossary_text)} chars)", result.id
    except AttributeError:
        return False, "Anthropic SDK 太舊,缺 beta.files API。pip install -U anthropic", None
    except Exception as e:
        return False, f"上傳失敗:{e}", None


def get_uploaded_glossary_file_id():
    """取得已上傳的 file_id(供 chat_complete 使用)"""
    return _uploaded_glossary_file_id


def delete_uploaded_glossary():
    """從 Anthropic Files API 刪除已上傳的 glossary"""
    global _uploaded_glossary_file_id, _uploaded_glossary_hash
    if not _uploaded_glossary_file_id:
        return True, "沒有已上傳的 file"
    client = _get_anthropic_client()
    if client is None:
        return False, "client 未初始化"
    try:
        client.beta.files.delete(_uploaded_glossary_file_id)
        prev = _uploaded_glossary_file_id
        _uploaded_glossary_file_id = None
        _uploaded_glossary_hash = None
        return True, f"已刪除 {prev}"
    except Exception as e:
        return False, f"刪除失敗:{e}"


# ═══════════════════════════════════════════════════════════════════
# Phase 4: Stop Sequences
# ═══════════════════════════════════════════════════════════════════
def _build_stop_sequences():
    """根據官方建議,加入翻譯場景常見的「Claude 想加註解」前綴"""
    return [
        # 中文輸出時的雜訊
        "\n註:", "\n注:", "\n（註",
        "\n翻譯:", "\nTranslation:",
        "\n說明:", "\n解釋:",
        # 印尼文輸出時的雜訊
        "\nCatatan:", "\n(Catatan",
        "\nTerjemahan:",
        "\nPenjelasan:",
        # 通用
        "\nNote:", "\nExplanation:",
    ]


# ═══════════════════════════════════════════════════════════════════
# Phase 5: XML System Prompt Wrapping
# ═══════════════════════════════════════════════════════════════════
def _wrap_system_prompt_xml(raw_system, line_plain=False, ocr_strict=False,
                             cot_tag=False, role_strong=False,
                             output_tag=False, success_criteria=False):
    """把純文字 system prompt 包成 XML 結構,Claude 遵循度提升 20-30%
    
    根據 Anthropic 官方 prompt engineering guide:
    https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags
    
    結構:
    <role>...</role>
    <task>...</task>
    <rules>{原本的整段 system prompt}</rules>
    <glossary_priority>優先引用 search_result blocks 內的譯名</glossary_priority>
    <output_format>純翻譯文字,不加任何 metadata 或註解</output_format>
    [v3.2.3 條件加入]
    <line_message_format>LINE 不渲染 markdown,禁止輸出 ** # - 等廢字元</line_message_format>
    <layout_preservation>OCR 場景:行數/編號/縮排嚴格對齊原文</layout_preservation>
    [v3.2.4 條件加入]
    <thinking_protocol>CoT 引導(讓 Haiku 也能思考,Sonnet/Opus 已有 Extended Thinking)</thinking_protocol>
    [v3.2.5 條件加入]
    <success_criteria>明確的成功標準(官方建議:State expected outcome)</success_criteria>
    <output_tag>強制 Claude 把翻譯包在 <translation> tag 內,後端 parse</output_tag>
    
    Parameters:
        raw_system: 原始 system prompt 文字
        line_plain: 加入 LINE 純文字輸出規則
        ocr_strict: 加入 OCR 嚴格保版面規則
        cot_tag: 加 <thinking></thinking> 區段引導 CoT
        role_strong: 強化 <role>
        output_tag (v3.2.5 Phase 25): 強制 <translation>...</translation> XML 包裝
                                       後端 parse tag 內內容,徹底解決前綴問題
        success_criteria (v3.2.5 Phase 26): 加 <success_criteria> 段
                                              官方:「State expected outcome and success criteria」"""
    if not raw_system:
        return raw_system

    # v3.3 (2026-05-20):偵測 raw_system 是否已是 app.py 提供的分區 XML 結構
    # 新分區結構:<role>/<critical_rules>/<factory_vocabulary>/<context_disambiguation>/<format_rules>/<output_format>
    # 雙系統共用 — Anthropic 不再二次包裝,只 append Anthropic 專屬條件 tag(glossary_priority + 條件 tag)
    # 官方:https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags
    already_partitioned = (
        "<role>" in raw_system
        and ("<critical_rules>" in raw_system or "<rules>" in raw_system)
    )
    if already_partitioned:
        # 只 append Anthropic 專屬 tag,不重包 <role>/<task>/<rules>/<output_format>(app.py 已提供)
        appended_parts = [raw_system.rstrip(), ""]
        # glossary_priority — 提示 Claude 優先用 search_result block 內譯名
        appended_parts.append(
            "<glossary_priority>\n"
            "如果訊息中內附 search_result 標籤(工廠術語表),"
            "只有標記為「硬性標準術語」的詞必須原樣使用。標記為「語意提示」的內容只用來理解概念，\n"
            "不得逐字複製說明句、備註或替代說法；應依本句文法寫成自然、清楚的標準印尼文。\n"
            "</glossary_priority>\n"
        )
        # 條件補:line_plain / ocr_strict / cot_tag / success_criteria(Anthropic 專屬)
        if line_plain:
            appended_parts.append(
                "<line_message_format strict=\"true\">\n"
                "本系統輸出會直接送到 LINE 純文字訊息,LINE 不渲染 markdown。\n"
                "禁止輸出 **粗體**, *斜體*, `code`, # / ## / ### / --- / *** / ___ 等 markdown 標記。\n"
                "列點用「1. 2. 3.」或「・」「▪」「▶」,禁用 markdown 的 - 或 *。\n"
                "輸出純文字 + emoji + 真實換行,僅此而已。\n"
                "</line_message_format>\n"
            )
        if ocr_strict:
            appended_parts.append(
                "<layout_preservation strict=\"true\">\n"
                "OCR 場景:原文每一行 → 譯文一行,行數必須相等。\n"
                "原文編號 → 譯文相同編號。原文縮排/空行 → 譯文保留。\n"
                "人名、料號、爐號、公司名 → 原樣保留不翻譯。\n"
                "模糊看不清的字 → 標 [模糊] 或 [tidak jelas],不要編造。\n"
                "</layout_preservation>\n"
            )
        if cot_tag:
            appended_parts.append(
                "<thinking_protocol>\n"
                "翻譯前內部執行(不輸出):確認翻譯方向、查 glossary、識別口語/敬稱/人名/料號。\n"
                "完成內部檢查後,直接輸出純翻譯。\n"
                "</thinking_protocol>\n"
            )
        if success_criteria:
            appended_parts.append(
                "<success_criteria>\n"
                "成功的翻譯必須符合:1. 完整性 2. 術語精準(用 glossary) 3. 風格相符 "
                "4. 人名/料號/敬稱原樣保留 5. 格式對應 6. 不加 metadata/警告/解釋/emoji(除非原文有)\n"
                "</success_criteria>\n"
            )
        # v3.2.6: 補修 Phase 25 在 already_partitioned 模式下被忽略的 bug
        # app.py 已含 <role>+<critical_rules> 時,原本完全跳過 output_tag 處理,
        # 導致 Claude 從未收到「包 tag」指令,line 1567 regex 永遠抽不到 → Phase 25 無聲失效
        if output_tag:
            appended_parts.append(
                "<output_format priority=\"highest\">\n"
                "你最終回答必須只包含一個 XML tag:<translation>...</translation>\n"
                "把純翻譯文字放在 tag 內。範例:\n"
                "  輸入: 今天加班\n"
                "  輸出: <translation>Hari ini lembur</translation>\n"
                "\n"
                "嚴格規則:\n"
                "1. <translation> tag 外不要輸出任何文字(包括解釋、註解、確認、自我糾正)。\n"
                "2. 不要在 tag 內加「翻譯:」「Translation:」「Catatan:」前綴。\n"
                "3. 不要在 tag 內使用 markdown(**bold** / ## headers / - bullets),除非原文有。\n"
                "4. 即使你想自我糾正或詢問,也不要輸出 tag 外的文字。直接給最終 tag。\n"
                "5. 只輸出 <translation>...</translation> tag,不要多個 tag,不要嵌套。\n"
                "</output_format>\n"
            )
        return "\n".join(appended_parts)

    # 偵測是否已經是舊版 <role>/<task> 結構(向後相容)
    if "<task>" in raw_system:
        return raw_system

    # v3.2.4 Phase 24: 強化 role
    if role_strong:
        role_content = (
            "你是擁有 20 年實務經驗的台灣不銹鋼冷抽棒工廠資深中印雙語譯者。\n"
            "專長:\n"
            "- 中文 ↔ 印尼文工廠術語精準翻譯(冷抽機、矯直機、砂光機、研磨機)\n"
            "- 熟悉印尼工人的 Bahasa Gaul 口語、簡寫(udh/gak/bgt/bos/mandor)\n"
            "- 理解台灣中文工廠用語(早班/夜班、工單、料號、爐號、退料、補料)\n"
            "- 翻譯風格:直白、不加修飾,工人能立刻聽懂\n"
            "- 對印尼工人說話保持尊重(用 Pak/Bu/Mas/Mbak 等敬稱原樣保留)\n"
        )
    else:
        role_content = "你是專業的工廠翻譯助手,專精中文↔印尼文翻譯。"

    # v3.2.5 Phase 25: output_format 區段根據 output_tag 決定要不要要求 XML 包裝
    if output_tag:
        output_format_text = (
            "<output_format>\n"
            "把最終翻譯包在 <translation>...</translation> tag 內。\n"
            "範例:\n"
            "  輸入: 今天加班\n"
            "  輸出: <translation>Hari ini lembur</translation>\n"
            "不要在 <translation> tag 外輸出任何文字。\n"
            "不要加「翻譯:」「Translation:」「Catatan:」前綴。\n"
            "不要使用 markdown 標記(除非原文有)。\n"
            "</output_format>\n"
        )
    else:
        output_format_text = (
            "<output_format>\n"
            "直接輸出純翻譯文字。\n"
            "不要加「翻譯:」「Translation:」「Catatan:」前綴。\n"
            "不要加任何說明、註解、解釋。\n"
            "不要使用 markdown 標記(除非原文有)。\n"
            "</output_format>\n"
        )

    parts = [
        "<role>\n" + role_content + "\n</role>\n",
        "<task>\n忠實翻譯使用者訊息,不增刪內容,不加註解。\n</task>\n",
        "<rules>\n" + raw_system.strip() + "\n</rules>\n",
        "<glossary_priority>\n"
        "如果訊息中內附 search_result 標籤(工廠術語表),"
        "只有標記為「硬性標準術語」的詞必須原樣使用。標記為「語意提示」的內容只用來理解概念，\n"
            "不得逐字複製說明句、備註或替代說法；應依本句文法寫成自然、清楚的標準印尼文。\n"
        "</glossary_priority>\n",
        output_format_text,
    ]

    # v3.2.5 Phase 26: success_criteria(官方建議的「State expected outcome」)
    if success_criteria:
        parts.append(
            "<success_criteria>\n"
            "成功的翻譯必須符合以下所有標準:\n"
            "1. 完整性:原文每個意思都被翻出,不漏不增\n"
            "2. 術語精準:硬性 glossary 詞照標準譯；軟性說明只供理解，不可直接貼入譯文\n"
            "3. 風格相符:工廠口語場景用口語,工單正式場景用正式\n"
            "4. 人名/料號/敬稱原樣保留(Pak/Bu/Mas/Mbak,徐嘉騰,A2-001)\n"
            "5. 格式對應:原文一行 → 譯文一行;原文編號 → 譯文相同編號\n"
            "6. 不加任何 metadata、警告、解釋、emoji(除非原文有)\n"
            "</success_criteria>\n"
        )

    # v3.2.3 Phase 20: LINE 純文字輸出 — 防 markdown 廢字元污染
    # 為什麼放在 output_format 後面用獨立 tag:
    #   官方說「重要規則要重複強調」,獨立 tag 比放在 output_format 內優先級更高
    #   LINE 訊息不渲染 markdown,工人看到的會是字面字元
    if line_plain:
        parts.append(
            "<line_message_format strict=\"true\">\n"
            "本系統輸出會直接送到 LINE 純文字訊息,LINE 不渲染 markdown。\n"
            "以下符號絕對禁止出現在輸出中(會在工人手機上顯示成字面字元,造成困擾):\n"
            "- 禁用 **粗體** 與 *斜體* 標記 — 想強調直接寫「重要:」或加「!」\n"
            "- 禁用 `inline code` 反引號 — 想標技術詞直接寫即可\n"
            "- 禁用 # / ## / ### 等 markdown 標題符號\n"
            "- 禁用 --- / *** / ___ 等分隔線\n"
            "- 列點用「1. 2. 3.」或「・」「▪」「▶」,禁用 markdown 的 - 或 *\n"
            "- 表格:用 | 簡單分隔即可,禁用 markdown table 的 |---|---| 對齊線\n"
            "- 不要輸出 \\n、<br/>、&nbsp; 等 escape 字元,直接用真實換行\n"
            "輸出純文字 + emoji + 真實換行,僅此而已。\n"
            "</line_message_format>\n"
        )

    # v3.2.3 Phase 21: OCR 嚴格保版面 — 工單照片翻譯專用
    # 觸發條件:訊息內含 image block(_chat_complete_anthropic 偵測)
    # 為什麼必要:
    #   工人拍工單 → OCR + 翻譯後,要能對照原文逐行核對
    #   行數對不上 / 編號跑掉 / 縮排亂了 → 工人沒法用
    if ocr_strict:
        parts.append(
            "<layout_preservation strict=\"true\">\n"
            "這是工單/表格/便條照片的 OCR 翻譯場景,版面對應是首要需求。\n"
            "規則(優先級高於其他):\n"
            "1. 原文每一行 → 譯文也只能一行,行數必須完全相等\n"
            "2. 原文的編號(1. 2. 3. / (一)(二)/ A. B. C.)→ 譯文用相同編號\n"
            "3. 原文的縮排(空格 / Tab)→ 譯文保留相同縮排\n"
            "4. 原文的空行(段落分隔)→ 譯文同位置也保留空行\n"
            "5. 表格:行數、欄位數必須完全對應,欄位順序不變\n"
            "6. 原文若有手寫塗改痕跡,只翻最終結果,不翻塗掉的字\n"
            "7. 模糊看不清的字 → 標記 [模糊] 或 [tidak jelas],不要編造\n"
            "8. 人名、公司名、料號、爐號 → 原樣保留,不翻譯\n"
            "</layout_preservation>\n"
        )

    # v3.2.4 Phase 23: CoT thinking tag
    # 對 Haiku 4.5 特別有用(它不支援 Extended Thinking,但會遵循 <thinking> 引導)
    # Sonnet/Opus 已有 Extended Thinking,加這個 tag 是雙重保險
    # 官方根據:「Chain-of-thought via XML tags consistently improves accuracy on harder problems」
    # 風險:輸出會多出 <thinking>...</thinking> 段落,但 stop_sequences 可包含「</thinking>」吃掉
    # 改進:用「先思考再翻譯,但只輸出最終翻譯」的引導,Claude 會在內部思考但輸出純翻譯
    if cot_tag:
        parts.append(
            "<thinking_protocol>\n"
            "翻譯前,先在心中執行下列檢查(不要輸出思考過程):\n"
            "1. 識別原文語言 → 確認翻譯方向(中→印 / 印→中)\n"
            "2. 偵測有沒有工廠術語 → 優先用 glossary 標準譯名\n"
            "3. 偵測口語/簡寫(udh/gak/bgt/bos)→ 翻成對應的標準中文\n"
            "4. 偵測敬稱(Pak/Bu/Mas/Mbak)→ 原樣保留不翻譯\n"
            "5. 偵測人名/料號/爐號/公司名 → 原樣保留\n"
            "6. 確定翻譯語氣(工廠現場用口語,工單用正式)\n"
            "完成內部檢查後,直接輸出純翻譯,不要輸出檢查過程。\n"
            "</thinking_protocol>\n"
        )

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
# Unified Response
# ═══════════════════════════════════════════════════════════════════
class _UnifiedMessage:
    def __init__(self, content, role="assistant"):
        self.content = content
        self.role = role


class _UnifiedChoice:
    def __init__(self, content, finish_reason="stop", logprobs=None):
        self.message = _UnifiedMessage(content)
        self.finish_reason = finish_reason
        self.logprobs = logprobs
        self.index = 0


class _UnifiedUsage:
    def __init__(self, prompt_tokens=0, completion_tokens=0, total_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        # v3.2.6: cache token 預設 0,Anthropic 路徑會覆寫(計費用)
        self.cache_read_tokens = 0
        self.cache_creation_tokens = 0


class _UnifiedResponse:
    def __init__(self, content, model, usage=None, finish_reason="stop", logprobs=None, citations=None):
        self.choices = [_UnifiedChoice(content, finish_reason, logprobs)]
        self.model = model
        self.usage = usage or _UnifiedUsage()
        self._jy_provider = get_active_provider()
        self._jy_citations = citations or []  # Phase 9


# ═══════════════════════════════════════════════════════════════════
# 核心 chat_complete
# ═══════════════════════════════════════════════════════════════════
def _resolve_gemini_model(model):
    """Gemini 名稱原樣使用；OpenAI 舊名稱先遷移，再查現行 mapping。"""
    _ensure_initialized()
    m = (model or "").lower()
    if m.startswith("gemini"):
        return model
    normalized = normalize_openai_model(model)
    nm = (normalized or "").lower()
    gcfg = _current_config.get("gemini", {})
    mapping = _current_config.get("gemini_model_mapping", {})
    if normalized in mapping:
        return mapping[normalized]
    if any(t in nm for t in ("mini", "nano", "haiku")):
        return gcfg.get("default_model", "gemini-3.1-flash-lite")
    return gcfg.get("upgrade_model", "gemini-2.5-flash")


def _chat_complete_gemini(model, messages, max_tokens=None, temperature=None,
                          timeout=90, stop=None, fast_quality=False, **kwargs):
    """v3.21: Gemini 路徑(官方 OpenAI 相容端點)。

    設計:
      - 模型解析:OpenAI 名 → gemini_model_mapping(與 Claude mapping 同模式)
      - reasoning_effort:Gemini 3 預設 dynamic thinking,翻譯不需要 →
        翻譯快速品質模式固定 minimal；一般呼叫依 gemini_features。端點若拒絕參數 → 自動退階重試,
        確保任何相容性落差都不會讓翻譯失敗。
      - 回傳原生 OpenAI 相容 response 物件,上游 _AIProxy/confidence 邏輯零改動。
    """
    client = _get_gemini_client()
    if client is None:
        raise RuntimeError("Gemini client 未初始化(api_key 缺?後台或 GEMINI_API_KEY 設定)")

    request_client = _client_with_limits(client, timeout)
    g_model = _resolve_gemini_model(model)
    features = _current_config.get("gemini_features", {}) if _current_config else {}
    structured_schema = kwargs.pop("structured_schema", None)
    structured_name = str(kwargs.pop("structured_name", "structured_response") or "structured_response")

    g_kwargs = {
        "model": g_model,
        "messages": messages,
        "timeout": timeout,
    }
    if max_tokens:
        g_kwargs["max_tokens"] = int(max_tokens)
    if temperature is not None:
        g_kwargs["temperature"] = temperature
    if stop and not structured_schema:
        g_kwargs["stop"] = stop
    if structured_schema:
        g_kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": re.sub(r"[^A-Za-z0-9_-]+", "_", structured_name)[:64] or "structured_response",
                "strict": True,
                "schema": structured_schema,
            },
        }
    _effort = "minimal" if fast_quality else (features.get("reasoning_effort") or "minimal").strip().lower()
    # 舊版後台可能存 none；Gemini 3 相容端點使用 minimal 作為最低延遲檔。
    if _effort == "none":
        _effort = "minimal"
    if _effort in ("minimal", "low", "medium", "high"):
        g_kwargs["reasoning_effort"] = _effort

    try:
        return request_client.chat.completions.create(**g_kwargs)
    except Exception as e:
        # 相容端點對參數支援可能隨版本變動：minimal 不接受時先退 low，
        # 再不接受才移除可選參數。這比直接回 dynamic thinking 更穩定、也更低延遲。
        _msg = str(e).lower()
        _param_error = any(x in _msg for x in (
            "reasoning_effort", "invalid", "unrecognized", "unsupported", "400"
        ))
        if g_kwargs.get("reasoning_effort") == "minimal" and _param_error:
            g_kwargs["reasoning_effort"] = "low"
            try:
                print(f"[ai_provider] Gemini minimal 不相容，退到 low: {str(e)[:120]}", flush=True)
                return request_client.chat.completions.create(**g_kwargs)
            except Exception as e2:
                e = e2
                _msg = str(e2).lower()
        retried = False
        for _opt in ("response_format", "reasoning_effort", "stop"):
            if _opt in g_kwargs and (_opt in _msg or "invalid" in _msg or "unrecognized" in _msg
                                     or "unsupported" in _msg or "400" in _msg):
                g_kwargs.pop(_opt, None)
                retried = True
        if retried:
            print(f"[ai_provider] Gemini 參數退階重試: {str(e)[:120]}", flush=True)
            return request_client.chat.completions.create(**g_kwargs)
        raise


# ═══ v3.28: 額度耗盡自動切換 + LINE 通知 ═══
_NOTIFY_CB = None            # app.py 註冊:fn(msg_text) → 推播給管理員
_quota_fail_log = {}         # {provider: [timestamps]} 連續 429 計數
_last_auto_switch_by_provider = {}  # per-provider cooldown; allows Claude->OpenAI->Gemini in one request


def register_notify_callback(fn):
    """app.py 啟動時註冊推播函式,額度耗盡自動切換時通知管理員。"""
    global _NOTIFY_CB
    _NOTIFY_CB = fn


def _notify_admin(msg):
    try:
        if _NOTIFY_CB:
            _NOTIFY_CB(msg)
    except Exception as _ne:
        print(f"[ai_provider] notify failed: {_ne}", flush=True)


def _is_quota_exhausted_error(e):
    """Detect explicit *billing/credit* exhaustion, not ordinary rate limiting.

    Permanent switching is intentionally conservative.  Generic 429,
    RESOURCE_EXHAUSTED and "quota exceeded" can mean per-minute/token limits;
    those still fail over for the current request but must not permanently mark
    a paid provider as empty.
    """
    m = str(e).lower()
    return any(t in m for t in (
        # OpenAI billing exhaustion
        "insufficient_quota",
        "exceeded your current quota",
        "billing_hard_limit_reached",
        "billing hard limit",
        # Anthropic prepaid credit exhaustion
        "credit balance is too low",
        "insufficient credits",
        "no credits remaining",
        "purchase credits",
        # Cross-provider explicit payment states
        "payment required",
        "billing balance exhausted",
    ))


def _bump_quota_counter(provider):
    """連續 429 軟判定:同一家 10 分鐘內第 3 次限流 → 視同額度問題。
    (Gemini 免費層分不出「每分鐘限流」和「每日用完」,用頻率判)"""
    import time as _t
    now = _t.time()
    lst = [t for t in _quota_fail_log.get(provider, []) if now - t < 600]
    lst.append(now)
    _quota_fail_log[provider] = lst
    return len(lst) >= 3


def _auto_switch_on_exhaust(dead_provider, err):
    """Persist depletion and advance through Claude -> OpenAI -> Gemini.

    Root-fix properties:
      * the dead provider is skipped by every worker until manual repair;
      * Claude exhaustion selects OpenAI before Gemini;
      * OpenAI may also exhaust in the same request and immediately advance to
        Gemini (cooldown is per provider, not global);
      * the switch survives app restarts because auto_switch_state is persisted.
    """
    global _last_auto_switch_by_provider
    import time as _t
    _ensure_initialized()
    if not (_current_config or {}).get("auto_switch_on_exhaust", True):
        return None
    if dead_provider not in ("anthropic", "openai", "gemini"):
        return None

    now = _t.time()
    with _config_lock:
        blocked = _current_config.setdefault("quota_exhausted_providers", {})
        blocked[dead_provider] = {
            "at": int(now),
            "error": str(err)[:240],
        }

        policy_order = list(((_current_config or {}).get("failover_policy", {})
                             .get("provider_order") or ("anthropic", "openai", "gemini")))
        canonical = list(dict.fromkeys(
            [p for p in policy_order if p in ("anthropic", "openai", "gemini")]
            + ["anthropic", "openai", "gemini"]
        ))
        try:
            idx = canonical.index(dead_provider)
            candidates = canonical[idx + 1:] + canonical[:idx]
        except ValueError:
            candidates = canonical

        alt = None
        for candidate in candidates:
            if candidate == dead_provider:
                continue
            if blocked.get(candidate):
                continue
            if not (_current_config or {}).get(candidate, {}).get("api_key"):
                continue
            if not _provider_supports(candidate, "chat"):
                continue
            alt = candidate
            break

        if alt:
            # Per-provider cooldown suppresses duplicate alerts from concurrent
            # workers but never prevents the next provider from advancing too.
            last = float(_last_auto_switch_by_provider.get(dead_provider, 0.0) or 0.0)
            _last_auto_switch_by_provider[dead_provider] = now
            _current_config["active_provider"] = alt
            _current_config["auto_switch_state"] = {
                "active": True,
                "from": dead_provider,
                "to": alt,
                "at": int(now),
                "reason": "quota_exhausted",
            }
            _save_config_to_disk(_current_config)
            reset_provider_health(alt)
            _label = {"openai": "🟢 OpenAI", "anthropic": "🟣 Claude", "gemini": "🔵 Gemini"}
            if now - last >= 60:
                _notify_admin(
                    "⛽ AI 額度警報\n─────\n"
                    + _label.get(dead_provider, dead_provider) + " 額度耗盡\n"
                    + "錯誤:" + str(err)[:80] + "\n─────\n"
                    + "✅ 已自動切換主力 → " + _label.get(alt, alt) + "\n"
                    + "翻譯服務不中斷。儲值後請至後台手動切回。")
            print(f"[ai_provider] ⛽ {dead_provider} 額度耗盡 → 主力自動切換 {alt}", flush=True)
            return alt

        _current_config["auto_switch_state"] = {
            "active": True,
            "from": dead_provider,
            "to": None,
            "at": int(now),
            "reason": "quota_exhausted_no_backup",
        }
        _save_config_to_disk(_current_config)

    _notify_admin("🚨 AI 額度警報:" + dead_provider + " 額度耗盡,且無其他可用 provider!"
                  "\n請立即儲值或到後台補 key,翻譯服務目前中斷。")
    return None


def _is_availability_error(e):
    """v3.26: 判斷是否為「provider 暫時不可用」類錯誤(才值得容錯移轉)。
    包含:連線/逾時、429 限流、5xx/529 過載。
    排除:400(參數)、401/403(key 問題)— 這些換家也沒用,該浮出來修。"""
    code = getattr(e, "status_code", None)
    if code in (429, 500, 502, 503, 504, 529):
        return True
    if code in (400, 401, 403, 404, 422):
        return False
    m = str(e).lower()
    return any(t in m for t in ("connection", "timed out", "timeout",
                                "overloaded", "unavailable", "rate limit",
                                "529", "503", "502"))


def _is_provider_failover_error(e):
    """只判斷「換一家可能成功」的錯誤，避免內容政策/資料格式錯誤白打三家。"""
    code = getattr(e, "status_code", None)
    if code in (401, 403, 404, 408, 409, 429, 500, 502, 503, 504, 529):
        return True
    m = str(e).lower()
    provider_specific = (
        "api key", "authentication", "unauthorized", "forbidden",
        "model not found", "does not exist", "unsupported model",
        "insufficient_quota", "quota", "credit balance", "billing",
        "rate limit", "resource_exhausted", "overloaded", "unavailable",
        "connection", "timed out", "timeout", "temporarily", "server error",
        "refusal",
    )
    if any(token in m for token in provider_specific):
        return True
    # 400/422 只在明確為供應商參數相容問題時接力；一般輸入錯誤不重送。
    if code in (400, 422) or "400" in m or "422" in m:
        return any(token in m for token in (
            "unsupported", "unrecognized", "unknown parameter", "invalid model",
            "reasoning_effort", "service_tier", "max_completion_tokens",
        ))
    return False


def _is_feature_parameter_error(e, *feature_names):
    code = getattr(e, "status_code", None)
    m = str(e).lower()
    if code not in (400, 422) and not any(x in m for x in ("400", "422", "invalid", "unsupported", "unrecognized")):
        return False
    return any(str(name).lower() in m for name in feature_names)


def _dispatch_provider(provider, model, messages, max_tokens=None,
                       max_completion_tokens=None, temperature=None, timeout=90,
                       prompt_cache_key=None, reasoning_effort=None, verbosity=None,
                       logprobs=False, top_logprobs=None, logit_bias=None,
                       stop=None, **kwargs):
    """單一 provider 呼叫分派(v3.26 自容錯重構抽出)。"""
    # 內部旗標只控制翻譯延遲，不送到任何第三方 API。
    fast_quality = bool(kwargs.pop("translation_fast_quality", False))
    structured_schema = kwargs.pop("structured_schema", None)
    structured_name = str(kwargs.pop("structured_name", "structured_response") or "structured_response")
    if provider == "anthropic":
        return _chat_complete_anthropic(
            model=model, messages=messages,
            max_tokens=max_tokens or max_completion_tokens or 1024,
            temperature=temperature, timeout=timeout,
            extra_stop=stop, fast_quality=fast_quality,
            structured_schema=structured_schema, structured_name=structured_name,
        )
    if provider == "gemini":
        return _chat_complete_gemini(
            model=model, messages=messages,
            max_tokens=max_tokens or max_completion_tokens or 1024,
            temperature=temperature, timeout=timeout, stop=stop,
            fast_quality=fast_quality, structured_schema=structured_schema,
            structured_name=structured_name,
        )
    model = normalize_openai_request_model(model, fallback=DEFAULT_OPENAI_MODEL)
    if fast_quality and reasoning_effort not in ("none", "minimal"):
        # 只對 reasoning family 覆寫；GPT-4.1 等非 reasoning 模型不送此參數。
        m = (model or "").lower()
        if m.startswith(("gpt-5.4", "gpt-5.5", "gpt-5.6")):
            reasoning_effort = "none"
        elif m.startswith(("gpt-5", "o1", "o3", "o4")):
            reasoning_effort = "minimal"
    return _chat_complete_openai(
        model=model, messages=messages,
        max_tokens=max_tokens, max_completion_tokens=max_completion_tokens,
        temperature=temperature, timeout=timeout,
        prompt_cache_key=prompt_cache_key,
        reasoning_effort=reasoning_effort, verbosity=verbosity,
        logprobs=logprobs, top_logprobs=top_logprobs,
        logit_bias=logit_bias, stop=stop,
        structured_schema=structured_schema, structured_name=structured_name,
        **kwargs,
    )


def chat_complete(model, messages, max_tokens=None, max_completion_tokens=None,
                  temperature=None, timeout=90, prompt_cache_key=None,
                  reasoning_effort=None, verbosity=None, logprobs=False,
                  top_logprobs=None, logit_bias=None, stop=None, **kwargs):
    """跨三家 AI 的唯一協調層。

    每家最多進入一次，所有嘗試共用一個總期限；provider SDK 內建重試另行關閉。
    required_capability / provider_preference / failover_* 為本層內部參數，不會送給 API。
    """
    _ensure_initialized()
    required_capability = str(kwargs.pop("required_capability", "chat") or "chat")
    provider_preference = kwargs.pop("provider_preference", None)
    latency_profile = str(kwargs.pop("latency_profile", "") or "").strip()
    response_validator = kwargs.pop("response_validator", None)
    policy = (_current_config or {}).get("failover_policy", {})
    profile_cfg = (policy.get("latency_profiles", {}) or {}).get(latency_profile, {})
    requested_total = kwargs.pop("failover_total_timeout", None)
    requested_per_provider = kwargs.pop("failover_per_provider_timeout", None)
    total_timeout = max(1.0, float(
        requested_total or profile_cfg.get("total") or policy.get("total_timeout_seconds", 60) or 60))
    per_provider_timeout = max(
        1.0, float(requested_per_provider or profile_cfg.get("per_provider")
                   or policy.get("per_provider_timeout_seconds", 24) or 24))
    requested_timeout = max(1.0, float(timeout or per_provider_timeout))
    failover_enabled = bool((_current_config or {}).get("provider_failover", True))

    providers = get_available_providers(required_capability, preference=provider_preference)
    if not providers:
        raise RuntimeError(f"沒有已設定且支援 {required_capability} 的 AI provider")
    if not failover_enabled:
        active = get_active_provider()
        providers = [active] if active in providers else providers[:1]

    deadline = time.monotonic() + total_timeout
    attempts = []
    last_error = None
    last_quality_error = None
    # Availability-first safety net: retain the latest non-empty provider
    # response even when the local response validator asks for failover.  If all
    # later providers fail or are also rejected, return this candidate marked as
    # degraded instead of raising a fake "no usable translation" outage.
    best_rejected_response = None
    best_rejected_provider = None
    best_rejected_reason = None
    best_rejected_elapsed = None
    _all_kwargs = dict(
        model=model, messages=messages, max_tokens=max_tokens,
        max_completion_tokens=max_completion_tokens, temperature=temperature,
        prompt_cache_key=prompt_cache_key, reasoning_effort=reasoning_effort,
        verbosity=verbosity, logprobs=logprobs, top_logprobs=top_logprobs,
        logit_bias=logit_bias, stop=stop, **kwargs,
    )

    # When only one provider is configured, a single transient network/5xx/429
    # error used to become an immediate visible translation failure.  Retry that
    # same provider exactly once, only for availability errors and only while the
    # shared request deadline still has room.  Multi-provider deployments still
    # fail over immediately to preserve latency.
    single_provider_retry = (
        len(providers) == 1
        and bool(policy.get("single_provider_retry", True))
    )

    for index, provider in enumerate(providers):
        provider_attempts = 2 if single_provider_retry else 1
        for provider_attempt in range(provider_attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            attempt_timeout = max(1.0, min(requested_timeout, per_provider_timeout, remaining))
            started = time.monotonic()
            try:
                if index and provider_attempt == 0:
                    print(f"[ai_provider] 容錯接力 → {provider} (剩餘 {remaining:.1f}s)", flush=True)
                elif provider_attempt:
                    print(f"[ai_provider] {provider} 暫時性錯誤，單次快速重試", flush=True)
                response = _dispatch_provider(provider, timeout=attempt_timeout, **_all_kwargs)
                elapsed = time.monotonic() - started
                if callable(response_validator):
                    verdict = response_validator(response, provider)
                    if isinstance(verdict, tuple):
                        usable = bool(verdict[0])
                        reason = str(verdict[1]) if len(verdict) > 1 else "quality validator rejected response"
                    else:
                        usable = bool(verdict)
                        reason = "quality validator rejected response"
                    if not usable:
                        last_quality_error = RuntimeError(reason)
                        best_rejected_response = response
                        best_rejected_provider = provider
                        best_rejected_reason = reason
                        best_rejected_elapsed = elapsed
                        attempts.append({
                            "provider": provider,
                            "error": reason[:300],
                            "kind": "quality_reject",
                            "latency_seconds": round(elapsed, 3),
                        })
                        # Quality rejection is deterministic for this candidate;
                        # do not retry the same provider with the same request.
                        if not failover_enabled:
                            raise last_quality_error
                        print(f"[ai_provider] {provider} 品質拒收: {reason[:160]}", flush=True)
                        break
                last_error = None
                _record_provider_success(provider, elapsed)
                try:
                    response._jy_provider = provider
                    response._jy_failover_attempts = list(attempts)
                    response._jy_latency_seconds = elapsed
                    response._jy_latency_profile = latency_profile or "default"
                except Exception:
                    pass
                return response
            except Exception as err:
                last_error = err
                transient = _is_availability_error(err) and not _is_quota_exhausted_error(err)
                can_retry_same = (
                    single_provider_retry
                    and provider_attempt == 0
                    and transient
                    and (deadline - time.monotonic()) > 1.25
                )
                attempts.append({
                    "provider": provider,
                    "error": str(err)[:300],
                    "kind": "transient_retry" if can_retry_same else "provider_error",
                    "attempt": provider_attempt + 1,
                })
                print(f"[ai_provider] {provider} 失敗: {type(err).__name__}: {str(err)[:160]}", flush=True)

                # Only explicit credit/quota exhaustion permanently changes the
                # active provider. Generic 429 means temporary rate limiting and
                # is handled by this request's failover + circuit cooldown.
                if _is_quota_exhausted_error(err):
                    _auto_switch_on_exhaust(provider, err)

                if can_retry_same:
                    time.sleep(min(0.20, max(0.0, (deadline - time.monotonic()) / 10.0)))
                    continue

                if _is_provider_failover_error(err):
                    _record_provider_failure(provider, err)
                if not failover_enabled or not _is_provider_failover_error(err):
                    raise
                break

    if best_rejected_response is not None:
        # A provider did return a translation.  Local validation may still mark
        # it non-cacheable or trigger repair downstream, but it must not be
        # discarded after every provider has been tried.
        try:
            _record_provider_success(best_rejected_provider, best_rejected_elapsed or 0.0)
        except Exception:
            pass
        try:
            best_rejected_response._jy_provider = best_rejected_provider
            best_rejected_response._jy_failover_attempts = list(attempts)
            best_rejected_response._jy_latency_seconds = best_rejected_elapsed or 0.0
            best_rejected_response._jy_latency_profile = latency_profile or "default"
            best_rejected_response._jy_quality_degraded = True
            best_rejected_response._jy_quality_reject_reason = best_rejected_reason or "quality validator rejected response"
        except Exception:
            pass
        print(
            f"[ai_provider] 所有候選皆被本地品管拒收，改送最後一份非空譯文: "
            f"{best_rejected_provider} ({str(best_rejected_reason or '')[:120]})",
            flush=True,
        )
        return best_rejected_response

    if last_error is not None:
        try:
            setattr(last_error, "_jy_failover_attempts", attempts)
        except Exception:
            pass
        raise last_error
    if last_quality_error is not None:
        try:
            setattr(last_quality_error, "_jy_failover_attempts", attempts)
        except Exception:
            pass
        raise last_quality_error
    raise TimeoutError(f"AI 翻譯超過總期限 {total_timeout:.0f} 秒")


def _chat_complete_openai(model, messages, **kwargs):
    client = _get_openai_client()
    if client is None:
        raise RuntimeError("OpenAI client 未初始化(api_key 缺?)")

    # 最後一道模型生命週期保護：任何內部模組傳入舊 ID 都先遷移。
    model = normalize_openai_request_model(model, fallback=DEFAULT_OPENAI_MODEL)

    structured_schema = kwargs.pop("structured_schema", None)
    structured_name = str(kwargs.pop("structured_name", "structured_response") or "structured_response")

    # v3.2.6: Phase 25 對稱 — OpenAI 路徑也支援 output_translation_tag
    # OpenAI GPT-5 reasoning model 跟 Claude 一樣會吐元評論(Wait/I notice/If English:),
    # 後端 regex 抽 <translation> tag 內容是兩家通用的官方治本路徑。
    # OpenAI 官方 cookbook 也推薦 XML structured output:
    #   https://developers.openai.com/api/docs/guides/prompt-guidance (<output_contract> 範例)
    _ensure_initialized()
    features = _current_config.get("claude_features", {})
    use_output_tag = features.get("output_translation_tag", False) and not structured_schema

    if use_output_tag and messages:
        # 在 system / developer role 訊息末尾注入 <output_format> 指令
        new_messages = []
        injected = False
        tag_instruction = (
            "\n\n<output_format priority=\"highest\">\n"
            "Your final answer must contain ONLY this XML tag: <translation>...</translation>\n"
            "Put the pure translation text inside the tag. Example:\n"
            "  Input: 今天加班\n"
            "  Output: <translation>Hari ini lembur</translation>\n"
            "\n"
            "Strict rules:\n"
            "1. Do NOT output any text outside the <translation> tag (no explanation, "
            "no self-correction like 'Wait — I notice', no clarifying questions, no alternatives).\n"
            "2. Do NOT add prefixes like 'Translation:' or 'Catatan:' inside the tag.\n"
            "3. Do NOT use markdown (**bold**, ## headers, - bullets) inside the tag unless source has them.\n"
            "4. Even if you want to self-correct or ask the user, do NOT output text outside the tag. "
            "Just give the final <translation>...</translation>.\n"
            "5. Output exactly ONE <translation> tag, not multiple, not nested.\n"
            "</output_format>\n"
        )
        for m in messages:
            if not injected and isinstance(m, dict) and m.get("role") in ("system", "developer"):
                content = m.get("content", "")
                if isinstance(content, str):
                    new_messages.append({**m, "content": content + tag_instruction})
                    injected = True
                    continue
            new_messages.append(m)
        if not injected:
            # 訊息中沒 system/developer role,prepend 一個
            new_messages.insert(0, {"role": "system", "content": tag_instruction.lstrip()})
        messages = new_messages

    timeout = kwargs.get("timeout", 90)
    request_client = _client_with_limits(client, timeout)
    call_kwargs = {"model": model, "messages": messages}
    for k, v in kwargs.items():
        if v is None:
            continue
        if k == "logprobs" and v is False:
            continue
        call_kwargs[k] = v
    if structured_schema:
        call_kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": re.sub(r"[^A-Za-z0-9_-]+", "_", structured_name)[:64] or "structured_response",
                "strict": True,
                "schema": structured_schema,
            },
        }
        call_kwargs.pop("stop", None)

    # GPT-5.4 / GPT-5.5 是 reasoning family：Chat Completions 使用
    # max_completion_tokens；純翻譯預設 reasoning=none，並移除不相容採樣參數。
    _model_lower = (model or "").lower()
    if _model_lower.startswith(("gpt-5.4", "gpt-5.5", "gpt-5.6")):
        if "max_tokens" in call_kwargs and "max_completion_tokens" not in call_kwargs:
            call_kwargs["max_completion_tokens"] = call_kwargs.pop("max_tokens")
        for _unsupported in (
            "temperature", "top_p", "seed", "stop", "logprobs",
            "top_logprobs", "logit_bias",
        ):
            call_kwargs.pop(_unsupported, None)
        if not _model_lower.endswith("-pro"):
            call_kwargs.setdefault("reasoning_effort", "none")
    # v3.25: 背景執行緒(bgpost/evlog)的呼叫走官方 Flex tier 半價。
    # 偵測:thread 名稱(v3.13 背景池命名)+ config 開關 + 模型支援
    # (官方 flex 支援 gpt-5 系/o 系;gpt-4.1 系不支援 → 自動略過)。
    # 任何拒絕 → 退掉 service_tier 重試,不讓背景品檢失敗。
    _flex_used = False
    try:
        _ofeat = (_current_config or {}).get("openai_features", {})
        if _ofeat.get("flex_background", True):
            import threading as _th
            _tn = _th.current_thread().name or ""
            _m = (call_kwargs.get("model") or "").lower()
            if _tn.startswith(("bgpost", "evlog")) and _m.startswith(("gpt-5", "o3", "o4")):
                call_kwargs["service_tier"] = "flex"
                _flex_used = True
    except Exception:
        pass
    try:
        resp = request_client.chat.completions.create(**call_kwargs)
    except Exception as _fe:
        if structured_schema and _is_feature_parameter_error(
                _fe, "response_format", "json_schema", "structured output"):
            # Older compatibility endpoints may not implement strict JSON schema.
            # The audit prompt still requires JSON, so retry once without the
            # transport-level constraint rather than dropping the audit.
            call_kwargs.pop("response_format", None)
            resp = request_client.chat.completions.create(**call_kwargs)
        elif _flex_used and _is_feature_parameter_error(_fe, "service_tier", "flex"):
            call_kwargs.pop("service_tier", None)
            resp = request_client.chat.completions.create(**call_kwargs)
        else:
            raise

    # v3.2.6: response 抽 <translation> tag(對稱 Anthropic line 1567-1574)
    # 容錯:若 LLM 沒乖乖包 tag,保留原 content 不動(向後相容)
    if use_output_tag and getattr(resp, "choices", None):
        try:
            first = resp.choices[0]
            msg = getattr(first, "message", None)
            content = getattr(msg, "content", None) if msg else None
            if content:
                import re as _re_tag
                match = _re_tag.search(
                    r"<translation[^>]*>(.*?)</translation>",
                    content, _re_tag.DOTALL | _re_tag.IGNORECASE,
                )
                if match:
                    extracted = match.group(1).strip()
                    if extracted:
                        try:
                            first.message.content = extracted
                        except Exception:
                            # OpenAI response 物件不可寫 — 印警告但不 raise
                            print(f"[ai_provider] WARN: OpenAI response.message.content 不可寫,"
                                  f"無法抽 tag。原內容保留。", flush=True)
        except Exception as _e:
            print(f"[ai_provider] WARN: OpenAI 抽 <translation> tag 失敗: {_e}", flush=True)

    return resp


def _chat_complete_anthropic(model, messages, max_tokens, temperature=None,
                              timeout=120, extra_stop=None, fast_quality=False,
                              structured_schema=None, structured_name="structured_response"):
    """Anthropic 路徑 — v3.0 完整 Claude 能力全部自動啟用"""
    client = _get_anthropic_client()
    if client is None:
        raise RuntimeError("Anthropic client 未初始化(api_key 缺?或 pip install anthropic)")
    request_client = _client_with_limits(client, timeout)

    _ensure_initialized()
    features = dict(_current_config.get("claude_features", {}))
    structured_name = str(structured_name or "structured_response")
    # Native structured outputs cannot be combined with citation blocks, XML
    # output wrappers or stop sequences that may truncate JSON.
    if structured_schema:
        features["extended_thinking"] = False
        features["adaptive_thinking"] = False
        features["citations"] = False
        features["stop_sequences"] = False
        features["output_translation_tag"] = False
        features["assistant_prefill"] = False
        features["glossary_grounding"] = False
    # 翻譯快速品質模式只關閉 Extended/Adaptive Thinking；模型、system prompt、
    # glossary grounding、cache、stop sequence 與輸出後處理全部維持不變。
    if fast_quality:
        features["extended_thinking"] = False
        features["adaptive_thinking"] = False
    use_cache = features.get("prompt_caching", True)
    use_thinking = features.get("extended_thinking", False)  # v3.18: 預設 False
    thinking_budget = int(features.get("thinking_budget", 2000))
    use_grounding = features.get("glossary_grounding", True)
    glossary_max = int(features.get("glossary_max_items", 50))
    use_stop = features.get("stop_sequences", True)
    use_xml = features.get("xml_system_prompt", True)
    use_citations = features.get("citations", True)
    use_cache_1h = features.get("extended_cache_1h", True)
    use_multi_cache = features.get("multi_block_caching", True)  # D3 Phase 12
    use_files_api = features.get("files_api_glossary", False)    # D3 Phase 17
    # === v3.2 D4 新增 ===
    use_smart_threshold = features.get("smart_cache_threshold", True)  # Phase 15

    anthropic_model = _resolve_anthropic_model(model)
    is_haiku = "haiku" in anthropic_model.lower()
    is_opus = "opus" in anthropic_model.lower()
    is_sonnet5 = _model_is_sonnet5(anthropic_model)

    # ─── Step 1: 訊息格式轉換 ───
    system_text = ""
    anthropic_messages = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            if system_text:
                system_text += "\n\n" + (content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
            else:
                system_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        elif role in ("user", "assistant"):
            if isinstance(content, list):
                anthropic_content = _convert_openai_content_blocks_to_anthropic(content)
                anthropic_messages.append({"role": role, "content": anthropic_content})
            else:
                anthropic_messages.append({"role": role, "content": str(content)})

    # v3.2.3 Phase 21: 偵測是否為 OCR 場景(訊息內含 image / document block)
    # 偵測完整 anthropic_messages list,只要任一 user message 有視覺 block 就算
    _has_visual_input = False
    for _m in anthropic_messages:
        _c = _m.get("content")
        if isinstance(_c, list):
            for _blk in _c:
                if isinstance(_blk, dict) and _blk.get("type") in ("image", "document"):
                    _has_visual_input = True
                    break
        if _has_visual_input:
            break

    # v3.2.3 讀取新 toggle
    use_line_plain = features.get("line_plain_text_mode", True)        # Phase 20
    use_ocr_strict = features.get("ocr_strict_layout", True)           # Phase 21
    # OCR 嚴格保版面只在有視覺輸入時觸發
    _apply_ocr_strict = use_ocr_strict and _has_visual_input

    # v3.2.4 Phase 23+24
    use_cot_tag = features.get("cot_thinking_tag", True)
    use_role_strong = features.get("role_strong", True)
    # v3.2.5 Phase 25+26
    use_output_tag = features.get("output_translation_tag", False)
    use_success_criteria = features.get("success_criteria", True)

    # Phase 5 + 20 + 21 + 23 + 24 + 25 + 26: XML 包裝 system prompt
    if use_xml and system_text:
        system_text = _wrap_system_prompt_xml(
            system_text,
            line_plain=use_line_plain,
            ocr_strict=_apply_ocr_strict,
            cot_tag=use_cot_tag,
            role_strong=use_role_strong,
            output_tag=use_output_tag,
            success_criteria=use_success_criteria,
        )

    # 連續 same-role 合併(Phase 6 few-shot 自動 OK,因為 user→assistant 交替)
    merged = []
    for m in anthropic_messages:
        if merged and merged[-1]["role"] == m["role"]:
            prev = merged[-1]
            if isinstance(prev["content"], str) and isinstance(m["content"], str):
                prev["content"] = prev["content"] + "\n\n" + m["content"]
            else:
                a = prev["content"] if isinstance(prev["content"], str) else json.dumps(prev["content"], ensure_ascii=False)
                b = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"], ensure_ascii=False)
                prev["content"] = a + "\n\n" + b
        else:
            merged.append(m)

    # ─── Step 2: Phase 3 — Glossary Grounding ───
    grounding_used = False
    matched_terms_count = 0
    if use_grounding and merged:
        matched = _find_relevant_glossary_terms(messages, max_items=glossary_max)
        if matched:
            matched_terms_count = len(matched)
            grounding_blocks = _build_glossary_search_results(
                matched, citations_enabled=use_citations
            )
            for i in range(len(merged) - 1, -1, -1):
                if merged[i]["role"] == "user":
                    cur_content = merged[i]["content"]
                    if isinstance(cur_content, str):
                        text_block = {"type": "text", "text": cur_content}
                        merged[i]["content"] = grounding_blocks + [text_block]
                    elif isinstance(cur_content, list):
                        merged[i]["content"] = grounding_blocks + cur_content
                    grounding_used = True
                    break

    # ─── Step 2.5: v3.2.4 Phase 22: Assistant Prefill ───
    # Anthropic 官方:prefill assistant turn 可跳過 preamble、強制輸出格式
    # https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/prefill-claudes-response
    #
    # 重要限制:
    #   - Sonnet 4.6 / Opus 4.6+ / Opus 4.7 / Mythos 不支援(送出會 400)
    #     歐那雙模型場景:Haiku 4.5 可用,Sonnet 4.6 跳過
    #   - prefill 內容不能 trailing whitespace
    #   - 必須是最後一個 message
    #   - 含圖片場景不 prefill(避免 vision 行為被干擾)
    #
    # 預設 OFF,歐那要時可在後台填自訂 prefill text(例如「翻譯:」)
    # 但建議**不填**,因 Phase 4 stop_sequences + Phase 5 XML output_format 已有效防前綴
    prefill_applied = False
    use_prefill = features.get("assistant_prefill", False)
    prefill_text = features.get("assistant_prefill_text", "")
    if use_prefill and prefill_text and isinstance(prefill_text, str):
        if _supports_prefill(anthropic_model):
            # 偵測上一條 user message 是純文字
            last_user_is_text = False
            if merged and merged[-1].get("role") == "user":
                last_content = merged[-1].get("content")
                if isinstance(last_content, str):
                    last_user_is_text = True
                elif isinstance(last_content, list):
                    has_visual = any(
                        isinstance(b, dict) and b.get("type") in ("image", "document")
                        for b in last_content
                    )
                    last_user_is_text = not has_visual
            if last_user_is_text:
                # rstrip 確保無 trailing whitespace(官方明文禁止,否則 400)
                clean_prefill = prefill_text.rstrip()
                if clean_prefill:
                    merged.append({"role": "assistant", "content": clean_prefill})
                    prefill_applied = True

    # ─── Step 3: 組裝 call_kwargs ───
    call_kwargs = {
        "model": anthropic_model,
        "messages": merged,
        "max_tokens": int(max_tokens or 1024),
    }

    # Phase 1 + 8 + 12 + 15: Prompt Caching
    # v3.2 BUG 修:改用 model-specific token 門檻,不再 silent fail
    caching_applied = False
    multi_block_applied = False
    cache_threshold_used = 0
    cache_est_tokens = 0
    if system_text:
        should_cache, threshold, est_tok = _should_apply_cache(
            system_text, anthropic_model, smart_mode=use_smart_threshold
        )
        cache_threshold_used = threshold
        cache_est_tokens = est_tok
        if use_cache and should_cache:
            if use_multi_cache:
                # Phase 12: Multi-block — 把 system 拆 stable + dynamic 兩層 cache
                blocks = _split_system_into_cache_blocks(
                    system_text, use_xml=use_xml, use_1h=use_cache_1h
                )
                if blocks:
                    call_kwargs["system"] = blocks
                    caching_applied = True
                    multi_block_applied = len(blocks) > 1
            else:
                # 單層 cache(舊邏輯)
                cache_control = {"type": "ephemeral"}
                if use_cache_1h:
                    cache_control["ttl"] = "1h"
                call_kwargs["system"] = [{
                    "type": "text",
                    "text": system_text,
                    "cache_control": cache_control
                }]
                caching_applied = True
        else:
            # 沒達門檻 → 純 system,不套 cache_control(避免 silent 浪費)
            call_kwargs["system"] = system_text

    # Phase 4: Stop Sequences
    if use_stop:
        stops = _build_stop_sequences()
        if extra_stop:
            if isinstance(extra_stop, str):
                stops.append(extra_stop)
            elif isinstance(extra_stop, list):
                stops.extend(extra_stop)
        # Anthropic 上限 4 個 stop sequences,挑最重要的
        call_kwargs["stop_sequences"] = stops[:4]

    # Phase 2 + 13 + 14: Extended/Adaptive Thinking
    # v3.2 BUG 修:Opus 4.7 強制 adaptive(舊 enabled mode 已 removed,丟了會 400)
    #             Sonnet 4.6 預設改 adaptive(舊 enabled mode deprecated)
    thinking_applied = False
    thinking_mode_used = "none"
    thinking_cfg, thinking_mode_used = _pick_thinking_config(features, anthropic_model)
    if thinking_cfg is not None:
        # adaptive 模式不需要也不能設 budget_tokens
        # enabled 模式需要確保 max_tokens >= budget + 1024
        if thinking_cfg.get("type") == "enabled":
            needed_max = max(
                int(max_tokens or 1024),
                int(thinking_cfg.get("budget_tokens", 2000)) + 1024
            )
            call_kwargs["max_tokens"] = needed_max
        call_kwargs["thinking"] = thinking_cfg
        thinking_applied = True
        if is_sonnet5:
            # Sonnet 5 的 effort 必須放在 output_config，且 sampling params 不接受。
            call_kwargs["output_config"] = {
                "effort": _normalize_effort(features.get("thinking_effort", "low"), anthropic_model)
            }
    else:
        if is_sonnet5:
            # Sonnet 5 未傳 thinking 時預設 adaptive/high，會增加即時翻譯延遲。
            # 翻譯路徑明確關閉 thinking，並把整體輸出 effort 固定 low。
            call_kwargs["thinking"] = {"type": "disabled"}
            call_kwargs["output_config"] = {"effort": "low"}
            thinking_mode_used = "disabled_low"
        elif temperature is not None and not is_opus:
            call_kwargs["temperature"] = max(0.0, min(1.0, float(temperature)))

    if structured_schema:
        output_config = dict(call_kwargs.get("output_config") or {})
        output_config["format"] = {
            "type": "json_schema",
            "schema": structured_schema,
        }
        call_kwargs["output_config"] = output_config

    # ─── Step 4: 呼叫 ───
    try:
        resp = request_client.messages.create(**call_kwargs)
    except Exception as e:
        err_msg = str(e).lower()
        # v3.2.7 根治: 所有 thinking 相關錯誤都 fallback,不再只抓特定關鍵字。
        # 原因:Sonnet 4.6 + adaptive thinking 失敗時,若錯誤訊息不含
        # "adaptive"/"effort"/"display" 等字,原本直接 raise → 翻譯無聲消失。
        # 新邏輯:只要 thinking_applied=True 且 API 呼叫失敗,一律 fallback。
        if structured_schema and _is_feature_parameter_error(
                e, "output_config", "json_schema", "format", "structured output"):
            print(f"[ai_provider] Anthropic structured output 不相容，退回 JSON prompt: {str(e)[:160]}", flush=True)
            output_config = dict(call_kwargs.get("output_config") or {})
            output_config.pop("format", None)
            if output_config:
                call_kwargs["output_config"] = output_config
            else:
                call_kwargs.pop("output_config", None)
            resp = request_client.messages.create(**call_kwargs)
        elif thinking_applied and _is_feature_parameter_error(
                e, "thinking", "adaptive", "budget_tokens", "effort", "display"):
            print(f"[ai_provider] thinking 呼叫失敗,嘗試 fallback: {type(e).__name__}: {str(e)[:200]}", flush=True)
            # Fallback A: adaptive → legacy
            if (isinstance(call_kwargs.get("thinking"), dict)
                and call_kwargs["thinking"].get("type") == "adaptive"):
                if _model_requires_adaptive(anthropic_model):
                    # Opus 4.7 / Sonnet 5 不支援 legacy budget thinking。
                    if is_sonnet5:
                        call_kwargs["thinking"] = {"type": "disabled"}
                        call_kwargs["output_config"] = {"effort": "low"}
                        thinking_mode_used = "disabled_low(fallback)"
                    else:
                        call_kwargs.pop("thinking", None)
                        call_kwargs.pop("output_config", None)
                        thinking_mode_used = "none(opus47_fallback)"
                    thinking_applied = False
                else:
                    legacy_budget = int(features.get("thinking_budget", 2000))
                    call_kwargs["thinking"] = {"type": "enabled", "budget_tokens": legacy_budget}
                    call_kwargs["max_tokens"] = max(int(max_tokens or 1024), legacy_budget + 1024)
                    thinking_mode_used = f"legacy_{legacy_budget}(fallback)"
                try:
                    resp = request_client.messages.create(**call_kwargs)
                except Exception as e2:
                    print(f"[ai_provider] legacy fallback 也失敗: {e2}", flush=True)
                    # Fallback B: legacy → 完全關掉 thinking
                    if is_sonnet5:
                        call_kwargs["thinking"] = {"type": "disabled"}
                        call_kwargs["output_config"] = {"effort": "low"}
                        thinking_mode_used = "disabled_low(all_thinking_failed)"
                    else:
                        call_kwargs.pop("thinking", None)
                        call_kwargs.pop("output_config", None)
                        thinking_mode_used = "none(all_thinking_failed)"
                    call_kwargs["max_tokens"] = int(max_tokens or 1024)
                    thinking_applied = False
                    resp = request_client.messages.create(**call_kwargs)
            else:
                # enabled mode 失敗 → 關掉 thinking
                if is_sonnet5:
                    call_kwargs["thinking"] = {"type": "disabled"}
                    call_kwargs["output_config"] = {"effort": "low"}
                    thinking_mode_used = "disabled_low(thinking_failed)"
                else:
                    call_kwargs.pop("thinking", None)
                    call_kwargs.pop("output_config", None)
                    thinking_mode_used = "none(thinking_failed)"
                call_kwargs["max_tokens"] = int(max_tokens or 1024)
                thinking_applied = False
                resp = request_client.messages.create(**call_kwargs)
        # 非 thinking 錯誤:grounding/citation/cache/stop 的 fallback
        elif grounding_used and ("search_result" in err_msg or "citation" in err_msg or "content block" in err_msg):
            print(f"[ai_provider] grounding/citation 失敗,fallback: {e}", flush=True)
            for i, m in enumerate(merged):
                if m["role"] == "user" and isinstance(m["content"], list):
                    text_only = [b for b in m["content"] if not (isinstance(b, dict) and b.get("type") == "search_result")]
                    if text_only:
                        if len(text_only) == 1 and text_only[0].get("type") == "text":
                            merged[i]["content"] = text_only[0]["text"]
                        else:
                            merged[i]["content"] = text_only
            call_kwargs["messages"] = merged
            grounding_used = False
            resp = request_client.messages.create(**call_kwargs)
        elif use_cache_1h and ("extended-cache" in err_msg or "beta" in err_msg or "ttl" in err_msg):
            print(f"[ai_provider] 1h cache fallback to 5min: {e}", flush=True)
            if isinstance(call_kwargs.get("system"), list):
                for blk in call_kwargs["system"]:
                    if isinstance(blk, dict) and "cache_control" in blk:
                        blk["cache_control"] = {"type": "ephemeral"}
            resp = request_client.messages.create(**call_kwargs)
        elif use_stop and ("stop_sequence" in err_msg or "too many" in err_msg):
            print(f"[ai_provider] stop_sequences fallback: {e}", flush=True)
            call_kwargs.pop("stop_sequences", None)
            resp = request_client.messages.create(**call_kwargs)
        else:
            raise

    # Sonnet 5 may return HTTP 200 with stop_reason=refusal. Treat it as an
    # unusable provider response so the unified coordinator can try another AI.
    if getattr(resp, "stop_reason", None) == "refusal":
        raise RuntimeError("Anthropic refusal response")

    # ─── Step 5: 抽出文字 + Phase 9 citations ───
    full_text = ""
    citations_list = []
    if resp.content:
        for block in resp.content:
            block_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
            if block_type == "text":
                if hasattr(block, "text"):
                    full_text += block.text or ""
                elif isinstance(block, dict):
                    full_text += block.get("text", "")
                # 收集 citations(Phase 9)
                block_citations = getattr(block, "citations", None) or (block.get("citations") if isinstance(block, dict) else None)
                if block_citations:
                    for c in block_citations:
                        title = getattr(c, "title", None) or (c.get("title") if isinstance(c, dict) else None)
                        if title:
                            citations_list.append(title)
            # thinking block 不抽出

    # v3.2.5 Phase 25: 若啟用 output_translation_tag,從 <translation>...</translation> 抽純翻譯
    # 容錯處理:
    #   - 若 Claude 沒乖乖包 tag(僅輸出純文字),回傳 full_text 不動(向後相容)
    #   - 若 Claude 包了 tag,只取 tag 內內容,丟棄 tag 前/後雜訊
    #   - 多個 tag 取第一個(理論上 Claude 只會出一個)
    if use_output_tag and full_text:
        import re as _re_tag
        # 寬鬆 regex:支援 <translation> 或 <translation lang="id"> 等變體
        match = _re_tag.search(r"<translation[^>]*>(.*?)</translation>", full_text, _re_tag.DOTALL | _re_tag.IGNORECASE)
        if match:
            extracted = match.group(1).strip()
            if extracted:
                full_text = extracted
        # 若沒命中 tag,保留 full_text 原樣(向後相容)

    usage = _UnifiedUsage(
        prompt_tokens=getattr(resp.usage, "input_tokens", 0) if resp.usage else 0,
        completion_tokens=getattr(resp.usage, "output_tokens", 0) if resp.usage else 0,
    )
    usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
    if resp.usage:
        usage.cache_read_tokens = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
        usage.cache_creation_tokens = getattr(resp.usage, "cache_creation_input_tokens", 0) or 0

    result = _UnifiedResponse(
        content=full_text,
        model=anthropic_model,
        usage=usage,
        finish_reason=getattr(resp, "stop_reason", "stop") or "stop",
        logprobs=None,
        citations=citations_list,
    )

    # debug 標記:這次用了哪些 Claude 能力
    result._jy_claude_features_used = {
        "caching": caching_applied,
        "caching_1h": caching_applied and use_cache_1h,
        "multi_block_caching": multi_block_applied,  # D3 Phase 12
        "thinking": thinking_applied,
        "thinking_mode": thinking_mode_used,         # v3.2 Phase 13:adaptive_medium / legacy_2000 / none
        "grounding": grounding_used,
        "grounding_terms_count": matched_terms_count,
        "stop_sequences": use_stop,
        "xml_system": use_xml,
        "citations": use_citations and len(citations_list) > 0,
        "citation_count": len(citations_list),
        # v3.2 Phase 15:cache 門檻診斷
        "cache_threshold_tokens": cache_threshold_used,
        "cache_est_tokens": cache_est_tokens,
        "cache_above_threshold": cache_est_tokens >= cache_threshold_used if cache_threshold_used > 0 else None,
        # v3.2.3 Phase 20+21
        "line_plain_text_mode": use_xml and use_line_plain,         # 防 LINE markdown 廢字元
        "has_visual_input": _has_visual_input,                       # 偵測到圖片/PDF
        "ocr_strict_layout_applied": use_xml and _apply_ocr_strict,  # OCR 嚴格保版面實際生效
        # v3.2.4 Phase 22+23
        "prefill_applied": prefill_applied,                          # Assistant Prefill 是否實際套用
        "prefill_supports_model": _supports_prefill(anthropic_model),  # 此 model 是否支援 prefill
        # v3.2.5 Phase 25+26
        "output_translation_tag_applied": use_output_tag,            # 是否強制 <translation> tag
        "success_criteria_applied": use_xml and use_success_criteria, # 是否注入成功標準
    }
    return result


# ═══════════════════════════════════════════════════════════════════
# Phase 10: Streaming
# ═══════════════════════════════════════════════════════════════════
def chat_complete_stream(model, messages, max_tokens=None, temperature=None, **kwargs):
    """Streaming 版本 — 漸進輸出 token
    
    Yields:
        - text chunks (字串)
        - 最後 yield 一個 dict {"_final": True, "usage": ..., "model": ...}
    
    使用方式:
        full_text = ""
        for chunk in chat_complete_stream(model="gpt-4.1-mini", messages=[...]):
            if isinstance(chunk, dict) and chunk.get("_final"):
                # 最後 metadata
                print("Total tokens:", chunk["usage"])
            else:
                full_text += chunk
                print(chunk, end="", flush=True)
    """
    provider = get_active_provider()
    if provider == "anthropic":
        yield from _chat_complete_stream_anthropic(model, messages, max_tokens, temperature, **kwargs)
    elif provider == "gemini":
        # v3.21: Gemini 相容端點支援 OpenAI 式 streaming
        client = _get_gemini_client()
        if client is None:
            raise RuntimeError("Gemini client 未初始化")
        g_model = _resolve_gemini_model(model)
        stream = client.chat.completions.create(
            model=g_model, messages=messages, stream=True,
            **({"max_tokens": int(max_tokens)} if max_tokens else {}),
            **({"temperature": temperature} if temperature is not None else {}),
        )
        usage = None
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content if chunk.choices else None
            except Exception:
                delta = None
            if delta:
                yield delta
            if getattr(chunk, "usage", None):
                usage = chunk.usage
        yield {"_final": True, "usage": usage, "model": g_model}
    else:
        yield from _chat_complete_stream_openai(model, messages, max_tokens, temperature, **kwargs)


def _chat_complete_stream_openai(model, messages, max_tokens, temperature, **kwargs):
    client = _get_openai_client()
    if client is None:
        raise RuntimeError("OpenAI client 未初始化")
    model = normalize_openai_model(model, fallback=DEFAULT_OPENAI_MODEL)
    call_kwargs = {"model": model, "messages": messages, "stream": True}
    if model.lower().startswith(("gpt-5.4", "gpt-5.5", "gpt-5.6")):
        if max_tokens is not None:
            call_kwargs["max_completion_tokens"] = max_tokens
        if not model.lower().endswith("-pro"):
            call_kwargs["reasoning_effort"] = "none"
    else:
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            call_kwargs["temperature"] = temperature
    stream = client.chat.completions.create(**call_kwargs)
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
    yield {"_final": True, "model": model, "provider": "openai"}


def _chat_complete_stream_anthropic(model, messages, max_tokens, temperature, **kwargs):
    """Anthropic streaming — 同樣套用 v3.0 所有 Claude 能力"""
    client = _get_anthropic_client()
    if client is None:
        raise RuntimeError("Anthropic client 未初始化")

    # 走跟 _chat_complete_anthropic 一樣的訊息處理邏輯,只是改 stream
    # 簡化:複用 non-stream 路徑來組 call_kwargs,然後改 stream=True
    _ensure_initialized()
    features = _current_config.get("claude_features", {})

    # 訊息轉換(同 non-stream)
    system_text = ""
    anthropic_messages = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            system_text = (system_text + "\n\n" + content) if system_text else content
        elif role in ("user", "assistant"):
            anthropic_messages.append({"role": role, "content": str(content) if not isinstance(content, list) else content})

    # v3.2.3: streaming 路徑同步套 line_plain / ocr_strict
    _has_visual_input = False
    for _m in anthropic_messages:
        _c = _m.get("content")
        if isinstance(_c, list):
            for _blk in _c:
                if isinstance(_blk, dict) and _blk.get("type") in ("image", "document"):
                    _has_visual_input = True
                    break
        if _has_visual_input:
            break

    if features.get("xml_system_prompt", True) and system_text:
        system_text = _wrap_system_prompt_xml(
            system_text,
            line_plain=features.get("line_plain_text_mode", True),
            ocr_strict=features.get("ocr_strict_layout", True) and _has_visual_input,
            cot_tag=features.get("cot_thinking_tag", True),
            role_strong=features.get("role_strong", True),
            output_tag=features.get("output_translation_tag", False),
            success_criteria=features.get("success_criteria", True),
        )

    # Grounding
    merged = anthropic_messages
    if features.get("glossary_grounding", True):
        matched = _find_relevant_glossary_terms(messages, max_items=features.get("glossary_max_items", 50))
        if matched:
            grounding_blocks = _build_glossary_search_results(matched, citations_enabled=features.get("citations", True))
            for i in range(len(merged) - 1, -1, -1):
                if merged[i]["role"] == "user":
                    cur = merged[i]["content"]
                    if isinstance(cur, str):
                        merged[i]["content"] = grounding_blocks + [{"type": "text", "text": cur}]
                    elif isinstance(cur, list):
                        merged[i]["content"] = grounding_blocks + cur
                    break

    anthropic_model = _resolve_anthropic_model(model)
    is_haiku = "haiku" in anthropic_model.lower()

    call_kwargs = {
        "model": anthropic_model,
        "messages": merged,
        "max_tokens": int(max_tokens or 1024),
    }

    # v3.2: 套 smart cache threshold
    use_smart_threshold = features.get("smart_cache_threshold", True)
    if system_text:
        should_cache, _, _ = _should_apply_cache(
            system_text, anthropic_model, smart_mode=use_smart_threshold
        )
        if features.get("prompt_caching", True) and should_cache:
            cc = {"type": "ephemeral"}
            if features.get("extended_cache_1h", True):
                cc["ttl"] = "1h"
            call_kwargs["system"] = [{"type": "text", "text": system_text, "cache_control": cc}]
        else:
            call_kwargs["system"] = system_text

    if features.get("stop_sequences", True):
        call_kwargs["stop_sequences"] = _build_stop_sequences()[:4]

    # v3.2: 用 _pick_thinking_config 自動處理 Opus 4.7 強制 adaptive 的問題
    thinking_cfg, _ = _pick_thinking_config(features, anthropic_model)
    if thinking_cfg is not None:
        if thinking_cfg.get("type") == "enabled":
            budget = int(thinking_cfg.get("budget_tokens", 2000))
            call_kwargs["max_tokens"] = max(call_kwargs["max_tokens"], budget + 1024)
        call_kwargs["thinking"] = thinking_cfg
    elif temperature is not None:
        call_kwargs["temperature"] = max(0.0, min(1.0, float(temperature)))

    # 啟用 stream
    input_tokens = 0
    output_tokens = 0
    try:
        with client.messages.stream(**call_kwargs) as stream:
            for text in stream.text_stream:
                yield text
            # stream 結束後可拿 final message
            final = stream.get_final_message()
            if final and final.usage:
                input_tokens = final.usage.input_tokens
                output_tokens = final.usage.output_tokens
    except Exception as e:
        # streaming 失敗 → 拋出讓上層處理(可 fallback to non-stream)
        raise

    yield {
        "_final": True,
        "model": anthropic_model,
        "provider": "anthropic",
        "usage": {"input": input_tokens, "output": output_tokens},
    }


# ═══════════════════════════════════════════════════════════════════
# Phase 7: Native Vision — 圖片直接給 Claude 讀
# ═══════════════════════════════════════════════════════════════════
def _convert_openai_content_blocks_to_anthropic(blocks):
    """OpenAI multi-modal content → Anthropic content list
    
    Phase 7: 圖片(包含 PDF base64)直接走 Claude vision
    
    v3.2.1 Phase 18: image-then-text 自動重排
      官方明文 best practice:
        "Claude works best when images come before text.
         Prefer image-then-text structure."
        — platform.claude.com/docs/en/build-with-claude/vision
      
      重排規則(保守,避免破壞 few-shot vision):
        - 若視覺 block(image/document) 剛好 1 個 + text block ≥ 1 個
          → 把視覺 block 提到最前面
        - 若多張視覺 block → 保持原順序(可能是 few-shot 範例)
        - 若沒有視覺或沒有文字 → 保持原順序
      
      不重排的情況不會犧牲品質 — 官方說
      "Images placed after text or interpolated with text still perform well"
      只是 image-first 略佳。
    
    v3.9.33 型別安全:
      - None 輸入 → 回空 list
      - 純字串輸入 → 包裝成單一 text block(避免字串被當 iterable 拆字元)
    """
    # 型別安全
    if blocks is None:
        return []
    if isinstance(blocks, str):
        return [{"type": "text", "text": blocks}]
    if not isinstance(blocks, list):
        return [{"type": "text", "text": str(blocks)}]
    
    result = []
    for b in blocks:
        if not isinstance(b, dict):
            result.append({"type": "text", "text": str(b)})
            continue
        btype = b.get("type", "")
        if btype == "text":
            result.append({"type": "text", "text": b.get("text", "")})
        elif btype == "image_url":
            url = b.get("image_url", {}).get("url", "") if isinstance(b.get("image_url"), dict) else ""
            if url.startswith("data:"):
                try:
                    header, b64data = url.split(",", 1)
                    media_type = header.split(";")[0].replace("data:", "") or "image/png"
                    # PDF 也支援(Claude 原生)
                    if media_type == "application/pdf":
                        result.append({
                            "type": "document",
                            "source": {"type": "base64", "media_type": "application/pdf", "data": b64data}
                        })
                    else:
                        result.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": b64data}
                        })
                except Exception:
                    result.append({"type": "text", "text": "[image decode error]"})
            else:
                # URL 模式
                result.append({
                    "type": "image",
                    "source": {"type": "url", "url": url}
                })
        elif btype == "image":
            # 已經是 Anthropic 格式,直接保留
            result.append(b)
        else:
            result.append({"type": "text", "text": f"[unsupported block type: {btype}]"})

    # v3.2.1 Phase 18: image-then-text 重排
    # 找出視覺 block 的位置
    visual_indices = [
        i for i, blk in enumerate(result)
        if isinstance(blk, dict) and blk.get("type") in ("image", "document")
    ]
    text_count = sum(
        1 for blk in result
        if isinstance(blk, dict) and blk.get("type") == "text"
    )
    # 規則:剛好 1 個視覺 block + ≥1 個文字 block + 視覺不在最前面 → 移到最前
    if len(visual_indices) == 1 and text_count >= 1 and visual_indices[0] != 0:
        visual_block = result.pop(visual_indices[0])
        result.insert(0, visual_block)

    return result


# ═══════════════════════════════════════════════════════════════════
# v3.2.2 Phase 19: Image Translation Toggle
# ═══════════════════════════════════════════════════════════════════
def should_use_claude_for_images():
    """app.py 在做圖片 OCR/翻譯前呼叫,決定要用 Claude 還是 OpenAI
    
    Returns: True  → 走 Claude vision(若 active provider 是 anthropic)
             False → 強制走 OpenAI vision(不管 active provider)
    
    用途:給歐那一個獨立開關,避免「切到 Anthropic 後圖片成本爆」
         圖片 token 比文字貴(一張 1MP 圖 ≈ 1600 tokens × $5/M Claude = $0.008)
         有時候只想讓文字翻譯走 Claude,圖片優先改走其他已設定的視覺 provider
    
    呼叫例:
        if ai_provider.get_active_provider() == "anthropic" and ai_provider.should_use_claude_for_images():
            # 圖片走 Claude(透過 ai.chat.completions.create 自動路由)
            ...
        else:
            # 強制 OpenAI:直接呼叫 oai client,繞過 _AIProxy
            ...
    """
    _ensure_initialized()
    features = _current_config.get("claude_features", {})
    return features.get("image_translation_use_claude", True)


# ═══════════════════════════════════════════════════════════════════
# Audio transcription(Anthropic 不支援)
# ═══════════════════════════════════════════════════════════════════
def audio_transcribe(audio_file_path, model="gpt-4o-transcribe", **kwargs):
    provider = get_active_provider()
    if provider == "anthropic":
        raise NotImplementedError(
            "Anthropic 不支援音訊轉文字 API。請切回 OpenAI 或在 LINE 內回覆 sender 改用文字。"
        )
    client = _get_openai_client()
    if client is None:
        raise RuntimeError("OpenAI client 未初始化")
    with open(audio_file_path, "rb") as f:
        return client.audio.transcriptions.create(model=model, file=f, **kwargs)


def supports_capability(capability):
    provider = get_active_provider()
    table = {
        "audio_transcribe": {"openai": True, "anthropic": False},
        "logprobs":         {"openai": True, "anthropic": False},
        "predicted_outputs": {"openai": True, "anthropic": False},
        "vision":           {"openai": True, "anthropic": True},
        "prompt_cache":     {"openai": True, "anthropic": True},
        "chat":             {"openai": True, "anthropic": True},
        "thinking":         {"openai": False, "anthropic": True},
        "grounding":        {"openai": False, "anthropic": True},
        "stop_sequences":   {"openai": True, "anthropic": True},
        "streaming":        {"openai": True, "anthropic": True},
        "citations":        {"openai": False, "anthropic": True},
        "pdf_native":       {"openai": False, "anthropic": True},
        "extended_cache_1h": {"openai": False, "anthropic": True},
    }
    return table.get(capability, {}).get(provider, False)


# ═══════════════════════════════════════════════════════════════════
# 模組啟動
# ═══════════════════════════════════════════════════════════════════
_init_config()

if __name__ == "__main__":
    print("Active provider:", get_active_provider())
    print("Config:", json.dumps(get_current_config_safe(), ensure_ascii=False, indent=2))
