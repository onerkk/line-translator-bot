"""Legacy help is pending everywhere; obsolete buttons have no side effects."""
import copy
import json

import pytest

import line_ack_reminders as reminders
from line_factory_store import StoreError
from test_line_factory_features import hub, storage, event, GROUP, USER, COLLEAGUE
from test_ack_recipient_scope import begin, tagged, THIRD, FOURTH
from test_ack_repeat_controls import stored, tick
from test_ack_pending_mentions import recipients


def legacy_notice(hub, *, completed=False):
    row, sent, replies, _ = begin(hub, [tagged('@Adi', COLLEAGUE), tagged('@Budi', THIRD)])
    due = row['delivered_at'] + 480 * 60
    changes = dict(reminder_minutes=480, reminder_due_at=due, next_reminder_at=due, wake_at=due,
        responses={COLLEAGUE: {'status': 'needs_help', 'name': 'Adi', 'at': row['created_at'],
                               'event_timestamp': 900},
                   THIRD: {'status': 'understood', 'name': 'Budi', 'at': row['created_at']}})
    if completed:
        changes.update(reminder_state='no_pending', wake_at=None, next_reminder_at=None,
                       reminder_completed_at=row['delivered_at'] + 10)
    row = hub.store.update('notice:' + GROUP + ':' + row['token'], lambda r: dict(r, **changes))
    return row, sent, replies


def test_legacy_help_is_pending_in_line_query_admin_api_and_scheduled_mentions(hub):
    row, sent, replies = legacy_notice(hub)
    assert reminders.pending_ids(row) == [COLLEAGUE]
    text = hub._receipt_text(row, 'Status', departed={}, include_content=False)
    assert '了解/Paham (1): Budi' in text
    assert '未回覆/Belum menjawab (1): Adi' in text
    assert all(term not in text for term in ('需說明', 'Perlu penjelasan', '請發起人協助說明', 'membantu menjelaskan'))
    data = hub.app.test_client().get('/api/admin/factory/receipts?group_id=' + GROUP).json['notices'][0]
    assert data['pending_ids'] == [COLLEAGUE]
    assert set(data['responses']) == {THIRD}
    assert 'needs_help' not in json.dumps(data)
    assert stored(hub, row)['responses'][COLLEAGUE] == row['responses'][COLLEAGUE]
    tick(hub, row['reminder_due_at'] - 0.001)
    assert len(sent) == 1 and replies == []
    tick(hub, row['reminder_due_at'])
    assert len(sent) == 2 and recipients(sent[-1]) == [COLLEAGUE]
    assert 'factory_help' not in json.dumps(sent[-1][1])


@pytest.mark.parametrize('uid', [COLLEAGUE, THIRD, USER, FOURTH, ''])
def test_legacy_help_tap_does_not_read_write_send_or_recover_a_notice(hub, monkeypatch, uid):
    row, sent, replies = legacy_notice(hub)
    before = copy.deepcopy(stored(hub, row))
    def forbidden(*args, **kwargs):
        pytest.fail('Removed help action must exit before storage, profiles or transport')
    with monkeypatch.context() as patch:
        for name in ('get', 'update', 'put', 'save_interaction'):
            patch.setattr(hub.store, name, forbidden)
        patch.setattr(hub, 'observe_members', forbidden)
        hub.h['_factory_receipt_sender'] = forbidden
        for token in (row['token'], 'missing'):
            assert hub.postback(event(uid=uid), {'action': 'factory_help', 'token': token})
    assert stored(hub, row) == before and len(sent) == 1 and replies == []


