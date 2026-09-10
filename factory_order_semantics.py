"""Order urgency belongs to the order, not to an unrelated action.

Shared source relations for prompts, acceptance and a small complete-grammar
fast path. Unconsumed names, codes, quantities, deadlines, questions or clauses
always keep the full message on the provider path instead of dropping content.
"""
from __future__ import annotations

import re
import unicodedata

import conversation_context
from translation_request_cache import memoize

BUILD_ID = '2026-09-10.9-order-urgency-and-request-state'
_ZH_ORDER = re.compile(r'(?:不急|不緊急|不紧急)(?:的)?(?:工[單单]|訂單|订单|單子|单子)|'
                       r'(?:普通|一般|正常)(?:工[單单]|訂單|订单)|'
                       r'(?:工[單单]|訂單|订单)(?:很|非常|相當|相当|十分|是|並不|并不|不|並非|并非){0,3}'
                       r'(?:緊急|紧急|急迫|加急|急|普通|一般|正常)|'
                       r'急[單单]|(?:緊急|紧急|加急|急用)(?:的)?(?:工[單单]|訂單|订单|單子|单子)|急件(?:工[單单])?')
_ZH_NEG = re.compile(r'(?:不是|並非|并非|不屬於|不属于|不算是?|非)\s*$')
_ORDER_ID = r'(?:work\s*order|order(?:\s+kerja)?|pesanan)'
_QUALIFIER_ID = r'(?:mendesak|urgen|urgent|biasa|reguler|normal|harus\s+segera\s+(?:diproses|ditangani|dikerjakan))'
_ORDER_CODE_TEXT = r'[A-Za-z]{1,6}[-/]\d{2,}[A-Za-z0-9/-]*'
_GAP_ID = r'(?:(?:yang|ini|itu|tersebut|sangat|benar-benar|tidak|bukan|lagi|masih|paling|' + _ORDER_CODE_TEXT + r')\s+){0,5}'
_ID_ORDER = re.compile(r'\b(?:' + _ORDER_ID + r'\s+' + _GAP_ID + _QUALIFIER_ID
                       + r'|' + r'(?:urgent|urgen)\s+' + _ORDER_ID + r')\b', re.I)
_ITEM = re.compile(r'(?m)^\s*(\d+)[.)、．]\s*(?!\d)')
_CLAUSE = re.compile(r'[，,。;；!?！？\n]')
_ORDER_CODE = re.compile(r'\b' + _ORDER_CODE_TEXT + r'\b')
_INSPECT = ('檢查一下', '检查一下', '確認一下', '确认一下', '查看一下', '看一下', '看一看', '看看', '檢查', '检查', '查看')
_HANDLE = ('處理一下', '处理一下', '處理', '处理')
_HELP = ('幫忙', '帮忙', '協助', '协助')
_PRIORITY = ('優先', '优先', '先')
_IMMEDIATE = ('趕快', '赶快', '馬上', '马上', '立刻', '立即')
_DEIXIS = ('這筆', '这笔', '那筆', '那笔', '這張', '这张', '那張', '那张', '這份', '这份', '那份', '這個', '这个', '那個', '那个', '這', '这', '那', '此')
_POLITE = ('麻煩', '麻烦', '請', '请')
_DIRECT_TOKENS = re.compile('|'.join(re.escape(x) for x in sorted(
    set(_INSPECT + _HANDLE + _HELP + _PRIORITY + _IMMEDIATE + _DEIXIS + _POLITE), key=len, reverse=True)))


def _scopes(text):
    marks = list(_ITEM.finditer(text))
    if not marks:
        return [(None, text)]
    result = [(None, text[:marks[0].start()])]
    seen = {}
    for i, mark in enumerate(marks):
        number = mark.group(1)
        seen[number] = seen.get(number, 0) + 1
        key = number if seen[number] == 1 else number + '#' + str(seen[number])
        result.append((key, text[mark.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)]))
    return result


