"""Lossless identity for approved translation sources (never fuzzy matching).

Chinese presentation spaces and prose commas/full stops may vary. Decimal
separators, word boundaries, signs, comparisons, flags and emoji carry meaning
and must never disappear from an exact-correction key.
"""
from __future__ import annotations

import json
from functools import lru_cache
import re
import unicodedata

SOURCE_IDENTITY_VERSION = "2026-09-04.1"
_TOKENS = re.compile(r"[\u3400-\u9fff]|[^\W\u3400-\u9fff]+|[^\w\s]", re.UNICODE)


def canonical_source_key(value) -> str:
    raw = str(value or "")
    # Pure text identity only, never a translation/acceptance cache. Keep
    # unusual large documents outside the bounded cache to limit worker memory.
    if len(raw) > 4096:
        return _canonical_source_key_cached.__wrapped__(raw)
    return _canonical_source_key_cached(raw)


@lru_cache(maxsize=1024)
def _canonical_source_key_cached(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw).casefold()
    chars = []
    for index, char in enumerate(text):
        if char in ",.。;":
            numeric = (char in ",." and index > 0 and index + 1 < len(text)
                       and text[index - 1].isdigit() and text[index + 1].isdigit())
            if not numeric:
                char = " "
        chars.append(char)
    tokens = _TOKENS.findall("".join(chars))
    return json.dumps(tokens, ensure_ascii=False, separators=(",", ":")) if tokens else ""
