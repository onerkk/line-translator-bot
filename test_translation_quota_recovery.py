"""Temporary API limits must not permanently silence original LINE messages.

The real provider coordinator, text pipeline and durable outbox run offline.
Only external AI/LINE transports are replaced. These are not live-model scores.
"""
import copy
import json
from types import SimpleNamespace

import pytest

import ai_provider
import app
import translation_retry_queue as queue
from test_translation_instruction_cost_quality import offline_transport, response
from test_translation_notice_availability import runtime, event, delivered_text, retry_pending


ORIGINAL_TRANSLATE = app.translate_openai
LIMIT_MESSAGE = (
    'You exceeded your current quota, please check your plan and billing details. '
    'Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, '
    'limit: 20. Please retry in 30s.'
)


class RateLimitError(RuntimeError):
    status_code = 429

    def __init__(self, message=LIMIT_MESSAGE, *, code='RESOURCE_EXHAUSTED'):
        super().__init__(message)
        self.body = {'error': {'code': code, 'message': message}}


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    monkeypatch.setenv('ACTIVE_LEARNING_DB_PATH', str(tmp_path / 'learning.db'))
    monkeypatch.setattr(app.al_module, 'AL_DB_PATH', None)
    monkeypatch.setattr(app.al_module, '_init_done', False)
    monkeypatch.setattr(app, '_MEDIA_CTX_FILE', str(tmp_path / 'media.json'))
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(previous)


@pytest.fixture
def pipeline(runtime, offline_transport, monkeypatch, tmp_path):
    cfg = offline_transport
    cfg['active_provider'] = 'gemini'
    cfg['anthropic']['api_key'] = cfg['openai']['api_key'] = ''
    monkeypatch.setattr(ai_provider, 'PROVIDER_CONFIG_PATH', str(tmp_path / 'providers.json'))
    monkeypatch.setattr(app, 'translate_openai', ORIGINAL_TRANSLATE)
    runtime.calls = []
    runtime.candidate = 'Jangan pasang cincin pelindung.'
    def dispatch(provider, **kwargs):
        runtime.calls.append(provider)
        if runtime.provider_down:
            raise RateLimitError()
        return response(runtime.candidate)
    monkeypatch.setattr(ai_provider, '_dispatch_provider', dispatch)
    return runtime, cfg


@pytest.mark.parametrize('source,target', [
    ('不要套環', 'Jangan pasang cincin pelindung.'),
    ('@阿堂 短尺收 照捆包包', '__MENTION_0__ Terima material pendek, kemas sesuai bundelnya.'),
])
@pytest.mark.parametrize('media', ['none', 'pending', 'work_order'])
def test_reported_messages_have_one_generation_and_one_delivery(pipeline, source, target, media):
    state, _ = pipeline
    state.candidate = target
    if media != 'none':
        app.store_pending_image_media_context('notice-group', 'supervisor', 'photo')
    if media == 'work_order':
        app.store_work_order_media_context('notice-group', 'supervisor', 'photo')
    message = event(source)
    if source.startswith('@'):
        message.message.mention = SimpleNamespace(mentionees=[SimpleNamespace(
            index=0, length=3, user_id='recipient', type='user')])
    app.handle_message(message)
    assert target.replace('__MENTION_0__', '@阿堂') in delivered_text(state)
    assert state.calls == ['gemini']
    assert len(state.sends) == 1 and queue.pending_count() == 0
    app.handle_message(message)
    assert state.calls == ['gemini'] and len(state.sends) == 1


@pytest.mark.parametrize('source,target', [
    ('不要套環', 'Jangan pasang cincin pelindung.'),
    ('@阿堂 短尺收 照捆包包', '__MENTION_0__ Terima material pendek, kemas sesuai bundelnya.'),
])
def test_rate_limit_then_recovery_delivers_saved_source_without_admin_reset(pipeline, source, target):
    state, cfg = pipeline
    state.candidate = target
    app.store_work_order_media_context('notice-group', 'supervisor', 'photo')
    state.provider_down = True
    app.handle_message(event(source))
    assert not state.sends
    pending = queue.get('notice-group:notice-message')
    assert pending['payload']['source_text'] == source
    assert len(state.calls) <= 3  # foreground request keeps its shared attempt cap
    state.provider_down = False
    calls_before = len(state.calls)
    assert retry_pending(), (cfg['quota_exhausted_providers'], state.calls)
    assert target.replace('__MENTION_0__', '@阿堂') in delivered_text(state)
    assert len(state.calls) == calls_before + 1
    assert len(state.sends) == 1 and queue.pending_count() == 0
    assert cfg['quota_exhausted_providers'] == {}


@pytest.mark.parametrize('code', ['RESOURCE_EXHAUSTED', 'rate_limit_exceeded', 'quota_exceeded', 'too_many_requests'])
def test_structured_temporary_limit_overrides_ambiguous_billing_words(code):
    assert not ai_provider._is_quota_exhausted_error(RateLimitError(code=code))


@pytest.mark.parametrize('message', [
    LIMIT_MESSAGE,
    '429 RESOURCE_EXHAUSTED: You exceeded your current quota.',
    'Quota exceeded for GenerateRequestsPerDay. Retry after reset.',
    'Rate limit exceeded for requests per minute.',
])
def test_temporary_limits_without_structured_billing_code_are_not_permanent(message):
    assert not ai_provider._is_quota_exhausted_error(RuntimeError(message))


