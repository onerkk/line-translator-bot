"""Real provider parsing/guards/outbox/LINE framing, with external I/O replaced.

These verify delivery and call counts for supplied provider candidates, not
live model accuracy or network latency. Keep variants, opposites and outages.
"""
import socket
import traceback
from types import SimpleNamespace

import pytest
import app
import ai_provider
import factory_message_semantics as semantics
import translation_retry_queue as queue
from test_translation_notice_availability import runtime, event, delivered_text, retry_pending
from test_translation_instruction_cost_quality import offline_transport, response

ORIGINAL_TRANSLATE_OPENAI = app.translate_openai
MENTION = '@小麥（研磨股班長）'
SOURCE = MENTION + ' 麻煩優先放行'
GOOD = 'Tolong prioritaskan release data ke stasiun berikutnya.'


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    # A preceding intentional bad-output test must not create learned risk
    # for another test. Keep the real risk assessor on its own fresh database.
    monkeypatch.setenv('ACTIVE_LEARNING_DB_PATH', str(tmp_path / 'learning.db'))
    monkeypatch.setattr(app.al_module, 'AL_DB_PATH', None)
    monkeypatch.setattr(app.al_module, '_init_done', False)
    def offline(*_a, **_k):
        raise AssertionError('Regression tests may not connect to production')
    monkeypatch.setattr(socket.socket, 'connect', offline)
    try:
        yield
    finally:
        app._tl.__dict__.clear()
        app._tl.__dict__.update(previous)


@pytest.fixture
def real_pipeline(runtime, offline_transport, monkeypatch):
    # runtime stubs translate_openai; restore it so the actual model response
    # parser, placeholder recovery, semantic contract and final guard all run.
    monkeypatch.setattr(app, 'translate_openai', ORIGINAL_TRANSLATE_OPENAI)
    runtime.calls = []
    runtime.call_paths = []
    runtime.candidate = GOOD
    def dispatch(provider, **kwargs):
        runtime.calls.append(provider)
        runtime.call_paths.append([(f.name, f.lineno) for f in traceback.extract_stack() if f.filename.endswith('app.py')])
        return response('__MENTION_0__ ' + runtime.candidate)
    monkeypatch.setattr(ai_provider, '_dispatch_provider', dispatch)
    monkeypatch.setattr(app, 'cache_get', lambda *_a, **_k: None)
    monkeypatch.setattr(app, 'cache_set', lambda *_a, **_k: None)
    monkeypatch.setattr(app, 'get_conv_context_enabled', lambda *_a: False)
    monkeypatch.setattr(app, 'get_recent_media_scene', lambda *_a, **_k: '')
    return runtime


def release_event(*, native=True):
    e = event(SOURCE)
    if native:
        e.message.mention = SimpleNamespace(mentionees=[SimpleNamespace(
            index=0, length=len(MENTION.encode('utf-16-le')) // 2,
            user_id='recipient', type='user')])
    return e


@pytest.mark.parametrize('candidate', [
    GOOD,
    'Mohon prioritaskan pelepasan data ke proses berikutnya.',
    'Mohon prioritaskan rilis data ini.',
    'Tolong utamakan rilis data ke stasiun selanjutnya.',
    'Dahulukan proses pelepasan datanya.',
    'Mohon data ini dirilis lebih dahulu.',
    'Mohon rilis data ini sebagai prioritas.',
    'Data ini mohon diutamakan untuk dirilis.',
])
def test_equivalent_priority_grammar_passes_every_boundary(candidate):
    target = MENTION + ' ' + candidate
    frame = semantics.build_frame(SOURCE, 'zh', 'id')
    assert frame['active'] and not frame['complete']
    assert semantics.validate_translation(frame, target) == (True, [])
    app._tl.semantic_contract = app.build_translation_semantic_contract(SOURCE, 'zh', 'id')
    assert app._build_translation_response_validator(SOURCE, 'zh', 'id')(response(target), 'test')[0]
    assert app._final_delivery_guard(SOURCE, target, 'zh', 'id') == target


@pytest.mark.parametrize('native', [True, False])
@pytest.mark.parametrize('candidate', [GOOD,
    'Mohon prioritaskan pelepasan data ke proses berikutnya.',
    'Mohon prioritaskan rilis data ini.'])
def test_reported_message_reaches_line_with_one_generation(real_pipeline, native, candidate):
    state = real_pipeline
    state.candidate = candidate
    e = release_event(native=native)
    app.handle_message(e)
    actual = delivered_text(state)
    assert MENTION + ' ' + candidate in actual
    assert len(state.calls) == 1 and len(state.sends) == 1
    assert queue.pending_count() == 0
    # LINE redelivery must not generate or send the same translation twice.
    app.handle_message(e)
    assert len(state.calls) == 1 and len(state.sends) == 1


