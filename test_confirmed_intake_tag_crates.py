"""User-confirmed referents, first-pass learning and bounded generation.

External transports are fake; these checks do not assert live model accuracy.
"""
import json

import pytest

import active_learning as learning
import ai_provider
import app
import conversation_context as context
import factory_knowledge as knowledge
import factory_input_semantics as inputs
import factory_workflow_semantics as workflow
import translation_learning_policy as policy
import translation_quality_gate as quality
from test_month_end_notice_delivery import SOURCE
from test_screenshot_workflow_learning import FIELD_SOURCE, FIELD_BAD, CRATE_SOURCE, CRATE_BAD
from test_translation_instruction_cost_quality import offline_transport, response
from test_translation_learned_policy import database, public_pipeline


NOTICE_BAD = (
    "Shift tengah bantu input 19 ton, target hari ini tembus 130 ton.\n\n"
    "Target pemasukan gudang bulan ini dinaikkan menjadi 3600 ton. "
    "Mulai besok sampai akhir bulan, rata-rata 143 ton per hari. "
    "Sampai akhir bulan, tenaga kerja akan diprioritaskan untuk membuka tiga stasiun. "
    "Harap atur waktu masuk gudang lebih merata."
)


def test_first_print_refers_to_tag_not_an_empty_system_field():
    frame = workflow.build_relations(FIELD_SOURCE)
    assert "printed_storage" in {r["kind"] for r in frame}
    assert "storage_field" not in {r["kind"] for r in frame}
    for bad in [FIELD_BAD, "Saat data disimpan maupun dicatat masuk gudang, kolom lokasi penyimpanan kosong."]:
        corrected = quality.canonicalize_source_terms(FIELD_SOURCE, bad, "zh", "id")
        assert "TAG yang pertama kali dicetak" in corrected
        assert "kolom" not in corrected and "dicetak ulang" not in corrected
        assert learning.validate_correction(FIELD_SOURCE, corrected, "zh", "id")["ok"]


@pytest.mark.parametrize("source,target", [
    ("資料的儲區欄位空白。", "Kolom lokasi penyimpanan pada data kosong."),
    ("TAG第一次列印沒有條碼。", "Kode batang tidak tercetak pada TAG pertama."),
    ("倉庫沒有空間。", "Gudang tidak memiliki area penyimpanan."),
    ("存檔入庫都沒儲區，但不要補印。", "Saat menyimpan data, informasi lokasi penyimpanan tidak muncul, tetapi jangan cetak ulang."),
    ("待組裝木箱", "Peti kayu yang menunggu dipasang."),
    ("原料待裝木箱。", "Bahan baku menunggu dikemas ke dalam peti kayu."),
])
def test_other_objects_and_longer_conditions_are_not_overwritten(source, target):
    assert workflow.canonicalize(source, target) == target


@pytest.mark.parametrize("source", ["TAG第一次列印沒有儲區，補印才出現。", "存檔入庫都沒儲區，要補印才有。"])
def test_confirmed_print_sequence_has_validated_exact_knowledge(source):
    assert "tag_first_print_missing_storage" in [c["id"] for c in knowledge.retrieve(source, "zh", "id", limit=3)]
    target = app.factory_translation_guard_module.exact_verified_target(source, "zh", "id")
    assert "pertama" in target and "baru muncul setelah TAG dicetak ulang" in target
    assert learning.validate_correction(source, target, "zh", "id")["ok"]


def test_goods_weight_and_system_recording_time_remain_separate():
    target = quality.canonicalize_source_terms(SOURCE, NOTICE_BAD, "zh", "id")
    assert "catat pemasukan gudang sebanyak 19 ton dalam sistem" in target
    assert "mengoperasikan tiga stasiun packing" in target
    assert "waktu pencatatan masuk gudang lebih merata" in target
    assert all(x in target for x in ["130 ton", "3600 ton", "143 ton per hari", "\n\n"])
    assert "801" not in target and "490" not in target
    assert not app._delivery_validation_issues(SOURCE, target, "zh", "id")
    assert quality.canonicalize_source_terms(SOURCE, target, "zh", "id") == target


@pytest.mark.parametrize("phrase", ["input 19 ton", "masukkan 19 ton ke gudang", "masukkan ke gudang 19 ton", "proses pemasukan 19 ton ke gudang"])
def test_intake_clarification_binds_only_the_stated_weight(phrase):
    target = workflow.canonicalize(SOURCE, "Tolong bantu " + phrase + ", target 130 ton.")
    assert "19 ton" in target and "130 ton" in target and "dalam sistem" in target
    assert "801" not in target


