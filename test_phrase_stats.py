import phrase_stats as ps


def test_only_short_reusable_replies_are_counted():
    assert ps.normalize("收到") == "收到"
    assert ps.normalize("  Siap  ") == "Siap"
    assert ps.normalize("/診斷") is None
    assert ps.normalize("@小麥 好") is None
    assert ps.normalize("0730") is None and ps.normalize("7J576102A") is None
    assert ps.normalize("x" * 31) is None
    assert ps.normalize("第一行\n第二行") is None


def test_rankings_are_separate_per_group_and_per_person():
    ps._MEMORY.clear()
    for _ in range(3):
        ps.record("G1", "U1", "收到")
    for _ in range(2):
        ps.record("G1", "U1", "好的")
    ps.record("G1", "U1", "只說一次")
    for _ in range(4):
        ps.record("G1", "U2", "Siap")
        ps.record("G2", "U1", "OK")
    assert ps.top("G1", "U1") == ["收到", "好的"]          # used once → not a habit yet
    assert ps.top("G1", "U2") == ["Siap"]
    assert ps.top("G2", "U1") == ["OK"]


def _fake_kv():
    store, calls = {}, []
    def kv(args):
        calls.append(args[0])
        op, key = args[0], args[1]
        h = store.setdefault(key, {})
        if op == "HGET":
            return h.get(args[2])
        if op == "HSET":
            h[args[2]] = args[3]; return 1
        if op == "HGETALL":
            out = []
            for k, v in h.items():
                out += [k, str(v)]
            return out
    return kv, store, calls


def test_store_writes_are_batched_to_protect_the_upstash_quota(monkeypatch):
    ps._MEMORY.clear()
    kv, store, calls = _fake_kv()
    for i in range(60):
        ps.record("G", "U", f"phrase {chr(65 + i % 26)}{i}", kv=kv)
    for _ in range(5):
        ps.record("G", "U", "Sudah", kv=kv)
    # 65 chat messages cost one load (HGET + one-time migration probe), not 130 commands
    assert calls.count("HSET") == 0 and len(calls) <= 2
    assert ps.top("G", "U", kv=kv) == ["Sudah"]
    assert len(ps._MEMORY[("G", "U")]["counts"]) <= ps.KEEP_PER_USER + 10
    assert ps.flush_all(kv) == 1 and calls.count("HSET") == 1
    # a fresh process reads the saved ranking back with a single command
    ps._MEMORY.clear(); calls.clear()
    assert ps.top("G", "U", kv=kv) == ["Sudah"] and calls == ["HGET"]


def test_due_flush_happens_on_the_next_message(monkeypatch):
    ps._MEMORY.clear()
    kv, store, calls = _fake_kv()
    ps.record("G", "U", "收到", kv=kv)
    ps._MEMORY[("G", "U")]["last_flush"] -= ps.FLUSH_SECONDS + 1
    ps.record("G", "U", "收到", kv=kv)
    assert calls.count("HSET") == 1


def test_first_layout_is_migrated_once():
    ps._MEMORY.clear()
    kv, store, calls = _fake_kv()
    store[ps._OLD_KEY_PREFIX + "G:U"] = {"收到": 3, "好": 1}
    assert ps.top("G", "U", kv=kv) == ["收到"]


def test_group_token_hides_group_id():
    token = ps.group_token("C123456", "secret")
    assert "C123456" not in token and len(token) == 24
    assert token == ps.group_token("C123456", "secret")


def test_group_messages_feed_the_per_person_ranking(monkeypatch):
    import app
    from test_translation_notice_availability import runtime, event  # noqa: F401
    recorded = []
    monkeypatch.setattr(app.phrase_stats_module, "record", lambda g, u, t, kv=None: recorded.append((g, u, t)))
    class Now:
        def submit(self, fn, *a, **k):
            fn(*a, **k)
    monkeypatch.setattr(app, "_EVENT_LOG_EXECUTOR", Now())
    monkeypatch.setattr(app, "translate", lambda *a, **k: "Diterima")
    ev = event("收到")
    try:
        app.handle_message(ev)
    except Exception:
        pass
    assert any(t == "收到" for _g, _u, t in recorded)


def test_shortcut_button_and_group_token_roundtrip(monkeypatch):
    import app
    monkeypatch.setitem(app.group_settings, "Cgroup1", True)
    action = app._phrases_shortcut_action("Cgroup1")
    assert action["type"] == "uri" and action["uri"].startswith("https://liff.line.me/2009810040-hthJ46K7?g=")
    assert len(action["label"]) <= 20 and "Cgroup1" not in action["uri"]
    token = action["uri"].split("g=", 1)[1]
    assert app._phrases_group_from_token(token) == "Cgroup1"
    assert app._phrases_group_from_token("forged") == ""
    qr = app.build_quick_reply("Cgroup1")
    labels = [item.action.label for item in qr.items]
    assert labels[0] == "⚡ 常用句/Kalimat" and len(labels) <= 13


def test_panel_api_returns_only_the_callers_own_phrases(monkeypatch):
    import app
    ps._MEMORY.clear()
    monkeypatch.setattr(app, "_kv_enabled", lambda: False)
    monkeypatch.setitem(app.group_settings, "Cgroup1", True)
    for _ in range(3):
        ps.record("Cgroup1", "Uaaa", "收到")
        ps.record("Cgroup1", "Ubbb", "Siap")
    token = app._phrases_shortcut_action("Cgroup1")["uri"].split("g=", 1)[1]
    client = app.app.test_client()
    monkeypatch.setattr(app, "_phrases_user_from_access_token", lambda t: "Uaaa" if t == "tok-a" else "")
    r = client.post("/api/liff/phrases", json={"g": token, "access_token": "tok-a"})
    assert r.status_code == 200 and r.get_json()["phrases"] == ["收到"]
    assert client.post("/api/liff/phrases", json={"g": token, "access_token": "bad"}).status_code == 401
    assert client.post("/api/liff/phrases", json={"g": "x", "access_token": "tok-a"}).status_code == 400
    page = client.get("/liff/phrases").get_data(as_text=True)
    assert '"2009810040-hthJ46K7"' in page and "liff.sendMessages" in page


def test_access_token_from_another_channel_is_rejected(monkeypatch):
    import app
    import requests
    class R:
        def __init__(self, code, data):
            self.status_code, self._d = code, data
        def json(self):
            return self._d
    calls = []
    def fake_get(url, **kw):
        calls.append(url)
        if "verify" in url:
            return R(200, {"client_id": "999", "expires_in": 100})
        return R(200, {"userId": "Uaaa"})
    monkeypatch.setattr(requests, "get", fake_get)
    assert app._phrases_user_from_access_token("t") == ""
    def fake_ok(url, **kw):
        if "verify" in url:
            return R(200, {"client_id": "2009810040", "expires_in": 100})
        return R(200, {"userId": "Uaaa"})
    monkeypatch.setattr(requests, "get", fake_ok)
    assert app._phrases_user_from_access_token("t") == "Uaaa"


def test_translation_latency_is_measured_per_group(monkeypatch):
    import app
    app._LATENCY_SAMPLES.clear()
    for ms, chars in ((800, 4), (1200, 6), (5000, 120)):
        app._record_translation_latency("x" * chars, ms, "Cg", True)
    app._record_translation_latency("x", 9000, "Cother", True)
    rep = app._latency_report("Cg")
    assert rep["短句（30字內）"]["n"] == 2 and rep["短句（30字內）"]["max"] == 1200
    assert rep["長訊息"]["n"] == 1 and rep["長訊息"]["p50"] == 5000
