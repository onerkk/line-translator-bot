"""Exercise actual command routing, permission checks and LINE card contracts."""
import copy
import json
from types import SimpleNamespace

import pytest
from linebot.v3.messaging import FlexContainer

import app
import line_command_catalog as catalog
import line_message_ui as ui
from test_line_factory_features import storage, hub, event as factory_event, GROUP, USER, notice
from test_translation_notice_availability import runtime, event, delivered_text


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def test_chinese_switches_have_same_effect_and_admin_gate(monkeypatch):
    monkeypatch.setattr(app, "save_settings", lambda **kwargs: None)
    monkeypatch.setattr(app, "group_img_settings", {})
    monkeypatch.setattr(app, "group_img_ask_settings", {})
    monkeypatch.setattr(app, "is_group_admin", lambda uid: uid == USER)
    assert "管理員" in app.handle_command("/圖片 詢問", GROUP, "non-admin")
    assert not app.group_img_ask_settings
    app.handle_command("/圖片　詢問", GROUP, USER)
    assert app.group_img_settings[GROUP] is False
    assert app.group_img_ask_settings[GROUP] is True
    status = app.handle_command("/狀態", GROUP, USER)
    assert "詢問後翻譯" in status
    app.handle_command("/img on", GROUP, USER)
    assert app.group_img_settings[GROUP] is True
    assert GROUP not in app.group_img_ask_settings
    assert app.handle_command("/圖片", GROUP, USER) == catalog.usage_text("img")


def test_wrongstats_is_not_parsed_as_wrong(monkeypatch):
    monkeypatch.setattr(app, "is_group_admin", lambda uid: False)
    monkeypatch.setattr(app, "translation_log", [])
    monkeypatch.setattr(app, "_verified_custom_translation_examples", lambda: [])
    monkeypatch.setattr(app, "mark_translation_wrong", lambda *a, **k: pytest.fail("stats changed a translation"))
    for command in ["/wrongstats", "/標記統計", "/stats wrong"]:
        assert "翻譯品質統計" in app.handle_command(command, GROUP, USER)


@pytest.mark.parametrize("text", ["/wrongly 請確認", "/today 今天生產", "/pkgX 保留代碼", "/noticeable 請開會"])
def test_unknown_prefixes_remain_translatable(text):
    assert catalog.normalize_command(text) == text
    assert app.handle_command(text, GROUP, USER) is None


def test_command_arguments_preserve_case_newlines_and_literal_names(monkeypatch):
    monkeypatch.setattr(app, "is_group_admin", lambda uid: True)
    monkeypatch.setattr(app, "is_cmd_enabled", lambda *a: True)
    calls = []
    monkeypatch.setattr(app, "make_notice", lambda text, target: calls.append((text, target)) or text)
    text = "@Adi ABc-I5 請確認\n不要改成 I15。"
    assert app.handle_command("/公告　" + text, GROUP, USER) == text
    assert calls[0][0] == text
    assert "公告內容" in app.handle_command("/公告", GROUP, USER)


def test_correction_id_cannot_modify_another_chat(monkeypatch):
    row = {"id": "private-entry", "group_id": "private-group", "src": "私密", "tgt": "rahasia"}
    monkeypatch.setattr(app, "translation_log", [row])
    assert app.mark_translation_wrong(GROUP, entry_id=row["id"])[0] is False
    assert "marked_wrong" not in row


def test_help_covers_every_executable_command_and_both_aliases():
    reached = set()
    for lang in ("zh", "id"):
        for topic in ("", *catalog.CATEGORIES):
            _, data = app.build_help_flex(lang, is_admin=True, topic=topic)
            FlexContainer.from_dict(data)
            assert 1 <= len(data["contents"]) <= 12
            for bubble in data["contents"]:
                assert len(json.dumps(bubble, ensure_ascii=False).encode()) < 30000
            for node in walk(data):
                if node.get("type") == "text":
                    assert len(node["text"]) <= 2000
                    for row in catalog.COMMANDS:
                        if node["text"] == "/" + row["english"] + "  /" + row["chinese"]:
                            reached.add(row["key"])
                if node.get("type") in ("postback", "message"):
                    assert len(node["label"].encode("utf-16-le")) // 2 <= 20
    assert reached == set(catalog.BY_KEY)
    for row in catalog.COMMANDS:
        # The exact names shown to a user must resolve to the real dispatcher.
        assert catalog.command_key("/" + row["english"]) == row["key"]
        assert catalog.command_key("/" + row["chinese"]) == row["key"]
    _, public = app.build_help_flex("zh", is_admin=False, topic="admin")
    assert "/setprice" not in json.dumps(public)


