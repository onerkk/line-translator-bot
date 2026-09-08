"""Offline transport/cache regressions; no live model accuracy claims."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import app
import ai_provider
import conversation_context as cc
import prompt_optimizer
import translation_request_guard as guard
from test_original_conversation_context import (
    isolated, pipeline, runtime, offline_transport, pair, CURRENT, GOOD, G, A, B,
)
from test_translation_instruction_cost_quality import response


@pytest.fixture(autouse=True)
def empty_context_cache():
    guard._context_results.clear()
    ai_provider._openai_cache_unsupported_until.clear()
    yield
    guard._context_results.clear()
    ai_provider._openai_cache_unsupported_until.clear()


def bind(snapshot):
    app._tl.group_id, app._tl.user_id = G, B
    app._tl.conversation_snapshot = snapshot


def test_real_pipeline_reuses_same_context_without_a_second_generation(pipeline):
    snapshot = pair(app._conversation_journal())
    bind(snapshot)
    first = app.translate(CURRENT, 'zh', 'id')
    assert first == '@十元 ' + GOOD
    assert len(pipeline.calls) == 1
    assert guard._context_results
    # A new delivery ID with identical effective evidence must reuse the result.
    changed = copy.deepcopy(snapshot)
    changed['message_id'] = 'new-delivery-id'
    bind(changed)
    assert app.translate(CURRENT, 'zh', 'id') == first
    assert len(pipeline.calls) == 1
    # Contextual results are never taught to source-only caches.
    assert not app.translation_cache


def test_withdrawn_context_cannot_reuse_old_translation(pipeline):
    snapshot = pair(app._conversation_journal())
    bind(snapshot)
    assert app.translate(CURRENT, 'zh', 'id') == '@十元 ' + GOOD
    assert len(pipeline.calls) == 1
    app._conversation_journal().remove(G, 'request')
    bind(snapshot)
    with cc.scope(app._conversation_journal().sanitize(snapshot)):
        assert not cc.current_for(CURRENT)['entries']
        assert app._context_translation_request_key(CURRENT, 'zh', 'id') is None


def test_effective_context_keys_keep_time_order_and_identity(pipeline):
    snapshot = pair(app._conversation_journal())
    bind(snapshot)
    def key(snap, text=CURRENT):
        with cc.scope(snap):
            return app._context_translation_request_key(text, 'zh', 'id')
    original = key(snapshot)
    assert original
    metadata = copy.deepcopy(snapshot)
    metadata['message_id'] = 'redelivery'
    assert key(metadata) == original
    for edit in (
        lambda s: s.update(timestamp=s['timestamp'] + 2),
        lambda s: s['entries'][0].update(text='把工具放到架上'),
        lambda s: s['entries'][0].update(author='another-worker'),
        lambda s: s.update(recipients=['another-worker']),
    ):
        changed = copy.deepcopy(snapshot)
        edit(changed)
        assert key(changed) != original


@pytest.mark.parametrize('field,value', [
    ('tone', 'formal'), ('tone_custom', '保留疑問語氣'),
    ('translation_variant', 'literal'), ('group_id', 'another-group'),
    ('user_id', A), ('quoted_context_source', '請先停機'),
    ('protected_name_map', {'__MENTION_0__': '@山多'}),
    ('force_model', 'gpt-4.1-mini'),
])
def test_settings_and_scopes_do_not_share_context_results(pipeline, field, value):
    snapshot = pair(app._conversation_journal())
    bind(snapshot)
    with cc.scope(snapshot):
        original = app._context_translation_request_key(CURRENT, 'zh', 'id')
        setattr(app._tl, field, value)
        assert app._context_translation_request_key(CURRENT, 'zh', 'id') != original


def test_glossary_update_invalidates_context_key(pipeline, monkeypatch):
    snapshot = pair(app._conversation_journal())
    bind(snapshot)
    with cc.scope(snapshot):
        original = app._context_translation_request_key(CURRENT, 'zh', 'id')
        monkeypatch.setattr(app, 'GLOSSARY_LOOKUP', {**app.GLOSSARY_LOOKUP, '新詞': 'istilah baru'})
        assert app._context_translation_request_key(CURRENT, 'zh', 'id') != original


def test_same_station_context_can_reuse_but_other_station_cannot(pipeline, monkeypatch):
    bind(cc.empty(G, CURRENT))
    monkeypatch.setattr(app.line_factory_features, 'station_scope', lambda: 'I5')
    monkeypatch.setattr(app.line_factory_features, 'station_prompt', lambda: 'Station: I5')
    first = app._context_translation_request_key(CURRENT, 'zh', 'id')
    assert first
    monkeypatch.setattr(app.line_factory_features, 'station_prompt', lambda: 'Station: I15')
    assert app._context_translation_request_key(CURRENT, 'zh', 'id') != first


def test_media_and_unavailable_context_are_not_cached(pipeline, monkeypatch):
    snapshot = pair(app._conversation_journal())
    bind(snapshot)
    with cc.scope(snapshot):
        app._tl.from_image_ocr = True
        assert app._context_translation_request_key(CURRENT, 'zh', 'id') is None
        app._tl.from_image_ocr = False
        monkeypatch.setattr(app, 'get_recent_media_scene', lambda *a: 'photo evidence')
        assert app._context_translation_request_key(CURRENT, 'zh', 'id') is None


def test_context_cache_expiry_capacity_and_oversize(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(guard.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(guard, 'CONTEXT_RESULT_MAX_ENTRIES', 2)
    guard.set_context_result('a', 'first')
    guard.set_context_result('b', 'second')
    assert guard.get_context_result('a') == 'first'
    guard.set_context_result('c', 'third')
    assert guard.get_context_result('b') is None
    guard.set_context_result('large', 'x' * (guard.CONTEXT_RESULT_MAX_CHARS + 1))
    assert guard.get_context_result('large') is None
    clock[0] += guard.CONTEXT_RESULT_TTL
    assert guard.get_context_result('a') is None


def test_concurrent_identical_work_runs_once_without_blocking_other_keys():
    entered, release, other_done = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def run(key):
        with guard.serialize_request(key):
            cached = guard.get_context_result(key)
            if cached:
                return cached
            calls.append(key)
            if key == 'shared':
                entered.set()
                assert release.wait(3)
            else:
                other_done.set()
            guard.set_context_result(key, key + ' result')
            return key + ' result'
    with ThreadPoolExecutor(max_workers=6) as pool:
        owner = pool.submit(run, 'shared')
        assert entered.wait(3)
        followers = [pool.submit(run, 'shared') for _ in range(4)]
        independent = pool.submit(run, 'other')
        assert other_done.wait(3)
        release.set()
        assert [f.result() for f in [owner, *followers]] == ['shared result'] * 5
        assert independent.result() == 'other result'
    assert calls.count('shared') == 1
    assert not guard._requests


def test_unapproved_results_and_exceptions_are_never_admitted(pipeline, monkeypatch):
    def unapproved(*args):
        app._tl.context_result_cacheable = False
        return 'unverified candidate'
    monkeypatch.setattr(app, '_translate_core', unapproved)
    assert app._translate_with_context_reuse('rejected', CURRENT, 'zh', 'id') == 'unverified candidate'
    assert guard.get_context_result('rejected') is None
    def failed(*args):
        raise RuntimeError('transport down')
    monkeypatch.setattr(app, '_translate_core', failed)
    with pytest.raises(RuntimeError):
        app._translate_with_context_reuse('failed', CURRENT, 'zh', 'id')
    assert guard.get_context_result('failed') is None
    assert not guard._requests


def test_tone_and_variants_follow_the_identical_stable_prefix():
    source = '<role>Translator.</role><output_format>Only translation.</output_format>'
    prompts = [prompt_optimizer.compile_translation_prompt(source, '請檢查標籤。', 'zh', 'id',
               tone_instruction=tone, variant=variant)[0]
               for tone, variant in [('friendly', 'default'), ('formal', 'formal')]]
    boundary = '</translation_principles>'
    assert prompts[0].split(boundary)[0] == prompts[1].split(boundary)[0]
    assert 'friendly' in prompts[0].split(boundary)[1]
    assert 'formal' in prompts[1].split(boundary)[1]


def test_compiled_claude_keeps_full_source_rules_without_legacy_rewrapping():
    compiled = prompt_optimizer.compile_translation_prompt(
        '<role>Translator.</role><semantic_contract>I5 must stop; I15 must run.</semantic_contract>',
        'I5停機，I15開機。', 'zh', 'id')[0]
    result = ai_provider._wrap_system_prompt_xml(compiled, line_plain=True, role_strong=True,
        output_tag=True, cot_tag=False, success_criteria=False)
    assert result.startswith(compiled)
    assert result.count('<role>') == 1
    assert '<task>' not in result and '<rules>' not in result
    assert 'I5 must stop; I15 must run.' in result
    assert '<translation>...</translation>' in result
    assert len(result) - len(compiled) < 250


def test_structured_output_is_checked_as_translation_not_json(pipeline, monkeypatch):
    monkeypatch.setattr(app, 'structured_output_enabled', True)
    source, good = 'PMI還沒檢測。', 'PMI belum diperiksa.'
    app._tl.semantic_contract = app.build_translation_semantic_contract(source, 'zh', 'id')
    check = app._build_translation_response_validator(source, 'zh', 'id')
    assert check(response(json.dumps({'translation': good})), 'test')[0]
    assert not check(response(json.dumps({'translation': 'PMI sudah diperiksa.'})), 'test')[0]
    pipeline.candidates = [json.dumps({'translation': good})]
    assert app.translate_openai(source, 'zh', 'id') == good
    # At the actual coordinator boundary the response is accepted on the first call.
    assert len(pipeline.calls) == 1


def test_name_numbers_and_opposite_states_still_fail_provider_validation(pipeline):
    source = 'I5 PMI檢測尚未完成；I15 PMI檢測已經完成。'
    app._tl.semantic_contract = app.build_translation_semantic_contract(source, 'zh', 'id')
    check = app._build_translation_response_validator(source, 'zh', 'id')
    assert not check(response('I5 pemeriksaan PMI sudah selesai; I15 pemeriksaan PMI belum selesai.'), 'test')[0]
    assert not check(response('I5 pemeriksaan PMI belum selesai; I16 pemeriksaan PMI sudah selesai.'), 'test')[0]


@pytest.mark.parametrize('model,effort', [
    ('gemini-2.5-flash', 'none'), ('gemini-2.5-flash-lite', 'none'),
    ('gemini-2.5-pro', 'low'), ('gemini-3.1-flash-lite', 'minimal'),
    ('gemini-3.1-pro-preview', 'low'), ('gemini-3.7-flash', 'low'),
    ('gemini-3.8-flash', 'low'),
])
def test_gemini_fast_path_uses_model_supported_thinking(offline_transport, monkeypatch, model, effort):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return response('ok')
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(ai_provider, '_get_gemini_client', lambda: client)
    monkeypatch.setattr(ai_provider, '_client_with_limits', lambda c, t: c)
    ai_provider._chat_complete_gemini(model, [{'role': 'user', 'content': 'text'}], 512, fast_quality=True)
    assert calls[0]['reasoning_effort'] == effort
    assert len(calls) == 1


def test_openai_explicit_cache_wire_body_and_short_prefix(offline_transport, monkeypatch):
    from openai import OpenAI, _base_client
    # Use the HTTP implementation owned by the installed SDK (v3 uses httpx2).
    httpx = getattr(_base_client, 'httpx2', None) or _base_client.httpx
    received = []
    def handler(request):
        received.append(json.loads(request.content))
        return httpx.Response(200, json={
            'id': 'offline', 'object': 'chat.completion', 'created': 1, 'model': 'gpt-5.6-luna',
            'choices': [{'index': 0, 'finish_reason': 'stop',
                         'message': {'role': 'assistant', 'content': '<translation>ok</translation>'}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 1, 'total_tokens': 11},
        })
    compiled = prompt_optimizer.compile_translation_prompt('<role>x</role>', '你好', 'zh', 'id')[0]
    messages = [{'role': 'system', 'content': compiled}, {'role': 'user', 'content': '你好'}]
    with OpenAI(api_key='offline-only', max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        monkeypatch.setattr(ai_provider, '_get_openai_client', lambda: client)
        result = ai_provider._chat_complete_openai('gpt-5.6-luna', messages, max_completion_tokens=512)
    assert result.choices[0].message.content == 'ok'
    assert received[0]['prompt_cache_options'] == {'mode': 'explicit'}
    assert 'extra_body' not in received[0]  # SDK flattened to the documented wire field.
    assert received[0]['messages'][0]['content'].startswith(compiled)
    assert 'prompt_cache_breakpoint' not in json.dumps(received[0])
    assert len(received) == 1


def test_openai_caches_only_stable_prefix_and_never_pads_short_input():
    stable = '<role>x</role><translation_principles>' + 'Preserve facts. ' * 2000 + '</translation_principles>'
    dynamic = '<translation_style>formal</translation_style><semantic_contract>I5: stop.</semantic_contract>'
    messages = [{'role': 'system', 'content': stable + dynamic}, {'role': 'user', 'content': 'I5停機'}]
    kwargs = {'model': 'gpt-5.6-luna', 'messages': messages}
    assert ai_provider._configure_openai_translation_cache(kwargs)
    parts = kwargs['messages'][0]['content']
    assert parts[0]['text'] == stable and parts[0]['prompt_cache_breakpoint'] == {'mode': 'explicit'}
    assert parts[1]['text'] == dynamic and 'prompt_cache_breakpoint' not in parts[1]
    assert messages[0]['content'] == stable + dynamic  # Input is never mutated.
    for model in ['gpt-4.1-mini', 'gpt-5.4-mini', 'gemini-3.1-flash-lite']:
        assert not ai_provider._configure_openai_translation_cache({'model': model, 'messages': messages})


def test_cache_parameter_rejection_is_bounded_and_remembered(offline_transport, monkeypatch):
    calls = []
    def create(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        if 'prompt_cache_options' in (kwargs.get('extra_body') or {}):
            raise ValueError('400 unsupported prompt_cache_options')
        return response('ok')
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(ai_provider, '_get_openai_client', lambda: client)
    monkeypatch.setattr(ai_provider, '_client_with_limits', lambda c, t: c)
    content = '<role>x</role><translation_principles>Preserve facts.</translation_principles>'
    messages = [{'role': 'system', 'content': content}, {'role': 'user', 'content': 'text'}]
    for _ in range(2):
        ai_provider._chat_complete_openai('gpt-5.6-luna', messages, max_completion_tokens=512)
    assert len(calls) == 3
    assert 'extra_body' not in calls[1] and 'extra_body' not in calls[2]


def outcome(**extra):
    return dict(source_text=CURRENT, candidate_text=GOOD, final_text=GOOD,
        src_lang='zh', tgt_lang='id', group_id=G, issues=[],
        path='single_api_local_validation', reviewed=False, cacheable=False, **extra)


def test_valid_context_does_not_teach_a_false_review_risk(pipeline, monkeypatch):
    # Verify independently of the new cache: different/expired context still
    # must not inherit an extra review solely from cache-admission policy.
    monkeypatch.setenv('TRANSLATION_CONTEXT_CACHE_ENABLED', '0')
    snapshot = pair(app._conversation_journal())
    for i in range(3):
        bind(snapshot)
        assert app.translate(CURRENT, 'zh', 'id') == '@十元 ' + GOOD
        assert len(pipeline.calls) == i + 1
    assert not app.al_module.assess_review_risk(CURRENT, 'zh', 'id', G)['requires_review']
    assert app.al_module.record_translation_outcome(**outcome()) == {'recorded': False, 'risk_updated': False}


def test_old_no_issue_risk_rows_are_ignored_without_deleting_history(pipeline, monkeypatch):
    al = app.al_module
    # Reproduce the previous version's stored false 0.65 signal.
    with monkeypatch.context() as m:
        m.setattr(al, '_risk_weight', lambda **k: 0.65)
        saved = al.record_translation_outcome(**outcome(event_type='translation_warning'))
    assert saved['risk_updated']
    assert not al.assess_review_risk(CURRENT, 'zh', 'id', G)['requires_review']
    with al._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM learning_events').fetchone()[0] == 1


def test_actual_old_defects_survive_later_empty_issue_overwrite(pipeline):
    al = app.al_module
    event = outcome()
    event['issues'] = ['conversation_context:release_reply_changed_to_placement_or_action_missing']
    assert al.record_translation_outcome(**event)['risk_updated']
    with al._connect() as db:
        db.execute("UPDATE risk_patterns SET issue_codes='[]',last_path='single_api_local_validation'")
    risk = al.assess_review_risk(CURRENT, 'zh', 'id', G)
    assert risk['requires_review']
    assert event['issues'][0] in risk['reasons']
    assert not al.assess_review_risk(CURRENT, 'zh', 'id', 'other-group')['requires_review']


def test_negative_feedback_invalidates_context_cache_key(pipeline):
    snapshot = pair(app._conversation_journal())
    bind(snapshot)
    with cc.scope(snapshot):
        before = app._context_translation_request_key(CURRENT, 'zh', 'id')
        event = outcome()
        event['issues'] = ['user_negative_translation_feedback']
        assert app.al_module.record_translation_outcome(**event)['risk_updated']
        after = app._context_translation_request_key(CURRENT, 'zh', 'id')
        assert before != after