def test_legacy_help_timestamp_cannot_veto_a_real_acknowledgement(hub):
    row, sent, replies = legacy_notice(hub)
    private = []
    hub.h['_factory_receipt_sender'] = lambda *args: private.append(args)
    for action, stamp in [('factory_ack', 100), ('factory_help', 1000), ('factory_ack', 1100)]:
        hub.postback(event(uid=COLLEAGUE, stamp=stamp), {'action': action, 'token': row['token']})
    saved = stored(hub, row)
    assert saved['responses'][COLLEAGUE]['status'] == 'understood'
    assert saved['responses'][COLLEAGUE]['event_timestamp'] == 100
    assert saved['reminder_state'] == 'no_pending'
    assert len(private) == 1 and len(sent) == 1 and replies == []


def test_worker_recovers_legacy_auto_stop_without_a_new_webhook_or_query(hub):
    row, sent, replies = legacy_notice(hub, completed=True)
    assert hub.store.due_notices(row['reminder_due_at']) == []
    tick(hub, row['delivered_at'] + 20)
    assert stored(hub, row)['wake_at'] == row['reminder_due_at']
    assert len(sent) == 1 and replies == []
    tick(hub, row['reminder_due_at'] - 0.001)
    assert len(sent) == 1
    tick(hub, row['reminder_due_at'])
    assert len(sent) == 2 and recipients(sent[-1]) == [COLLEAGUE]
    assert stored(hub, row)['next_reminder_at'] == row['reminder_due_at'] + 480 * 60


def test_authorized_status_query_repairs_legacy_schedule_without_posting_a_card(hub):
    row, sent, replies = legacy_notice(hub, completed=True)
    hub.postback(event(uid=USER), {'action': 'factory_receipts', 'token': row['token']})
    assert len(sent) == 1 and len(replies) == 1
    text = replies[0]['fallback_text']
    assert '未回覆/Belum menjawab (1): Adi' in text and '需說明' not in text
    assert '已自動停止提醒' not in text
    saved = stored(hub, row)
    assert saved['wake_at'] == row['reminder_due_at']
    assert set(saved['status_views']) == {USER}


@pytest.mark.parametrize('mode', ['before_due', 'overdue', 'after_previous_round', 'uncertain_old_round'])
def test_recovery_preserves_deadlines_and_excludes_an_earlier_ack(hub, mode):
    row, sent, replies = legacy_notice(hub, completed=True)
    key = 'notice:' + GROUP + ':' + row['token']
    due = row['reminder_due_at']
    if mode == 'after_previous_round':
        row = hub.store.update(key, lambda r: dict(r, reminded_at=due, reminder_count=1,
            reminder_completed_at=due + 10, reminded_ids=[COLLEAGUE], reminder_round=2))
        due += 480 * 60
    elif mode == 'uncertain_old_round':
        row = hub.store.update(key, lambda r: dict(r, reminder_completed_at=due + 10))
        due += 10 + 480 * 60
    now = due + 10 if mode == 'overdue' else due - 1
    tick(hub, now)
    if mode != 'overdue':
        assert len(sent) == 1 and stored(hub, row)['wake_at'] == due
        assert stored(hub, row)['reminder_round'] > row.get('reminder_round', 0)
        tick(hub, due)
    assert len(sent) == 2 and recipients(sent[-1]) == [COLLEAGUE] and replies == []
    assert stored(hub, row)['responses'][THIRD] == row['responses'][THIRD]


@pytest.mark.parametrize('reason', ['stopped', 'disabled', 'mode_off', 'expired', 'edited', 'bot_left', 'one_shot_done', 'departed'])
def test_legacy_recovery_does_not_revive_an_ineligible_notice(hub, monkeypatch, reason):
    row, sent, replies = legacy_notice(hub, completed=True)
    key = 'notice:' + GROUP + ':' + row['token']
    if reason == 'stopped':
        hub.stop_notice(GROUP, row['token'], USER)
    elif reason in {'disabled', 'mode_off'}:
        values = {'ack_reminder_enabled': False} if reason == 'disabled' else {'acknowledgements': 'off'}
        hub.store.put('ack-settings', {'groups': {GROUP: values}})
    elif reason == 'expired':
        hub.store.update(key, lambda r: dict(r, expires_at=row['created_at'] - 1))
    elif reason == 'edited':
        monkeypatch.setattr(hub, 'current', lambda metadata: False)
    elif reason == 'bot_left':
        hub.store.put('bot-left:' + GROUP, {'at': row['created_at'] + 1})
    elif reason == 'one_shot_done':
        hub.store.update(key, lambda r: dict(r, reminder_repeat=False, reminder_count=1,
                                           reminded_at=row['reminder_due_at']))
    else:
        hub.member_presence(GROUP, COLLEAGUE, left=True)
    tick(hub, row['reminder_due_at'] + 480 * 60)
    saved = stored(hub, row)
    assert saved['wake_at'] is None
    assert len(sent) == 1 and replies == []
    if reason == 'stopped':
        assert saved['reminder_state'] == 'stopped' and saved['stopped_by'] == USER


