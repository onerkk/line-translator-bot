"""Conversation sequences with real parsing/guards and offline transports.

These are deterministic routing/validation tests, not live-model accuracy or
Internet latency measurements. Original evidence must survive failed replies.
"""
import json
import socket
import subprocess
import sys
import threading
import time
import traceback
from types import SimpleNamespace

import pytest

import app
import ai_provider
import conversation_context as cc
import factory_message_semantics as semantics
import translation_request_cache as memo
import translation_retry_queue as queue
from test_translation_notice_availability import runtime, event, delivered_text
from test_translation_instruction_cost_quality import offline_transport, response

REAL_OPENAI = app.translate_openai
REAL_TRANSLATE = app.translate
A, B, C = 'U' + 'a' * 32, 'U' + 'b' * 32, 'U' + 'c' * 32
G = 'notice-group'
PRIOR = '@小麥（研磨股班長） 麻煩優先放行'
CURRENT = '@十元 放了'
GOOD = 'Datanya sudah di-release.'


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    old = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    monkeypatch.setenv('ACTIVE_LEARNING_DB_PATH', str(tmp_path / 'learning.db'))
    monkeypatch.setattr(app.al_module, 'AL_DB_PATH', None)
    monkeypatch.setattr(app.al_module, '_init_done', False)
    monkeypatch.setattr(queue, 'DB_PATH', str(tmp_path / 'retry.db'))
    monkeypatch.setattr(app, 'get_conv_context_enabled', lambda *_a: True)
    monkeypatch.setattr(app, 'get_recent_media_scene', lambda *_a, **_k: '')
    def offline(*_a, **_k):
        raise AssertionError('No production connection allowed')
    monkeypatch.setattr(socket.socket, 'connect', offline)
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(old)


def pair(journal, prior=PRIOR, current=CURRENT, *, now=None, lang='zh'):
    now = now or time.time()
    journal.capture(G, 'request', prior, author=A, recipients=[B], lang='zh', timestamp=now-900)
    return journal.capture(G, 'answer', current, author=B, recipients=[A], lang=lang, timestamp=now)


def test_original_evidence_is_shared_across_processes_and_language_directions(tmp_path):
    path = str(tmp_path / 'context.db')
    now = time.time()
    script = ('import conversation_context as c; '
              'c.SourceJournal(' + repr(path) + ').capture(' + repr(G) + ', "request", '
              + repr(PRIOR) + ', author=' + repr(A) + ', recipients=[' + repr(B)
              + '], lang="zh", timestamp=' + repr(now-900) + ')')
    subprocess.run([sys.executable, '-c', script], check=True)
    snap = cc.SourceJournal(path).capture(G, 'answer', '@十元 sudah', author=B,
                                          recipients=[A], lang='id', timestamp=now)
    assert [row['text'] for row in snap['entries']] == [PRIOR]
    assert cc.resolve_action('@十元 sudah', 'id', snap)['sense'] == 'erp_data_release'
    assert 'release' not in json.dumps(snap['entries'])  # only original Chinese, no bot translation


@pytest.mark.parametrize('current', ['放了', '已經放好了', '還沒放', '好了', '處理好了', 'sudah', 'belum selesai'])
def test_short_replies_do_not_need_a_keyword_to_receive_history(current):
    snap = pair(app._conversation_journal(), current=current,
                lang='id' if current.isascii() else 'zh')
    assert cc.needs_history(current) and snap['entries']


@pytest.mark.parametrize('current,lang,good,bad', [
    (CURRENT, 'zh', '@十元 ' + GOOD, '@十元 sudah menaruhnya'),
    ('@十元 還沒放', 'zh', '@十元 Datanya belum dirilis.', '@十元 Data sudah dirilis.'),
    ('@十元 已經放好了', 'zh', '@十元 Data sudah dirilis.', '@十元 Tolong rilis data.'),
    ('@十元 sudah', 'id', '@十元 已經放行了。', '@十元 已經放到架上了。'),
    ('@十元 belum', 'id', '@十元 還沒放行。', '@十元 已放行。'),
])
def test_same_snapshot_controls_prompt_contract_provider_and_final_guard(current, lang, good, bad):
    target = 'id' if lang == 'zh' else 'zh'
    snapshot = pair(app._conversation_journal(), current=current, lang=lang)
    with memo.scope(), cc.scope(snapshot):
        frame = semantics.build_frame(current, lang, target)
        assert frame['context_bound'] and not frame['complete']
        assert semantics.validate_translation(frame, good) == (True, [])
        assert not semantics.validate_translation(frame, bad)[0]
        app._tl.semantic_contract = app.build_translation_semantic_contract(current, lang, target)
        validator = app._build_translation_response_validator(current, lang, target)
        assert validator(response(good), 'offline')[0]
        assert not validator(response(bad), 'offline')[0]
        assert app._final_delivery_guard(current, good, lang, target) == good
        messages = app._build_messages_with_fewshot('Translate.', current, lang, target, G, include_examples=False)
        assert PRIOR in '\n'.join(m['content'] for m in messages)
        assert messages[-1]['content'] == current
        assert not any(m['role'] == 'assistant' for m in messages)
        assert all(PRIOR not in m['content'] for m in messages if m['role'] == 'system')


