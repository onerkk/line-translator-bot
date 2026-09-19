"""Compile measured translation failures into bounded first-pass instructions.

This is online policy learning, not model-weight training. Only a correction
whose old target fails today's validator and whose new target passes it can
teach a rule. Rules retain error classes and source features, never generated
target text. Similarity selects advice; it never authorizes translation reuse.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re
import time

import factory_source_understanding as understanding
from translation_source_identity import canonical_source_key

BUILD_ID = '2026-09-19.2-material-relation-learning'

# A fixed error taxonomy is independent of individual factory sentences. The
# observed associations, frequency and source vocabulary are learned in SQLite.
_ADVICE = {
    'material_relation': 'Keep material origin (dari/from), location (di/at), and part (bagian/ujung) distinct. Bind dimensions to the material, not a distance from its end. Many bent pieces is a count statement, not bend severity. A direction never identifies an unstated process or station; use only current-source values.',
    'identity': 'Generic equipment words do not identify a specific machine or area. Preserve only identifiers present in the CURRENT source. Never copy equipment codes from a glossary label or historical example.',
    'handoff': 'Distinguish ERP data release from physical placement or general approval. A station code is not a quantity. Keep alternative causes as a question, and do not turn inspection not occurring into inability or refusal without source evidence.',
    'record': 'Distinguish a system record/field from physical material. Keep each value attached to its stated field and storage operation; never move a number into a physical warehouse.',
    'printed_storage': 'For the confirmed shorthand 存檔入庫都沒儲區, the storage location is missing from the first TAG print, not necessarily from the system field. 補印才有 inherits that object only from linked original context; never assume every reprint concerns storage.',
    'intake_record': 'In this plant intake plan, goods count as inventoried when their data is recorded. Preserve the stated goods weight in ton and distinguish it from a record count or a physical lifting instruction. Spread system entry times when requested; never add an unstated station ID.',
    'sequence': 'Resolve before/after from the current source, not clause order. Keep inspection, submission and production in the stated order, including negated or conditional prerequisites.',
    'quantity': 'Bind every number, unit, comparison and identifier to its own object or field. Preserve exact values and unspecified units; never borrow a quantity from another clause.',
    'permission': 'Keep permission, prohibition, obligation and completion attached to the correct action. An unlocked field or an available machine does not itself grant permission.',
    'storage_destination': 'For the plant packaging shorthand 不論哪一站都幫忙 plus a storage code, finished material must be physically lifted/moved to THIS explicitly assigned area regardless of the area printed on TAG. Keep the current customer/time/code. This is a temporary instruction, not machine support, data transfer or a change to the customer default.',
    'packing': 'In this plant, 待裝木箱 means finished products awaiting packing into wooden crates (produk jadi yang menunggu dikemas ke dalam peti kayu), not installing crates. Preserve an explicitly different source object; explicit crate assembly remains assembly.',
    'operation': 'In staffing/production plans, 開幾站 means operating existing stations. The confirmed 開三站 shorthand means three PACKING stations (round, special-shape, peeling packing), not peeling processing. Do not infer which two for 開兩站; explicit other station types and new construction remain as stated. Preserve priority/count/time.',
    'actor': 'Reconstruct who performs which action, on what object, for which recipient. Keep organization roles and data ownership distinct; do not invent the omitted actor.',
    'coverage': 'Translate every source instruction and qualification. Preserve paragraph scope, names and identifiers; do not omit a clause or add an explanation.',
    'meaning': 'Resolve factory terms from the linked object and operation in this source. Preserve the current status, cause and consequence, rather than a past message\'s wording.',
}


def category(issue):
    """Accept only machine issue codes, never free-form feedback as a command."""
    code = str(issue).removeprefix('quality_gate:')
    if not re.match(r'^(?:factory_|record_|structured_|immutable_|missing_|invented_|number_|quantity_|source_|untranslated_|paragraph_|glossary_)', code):
        return None
    if re.search(r'unavailable|exception|timeout|network|provider', code):
        return None
    if 'ambiguous_packing_location_or_repeat' in code: return None
    if 'invented_identifier' in code: return 'identity'
    if 'factory_material_relation:' in code: return 'material_relation'
    if re.search(r'handoff|erp_release_as_general_approval|inspection_station_as_quantity|inspection_ability_or_intent', code): return 'handoff'
    if re.search(r'verification_(?:before|after)|sequence|temporal|before_after', code): return 'sequence'
    if re.search(r'permission|prohibit|manual_entry|negation|polarity', code): return 'permission'
    if 'printed_storage' in code: return 'printed_storage'
    if 'intake_quantity' in code: return 'intake_record'
    if re.search(r'inventory_entry|record_category|record_transfer|record_save|storage_field|storage_update', code): return 'record'
    if 'storage_destination' in code: return 'storage_destination'
    if 'pending_crate_packing' in code: return 'packing'
    if 'station_operation' in code: return 'operation'
    if re.search(r'quantity|number|record_field|invented_unit|measurement|immutable|structured_', code): return 'quantity'
    if re.search(r'actor|recipient|ownership|organization|title|role', code): return 'actor'
    if re.search(r'missing|coverage|untranslated|paragraph', code): return 'coverage'
    return 'meaning'


def _features(source, lang):
    normalized = understanding.normalized_view(str(source), lang).casefold()
    # Names/IDs/amounts are not predictive facts to carry to the next sentence.
    normalized = re.sub(r'https?://\S+|__[a-z0-9_]+__|@\S+|'
                        r'(?<![a-z0-9])(?=[a-z0-9_/.-]*\d)[a-z0-9]+(?:[_/.-][a-z0-9]+)*(?![a-z0-9])',
                        ' ', normalized)
    concepts = {'c:' + value for value in understanding.concepts(normalized)}
    if understanding.material_relations.build_facts(source, lang):
        concepts.add('c:material_relation')
    if re.search(r'入庫|登錄|登記|存入|輸入|系統|存檔|儲區|\b(?:input|diinput|menginput|mencatat|dicatat|pencatatan|sistem)\b', normalized):
        concepts.add('c:record_entry')
    if re.search(r'包(?:裝的|装的|的).*(?:不論|不论|不管).*站.*(?:幫忙|帮忙)', normalized):
        concepts.update({'c:pack', 'c:storage_destination'})
    if re.search(r'(?:開|开)(?:[一二兩两三四五六七八九十]|\d+)(?:個|个)?站', normalized):
        concepts.add('c:station_operation')
    if re.search(r'支數|數量|\bjumlah\b', normalized):
        concepts.add('c:field_quantity')
    if lang == 'zh':
        runs = re.findall(r'[\u3400-\u9fff]+', normalized)
        lexical = {'w:' + run[i:i+2] for run in runs for i in range(len(run)-1)}
    else:
        lexical = {'w:' + word for word in re.findall(r'[a-z]{4,}', normalized)}
    return concepts | set(sorted(lexical)[:96])


def init_schema(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS translation_learned_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            src_lang TEXT NOT NULL, tgt_lang TEXT NOT NULL, group_id TEXT NOT NULL,
            source_key TEXT NOT NULL, features_json TEXT NOT NULL,
            category TEXT NOT NULL, issues_json TEXT NOT NULL,
            policy_fingerprint TEXT NOT NULL, correction_id INTEGER,
            event_id INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            UNIQUE(src_lang,tgt_lang,group_id,source_key,category,policy_fingerprint)
        );
        CREATE INDEX IF NOT EXISTS idx_learned_rules_scope
        ON translation_learned_rules(src_lang,tgt_lang,group_id,updated_at DESC);
        CREATE TABLE IF NOT EXISTS translation_learning_replay (
            policy_fingerprint TEXT PRIMARY KEY, last_event_id INTEGER NOT NULL
        );
    ''')


def derive(source, old, new, src, tgt, *, reviewed, cacheable, validator):
    """Replay both targets locally; a caller's confidence/flags are not proof."""
    # A source-grounded local correction can teach the same lesson as a human
    # correction. Replaying both validators below is the evidence; a second AI
    # review is neither necessary nor sufficient.
    if not (cacheable and old and new and old.strip() != new.strip()):
        return []
    if (src, tgt) not in {('zh', 'id'), ('id', 'zh')} or max(map(len, (source, old, new))) > 12000:
        return []
    current = validator(source, new, src, tgt)
    if not current.get('ok'):
        return []
    previous = validator(source, old, src, tgt)
    if previous.get('ok'):
        return []  # stylistic rewrites are not measured improvements
    grouped = defaultdict(list)
    for issue in previous.get('issues', []):
        kind = category(issue)
        if kind:
            # Save codes only; values and arbitrary feedback never enter prompts.
            code = str(issue).removeprefix('quality_gate:').split('=', 1)[0][:160]
            grouped[kind].append(code)
    features = sorted(_features(source, src))
    if not features:
        return []
    return [{'category': kind, 'issues': sorted(set(codes)), 'features': features}
            for kind, codes in grouped.items()]


