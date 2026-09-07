from types import SimpleNamespace

import pytest

import app
import line_user_names


GROUP = "C" + "3" * 32
ROOM = "R" + "4" * 32
USER = "U" + "1" * 32
OTHER = "U" + "2" * 32


@pytest.fixture
def names(monkeypatch):
    for attr in ("group_user_names", "dm_known_users", "user_pictures",
                 "user_languages", "_line_profile_cache", "translation_cache"):
        monkeypatch.setattr(app, attr, {})
    monkeypatch.setattr(app, "_line_profile_refreshing", set())
    monkeypatch.setattr(app, "extra_names_by_group", {"__all__": []})
    monkeypatch.setattr(app, "STORAGE_LOOKUP", {})
    monkeypatch.setattr(app, "CUSTOMER_NAMES", [])
    monkeypatch.setattr(app, "EXTRA_CUSTOMERS", [])
    monkeypatch.setattr(app, "check_manager_access", lambda _tab=None: True)
    saves = []
    monkeypatch.setattr(app, "save_settings", lambda *a, **kw: saves.append(1) or True)
    monkeypatch.setattr(app._tl, "group_id", GROUP, raising=False)
    monkeypatch.setattr(app._tl, "user_id", USER, raising=False)
    monkeypatch.setattr(app._tl, "disable_tone_emoji", True, raising=False)
    monkeypatch.setattr(app, "_refresh_line_profile_later", lambda *_a: None)
    return saves


def install_profile_api(monkeypatch, name, *, picture=None):
    calls = []
    profile = SimpleNamespace(display_name=name, picture_url=picture, language="id")
    class Client:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def lookup(kind):
        def run(*a, **kw):
            assert kw["_request_timeout"] == (1, 2)
            calls.append(kind)
            return profile
        return run
    monkeypatch.setattr(app, "ApiClient", Client)
    monkeypatch.setattr(app, "MessagingApi", lambda _c: SimpleNamespace(
        get_profile=lookup("direct"), get_group_member_profile=lookup("group"),
        get_room_member_profile=lookup("room")))
    return calls, profile


def test_existing_directory_is_automatically_visible_without_copying_manual_data(names):
    app.group_user_names[GROUP] = {USER: "林宥辰", OTHER: "budi santoso 山多"}
    app.dm_known_users.update({USER: "林宥辰", OTHER: OTHER})
    app.extra_names_by_group["__all__"] = ["林宥辰", "手動別名"]
    app.STORAGE_LOOKUP["林宥辰"] = []
    app.rebuild_customer_names()
    data = app.app.test_client().get("/api/admin/names").get_json()
    assert data["names"] == ["林宥辰", "手動別名", "budi santoso 山多"]
    assert data["user_names"] == ["林宥辰", "budi santoso 山多"]
    assert (data["count"], data["user_count"], data["manual_count"]) == (3, 2, 1)
    assert app.extra_names_by_group["__all__"] == ["林宥辰", "手動別名"]
    assert names == []  # Backfill needs neither a cloud write nor a profile call.


@pytest.mark.parametrize("chat,kind", [(GROUP, "group"), (ROOM, "room"), (USER, "direct")])
def test_first_profile_is_protected_and_saved_once_without_repeated_lookups(names, monkeypatch, chat, kind):
    calls, _profile = install_profile_api(monkeypatch, "林宥辰✨")
    for _ in range(3):
        app.record_user_name(chat, USER)
        assert app.get_display_name(chat, USER) == "林宥辰✨"
        assert app.get_user_picture_url(chat, USER) == ""
    assert calls == [kind]
    assert names == [1]
    assert app.protected_name_inventory()["user_names"] == ["林宥辰✨"]
    assert app.collect_visible_protected_names("請通知林宥辰✨") == ["林宥辰✨"]


