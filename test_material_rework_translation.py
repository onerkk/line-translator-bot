"""Reported production failure, alternative targets and adversarial neighbours.

Fixed candidates exercise guards and real pipeline wiring, not live AI quality.
"""
from types import SimpleNamespace

import pytest

import app
import factory_instruction_semantics as instructions
import factory_knowledge as knowledge
import factory_rework_semantics as rework
import factory_semantic_audit as audit
import translation_quality_gate as quality
import translation_retry_queue as queue
from test_translation_notice_availability import runtime, event, delivered_text
from test_translation_instruction_cost_quality import offline_transport, response

SOURCE = '這把噴漆錯誤的記得重洗'
REPORTED = 'Yang salah pengecatan semprot, ingat untuk dicuci ulang.'
GOOD = 'Jangan lupa cuci ulang bundel yang salah dicat semprot ini.'


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    monkeypatch.setattr(app, 'translation_cache', {})
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(previous)


def test_reported_target_is_rejected_by_raw_relation_and_quality_checks():
    relations = instructions.build_relations(SOURCE)
    assert relations and relations[0]['kind'] == 'material_rework'
    assert instructions.validate_relations(relations, REPORTED)
    assert not quality.validate_translation(SOURCE, REPORTED, 'zh', 'id').ok
    prompt = audit.build_prompt(audit.build_source_frame(SOURCE, 'zh', 'id'))
    assert 'bundel ini' in prompt and 'cuci ulang' in prompt


@pytest.mark.parametrize('target', [
    GOOD,
    'Bundel ini salah disemprot cat, jangan lupa dicuci ulang.',
    'Ingat untuk mencuci ulang bundel yang pengecatan semprotnya salah ini.',
    'Bundel yang keliru dicat dengan semprotan ini perlu dicuci lagi.',
])
def test_natural_inflections_are_accepted_and_not_rewritten(target):
    cards = knowledge.retrieve(SOURCE, 'zh', 'id')
    assert knowledge.validate_translation(cards, SOURCE, target) == (True, [])
    assert instructions.validate_relations(instructions.build_relations(SOURCE), target) == []
    assert quality.canonicalize_source_terms(SOURCE, target, 'zh', 'id') == target
    assert app._final_delivery_guard(SOURCE, target, 'zh', 'id') == target


@pytest.mark.parametrize('target', [
    'Jangan lupa cuci ulang yang salah dicat semprot.',  # missing material referent
    'Jangan lupa cuci ulang batang yang salah dicat semprot ini.',  # piece vs bundle
    'Jangan lupa cuci ulang bundel yang salah dicat semprot itu.',
    'Jangan lupa mengecat semprot ulang bundel yang salah dicat semprot ini.',
    'Bundel yang salah dicat semprot ini sudah dicuci ulang.',
    'Bundel yang salah dicat semprot ini jangan dicuci ulang.',
    'Jangan lupa cuci bundel yang salah dicat semprot ini.',  # repeat omitted
    'Jangan lupa cuci ulang bundel ini yang tidak salah dicat semprot.',
    'Jangan lupa cuci ulang bundel yang salah dicat semprot ini lalu dicat ulang.',
    'Jangan lupa cuci ulang bundel yang salah warna cat semprot ini.',
    'Jangan lupa cuci ulang bundel yang salah dicat semprot ini dengan thinner.',
    'Bundel yang salah dicat semprot ini sudah selesai. Bundel itu harus dicuci ulang.',
])
def test_wrong_object_action_state_or_added_instructions_do_not_pass(target):
    assert instructions.validate_relations(instructions.build_relations(SOURCE), target)
    assert app._final_delivery_guard(SOURCE, target, 'zh', 'id') is None


@pytest.mark.parametrize('source,target', [
    ('這捆噴漆有誤，別忘了重新清洗。', 'Jangan lupa cuci ulang bundel yang keliru dicat semprot ini.'),
    ('那把噴錯漆的再洗一次。', 'Bundel yang salah dicat semprot itu perlu dicuci ulang.'),
    ('這支材料噴漆錯誤，記得重洗。', 'Ingat untuk mencuci ulang batang yang salah dicat semprot ini.'),
    ('那批噴漆有誤，重新清洗。', 'Cuci ulang batch yang salah dicat semprot itu.'),
    ('這把噴漆錯誤的不要重洗。', 'Bundel yang salah dicat semprot ini jangan dicuci ulang.'),
    ('這把噴漆錯誤的已經重洗好了。', 'Bundel yang salah dicat semprot ini sudah dicuci ulang.'),
    ('這把噴漆錯誤的還沒重洗。', 'Bundel yang salah dicat semprot ini belum dicuci ulang.'),
    ('這把噴漆錯誤的記得重噴。', 'Jangan lupa mengecat semprot ulang bundel yang salah dicat semprot ini.'),
    ('這把噴漆錯誤的不要忘記重洗。', GOOD),
])
def test_rephrasing_classifiers_and_opposite_states_share_the_same_relation(source, target):
    relations = instructions.build_relations(source)
    assert relations and not instructions.validate_relations(relations, target)


@pytest.mark.parametrize('source', [
    '這把刀噴漆錯誤的記得重洗。', '這把噴漆槍記得重洗。',
    '把噴漆錯誤的零件重新清洗。', '這把材料沒有噴錯漆，不用重洗。',
    '噴漆錯誤原因尚未確認。', '這把刀和那把傘都要清洗。',
    '這把噴漆錯誤的要不要重洗？',
    '「這把噴漆錯誤的記得重洗」只是待修改的標題。',
])
def test_disposal_particle_explicit_tools_and_quoted_titles_do_not_invent_bundles(source):
    assert not rework.build_relations(source)
    assert rework.canonicalize_noun_phrase(source, REPORTED) == REPORTED


