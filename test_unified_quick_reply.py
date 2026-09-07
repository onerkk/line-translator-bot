"""Behavioral contracts for the single menu, persistence and actual LINE sends."""
import copy
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from flask import Flask

import app
import line_quick_reply as menus
from test_translation_notice_availability import runtime, event, SOURCE, TARGET, delivered_text
from test_line_factory_features import hub, storage, notice

GROUP = "C" + "1" * 32
OTHER = "C" + "2" * 32


def item(key, label=None, *, action=None, kind=None):
    return {"id": key, "type": "builtin" if action else "message", "action": action or "",
            "text": "msg-" + key, "label": label or key, "enabled": True, "contexts": kind or ["text", "image"]}


def profile(rows, enabled=True, ack="work"):
    return menus.clean_profile({"enabled": enabled, "acknowledgements": ack, "items": rows})


@pytest.fixture
def menu():
    saved = []
    host = {"quick_reply_items_settings": [item("help", "📖 說明/Info")],
            "_reminder_catalog": lambda: {GROUP: {"name": "A 班"}, OTHER: {"name": "B 班"}},
            "check_manager_access": lambda tab: tab == "quickreply",
            "factory_line_settings": {"groups": {GROUP: {"acknowledgements": "all"}}}}
    host["save_settings"] = lambda **_: saved.append(copy.deepcopy(host[menus.KEY])) or True
    return menus.Menu(host), saved


def test_group_override_global_inheritance_and_restart_are_independent(menu):
    m, saved = menu
    m.save("", profile([item("one", "全群預設")]), m.version())
    m.save(GROUP, profile([item("two", "A 班專用")]), m.version())
    m.save("", profile([item("three", "新預設")]), m.version())
    assert m.build(GROUP).items[0].action.label == "A 班專用"
    assert m.build(OTHER).items[0].action.label == "新預設"
    assert m.build("").items[0].action.label == "新預設"
    restarted = menus.Menu({**m.h, menus.KEY: json.loads(json.dumps(saved[-1]))})
    assert restarted.profile(GROUP) == m.profile(GROUP)
    m.save(GROUP, None, m.version(), reset=True)
    assert m.profile(GROUP) == m.profile(OTHER)


def test_delete_and_custom_labels_do_not_reappear_from_legacy_defaults(menu):
    m, saved = menu
    m.save("", profile([item("custom", "自己的按鈕")]), m.version())
    m.h["quick_reply_items_settings"] = [item("help"), item("should_not_return")]
    assert [row["id"] for row in m.profile("")["items"]] == ["custom"]
    m.save("", profile([]), m.version())
    assert m.build(OTHER) is None
    assert saved[-1]["default"]["items"] == []


def test_legacy_migration_keeps_image_choices_group_master_and_camera_overrides(menu):
    m, _ = menu
    m.h["group_image_translation_action_modes"] = {GROUP: {"natural": False}}
    m.h["group_qr_settings"] = {OTHER: False}
    m.h["quick_reply_items_settings"].append({"id": "camera", "type": "camera", "label": "拍照", "enabled": False})
    m.h["group_camera_qr_settings"] = {GROUP: True}
    doc = m.document()
    assert doc["groups"][OTHER]["enabled"] is False
    natural = next(x for x in doc["groups"][GROUP]["items"] if x.get("action") == "natural")
    assert natural["contexts"] == ["text"]
    assert next(x for x in doc["groups"][GROUP]["items"] if x["id"] == "camera")["enabled"]


def test_menu_permissions_use_quickreply_and_not_settings(menu):
    m, _ = menu
    api = Flask(__name__);m.register(api);client = api.test_client()
    read = client.get('/api/admin/quick-reply/list').get_json()
    result = client.post('/api/admin/quick-reply/save', json={"group_id": "", "profile": profile([]), "version": read["version"]})
    assert result.status_code == 200
    m.h["check_manager_access"] = lambda tab: tab == "settings"
    assert client.get('/api/admin/quick-reply/list').status_code == 403
    assert client.post('/api/admin/quick-reply/save', json={}).status_code == 403


def test_save_rejects_stale_cross_group_and_failed_storage_without_losing_settings(menu):
    m, saved = menu
    original = m.version()
    m.save("", profile([item("one")]), original)
    with pytest.raises(ValueError, match="已被更新"):
        m.save(GROUP, profile([]), original)
    with pytest.raises(ValueError, match="已加入"):
        m.save("C" + "3" * 32, profile([]), m.version())
    before = copy.deepcopy(m.document())
    m.h["save_settings"] = lambda **_: False
    with pytest.raises(RuntimeError, match="未儲存"):
        m.save(GROUP, profile([]), m.version())
    assert m.document() == before


def test_more_pages_preserve_all_buttons_and_original_context(menu):
    m, _ = menu
    m.save("", profile([item("button" + str(i)) for i in range(31)]), m.version())
    labels, page = [], 0
    for _ in range(3):
        rendered = m.build(OTHER, token="opaque_context", page=page).items
        assert len(rendered) <= 13
        labels.extend(x.action.label for x in rendered[:-1])
        params = parse_qs(rendered[-1].action.data)
        assert params['token'] == ['opaque_context']
        page = int(params['page'][0])
    assert page == 0
    assert labels == ["button" + str(i) for i in range(31)]


def test_context_buttons_and_notices_obey_same_profile_and_master(menu):
    m, _ = menu
    rows = [item("ack", "收到/Paham", action="factory_ack"), item("style", action="natural", kind=["image"])]
    m.save(GROUP, profile(rows), m.version())
    record = {"original": "PMI一定要檢測", "translated": "Periksa PMI", "tgt": "id"}
    assert [x.action.label for x in m.build(GROUP, record, "test_context").items] == ["收到/Paham"]
    assert len(m.build(GROUP, record, "test_context", "image").items) == 2
    assert m.build(GROUP, {"original": "早安"}, "test_context") is None
    m.save(GROUP, profile(rows, enabled=False), m.version())
    assert m.build(GROUP, record, "test_context", "image") is None
    assert m.notice_rows(GROUP, record['original']) == []