@pytest.mark.parametrize('prior', ['@小麥（研磨股班長） 把工具放到架上',
                                   '@小麥（研磨股班長） 請把物品放在桌上'])
def test_physical_placement_is_not_overridden_by_an_old_release_request(prior):
    journal = app._conversation_journal()
    now = time.time()
    journal.capture(G, 'older', PRIOR, author=A, recipients=[B], timestamp=now-1100)
    snap = pair(journal, prior=prior, now=now)
    with cc.scope(snap):
        frame = semantics.build_frame(CURRENT, 'zh', 'id')
        assert semantics.validate_translation(frame, '@十元 Sudah menaruhnya.')[0]
        assert not semantics.validate_translation(frame, '@十元 ' + GOOD)[0]


@pytest.mark.parametrize('prior,current', [
    ('品保檢驗後放行', '放了'), ('先放行再檢查標籤', '好了'),
    (PRIOR, '我把工具放在架上了'), (PRIOR, '放了嗎？'),
    ('把工單放到桌上', '好了'), ('重新開啟電腦', '好了'),
])
def test_ambiguous_or_explicit_current_source_is_never_forced_into_erp(prior, current):
    snap = pair(app._conversation_journal(), prior=prior, current=current)
    resolution = cc.resolve_action(current, 'zh', snap)
    assert not resolution or resolution['sense'] != 'erp_data_release'


def test_other_group_participants_future_and_expired_turns_are_excluded():
    j, now = app._conversation_journal(), time.time()
    for group, mid, ts, author, recipient in [
        ('other-group', 'wrong-group', now-1, A, B),
        (G, 'other-thread', now-1, C, A),
        (G, 'expired', now-3601, A, B), (G, 'future', now+1, A, B),
    ]:
        j.capture(group, mid, PRIOR, author=author, recipients=[recipient], timestamp=ts)
    snap = j.capture(G, 'answer', CURRENT, author=B, recipients=[A], timestamp=now)
    assert snap['entries'] == []


def test_quote_wins_without_replaying_a_translated_answer():
    j, now = app._conversation_journal(), time.time()
    pair(j, now=now-1)
    j.capture(G, 'other-topic', '工具放到架上', author=A, recipients=[B], timestamp=now)
    snap = j.capture(G, 'quoted-answer', CURRENT, author=B, recipients=[A],
                     quoted_id='request', timestamp=now+1)
    assert [row['message_id'] for row in snap['entries']] == ['request']
    assert cc.resolve_action(CURRENT, 'zh', snap)['sense'] == 'erp_data_release'


def test_memo_and_cache_cannot_reuse_a_different_conversation():
    j, now = app._conversation_journal(), time.time()
    erp = pair(j, now=now)
    physical = pair(j, prior='把工具放到架上', now=now+1)
    with memo.scope():
        with cc.scope(erp):
            a = semantics.build_frame(CURRENT, 'zh', 'id')
            assert app._translation_cache_context_bound(CURRENT)
            app.cache_set(CURRENT, 'zh', 'id', GOOD, force=True)
            assert app.cache_get(CURRENT, 'zh', 'id') is None
        with cc.scope(physical):
            b = semantics.build_frame(CURRENT, 'zh', 'id')
        assert a['context_resolution']['sense'] != b['context_resolution']['sense']
        assert cc.fingerprint(erp) != cc.fingerprint(physical)
    assert cc.current_for(CURRENT) is None


