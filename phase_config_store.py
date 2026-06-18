"""
phase_config_store.py — unified phase configuration persistence v2.0

All backend phase toggles are stored in:
1. Upstash Redis when configured (primary cloud copy)
2. GitHub ``data`` branch (cloud backup)
3. local/persistent disk cache (fallback)

The newest timestamped copy wins on startup.  Existing v1 files containing a
plain ``{phase_name: config}`` mapping are migrated transparently.
"""

import base64
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_PATH: Optional[str] = None
_cache: Dict[str, Any] = {}
_loaded = False
_load_source = "none"
_last_save_status: Dict[str, Any] = {
    "ok": False,
    "cloud_ok": False,
    "local": None,
    "upstash": None,
    "github": None,
    "error": "not_saved_yet",
}

_FILE_NAME = "phase_config.json"
_GITHUB_BRANCH = "data"
_GITHUB_REPO = os.environ.get("GITHUB_REPO", "onerkk/line-translator-bot").strip() or "onerkk/line-translator-bot"
_GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
_UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
_UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()
_KV_KEY = os.environ.get("PHASE_CONFIG_KV_KEY", "line_bot:phase_config:v2").strip() or "line_bot:phase_config:v2"
_REQUIRE_CLOUD = os.environ.get("REQUIRE_CLOUD_SETTINGS", "1").strip().lower() not in ("0", "false", "no", "off")


def _resolve_path() -> str:
    env = os.environ.get("PHASE_CONFIG_PATH", "").strip()
    if env:
        return env
    for directory in ("/var/data", "/data", "/tmp"):
        if os.path.isdir(directory) and os.access(directory, os.W_OK):
            return os.path.join(directory, _FILE_NAME)
    return _FILE_NAME


def _parse_document(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("phases"), dict):
        return raw
    # v1 compatibility: the whole document was the phase mapping.
    return {"_meta": {}, "phases": raw}


def _updated_at(document: Dict[str, Any]) -> float:
    try:
        return float((document.get("_meta") or {}).get("updated_at_unix") or 0)
    except Exception:
        return 0.0


