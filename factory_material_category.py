"""Plant material categories are nouns, not an extra processing operation.

轉用料 denotes material that cannot be shipped normally for quality or other
reasons. Bare 轉用 can abbreviate this category as a stock-report subject.
Only a completely parsed receipt status is locally repaired; dates, quantities,
questions, destinations and extra clauses remain with normal translation.
"""
from __future__ import annotations

import re
import unicodedata

from translation_mentions import mention_spans

BUILD_ID = "2026-09-22.1-material-category-receipt"
_ZH_EXPLICIT = r"[轉转]\s*用\s*(?:材料|物料|料)"
_MODIFIER = r"全部|全數|全数|完全|全|都|已經|已经|已|尚未|還沒|还没|未"
_ZH_CATEGORY_RE = re.compile(
    _ZH_EXPLICIT + r"|[轉转]\s*用(?=\s*(?:(?:" + _MODIFIER + r")\s*){0,5}入[庫库])"
)
_ID_CATEGORY_RE = re.compile(
    r"\b(?:material|barang|bahan)\s+(?:(?:kategori\s+)?alih\s+guna|"
    r"untuk\s+penggunaan\s+lain|yang\s+(?:dialihgunakan|"
    r"dialihkan\s+(?:penggunaannya|untuk\s+penggunaan\s+lain)))\b", re.I,
)
_ZH_RECEIPT_RE = re.compile(
    r"\s*[轉转]\s*用(?:\s*(?:材料|物料|料))?\s*"
    r"(?P<mod>(?:(?:" + _MODIFIER + r")\s*){0,5})入[庫库]"
    r"(?P<end>完成了|完成|完了|了)?\s*[。.!！]?\s*"
)
_ZH_ALL_RE = re.compile(r"全部|全數|全数|完全|全|都")
_ZH_PENDING_RE = re.compile(r"尚未|還沒|还没|未")
_STOCK_ID_RE = re.compile(
    r"\b(?:masuk|dimasukkan|diterima)\s+(?:(?:ke|di|dalam)\s+)?(?:stok|gudang|persediaan)\b|"
    r"\b(?:dicatat|tercatat|diinput)\s+(?:(?:ke|di|dalam|sebagai)\s+){0,2}(?:stok|persediaan)\b", re.I,
)
_ID_ALL_RE = re.compile(r"\b(?:semua|seluruh)(?:nya)?\b", re.I)
_IDENTITY_RE = re.compile(r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]+|__[A-Za-z0-9_]+__", re.I)
_QUOTED_RE = re.compile(r"""「[^」]*」|『[^』]*』|“[^”]*”|"[^"\n]*"|'[^'\n]*'""")
_MEANING = (
    "使用者確認：轉用料是因品質異常等原因無法正常出貨的材料類別。"
    "庫存回報中作為主詞的『轉用』是『轉用料』的省略，不是已完成轉用的動作。"
    "印尼文可用名詞 material alih guna，也可用完整名詞片語表達；"
    "不能另加 sudah dialihkan，也不推定已轉給其他訂單、已重工或已報廢。"
    "定義供辨義，不要替本批材料補出原文沒寫的特定異常原因。"
    "全入庫了=全部已入庫；未全部入庫與全部未入庫必須區分。"
    "動詞『把材料轉用到…』則保留原文動作和目的地。"
)


def _lang(value):
    return str(value).lower().replace("_", "-").split("-")[0]


def _visible(text):
    value = unicodedata.normalize("NFKC", str(text or ""))
    for start, end, _ in reversed(mention_spans(value)):
        value = value[:start] + " " * (end - start) + value[end:]
    return _QUOTED_RE.sub(" ", _IDENTITY_RE.sub(" ", value))


def _prefix_and_body(source):
    raw = str(source or "")
    spans = mention_spans(raw) + [(m.start(), m.end(), m.group())
                                 for m in re.finditer(r"__MENTION_\d+__", raw)]
    end = 0
    for left, right, _ in sorted(spans):
        if raw[end:left].strip():
            break
        end = right
    return raw[:end], raw[end:]


def _zh_receipt(text):
    _, body = _prefix_and_body(text)
    match = _ZH_RECEIPT_RE.fullmatch(unicodedata.normalize("NFKC", body))
    if not match:
        return None
    mod = match['mod']
    quantifiers = list(_ZH_ALL_RE.finditer(mod))
    negatives = list(_ZH_PENDING_RE.finditer(mod))
    if len(negatives) > 1:
        return None
    all_match = quantifiers[0] if quantifiers else None
    pending = negatives[0] if negatives else None
    # "都還沒全部入庫" has nested quantifier scopes. Do not flatten
    # that into "all have not entered"; let the normal translator retain it.
    if pending and any(m.start() < pending.start() for m in quantifiers) and any(
            m.start() > pending.start() for m in quantifiers):
        return None
    done = bool(re.search(r"已經|已经|已", mod) or match['end'])
    if (pending and re.search(r"已經|已经|已", mod)) or not (pending or done):
        return None
    scope = 'all' if all_match else 'unspecified'
    if pending and all_match and pending.start() < all_match.start():
        scope = 'not_all'
    return {'mode': 'pending' if pending else 'completed', 'scope': scope}