def test_rename_updates_all_known_chats_but_keeps_manual_alias_and_another_user(names):
    app.group_user_names.update({GROUP: {USER: "舊名字", OTHER: "同名"}, ROOM: {USER: "舊名字"}})
    app.dm_known_users[USER] = "舊名字"
    app.extra_names_by_group["__all__"] = ["舊名字"]
    app._remember_line_profile(GROUP, USER, SimpleNamespace(display_name="同名"))
    assert app.group_user_names[GROUP] == {USER: "同名", OTHER: "同名"}
    assert app.group_user_names[ROOM][USER] == app.dm_known_users[USER] == "同名"
    assert app.protected_name_inventory()["user_names"] == ["同名"]
    assert app.protected_name_inventory()["names"] == ["舊名字", "同名"]
    assert names == [1]


def test_rename_refresh_uses_cached_name_without_waiting_and_deduplicates_jobs(names, monkeypatch):
    # Restore the real scheduler hidden by the default offline fixture.
    # Keep the original function object saved below; do not re-import the app.
    monkeypatch.setattr(app, "_refresh_line_profile_later", REAL_REFRESH)
    app.group_user_names[GROUP] = {USER: "原名稱"}
    scheduled = []
    class Thread:
        def __init__(self, *, target, **kw): self.target = target
        def start(self): scheduled.append(self.target)
    monkeypatch.setattr(app.threading, "Thread", Thread)
    calls, _profile = install_profile_api(monkeypatch, "新名稱")
    for _ in range(3):
        assert app.record_user_name(GROUP, USER) == "原名稱"
        assert app.get_user_picture_url(GROUP, USER) == ""
    assert calls == []
    assert len(scheduled) == 1
    scheduled[0]()
    assert app.record_user_name(GROUP, USER) == "新名稱"
    assert app.protected_name_inventory()["user_names"] == ["新名稱"]
    assert calls == ["group"]
    assert len(scheduled) == 1


REAL_REFRESH = app._refresh_line_profile_later


def test_failed_profile_preserves_existing_name_and_never_protects_line_id(names, monkeypatch):
    app.group_user_names[GROUP] = {USER: "既有名稱"}
    app.dm_known_users[OTHER] = OTHER
    assert app._remember_line_profile(GROUP, USER, None) is None
    assert app._remember_line_profile(GROUP, USER, SimpleNamespace(display_name=USER)) is None
    assert app.protected_name_inventory()["user_names"] == ["既有名稱"]
    monkeypatch.setattr(app, "_get_line_member_profile", lambda *_a: None)
    assert app.record_user_name(GROUP, OTHER) is None
    assert names == []


@pytest.mark.parametrize("name,text,expected", [
    ("Adi", "tadi sudah selesai", []),
    ("Ann", "announcement", []),
    ("José", "Joséphine sudah datang", []),
    ("A", "API sudah siap", []),
    ("Adi", "Adi sudah datang", ["Adi"]),
    ("Adi", "請Adi過來", ["Adi"]),
    ("budi santoso 山多", "請找budi santoso 山多確認", ["budi santoso 山多"]),
    ("林宥辰", "林宥辰請過來", ["林宥辰"]),
    ("小麥（研磨股班長）", "小麥（研磨股班長）已確認", ["小麥（研磨股班長）"]),
])
def test_exact_names_do_not_capture_ordinary_word_fragments(names, name, text, expected):
    app.group_user_names[GROUP] = {USER: name}
    assert app.collect_visible_protected_names(text) == expected
    protected, mapping = app.protect_names(text)
    assert app.restore_names(protected, mapping) == text
    if not expected:
        assert protected == text and not mapping


def test_full_name_is_not_split_into_guessed_nicknames(names):
    app.group_user_names[GROUP] = {USER: "budi santoso 山多✨"}
    assert app.collect_visible_protected_names("budi今天沒來") == []
    assert app.protected_name_inventory()["user_names"] == ["budi santoso 山多✨"]


