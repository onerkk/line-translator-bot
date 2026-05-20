"""
phase_config_store.py — 統一 phase config 持久化 v1.0 (2026-05-20)

問題:Phase H/N/Q/D/E/C 等模組的 config 都是 module-level globals,
Render 重啟就歸零。需要持久化到 /var/data/phase_config.json。

設計:
- 每個 phase 模組 init 時 call load_config(phase_name) 拿之前的 config dict
- 每次 set_config() 時 call save_config(phase_name, cfg) 寫回
- JSON 格式,人工可讀可改
- File lock 避免 multi-worker race

用法(模組內):
    import phase_config_store as pcs
    # init 時
    _saved = pcs.load_config("ge")
    if _saved:
        GE_ENABLED = _saved.get("enabled", GE_ENABLED)
        GE_ACTION = _saved.get("action", GE_ACTION)
    # set_config 時
    pcs.save_config("ge", {"enabled": GE_ENABLED, "action": GE_ACTION, ...})
"""

import os
import json
import threading
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_PATH: Optional[str] = None
_cache: Dict[str, Any] = {}
_loaded = False


def _resolve_path() -> str:
    env = os.environ.get("PHASE_CONFIG_PATH", "").strip()
    if env:
        return env
    for d in ("/var/data", "/data", "/tmp"):
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return os.path.join(d, "phase_config.json")
    return "phase_config.json"


def _init_once():
    global _PATH, _cache, _loaded
    if _loaded:
        return
    _PATH = _resolve_path()
    try:
        if os.path.exists(_PATH):
            with open(_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
            logger.info("[PhaseCfg] loaded %d phases from %s", len(_cache), _PATH)
        else:
            _cache = {}
    except Exception as e:
        logger.warning("[PhaseCfg] init load failed: %s", e)
        _cache = {}
    _loaded = True


def load_config(phase_name: str) -> Dict[str, Any]:
    """讀某 phase 的持久化 config(若無回空 dict)"""
    with _lock:
        _init_once()
        return dict(_cache.get(phase_name, {}))


def save_config(phase_name: str, cfg: Dict[str, Any]) -> bool:
    """寫某 phase 的 config 到磁碟"""
    with _lock:
        _init_once()
        # 只存可 JSON 化的 value
        clean = {}
        for k, v in cfg.items():
            try:
                json.dumps(v)
                clean[k] = v
            except Exception:
                continue
        _cache[phase_name] = clean
        try:
            tmp = _PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_cache, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _PATH)
            return True
        except Exception as e:
            logger.error("[PhaseCfg] save failed for %s: %s", phase_name, e)
            return False


def load_all() -> Dict[str, Any]:
    with _lock:
        _init_once()
        return dict(_cache)


def get_path() -> str:
    _init_once()
    return _PATH or ""
