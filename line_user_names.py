"""Exact LINE profile names, derived from the existing durable user directory.

Do not infer people from sentence words, split nicknames, or treat LINE IDs as
names. The profile API is the authority; translation never needs an AI lookup.
"""

import re
from functools import lru_cache


def clean_name(value, user_id=""):
    if not isinstance(value, str):
        return ""
    name = value.strip()
    if (not name or len(name) > 256 or name == user_id
            or re.fullmatch(r"[UCR][0-9a-fA-F]{32}", name)
            or any(ord(char) < 32 for char in name)):
        return ""
    return name


def known_names(groups, direct_users):
    """Union names without altering the manual list or duplicating storage."""
    names = {}
    for users in [direct_users, *groups.values()]:
        if not isinstance(users, dict):
            continue
        for user_id, value in users.items():
            name = clean_name(value, user_id)
            if name:
                names.setdefault(name, None)
    return list(names)


@lru_cache(maxsize=512)
def name_pattern(name):
    # Latin names must not protect a substring of ordinary words: Adi/tadi,
    # Ann/announcement, or A/API. CJK names may touch Chinese sentence text.
    word = r"[A-Za-zÀ-ÖØ-öø-ÿ\u0100-\u024f\u1e00-\u1eff\u0300-\u036f0-9_]"
    left = "(?<!" + word + ")" if re.match(word, name) else ""
    right = "(?!" + word + ")" if re.search(word + "$", name) else ""
    return re.compile(left + re.escape(name) + right)


def visible_names(text, names):
    """Keep full names, spacing, emoji and spelling exactly as LINE supplied."""
    return [name for name in sorted(set(names), key=lambda n: (-len(n), n))
            if name and name_pattern(name).search(text)]
