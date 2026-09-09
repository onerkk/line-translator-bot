"""Receipts exercise real storage and signed webhook boundaries, without LINE sends."""
import base64
import hashlib
import hmac
import json
import time

import pytest
from conftest import wait_for_webhooks
from line_factory_store import StoreError
from test_line_factory_features import hub, storage, notice, event, GROUP, OTHER, USER, COLLEAGUE


def actions(value):
    if isinstance(value, dict):
        if value.get('type') == 'postback':
            yield value.get('data', '')
        for child in value.values():
            yield from actions(child)
    elif isinstance(value, list):
        for child in value:
            yield from actions(child)


def test_confirmation_controls_are_in_the_message_not_only_quick_reply(hub):
    token, _, messages = notice(hub)
    bodies = []
    for message in messages:
        body = message.to_dict()
        body.pop('quickReply', None)
        bodies.extend(actions(body))
    assert any('action=factory_ack&token=' + token == data for data in bodies)
    # New notices default to Paham and status; old help postbacks stay readable.
    assert not any('action=factory_help&token=' + token == data for data in bodies)
    assert any('action=factory_receipts&token=' + token == data for data in bodies)


def test_clicked_receipt_survives_style_context_expiry(hub, monkeypatch):
    token, _, _ = notice(hub)
    context = hub.store.get('context:' + token)
    context['expires_at'] = time.time() - 1
    hub.store.put('context:' + token, context)
    hub.store.delete('context:' + token)
    replies = []
    monkeypatch.setattr(hub, '_reply', lambda e, text, **kw: replies.append(text))
    assert hub.postback(event(uid=COLLEAGUE), {'action': 'factory_ack', 'token': token})
    row = hub.store.get('notice:' + GROUP + ':' + token)
    assert row['responses'][COLLEAGUE]['status'] == 'understood'
    assert 'Adi' in replies[-1] and 'PMI' in replies[-1]
    assert '管理者' in replies[-1]


def test_receipt_query_explicitly_identifies_the_group_and_response(hub, monkeypatch):
    token, _, _ = notice(hub)
    monkeypatch.setattr(hub, '_reply', lambda *a, **kw: None)
    hub.postback(event(uid=COLLEAGUE), {'action': 'factory_ack', 'token': token})
    client = hub.app.test_client()
    data = client.get('/api/admin/factory/receipts?group_id=' + GROUP).json
    assert data['group_id'] == GROUP and data['group_name'] == 'A 班'
    assert data['notices'][0]['responses'][COLLEAGUE]['name'] == 'Adi'
    other = client.get('/api/admin/factory/receipts?group_id=' + OTHER).json
    assert other['group_name'] == 'B 班' and other['notices'] == []


def test_missing_receipt_can_be_recovered_from_a_valid_stored_context(hub, monkeypatch):
    token, _, _ = notice(hub)
    hub.store.delete('notice:' + GROUP + ':' + token)
    monkeypatch.setattr(hub, '_reply', lambda *a, **kw: None)
    hub.postback(event(uid=COLLEAGUE), {'action': 'factory_help', 'token': token})
    assert hub.store.get('notice:' + GROUP + ':' + token)['responses'][COLLEAGUE]['status'] == 'needs_help'


def test_storage_outage_has_visible_feedback_and_stays_retryable(hub, monkeypatch):
    token, _, _ = notice(hub)
    replies = []
    monkeypatch.setattr(hub, '_reply', lambda e, text, **kw: replies.append(text))
    original = hub.store.update
    def broken(key, *args, **kwargs):
        if key.startswith('notice:'):
            raise StoreError('offline storage')
        return original(key, *args, **kwargs)
    monkeypatch.setattr(hub.store, 'update', broken)
    with pytest.raises(StoreError):
        hub.postback(event(uid=COLLEAGUE), {'action': 'factory_ack', 'token': token})
    assert replies and '尚未確認儲存成功' in replies[-1]
    assert not hub.store.get('notice:' + GROUP + ':' + token)['responses']


def test_late_or_cross_group_click_cannot_record_for_obsolete_notice(hub, monkeypatch):
    token, _, _ = notice(hub)
    monkeypatch.setattr(hub, '_reply', lambda *a, **kw: None)
    hub.postback(event(group=OTHER), {'action': 'factory_ack', 'token': token})
    assert not hub.store.get('notice:' + GROUP + ':' + token)['responses']
    with hub.message_scope(event('PMI 明天再檢驗', stamp=200, edited=True), 'text'):
        pass
    hub.postback(event(uid=COLLEAGUE, stamp=300), {'action': 'factory_ack', 'token': token})
    assert not hub.store.get('notice:' + GROUP + ':' + token)['responses']