def _attributes(text, language):
    attributes = []
    for clause in _CLAUSE.split(text):
        pattern = _ZH_ORDER if language == 'zh' else _ID_ORDER
        for match in pattern.finditer(clause):
            prefix = clause[:match.start()]
            if language == 'zh':
                negated = bool(_ZH_NEG.search(prefix) or re.search(r'不|並非|并非', match.group()))
                ordinary = bool(re.search(r'普通|一般|正常', match.group()))
            else:
                negated = bool(re.search(r'\b(?:bukan(?:lah)?|tidak)\s*(?:sebuah\s+)?$', prefix, re.I)
                               or re.search(r'\b(?:tidak|bukan)\b', match.group(), re.I))
                ordinary = bool(re.search(r'\b(?:biasa|reguler|normal)\b', match.group(), re.I))
            # "Not an ordinary order" may mean special, custom or unusual. It
            # does not establish whether the order is urgent; do not infer it.
            if ordinary and negated:
                continue
            negative = negated or ordinary
            codes = _ORDER_CODE.findall(clause)
            attributes.append({'evidence': match.group(), 'negative': negative,
                               'code': codes[0] if len(codes) == 1 else '', 'clause': clause})
    return attributes


def _direct_slots(body, attributes):
    if len(attributes) != 1 or attributes[0]['negative'] or re.search(r'[?？\n]', body):
        return {}
    # 急件 alone may be a document, package or task. Do not invent an order
    # object for it; the full provider translation retains that distinction.
    if attributes[0]['evidence'] == '急件':
        return {}
    compact = re.sub(r'[\s，,。.!！、]', '', body)
    order_matches = list(_ZH_ORDER.finditer(compact))
    if len(order_matches) != 1:
        return {}
    match = order_matches[0]
    remainder = compact[:match.start()] + compact[match.end():]
    tokens = _DIRECT_TOKENS.findall(remainder)
    if _DIRECT_TOKENS.sub('', remainder):
        return {}
    inspect = any(t in _INSPECT for t in tokens)
    handle = any(t in _HANDLE for t in tokens)
    if not (inspect or handle):
        return {}
    # Repeated/different references or action ordering need real sentence
    # interpretation. A token inventory alone must not turn them into a fast path.
    for choices in (_INSPECT, _HANDLE, _DEIXIS, _PRIORITY, _IMMEDIATE):
        if sum(t in choices for t in tokens) > 1:
            return {}
    action_tokens = [t for t in tokens if t in _INSPECT + _HANDLE]
    if inspect and handle and action_tokens[0] not in _INSPECT:
        return {}
    return {'inspect': inspect, 'handle': handle, 'help': any(t in _HELP for t in tokens),
            'priority': any(t in _PRIORITY for t in tokens),
            'immediate': any(t in _IMMEDIATE for t in tokens),
            'deixis': 'itu' if any(t.startswith('那') for t in tokens) else 'ini'}


@memoize
def build_frame(source, src_lang='zh', tgt_lang='id'):
    src, tgt = str(src_lang).lower().split('-')[0], str(tgt_lang).lower().split('-')[0]
    body = unicodedata.normalize('NFKC', conversation_context.body(source))
    frame = {'active': False, 'source': str(source), 'src_lang': src, 'tgt_lang': tgt,
             'relations': [], 'direct': {}}
    if (src, tgt) not in {('zh', 'id'), ('id', 'zh')}:
        return frame
    for item, text in _scopes(body):
        for relation in _attributes(text, src):
            frame['relations'].append(dict(relation, item=item))
    frame['active'] = bool(frame['relations'])
    if src == 'zh' and frame['active']:
        frame['direct'] = _direct_slots(body, frame['relations'])
    return frame


def is_order_message(source):
    return bool(_ZH_ORDER.search(str(source or '')) or _ID_ORDER.search(str(source or '')))