def store(conn, lessons, *, source, src, tgt, group, policy, event_id, correction_id=None):
    key = hashlib.sha256(canonical_source_key(source).encode()).hexdigest()
    for lesson in lessons:
        conn.execute('''
            INSERT INTO translation_learned_rules
              (src_lang,tgt_lang,group_id,source_key,features_json,category,issues_json,
               policy_fingerprint,correction_id,event_id,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(src_lang,tgt_lang,group_id,source_key,category,policy_fingerprint)
            DO UPDATE SET features_json=excluded.features_json,issues_json=excluded.issues_json,
                correction_id=excluded.correction_id,event_id=excluded.event_id,updated_at=excluded.updated_at
        ''', (src, tgt, group, key, json.dumps(lesson['features'], ensure_ascii=False),
              lesson['category'], json.dumps(lesson['issues']), policy, correction_id,
              event_id, int(time.time())))
    # Repeated retries of one source replace evidence, rather than manufacturing
    # independent corroboration. Bound growth across old policy revisions too.
    if lessons:
        conn.execute('DELETE FROM translation_learned_rules WHERE id NOT IN '
                     '(SELECT id FROM translation_learned_rules ORDER BY updated_at DESC,id DESC LIMIT 3000)')


def select(conn, source, src, tgt, group, policy):
    query_features = _features(source, src)
    if not query_features:
        return {'rules': [], 'build': BUILD_ID}
    key = hashlib.sha256(canonical_source_key(source).encode()).hexdigest()
    rows = conn.execute('''
        SELECT r.* FROM translation_learned_rules r
        LEFT JOIN corrections c ON c.id=r.correction_id
        WHERE r.src_lang=? AND r.tgt_lang=? AND r.group_id IN (?, '')
          AND r.policy_fingerprint=?
          AND (r.correction_id IS NULL OR (c.status='approved' AND c.validation_state='passed'))
        ORDER BY (r.group_id=?) DESC,r.updated_at DESC,r.id DESC LIMIT 300
    ''', (src, tgt, group, policy, group)).fetchall()
    ranked = {}
    for row in rows:
        features = set(json.loads(row['features_json']))
        shared = query_features & features
        concept_overlap = sum(value.startswith('c:') for value in shared)
        lexical_overlap = sum(value.startswith('w:') for value in shared)
        exact = key == row['source_key']
        if not exact and not (concept_overlap >= 2 or (concept_overlap and lexical_overlap >= 2)):
            continue
        kind = row['category']
        if kind not in _ADVICE:
            continue
        score = 1.0 if exact else len(shared) / max(1, len(features | query_features))
        if kind not in ranked:
            ranked[kind] = {'category': kind, 'score': score, 'evidence': set(), 'issues': set()}
        item = ranked[kind]
        item['score'] = max(score, item['score'])
        item['evidence'].add(row['source_key'])
        item['issues'].update(json.loads(row['issues_json']))
    rules = sorted(ranked.values(), key=lambda item: (-item['score'], item['category']))[:4]
    return {'rules': [{'category': r['category'], 'score': round(r['score'], 4),
                      'evidence_count': len(r['evidence']), 'issues': sorted(r['issues'])[:12]}
                     for r in rules], 'build': BUILD_ID}


