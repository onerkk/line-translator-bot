"""Held-out routing/retrieval/PMI probes, with real local delivery guards.

These are code regressions, not measurements of a live model's accuracy.
No source/target pair below is installed as a translation shortcut.
"""
from types import SimpleNamespace

import pytest

import app
import ai_provider
import factory_pmi_semantics as pmi
import translation_casebook as casebook
from test_translation_notice_availability import SOURCE


@pytest.fixture(autouse=True)
def clean_context(monkeypatch):
    saved = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    monkeypatch.setenv("TRANSLATION_CP_ROUTER_ENABLED", "1")
    monkeypatch.setattr(app, "model_threshold", 150)
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(saved)


ROUTINE_ID = [
    "Besok saya masuk kerja seperti biasa.",
    "Nanti saya kirim fotonya setelah makan.",
    "Terima kasih, nanti saya kabari lagi.",
    "Saya sudah membaca pesan yang dikirim tadi.",
    "Tolong kirim foto kemasan ke grup ini.",
    "Hari ini saya pulang bersama teman.",
]


@pytest.mark.parametrize("source", ROUTINE_ID)
def test_indonesian_prose_does_not_pay_for_fictitious_equipment_codes(source):
    app._tl.semantic_contract = app.build_translation_semantic_contract(source, "id", "zh")
    assert app.pick_model(source) == ai_provider.DEFAULT_OPENAI_MODEL


@pytest.mark.parametrize("source", [
    "請核對 I5、I15、BF3、E11 各機台的生產紀錄與目前的包裝進度。",
    "Tolong cocokkan I5, I15, BF3 dan E11 dengan catatan produksi.",
    "請核對 R28.57、H26、1250kg、4.5cm 與材料規格和工單資料是否一致。",
    "禁止混料，必須立即停機。",
    "Dilarang mencampur material, segera hentikan mesin.",
    "1.確認標籤。\n2.核對重量。\n3.確認包裝。\n4.交接班。",
    SOURCE,
])
def test_real_data_and_high_risk_instructions_keep_quality_tier(source):
    assert app.pick_model(source) == ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL


def test_operational_risk_context_and_explicit_override_are_preserved():
    for contract in ({"context_bound": True}, {"risks": [{"analysis": {"suggestions": ["uncertain"]}}]}):
        app._tl.semantic_contract = contract
        assert app.pick_model("資料確認") == ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL
    app._tl.force_model = "gpt-5.6-sol"
    assert app.pick_model(ROUTINE_ID[0]) == "gpt-5.6-sol"


@pytest.mark.parametrize("source", [
    "今天下午再確認包裝進度。", "明天下午再確認包裝進度。", "下午要再核對打包的進度。",
])
def test_generic_time_and_check_words_cannot_select_maintenance_or_staffing(source):
    rows = app._retrieve_verified_translation_cases(source, "zh", "id")
    assert not {"example:23", "month_end_staffing_target_without_invented_unit"} & {r["case_id"] for r in rows}
    assert "3800" not in casebook.build_prompt(rows)


def test_explicit_maintenance_and_steel_grade_still_retrieve_useful_examples():
    rows = app._retrieve_verified_translation_cases("今天BF2下午要保養，料件提早做完。", "zh", "id")
    assert any("保養" in r["source"] for r in rows)
    rows = app._retrieve_verified_translation_cases("PMI還沒檢測。", "zh", "id")
    assert any("PMI" in r["source"] for r in rows)


PMI_CASES = [
    ("PMI還沒檢測。", "PMI belum diperiksa.", "PMI sudah diperiksa.", "pending"),
    ("PMI還没檢測。", "PMI belum diuji.", "PMI sudah diuji.", "pending"),
    ("PMI尚未檢驗。", "Pemeriksaan PMI belum dilakukan.", "Pemeriksaan PMI telah dilakukan.", "pending"),
    ("PMI檢驗尚未完成。", "Pemeriksaan PMI belum selesai.", "Pemeriksaan PMI sudah selesai.", "pending"),
    ("PMI檢測沒有完成。", "Pemeriksaan PMI belum selesai.", "Pemeriksaan PMI sudah selesai.", "pending"),
    ("PMI檢測已經完成。", "Pemeriksaan PMI sudah selesai.", "Pemeriksaan PMI belum selesai.", "completed"),
    ("PMI檢測完成。", "Pemeriksaan PMI sudah selesai.", "Pemeriksaan PMI belum selesai.", "completed"),
    ("PMI檢測不要做。", "Pemeriksaan PMI tidak boleh dilakukan.", "Pemeriksaan PMI wajib dilakukan.", "prohibited"),
    ("PMI不需要檢測。", "Pemeriksaan PMI tidak perlu dilakukan.", "Pemeriksaan PMI wajib dilakukan.", "optional"),
]


@pytest.mark.parametrize("source,good,bad,mode", PMI_CASES)
def test_new_pmi_wordings_keep_operation_state_in_both_directions(source, good, bad, mode):
    facts = pmi.build_facts(source, "zh")
    assert facts and facts[0]["mode"] == mode
    assert pmi.validate(facts, good, "id") == []
    assert pmi.validate(facts, bad, "id")
    assert pmi.validate(pmi.build_facts(good, "id"), source, "zh") == []