@pytest.mark.parametrize('message', ['insufficient_quota', 'credit balance is too low',
    'insufficient credits', 'billing_hard_limit_reached', 'payment required'])
def test_explicit_billing_exhaustion_remains_blocked(message):
    assert ai_provider._is_quota_exhausted_error(RuntimeError(message))


def test_structured_billing_code_survives_generic_or_localized_message():
    error = RateLimitError('目前無法處理請求', code='insufficient_quota')
    assert ai_provider._is_quota_exhausted_error(error)


@pytest.mark.parametrize('provider,blocked', [('openai', True), ('gemini', False), ('anthropic', False), (None, False)])
def test_legacy_openai_billing_wording_is_provider_specific(provider, blocked):
    message = 'You exceeded your current quota, please check your plan and billing details.'
    assert ai_provider._is_quota_exhausted_error(RuntimeError(message), provider=provider) is blocked


def test_flat_sdk_body_and_error_type_are_authoritative():
    error = RateLimitError()
    error.body = {'code': None, 'type': 'insufficient_quota', 'message': '無可用餘額'}
    assert ai_provider._is_quota_exhausted_error(error, provider='openai')
    error.body = {'code': 'rate_limit_exceeded', 'type': 'requests', 'message': LIMIT_MESSAGE}
    assert not ai_provider._is_quota_exhausted_error(error, provider='openai')


def test_persistence_boundary_cannot_lock_a_temporary_limit(offline_transport, monkeypatch):
    writes, notices = [], []
    monkeypatch.setattr(ai_provider, '_save_config_to_disk', lambda cfg: writes.append(copy.deepcopy(cfg)))
    monkeypatch.setattr(ai_provider, '_notify_admin', notices.append)
    before = copy.deepcopy(offline_transport)
    assert ai_provider._auto_switch_on_exhaust('gemini', RateLimitError()) is None
    assert offline_transport == before and not writes and not notices


def test_new_billing_record_remains_blocked_after_save_reload(offline_transport, monkeypatch, tmp_path):
    monkeypatch.setattr(ai_provider, 'PROVIDER_CONFIG_PATH', str(tmp_path / 'providers.json'))
    error = RateLimitError('帳戶額度不足', code='insufficient_quota')
    assert ai_provider._auto_switch_on_exhaust('openai', error) == 'gemini'
    restored = ai_provider._load_config_from_disk()
    assert restored['quota_exhausted_providers']['openai']['kind'] == 'billing_exhausted'
    assert restored['active_provider'] == 'gemini'


def test_configuration_reload_recovers_old_gemini_misclassification_only(monkeypatch, tmp_path):
    cfg = copy.deepcopy(ai_provider.DEFAULT_CONFIG)
    cfg['active_provider'] = 'gemini'
    cfg['gemini']['api_key'] = 'offline-key'
    cfg['quota_exhausted_providers'] = {
        'gemini': {'at': 1, 'error': LIMIT_MESSAGE[:240]},
        'anthropic': {'at': 1, 'error': 'credit balance is too low'},
        'openai': {'at': 1, 'error': 'insufficient_quota'},
    }
    path = tmp_path / 'provider-state.json'
    path.write_text(json.dumps(cfg), encoding='utf-8')
    monkeypatch.setattr(ai_provider, 'PROVIDER_CONFIG_PATH', str(path))
    monkeypatch.setattr(ai_provider, '_current_config', None)
    monkeypatch.setattr(ai_provider, '_last_config_mtime', 0)
    ai_provider._ensure_initialized()
    assert ai_provider.get_available_providers() == ['gemini']
    assert set(ai_provider.get_quota_exhausted_providers()) == {'anthropic', 'openai'}
    assert ai_provider.get_active_provider() == 'gemini'
    # The same saved pre-fix file must be safe on every worker/restart.
    monkeypatch.setattr(ai_provider, '_current_config', None)
    ai_provider._ensure_initialized()
    assert ai_provider.get_available_providers() == ['gemini']
    assert ai_provider._save_config_to_disk(ai_provider._current_config)
    assert set(json.loads(path.read_text())['quota_exhausted_providers']) == {'anthropic', 'openai'}


@pytest.mark.parametrize('provider,error', [
    ('gemini', 'billing balance exhausted'),
    ('openai', 'You exceeded your current quota, please check your plan and billing details.'),
    ('anthropic', 'credit balance is too low'),
    ('gemini', ''),  # Missing historical evidence is not permission to reset billing.
])
def test_migration_retains_confirmed_or_undetermined_billing_blocks(provider, error):
    cfg = copy.deepcopy(ai_provider.DEFAULT_CONFIG)
    cfg['quota_exhausted_providers'] = {provider: {'at': 1, 'error': error}}
    migrated = ai_provider._migrate_config_models(cfg)
    assert provider in migrated['quota_exhausted_providers']


def test_transport_outage_never_becomes_a_cached_translation(pipeline):
    state, _ = pipeline
    state.provider_down = True
    app.handle_message(event('不要套環'))
    assert not state.sends
    assert not app.translation_cache
    assert queue.pending_count() == 1
