"""September 19 screenshots: source scope, alternatives, and learning boundaries.

All network/provider/LINE calls are blocked or mocked by the offline runner.
The long screenshot is folded: only its visible excerpt is used as evidence.
"""
import json
from pathlib import Path

import pytest

import active_learning as learning
import ai_provider
import app
import factory_knowledge as knowledge
import factory_semantic_audit as audit
import factory_workflow_semantics as workflow
import glossary_enforcement as glossary
import glossary_policy
import translation_learning_policy as policy
import translation_quality_gate as quality
from test_translation_learned_policy import database, public_pipeline
from test_translation_instruction_cost_quality import offline_transport, response


SOURCE_ID = (
    "@All PERINGATAN UNTUK OPERATOR MESIN GRINDING (CG)\n\n"
    "1. Ukuran diameter terlalu kecil dari ukuran yang diminta pelanggan, terutama pada bagian "
    "yang berada sekitar 15 cm dari ujung batang.\n"
    "2. Terdapat permukaan yang kasar atau bekas grinding yang tidak dapat dihilangkan oleh mesin Polishing.\n\n"
    "Untuk permukaan kasar yang tidak dapat dihilangkan oleh mesin Polishing, operator CG harus melakukan "
    "polishing secara manual menggunakan gerinda."
)
GOOD_ZH = (
    "@All 給研磨機（CG）操作員的提醒\n\n"
    "1. 直徑小於客戶要求的尺寸，尤其是距離棒材端部約15 cm的位置。\n"
    "2. 表面粗糙，或有拋光機無法去除的研磨痕。\n\n"
    "對於拋光機無法去除的粗糙表面，CG操作員必須使用砂輪機手動修整。"
)
BAD_ZH = GOOD_ZH.replace("拋光機", "E824拋光設備區")
HANDOFF = "資料都卡452是放不過來\n還是忘記放"
HANDOFF_BAD = "Data semuanya tertahan di 452 dan tidak bisa dipindahkan, atau lupa menaruhnya?"
PENDING = "沒有放行\n480都不驗"
PENDING_BAD = "Belum disetujui untuk dilanjutkan.\n480 semuanya tidak diperiksa."


def test_real_glossary_maps_generic_polishing_to_generic_equipment():
    rows = json.loads(Path("glossary_data.json").read_text())
    index = glossary.build_safe_reverse_index(rows)
    assert index["mesin polishing"]["target_term"] == "拋光機"
    assert "E824拋光設備區" not in {row["target_term"] for row in index.values()}
    assert "E824" not in app.inject_glossary_hint("Mesin Polishing", "id", "zh")
    assert glossary_policy.normalize_entry("E824拋光設備區", {"idn": "Mesin Polishing"})["idn"] == "area mesin polishing E824"


@pytest.mark.parametrize("code", ["E824", "BF235", "X72", "I15"])
def test_narrow_equipment_rows_cannot_be_reversed_even_if_marked_safe(code):
    rows = {code + "二號拋光設備區": {"idn": "Mesin Pemoles", "reverse_safe": True}}
    assert "mesin pemoles" not in glossary.build_safe_reverse_index(rows)
    assert any(code in x for x in glossary.build_unsafe_reverse_ui_targets(rows))


def test_full_visible_notice_repairs_only_the_leaked_label():
    fixed = quality.canonicalize_source_terms(SOURCE_ID, BAD_ZH, "id", "zh")
    assert fixed == GOOD_ZH
    assert quality.canonicalize_source_terms(SOURCE_ID, fixed, "id", "zh") == fixed
    assert "invented_identifier:e824" in quality.validate_translation(SOURCE_ID, BAD_ZH, "id", "zh").issues
    assert quality.validate_translation(SOURCE_ID, GOOD_ZH, "id", "zh").ok
    assert not app._tm_bypass_integrity_ok(SOURCE_ID, BAD_ZH, "id", "zh")[0]
    assert not learning.validate_correction(SOURCE_ID, BAD_ZH, "id", "zh")["ok"]
    assert learning.validate_correction(SOURCE_ID, GOOD_ZH, "id", "zh")["ok"]


