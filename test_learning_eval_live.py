import threading

import app
import learning_eval_live as live


def _review_ids(cards):
    return [c["id"] for c in cards if c["id"].startswith("review_")]


def test_rule_cards_are_hidden_only_for_the_evaluating_thread():
    text = "圓棒十點前會放，再幫忙吊過來包裝站"
    app._tl.__dict__.pop("eval_hide_cards", None)
    normal = _review_ids(app._FACTORY_KNOWLEDGE_STORE.retrieve(text, "zh", "id", limit=6))
    assert any(i.endswith("bundle-data-released") for i in normal)
    seen = {}
    def other_thread():
        seen["ids"] = _review_ids(app._FACTORY_KNOWLEDGE_STORE.retrieve(text, "zh", "id", limit=6))
    try:
        app._tl.eval_hide_cards = "all"
        assert _review_ids(app._FACTORY_KNOWLEDGE_STORE.retrieve(text, "zh", "id", limit=6)) == []
        t = threading.Thread(target=other_thread); t.start(); t.join()
        assert seen["ids"] == normal                      # live traffic keeps its learning
        app._tl.eval_hide_cards = "bundle-data-released"
        own_hidden = _review_ids(app._FACTORY_KNOWLEDGE_STORE.retrieve(text, "zh", "id", limit=6))
        assert not any(i.endswith("bundle-data-released") for i in own_hidden)
    finally:
        app._tl.__dict__.pop("eval_hide_cards", None)


def test_corrections_and_cache_switches(monkeypatch):
    rows = [{"src_text": "沒加套環", "id": 1}, {"src_text": "收到  了", "id": 2}]
    monkeypatch.setattr(app.translation_casebook_module, "active_corrections_snapshot", lambda *a, **k: list(rows))
    try:
        assert len(app._active_translation_corrections_for_casebook("")) == 2
        app._tl.eval_hide_source = "收到 了"
        assert [r["id"] for r in app._active_translation_corrections_for_casebook("")] == [1]
        app._tl.eval_hide_source = "*"
        assert app._active_translation_corrections_for_casebook("") == []
        app._tl.eval_no_cache = True
        assert app.cache_get("測試", "zh", "id") is None
        assert app.cache_set("測試", "zh", "id", "Tes") is None
    finally:
        for k in ("eval_hide_source", "eval_no_cache"):
            app._tl.__dict__.pop(k, None)


def test_report_compares_untrained_with_generalised(monkeypatch):
    calls = []
    def fake_translate(app_module, case, hide):
        calls.append(hide)
        assert getattr(app_module._tl, "eval_hide_cards", None) is None      # switches never leak out
        return "baik" if hide == "own" else "buruk"
    monkeypatch.setattr(live, "translate_in_mode", fake_translate)
    judge = lambda app_module, case, output, model: {"score": 5 if output == "baik" else 3, "note": ""}
    report = live.run(app, limit=4, seed=1, judge_model="m", judge=judge)
    assert report["cases"] == 4 and report["scored"] == 4 and calls.count("all") == 4 and calls.count("own") == 4
    assert report["untrained"] == 3 and report["generalised"] == 5 and len(report["better"]) == 4
    text = live.summary_text(report, "abc1234")
    assert "有變聰明" in text and "+2" in text and "abc1234" in text


def test_translate_in_mode_restores_thread_state(monkeypatch):
    seen = {}
    def fake(text, src, tgt):
        seen.update(cache=app._tl.eval_no_cache, hide=app._tl.eval_hide_source, cards=app._tl.eval_hide_cards)
        return "ok"
    monkeypatch.setattr(app, "translate", fake)
    app._tl.group_id = "Ckeep"
    case = {"source": "S", "item_id": "x", "src_lang": "zh", "tgt_lang": "id"}
    assert live.translate_in_mode(app, case, "own") == "ok"
    assert seen == {"cache": True, "hide": "S", "cards": "x"}
    assert app._tl.group_id == "Ckeep" and not hasattr(app._tl, "eval_no_cache")
    live.translate_in_mode(app, case, "all")
    assert seen["hide"] == "*" and seen["cards"] == "all"


def test_real_pipeline_runs_in_both_modes_without_touching_the_stores(monkeypatch):
    from test_translation_instruction_cost_quality import response
    prompts = []
    def create(**kw):
        prompts.append(str(kw.get("messages")))
        return response("Tidak dipasang Cincin Pelindung.")
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    case = next(c for c in live.load_cases(100, seed=3) if c["item_id"] == "tidak-bulat-out-of-round" or True)
    case = {"id": "x", "item_id": "nonexistent", "source": "這捆沒有加套環", "corrected": "Bundel ini tidak dipasang Cincin Pelindung.",
            "src_lang": "zh", "tgt_lang": "id", "category": "term"}
    stored = []
    monkeypatch.setattr(app, "_post_translation_async", lambda *a, **k: stored.append(a))
    class Now:
        def submit(self, fn, *a, **k):
            fn(*a, **k)
    monkeypatch.setattr(app, "_BG_POST_EXECUTOR", Now())
    for hide in ("all", "own"):
        assert "Cincin Pelindung" in live.translate_in_mode(app, case, hide)
    assert len(prompts) >= 2                       # no cache / memory hit between the two modes
    assert stored == []                            # evaluation output never enters translation memory
    assert isinstance(app.pick_model("評分"), str) and app.pick_model("評分")
