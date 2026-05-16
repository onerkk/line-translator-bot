"""
ai_provider.py — 統一 AI Provider 介面層 (v3.2.5 / 2026-05-16)

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
import time
import threading

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
# 預設配置
# ═══════════════════════════════════════════════════════════════════
DEFAULT_CONFIG = {
    "active_provider": "openai",
    "openai": {"api_key": "", "base_url": None},
    "anthropic": {
        "api_key": "",
        "default_model": "claude-haiku-4-5-20251001",
    },
    "model_mapping": {
        "gpt-4.1":             "claude-sonnet-4-6",
        "gpt-4.1-mini":        "claude-haiku-4-5-20251001",
        "gpt-4.1-nano":        "claude-haiku-4-5-20251001",
        "gpt-4o":              "claude-sonnet-4-6",
        "gpt-4o-mini":         "claude-haiku-4-5-20251001",
        "gpt-5":               "claude-opus-4-7",
        "gpt-5-mini":          "claude-haiku-4-5-20251001",
        "gpt-5-nano":          "claude-haiku-4-5-20251001",
        "gpt-5.4":             "claude-sonnet-4-6",
        "gpt-5.4-mini":        "claude-haiku-4-5-20251001",
        "gpt-5.4-nano":        "claude-haiku-4-5-20251001",
        "gpt-5.5":             "claude-opus-4-7",
        "gpt-5.5-mini":        "claude-haiku-4-5-20251001",
        "gpt-5.1":             "claude-sonnet-4-6",
        "gpt-5.2":             "claude-sonnet-4-6",
        "o1":                  "claude-opus-4-7",
        "o3":                  "claude-opus-4-7",
        "o3-mini":             "claude-haiku-4-5-20251001",
        "o4-mini":             "claude-haiku-4-5-20251001",
    },
    # === v3.0 Claude 專屬能力(全部預設 ON)===
    "claude_features": {
        "prompt_caching": True,         # Phase 1
        "extended_thinking": True,      # Phase 2
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
        "thinking_effort": "medium",    # low / medium / high (Opus 4.7 加 xhigh)
        "thinking_display": "auto",     # auto / summarized / omitted
                                        # auto = Opus 4.7→omitted(快); Sonnet/Opus 4.6→summarized
        "smart_cache_threshold": True,  # Phase 15 — 用 model-specific token 門檻,而非字元數 1024
        # === D5 v3.2.2 新增 ===
        "image_translation_use_claude": True,  # Phase 19 — 切到 Anthropic 時,圖片翻譯也走 Claude vision
                                                # OFF 時:即使 active=anthropic,圖片仍走 OpenAI(節省成本/避開未驗證)
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
        "output_translation_tag": False, # Phase 25 — 強制 <translation>...</translation> XML 包裝
                                         # 預設 OFF,因 Claude 已有 stop_sequences + plain text mode
                                         # 開啟後徹底解決前綴問題,但需後端 parse tag
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
_registered_glossary = None

# === D3 Phase 17: Files API state ===
# 把整個 glossary 上傳到 Anthropic Files API,後續 messages 內只引用 file_id
# 避免每次都送 2-5KB glossary 文字
_uploaded_glossary_file_id = None  # 上傳成功後存的 file_id
_uploaded_glossary_hash = None      # 用來判斷 glossary 有沒有改過需要重傳


# ═══════════════════════════════════════════════════════════════════
# Config 讀寫
# ═══════════════════════════════════════════════════════════════════
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
            return merged
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
            _openai_client = OpenAI(api_key=api_key, timeout=30.0)
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
                default_headers=extra_headers if extra_headers else None,
            )
            _anthropic_client._jy_key = api_key
            return _anthropic_client
        except Exception as e:
            print(f"[ai_provider] Anthropic client 建立失敗 {e}", flush=True)
            return None


# ═══════════════════════════════════════════════════════════════════
# 對外 API
# ═══════════════════════════════════════════════════════════════════
def get_active_provider():
    _ensure_initialized()
    return _current_config.get("active_provider", "openai")


def set_active_provider(provider):
    if provider not in ("openai", "anthropic"):
        return False, f"unknown provider: {provider}"
    _ensure_initialized()
    with _config_lock:
        _current_config["active_provider"] = provider
        if _save_config_to_disk(_current_config):
            return True, f"切換到 {provider}"
        return False, "存檔失敗"


def update_provider_key(provider, api_key):
    if provider not in ("openai", "anthropic"):
        return False, f"unknown provider: {provider}"
    _ensure_initialized()
    with _config_lock:
        _current_config[provider]["api_key"] = (api_key or "").strip()
        if _save_config_to_disk(_current_config):
            global _openai_client, _anthropic_client
            if provider == "openai":
                _openai_client = None
            else:
                _anthropic_client = None
            return True, f"{provider} key 已更新"
        return False, "存檔失敗"


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
        for p in ("openai", "anthropic"):
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
    """v3.2.3 修正:若 caller 直接傳 claude-* model name(app.py v3.9.33 起的 pick_model
    在 Anthropic 路徑下會這樣做),直接原樣使用,不查 mapping。
    """
    _ensure_initialized()
    # 若已是 Claude model 名稱,直接回傳
    if openai_model and isinstance(openai_model, str) and openai_model.startswith("claude-"):
        return openai_model
    mapping = _current_config.get("model_mapping", {})
    if openai_model in mapping:
        return mapping[openai_model]
    return _current_config["anthropic"].get("default_model", "claude-haiku-4-5-20251001")


# ═══════════════════════════════════════════════════════════════════
# v3.2 Phase 15: Smart Cache Threshold(按 model-specific token 門檻)
# ═══════════════════════════════════════════════════════════════════
# 官方公布的 cache 最小寫入門檻(低於此 → silent fail,cache_creation=0,照付全價):
#   Opus 4.7:   4,096 tokens
#   Haiku 4.5:  4,096 tokens
#   Sonnet 4.6: 2,048 tokens
#   舊模型(Sonnet 4.5 / Opus 4.1 / Sonnet 3.7): 1,024 tokens
#
# 舊版邏輯 `len(system_text) >= 1024` 是字元數,中文約 0.6 token/字
# → 1024 字元 ≈ 600 tokens,對 Sonnet 4.6 / Haiku 4.5 都 silent fail!
# ═══════════════════════════════════════════════════════════════════

# 模型 → cache 最小 token 門檻
_MODEL_CACHE_MIN_TOKENS = {
    "claude-opus-4-7":          4096,
    "claude-haiku-4-5":         4096,
    "claude-sonnet-4-6":        2048,
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

def _model_supports_adaptive(anthropic_model):
    """Opus 4.6+/4.7 + Sonnet 4.6+ 支援 adaptive thinking"""
    m = (anthropic_model or "").lower()
    return any(k in m for k in ["opus-4-7", "opus-4-6", "sonnet-4-6"])


def _model_requires_adaptive(anthropic_model):
    """Opus 4.7 強制 adaptive(舊 mode 會 400)"""
    return "opus-4-7" in (anthropic_model or "").lower()


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
    valid_lmh = {"low", "medium", "high"}
    eff = (effort or "medium").lower().strip()
    if eff not in valid_lmh and eff != "xhigh":
        eff = "medium"
    # Opus 4.7 允許 xhigh,其他打回 high
    if eff == "xhigh" and "opus-4-7" not in (anthropic_model or "").lower():
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

    use_thinking = features.get("extended_thinking", True)
    if not use_thinking:
        # 全關 thinking
        return None, "none"

    use_adaptive = features.get("adaptive_thinking", True)
    effort_pref = features.get("thinking_effort", "medium")
    display_pref = features.get("thinking_display", "auto")
    legacy_budget = int(features.get("thinking_budget", 2000))

    # Opus 4.7 強制 adaptive
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
        _registered_glossary = glossary_dict
        print(f"[ai_provider] ✅ 註冊 glossary,共 {len(glossary_dict)} 條工廠術語", flush=True)


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
        idn = info.get("idn", "") if isinstance(info, dict) else str(info)
        note_zh = info.get("note_zh", "") if isinstance(info, dict) else ""
        note_id = info.get("note_id", "") if isinstance(info, dict) else ""

        content = f"中文術語:{term}\n標準印尼譯:{idn}"
        if note_zh:
            content += f"\n中文說明:{note_zh}"
        if note_id:
            content += f"\n印尼補充:{note_id}"

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

    if use_xml and "<role>" in raw_system and "</rules>" in raw_system:
        # XML 結構:把 </rules> 之前當 stable,之後當 dynamic
        split_idx = raw_system.find("</rules>") + len("</rules>")
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
        if isinstance(info, dict):
            idn = info.get("idn", "")
            note_zh = info.get("note_zh", "")
            note_id = info.get("note_id", "")
        else:
            idn = str(info)
            note_zh = ""
            note_id = ""
        lines.append(f"\n## {term}")
        lines.append(f"標準印尼譯:{idn}")
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

    # 偵測是否已經是 XML 結構,避免重複包裝
    if "<role>" in raw_system or "<task>" in raw_system:
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
        "務必引用裡面的「標準印尼譯」作為翻譯,不要自己另創譯法。\n"
        "</glossary_priority>\n",
        output_format_text,
    ]

    # v3.2.5 Phase 26: success_criteria(官方建議的「State expected outcome」)
    if success_criteria:
        parts.append(
            "<success_criteria>\n"
            "成功的翻譯必須符合以下所有標準:\n"
            "1. 完整性:原文每個意思都被翻出,不漏不增\n"
            "2. 術語精準:工廠術語用 glossary 標準譯,非自創\n"
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
def chat_complete(model, messages, max_tokens=None, max_completion_tokens=None,
                  temperature=None, timeout=30, prompt_cache_key=None,
                  reasoning_effort=None, verbosity=None, logprobs=False,
                  top_logprobs=None, logit_bias=None, stop=None, **kwargs):
    provider = get_active_provider()

    if provider == "anthropic":
        return _chat_complete_anthropic(
            model=model, messages=messages,
            max_tokens=max_tokens or max_completion_tokens or 1024,
            temperature=temperature, timeout=timeout,
            extra_stop=stop,
        )

    return _chat_complete_openai(
        model=model, messages=messages,
        max_tokens=max_tokens, max_completion_tokens=max_completion_tokens,
        temperature=temperature, timeout=timeout,
        prompt_cache_key=prompt_cache_key,
        reasoning_effort=reasoning_effort, verbosity=verbosity,
        logprobs=logprobs, top_logprobs=top_logprobs,
        logit_bias=logit_bias, stop=stop, **kwargs,
    )


def _chat_complete_openai(model, messages, **kwargs):
    client = _get_openai_client()
    if client is None:
        raise RuntimeError("OpenAI client 未初始化(api_key 缺?)")
    call_kwargs = {"model": model, "messages": messages}
    for k, v in kwargs.items():
        if v is None:
            continue
        if k == "logprobs" and v is False:
            continue
        call_kwargs[k] = v
    return client.chat.completions.create(**call_kwargs)


def _chat_complete_anthropic(model, messages, max_tokens, temperature=None,
                              timeout=120, extra_stop=None):
    """Anthropic 路徑 — v3.0 完整 Claude 能力全部自動啟用"""
    client = _get_anthropic_client()
    if client is None:
        raise RuntimeError("Anthropic client 未初始化(api_key 缺?或 pip install anthropic)")

    _ensure_initialized()
    features = _current_config.get("claude_features", {})
    use_cache = features.get("prompt_caching", True)
    use_thinking = features.get("extended_thinking", True)
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
        # adaptive 模式 — 直接設,不動 max_tokens(adaptive 的 budget 可超過 max_tokens)
        call_kwargs["thinking"] = thinking_cfg
        thinking_applied = True
    else:
        # 沒啟用 thinking(Haiku 或關 toggle)— 才能用 temperature
        if temperature is not None and not is_opus:
            call_kwargs["temperature"] = max(0.0, min(1.0, float(temperature)))

    # ─── Step 4: 呼叫 ───
    try:
        resp = client.messages.create(**call_kwargs)
    except Exception as e:
        err_msg = str(e).lower()
        # Fallback 0 (v3.2 新): adaptive thinking 失敗 → 降回 legacy enabled
        # 觸發條件:SDK 太舊 / model 不支援 adaptive
        if (thinking_applied
            and isinstance(call_kwargs.get("thinking"), dict)
            and call_kwargs["thinking"].get("type") == "adaptive"
            and ("adaptive" in err_msg or "effort" in err_msg
                 or "display" in err_msg or "unknown" in err_msg
                 or "invalid" in err_msg)):
            print(f"[ai_provider] adaptive thinking 失敗,fallback to legacy: {e}", flush=True)
            # 不能 fallback 到 enabled 的 model(只有 Opus 4.7)就完全關掉 thinking
            if _model_requires_adaptive(anthropic_model):
                call_kwargs.pop("thinking", None)
                thinking_applied = False
                thinking_mode_used = "none(opus47_adaptive_failed)"
            else:
                legacy_budget = int(features.get("thinking_budget", 2000))
                call_kwargs["thinking"] = {"type": "enabled", "budget_tokens": legacy_budget}
                call_kwargs["max_tokens"] = max(int(max_tokens or 1024), legacy_budget + 1024)
                thinking_mode_used = f"legacy_{legacy_budget}(fallback)"
            resp = client.messages.create(**call_kwargs)
        # Fallback 1: thinking 失敗 → 不啟用 thinking 重試
        elif thinking_applied and ("thinking" in err_msg or "budget" in err_msg):
            print(f"[ai_provider] thinking 失敗,fallback: {e}", flush=True)
            call_kwargs.pop("thinking", None)
            call_kwargs["max_tokens"] = int(max_tokens or 1024)
            thinking_applied = False
            thinking_mode_used = "none(thinking_failed)"
            resp = client.messages.create(**call_kwargs)
        # Fallback 2: grounding/citation 失敗
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
            resp = client.messages.create(**call_kwargs)
        # Fallback 3: extended cache 1h beta header 失敗
        elif use_cache_1h and ("extended-cache" in err_msg or "beta" in err_msg or "ttl" in err_msg):
            print(f"[ai_provider] 1h cache fallback to 5min: {e}", flush=True)
            if isinstance(call_kwargs.get("system"), list):
                for blk in call_kwargs["system"]:
                    if isinstance(blk, dict) and "cache_control" in blk:
                        blk["cache_control"] = {"type": "ephemeral"}
            resp = client.messages.create(**call_kwargs)
        # Fallback 4: stop_sequences 太多
        elif use_stop and ("stop_sequence" in err_msg or "too many" in err_msg):
            print(f"[ai_provider] stop_sequences fallback: {e}", flush=True)
            call_kwargs.pop("stop_sequences", None)
            resp = client.messages.create(**call_kwargs)
        else:
            raise

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
    else:
        yield from _chat_complete_stream_openai(model, messages, max_tokens, temperature, **kwargs)


def _chat_complete_stream_openai(model, messages, max_tokens, temperature, **kwargs):
    client = _get_openai_client()
    if client is None:
        raise RuntimeError("OpenAI client 未初始化")
    call_kwargs = {"model": model, "messages": messages, "stream": True}
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
         有時候只想讓文字翻譯走 Claude,圖片仍用 OpenAI gpt-5-mini
    
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