def test_unsend_and_edit_invalidate_frozen_evidence_without_new_topic_substitution():
    j, now = app._conversation_journal(), time.time()
    snap = pair(j, now=now)
    j.remove(G, 'request')
    assert not j.sanitize(snap)['entries']
    j.capture(G, 'request', PRIOR, author=A, recipients=[B], timestamp=now-900)
    assert not j.sanitize(snap)['entries']  # delayed duplicate cannot resurrect
    snap = pair(j, now=now+1)  # tombstone remains
    assert not snap['entries']
    j.capture(G, 'editable', PRIOR, author=A, recipients=[B], timestamp=now+2)
    editable = j.capture(G, 'reply2', CURRENT, author=B, recipients=[A], timestamp=now+3)
    j.capture(G, 'editable', '改成先檢查標籤', author=A, recipients=[B], timestamp=now+4)
    assert 'editable' not in [row['message_id'] for row in j.sanitize(editable)['entries']]


@pytest.fixture
def pipeline(runtime, offline_transport, monkeypatch):
    monkeypatch.setattr(app, 'translate_openai', REAL_OPENAI)
    runtime.calls, runtime.prompts, runtime.paths = [], [], []
    runtime.candidates = ['__MENTION_0__ ' + GOOD]
    def dispatch(provider, **kwargs):
        runtime.calls.append(provider)
        runtime.paths.append([(f.name, f.lineno) for f in traceback.extract_stack() if f.filename.endswith('app.py')])
        runtime.prompts.append(kwargs.get('messages'))
        return response(runtime.candidates[min(len(runtime.calls)-1, len(runtime.candidates)-1)])
    monkeypatch.setattr(ai_provider, '_dispatch_provider', dispatch)
    return runtime