def test_actual_equipment_code_is_preserved_and_unknown_codes_are_not_deleted():
    source = "Mesin polishing E824 tidak dapat menghilangkan permukaan kasar."
    target = "E824拋光設備區無法去除粗糙表面。"
    assert quality.canonicalize_source_terms(source, target, "id", "zh") == target
    assert not any(x.startswith("invented_identifier") for x in quality.validate_translation(source, target, "id", "zh").issues)
    unknown = "H917拋光機無法去除粗糙表面。"
    assert quality.canonicalize_source_terms("Mesin polishing tidak bisa.", unknown, "id", "zh") == unknown
    assert "invented_identifier:h917" in quality.validate_translation("Mesin polishing tidak bisa.", unknown, "id", "zh").issues


def test_glossary_pair_cannot_authorize_an_invented_id():
    report = quality.validate_translation("mesin polishing", "E824拋光設備區", "id", "zh",
                                         glossary_pairs=[("mesin polishing", "E824拋光設備區")])
    assert "invented_identifier:e824" in report.issues


@pytest.mark.parametrize("station", ["452", "453", "480"])
def test_data_handoff_is_a_question_and_uses_current_station(station):
    source = HANDOFF.replace("452", station)
    bad = HANDOFF_BAD.replace("452", station)
    assert workflow.issues(source, bad)
    fixed = quality.canonicalize_source_terms(source, bad, "zh", "id")
    assert "Apakah" in fixed and "atau lupa di-release?" in fixed
    assert "stasiun " + station in fixed and "menaruh" not in fixed
    assert not workflow.issues(source, fixed)
    assert quality.canonicalize_source_terms(source, fixed, "zh", "id") == fixed
    assert learning.validate_correction(source, fixed, "zh", "id")["ok"]


def test_uncertain_packing_word_is_not_silently_resolved_or_learned(database):
    source = "料再包裝\n" + HANDOFF
    bad = "Material dikemas ulang.\n\n" + HANDOFF_BAD
    fixed = quality.canonicalize_source_terms(source, bad, "zh", "id")
    assert fixed.startswith("Material dikemas ulang.\n\n")  # no invented 在/再 correction
    assert "Apakah" in fixed and "lupa di-release" in fixed
    assert not learning.validate_correction(source, fixed, "zh", "id")["ok"]
    outcome = learning.record_translation_outcome(source_text=source, candidate_text=bad, final_text=fixed,
        src_lang="zh", tgt_lang="id", group_id="G1", issues=workflow.issues(source, bad),
        reviewed=False, cacheable=True, path="source_bound_local_correction")
    assert outcome["learned_rules"] == 0
    assert not learning.prepare_translation(source, "zh", "id", "G1")["rules"]
    prompt = audit.build_prompt(audit.build_source_frame(source, "zh", "id"))
    assert "unresolved" in prompt.lower() or "尚未確認" in prompt


def test_inspection_station_is_not_a_quantity_and_no_cause_is_invented():
    fixed = quality.canonicalize_source_terms(PENDING, PENDING_BAD, "zh", "id")
    assert fixed == "Data belum di-release ke stasiun berikutnya.\nStasiun 480 tidak melakukan pemeriksaan."
    assert not workflow.issues(PENDING, fixed)
    assert learning.validate_correction(PENDING, fixed, "zh", "id")["ok"]
    assert not any(x in fixed for x in ("tidak bisa", "tidak mau", "karena"))


@pytest.mark.parametrize("source,target", [
    ("材料要再包裝。", "Material harus dikemas ulang."),
    ("材料放不過來，還是忘記放在架上？", "Material tidak bisa dipindahkan atau lupa ditaruh di rak?"),
    ("請把資料放在桌上。", "Tolong taruh data di atas meja."),
    ("沒有放行，480不能檢驗。", "Data belum di-release, stasiun 480 tidak bisa melakukan pemeriksaan."),
    (PENDING + "，等主管通知。", PENDING_BAD + " Tunggu pemberitahuan atasan."),
    (HANDOFF + "，超過3噸的例外。", HANDOFF_BAD + " Kecuali yang lebih dari 3 ton."),
    ("請詢問：" + HANDOFF, HANDOFF_BAD),
])
def test_different_senses_or_extra_conditions_are_not_rewritten(source, target):
    assert workflow.canonicalize(source, target) == target


