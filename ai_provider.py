"""
ai_provider.py — 統一 AI Provider 介面層 (v1.0 / 2026-05-15)

【設計目標】
讓 app.py 不需大改,後台可即時切換 OpenAI ↔ Anthropic provider。

【核心架構】
- 後台寫 provider_config.json
- ai_provider 啟動時讀 config + 環境變數,建立兩家 client
- chat_complete() 統一介面,內部判斷 active provider 走哪邊
- 自動處理:
    * API 格式轉換 (messages / system / max_tokens / temperature)
    * 回傳格式轉換 (回傳 OpenAI-compatible 物件,app.py 不用改解析)
    * 不支援功能 fallback (Anthropic 沒語音 → 拋明確錯誤)

【支援功能對比】
                       OpenAI    Anthropic
chat completions       ✅        ✅
vision (image input)   ✅        ✅
prompt caching         ✅        ✅
logprobs               ✅        ❌ (Anthropic 不支援)
audio transcription    ✅        ❌ (Anthropic 沒這個 API)
predicted outputs      ✅        ❌

【使用方式】
from ai_provider import chat_complete, get_active_provider, set_active_provider

response = chat_complete(
    model="gpt-4.1-mini",        # OpenAI 模型名,自動轉 Anthropic 對應模型
    messages=[{"role": "user", "content": "翻譯成印尼文: 你好"}],
    max_tokens=500,
    temperature=0.3,
)
# response.choices[0].message.content 可以直接用,跟 OpenAI 一致

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
    """設定檔放在持久化磁碟,Render 重啟不會掉"""
    for d in ("/var/data", "/data", "/tmp"):
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return os.path.join(d, "ai_provider_config.json")
    return "ai_provider_config.json"

PROVIDER_CONFIG_PATH = _resolve_provider_config_path()

# ═══════════════════════════════════════════════════════════════════
# 預設配置(env 啟動時的 fallback)
# ═══════════════════════════════════════════════════════════════════
DEFAULT_CONFIG = {
    "active_provider": "openai",  # "openai" or "anthropic"
    "openai": {
        "api_key": "",            # 從環境變數讀,後台可覆寫
        "base_url": None,
    },
    "anthropic": {
        "api_key": "",
        "default_model": "claude-haiku-4-5-20251001",  # Anthropic 預設模型
    },
    # 模型映射:OpenAI 模型名 → Anthropic 對應模型
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
    "last_updated": "",
}

_config_lock = threading.RLock()
_current_config = None
_openai_client = None
_anthropic_client = None


# ═══════════════════════════════════════════════════════════════════
# Config 讀寫
# ═══════════════════════════════════════════════════════════════════
def _load_config_from_disk():
    """從磁碟讀 config,不存在則用 DEFAULT_CONFIG"""
    try:
        if os.path.exists(PROVIDER_CONFIG_PATH):
            with open(PROVIDER_CONFIG_PATH, "r", encoding="utf-8") as f:
                disk_cfg = json.load(f)
            # 跟 DEFAULT_CONFIG 合併,確保缺漏欄位有預設
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
        print(f"[ai_provider] WARN: 讀 config 失敗 {e},用預設值", flush=True)
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
    """啟動時初始化:磁碟 config + 環境變數 fallback"""
    global _current_config
    cfg = _load_config_from_disk()
    # 環境變數補位:後台沒設過 key 時用 env
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
# Client 建立(lazy init)
# ═══════════════════════════════════════════════════════════════════
def _get_openai_client():
    global _openai_client
    _ensure_initialized()
    with _config_lock:
        api_key = _current_config["openai"].get("api_key", "")
        if not api_key:
            return None
        # 若 client 已存在,但 key 不同了 → 重建
        if _openai_client is not None and getattr(_openai_client, "_jy_key", None) == api_key:
            return _openai_client
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=api_key, timeout=30.0)
            _openai_client._jy_key = api_key  # tag 起來方便對比
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
            print(f"[ai_provider] Anthropic client 建立失敗 {e},確認已 pip install anthropic", flush=True)
            return None


# ═══════════════════════════════════════════════════════════════════
# 對外 API
# ═══════════════════════════════════════════════════════════════════
def get_active_provider():
    """回傳目前 active provider 字串 'openai' or 'anthropic'"""
    _ensure_initialized()
    return _current_config.get("active_provider", "openai")


def set_active_provider(provider):
    """切換 active provider"""
    if provider not in ("openai", "anthropic"):
        return False, f"unknown provider: {provider}"
    _ensure_initialized()
    with _config_lock:
        _current_config["active_provider"] = provider
        if _save_config_to_disk(_current_config):
            return True, f"切換到 {provider}"
        return False, "存檔失敗"


def update_provider_key(provider, api_key):
    """後台更新 key"""
    if provider not in ("openai", "anthropic"):
        return False, f"unknown provider: {provider}"
    _ensure_initialized()
    with _config_lock:
        _current_config[provider]["api_key"] = (api_key or "").strip()
        if _save_config_to_disk(_current_config):
            # 強制下次重建 client
            global _openai_client, _anthropic_client
            if provider == "openai":
                _openai_client = None
            else:
                _anthropic_client = None
            return True, f"{provider} key 已更新"
        return False, "存檔失敗"


def update_model_mapping(mapping):
    """後台更新 OpenAI→Anthropic 模型映射表"""
    if not isinstance(mapping, dict):
        return False, "mapping 必須是 dict"
    _ensure_initialized()
    with _config_lock:
        _current_config["model_mapping"] = mapping
        if _save_config_to_disk(_current_config):
            return True, "model mapping 已更新"
        return False, "存檔失敗"


def get_current_config_safe():
    """回傳目前 config(API key 脫敏,給後台顯示用)"""
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
        return cfg


def _resolve_anthropic_model(openai_model):
    """把 OpenAI 模型名映射到 Anthropic 對應模型"""
    _ensure_initialized()
    mapping = _current_config.get("model_mapping", {})
    if openai_model in mapping:
        return mapping[openai_model]
    return _current_config["anthropic"].get("default_model", "claude-haiku-4-5-20251001")


# ═══════════════════════════════════════════════════════════════════
# 核心:chat_complete 統一介面
# ═══════════════════════════════════════════════════════════════════
class _UnifiedMessage:
    """模擬 OpenAI response.choices[0].message"""
    def __init__(self, content, role="assistant"):
        self.content = content
        self.role = role


class _UnifiedChoice:
    """模擬 OpenAI response.choices[0]"""
    def __init__(self, content, finish_reason="stop", logprobs=None):
        self.message = _UnifiedMessage(content)
        self.finish_reason = finish_reason
        self.logprobs = logprobs
        self.index = 0


class _UnifiedUsage:
    """模擬 OpenAI response.usage"""
    def __init__(self, prompt_tokens=0, completion_tokens=0, total_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class _UnifiedResponse:
    """模擬 OpenAI ChatCompletion response,讓 app.py 不用改解析"""
    def __init__(self, content, model, usage=None, finish_reason="stop", logprobs=None):
        self.choices = [_UnifiedChoice(content, finish_reason, logprobs)]
        self.model = model
        self.usage = usage or _UnifiedUsage()
        # 加標記讓 app.py 內 track_tokens 等可辨識來源
        self._jy_provider = get_active_provider()


def chat_complete(model, messages, max_tokens=None, max_completion_tokens=None,
                  temperature=None, timeout=30, prompt_cache_key=None,
                  reasoning_effort=None, verbosity=None, logprobs=False,
                  top_logprobs=None, logit_bias=None, stop=None, **kwargs):
    """
    統一 chat completion 介面。Drop-in replacement for `oai.chat.completions.create()`.

    依目前 active_provider 自動分流:
    - openai → 走 OpenAI SDK,完整保留所有參數
    - anthropic → 走 Anthropic SDK,自動轉換格式,不支援的參數忽略
    """
    provider = get_active_provider()

    if provider == "anthropic":
        return _chat_complete_anthropic(
            model=model, messages=messages,
            max_tokens=max_tokens or max_completion_tokens or 1024,
            temperature=temperature, timeout=timeout,
        )

    # default → OpenAI
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
    """OpenAI 路徑:原樣丟給 SDK"""
    client = _get_openai_client()
    if client is None:
        raise RuntimeError("OpenAI client 未初始化(api_key 缺?)")

    # 清掉 None 值的 kwargs,避免 SDK 報錯
    call_kwargs = {"model": model, "messages": messages}
    for k, v in kwargs.items():
        if v is None:
            continue
        if k == "logprobs" and v is False:
            continue
        call_kwargs[k] = v

    return client.chat.completions.create(**call_kwargs)


def _chat_complete_anthropic(model, messages, max_tokens, temperature=None, timeout=60):
    """
    Anthropic 路徑:轉格式 + 包成 OpenAI-compatible response

    格式差異:
    - OpenAI: [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
    - Anthropic: system=<str>, messages=[{"role": "user", "content": "..."}]
                 (system 是獨立參數,不在 messages 內)
    """
    client = _get_anthropic_client()
    if client is None:
        raise RuntimeError("Anthropic client 未初始化(api_key 缺?或 pip install anthropic)")

    # 模型名稱映射(OpenAI 名 → Anthropic 名)
    anthropic_model = _resolve_anthropic_model(model)

    # 訊息格式轉換
    system_text = ""
    anthropic_messages = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            # OpenAI 多個 system 用換行串接,Anthropic 只支援單一 system
            if system_text:
                system_text += "\n\n" + (content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
            else:
                system_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        elif role in ("user", "assistant"):
            # content 可能是 string 或 list (multi-modal),Anthropic 也支援 list 但格式略不同
            if isinstance(content, list):
                # multi-modal:轉成 Anthropic content blocks
                anthropic_content = _convert_openai_content_blocks_to_anthropic(content)
                anthropic_messages.append({"role": role, "content": anthropic_content})
            else:
                anthropic_messages.append({"role": role, "content": str(content)})

    # 連續 user message 合併(Anthropic 要求 user/assistant 交替)
    merged = []
    for m in anthropic_messages:
        if merged and merged[-1]["role"] == m["role"]:
            # 合併
            prev = merged[-1]
            if isinstance(prev["content"], str) and isinstance(m["content"], str):
                prev["content"] = prev["content"] + "\n\n" + m["content"]
            else:
                # list 模式比較複雜,簡化:轉 string
                a = prev["content"] if isinstance(prev["content"], str) else json.dumps(prev["content"], ensure_ascii=False)
                b = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"], ensure_ascii=False)
                prev["content"] = a + "\n\n" + b
        else:
            merged.append(m)

    # 組裝 Anthropic API call
    call_kwargs = {
        "model": anthropic_model,
        "messages": merged,
        "max_tokens": int(max_tokens or 1024),
    }
    if system_text:
        call_kwargs["system"] = system_text
    if temperature is not None:
        # Opus 4.7 不支援 temperature(Anthropic 規定),自動跳過
        if "opus-4-7" not in anthropic_model:
            call_kwargs["temperature"] = max(0.0, min(1.0, float(temperature)))

    # 實際呼叫
    resp = client.messages.create(**call_kwargs)

    # 抽出文字內容(Anthropic 回的是 content blocks)
    full_text = ""
    if resp.content:
        for block in resp.content:
            if hasattr(block, "text") and block.text:
                full_text += block.text
            elif isinstance(block, dict) and block.get("type") == "text":
                full_text += block.get("text", "")

    # 組裝 OpenAI-compatible 回應
    usage = _UnifiedUsage(
        prompt_tokens=getattr(resp.usage, "input_tokens", 0) if resp.usage else 0,
        completion_tokens=getattr(resp.usage, "output_tokens", 0) if resp.usage else 0,
    )
    usage.total_tokens = usage.prompt_tokens + usage.completion_tokens

    return _UnifiedResponse(
        content=full_text,
        model=anthropic_model,
        usage=usage,
        finish_reason=getattr(resp, "stop_reason", "stop") or "stop",
        logprobs=None,  # Anthropic 不支援
    )


def _convert_openai_content_blocks_to_anthropic(blocks):
    """OpenAI 的 content list (multi-modal) → Anthropic content list

    OpenAI:
      [{"type": "text", "text": "..."},
       {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]

    Anthropic:
      [{"type": "text", "text": "..."},
       {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}]
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
                # data:image/png;base64,XXX
                try:
                    header, b64data = url.split(",", 1)
                    # data:image/png;base64
                    media_type = header.split(";")[0].replace("data:", "") or "image/png"
                    result.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64data}
                    })
                except Exception:
                    result.append({"type": "text", "text": "[image decode error]"})
            else:
                # URL 模式 Anthropic 也支援
                result.append({
                    "type": "image",
                    "source": {"type": "url", "url": url}
                })
        else:
            # 其他類型(audio 等)直接降級為文字提示
            result.append({"type": "text", "text": f"[unsupported block type: {btype}]"})
    return result


