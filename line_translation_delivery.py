"""Lossless LINE text framing and acknowledged retry classification.

References:
https://developers.line.biz/en/docs/messaging-api/text-character-count/
https://developers.line.biz/en/docs/messaging-api/retrying-api-request/

No SDK/client credentials here: callers retain transport and persistence.
"""
from __future__ import annotations

import json
import uuid


def utf16_units(text: str) -> int:
    return sum(2 if ord(char) > 0xFFFF else 1 for char in str(text or ""))


def split_text(text: str, *, max_units: int = 4700) -> list[str]:
    """Keep every character, including paragraph separators and astral emoji."""
    value = str(text or "")
    if max_units < 2 or max_units > 5000:
        raise ValueError("LINE text limit must be between 2 and 5000 UTF-16 units")
    chunks = []
    start = 0
    while start < len(value):
        end, units = start, 0
        while end < len(value):
            width = 2 if ord(value[end]) > 0xFFFF else 1
            if units + width > max_units:
                break
            units += width
            end += 1
        if end < len(value):
            boundary = max(value.rfind("\n", start, end), value.rfind(" ", start, end))
            if boundary >= start + (end - start) // 2:
                end = boundary + 1
        chunks.append(value[start:end])
        start = end
    return chunks


def text_batches(text: str, *, max_messages: int = 5) -> list[list[str]]:
    size = max(1, min(5, int(max_messages)))
    chunks = split_text(text)
    return [chunks[index:index + size] for index in range(0, len(chunks), size)]


def retry_key(job_key: str, batch_index: int = 0) -> str:
    # Keep the existing durable first-request key for pending pre-upgrade jobs.
    suffix = "" if batch_index == 0 else ":batch:" + str(batch_index)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "jy-translation-retry:" + str(job_key) + suffix))


def already_accepted(error: Exception) -> bool:
    """409 is success only with LINE's acknowledgement of the original request."""
    if str(getattr(error, "status", "")) != "409":
        return False
    headers = getattr(error, "headers", None) or {}
    return any(str(key).lower() == "x-line-accepted-request-id" and bool(value)
               for key, value in headers.items())


def invalid_quote(error: Exception) -> bool:
    if str(getattr(error, "status", "")) != "400":
        return False
    body = getattr(error, "body", "")
    if isinstance(body, dict):
        body = json.dumps(body)
    text = str(body or error).casefold().replace("_", "")
    return "quotetoken" in text or "quote token" in text