def test_ack_card_prioritizes_complete_question_and_translation(hub):
    token, _, _ = notice(hub)
    record = hub.get_notice(token, GROUP)
    record["original"] = "7J821007\n研發已確認，三把都可以生產。"
    record["translated"] = "[id] R&D sudah memeriksa. Ketiga bundel sudah boleh diproduksi."
    record["responses"] = {USER: {"status": "understood", "name": "Adi"}}
    text = hub._receipt_text(record, "✅ Adi 已了解 / sudah paham")
    card = hub._notice_card(token, text, record).to_dict()["contents"]
    nodes = list(walk(card["body"]))
    source = next(n for n in nodes if n.get("text") == record["original"])
    assert source["size"] == "xl" and source["weight"] == "bold"
    texts = [n["text"] for n in nodes if n.get("type") == "text"]
    assert texts.index(record["original"]) < texts.index("✅ Adi 已了解 / sudah paham")
    assert any("Ketiga bundel" in s for s in texts)
    assert "需說明/Perlu penjelasan (0)" not in "\n".join(texts)
    buttons = list(walk(card["footer"]))
    assert any(n.get("type") == "button" and n.get("style") == "primary" and
               "factory_ack" in n["action"]["data"] for n in buttons)
    assert not any(n.get("type") == "postback" and "factory_help" in n.get("data", "") for n in buttons)


def test_reason_index_tracks_live_alias_changes_without_weakening_match(monkeypatch):
    old = copy.deepcopy(app._FACTORY_REASON_ACTIONS)
    assert app._match_factory_reason_action("套環要補上") is None
    row = copy.deepcopy(old[0])
    row["variants"] = ["完全新的處理原因"]
    monkeypatch.setattr(app, "_FACTORY_REASON_ACTIONS", [row])
    assert app._match_factory_reason_action("完全新的處理原因") is row
    row["variants"] = ["已更改的處理原因"]
    assert app._match_factory_reason_action("完全新的處理原因") is None
    assert app._match_factory_reason_action("已更改的處理原因") is row
    assert app._match_factory_reason_action("不要已更改的處理原因") is None


def test_pmi_analysis_reuse_keeps_different_sources_and_copies_isolated():
    import factory_pmi_semantics as pmi
    import translation_request_cache as memo
    with memo.scope():
        first = pmi.build_facts("PMI一定要檢測。", "zh")
        expected = copy.deepcopy(first)
        first.clear()
        assert pmi.build_facts("PMI一定要檢測。", "zh") == expected
        assert pmi.build_facts("PMI不用檢測。", "zh") != expected


def test_every_legacy_language_and_tool_alias_still_resolves():
    for prefix in app._PERSONAL_LANGUAGE_COMMAND_PREFIXES:
        assert catalog.command_key(prefix) == "mylang"
    for prefix in app._HANDOVER_COMMANDS:
        assert catalog.command_key(prefix) == "handover"
    for prefix in app._INTERPRETER_COMMANDS:
        assert catalog.command_key(prefix) == "interpreter"


def test_help_topic_reaches_actual_webhook_dispatch(runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "send_help_flex", lambda *args, **kwargs: calls.append(kwargs) or True)
    app.handle_message(event("/說明 確認"))
    assert calls[0]["topic"] == "確認"
    assert not runtime.generations


def test_new_group_title_does_not_block_translation(runtime, monkeypatch):
    tasks = []
    class Thread:
        def __init__(self, *, target, **kwargs):
            self.target = target
        def start(self):
            tasks.append(self.target)
    monkeypatch.setattr(app, "threading", SimpleNamespace(**{**vars(app.threading), "Thread": Thread}))
    monkeypatch.setattr(app, "group_tracking", {})
    monkeypatch.setattr(app, "_group_title_pending", set())
    current = event()
    app.handle_message(current)
    assert runtime.sends and runtime.generations
    assert len(tasks) == 1
    assert app.group_tracking[current.source.group_id]["name"] == ""