def test_seven_day_receipt_expiry_is_independent_of_updates(hub, monkeypatch):
    token, _, _ = notice(hub)
    key = 'notice:' + GROUP + ':' + token
    row = hub.store.get(key)
    row['created_at'] = time.time() - 8 * 86400
    row['expires_at'] = time.time() - 86400
    hub.store.put(key, row)
    replies = []
    monkeypatch.setattr(hub, '_reply', lambda e, text, **kw: replies.append(text))
    hub.postback(event(uid=COLLEAGUE), {'action': 'factory_ack', 'token': token})
    assert not hub.store.get(key)['responses']
    assert '過期' in replies[-1]


def test_real_signed_postback_reaches_storage_admin_and_group_feedback(hub, monkeypatch):
    import app
    monkeypatch.setattr(app, 'factory_hub', hub)
    monkeypatch.setattr(app, '_processed_msg_ids', app._collections_dedup.OrderedDict())
    token, _, _ = notice(hub)
    sends = []
    hub.h['_send_reply_with_push_fallback'] = lambda **kwargs: sends.append(kwargs)
    client = app.app.test_client()
    def click(uid, eid, state='factory_ack', reply='reply'):
        raw = json.dumps({'destination': 'U' + 'f' * 32, 'events': [{
            'type': 'postback', 'timestamp': 1000, 'webhookEventId': eid,
            'replyToken': reply, 'mode': 'active', 'deliveryContext': {'isRedelivery': False},
            'source': {'type': 'group', 'groupId': GROUP, 'userId': uid},
            'postback': {'data': 'action=' + state + '&token=' + token}}]}).encode()
        secret = app.handler.parser.signature_validator.channel_secret
        if isinstance(secret, str):
            secret = secret.encode()
        signature = base64.b64encode(hmac.new(secret, raw, hashlib.sha256).digest()).decode()
        response = client.post('/callback', data=raw, headers={'X-Line-Signature': signature, 'Content-Type': 'application/json'})
        wait_for_webhooks()
        return response
    assert click(COLLEAGUE, 'receipt-click').status_code == 200
    assert len(sends) == 1 and sends[0]['target_id'] == GROUP
    assert 'Adi' in sends[0]['fallback_text'] and 'PMI' in sends[0]['fallback_text']
    assert '管理者' in sends[0]['fallback_text']
    data = hub.app.test_client().get('/api/admin/factory/receipts?group_id=' + GROUP).json
    assert data['notices'][0]['responses'][COLLEAGUE]['status'] == 'understood'
    assert click(COLLEAGUE, 'receipt-click', reply='new-reply').status_code == 200
    assert len(sends) == 1  # Redelivery does not notify the group twice.
    assert click(USER, 'receipt-help', 'factory_help').status_code == 200
    row = hub.store.get('notice:' + GROUP + ':' + token)
    assert len(row['responses']) == 2 and row['responses'][USER]['status'] == 'needs_help'


def test_control_feedback_does_not_create_or_redecorate_another_notice(hub):
    token, _, _ = notice(hub)
    rows_before = len(hub.store.recent('notice:' + GROUP))
    def sending(**kw):
        message = kw['message_obj']
        before = message.to_dict()
        result = hub.decorate_delivery([message], {'group_id': GROUP}, kw['fallback_text'])
        assert len(result) == 1 and result[0].to_dict() == before
    hub.h['_send_reply_with_push_fallback'] = sending
    hub.postback(event(uid=COLLEAGUE), {'action': 'factory_ack', 'token': token})
    assert len(hub.store.recent('notice:' + GROUP)) == rows_before


def test_unidentified_user_gets_feedback_without_anonymous_record(hub, monkeypatch):
    token, _, _ = notice(hub)
    replies = []
    monkeypatch.setattr(hub, '_reply', lambda e, text, **kw: replies.append(text))
    hub.postback(event(uid=''), {'action': 'factory_ack', 'token': token})
    assert not hub.store.get('notice:' + GROUP + ':' + token)['responses']
    assert '本次無法記錄' in replies[-1]
