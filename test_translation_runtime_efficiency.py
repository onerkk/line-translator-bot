"""Lossless prompt factoring and mutation-safe fast-path regressions.

All provider responses are fixed test data. These tests do not measure model
accuracy, network latency, token billing or production savings.
"""
import copy
import json
from types import SimpleNamespace

import pytest

import app
import factory_semantic_audit as audit
import prompt_optimizer as compiler
import translation_request_cache as memo
from test_translation_instruction_cost_quality import CORRECT_NOTICE, response
from test_translation_notice_availability import SOURCE


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    monkeypatch.setattr(app, 'LAST_TRANSLATE_DEBUG_FILE', str(tmp_path / 'debug.json'))
    monkeypatch.setattr(app, 'last_translate_debug', {})
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(previous)


def outgoing(monkeypatch, source, target):
    calls = []
    def capture(**kwargs):
        calls.append(kwargs)
        return response(target)
    monkeypatch.setattr(app.ai.chat.completions, 'create', capture)
    app._tl.semantic_contract = app.build_translation_semantic_contract(source, 'zh', 'id')
    assert app.translate_openai(source, 'zh', 'id') == target
    assert len(calls) == 1
    return calls[0]


def test_record_category_conflict_is_removed_at_actual_provider_boundary(monkeypatch):
    source = '這是入非本月沒有換TAG嗎？'
    target = 'Apakah data ini sudah dimasukkan ke kategori bukan untuk bulan ini, tetapi TAG-nya belum diganti?'
    request = outgoing(monkeypatch, source, target)
    prompt = request['messages'][0]['content']
    assert 'RECORD CATEGORY' in prompt
    assert 'TAG replacement is NOT YET done' in prompt
    assert 'The source is a question' in prompt
    assert '非本月=bukan order bulan ini' not in prompt
    assert '<immutable_data>' in prompt and '- TAG' in prompt
    assert request['messages'][-1]['content'].endswith(source)
    assert not request['response_validator'](response(target.replace('belum diganti', 'sudah diganti')), 'test')[0]


def test_hard_term_has_one_owner_in_real_prompt(monkeypatch):
    request = outgoing(monkeypatch, '套環要補上', 'Cincin Pelindung perlu dipasang.')
    prompt = request['messages'][0]['content']
    assert prompt.count('套環 => Cincin Pelindung') == 1
    assert 'HARD mappings must use the exact target term;' in prompt
    assert request['translation_fast_quality'] is True
    assert request['translation_max_generations'] == 2
    assert callable(request['response_validator'])


def test_factored_notice_keeps_every_claim_and_current_source(monkeypatch):
    request = outgoing(monkeypatch, SOURCE, CORRECT_NOTICE)
    prompt = request['messages'][0]['content']
    frame = audit.build_source_frame(SOURCE, 'zh', 'id')
    assert frame['active']
    for claim in frame['claims']:
        assert 'Claim ' + claim['claim_id'] + ':' in prompt
        assert 'source=' + claim['source_evidence'] in prompt
        assert claim['meaning_zh'] in prompt
        assert claim['required_target_meaning_id'] in prompt
    assert request['messages'][-1]['content'].endswith(SOURCE)
    bad = CORRECT_NOTICE.replace('Pemeriksaan PMI wajib dilakukan', 'Pemeriksaan PMI tidak perlu dilakukan')
    assert not request['response_validator'](response(bad), 'test')[0]


def test_same_concept_does_not_merge_distinct_item_states():
    source = '1.不要讓庫存降不下來。\n2.不要讓庫存下降。\n3.不要讓庫存降不下來。'
    frame = audit.build_source_frame(source, 'zh', 'id')
    prompt = audit.build_prompt(frame)
    for claim in frame['claims']:
        assert claim['claim_id'] in prompt and claim['source_evidence'] in prompt
    good = '1. Pastikan stok bisa berkurang.\n2. Jangan biarkan stok turun.\n3. Pastikan stok bisa berkurang.'
    wrong = '1. Jangan biarkan stok turun.\n2. Pastikan stok bisa berkurang.\n3. Pastikan stok bisa berkurang.'
    assert audit.validate_translation(frame, good)[0]
    assert not audit.validate_translation(frame, wrong)[0]


@pytest.mark.parametrize('active,expected_old', [(True, False), (False, True)])
def test_authority_is_from_active_source_claims_not_mentions(active, expected_old):
    contract = {'risks': [{'sense': 'factory_source_semantic_frame', 'frame': {
        'active': active, 'claims': [{'source_evidence': 'item 2: 儲格',
        'meaning_zh': '一個有編號的儲存格', 'required_target_meaning_id': 'slot bernomor'}]}}]}
    full = ('<role>translator</role><semantic_contract>儲格 = slot bernomor</semantic_contract>'
            '<factory_vocabulary>儲格=old storage meaning, 外儲格=outer storage</factory_vocabulary>'
            '<context_disambiguation>a) 儲格=old context meaning</context_disambiguation>')
    prompt, _ = compiler.compile_translation_prompt(full, '儲格和外儲格', 'zh', 'id',
        authoritative_terms=compiler.authoritative_terms(contract))
    assert ('old storage meaning' in prompt) == expected_old
    assert ('old context meaning' in prompt) == expected_old
    assert '外儲格=outer storage' in prompt
    assert '儲格 = slot bernomor' in prompt