@pytest.mark.parametrize("source,phrase", [
    (SOURCE, "catat 19 ton sebagai pemasukan gudang dalam sistem"),
    (SOURCE, "input 19 ton ke sistem"),
    (SOURCE, "input 190 ton"),
    ("中班幫忙搬運19噸成品入庫。", "masukkan 19 ton ke gudang"),
    ("中班幫忙入19噸，入庫目標130噸，貨車卸貨時間分散一點。", "masukkan 19 ton ke gudang"),
])
def test_intake_does_not_change_other_quantities_or_explicit_physical_handling(source, phrase):
    assert workflow.canonicalize(source, phrase) == phrase


def test_801_is_the_recorded_intake_condition_and_not_the_goods_destination():
    source = "包裝成品資料進入801就算入庫，入庫時間要分散。"
    cards = knowledge.retrieve(source, "zh", "id", limit=3)
    prompt = knowledge.build_prompt(cards)
    assert "資料進入801就算入庫" in prompt
    target = app.factory_translation_guard_module.exact_verified_target(source, "zh", "id")
    assert "datanya masuk ke stasiun 801" in target
    assert "waktu pencatatan masuk gudang agar tersebar" in target
    checked = learning.validate_correction(source, target, "zh", "id")
    assert checked["ok"], checked


@pytest.mark.parametrize("target", [
    "Datanya sudah tercatat masuk gudang.",
    "Jumlahnya sudah dicatat dalam sistem.",
    "Beratnya tercatat sebagai pemasukan gudang.",
    "Catat nilai tersebut dalam sistem.",
])
def test_possessive_and_recorded_forms_are_valid_inventory_language(target):
    assert inputs.inventory_entry(target, "id")


@pytest.mark.parametrize("target", ["Masukkan barangnya ke gudang.", "Angkat 19 ton produk jadi ke area EC51."])
def test_physical_material_motion_is_not_proof_of_a_system_record(target):
    assert not inputs.inventory_entry(target, "id")


def test_legacy_timing_example_no_longer_invents_station_numbers():
    source = "入庫時間再平均一點，太早移完開會很難解釋"
    target = app.factory_translation_guard_module.exact_verified_target(source, "zh", "id")
    assert "490" not in target and "801" not in target
    assert "pencatatan masuk gudang" in target and "rapat" in target
    assert learning.validate_correction(source, target, "zh", "id")["ok"]


def test_pending_crates_mean_finished_products_while_both_staffing_branches_survive():
    target = quality.canonicalize_source_terms(CRATE_SOURCE, CRATE_BAD, "zh", "id")
    assert "produk jadi yang masih menunggu dikemas ke dalam peti kayu" in target
    assert "Jika ada" in target and "Jika tidak ada" in target
    assert "menunggu dipasang" not in target


@pytest.mark.parametrize("source", ["開3站", "包裝人力會開三個包裝站。", "月底前人力優先開三站。"])
def test_three_station_local_repair_names_packing_without_inventing_the_full_list(source):
    result = workflow.canonicalize(source, "Prioritaskan untuk mengoperasikan tiga stasiun.")
    assert result == "Prioritaskan untuk mengoperasikan tiga stasiun packing."
    assert workflow.canonicalize(source, result) == result


@pytest.mark.parametrize("source", ["研磨站今天開三站。", "人力優先開兩站。", "捷運新開三站。", "請到第3站。"])
def test_other_stations_do_not_inherit_packing(source):
    target = "Mengoperasikan tiga stasiun."
    assert workflow.canonicalize(source, target) == target


def test_passive_station_plan_keeps_its_time_and_purpose():
    source = "月底前應該都是開三站追量。"
    target = "Hingga akhir bulan, kemungkinan tiga stasiun akan terus dioperasikan untuk mengejar target."
    corrected = workflow.canonicalize(source, target)
    assert corrected == "Hingga akhir bulan, kemungkinan tiga stasiun packing akan terus dioperasikan untuk mengejar target."


def test_explicit_packing_station_period_is_not_misread_as_a_deadline():
    source = "月底前人力優先開3個包裝站。"
    target = "Sampai akhir bulan, tenaga kerja diprioritaskan untuk mengoperasikan 3 stasiun packing."
    assert learning.validate_correction(source, target, "zh", "id")["ok"]


