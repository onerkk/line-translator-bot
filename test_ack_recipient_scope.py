"""Real command -> stored audience -> replies -> reminder recipients -> stop."""
import copy
import json

import pytest
from linebot.v3.webhooks import MessageEvent

import line_factory_features as factory
import line_ack_reminders as reminders
from test_line_factory_features import hub, storage, event, GROUP, USER, COLLEAGUE
from test_line_ack_commands import configure
from test_ack_repeat_controls import THIRD, stored, tick
from test_ack_known_zero_stop import answer, assert_finished
from test_ack_pending_mentions import recipients

FOURTH = 'U' + 'd' * 32
BOT = 'U' + 'f' * 32


def tagged(label, uid=None, *, kind='user', self_bot=False):
    item = {'label': label, 'type': kind}
    if uid is not None:
        item['userId'] = uid
    if self_bot:
        item['isSelf'] = True
    return item


def command_event(tags=(), *, alias='/確認', suffix='請完成 PMI 檢驗', mid='scoped-ack'):
    text, native = alias + ' 📌 ', []
    for tag in tags:
        label = tag['label']
        native.append({**{k: v for k, v in tag.items() if k != 'label'},
                       'index': len(text.encode('utf-16-le')) // 2,
                       'length': len(label.encode('utf-16-le')) // 2})
        text += label + ' '
    return event(text + suffix, mid=mid, mentions=native)


def begin(hub, tags=(), *, alias='/確認', suffix='請完成 PMI 檢驗', sdk=False):
    configure(hub, 1, repeat=True)
    hub.h['group_user_names'][GROUP].update({THIRD: 'Budi', FOURTH: 'Siti'})
    hub.h['_factory_member_ids'] = lambda group: [USER, COLLEAGUE, THIRD, FOURTH]
    hub.h['_factory_member_count'] = lambda group: 4
    sent, replies = [], []
    hub.reminders.sender = lambda *args: sent.append(copy.deepcopy(args))
    hub.h['_send_reply_with_push_fallback'] = lambda **kw: replies.append(kw)
    ev = command_event(tags, alias=alias, suffix=suffix)
    if sdk:
        ev.update(mode='active', deliveryContext={'isRedelivery': False})
        ev['message']['quoteToken'] = 'fixture-quote-token'
        ev = MessageEvent.from_dict(ev)
    with hub.message_scope(ev, 'text'):
        assert hub.command(ev)
    rows = hub.store.recent('notice:' + GROUP)
    return rows[0] if rows else None, sent, replies, ev


@pytest.mark.parametrize('alias', ['/ack', '/確認', '/确认'])
@pytest.mark.parametrize('case', ['none', 'native_all', 'literal_all', 'mixed_all', 'mixed_literal', 'one', 'two', 'named_all'])
def test_command_audience_matches_all_and_individual_mention_rules(hub, alias, case):
    tags = {
        'none': [], 'native_all': [tagged('@All', kind='all')],
        'literal_all': [], 'mixed_all': [tagged('@Adi', COLLEAGUE), tagged('@All', kind='all')],
        'mixed_literal': [tagged('@Adi', COLLEAGUE)],
        'one': [tagged('@Adi', COLLEAGUE)],
        'two': [tagged('@Adi', COLLEAGUE), tagged('@Budi', THIRD)],
        'named_all': [tagged('@All', COLLEAGUE)],
    }[case]
    all_members = case in {'none', 'native_all', 'literal_all', 'mixed_all', 'mixed_literal'}
    row, sent, replies, _ = begin(hub, tags, alias=alias,
        suffix=('@aLL ' if 'literal' in case else '') + '請完成 PMI 檢驗', sdk=True)
    expected = {COLLEAGUE, THIRD, FOURTH} if all_members else {COLLEAGUE, THIRD} if case == 'two' else {COLLEAGUE}
    assert set(row['expected']) == expected
    assert row['recipient_scope'] == ('all' if all_members else 'mentioned')
    assert set(row['recipient_ids']) == (set() if all_members else expected)
    assert row['roster_basis'] == ('line_group_members' if all_members else 'explicit_mentions')
    assert USER not in reminders.pending_ids(row) and replies == [] and len(sent) == 1
    card = json.dumps(sent[0][1][-1], ensure_ascii=False)
    assert ('追蹤：全群' if all_members else '追蹤：指定 ' + str(len(expected)) + ' 人') in card


def test_selected_scope_is_frozen_across_rounds_restarts_and_later_group_discovery(hub):
    row, sent, replies, _ = begin(hub, [tagged('@Adi', COLLEAGUE), tagged('@Budi', THIRD)])
    # Neither creation nor reminder delivery should need group-wide APIs.
    def forbidden(group):
        pytest.fail('A targeted notice must not enumerate the group or request its count')
    hub.h['_factory_member_ids'] = hub.h['_factory_member_count'] = forbidden
    newcomer = 'U' + 'e' * 32
    hub.remember_members(GROUP, {newcomer: '新同事'})
    before = stored(hub, row)
    answer(hub, row, FOURTH)
    answer(hub, row, USER)
    assert stored(hub, row) == before and replies == []
    tick(hub, row['reminder_due_at'])
    assert set(recipients(sent[-1])) == {COLLEAGUE, THIRD}
    answer(hub, row, COLLEAGUE)
    fresh = factory.FactoryHub(hub.app, hub.h, hub.store)
    fresh.reminders.sender = hub.reminders.sender
    tick(fresh, stored(hub, row)['next_reminder_at'])
    assert recipients(sent[-1]) == [THIRD]
    saved = stored(hub, row)
    assert set(saved['expected']) == set(saved['recipient_ids']) == {COLLEAGUE, THIRD}
    assert reminders.unknown_member_count(dict(saved, roster_count=99)) == 0
    card = json.dumps(sent[-1][1][-1], ensure_ascii=False)
    assert '指定成員未回覆/Belum menjawab (1): Budi' in card
    assert 'Siti' not in card and '新同事' not in card and '名單不完整' not in card
    answer(fresh, row, THIRD)
    assert_finished(hub, row)
    tick(fresh, row['reminder_due_at'] + 7200)
    assert len(sent) == 3 and replies == []


def test_selected_command_does_not_call_full_roster_api_when_created(hub, monkeypatch):
    calls, original = [], hub._translate_notice
    def translated(*args):
        hub.h['_factory_member_ids'] = lambda group: calls.append('member_ids') or [USER, COLLEAGUE, THIRD]
        hub.h['_factory_member_count'] = lambda group: calls.append('member_count') or 3
        return original(*args)
    monkeypatch.setattr(hub, '_translate_notice', translated)
    row, _, _, _ = begin(hub, [tagged('@Adi', COLLEAGUE)])
    assert calls == [] and row['expected'] == {COLLEAGUE: 'Adi'}


@pytest.mark.parametrize('tags,expected', [
    ([tagged('@Adi', COLLEAGUE), tagged('@Adi', COLLEAGUE), tagged('@本人', USER), tagged('@翻譯', BOT, self_bot=True)], {COLLEAGUE}),
    ([tagged('@本人', USER)], set()),
    ([tagged('@翻譯', BOT, self_bot=True)], set()),
])
def test_duplicate_sender_and_bot_mentions_do_not_broaden_or_block_scope(hub, tags, expected):
    row, sent, replies, _ = begin(hub, tags)
    assert row['recipient_scope'] == 'mentioned'
    assert set(row['expected']) == set(row['recipient_ids']) == expected
    assert len(sent) == 1 and replies == []
    for uid in expected:
        answer(hub, row, uid)
    assert_finished(hub, row)


def test_departed_selected_member_does_not_keep_reminders_running(hub):
    row, sent, replies, _ = begin(hub, [tagged('@Adi', COLLEAGUE), tagged('@Budi', THIRD)])
    hub.member_presence(GROUP, THIRD, left=True)
    answer(hub, row, COLLEAGUE)
    assert_finished(hub, row)
    tick(hub, row['reminder_due_at'])
    assert len(sent) == 1 and replies == []


@pytest.mark.parametrize('invalid', [tagged('@未知'), tagged('@未知', 'invalid-id')])
def test_unavailable_selected_identity_is_not_guessed_or_replaced_with_all(hub, invalid):
    row, sent, replies, _ = begin(hub, [tagged('@Adi', COLLEAGUE), invalid])
    assert row is None and sent == [] and len(replies) == 1
    assert '未取得指定成員的 LINE 身分' in replies[0]['fallback_text']


def test_all_scope_still_refreshes_the_group_in_later_reminder_rounds(hub):
    row, sent, replies, _ = begin(hub)
    new_member = 'U' + 'e' * 32
    hub.h['_factory_member_ids'] = lambda group: [USER, COLLEAGUE, THIRD, FOURTH, new_member]
    tick(hub, row['reminder_due_at'])
    assert set(recipients(sent[-1])) == {COLLEAGUE, THIRD, FOURTH, new_member}
    assert replies == []


def test_context_recovery_keeps_selected_audience_and_old_event_redelivery_does_not_change_it(hub):
    row, sent, replies, ev = begin(hub, [tagged('@Adi', COLLEAGUE), tagged('@Budi', THIRD)])
    key = 'notice:' + GROUP + ':' + row['token']
    hub.store.delete(key)
    answer(hub, row)
    recovered = stored(hub, row)
    assert recovered['recipient_scope'] == 'mentioned'
    assert set(recovered['expected']) == set(recovered['recipient_ids']) == {COLLEAGUE, THIRD}
    # Membership growth must not alter a notice on webhook redelivery.
    with hub.message_scope(ev, 'text'):
        assert hub.command(ev)
    assert stored(hub, row)['recipient_ids'] == recovered['recipient_ids']
    # Repair marks receipt of the existing card before the response write, so
    # no worker can see a fresh initial send in the intervening storage window.
    assert recovered['reminder_due_at'] == recovered['delivered_at'] + 60
    assert recovered['delivered_at'] <= recovered['responses'][COLLEAGUE]['at'] < recovered['reminder_due_at']
    assert replies == [] and len(sent) == 1
    tick(hub, recovered['reminder_due_at'] - 0.01)
    assert len(sent) == 1
    tick(hub, recovered['reminder_due_at'])
    assert recipients(sent[-1]) == [THIRD]


def test_selected_pending_stats_and_admin_exclude_unrelated_stale_rows(hub):
    row, _, replies, _ = begin(hub, [tagged('@Adi', COLLEAGUE)])
    key = 'notice:' + GROUP + ':' + row['token']
    hub.store.update(key, lambda r: dict(r, expected={**r['expected'], FOURTH: 'Siti'},
        responses={FOURTH: {'status': 'understood', 'name': 'Siti', 'at': 1}}, roster_count=90))
    saved = stored(hub, row)
    assert reminders.pending_ids(saved) == [COLLEAGUE]
    assert reminders.unknown_member_count(saved) == 0
    text = hub._receipt_text(saved, '📋 作業確認 / Konfirmasi')
    assert '了解/Paham (0)' in text and 'Siti' not in text and '名單不完整' not in text
    data = hub.app.test_client().get('/api/admin/factory/receipts?group_id=' + GROUP).get_json()['notices'][0]
    assert data['responses'] == {} and data['expected'] == {COLLEAGUE: 'Adi'}
    assert data['pending_ids'] == [COLLEAGUE] and data['unknown_member_count'] == 0 and replies == []


def test_manual_stop_stays_terminal_with_selected_and_unrelated_late_clicks(hub):
    row, sent, replies, _ = begin(hub, [tagged('@Adi', COLLEAGUE)])
    hub.stop_notice(GROUP, row['token'], USER)
    answer(hub, row, FOURTH)
    answer(hub, row, COLLEAGUE)
    saved = stored(hub, row)
    assert set(saved['responses']) == {COLLEAGUE}
    assert saved['reminder_state'] == 'stopped' and saved['wake_at'] is None
    tick(hub, row['reminder_due_at'] + 3600)
    assert len(sent) == 1 and replies == []