def test_advisory_example_cannot_suppress_glossary():
    contract = {'risks': [{'sense': 'verified_correction_cases', 'cases': [{
        'source_evidence': '儲格', 'meaning_zh': 'anything', 'required_target_meaning_id': 'anything'}]}]}
    assert compiler.authoritative_terms(contract) == set()


@pytest.mark.parametrize('table,remaining', [
    ('[HARD] 套環 => Cincin Pelindung', [('標籤', 'label produk')]),
    ('[SOFT] 套環 ~= Cincin Pelindung', [('套環', 'Cincin Pelindung'), ('標籤', 'label produk')]),
    ('[HARD] 套環 => cincin lain', [('套環', 'Cincin Pelindung'), ('標籤', 'label produk')]),
])
def test_only_identical_hard_pairs_can_skip_duplicate_table(table, remaining):
    prompt = ('<translation_principles>x</translation_principles><source_terminology>'
              '<factory_terminology>HARD mappings must use the exact target term;\n'
              + table + '\n</factory_terminology></source_terminology>')
    pairs = [('套環', 'Cincin Pelindung'), ('標籤', 'label produk')]
    assert compiler.uncovered_glossary_pairs(prompt, pairs) == remaining
    assert compiler.uncovered_glossary_pairs('legacy ' + table, pairs) == pairs


def test_tiny_optional_budget_keeps_hard_facts_learning_and_references():
    required = {'semantic_contract': 'I5 stop, I15 run.',
                'source_terminology': '[HARD] 套環 => Cincin Pelindung',
                'learned_translation_policy': 'Verification precedes submission.',
                'translation_reference_context': 'Evidence only; current source wins.'}
    full = '<role>translator</role>' + ''.join(f'<{key}>{value}</{key}>' for key, value in required.items())
    prompt, stats = compiler.compile_translation_prompt(full, 'I5停機', 'zh', 'id', max_chars=1)
    assert all(value in prompt for value in required.values())
    assert compiler.prompt_contains_required_invariants(prompt)
    assert not stats.fallback_used


def test_fast_detach_preserves_shared_children_cycles_and_tuple_identity():
    child = {'facts': ['I5', 'stop']}
    loop = []
    root = (child, child, loop)
    loop.append(root)
    with memo.scope():
        assert memo.reuse('cycle', (), lambda: root) is root
        one = memo.reuse('cycle', (), lambda: pytest.fail('recomputed'))
        assert one is not root and one[0] is one[1]
        assert one[2][0] is one
        one[0]['facts'][1] = 'run'
        two = memo.reuse('cycle', (), lambda: pytest.fail('recomputed'))
        assert two[0]['facts'] == ['I5', 'stop']
        assert two[2][0] is two


def test_fast_detach_respects_subclass_deepcopy_protocol():
    class ProtectedDict(dict):
        def __deepcopy__(self, state):
            result = type(self)()
            state[id(self)] = result
            result['facts'] = copy.deepcopy(self['facts'], state)
            result['copied_by_protocol'] = True
            return result
    original = ProtectedDict(facts=['do not start'])
    with memo.scope():
        memo.reuse('custom', (), lambda: original)
        result = memo.reuse('custom', (), lambda: None)
        assert type(result) is ProtectedDict and result['copied_by_protocol']
        result['facts'].clear()
        assert memo.reuse('custom', (), lambda: None)['facts'] == ['do not start']


def test_compact_debug_persistence_preserves_all_structured_evidence(tmp_path):
    snapshot = {'src_text': '支數\n2支', 'messages_sent': [{'role': 'system', 'content': '原樣 <TAG> \\"'}],
                'semantic_contract': {'risks': [{'values': [0, False, None, '1.20']} ]}}
    saved = app._replace_last_translate_debug(snapshot)
    assert app._load_last_translate_debug_from_disk() == saved
    app._update_last_translate_debug(final_candidate='Jumlah: 2 batang', pipeline_status='complete')
    data = json.loads(open(app.LAST_TRANSLATE_DEBUG_FILE, encoding='utf-8').read())
    assert data['semantic_contract'] == snapshot['semantic_contract']
    assert data['messages_sent'] == snapshot['messages_sent']
    assert data['src_text'] == snapshot['src_text']
    assert data['final_candidate'] == 'Jumlah: 2 batang'