def test_auto_managed_names_cannot_be_duplicated_or_silently_deleted(names):
    app.group_user_names[GROUP] = {USER: "林宥辰"}
    client = app.app.test_client()
    added = client.post("/api/admin/names", json={"action": "add", "name": "林宥辰"}).get_json()
    assert added["source"] == "line_user" and added["added"] is False
    removed = client.post("/api/admin/names", json={"action": "remove", "name": "林宥辰"}).get_json()
    assert removed["locked"] is True and removed["removed"] is False
    assert app.extra_names_by_group == {"__all__": []}


def test_names_endpoint_still_requires_existing_admin_permission(names, monkeypatch):
    monkeypatch.setattr(app, "check_manager_access", lambda _tab=None: False)
    assert app.app.test_client().get("/api/admin/names").status_code == 403


def test_name_change_invalidates_request_cache_and_legacy_bad_name_is_rejected(names, monkeypatch):
    before = app._translation_cache_scope()
    app.group_user_names[GROUP] = {USER: "Adi"}
    assert app._translation_cache_scope() != before
    monkeypatch.setattr(app, "_factory_route_requires_validation", lambda *_a: False)
    app.translation_cache[("Adi sudah datang", "id", "zh")] = ("阿迪已經來了", app.time.time())
    assert app.cache_get("Adi sudah datang", "id", "zh") is None
    assert not app.translation_cache


def test_public_translation_and_quality_guard_receive_the_detected_name(names, monkeypatch):
    app.group_user_names[GROUP] = {USER: "Adi"}
    observed = []
    def core(text, src, tgt):
        observed.append(dict(app._tl.protected_name_map))
        return "Adi已經來了"
    monkeypatch.setattr(app, "_translate_core", core)
    assert "Adi" in app.translate("Adi sudah datang", "id", "zh")
    assert observed and "Adi" in observed[0].values()
    bad = app._factory_guard_report("Adi sudah datang", "阿迪已經來了", "id", "zh")
    assert bad.ok is False


def test_follow_and_member_join_use_the_same_profile_protection(names, monkeypatch):
    calls, _ = install_profile_api(monkeypatch, "新同事")
    app.handle_follow(SimpleNamespace(source=SimpleNamespace(user_id=USER)))
    monkeypatch.setattr(app, "_is_redelivery", lambda *_a: False)
    monkeypatch.setattr(app, "get_group_welcome", lambda *_a: {"enabled": False})
    app.handle_member_joined(SimpleNamespace(source=SimpleNamespace(room_id=ROOM),
        joined=SimpleNamespace(members=[SimpleNamespace(user_id=OTHER)])))
    assert app.dm_known_users[USER] == app.group_user_names[ROOM][OTHER] == "新同事"
    assert app.protected_name_inventory()["user_names"] == ["新同事"]
    assert calls == ["direct", "room"]


def test_automatic_names_are_restored_from_the_real_durable_settings_snapshot(names, monkeypatch):
    documents = []
    monkeypatch.setattr(app, "_persist_settings_document", lambda data: documents.append(data) or True)
    monkeypatch.setattr(app, "_last_persisted_state_hash", "")
    app._remember_line_profile(GROUP, USER, SimpleNamespace(display_name="林宥辰✨"))
    assert app._do_save_impl() is True
    assert documents[-1]["group_user_names"][GROUP][USER] == "林宥辰✨"
    app.group_user_names.clear()
    app.dm_known_users.clear()
    monkeypatch.setattr(app, "_load_settings_document", lambda: documents[-1])
    app.load_settings()
    assert app.protected_name_inventory()["user_names"] == ["林宥辰✨"]


@pytest.mark.parametrize("name", ["😊", "123", "林宥辰✨"])
def test_valid_profile_display_names_are_not_replaced_by_raw_ids(names, name):
    app._remember_line_profile(GROUP, USER, SimpleNamespace(display_name=name))
    assert app.group_user_names[GROUP][USER] == name
    assert app.protected_name_inventory()["user_names"] == [name]
