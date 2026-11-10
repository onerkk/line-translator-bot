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


def test_report_counts_repeated_mistakes_on_new_sentences(monkeypatch):
    seen = []
    def fake_translate(app_module, text, src, tgt, hide_learning):
        seen.append((text, hide_learning))
        assert getattr(app_module._tl, "eval_hide_cards", None) is None      # switches never leak out
        return "salah" if hide_learning else "benar"
    monkeypatch.setattr(live, "translate_text", fake_translate)
    variant = lambda app_module, case, model: "新句子 " + case["item_id"]
    judge = lambda app_module, case, variant_text, output, model: {"repeats": output == "salah", "note": ""}
    report = live.run(app, limit=4, seed=1, model="m", variant_fn=variant, judge_fn=judge)
    assert report["scored"] == 4 and report["untrained_mistakes"] == 4 and report["trained_mistakes"] == 0
    assert len(report["fixed"]) == 4 and report["still_wrong"] == [] and report["newly_wrong"] == []
    assert all(text.startswith("新句子 ") for text, _ in seen)               # the stored sentence is never the exam
    text = live.summary_text(report, "abc1234")
    assert "有學到" in text and "沒學過：4 題" in text and "學過後：0 題" in text and "abc1234" in text


def test_summary_is_honest_when_nothing_improves_or_cannot_tell():
    base = {"scored": 5, "fixed": [], "still_wrong": ["a", "b"], "newly_wrong": [], "never_wrong": ["c", "d", "e"],
            "untrained_mistakes": 2, "trained_mistakes": 2}
    assert "沒有改善" in live.summary_text(base) and "仍然錯：a、b" in live.summary_text(base)
    none = dict(base, still_wrong=[], untrained_mistakes=0, trained_mistakes=0, never_wrong=list("abcde"))
    assert "看不出學習的差別" in live.summary_text(none)
    worse = dict(base, still_wrong=[], newly_wrong=["x"], untrained_mistakes=0, trained_mistakes=1)
    assert "變差了" in live.summary_text(worse) and "反而變錯：x" in live.summary_text(worse)


def test_unusable_variants_are_skipped_not_scored(monkeypatch):
    monkeypatch.setattr(live, "translate_text", lambda *a, **k: "x")
    report = live.run(app, limit=3, seed=2, model="m", variant_fn=lambda *a: "",
                      judge_fn=lambda *a: {"repeats": False, "note": ""})
    assert report["scored"] == 0 and report["skipped"] == 3
    assert "沒有可評分" in live.summary_text(report)


def test_translate_text_sets_and_restores_thread_state(monkeypatch):
    seen = {}
    def fake(text, src, tgt):
        seen.update(cache=app._tl.eval_no_cache, hide=getattr(app._tl, "eval_hide_source", None),
                    cards=getattr(app._tl, "eval_hide_cards", None))
        return "ok"
    monkeypatch.setattr(app, "translate", fake)
    app._tl.group_id = "Ckeep"
    assert live.translate_text(app, "S", "zh", "id", True) == "ok"
    assert seen == {"cache": True, "hide": "*", "cards": "all"}
    assert app._tl.group_id == "Ckeep" and not hasattr(app._tl, "eval_no_cache")
    live.translate_text(app, "S", "zh", "id", False)
    assert seen == {"cache": True, "hide": None, "cards": None}          # trained: everything learned stays on


def test_real_pipeline_runs_in_both_modes_without_touching_the_stores(monkeypatch):
    from test_translation_instruction_cost_quality import response
    prompts = []
    def create(**kw):
        prompts.append(str(kw.get("messages")))
        return response("Tidak dipasang Cincin Pelindung.")
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    stored = []
    monkeypatch.setattr(app, "_post_translation_async", lambda *a, **k: stored.append(a))
    class Now:
        def submit(self, fn, *a, **k):
            fn(*a, **k)
    monkeypatch.setattr(app, "_BG_POST_EXECUTOR", Now())
    for hide in (True, False):
        assert "Cincin Pelindung" in live.translate_text(app, "這捆沒有加套環", "zh", "id", hide)
    assert len(prompts) >= 2                       # no cache / memory hit between the two modes
    assert stored == []                            # evaluation output never enters translation memory
    assert isinstance(app.pick_model("評分"), str) and app.pick_model("評分")


