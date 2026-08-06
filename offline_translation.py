"""Optional local translation fallbacks.

This module never depends on a public internet service.  It supports two local
routes when operators provision them:

1. a LibreTranslate-compatible HTTP endpoint on the private network;
2. installed Argos Translate language packages.

The application treats these as availability fallbacks after its primary LLM
and cloud-NMT routes.  Missing local components are a normal no-op, not an
exception.  No model package is downloaded at request time.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)


def _normalized_lang(code: str) -> str:
    value = str(code or "").strip().lower()
    aliases = {
        "zh-tw": "zh",
        "zh-hant": "zh",
        "zh-cn": "zh",
        "zh-hans": "zh",
        "in": "id",
    }
    return aliases.get(value, value)


def _local_http_translate(text: str, src: str, tgt: str) -> Optional[str]:
    endpoint = str(os.environ.get("LOCAL_TRANSLATE_URL", "") or "").strip()
    if not endpoint:
        return None
    timeout = max(1.0, min(30.0, float(os.environ.get("LOCAL_TRANSLATE_TIMEOUT", "8") or 8)))
    payload = {
        "q": text,
        "source": "auto" if _normalized_lang(src) in ("", "auto") else _normalized_lang(src),
        "target": _normalized_lang(tgt),
        "format": "text",
    }
    api_key = str(os.environ.get("LOCAL_TRANSLATE_API_KEY", "") or "").strip()
    if api_key:
        payload["api_key"] = api_key
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        result = str(data.get("translatedText") or data.get("translation") or "").strip()
        return result or None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        logger.warning("[OfflineTranslation] local endpoint unavailable: %s", exc)
        return None
    except Exception as exc:
        logger.warning("[OfflineTranslation] local endpoint failed: %s", exc)
        return None


def _argos_translate(text: str, src: str, tgt: str) -> Optional[str]:
    source = _normalized_lang(src)
    target = _normalized_lang(tgt)
    if source in ("", "auto") or not target or source == target:
        return None
    try:
        from argostranslate import translate as argos_translate  # type: ignore
    except Exception:
        return None
    try:
        installed = list(argos_translate.get_installed_languages())
        from_lang = next((lang for lang in installed if str(lang.code).lower() == source), None)
        to_lang = next((lang for lang in installed if str(lang.code).lower() == target), None)
        if not from_lang or not to_lang:
            return None
        translation = from_lang.get_translation(to_lang)
        if not translation:
            return None
        result = str(translation.translate(text) or "").strip()
        return result or None
    except Exception as exc:
        logger.warning("[OfflineTranslation] Argos route failed: %s", exc)
        return None


def translate(text: str, src: str, tgt: str) -> Optional[str]:
    """Return a local translation, or ``None`` when no local route is ready."""
    source_text = str(text or "").strip()
    if not source_text:
        return None
    for call in (_local_http_translate, _argos_translate):
        result = call(source_text, src, tgt)
        if result:
            return result
    return None


def is_configured() -> bool:
    """Whether at least one local route appears configured or installed."""
    if str(os.environ.get("LOCAL_TRANSLATE_URL", "") or "").strip():
        return True
    try:
        from argostranslate import translate as argos_translate  # type: ignore
        return bool(argos_translate.get_installed_languages())
    except Exception:
        return False
