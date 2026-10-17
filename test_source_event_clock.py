"""Original event-time evidence and cache separation, with offline generation."""
from datetime import datetime
from types import SimpleNamespace
import json
import pytest
import app
import conversation_context as cc
from test_visual_evidence_batches import runtime

SOURCE='休息時間再注意一下 不要超過6點半'

def stamp(hour):
    return datetime.fromisoformat(f'2026-10-17T{hour:02d}:46:00+08:00').timestamp()


def snapshot(tmp_path, hour=17, source=SOURCE):
    return cc.SourceJournal(tmp_path/'context.db').capture('G','m',source,author='U',timestamp=stamp(hour),record=False)


def test_event_time_survives_serialization_and_later_processing(tmp_path,monkeypatch):
    first=snapshot(tmp_path)
    restored=json.loads(json.dumps(first))
    monkeypatch.setattr(cc.time,'time',lambda:stamp(23))
    assert cc.event_clock(restored)=={'sent_at':'2026-10-17T17:46+08:00','timezone':'Asia/Taipei'}
    assert 'source_event_clock' in cc.prompt_data(restored)
    assert cc.fingerprint(restored)==cc.fingerprint(first)
    assert cc.fingerprint(snapshot(tmp_path,5))!=cc.fingerprint(first)


@pytest.mark.parametrize('source',['不要超過7點半','八點整集合','pukul 6.30','jam 7','18:30前回來'])
def test_clock_detection_is_not_specific_to_reported_sentence(tmp_path,source):
    assert cc.event_clock(snapshot(tmp_path,source=source))


@pytest.mark.parametrize('source',['董事長進去了','以上都是要注意的事項','再委屈一點時間，換一下制服','BF5缺失改善'])
def test_non_clock_messages_have_no_time_prompt_cost(tmp_path,source):
    assert cc.event_clock(snapshot(tmp_path,source=source)) is None


def test_unknown_or_disabled_source_time_is_not_fabricated(tmp_path):
    journal=cc.SourceJournal(tmp_path/'context.db')
    assert cc.event_clock(journal.capture('G','m',SOURCE,record=False)) is None
    assert cc.event_clock(journal.capture('G','m',SOURCE,timestamp=stamp(17),enabled=False)) is None
    data=snapshot(tmp_path);data['timestamp']='invalid'
    assert cc.event_clock(data) is None


@pytest.mark.parametrize("public_entry", [False, True])
def test_actual_request_includes_original_clock_without_prior_turns(runtime,tmp_path,monkeypatch,public_entry):
    data=snapshot(tmp_path)
    app._tl.conversation_snapshot=data
    calls=[]
    expected='Perhatikan waktu istirahat. Jangan sampai lewat pukul 6.30 sore.'
    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=expected),finish_reason='stop')],usage=None)
    monkeypatch.setattr(app.ai.chat.completions,'create',create)
    monkeypatch.setattr(app,'get_conv_context_enabled',lambda _:True)
    with cc.scope(data):
        assert app._translation_cache_context_bound(SOURCE)
        translate = app.translate if public_entry else app.translate_openai
        assert translate(SOURCE,'zh','id')==expected
    assert len(calls)==1
    messages=json.dumps(calls[0]['messages'],ensure_ascii=False)
    assert '2026-10-17T17:46+08:00' in messages
    assert 'Explicit dates/day-periods, past reports and future schedules override proximity' in messages
    assert calls[0]['translation_max_generations']==1


def test_clock_context_keeps_an_explicit_quote(runtime,tmp_path):
    data=snapshot(tmp_path)
    app._tl.quoted_context_source='下午休息結束要回現場'
    with cc.scope(data):
        messages=app._build_messages_with_fewshot('translate',SOURCE,'zh','id',include_examples=False)
    assert any('source_event_clock' in m['content'] for m in messages)
    assert any('下午休息結束要回現場' in m['content'] for m in messages)
    assert 'source_event_clock' not in cc.prompt_rules(None)