def _id_receipt(text):
    _, body = _prefix_and_body(text)
    body = body.strip().rstrip('.!。！').strip()
    nouns, actions = list(_ID_CATEGORY_RE.finditer(body)), list(_STOCK_ID_RE.finditer(body))
    if len(nouns) != 1 or len(actions) != 1:
        return None
    # Full consumption prevents another object's status, extra conditions or
    # another action from being treated as this category's receipt status.
    rest = _STOCK_ID_RE.sub(' ', _ID_CATEGORY_RE.sub(' ', body))
    if not re.fullmatch(r"(?:\s|sudah|telah|belum|tidak|semua|seluruh|semuanya|seluruhnya)*", rest, re.I):
        return None
    pending = re.search(r"\bbelum\b", body, re.I)
    done = re.search(r"\b(?:sudah|telah)\b", body, re.I)
    all_match = _ID_ALL_RE.search(body)
    not_all = re.search(r"\b(?:belum|tidak)\s+(?:semua|seluruh)(?:nya)?\b", body, re.I)
    if not_all:
        return {'mode': 'pending', 'scope': 'not_all'}
    if re.search(r"\btidak\b", body, re.I) or (pending and done) or not (pending or done):
        return None
    scope = 'all' if all_match else 'unspecified'
    if pending and all_match and pending.start() < all_match.start():
        scope = 'not_all'
    return {'mode': 'pending' if pending else 'completed', 'scope': scope}


def build_facts(source, lang):
    lang = _lang(lang)
    if lang not in {'zh', 'id'}:
        return []
    visible = _visible(source)
    pattern = _ZH_CATEGORY_RE if lang == 'zh' else _ID_CATEGORY_RE
    matches = []
    for match in pattern.finditer(visible):
        if lang == 'zh' and not re.fullmatch(_ZH_EXPLICIT, match.group()):
            # Bare 轉用 must occupy a noun/subject slot, not the verb after
            # 已經/把/將/改為. Explicit 轉用料 remains a noun anywhere.
            prefix = re.split(r"[。.!?！？;；,，\n]", visible[:match.start()])[-1].strip()
            if not re.fullmatch(r"(?:這些|这些|這批|这批|那些|那批|全部|所有|目前|今天)?", prefix):
                continue
        matches.append(match)
    if not matches:
        return []
    receipt = (_zh_receipt if lang == 'zh' else _id_receipt)(source)
    return [{'sense': 'material_category', 'category': 'alih_guna',
             'evidence': [m.group() for m in matches], 'meaning': _MEANING,
             'receipt': receipt, 'source_language': lang}]


def validate(facts, candidate, tgt):
    facts = [fact for fact in facts if fact.get('sense') == 'material_category']
    tgt = _lang(tgt)
    if not facts or tgt not in {'zh', 'id'}:
        return []
    issues = []
    has_category = bool(build_facts(candidate, tgt))
    for fact in facts:
        if not has_category:
            issues.append('category_noun_missing')
        expected = fact.get('receipt')
        if expected:
            actual = (_id_receipt if tgt == 'id' else _zh_receipt)(candidate)
            if actual is None:
                issues.append('receipt_relation_missing_or_added_action')
            elif actual != expected:
                issues.append('receipt_state_or_scope_changed')
    return list(dict.fromkeys('factory_material_category:' + issue for issue in issues))


def canonicalize(source, candidate, src, tgt):
    if (_lang(src), _lang(tgt)) != ('zh', 'id') or not candidate:
        return candidate
    receipt = _zh_receipt(source)
    if not receipt:
        return candidate
    facts = build_facts(source, src)
    if not facts or not validate(facts, candidate, tgt):
        return candidate
    # The source was consumed completely, including every leading mention.
    # No unseen time, quantity, identity, destination or extra clause is lost.
    mode, scope = receipt['mode'], receipt['scope']
    if scope == 'not_all':
        result = 'Material alih guna belum seluruhnya masuk stok.'
    else:
        subject = 'Seluruh material alih guna' if scope == 'all' else 'Material alih guna'
        result = subject + (' sudah' if mode == 'completed' else ' belum') + ' masuk stok.'
    prefix, _ = _prefix_and_body(source)
    return (prefix + ' ' if prefix else '') + result