@pytest.mark.parametrize('bad', [
    'Tolong release data ke stasiun berikutnya.',  # priority omitted
    'Tolong prioritaskan meletakkan material di rak.',  # physical placement
    'Data sudah dirilis ke stasiun berikutnya terlebih dahulu.',  # completed
    'Jangan prioritaskan rilis data ke stasiun berikutnya.',  # opposite request
    'Data belum bisa dirilis ke stasiun berikutnya terlebih dahulu.',  # unable
    'Tolong rilis data ini, tetapi tidak perlu diprioritaskan.',  # priority denied
    'Prioritaskan pengemasan. Tolong rilis data ke stasiun berikutnya.',  # different action
    'Tolong prioritaskan pelepasan bundel. Data sudah diperiksa.',  # borrowed data noun
])
def test_opposites_and_priority_on_another_action_still_rejected(bad):
    target = MENTION + ' ' + bad
    assert not semantics.validate_translation(semantics.build_frame(SOURCE, 'zh', 'id'), target)[0]
    assert app._final_delivery_guard(SOURCE, target, 'zh', 'id') is None


@pytest.mark.parametrize('source, mode, good, bad', [
    ('不要優先放行', 'prohibited', 'Jangan prioritaskan rilis data.', 'Tolong prioritaskan rilis data.'),
    ('已經優先放行', 'completed', 'Data sudah dirilis terlebih dahulu.', 'Tolong prioritaskan rilis data.'),
    ('尚未優先放行', 'pending', 'Data belum dirilis terlebih dahulu.', 'Data sudah dirilis terlebih dahulu.'),
])
def test_priority_modifier_cannot_override_status_or_prohibition(source, mode, good, bad):
    frame = semantics.build_frame(source, 'zh', 'id')
    assert frame['slots']['release_relations'][0]['mode'] == mode
    assert semantics.validate_translation(frame, good) == (True, [])
    assert not semantics.validate_translation(frame, bad)[0]


def test_explicit_station_destination_is_kept_with_equivalent_next_word():
    source = '麻煩優先放行到下站別'
    frame = semantics.build_frame(source, 'zh', 'id')
    assert semantics.validate_translation(frame, 'Mohon prioritaskan rilis data ke stasiun selanjutnya.')[0]
    assert not semantics.validate_translation(frame, 'Mohon prioritaskan rilis data.')[0]
    assert not semantics.validate_translation(frame, 'Mohon prioritaskan rilis data ke stasiun sebelumnya.')[0]


def test_priority_stays_with_its_record_after_clause_reordering():
    source = 'I6資料麻煩優先放行。I7資料已經放行。'
    frame = semantics.build_frame(source, 'zh', 'id')
    good = 'Data I7 sudah dirilis. Tolong prioritaskan rilis data I6.'
    bad = 'Data I7 sudah dirilis terlebih dahulu. Tolong rilis data I6.'
    assert semantics.validate_translation(frame, good)[0]
    assert not semantics.validate_translation(frame, bad)[0]


def test_failed_line_delivery_retries_saved_translation_without_generating_again(real_pipeline):
    state = real_pipeline
    state.reply_down = state.push_down = True
    with pytest.raises(TimeoutError):
        app.handle_message(release_event())
    assert len(state.calls) == 1 and queue.pending_count() == 1
    state.push_down = False
    assert retry_pending()
    assert MENTION + ' ' + GOOD in delivered_text(state)
    assert len(state.calls) == 1 and queue.pending_count() == 0


def test_bad_provider_output_stays_queued_and_recovery_delivers_the_correct_translation(real_pipeline):
    state = real_pipeline
    state.candidate = 'Tolong prioritaskan meletakkan material di rak.'
    app.handle_message(release_event())
    assert not state.sends and queue.pending_count() == 1
    assert len(state.calls) == 2  # existing total generation budget
    state.candidate = GOOD
    assert retry_pending()
    assert MENTION + ' ' + GOOD in delivered_text(state)
    assert queue.pending_count() == 0


@pytest.mark.parametrize('image_on', [False, True])
def test_earlier_disabled_or_waiting_image_does_not_block_text(real_pipeline, monkeypatch, image_on):
    state = real_pipeline
    monkeypatch.setitem(app.group_img_settings, 'notice-group', image_on)
    monkeypatch.setitem(app.group_img_ask_settings, 'notice-group', False)
    monkeypatch.setattr(app, '_has_ai_capability', lambda capability: capability != 'vision')
    monkeypatch.setattr(app, 'store_pending_image_media_context', lambda *_a, **_k: None)
    image_event = event('')
    image_event.message.id = 'earlier-image'
    app.handle_image(image_event)
    assert queue.pending_count() == int(image_on)
    app.handle_message(release_event())
    assert MENTION + ' ' + GOOD in delivered_text(state)
    assert len(state.calls) == 1, state.call_paths
    assert queue.pending_count() == int(image_on)
    if image_on:
        assert queue.list_pending()[0]['job_kind'] == 'image'
