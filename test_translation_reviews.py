"""Screenshot-review bundles feed glossary, knowledge cards and corrections without code changes."""
import copy
import json

import pytest

import factory_knowledge
import translation_reviews as reviews


def test_shipped_bundles_validate_and_reach_all_three_layers():
    bundles = reviews.load_bundles()
    assert bundles, "at least the 2026-10-20 sample bundle must ship"
    lookup = {}
    assert reviews.apply_glossary(lookup, bundles) >= 1 and "採集檢" in lookup
    entries = reviews.knowledge_entries(bundles)
    assert entries and entries[0]["id"].startswith("review_")
    factory_knowledge.validate_document({"schema_version": 1, "entries": entries})


def test_knowledge_store_retrieves_reviewed_rule_for_a_paraphrase():
    store = factory_knowledge.get_store()
    cards = store.retrieve("阿迪 你的體檢檢體還沒採集，明天司機會去收罐子。", "zh", "id", limit=3) \
        if hasattr(store, "retrieve") else factory_knowledge.retrieve("阿迪 你的體檢檢體還沒採集，明天司機會去收罐子。", "zh", "id", limit=3)
    assert any(str(card.get("id", "")).startswith("review_2026-10-20") for card in cards)
    prompt = factory_knowledge.build_prompt(cards)
    assert "採集" in prompt


def test_review_glossary_never_overwrites_admin_terms():
    bundles = reviews.load_bundles()
    lookup = {"採集檢": {"idn": "admin term"}}
    reviews.apply_glossary(lookup, bundles)
    assert lookup["採集檢"]["idn"] == "admin term"


def test_validation_rejects_placeholders_and_no_op_corrections():
    base = reviews.load_bundles()[0]
    broken = copy.deepcopy(base)
    broken["items"][0]["corrected"] = broken["items"][0]["observed"]
    with pytest.raises(reviews.ReviewError):
        reviews.validate_bundle(broken)
    broken = copy.deepcopy(base)
    broken["items"][0]["corrected"] = "Sampel … belum dikumpulkan"
    with pytest.raises(reviews.ReviewError):
        reviews.validate_bundle(broken)
    broken = copy.deepcopy(base)
    broken["items"][0]["category"] = "typo"
    with pytest.raises(reviews.ReviewError):
        reviews.validate_bundle(broken)


def test_corrections_are_submitted_as_approved_global_rows():
    calls = []
    class FakeAL:
        def submit_correction(self, *args, **kwargs):
            calls.append((args, kwargs)); return {"ok": True}
    stats = reviews.apply_corrections(FakeAL(), reviews.load_bundles())
    assert stats["submitted"] == len(calls) >= 1
    args, kwargs = calls[0]
    assert kwargs["auto_approve"] is True and kwargs["group_id"] == "" and kwargs["approved_by"].startswith("review:")
    assert args[2].startswith("蘇比, sampel")


def test_reviewed_forbidden_phrase_triggers_exactly_one_repair_generation(monkeypatch):
    import app
    from test_translation_instruction_cost_quality import response
    src = "阿迪 你今天體檢沒有採集檢，明天 09:00 司機會去收罐子，請放陰涼處，謝謝"
    bad = ("阿迪, pemeriksaan kesehatan hari ini belum mengambil sampel. Besok pukul 09:00 "
           "sopir akan mengambil wadahnya, letakkan di tempat yang sejuk. Terima kasih.")
    good = ("阿迪, sampel pemeriksaan kesehatan kamu hari ini belum dikumpulkan. Besok pukul 09:00 "
            "sopir akan mengambil wadahnya, letakkan di tempat teduh. Terima kasih.")
    calls = []
    def create(**kw):
        calls.append(kw)
        return response(good if len(calls) > 1 else bad)
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    app._tl.__dict__.clear()
    app._tl.tone, app._tl.group_id = "factory", "Cx"
    out = app.translate(src, "zh", "id")
    assert "belum dikumpulkan" in out and "belum mengambil sampel" not in out
    assert len(calls) == 2
    assert "Previous translation that violated" in calls[1]["messages"][-1]["content"]
    # a clean first candidate never pays for the repair
    calls.clear()
    app._tl.__dict__.clear()
    app._tl.tone, app._tl.group_id = "factory", "Cx"
    monkeypatch.setattr(app.ai.chat.completions, "create", lambda **kw: (calls.append(kw) or response(good)))
    assert "belum dikumpulkan" in app.translate(src + "。", "zh", "id") and len(calls) == 1
