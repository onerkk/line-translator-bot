"""
ai_provider.py — 統一 AI Provider 介面層 (v2.0 / 2026-05-15)

【v2.0 新增 — Claude 專屬能力(active=anthropic 時自動啟用)】
✅ Phase 1: Prompt Caching — system / glossary 自動 cache,輸入成本降 70-90%
✅ Phase 2: Extended Thinking — Sonnet/Opus 啟用思考鏈(budget 2000 tokens)
✅ Phase 3: Search Result Grounding — 自動把 LINE bot 的 glossary 包成 source blocks
              強迫 Claude 引用工廠術語,翻譯遵循度提升

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
    "openai": {
        "api_key": "",
        "base_url": None,
    },
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
        "o1":                  "claude-opus-4-7",
        "o3":                  "claude-opus-4-7",
        "o4-mini":             "claude-haiku-4-5-20251001",
    },
    # === v2.0 Claude 專屬能力 ===
    "claude_features": {
        "prompt_caching": True,
        "extended_thinking": True,
        "thinking_budget": 2000,
        "glossary_grounding": True,
        "glossary_max_items": 50,
    },
    "last_updated": "",
}

_config_lock = threading.RLock()
_current_config = None
_openai_client = None
_anthropic_client = None
_registered_glossary = None


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
# Client
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
            _anthropic_client = Anthropic(api_key=api_key, timeout=60.0)
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
        # 加上 glossary 註冊狀態
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
# Glossary 注入(Phase 3)
# ═══════════════════════════════════════════════════════════════════
def register_glossary(glossary_dict):
    """app.py 啟動時呼叫,把 GLOSSARY_LOOKUP 註冊給 ai_provider"""
    global _registered_glossary
    if isinstance(glossary_dict, dict):
        _registered_glossary = glossary_dict
        print(f"[ai_provider] ✅ 註冊 glossary,共 {len(glossary_dict)} 條工廠術語", flush=True)


def _find_relevant_glossary_terms(messages, max_items=50):
    """掃 messages,只注入有出現的術語"""
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


def _build_glossary_search_results(matched_terms):
    """轉成 Anthropic search_result blocks"""
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
            "citations": {"enabled": True},
        })
    return blocks


# ═══════════════════════════════════════════════════════════════════
# Unified Response classes
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
    def __init__(self, content, model, usage=None, finish_reason="stop", logprobs=None):
        self.choices = [_UnifiedChoice(content, finish_reason, logprobs)]
        self.model = model
        self.usage = usage or _UnifiedUsage()
        self._jy_provider = get_active_provider()


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


def _chat_complete_anthropic(model, messages, max_tokens, temperature=None, timeout=60):
    """Anthropic 路徑 + v2.0 Claude 專屬能力自動啟用"""
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

    # 連續 same-role 合併
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
            grounding_blocks = _build_glossary_search_results(matched)
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

    # Phase 1 — Prompt Caching
    caching_applied = False
    if system_text:
        if use_cache and len(system_text) >= 1024:
            call_kwargs["system"] = [{
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"}
            }]
            caching_applied = True
        else:
            call_kwargs["system"] = system_text

    # Phase 2 — Extended Thinking(Sonnet/Opus only)
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
            print(f"[ai_provider] thinking 失敗,fallback 不啟用: {e}", flush=True)
            call_kwargs.pop("thinking", None)
            call_kwargs["max_tokens"] = int(max_tokens or 1024)
            thinking_applied = False
            resp = client.messages.create(**call_kwargs)
        # Fallback 2: search_result 失敗 → 移除 grounding 重試
        elif grounding_used and ("search_result" in err_msg or "citation" in err_msg or "content block" in err_msg):
            print(f"[ai_provider] grounding 失敗,fallback 移除: {e}", flush=True)
            # 重組 messages 移除 grounding blocks
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
        else:
            raise

    # ─── Step 5: 抽出文字 ───
    full_text = ""
    if resp.content:
        for block in resp.content:
            if hasattr(block, "type"):
                if block.type == "text" and hasattr(block, "text"):
                    full_text += block.text or ""
                # thinking block 不抽出
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    full_text += block.get("text", "")

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
    )

    # debug 標記
    result._jy_claude_features_used = {
        "caching": caching_applied,
        "thinking": thinking_applied,
        "grounding": grounding_used,
        "grounding_terms_count": matched_terms_count,
    }
    return result


def _convert_openai_content_blocks_to_anthropic(blocks):
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
                    result.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64data}
                    })
                except Exception:
                    result.append({"type": "text", "text": "[image decode error]"})
            else:
                result.append({
                    "type": "image",
                    "source": {"type": "url", "url": url}
                })
        else:
            result.append({"type": "text", "text": f"[unsupported block type: {btype}]"})
    return result


# ═══════════════════════════════════════════════════════════════════
# 不支援功能 fallback
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
    }
    return table.get(capability, {}).get(provider, False)


# ═══════════════════════════════════════════════════════════════════
# 模組啟動
# ═══════════════════════════════════════════════════════════════════
_init_config()

if __name__ == "__main__":
    print("Active provider:", get_active_provider())
    print("Config:", json.dumps(get_current_config_safe(), ensure_ascii=False, indent=2))
