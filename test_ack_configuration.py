"""Saved admin settings must agree with command, card, reply and reminder behavior."""
import copy
import json

import pytest

import line_quick_reply as menus
from test_line_factory_features import hub, storage, event, GROUP, OTHER, COLLEAGUE, USER
from test_line_ack_commands import command
from test_ack_repeat_controls import setup, tick, stored, people, THIRD


@pytest.fixture
def client(hub):
    hub.menu.register(hub.app)
    return hub.app.test_client()


def save(client, group, profile):
    current = client.get('/api/admin/quick-reply/list', query_string={'group_id': group}).get_json()
    response = client.post('/api/admin/quick-reply/save', json={
        'group_id': group, 'version': current['version'], 'profile': profile})
    assert response.status_code == 200, response.get_json()
    return response.get_json()['profile']


@pytest.mark.parametrize('case,alias,menu_enabled', [
    ('deleted', '/ack', True),
    ('disabled', '/確認', True),
    ('image_only', '/ack', True),
    ('status_only', '/確認', True),
    ('deleted', '/確認', False),
])
def test_saved_command_mode_always_delivers_an_answerable_card(hub, client, case, alias, menu_enabled):
    ack = {'id': 'custom_ack', 'type': 'builtin', 'action': 'factory_ack',
           'label': '收到/Paham', 'enabled': case != 'disabled',
           'contexts': ['image'] if case == 'image_only' else ['text', 'image']}
    rows = [] if case == 'deleted' else [ack]
    if case == 'status_only':
        rows = [{**ack, 'id': 'status', 'action': 'factory_receipts', 'label': '回覆/Status'}]
    profile = save(client, GROUP, {'enabled': menu_enabled, 'acknowledgements': 'command', 'items': rows})
    # Reload the persisted document as a fresh menu instance; rendering must not
    # silently put a deleted/disabled shortcut back into the saved settings.
    document = json.loads(json.dumps(hub.h[menus.KEY]))
    hub.h[menus.KEY] = copy.deepcopy(document)
    hub.menu = menus.Menu(hub.h)
    assert client.get('/api/admin/quick-reply/list', query_string={'group_id': GROUP}).get_json()['profile'] == profile
    assert hub.options(GROUP)['acknowledgements'] == 'command'
    sends, replies = [], []
    hub.reminders.sender = lambda *args: sends.append(copy.deepcopy(args))
    hub.h['_factory_member_ids'] = lambda group: [USER, COLLEAGUE]
    hub.h['_send_reply_with_push_fallback'] = lambda **kw: replies.append(kw['fallback_text'])
    row = command(hub, alias + ' 請確認設備已停機')
    assert row is not None and row['delivery_state'] == 'delivered'
    assert row['original'] == '請確認設備已停機' and row['translated']
    assert len(sends) == 1 and not replies
    rendered = json.dumps(sends[0][1], ensure_ascii=False)
    assert 'action=factory_ack&token=' + row['token'] in rendered
    expected_label = menus.BUILTINS['factory_ack'] if case in {'deleted', 'status_only'} else ack['label']
    assert expected_label in rendered
    assert 'factory_help' not in rendered  # Do not resurrect the removed needs-help control.
    preview = client.post('/api/admin/quick-reply/preview', json={
        'group_id': GROUP, 'profile': profile, 'kind': 'ack'}).get_json()
    assert expected_label in [label for page in preview['pages'] for label in page]
    # A normal translation and the user's hidden bottom shortcut stay unchanged.
    for requested in (False, True):
        bottom = hub.menu.actions(GROUP, {'notice_requested': requested}, row['token'])
        assert 'factory_ack' not in json.dumps(bottom)
    assert hub.menu.notice_rows(GROUP, row['original'], requested=False) == []
    assert hub.postback(event(uid=COLLEAGUE), {'action': 'factory_ack', 'token': row['token']})
    assert hub.get_notice(row['token'], GROUP)['responses'][COLLEAGUE]['status'] == 'understood'
    assert replies == []  # Hidden/menu-off cards still record without chat spam.
    assert hub.h[menus.KEY] == document


def test_mode_inheritance_and_group_override_survive_saved_reload(hub, client):
    on = {'enabled': False, 'acknowledgements': 'command', 'items': []}
    off = {**on, 'acknowledgements': 'off'}
    save(client, '', off)
    save(client, GROUP, on)
    hub.menu = menus.Menu(hub.h)
    assert hub.options(GROUP)['acknowledgements'] == 'command'
    assert hub.menu.notice_rows(GROUP, '測試', requested=True)
    assert hub.options(OTHER)['acknowledgements'] == 'off'
    assert not hub.menu.notice_rows(OTHER, '測試', requested=True)
    save(client, '', on)
    save(client, GROUP, off)
    hub.menu = menus.Menu(hub.h)
    assert hub.options(GROUP)['acknowledgements'] == 'off'
    assert hub.options(OTHER)['acknowledgements'] == 'command'
    version = client.get('/api/admin/quick-reply/list').get_json()['version']
    assert client.post('/api/admin/quick-reply/reset', json={'group_id': GROUP, 'version': version}).status_code == 200
    assert hub.options(GROUP)['acknowledgements'] == 'command'
    assert hub.menu.notice_rows(GROUP, '測試', requested=True)
    assert not hub.menu.notice_rows(USER, '測試', requested=True)


def test_explicit_off_blocks_command_before_translation_and_storage(hub, client):
    save(client, GROUP, {'enabled': True, 'acknowledgements': 'off', 'items': []})
    def unexpected(*args):
        pytest.fail('A disabled command must not translate or send a notice')
    hub.h['translate'] = unexpected
    hub.reminders.sender = unexpected
    replies = []
    hub.h['_send_reply_with_push_fallback'] = lambda **kw: replies.append(kw['fallback_text'])
    assert command(hub) is None
    assert '此群組已關閉作業確認' in replies[-1]
    assert '儲存此設定' in replies[-1] and '/ack' in replies[-1] and '/確認' in replies[-1]
    assert not hub.store.recent('notice:' + GROUP)


def test_hiding_shortcuts_keeps_existing_replies_and_repeat_reminders_until_explicit_off(hub, client):
    row, sends, replies = setup(hub)
    profile = save(client, GROUP, {'enabled': False, 'acknowledgements': 'command', 'items': []})
    tick(hub, row['reminder_due_at'])
    assert stored(hub, row)['reminder_state'] == 'repeat_pending'
    assert {person['userId'] for person in people(sends)} == {COLLEAGUE, THIRD}
    hub.postback(event(uid=COLLEAGUE), {'action': 'factory_ack', 'token': row['token']})
    assert stored(hub, row)['responses'][COLLEAGUE]['status'] == 'understood'
    tick(hub, stored(hub, row)['next_reminder_at'])
    assert people(sends) == [{'type': 'user', 'userId': THIRD}]
    due = stored(hub, row)['next_reminder_at']
    save(client, GROUP, {**profile, 'acknowledgements': 'off'})
    count = len(sends)
    tick(hub, due)
    assert len(sends) == count
    assert stored(hub, row)['reminder_state'] == 'cancelled'
    hub.postback(event(uid=THIRD), {'action': 'factory_ack', 'token': row['token']})
    assert '此群組已關閉作業確認' in replies[-1]
    assert THIRD not in stored(hub, row)['responses']
