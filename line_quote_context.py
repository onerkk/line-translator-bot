"""LINE quote/reply metadata helpers.

The generated LINE SDK has changed representation details across releases
(Pydantic object attributes, aliases, dictionaries).  These helpers normalize
all supported shapes so reply messages are never skipped merely because the
SDK exposes ``quotedMessageId`` differently.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, Mapping as TypingMapping, Optional


_QUOTED_ID_NAMES = (
    "quoted_message_id",
    "quotedMessageId",
    "quoted_messageId",
)
_QUOTE_TOKEN_NAMES = (
    "quote_token",
    "quoteToken",
)


def _object_mappings(value: Any):
    """Yield mapping views from dict/Pydantic/OpenAPI-style objects."""
    if isinstance(value, Mapping):
        yield value

    for method_name, kwargs in (
        ("model_dump", {"by_alias": True}),
        ("model_dump", {}),
        ("dict", {"by_alias": True}),
        ("dict", {}),
        ("to_dict", {}),
    ):
        method = getattr(value, method_name, None)
        if not callable(method):
            continue
        try:
            result = method(**kwargs)
        except TypeError:
            try:
                result = method()
            except Exception:
                continue
        except Exception:
            continue
        if isinstance(result, Mapping):
            yield result


def get_message_field(message: Any, *names: str) -> Any:
    """Read the first non-empty field from object attributes or aliases."""
    if message is None:
        return None

    for name in names:
        try:
            value = getattr(message, name)
        except Exception:
            value = None
        if value not in (None, ""):
            return value

    for mapping in _object_mappings(message):
        for name in names:
            value = mapping.get(name)
            if value not in (None, ""):
                return value
    return None


def get_quoted_message_id(message: Any) -> Optional[str]:
    value = get_message_field(message, *_QUOTED_ID_NAMES)
    if value in (None, ""):
        return None
    return str(value)


def get_quote_token(message: Any) -> Optional[str]:
    value = get_message_field(message, *_QUOTE_TOKEN_NAMES)
    if value in (None, ""):
        return None
    return str(value)


def resolve_quote_context(
    message: Any,
    message_cache: TypingMapping[str, TypingMapping[str, Any]],
    *,
    source_language: Optional[str] = None,
) -> Dict[str, Any]:
    """Return normalized quote metadata and the best cached context text.

    LINE only supplies ``quotedMessageId`` for a user's reply.  It doesn't let
    the bot fetch the old text again, so the content is available only when the
    bot cached the earlier webhook.  Missing cache data must never suppress the
    current message; this function therefore returns an empty context safely.
    """
    quoted_id = get_quoted_message_id(message)
    entry = None
    if quoted_id:
        try:
            candidate = message_cache.get(quoted_id)
        except Exception:
            candidate = None
        if isinstance(candidate, Mapping):
            entry = candidate

    context_text = ""
    if entry:
        translations = entry.get("tr")
        if source_language and isinstance(translations, Mapping):
            translated_context = translations.get(source_language)
            if translated_context:
                context_text = str(translated_context).strip()
        if not context_text:
            context_text = str(entry.get("text") or "").strip()

    return {
        "quoted_message_id": quoted_id,
        "quote_token": get_quote_token(message),
        "entry": entry,
        "context_text": context_text,
    }