@pytest.mark.parametrize("source,target", [
    ("新的提案改善連結", "Tautan usulan perbaikan terbaru 🔧"),
    ("@伊努滿 Sumertha 來找我", "@伊努滿 Sumertha datang temui saya"),
    ("@All 六點半來拍一下KYT影片。", "@All datang pukul 6.30 untuk merekam video KYT."),
    ("@All 早班班股會議，下班前風扇記得關閉", "@All Rapat bagian shift pagi. Sebelum pulang, ingat matikan kipas."),
    ("@All 系統問題，近期常有掛牌跟工單顏色不同，拋光噴漆前一定要找台灣同仁幫忙確認一下顏色是否正確，包裝站也麻煩幫忙確認一下正確的客需。",
     "@All Ada masalah pada sistem. Belakangan ini sering terjadi perbedaan warna antara TAG dan work order. "
     "Sebelum proses pengecatan semprot di stasiun polishing, mohon minta bantuan rekan Taiwan untuk memastikan warna sudah benar. "
     "Mohon bantuan stasiun packing untuk memastikan permintaan pelanggan yang benar."),
])
def test_other_screenshot_meanings_and_identities_are_preserved(source, target):
    assert quality.canonicalize_source_terms(source, target, "zh", "id") == target
    assert not workflow.issues(source, target)


def test_kyt_and_cg_are_locked_acronyms():
    for text, acronym in [("六點半拍KYT影片", "KYT"), ("MESIN GRINDING (CG)", "CG")]:
        envelope = quality.protect_immutable_spans(text)
        assert acronym in envelope.mapping.values()


def test_knowledge_is_retrieved_without_historical_answer_copying():
    assert "grinding_quality_equipment_scope" in [r["id"] for r in knowledge.retrieve(SOURCE_ID, "id", "zh", limit=3)]
    assert "station_data_handoff_question" in [r["id"] for r in knowledge.retrieve(HANDOFF, "zh", "id", limit=3)]


def test_repaired_identity_teaches_a_rule_not_the_old_machine_code(database):
    outcome = learning.record_translation_outcome(source_text=SOURCE_ID, candidate_text=BAD_ZH, final_text=GOOD_ZH,
        src_lang="id", tgt_lang="zh", group_id="G1", issues=["invented_identifier:e824"],
        reviewed=False, cacheable=True, path="source_bound_local_correction")
    assert outcome["learned_rules"] > 0
    snapshot = learning.prepare_translation(SOURCE_ID, "id", "zh", "G1")
    assert "identity" in [r["category"] for r in snapshot["rules"]]
    prompt = policy.build_prompt(snapshot)
    assert "E824" not in prompt and "identifiers" in prompt


def test_public_text_flow_repairs_pending_inspection_with_one_model_call(public_pipeline, monkeypatch):
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(kwargs)
        return response(PENDING_BAD)
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    fixed = app.translate(PENDING, "zh", "id")
    assert fixed == quality.canonicalize_source_terms(PENDING, PENDING_BAD, "zh", "id")
    assert len(calls) == 1
    assert app.cache_get(PENDING, "zh", "id") == fixed


def test_public_reverse_flow_repairs_known_equipment_leak(public_pipeline, monkeypatch):
    calls = []
    source = "Permukaan kasar tidak dapat dihilangkan oleh mesin Polishing."
    def dispatch(provider, **kwargs):
        calls.append(kwargs)
        return response("E824拋光設備區無法去除粗糙表面。")
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    assert app.translate(source, "id", "zh").rstrip("。") == "拋光機無法去除粗糙表面"
    assert len(calls) == 1