@pytest.mark.parametrize('rows', [
    [item('same'), item('same')],
    [item('a', action='natural'), item('b', action='natural')],
    [dict(item('a'), label='😀'*11)],
    [dict(item('a'), type='uri', uri='javascript:alert(1)')],
    [dict(item('a'), contexts=[])],
])
def test_invalid_menu_is_rejected_instead_of_silently_truncated(rows):
    with pytest.raises(ValueError):
        profile(rows)


def test_actual_text_and_image_builders_follow_saved_order_without_flex_duplicates(runtime, monkeypatch):
    configured = profile([item('custom', '班別按鈕'), item('style', '自然重譯', action='natural')])
    monkeypatch.setattr(app, menus.KEY, {'schema': 1, 'default': profile([], enabled=False), 'groups': {'notice-group': configured}})
    original_builder = app._build_unified_translation_menu
    monkeypatch.setattr(app, '_build_translation_action_quick_reply', original_builder)
    app.handle_message(event())
    assert TARGET in delivered_text(runtime)
    qr = runtime.sends[-1][1].messages[-1].quick_reply
    assert [x.action.label for x in qr.items] == ['班別按鈕','自然重譯']
    image = app._build_image_translation_action_quick_reply('notice-group',SOURCE,TARGET,'zh','id','image',overlay_token='picture')
    assert [x.action.label for x in image.items] == ['班別按鈕','自然重譯']
    assert app._flex_v2_button_row('notice-group',SOURCE,TARGET,'id',src_lang='zh') is None
    assert len(runtime.generations) == 1


def test_menu_master_off_skips_context_registration_and_hidden_append(runtime, monkeypatch):
    monkeypatch.setattr(app, menus.KEY, {'schema': 1, 'default': profile([], enabled=False), 'groups': {}})
    calls=[]
    monkeypatch.setattr(app,'_register_translation_action_context',lambda *a,**k: calls.append(a))
    assert app._build_unified_translation_menu('notice-group',SOURCE,TARGET,'zh','id') is None
    app.handle_message(event())
    assert TARGET in delivered_text(runtime)
    assert all(m.quick_reply is None for _, req, _ in runtime.sends for m in req.messages)
    assert not calls


def test_turning_menu_page_uses_current_group_and_does_not_call_ai(runtime, monkeypatch):
    configured = profile([item('b'+str(i)) for i in range(20)])
    monkeypatch.setattr(app, menus.KEY, {'schema': 1, 'default': configured, 'groups': {}})
    monkeypatch.setattr(app, '_is_duplicate_message', lambda *_: False)
    token = app._register_translation_action_context('notice-group', SOURCE, TARGET, 'zh', 'id')
    e = event();e.postback=SimpleNamespace(data='action=quick_reply_page&page=1&token='+token)
    app.handle_postback(e)
    assert runtime.sends[-1][1].messages[-1].quick_reply.items[0].action.label == 'b12'
    e.source.group_id = OTHER
    app.handle_postback(e)
    assert runtime.sends[-1][1].messages[-1].quick_reply is None
    assert '不屬於此群組' in runtime.sends[-1][1].messages[-1].text
    assert not runtime.generations


def test_settings_page_no_longer_accepts_a_second_menu_configuration(monkeypatch):
    monkeypatch.setattr(app, 'check_manager_access', lambda *_: True)
    response = app.app.test_client().post('/api/admin/features', json={'quick_reply_enabled': False})
    assert response.status_code == 400
    assert '快捷鍵' in response.get_json()['error']


def test_image_overlay_alone_keeps_its_translation_context_after_decoration(runtime, monkeypatch):
    configured=profile([item('photo','圖片對照',action='overlay',kind=['image'])])
    monkeypatch.setattr(app, menus.KEY, {'schema':1,'default':configured,'groups':{}})
    qr=app._build_image_translation_action_quick_reply('notice-group',SOURCE,TARGET,'zh','id','image',overlay_token='overlay_test')
    params=parse_qs(qr.items[0].action.data)
    assert params['token']==['overlay_test'] and params['context_token']
    payload={'group_id':'notice-group','source_text':SOURCE,'target_langs':['id'],'kind':'image'}
    messages=app.factory_hub.decorate_delivery([app.TextMessage(text=TARGET,quick_reply=qr)],payload,TARGET)
    assert len(messages)==1
    assert parse_qs(messages[-1].quick_reply.items[0].action.data)==params


def test_unrelated_legacy_group_settings_do_not_break_global_menu_inheritance(menu):
    m,_=menu
    m.h['factory_line_settings']={'groups':{GROUP:{'translation_mode':'mentioned'}}}
    assert GROUP not in m.document()['groups']
    m.save('',profile([item('global')]),m.version())
    assert m.profile(GROUP)==m.profile(OTHER)


def test_notice_card_uses_the_same_selected_buttons_and_labels_as_bottom_menu(hub):
    rows=[item('ack','我已了解/Paham',action='factory_ack')]
    hub.menu.save(GROUP,profile(rows),hub.menu.version())
    token,payload,messages=notice(hub)
    data=json.dumps([m.to_dict() for m in messages],ensure_ascii=False)
    assert '我已了解/Paham' in data
    assert 'factory_help' not in data and 'factory_receipts' not in data
    assert messages[-1].quick_reply.items[0].action.label=='我已了解/Paham'
    assert hub.get_notice(token,GROUP)