def test_three_complete_station_names_are_translated_without_generating(public_pipeline, monkeypatch):
    monkeypatch.setattr(ai_provider, "_dispatch_provider", lambda *_a, **_k: pytest.fail("verified station names need no generation"))
    target = app.translate("圓型包裝站、異型包裝站、削皮包裝站。", "zh", "id")
    for name in ("Stasiun packing batang bulat", "Stasiun packing barang bentuk khusus", "Stasiun packing peeling"):
        assert name in target
    assert "801" not in target and "490" not in target


@pytest.mark.parametrize("source,bad,category", [
    (FIELD_SOURCE, FIELD_BAD, "printed_storage"),
    (CRATE_SOURCE, CRATE_BAD, "packing"),
    (SOURCE, NOTICE_BAD, "intake_record"),
    ("月底前人力優先開三站。", "Sampai akhir bulan, tenaga kerja diprioritaskan untuk mengoperasikan tiga stasiun.", "operation"),
])
def test_confirmed_repairs_teach_without_a_second_model_call(database, source, bad, category):
    good = quality.canonicalize_source_terms(source, bad, "zh", "id")
    result = learning.record_translation_outcome(source_text=source, candidate_text=bad, final_text=good,
        src_lang="zh", tgt_lang="id", group_id="G1", reviewed=False, cacheable=True,
        issues=workflow.issues(source, bad), path="source_bound_local_correction")
    assert result["learned_rules"]
    snapshot = learning.prepare_translation(source, "zh", "id", "G1")
    assert category in {r["category"] for r in snapshot["rules"]}
    assert policy.build_prompt(snapshot)
    assert not learning.prepare_translation(source, "zh", "id", "unrelated")["rules"]
    assert learning.list_corrections(status="approved") == []


def test_actual_notice_pipeline_corrects_and_learns_in_one_generation(public_pipeline, monkeypatch):
    calls = []
    monkeypatch.setattr(ai_provider, "_dispatch_provider", lambda provider, **kw: calls.append(kw) or response(NOTICE_BAD))
    result = app.translate(SOURCE, "zh", "id")
    assert "catat pemasukan gudang sebanyak 19 ton dalam sistem" in result
    assert "mengoperasikan tiga stasiun packing" in result
    assert "waktu pencatatan masuk gudang lebih merata" in result
    assert len(calls) == 1
    assert learning.prepare_translation(SOURCE, "zh", "id", "G1")["rules"]
    actual_prompt = "\n".join(str(m["content"]) for m in calls[0]["messages"])
    assert "資料進入801就算入庫" in actual_prompt
    assert "tiga stasiun packing" in actual_prompt
    assert app.translate(SOURCE, "zh", "id") == result
    assert len(calls) == 1


@pytest.mark.parametrize("prior,group,expected", [
    (FIELD_SOURCE, "G1", "Informasi lokasi penyimpanan pada TAG baru muncul setelah dicetak ulang."),
    ("TAG第一次列印沒有條碼", "G1", "Kode batang baru muncul setelah dicetak ulang."),
    (FIELD_SOURCE, "other", "Harus dicetak ulang dulu, baru muncul."),
])
def test_reprint_first_prompt_uses_only_its_original_context(public_pipeline, tmp_path, monkeypatch, prior, group, expected):
    journal = context.SourceJournal(tmp_path / "source-context.db")
    monkeypatch.setattr(app, "_conversation_journal", lambda: journal)
    monkeypatch.setattr(app, "get_conv_context_enabled", lambda _group: True)
    journal.capture(group, "prior", prior, author="A")
    snap = journal.capture("G1", "current", "要補印才有", author="A")
    app._tl.conversation_snapshot = snap
    calls = []
    monkeypatch.setattr(ai_provider, "_dispatch_provider", lambda provider, **kw: calls.append(kw) or response(expected))
    with context.scope(snap):
        assert app.translate("要補印才有", "zh", "id") == expected
    # Isolated shorthand can reuse its verified, object-free knowledge example.
    assert len(calls) <= 1
    if group == "G1":
        assert len(calls) == 1
        prompt = "\n".join(str(m["content"]) for m in calls[0]["messages"])
        assert prior in prompt and "<conversation_context>" in prompt
    else:
        assert not json.loads(context.prompt_data(snap))["original_turns"]
