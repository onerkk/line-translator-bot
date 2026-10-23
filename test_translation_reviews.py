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
    base = next(b for b in reviews.load_bundles() if b["review_id"].startswith("2026-10-20"))
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
    args, kwargs = next(c for c in calls if c[1]["approved_by"].endswith("health-check-sample"))
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


def test_verified_correction_bad_wording_also_earns_the_single_repair(monkeypatch):
    import app
    from test_translation_instruction_cost_quality import response
    import translation_casebook as casebook
    src = "黃色貼紙留在櫃子，客戶的紫色貼紙改放辦公桌旁。"
    case = {"source": "客戶的紫色貼紙先放辦公桌，黃色貼紙留在櫃子。",
            "target": "Taruh stiker ungu milik pelanggan di meja kantor dahulu, dan biarkan stiker kuning di lemari.",
            "bad_target": "Stiker ungu disimpan di lemari.", "direction": "zh-id", "origin": "active_learning",
            "case_id": "correction:1", "source_match": {}, "verified_correction": True, "revision": 1,
            "group_id": "", "validation_state": "passed", "reason": "顏色與位置對調"}
    monkeypatch.setattr(casebook, "active_corrections_snapshot", lambda *a, **k: [dict(case)])
    bad = "Stiker ungu disimpan di lemari, stiker kuning taruh di meja kantor."
    good = "Biarkan stiker kuning di lemari, stiker ungu milik pelanggan dipindahkan ke samping meja kantor."
    calls = []
    def create(**kw):
        calls.append(kw)
        return response(good if len(calls) > 1 else bad)
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    app._tl.__dict__.clear()
    app._tl.tone, app._tl.group_id = "factory", "Cx"
    out = app.translate(src, "zh", "id")
    assert len(calls) <= 2
    if len(calls) == 2:
        assert "kuning di lemari" in out


def test_role_canonicalization_keeps_sentence_initial_capital():
    import factory_terminology as ft
    out = ft.canonicalize_organization_translation("處長下來巡視", "Kepala Divisi turun untuk meninjau keadaan.", "zh", "id")
    assert out.startswith("Kepala divisi")
    out = ft.canonicalize_organization_translation("處長下來巡視", "Tadi kepala departemen turun.", "zh", "id")
    assert out == "Tadi kepala divisi turun."


def test_2026_10_22_intake_rule_uses_tons_and_target_not_work_order_numbers(monkeypatch):
    import app
    from test_translation_instruction_cost_quality import response
    src = "圓棒跟急單幫忙注意，10/25幫忙入到170～175"
    bad = "Mohon perhatikan batang bulat dan work order mendesak. Pada 10/25 jumlah yang masuk mencapai 170～175."
    good = "Mohon perhatikan batang bulat dan work order mendesak; pada 10/25 mohon bantu catat pemasukan gudang sampai 170–175 ton."
    calls = []
    def create(**kw):
        calls.append(kw); return response(good if len(calls) > 1 else bad)
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    app._tl.__dict__.clear(); app._tl.tone, app._tl.group_id = "factory", "Cx"
    out = app.translate(src, "zh", "id")
    prompt = calls[0]["messages"][0]["content"]
    assert "入庫噸數目標" in prompt and "工單編號" in prompt  # card says: tons, not work-order numbers
    assert "170–175 ton" in out and len(calls) == 2


def test_alert_rule_needs_a_presence_cue_not_just_a_supervisor_title():
    import factory_knowledge
    store = factory_knowledge.get_store()
    hit = store.retrieve("處長晚上可能會進來巡視，大家再注意一下。", "zh", "id", limit=5)
    miss = store.retrieve("樓上是一股股長，基本紀律注意一下，他什麼都會跟處長講。", "zh", "id", limit=5)
    assert any(str(c.get("id", "")).endswith("supervisor-return-stay-alert") for c in hit)
    assert not any(str(c.get("id", "")).endswith("supervisor-return-stay-alert") for c in miss)


def test_three_station_shift_estimate_rule_reaches_prompt_for_paraphrase():
    import factory_knowledge
    cards = factory_knowledge.get_store().retrieve("明天開兩站夜班大概在15噸左右", "zh", "id", limit=5)
    assert any(str(c.get("id", "")).endswith("three-stations-shift-estimate") for c in cards)


def test_review_glossary_makes_storage_transport_dept_global():
    lookup = {}
    reviews.apply_glossary(lookup, reviews.load_bundles())
    assert lookup["儲運"]["idn"] == "bagian pergudangan dan transportasi"


def test_removed_bundle_items_are_retracted_but_admin_rows_are_not():
    rejected = []
    rows = [
        {"id": 1, "src_lang": "zh", "tgt_lang": "id", "src_text": "舊句", "corrected_translation": "lama",
         "corrected_by": "review:old-bundle"},
        {"id": 2, "src_lang": "zh", "tgt_lang": "id", "src_text": "後台改的", "corrected_translation": "admin",
         "corrected_by": "admin"},
    ]
    class FakeAL:
        def list_corrections(self, **kw):
            return rows if kw.get("offset", 0) == 0 else []
        def reject_correction(self, cid, **kw):
            rejected.append(cid); return {"ok": True}
    bundles = reviews.load_bundles()
    assert reviews.retract_removed(FakeAL(), bundles) == 1 and rejected == [1]
    item = bundles[0]["items"][0]
    rows[0].update(src_text=item["source"], corrected_translation=item["corrected"],
                   src_lang=item["src"], tgt_lang=item["tgt"])
    rejected.clear()
    assert reviews.retract_removed(FakeAL(), bundles) == 0


def test_confirmed_length_entry_rule_repairs_measure_wording(monkeypatch):
    import app
    from test_translation_instruction_cost_quality import response
    src = "沒短尺的都亂打重量跟長度"
    bad = "Kalau tidak ada material pendek, panjangnya diukur asal-asalan."
    good = "Tidak ada material pendek, tapi data berat dan panjangnya diinput asal-asalan di sistem."
    calls = []
    def create(**kw):
        calls.append(kw); return response(good if len(calls) > 1 else bad)
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    app._tl.__dict__.clear(); app._tl.tone, app._tl.group_id = "factory", "Cx"
    out = app.translate(src, "zh", "id")
    assert "系統輸入" in calls[0]["messages"][0]["content"]
    assert "diinput" in out and len(calls) == 2


def test_middle_shift_is_a_confirmed_hard_term():
    lookup = {}
    reviews.apply_glossary(lookup, reviews.load_bundles())
    assert lookup["中班"]["idn"] == "shift tengah" and lookup["中班"]["translation_mode"] == "hard"
    assert "小夜班" not in lookup  # existing card keeps 小夜班 = shift sore