def test_numbered_items_cannot_borrow_another_materials_remedy():
    source = '1.這把噴漆錯誤的記得重洗。\n2.那把噴漆錯誤的不要重洗。'
    good = '1. Jangan lupa cuci ulang bundel yang salah dicat semprot ini.\n2. Bundel yang salah dicat semprot itu jangan dicuci ulang.'
    relations = instructions.build_relations(source)
    assert len(relations) == 2 and not instructions.validate_relations(relations, good)
    assert instructions.validate_relations(relations, good.replace('2. Bundel', '2. Jangan lupa cuci ulang bundel').replace('itu jangan dicuci ulang', 'itu'))


@pytest.mark.parametrize('source,target', [
    ('這把已經噴錯漆，記得重洗。', 'Bundel yang sudah salah dicat semprot ini harus dicuci ulang.'),
    (SOURCE, 'Bundel ini salah dicat semprot harus dicuci ulang.'),
    (SOURCE, 'Bundel yang salah dicat semprot ini cuci ulang.'),
    ('這把噴錯漆的已經重洗過了。', 'Bundel yang salah dicat semprot ini sudah dicuci ulang.'),
])
def test_painting_completion_and_washing_status_are_distinct(source, target):
    relations = instructions.build_relations(source)
    assert relations and not instructions.validate_relations(relations, target)


def test_other_objects_demonstrative_does_not_excuse_wrong_bundle():
    target = 'Cuci ulang bundel itu dan batang ini yang salah dicat semprot.'
    assert instructions.validate_relations(instructions.build_relations(SOURCE), target)


@pytest.mark.parametrize('candidate', [
    REPORTED, 'Bundel yang salah pengecatan semprot, ingat untuk dicuci ulang.',
])
def test_bounded_noun_phrase_repair_retains_washing_and_is_idempotent(candidate):
    fixed = quality.canonicalize_source_terms(SOURCE, candidate, 'zh', 'id')
    assert fixed == 'Bundel yang salah dicat semprot ini, ingat untuk dicuci ulang.'
    assert quality.canonicalize_source_terms(SOURCE, fixed, 'zh', 'id') == fixed
    assert quality.validate_translation(SOURCE, fixed, 'zh', 'id').ok


@pytest.mark.parametrize('candidate', [
    REPORTED.replace('dicuci ulang', 'dicat ulang'),
    REPORTED.replace('dicuci ulang', 'jangan dicuci ulang'),
    'Batang yang salah pengecatan semprot, ingat untuk dicuci ulang.',
    'Bundel yang salah pengecatan semprot itu, ingat untuk dicuci ulang.',
])
def test_local_grammar_repair_does_not_hide_other_semantic_errors(candidate):
    assert quality.canonicalize_source_terms(SOURCE, candidate, 'zh', 'id') == candidate


def test_actual_line_handler_fixes_reported_candidate_in_one_generation(runtime):
    runtime.provider_result = REPORTED
    app.handle_message(event(SOURCE))
    actual = delivered_text(runtime)
    assert 'Bundel yang salah dicat semprot ini' in actual
    assert 'dicuci ulang' in actual and 'salah pengecatan semprot' not in actual
    assert len(runtime.generations) == 1 and len(runtime.sends) == 1
    assert queue.pending_count() == 0


def test_real_coordinator_accepts_natural_verb_on_first_generation(offline_transport, monkeypatch):
    import ai_provider
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(kwargs)
        return response(GOOD)
    monkeypatch.setattr(ai_provider, '_dispatch_provider', dispatch)
    app._tl.semantic_contract = app.build_translation_semantic_contract(SOURCE, 'zh', 'id')
    result = app.translate_openai(SOURCE, 'zh', 'id')
    assert result == GOOD and len(calls) == 1


def test_known_grammar_repair_is_applied_before_paying_for_regeneration():
    check = app._build_translation_response_validator(SOURCE, 'zh', 'id')
    assert check(response(REPORTED), 'offline')[0]


def test_full_translation_route_keeps_one_generation_for_reported_grammar(offline_transport, monkeypatch):
    import ai_provider
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(kwargs)
        return response(REPORTED)
    monkeypatch.setattr(ai_provider, '_dispatch_provider', dispatch)
    actual = app.translate(SOURCE, 'zh', 'id')
    assert actual == 'Bundel yang salah dicat semprot ini, ingat untuk dicuci ulang.'
    assert len(calls) == 1


@pytest.mark.parametrize('target', [
    'Bundel ini salah disemprot cat, jangan lupa dicuci ulang.',
    GOOD, 'Penyemprotan cat pada bundel ini salah, harap dicuci ulang.',
])
def test_every_painting_gate_accepts_the_same_concept_and_preserves_inflection(target):
    risk = app._classify_factory_domain_terms_zh_id(SOURCE)
    assert app._repair_factory_domain_term_translation(target, risk) == target
    entry = app._FACTORY_DOMAIN_TERM_MAP_ZH_ID['spray_cat']
    assert app._factory_domain_translation_contains(entry, target.lower())
    contract = app.build_translation_semantic_contract(SOURCE, 'zh', 'id')
    assert app.translation_satisfies_semantic_contract(contract, target)[0]


def test_previously_accepted_bad_source_cache_is_not_reused_as_valid():
    # Asset version changed; even direct admission with the old content is
    # rejected. The final boundary can apply the same bounded local repair.
    app.cache_set(SOURCE, 'zh', 'id', REPORTED)
    assert app.translation_cache == {}
    fixed = app._final_delivery_guard(SOURCE, REPORTED, 'zh', 'id')
    assert fixed and fixed != REPORTED