def build_prompt(snapshot, max_chars=1200):
    start = '<learned_translation_policy>\n'
    end = '\n</learned_translation_policy>'
    lines = ['Prior measured failures select these checks. Apply them to CURRENT source facts; no historical target is authoritative.']
    selected = []
    for rule in (snapshot or {}).get('rules', []):
        advice = _ADVICE.get(rule.get('category'))
        if advice and advice not in selected:
            if len(start + '\n'.join(lines + selected + [advice]) + end) <= max_chars:
                selected.append(advice)
    return start + '\n'.join(lines + selected) + end if selected else ''


def needs_extra_review(risk, snapshot, source, src, tgt):
    """Avoid a redundant review only for explicit, active local invariants.

    This never disables the quality gate's repair of a failed current candidate,
    or independent review selected for serious incidents/unresolved context.
    Unrecognized historical errors still request review.
    """
    if not (risk or {}).get('requires_review'):
        return False
    learned = {code for rule in (snapshot or {}).get('rules', []) for code in rule.get('issues', [])}
    if not learned:
        return True
    import factory_input_semantics as inputs
    frame = inputs.build_frame(source, src, tgt)
    covered = set()
    if frame.get('inventory_entry'):
        covered.add('factory_input_semantics:inventory_entry_record_missing')
    if frame.get('verification_order'):
        covered.add('factory_input_semantics:verification_' + frame['verification_order'] + '_entry_missing')
    for state in frame.get('manual', []):
        covered.add('factory_input_semantics:manual_entry_' + state + '_missing')
    prior = {str(code).removeprefix('quality_gate:')
             for match in risk.get('matches', []) for code in match.get('issues', [])}
    return not (prior and prior <= covered and prior <= learned)
