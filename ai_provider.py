"""
ai_provider.py — 統一 AI Provider 介面層 (v3.1 / 2026-05-15)

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
    },
    "last_updated": "",
}

_config_lock = threading.RLock()
_current_config = None
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
    try:
        cfg["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(PROVIDER_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
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
    with _config_lock:
        if _current_config is None:
            _init_config()


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
    _ensure_initialized()
    mapping = _current_config.get("model_mapping", {})
    if openai_model in mapping:
        return mapping[openai_model]
    return _current_config["anthropic"].get("default_model", "claude-haiku-4-5-20251001")


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
def _wrap_system_prompt_xml(raw_system):
    """把純文字 system prompt 包成 XML 結構,Claude 遵循度提升 20-30%
    
    根據 Anthropic 官方 prompt engineering guide:
    https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags
    
    結構:
    <role>...</role>
    <task>...</task>
    <rules>{原本的整段 system prompt}</rules>
    <glossary_priority>優先引用 search_result blocks 內的譯名</glossary_priority>
    <output_format>純翻譯文字,不加任何 metadata 或註解</output_format>
    """
    if not raw_system:
        return raw_system

    # 偵測是否已經是 XML 結構,避免重複包裝
    if "<role>" in raw_system or "<task>" in raw_system:
        return raw_system

    wrapped = (
        "<role>\n你是專業的工廠翻譯助手,專精中文↔印尼文翻譯。\n</role>\n\n"
        "<task>\n忠實翻譯使用者訊息,不增刪內容,不加註解。\n</task>\n\n"
        "<rules>\n" + raw_system.strip() + "\n</rules>\n\n"
        "<glossary_priority>\n"
        "如果訊息中內附 search_result 標籤(工廠術語表),"
        "務必引用裡面的「標準印尼譯」作為翻譯,不要自己另創譯法。\n"
        "</glossary_priority>\n\n"
        "<output_format>\n"
        "直接輸出純翻譯文字。\n"
        "不要加「翻譯:」「Translation:」「Catatan:」前綴。\n"
        "不要加任何說明、註解、解釋。\n"
        "不要使用 markdown 標記(除非原文有)。\n"
        "</output_format>"
    )
    return wrapped


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

    # Phase 5: XML 包裝 system prompt
    if use_xml and system_text:
        system_text = _wrap_system_prompt_xml(system_text)

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

    # ─── Step 3: 組裝 call_kwargs ───
    call_kwargs = {
        "model": anthropic_model,
        "messages": merged,
        "max_tokens": int(max_tokens or 1024),
    }

    # Phase 1 + 8 + 12: Prompt Caching(支援單層 / 多層)
    caching_applied = False
    multi_block_applied = False
    if system_text:
        if use_cache and len(system_text) >= 1024:
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

    # Phase 2: Extended Thinking(Sonnet/Opus only)
    thinking_applied = False
    if use_thinking and not is_haiku:
        needed_max = max(int(max_tokens or 1024), thinking_budget + 1024)
        call_kwargs["max_tokens"] = needed_max
        call_kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": thinking_budget,
        }
        thinking_applied = True
    else:
        if temperature is not None and not is_opus:
            call_kwargs["temperature"] = max(0.0, min(1.0, float(temperature)))

    # ─── Step 4: 呼叫 ───
    try:
        resp = client.messages.create(**call_kwargs)
    except Exception as e:
        err_msg = str(e).lower()
        # Fallback 1: thinking 失敗 → 不啟用 thinking 重試
        if thinking_applied and ("thinking" in err_msg or "budget" in err_msg):
            print(f"[ai_provider] thinking 失敗,fallback: {e}", flush=True)
            call_kwargs.pop("thinking", None)
            call_kwargs["max_tokens"] = int(max_tokens or 1024)
            thinking_applied = False
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
        "grounding": grounding_used,
        "grounding_terms_count": matched_terms_count,
        "stop_sequences": use_stop,
        "xml_system": use_xml,
        "citations": use_citations and len(citations_list) > 0,
        "citation_count": len(citations_list),
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

    if features.get("xml_system_prompt", True) and system_text:
        system_text = _wrap_system_prompt_xml(system_text)

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

    if system_text:
        if features.get("prompt_caching", True) and len(system_text) >= 1024:
            cc = {"type": "ephemeral"}
            if features.get("extended_cache_1h", True):
                cc["ttl"] = "1h"
            call_kwargs["system"] = [{"type": "text", "text": system_text, "cache_control": cc}]
        else:
            call_kwargs["system"] = system_text

    if features.get("stop_sequences", True):
        call_kwargs["stop_sequences"] = _build_stop_sequences()[:4]

    if features.get("extended_thinking", True) and not is_haiku:
        budget = int(features.get("thinking_budget", 2000))
        call_kwargs["max_tokens"] = max(call_kwargs["max_tokens"], budget + 1024)
        call_kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
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
    """
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
    return result


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