@pytest.mark.parametrize('race', ['ack', 'stop'])
def test_concurrent_final_ack_or_manual_stop_wins_over_legacy_recovery(hub, monkeypatch, race):
    row, sent, replies = legacy_notice(hub, completed=True)
    key = 'notice:' + GROUP + ':' + row['token']
    original, raced = hub.store.compare_swap, []
    def compare(name, *args):
        if name == key and not raced:
            raced.append(True)
            if race == 'ack':
                hub.postback(event(uid=COLLEAGUE), {'action': 'factory_ack', 'token': row['token']})
            else:
                hub.stop_notice(GROUP, row['token'], USER)
        return original(name, *args)
    monkeypatch.setattr(hub.store, 'compare_swap', compare)
    hub.reminders.reconcile_legacy_notice(row)
    saved = stored(hub, row)
    assert raced and saved['wake_at'] is None
    assert saved['reminder_state'] == ('no_pending' if race == 'ack' else 'stopped')
    assert len(sent) == 1 and replies == []


def test_background_recovery_visits_history_beyond_the_admin_limit(hub):
    row, sent, replies = legacy_notice(hub, completed=True)
    tokens = [row['token']]
    for i in range(215):
        token = 'old-notice-' + str(i).zfill(3)
        tokens.append(token)
        hub.store.put('notice:' + GROUP + ':' + token, dict(row, token=token))
    assert len(hub.store.recent('notice:' + GROUP, 200)) == 200
    for _ in range(40):
        hub.reminders.reconcile_legacy_notices()
        if hub.reminders._legacy_scan_next_at:
            break
    assert hub.reminders._legacy_scan_next_at
    for token in tokens:
        saved = hub.store.get('notice:' + GROUP + ':' + token)
        assert saved['wake_at'] == row['reminder_due_at']
        assert saved['reminder_round'] == row.get('reminder_round', 0) + 1
    # Restarting the scan must be idempotent, including duplicate SCAN results.
    hub.reminders._legacy_scan_next_at = 0
    hub.reminders.reconcile_legacy_notices()
    assert stored(hub, row)['reminder_round'] == row.get('reminder_round', 0) + 1
    assert len(sent) == 1 and replies == []


def test_empty_scan_page_and_failed_write_do_not_drop_a_legacy_notice(hub, monkeypatch):
    row, sent, replies = legacy_notice(hub, completed=True)
    pages = iter([([], 'continue'), ([row, row], '')])
    monkeypatch.setattr(hub.store, 'notice_page', lambda *args: next(pages))
    hub.reminders.reconcile_legacy_notices()
    assert stored(hub, row)['wake_at'] is None
    with monkeypatch.context() as patch:
        patch.setattr(hub.store, 'compare_swap', lambda *args: (_ for _ in ()).throw(StoreError('offline')))
        with pytest.raises(StoreError):
            hub.reminders.reconcile_legacy_notices()
    hub.reminders.reconcile_legacy_notices()
    assert stored(hub, row)['wake_at'] == row['reminder_due_at']
    assert stored(hub, row)['reminder_round'] == row.get('reminder_round', 0) + 1
    assert len(sent) == 1 and replies == []
