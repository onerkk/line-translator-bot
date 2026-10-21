"""/help ui105: commands are visible on the first screen; rows open detail cards."""
import json

from linebot.v3.messaging import FlexContainer

import line_command_catalog as catalog


def _texts(node, out):
    if isinstance(node, dict):
        if node.get("type") == "text":
            out.append(node["text"])
        for v in node.values():
            _texts(v, out)
    elif isinstance(node, list):
        for v in node:
            _texts(v, out)
    return out


def test_menu_shows_every_visible_command_without_a_second_tap():
    _, data = catalog.build_help("zh", is_admin=False, topic="")
    FlexContainer.from_dict(data)
    texts = _texts(data, [])
    for row in catalog.COMMANDS:
        shown = ("/" + row["english"] + "  /" + row["chinese"]) in texts
        assert shown == (not row["admin"]), row["key"]
    assert data["contents"][0]["header"]["contents"][0]["contents"][0]["text"].endswith("常用指令")
    assert len(data["contents"]) <= 12


def test_command_row_opens_detail_even_when_key_equals_a_category():
    _, category = catalog.build_help("zh", is_admin=True, topic="ack")
    _, detail = catalog.build_help("zh", is_admin=True, topic="cmd:ack")
    assert len(category["contents"]) == 1 and len(detail["contents"]) == 1
    assert "作業確認與交班" in json.dumps(category, ensure_ascii=False)
    assert "範例 / Contoh" in json.dumps(detail, ensure_ascii=False)
    assert "範例 / Contoh" not in json.dumps(category, ensure_ascii=False)


def test_detail_offers_one_tap_send_only_for_argument_free_commands():
    _, status = catalog.build_help("zh", is_admin=True, topic="cmd:status")
    _, lang = catalog.build_help("zh", is_admin=True, topic="cmd:lang")
    assert any(b["action"].get("type") == "message" and b["action"]["text"] == "/狀態"
               for b in status["contents"][0]["footer"]["contents"])
    assert all(b["action"].get("type") != "message" for b in lang["contents"][0]["footer"]["contents"])


def test_admin_commands_are_hidden_from_members_in_every_view():
    for topic in ("", "admin", "cmd:audit", "settings"):
        _, data = catalog.build_help("id", is_admin=False, topic=topic)
        assert "/audit" not in json.dumps(data) and "/抽檢" not in json.dumps(data, ensure_ascii=False)
