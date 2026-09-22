"""Language selection, durable content, authenticated editor translation.

All external translation and LINE transports are replaced with local fakes.
"""
import json
from flask import Flask, request
from linebot.v3.messaging import FlexMessage
import pytest

import reminders_web as web
import scheduled_reminders as r
from test_custom_reminder_card import card, texts
from test_scheduled_reminders import store, service, CATALOG, GID, NOW, DUE, spec

ZH = "明天班股會議，早上0750會議室集合\n（台灣同仁就好）"
ID = "Besok ada rapat regu dan bagian. Berkumpul di ruang rapat pukul 07.50 pagi.\nKhusus rekan kerja Taiwan."


def bilingual(**changes):
    return spec(language_mode="bilingual", content_zh=ZH, content_id=ID, **changes)


def test_bilingual_content_is_primary_and_send_time_is_metadata():
    row = r.validate_spec(bilingual(), CATALOG, NOW)
    flex = card(r.build_line_messages(row))
    assert FlexMessage.from_dict(flex).to_dict() == flex
    body = flex["contents"]["body"]
    sections = [node for node in body["contents"] if node["type"] == "box"]
    assert list(texts(sections[0])) == ["繁體中文", ZH]
    assert list(texts(sections[1])) == ["BAHASA INDONESIA", ID]
    assert "08:00" not in "\n".join(texts(body))
    assert "08:00" in "\n".join(texts(flex["contents"]["footer"]))
    assert ZH.splitlines()[0] in flex["altText"]
    assert "maxLines" not in json.dumps(flex) and row["content"] == ZH


@pytest.mark.parametrize("mode,zh,idn,expected", [("zh", ZH, "", [ZH]), ("id", "", ID, [ID])])
def test_single_language_does_not_show_empty_or_unselected_panels(mode, zh, idn, expected):
    row = r.validate_spec(spec(language_mode=mode, content_zh=zh, content_id=idn), CATALOG, NOW)
    body = card(r.build_line_messages(row))["contents"]["body"]
    found = list(texts(body))
    assert all(value in found for value in expected)
    assert (ID not in found) if mode == "zh" else (ZH not in found)
    assert len([node for node in body["contents"] if node["type"] == "box"]) == 1


@pytest.mark.parametrize("changes", [
    {"content_zh": ""}, {"content_id": ""}, {"content_id": ["invalid"]},
    {"content_id": "i" * 1501}, {"content_zh": "🙂" * 751}, {"content_id": "abc\u0001def"},
    {"language_mode": "invalid"},
])
def test_invalid_or_incomplete_languages_cannot_be_saved(changes):
    with pytest.raises(r.ReminderError):
        r.validate_spec(dict(bilingual(), **changes), CATALOG, NOW)


def test_maximum_bilingual_text_and_literals_survive_line_limits():
    zh, idn = "🙂" * 750, "{" * 1500
    row = r.validate_spec(dict(bilingual(), content_zh=zh, content_id=idn), CATALOG, NOW)
    flex = card(r.build_line_messages(row))
    assert zh in list(texts(flex)) and idn in list(texts(flex))
    assert r.utf16_units(flex["altText"]) <= 400
    assert all(r.utf16_units(text) <= 2000 for text in texts(flex))
    assert len(json.dumps(flex["contents"], ensure_ascii=False).encode()) < 30000


def test_edit_list_and_restart_preserve_both_languages(service):
    instance, clock, sent = service
    data = bilingual()
    row = instance.create(data, "admin")
    edited = instance.change(row["id"], dict(data, revision=row["revision"], content_id=ID + "\nTerima kasih."))
    assert instance.list()[0]["content_id"] == ID + "\nTerima kasih."
    restarted = r.ReminderService(instance.store, lambda: CATALOG, sent.append, lambda: DUE)
    assert restarted.run_due()["sent"] == 1
    delivered = list(texts(card(r.build_line_messages(sent[0]))))
    assert ZH in delivered and edited["content_id"] in delivered


def test_duplicate_post_compares_the_indonesian_field_too(service):
    instance, clock, _ = service
    data = bilingual()
    row = instance.create(data, "admin")
    clock[0] = DUE + 1
    assert instance.create(data, "admin") == row
    with pytest.raises(r.ReminderError) as caught:
        instance.create(dict(data, content_id=ID + " Berubah."), "admin")
    assert caught.value.status == 409