def test_untrained_mode_never_answers_a_reviewed_sentence_from_memory(monkeypatch, tmp_path):
    """Real failure 2026-11-11: /評測 reported 5.0 vs 5.0 because the approved
    answer was served from four different memory layers."""
    import active_learning as al
    import translation_reviews as tr
    from test_translation_instruction_cost_quality import response
    monkeypatch.setattr(al, "AL_DB_PATH", str(tmp_path / "al.db"))
    monkeypatch.setattr(al, "_init_done", False)
    al.init()
    tr.apply_corrections(al, tr.load_bundles())
    app.translation_casebook_module.invalidate_active_cache()
    calls = []
    def create(**kw):
        calls.append(1)
        return response("MODEL OUTPUT")
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    routes = []
    original_log = app._log_translation
    def spy(*a, **k):
        routes.append(str(a[4]) if len(a) > 4 else "?")
        return original_log(*a, **k)
    monkeypatch.setattr(app, "_log_translation", spy)
    served_from_memory, without_model = [], set()
    for case in live.load_cases(100, seed=1):
        before = len(calls); routes.clear()
        live.translate_text(app, case["source"], case["src_lang"], case["tgt_lang"], True)
        if any(("verified" in r) or ("cache" in r) or r.startswith("tm") for r in routes):
            served_from_memory.append((case["item_id"], list(routes)))
        if len(calls) == before:
            without_model.add(case["item_id"])
    assert served_from_memory == []
    # Only deterministic local translators (fixed record/ERP phrasing) may answer without a model.
    assert len(without_model) <= 4, sorted(without_model)


def test_generic_workplace_words_do_not_pull_in_an_unrelated_correction(monkeypatch, tmp_path):
    """Regression from the first real /評測: 回報/安排/工作 alone attached a long
    異型棒 notice to a PPE-photo request and made the translation worse."""
    import active_learning as al
    import translation_reviews as tr
    monkeypatch.setattr(al, "AL_DB_PATH", str(tmp_path / "al.db"))
    monkeypatch.setattr(al, "_init_done", False)
    al.init()
    tr.apply_corrections(al, tr.load_bundles())
    app.translation_casebook_module.invalidate_active_cache()
    src = "早上工作安排後，安全護具的拍照回報先讓我拍穿長袖制服，要脫後面再脫掉🙏"
    try:
        app._tl.__dict__.clear()
        app._tl.eval_hide_source, app._tl.eval_hide_cards = src, "ppe-photo-who-wears"
        cases = app._retrieve_verified_translation_cases(src, "zh", "id", max_cases=3)
        assert [c for c in cases if "異型棒" in str(c.get("source", ""))] == []
        # a sentence that really shares the subject still finds its reference
        related = app._retrieve_verified_translation_cases(
            "線外人員入儲分流時注意異型棒出料，地上有3把檢驗完成就上車", "zh", "id", max_cases=3)
        assert any("異型棒" in str(c.get("source", "")) for c in related)
    finally:
        app._tl.__dict__.clear()


def test_a_new_sentence_of_the_same_kind_receives_the_lesson_only_when_trained(monkeypatch):
    """The exam can only show learning if the lesson reaches a sentence that was
    never stored: here a different ID with the same OKGO wording."""
    from test_translation_instruction_cost_quality import response
    prompts = {}
    state = {"mode": None}
    def create(**kw):
        prompts[state["mode"]] = str(kw.get("messages"))
        return response("ID 7K902200 還沒辦法按 OKGO。")
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    for hide in (True, False):
        state["mode"] = "untrained" if hide else "trained"
        live.translate_text(app, "ID 7K902200 belum bisa OKGO", "id", "zh", hide)
    assert "下一個製程" in prompts["trained"] or "下一製程" in prompts["trained"]
    assert "下一個製程" not in prompts["untrained"] and "下一製程" not in prompts["untrained"]