def _read_local(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return _parse_document(json.load(handle))
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("[PhaseCfg] local load failed: %s", exc)
        return None


def _write_local(path: str, text: str) -> bool:
    try:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        tmp = "%s.tmp.%s.%s" % (path, os.getpid(), threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
        return True
    except Exception as exc:
        logger.error("[PhaseCfg] local save failed: %s", exc)
        try:
            if 'tmp' in locals() and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def _kv_enabled() -> bool:
    return bool(_UPSTASH_URL and _UPSTASH_TOKEN)


def _kv_command(args, timeout: int = 8):
    if not _kv_enabled():
        return None
    try:
        request = urllib.request.Request(
            _UPSTASH_URL,
            data=json.dumps(args).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + _UPSTASH_TOKEN,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")).get("result")
    except Exception as exc:
        logger.warning("[PhaseCfg] Upstash %s failed: %s", args[0] if args else "?", exc)
        return None


def _github_load() -> Optional[Dict[str, Any]]:
    if not _GITHUB_TOKEN:
        return None
    try:
        url = "https://api.github.com/repos/%s/contents/%s?ref=%s" % (
            _GITHUB_REPO, _FILE_NAME, _GITHUB_BRANCH)
        request = urllib.request.Request(url, headers={
            "Authorization": "token " + _GITHUB_TOKEN,
            "Accept": "application/vnd.github.v3+json",
        })
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = base64.b64decode(payload["content"]).decode("utf-8")
        return _parse_document(content)
    except Exception as exc:
        logger.warning("[PhaseCfg] GitHub load failed: %s", exc)
        return None


def _github_save(text: str) -> bool:
    if not _GITHUB_TOKEN:
        return False
    url = "https://api.github.com/repos/%s/contents/%s" % (_GITHUB_REPO, _FILE_NAME)
    for attempt in range(3):
        try:
            sha = None
            get_request = urllib.request.Request(url + "?ref=" + _GITHUB_BRANCH, headers={
                "Authorization": "token " + _GITHUB_TOKEN,
                "Accept": "application/vnd.github.v3+json",
            })
            try:
                with urllib.request.urlopen(get_request, timeout=8) as response:
                    sha = json.loads(response.read().decode("utf-8")).get("sha")
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise

            body = {
                "message": "Persist backend phase settings",
                "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
                "branch": _GITHUB_BRANCH,
            }
            if sha:
                body["sha"] = sha
            put_request = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                method="PUT",
                headers={
                    "Authorization": "token " + _GITHUB_TOKEN,
                    "Accept": "application/vnd.github.v3+json",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(put_request, timeout=10):
                return True
        except urllib.error.HTTPError as exc:
            if exc.code in (409, 502, 503, 504) and attempt < 2:
                time.sleep(0.3 * (attempt + 1))
                continue
            logger.error("[PhaseCfg] GitHub save HTTP %s", exc.code)
            return False
        except Exception as exc:
            if attempt < 2:
                time.sleep(0.3 * (attempt + 1))
                continue
            logger.error("[PhaseCfg] GitHub save failed: %s", exc)
            return False
    return False


def _init_once():
    global _PATH, _cache, _loaded, _load_source
    if _loaded:
        return
    _PATH = _resolve_path()
    candidates = []

    if _kv_enabled():
        try:
            document = _parse_document(_kv_command(["GET", _KV_KEY]))
            if document:
                candidates.append(("upstash", document, 3))
        except Exception as exc:
            logger.warning("[PhaseCfg] Upstash parse failed: %s", exc)

    document = _github_load()
    if document:
        candidates.append(("github", document, 2))

    document = _read_local(_PATH)
    if document:
        candidates.append(("local", document, 1))

    if candidates:
        source, newest, _ = max(candidates, key=lambda item: (_updated_at(item[1]), item[2]))
        phases = newest.get("phases") or {}
        _cache = dict(phases) if isinstance(phases, dict) else {}
        _load_source = source
        logger.info("[PhaseCfg] loaded %d phases from %s", len(_cache), source)
    else:
        _cache = {}
        _load_source = "none"
    _loaded = True


def _persist_all() -> bool:
    global _last_save_status
    now = time.time()
    document = {
        "_meta": {
            "schema_version": 2,
            "updated_at_unix": now,
            "updated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        },
        "phases": _cache,
    }
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    status = {
        "ok": False,
        "cloud_ok": False,
        "local": _write_local(_PATH or _resolve_path(), text),
        "upstash": None,
        "github": None,
        "error": None,
    }
    if _kv_enabled():
        status["upstash"] = _kv_command(["SET", _KV_KEY, text]) in ("OK", True, 1)
    if _GITHUB_TOKEN:
        status["github"] = _github_save(text)
    status["cloud_ok"] = bool(status.get("upstash") or status.get("github"))
    cloud_configured = bool(_kv_enabled() or _GITHUB_TOKEN)
    if _REQUIRE_CLOUD:
        status["ok"] = status["cloud_ok"]
        if not cloud_configured:
            status["error"] = "no_cloud_backend_configured"
        elif not status["cloud_ok"]:
            status["error"] = "cloud_write_failed"
    else:
        status["ok"] = bool(status["local"] or status["cloud_ok"])
        if not status["ok"]:
            status["error"] = "all_writes_failed"
    _last_save_status = status
    # Let the Flask admin response surface cloud persistence failures instead
    # of returning a misleading {ok:true}. Dynamic import keeps this module
    # usable outside Flask and avoids an app import cycle.
    try:
        from flask import g, has_request_context
        if has_request_context():
            g.settings_persist_attempted = True
            g.settings_persist_ok = bool(status["ok"])
            g.settings_persist_status = dict(status)
    except Exception:
        pass
    if not status["ok"]:
        logger.error("[PhaseCfg] persistence incomplete: %s", status)
    return bool(status["ok"])


def load_config(phase_name: str) -> Dict[str, Any]:
    """Return a copy of one phase's persisted configuration."""
    with _lock:
        _init_once()
        value = _cache.get(phase_name, {})
        return dict(value) if isinstance(value, dict) else {}


def save_config(phase_name: str, cfg: Dict[str, Any]) -> bool:
    """Persist one phase and synchronously confirm a cloud write."""
    with _lock:
        _init_once()
        clean = {}
        for key, value in (cfg or {}).items():
            try:
                json.dumps(value)
                clean[key] = value
            except Exception:
                continue
        _cache[phase_name] = clean
        return _persist_all()


def load_all() -> Dict[str, Any]:
    with _lock:
        _init_once()
        return json.loads(json.dumps(_cache))


def get_path() -> str:
    _init_once()
    return _PATH or ""


def get_status() -> Dict[str, Any]:
    _init_once()
    return {
        "load_source": _load_source,
        "path": _PATH,
        "cloud_configured": bool(_kv_enabled() or _GITHUB_TOKEN),
        "last_save": dict(_last_save_status),
    }