def test_bilingual_retry_reuses_frozen_text_without_new_translation(service, monkeypatch):
    instance, clock, sent = service
    row = instance.create(bilingual(), "admin")
    attempts = []
    def fail(record):
        attempts.append(r.build_line_messages(record))
        raise r.DeliveryError("offline")
    instance.sender = fail
    clock[0] = DUE
    assert instance.run_due()["retrying"] == 1
    monkeypatch.setattr(r, "scheduled_reminder_message", lambda *_: pytest.fail("retry must not rebuild"))
    instance.sender = lambda record: attempts.append(r.build_line_messages(record))
    clock[0] += 31
    assert instance.run_due()["sent"] == 1 and attempts[0] == attempts[1]
    assert instance.store.get(row["id"])["content_id"] == ID


@pytest.fixture
def editor_api(monkeypatch, tmp_path):
    calls, state = [], {"result": ID}
    def translate(*args):
        calls.append(args)
        if isinstance(state["result"], Exception):
            raise state["result"]
        return state["result"]
    monkeypatch.setattr(r.ReminderWorker, "start", lambda *a, **k: None)
    monkeypatch.setattr(web, "configured_store", lambda: r.SQLiteReminderStore(tmp_path / "reminders.db"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "offline")
    app = Flask("bilingual-reminders")
    web.register_reminders(app, authorize=lambda: "editor" if request.headers.get("X-Test") == "yes" else None,
                           catalog=lambda: CATALOG, translator=translate)
    instance = app.extensions["scheduled_reminders"]["service"]()
    instance.clock = lambda: NOW
    instance.sender = lambda _: pytest.fail("editor must not send a reminder")
    return app.test_client(), {"X-Test": "yes"}, calls, state, instance


def test_translate_then_save_and_edit_returns_both_languages(editor_api):
    client, headers, calls, state, instance = editor_api
    response = client.post("/api/admin/reminders/translate", headers=headers,
                           json={"group_id": GID, "source": "zh", "target": "id", "content": ZH})
    assert response.status_code == 200 and response.json["content"] == ID
    assert calls == [(ZH, "zh", "id", GID, "editor")] and not instance.list()
    created = client.post("/api/admin/reminders", headers=headers, json=bilingual())
    assert created.status_code == 201
    row = created.json["reminder"]
    assert row["content_zh"] == ZH and row["content_id"] == ID
    assert client.get("/api/admin/reminders", headers=headers).json["reminders"][0]["content_id"] == ID
    changed = client.put("/api/admin/reminders/" + row["id"], headers=headers,
                         json=spec(revision=row["revision"], language_mode="id", content_zh="", content_id=ID))
    assert changed.status_code == 200 and changed.json["reminder"]["content_zh"] == ""
    assert len(calls) == 1


def test_translation_requires_access_known_group_and_supported_languages(editor_api):
    client, headers, calls, _, _ = editor_api
    data = {"group_id": GID, "source": "zh", "target": "id", "content": ZH}
    assert client.post("/api/admin/reminders/translate", json=data).status_code == 403
    for change in ({"group_id": "C" + "f" * 32}, {"source": "en"}, {"target": "zh"}, {"content": ""}):
        assert client.post("/api/admin/reminders/translate", headers=headers, json=dict(data, **change)).status_code == 400
    assert not calls


def test_failed_translation_never_saves_an_incomplete_job(editor_api):
    client, headers, calls, state, instance = editor_api
    state["result"] = RuntimeError("offline")
    response = client.post("/api/admin/reminders/translate", headers=headers,
                           json={"group_id": GID, "source": "id", "target": "zh", "content": ID})
    assert response.status_code == 502 and not response.json["ok"]
    assert client.post("/api/admin/reminders", headers=headers,
                       json=dict(bilingual(), content_id="")).status_code == 400
    assert not instance.list()


def test_app_translator_reuses_factory_fixes_and_restores_chat_context(monkeypatch):
    import app as bot
    saved = dict(bot._tl.__dict__)
    calls = []
    def translate(text, src, tgt):
        calls.append((text, src, tgt, bot._tl.group_id, bot._tl.user_id))
        return "Sudah dialihkan dan seluruhnya sudah masuk stok."
    monkeypatch.setattr(bot, "translate", translate)
    try:
        bot._tl.group_id = "previous-chat"
        snapshot = dict(bot._tl.__dict__)
        result = bot._translate_custom_reminder("轉用全入庫了", "zh", "id", GID, "editor")
        assert result.rstrip(".") == "Seluruh material alih guna sudah masuk stok"
        assert calls == [("轉用全入庫了", "zh", "id", GID, "editor")]
        assert bot._tl.__dict__ == snapshot
    finally:
        bot._tl.__dict__.clear()
        bot._tl.__dict__.update(saved)