# ═══════════════════════════════════════════════════════════════════
# 不支援功能的 fallback 函式
# ═══════════════════════════════════════════════════════════════════
def audio_transcribe(audio_file_path, model="gpt-4o-transcribe", **kwargs):
    """
    音訊轉文字。Anthropic 沒這個 API,active=anthropic 時拋 NotImplementedError。
    app.py 內呼叫處要 catch 這個例外,改用 LINE 內建語音 / 回覆「請打字」。
    """
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
    """
    查詢目前 provider 是否支援某個能力。app.py 內可在啟用前 query 一下。
    capabilities:
      - "audio_transcribe"  : Anthropic 不支援
      - "logprobs"          : Anthropic 不支援
      - "predicted_outputs" : Anthropic 不支援
      - "vision"            : 兩家都支援
      - "prompt_cache"      : 兩家都支援
    """
    provider = get_active_provider()
    table = {
        "audio_transcribe": {"openai": True, "anthropic": False},
        "logprobs":         {"openai": True, "anthropic": False},
        "predicted_outputs": {"openai": True, "anthropic": False},
        "vision":           {"openai": True, "anthropic": True},
        "prompt_cache":     {"openai": True, "anthropic": True},
        "chat":             {"openai": True, "anthropic": True},
    }
    return table.get(capability, {}).get(provider, False)


# ═══════════════════════════════════════════════════════════════════
# 模組啟動
# ═══════════════════════════════════════════════════════════════════
_init_config()

if __name__ == "__main__":
    # 簡易自我測試
    print("Active provider:", get_active_provider())
    print("Config (safe):", json.dumps(get_current_config_safe(), ensure_ascii=False, indent=2))