def deterministic_translation(frame):
    slots = frame.get('direct') or {}
    if not slots:
        return ''
    obj = 'work order mendesak ' + slots['deixis']
    start = 'Tolong ' + ('segera ' if slots['immediate'] else '')
    if slots['inspect']:
        text = start + ('bantu ' if slots['help'] and not slots['handle'] else '') + 'periksa ' + obj
        if slots['handle']:
            text += ' dan ' + ('bantu menanganinya' if slots['help'] else 'tangani work order tersebut')
    else:
        text = start + ('bantu menangani ' if slots['help'] else 'tangani ') + obj
    if slots['priority']:
        text += ' terlebih dahulu'
    return text + '.'


def validate_translation(frame, candidate):
    if not frame or not frame.get('active'):
        return True, []
    target = str(candidate or '')
    scopes = dict(_scopes(unicodedata.normalize('NFKC', target)))
    issues = []
    for relation in frame['relations']:
        scope = scopes.get(relation['item'], '')
        matches = _attributes(scope, frame['tgt_lang'])
        if relation['evidence'] == '急件' and frame['src_lang'] == 'zh':
            # Do not require a work-order noun for an unspecified urgent item.
            matched = any(bool(re.search(r'\b(?:bukan|tidak)\b', clause, re.I)) == relation['negative']
                          for clause in _CLAUSE.split(scope)
                          if re.search(r'\b(?:mendesak|urgen|urgent)\b', clause, re.I))
        else:
            matched = any(m['negative'] == relation['negative']
                          and (not relation['code'] or relation['code'].casefold() in m['clause'].casefold())
                          for m in matches)
        if not matched:
            issues.append('factory_order_semantics:order_urgency_relation_missing:' + str(relation['item'] or 'body'))
    slots = frame.get('direct') or {}
    if slots:
        low = target.lower()
        if not re.search(r'\b(?:tolong|mohon|silakan|harap|periksa|cek|lihat|tangani|bantu|prioritaskan)\b', low):
            issues.append('factory_order_semantics:request_changed_to_report')
        if re.search(r'\b(?:sudah|telah)\b.{0,45}\b(?:selesai|ditangani|diproses|dikerjakan|diperiksa)\b', low):
            issues.append('factory_order_semantics:request_changed_to_completed')
        if slots['inspect'] and not re.search(r'\b(?:periksa|memeriksa|cek|mengecek|lihat|melihat|tinjau|meninjau)\b', low):
            issues.append('factory_order_semantics:inspection_request_missing')
        if slots['handle'] and not re.search(r'\b(?:tangani|menangani|menanganinya|ditangani|proses|prosesnya|memproses|memprosesnya|kerjakan|mengerjakan|selesaikan|menyelesaikan)\b', low):
            issues.append('factory_order_semantics:handling_request_missing')
        if slots['help'] and not re.search(r'\b(?:bantu|membantu|bantuan|tolong|mohon)\b', low):
            issues.append('factory_order_semantics:assistance_request_missing')
        if slots['priority'] and not re.search(r'\b(?:prioritaskan|memprioritaskan|didahulukan|dahulukan|lebih dulu|lebih dahulu|terlebih dahulu|terlebih dulu)\b', low):
            issues.append('factory_order_semantics:priority_request_missing')
        if slots['immediate'] and not re.search(r'\b(?:segera|secepatnya|langsung|sekarang)\b', low):
            issues.append('factory_order_semantics:immediate_request_missing')
    return not issues, list(dict.fromkeys(issues))


def build_prompt(frame):
    if not frame or not frame.get('active'):
        return ''
    lines = ['<factory_order_relations>',
             '急單 is an urgent work order (work order mendesak), not an ordinary order with segera attached only to periksa.',
             'Preserve the order urgency, its negation and referenced order ID in the same item. Do not infer a deadline or completion.',
             '看一下 is a request to check/look; 幫忙處理 asks for assistance handling the task, not a report that it is done.']
    for relation in frame['relations']:
        lines.append('item=' + str(relation['item']) + '; source=' + relation['evidence']
                     + '; urgency=' + ('negated' if relation['negative'] else 'urgent')
                     + ('; order=' + relation['code'] if relation['code'] else ''))
    lines.append('</factory_order_relations>')
    return '\n'.join(lines)