def message(text, mid, author, recipient, timestamp):
    e = event(text)
    e.message.id, e.source.user_id, e.timestamp = mid, author, int(timestamp*1000)
    label = cc.extract_mentions(text)[0]
    e.message.mention = SimpleNamespace(mentionees=[SimpleNamespace(
        index=0, length=len(label.encode('utf-16-le'))//2, user_id=recipient, type='user')])
    return e


def failed_first_turn(monkeypatch):
    now = time.time()
    with monkeypatch.context() as m:
        m.setattr(app, 'translate', lambda *_a, **_k: None)
        app.handle_message(message(PRIOR, 'request', A, B, now-900))
    return now


def test_failed_first_translation_still_informs_a_real_one_call_reply(pipeline, monkeypatch):
    now = failed_first_turn(monkeypatch)
    app.handle_message(message(CURRENT, 'answer', B, A, now))
    assert '@十元 ' + GOOD in delivered_text(pipeline)
    assert len(pipeline.calls) == 1, pipeline.paths
    assert PRIOR in json.dumps(pipeline.prompts, ensure_ascii=False)
    assert queue.get(G + ':request')  # missing first translation still queued
    assert not queue.get(G + ':answer')


def test_wrong_first_candidate_is_rejected_then_repaired_with_same_context(pipeline, monkeypatch):
    now = failed_first_turn(monkeypatch)
    pipeline.candidates = ['__MENTION_0__ sudah menaruhnya', '__MENTION_0__ ' + GOOD]
    app.handle_message(message(CURRENT, 'answer', B, A, now))
    assert '@十元 ' + GOOD in delivered_text(pipeline)
    assert 'menaruhnya' not in delivered_text(pipeline)
    assert len(pipeline.calls) == 2
    assert all(PRIOR in json.dumps(prompt, ensure_ascii=False) for prompt in pipeline.prompts)


def test_outbox_retry_uses_original_snapshot_after_group_topic_changes(pipeline, monkeypatch):
    now = failed_first_turn(monkeypatch)
    with monkeypatch.context() as m:
        m.setattr(app, 'translate', lambda *_a, **_k: None)
        app.handle_message(message(CURRENT, 'answer', B, A, now))
    saved = queue.get(G + ':answer')
    assert saved['payload']['conversation_snapshot']['entries'][0]['text'] == PRIOR
    app._conversation_journal().capture(G, 'new-topic', '把工具放到架上',
                                        author=A, recipients=[B], timestamp=now+1)
    assert app._translation_retry_attempt(saved)
    assert GOOD in delivered_text(pipeline)
    assert '把工具放到架上' not in json.dumps(pipeline.prompts, ensure_ascii=False)


def test_context_off_does_not_read_or_record_originals(pipeline, monkeypatch):
    now = failed_first_turn(monkeypatch)
    monkeypatch.setattr(app, 'get_conv_context_enabled', lambda *_a: False)
    pipeline.candidates = ['__MENTION_0__ Sudah menaruhnya.']
    app.handle_message(message(CURRENT, 'answer', B, A, now))
    assert PRIOR not in json.dumps(pipeline.prompts, ensure_ascii=False)
    assert 'Sudah menaruhnya' in delivered_text(pipeline)


@pytest.mark.parametrize('mode', ['natural', 'literal', 'formal'])
def test_translation_buttons_keep_the_original_action_after_a_new_topic(pipeline, mode):
    snapshot = pair(app._conversation_journal())
    context = {'original': CURRENT, 'translated': '@十元 ' + GOOD, 'src': 'zh', 'tgt': 'id',
               'conversation_snapshot': snapshot}
    app._conversation_journal().capture(G, 'later', '把工具放到架上', author=A, recipients=[B])
    result, _, _ = app._execute_translation_variant(context, mode, G, B)
    assert result == '@十元 ' + GOOD
    assert PRIOR in json.dumps(pipeline.prompts, ensure_ascii=False)
    assert '把工具放到架上' not in json.dumps(pipeline.prompts, ensure_ascii=False)


def test_multi_target_workers_receive_the_same_frozen_originals(monkeypatch):
    snapshot = pair(app._conversation_journal())
    app._tl.group_id, app._tl.conversation_snapshot = G, snapshot
    seen, barrier = [], threading.Barrier(2)
    def translate(text, src, tgt):
        barrier.wait(timeout=5)
        seen.append((threading.get_ident(), app._tl.conversation_snapshot))
        return 'test ' + tgt
    monkeypatch.setattr(app, 'translate', translate)
    assert len(app.translate_multi(CURRENT, 'zh', ['id', 'en'])) == 2
    assert len({row[0] for row in seen}) == 2
    assert all(row[1] == snapshot for row in seen)


def test_queue_snapshot_survives_history_expiry_but_not_a_withdrawal():
    journal = app._conversation_journal()
    snapshot = pair(journal)
    with journal.connect() as db:
        # Ordinary rolling-history TTL/size eviction is not an unsend.
        db.execute('DELETE FROM sources WHERE group_id=?', (G,))
        db.commit()
    assert journal.sanitize(snapshot)['entries'] == snapshot['entries']
    journal.remove(G, 'request')
    assert journal.sanitize(snapshot)['entries'] == []


def test_context_store_failure_is_observable_and_keeps_event_identity(monkeypatch):
    journal = app._conversation_journal()
    def fail():
        raise OSError('disk unavailable')
    monkeypatch.setattr(journal, 'connect', fail)
    snapshot = journal.capture(G, 'answer', CURRENT, author=B, recipients=[A])
    assert snapshot['reason'] == 'store_unavailable'
    assert snapshot['message_id'] == 'answer' and snapshot['author'] == B


def test_private_context_and_cache_scope_are_separate_for_each_user():
    journal, now = app._conversation_journal(), time.time()
    journal.capture(A, 'a-request', '麻煩優先放行', author=A, timestamp=now-900)
    other = journal.capture(B, 'b-answer', '放了', author=B, timestamp=now)
    assert other['entries'] == []
    mine = journal.capture(A, 'a-answer', '放了', author=A, timestamp=now)
    assert len(mine['entries']) == 1
    app._tl.group_id = '__dm__'
    app._tl.user_id = A
    first_scope = app._translation_cache_scope()
    app._tl.user_id = B
    assert first_scope != app._translation_cache_scope()


def test_dm_ingestion_reuses_its_own_original_after_a_failed_turn(pipeline, monkeypatch):
    monkeypatch.setattr(app, 'dm_master_enabled', True)
    first, second = event('麻煩優先放行'), event('放了')
    for e, mid in [(first, 'dm-request'), (second, 'dm-answer')]:
        e.source.group_id, e.source.user_id, e.message.id = None, A, mid
    with monkeypatch.context() as m:
        m.setattr(app, 'translate', lambda *_a, **_k: None)
        app.handle_message(first)
    pipeline.candidates = [GOOD]
    app.handle_message(second)
    assert GOOD in delivered_text(pipeline)
    assert '麻煩優先放行' in json.dumps(pipeline.prompts, ensure_ascii=False)
    assert len(pipeline.calls) == 1


@pytest.mark.parametrize('reply', ['sudah', 'udah', 'belum'])
def test_reverse_language_reply_keeps_context_through_normalization(pipeline, monkeypatch, reply):
    now = failed_first_turn(monkeypatch)
    translated = '還沒放行。' if reply == 'belum' else '已經放行了。'
    pipeline.candidates = ['__MENTION_0__ ' + translated]
    app.handle_message(message('@十元 ' + reply, 'answer', B, A, now))
    assert '@十元 ' + translated in delivered_text(pipeline)
    assert PRIOR in json.dumps(pipeline.prompts, ensure_ascii=False)
    assert len(pipeline.calls) == 1
