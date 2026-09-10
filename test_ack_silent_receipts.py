"""Receipts stay silent; only due, active reminders publish fresh group lists."""
import json

import pytest

import line_factory_features as factory
from test_line_factory_features import hub, storage, event, GROUP, USER, COLLEAGUE
from test_ack_known_zero_stop import start, answer, assert_finished
from test_ack_repeat_controls import THIRD, stored, tick
from test_ack_pending_mentions import recipients


def admin_notice(hub, row):
    response = hub.app.test_client().get('/api/admin/factory/receipts?group_id=' + GROUP)
    assert response.status_code == 200
    return next(item for item in response.get_json()['notices'] if item['token'] == row['token'])


def test_first_ack_is_stored_silently_then_due_broadcast_shows_current_list(hub):
    row, sent, replies = start(hub, people=(COLLEAGUE, THIRD))
    hub.h['group_user_names'][GROUP].update({COLLEAGUE: '已回覆同事', THIRD: '待回覆同事'})
    answer(hub, row)
    saved = admin_notice(hub, row)
    assert replies == [] and len(sent) == 1
    assert saved['responses'][COLLEAGUE]['status'] == 'understood'
    assert saved['pending_ids'] == [THIRD]
    assert saved['wake_at'] == row['reminder_due_at']

    tick(hub, row['reminder_due_at'] - 0.01)
    assert len(sent) == 1 and replies == []
    tick(hub, row['reminder_due_at'])
    assert len(sent) == 2 and replies == []
    assert recipients(sent[-1]) == [THIRD]
    card = json.dumps(sent[-1][1][-1], ensure_ascii=False)
    assert '了解/Paham (1): 已回覆同事' in card
    assert '已知成員未回覆/Belum menjawab (1): 待回覆同事' in card
    assert row['original'] in card and '作業確認提醒' in card
    assert '按了解只記錄，不另發訊息' not in card

    # The final response stops the existing schedule silently, without a final
    # completion card or restarting broadcasts after a process restart.
    answer(hub, row, THIRD)
    completed = assert_finished(hub, row)
    fresh = factory.FactoryHub(hub.app, hub.h, hub.store)
    fresh.reminders.sender = hub.reminders.sender
    tick(fresh, row['reminder_due_at'] + 7200)
    assert stored(hub, row) == completed and len(sent) == 2 and replies == []


@pytest.mark.parametrize('stop_via', ['button', 'admin'])
def test_new_answers_after_manual_stop_are_saved_without_any_group_update(hub, stop_via):
    row, sent, replies = start(hub, people=(COLLEAGUE, THIRD))
    tick(hub, row['reminder_due_at'])
    assert len(sent) == 2
    if stop_via == 'button':
        hub.h['admin_users'] = {COLLEAGUE: {'is_admin': True, 'allowed_tabs': ['factory']}}
        assert hub.postback(event(uid=COLLEAGUE), {'action': 'factory_stop', 'token': row['token']})
        assert replies == []
    else:
        response = hub.app.test_client().post('/api/admin/factory/receipts/stop', json={
            'group_id': GROUP, 'token': row['token']})
        assert response.status_code == 200
        assert replies == []
    boundary = len(replies)
    stopped = stored(hub, row)
    for uid in (COLLEAGUE, THIRD, COLLEAGUE, THIRD):
        answer(hub, row, uid)
        current = stored(hub, row)
        assert current['responses'][uid]['status'] == 'understood'
        assert current['reminder_state'] == 'stopped'
        assert current['reminder_stopped_at'] == stopped['reminder_stopped_at']
        assert current['wake_at'] is current['next_reminder_at'] is current['pending_batch'] is None
        assert current['lease_id'] == '' and len(replies) == boundary
    tick(hub, row['reminder_due_at'] + 7200)
    saved = admin_notice(hub, row)
    assert len(sent) == 2 and len(replies) == boundary
    assert set(saved['responses']) == {COLLEAGUE, THIRD} and saved['pending_ids'] == []
    assert saved['reminder_state'] == 'stopped'


def test_stop_committed_during_ack_does_not_get_undone_by_the_cas_retry(hub, monkeypatch):
    row, sent, replies = start(hub, people=(COLLEAGUE, THIRD))
    key = 'notice:' + GROUP + ':' + row['token']
    original_cas, raced, conflicts = hub.store.compare_swap, False, []

    def compare(name, previous, current, ttl):
        nonlocal raced
        if name == key and not raced and COLLEAGUE in current.get('responses', {}):
            raced = True
            hub.stop_notice(GROUP, row['token'], USER)
            result = original_cas(name, previous, current, ttl)
            conflicts.append(result)
            return result
        return original_cas(name, previous, current, ttl)

    monkeypatch.setattr(hub.store, 'compare_swap', compare)
    answer(hub, row)
    assert conflicts == [False] and replies == []
    saved = stored(hub, row)
    assert saved['responses'][COLLEAGUE]['status'] == 'understood'
    assert saved['reminder_state'] == 'stopped' and saved['wake_at'] is None
    tick(hub, row['reminder_due_at'] + 120)
    assert len(sent) == 1


def test_unlisted_author_tap_cannot_change_summary_before_first_send(hub, monkeypatch):
    row, sent, replies = start(hub, people=(COLLEAGUE, THIRD))
    key = 'notice:' + GROUP + ':' + row['token']
    original_get, fired = hub.store.get, False

    def get(name):
        nonlocal fired
        value = original_get(name)
        if name == key and not fired and (value.get('pending_batch') or {}).get('prepared_only'):
            fired = True
            # An unlisted author's click must not alter the frozen send or
            # create a response while the worker prepares its batch.
            answer(hub, row, USER)
            return original_get(name)
        return value

    monkeypatch.setattr(hub.store, 'get', get)
    tick(hub, row['reminder_due_at'])
    assert fired and len(sent) == 2 and replies == []
    assert set(recipients(sent[-1])) == {COLLEAGUE, THIRD}
    card = json.dumps(sent[-1][1][-1], ensure_ascii=False)
    assert '了解/Paham (0)' in card and USER not in stored(hub, row)['responses']
    assert '已知成員未回覆/Belum menjawab (2)' in card


def test_scheduler_receipt_summary_uses_snapshot_without_an_extra_storage_read(hub, monkeypatch):
    row, _, _ = start(hub, people=(COLLEAGUE, THIRD))
    answer(hub, row)
    saved = stored(hub, row)

    def unavailable(*args, **kwargs):
        raise AssertionError('Rendering the prepared reminder must not read storage or LINE')

    monkeypatch.setattr(hub.store, 'get', unavailable)
    hub.h['get_display_name'] = unavailable
    card = json.dumps(hub.reminders._reminder_card(saved, {THIRD: 1}), ensure_ascii=False)
    assert '了解/Paham (1)' in card and '已知成員未回覆/Belum menjawab (0)' in card
