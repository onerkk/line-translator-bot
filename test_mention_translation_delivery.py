"""A display-name mention must not turn following prose into immutable data."""

import socket
from types import SimpleNamespace

import pytest

import app
import translation_quality_gate as quality
from test_translation_notice_availability import runtime, event, delivered_text, retry_pending
import translation_retry_queue as queue


MENTIONS = [
    '@小麥（研磨股班長）', '@小麥 （研磨股班長）', '@小麥(研磨股班長)',
    '@小麥', '@蘇比 sobirin', '@(John Doe)', '@（杰弗）',
    '@budi santoso', '@John  Doe', '@All',
]
REQUEST = 'tolong bukakan komputer tengah'
TRANSLATION = '請幫忙開一下中間那台電腦。'


@pytest.fixture(autouse=True)
def isolated_context(monkeypatch):
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    def block_network(*_args, **_kwargs):
        raise AssertionError('Mention regression tests must run offline')
    monkeypatch.setattr(socket.socket, 'connect', block_network)
    try:
        yield
    finally:
        app._tl.__dict__.clear()
        app._tl.__dict__.update(previous)


@pytest.mark.parametrize('mention', MENTIONS)
def test_prose_after_display_name_is_translatable_and_not_immutable(mention):
    source = mention + ' ' + REQUEST
    envelope = quality.protect_immutable_spans(source)
    assert list(envelope.mapping.values()) == [mention]
    assert envelope.protected.endswith(REQUEST)
    assert app.strip_mentions_for_detect(source).strip() == REQUEST
    report = quality.validate_translation(source, mention + ' ' + TRANSLATION, 'id', 'zh')
    assert report.ok, report.issues


@pytest.mark.parametrize('mention, prose, language', [
    ('@小麥（班長）', 'bukakan komputer tengah', 'id'),
    ('@(John Doe)', 'please open the middle computer', 'en'),
    ('@All', 'harap perhatikan mesin I01', 'id'),
    ('@阿明', 'jangan masuk gudang', 'id'),
])
def test_role_and_sentence_boundaries_are_not_tied_to_one_incident(mention, prose, language):
    source = mention + ' ' + prose
    envelope = quality.protect_immutable_spans(source)
    assert mention in envelope.mapping.values()
    assert prose.split()[0] in envelope.protected
    assert app.strip_mentions_for_detect(source).strip() == prose
    assert app.detect_language(prose) == language


@pytest.mark.parametrize('metadata', [False, True])
@pytest.mark.parametrize('mention', MENTIONS)
def test_handler_delivers_indonesian_request_after_mention(runtime, mention, metadata):
    message = event(mention + ' ' + REQUEST)
    if metadata:
        message.message.mention = SimpleNamespace(mentionees=[SimpleNamespace(
            index=0, length=len(mention.encode('utf-16-le')) // 2,
            user_id='recipient', type='all' if mention == '@All' else 'user',
        )])
    runtime.provider_result = '__MENTION_0__ ' + TRANSLATION
    app.handle_message(message)
    delivered = delivered_text(runtime)
    assert '中間那台電腦' in delivered
    assert delivered.count(mention) == 1
    assert len(runtime.generations) == 1
    assert runtime.generations[0][1:] == ('id', 'zh')
    assert REQUEST in runtime.generations[0][0]
    assert len(runtime.sends) == 1
    assert queue.pending_count() == 0


def test_missing_identity_code_quantity_and_untranslated_prose_still_fail():
    source = '@小麥（班長） tolong cek I01 5 mm'
    good = '@小麥（班長） 請檢查 I01 5 mm'
    report = quality.validate_translation(source, good, 'id', 'zh')
    assert report.ok, report.issues
    for candidate, expected in (
        (good.replace('@小麥（班長）', ''), 'missing_literal:@小麥（班長）'),
        (good.replace('I01', 'I02'), 'missing_literal:I01'),
        (good.replace('5 mm', '6 mm'), 'missing_literal:5 mm'),
    ):
        report = quality.validate_translation(source, candidate, 'id', 'zh')
        assert not report.ok
        assert expected in report.hard_issues
    for untranslated in ('tolong', 'bukakan'):
        report = quality.validate_translation(
            '@小麥（班長） ' + REQUEST,
            '@小麥（班長） ' + untranslated + ' 中間那台電腦', 'id', 'zh',
        )
        assert not report.ok, untranslated


def test_multiple_and_repeated_mentions_remain_exact():
    source = '@蘇比 sobirin @(杰弗) tolong cek I01. @蘇比 sobirin'
    protected, mapping = app.protect_mentions(source)
    assert len(mapping) == 3
    assert 'tolong cek' in protected
    assert app.restore_mentions(protected, mapping) == source
    values = list(quality.inspect_immutable_spans(source).mapping.values())
    assert values.count('@蘇比 sobirin') == 2
    assert '@(杰弗)' in values
    assert 'I01' in values


def test_pending_mentioned_request_recovers_after_provider_returns(runtime):
    source = '@小麥（研磨股班長） ' + REQUEST
    runtime.provider_down = True
    app.handle_message(event(source))
    assert not runtime.sends
    pending = queue.get('notice-group:notice-message')
    assert pending['payload']['source_text'] == source
    assert pending['payload']['src_lang'] == 'id'
    runtime.provider_down = False
    runtime.provider_result = '__MENTION_0__ ' + TRANSLATION
    assert retry_pending()
    assert '中間那台電腦' in delivered_text(runtime)
    assert '@小麥（研磨股班長）' in delivered_text(runtime)
    assert queue.pending_count() == 0