@pytest.mark.parametrize("source,good,bad,mode", PMI_CASES)
def test_pmi_state_reaches_real_provider_and_delivery_validation(source, good, bad, mode):
    app._tl.semantic_contract = app.build_translation_semantic_contract(source, "zh", "id")
    response = lambda text: SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=text), finish_reason="stop")])
    check = app._build_translation_response_validator(source, "zh", "id")
    assert check(response(good), "offline")[0]
    assert not check(response(bad), "offline")[0]
    assert app._final_delivery_guard(source, good, "zh", "id") == good
    assert app._final_delivery_guard(source, bad, "zh", "id") is None
    assert app.pick_model(source) == ai_provider.DEFAULT_OPENAI_MODEL


def test_pmi_state_is_not_borrowed_from_packaging_or_another_machine():
    assert pmi.build_facts("PMI檢測，包裝已經完成。", "zh")[0]["mode"] == "plain"
    source = "I5 PMI檢測尚未完成；I15 PMI檢測已經完成。"
    good = "I5 pemeriksaan PMI belum selesai; I15 pemeriksaan PMI sudah selesai."
    bad = "I5 pemeriksaan PMI sudah selesai; I15 pemeriksaan PMI belum selesai."
    facts = pmi.build_facts(source, "zh")
    assert not pmi.validate(facts, good, "id")
    assert pmi.validate(facts, bad, "id")
    app._tl.semantic_contract = app.build_translation_semantic_contract(source, "zh", "id")
    assert app.pick_model(source) == ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL


def test_instrument_check_cannot_replace_steel_grade_inspection():
    assert not pmi.build_facts("請檢查PMI儀器。", "zh")
    assert not pmi.build_facts("Periksa alat PMI.", "id")
    facts = pmi.build_facts("PMI一定要檢測。", "zh")
    assert pmi.validate(facts, "Alat PMI wajib diperiksa.", "id")


def test_reference_edits_take_effect_after_warm_retrieval():
    example = {"zh": "請確認 Z99 的材料標籤。", "id": "Periksa label produk material Z99."}
    rows = casebook.retrieve(example["zh"], "zh", "id", examples=[example])
    assert rows[0]["target"] == example["id"]
    example["id"] = "Mohon cek label produk untuk material Z99."
    rows = casebook.retrieve(example["zh"], "zh", "id", examples=[example])
    assert rows[0]["target"] == example["id"]
    assert rows[0]["target"] != "Periksa label produk material Z99."


def test_less_prompt_bulk_does_not_drop_actual_contract_or_source(monkeypatch):
    sent = []
    def capture(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="Sore ini, periksa lagi progres pengemasan."), finish_reason="stop")])
    monkeypatch.setattr(app.ai.chat.completions, "create", capture)
    source = "今天下午再確認包裝進度。"
    app._tl.semantic_contract = app.build_translation_semantic_contract(source, "zh", "id")
    app.translate_openai(source, "zh", "id")
    assert sent
    prompt = str(sent[0]["messages"])
    assert source in prompt
    assert "runtime semantic contract" in prompt and "Preserve @mentions" in prompt
    assert "3800" not in prompt and "BF2" not in prompt
    assert sent[0]["translation_max_generations"] == 2


@pytest.mark.parametrize("src,tgt,source,good,bad", [
    ("zh", "id", "禁止混料，必須立即停機。",
     "Dilarang mencampur material. Mesin harus segera dihentikan.",
     "Boleh mencampur material. Mesin harus segera dijalankan."),
    ("zh", "id", "不要混料，請馬上停機。",
     "Jangan sampai material tercampur. Segera hentikan mesin.",
     "Bahan boleh dicampur. Segera nyalakan mesin."),
    ("zh", "id", "必須立即停機。", "Mesin harus segera dihentikan.", "Mesin harus segera dijalankan."),
    ("zh", "id", "請立即開機。", "Segera nyalakan mesin.", "Segera hentikan mesin."),
    ("zh", "id", "不要停機。", "Jangan hentikan mesin.", "Hentikan mesin."),
    ("id", "zh", "Dilarang mencampur material. Mesin harus segera dihentikan.",
     "禁止混料，必須立即停機。", "可以混料，必須立即開機。"),
])
def test_explicit_safety_action_and_negation_reversals_reach_delivery_guard(src, tgt, source, good, bad):
    assert app._final_delivery_guard(source, good, src, tgt) == good
    assert app._final_delivery_guard(source, bad, src, tgt) is None


def test_instructions_for_different_machines_and_non_machine_stops_are_distinct():
    understanding = app.source_understanding_module
    source = "I5必須立即停機；I15請立即開機。"
    good = "Mesin I5 harus segera dihentikan; mesin I15 harus segera dinyalakan."
    bad = "Mesin I5 harus segera dinyalakan; mesin I15 harus segera dihentikan."
    analysis = understanding.analyze(source, "zh")
    assert understanding.validate_operational_states(analysis, good, "zh", "id")[0]
    assert not understanding.validate_operational_states(analysis, bad, "zh", "id")[0]
    for text in ("Hentikan pengiriman.", "Campur minuman ini."):
        assert not understanding.operational_states(text, "id")
