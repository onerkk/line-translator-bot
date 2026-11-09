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


def test_kv_backend_and_trimming():
    store = {}
    def kv(args):
        op, key = args[0], args[1]
        h = store.setdefault(key, {})
        if op == "HINCRBY":
            h[args[2]] = h.get(args[2], 0) + int(args[3]); return h[args[2]]
        if op == "HLEN":
            return len(h)
        if op == "HGETALL":
            out = []
            for k, v in h.items():
                out += [k, str(v)]
            return out
        if op == "HDEL":
            for k in args[2:]:
                h.pop(k, None)
            return 1
    for i in range(60):
        ps.record("G", "U", f"phrase{i}", kv=kv)
    for _ in range(5):
        ps.record("G", "U", "Sudah", kv=kv)
    assert len(store[ps._key("G", "U")]) <= ps.KEEP_PER_USER + 10
    assert ps.top("G", "U", kv=kv) == ["Sudah"]


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
