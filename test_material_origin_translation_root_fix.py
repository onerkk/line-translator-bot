"""232923: preserve material origin, size role and affected-piece quantifier."""
import pytest

import active_learning as learning
import ai_provider
import app
import factory_source_understanding as understanding
import factory_material_relations as relations
import factory_translation_guard as guard
import translation_learning_policy as policy
import translation_quality_gate as quality
from test_translation_learned_policy import database, public_pipeline
from test_translation_instruction_cost_quality import offline_transport, response


SOURCE = "Barang dari belakang ukuran 55 mm banyak yang bengkok"
BAD = "後端 55 mm 的料件很多彎曲"
GOOD = "從後面來的 55 mm 材料，很多都有彎曲。"


def test_screenshot_bad_relation_is_rejected_at_shared_acceptance_boundaries():
    assert not quality.validate_translation(SOURCE, BAD, "id", "zh").ok
    assert not guard.validate_translation(SOURCE, BAD, "id", "zh").ok
    assert not learning.validate_correction(SOURCE, BAD, "id", "zh")["ok"]
    assert not app._tm_bypass_integrity_ok(SOURCE, BAD, "id", "zh")[0]
    assert app._delivery_validation_issues(SOURCE, BAD, "id", "zh")


def test_screenshot_correct_meaning_is_accepted_and_not_reworded():
    assert quality.validate_translation(SOURCE, GOOD, "id", "zh").ok
    assert guard.validate_translation(SOURCE, GOOD, "id", "zh").ok
    assert learning.validate_correction(SOURCE, GOOD, "id", "zh")["ok"]
    assert quality.canonicalize_source_terms(SOURCE, GOOD, "id", "zh") == GOOD


def test_local_repair_uses_source_roles_and_is_idempotent():
    fixed = quality.canonicalize_source_terms(SOURCE, BAD, "id", "zh")
    assert fixed == GOOD
    assert quality.canonicalize_source_terms(SOURCE, fixed, "id", "zh") == fixed
    assert not app._delivery_validation_issues(SOURCE, fixed, "id", "zh")


def test_source_prompt_explains_origin_without_assigning_an_unstated_station():
    contract = app.build_translation_semantic_contract(SOURCE, "id", "zh")
    prompt = app.build_translation_semantic_contract_prompt(contract)
    assert "material_spatial_relation" in prompt
    assert "origin" in prompt and "55" in prompt
    assert "many_pieces" in prompt
    hint = app.build_factory_context_hint(SOURCE, "id", "zh")
    assert "dari belakang=後端" not in hint
    assert "belakang=後端" not in hint
    assert "不要翻從後面" not in hint
    assert app.post_fix_factory_id_to_zh(SOURCE, GOOD) == GOOD.rstrip("。")
    assert app.validate_factory_translation(SOURCE, GOOD, "id", "zh")[0]
    assert not app.validate_factory_translation(SOURCE, BAD, "id", "zh")[0]


def test_public_flow_repairs_the_reported_output_with_one_provider_call(public_pipeline, monkeypatch):
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(kwargs)
        return response(BAD)
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    assert app.translate(SOURCE, "id", "zh") == GOOD.rstrip("。")
    assert len(calls) == 1
    assert app.cache_get(SOURCE, "id", "zh") == GOOD.rstrip("。")
    assert "material_spatial_relation" in str(calls[0]["messages"])


def test_learning_teaches_relation_not_a_historical_value_or_station(database):
    outcome = learning.record_translation_outcome(
        source_text=SOURCE, candidate_text=BAD, final_text=GOOD,
        src_lang="id", tgt_lang="zh", group_id="G1",
        issues=["factory_material_relation:origin_missing"],
        reviewed=False, cacheable=True, path="source_bound_local_correction",
    )
    assert outcome["learned_rules"] > 0
    snapshot = learning.prepare_translation(SOURCE.replace("55", "62"), "id", "zh", "G1")
    assert "material_relation" in [rule["category"] for rule in snapshot["rules"]]
    prompt = policy.build_prompt(snapshot)
    assert "55" not in prompt and BAD not in prompt and GOOD not in prompt


@pytest.mark.parametrize("source,expected", [
    (SOURCE.replace("55", "62.5"), GOOD.replace("55", "62.5")),
    ("Material ukuran 62,5 mm dari depan banyak yg bengkok.",
     "從前面來的 62,5 mm 材料，很多都有彎曲。"),
    ("Bahan yang datang dari samping ukuran 6 cm banyak yang bengkok.",
     "從旁邊來的 6 cm 材料，很多都有彎曲。"),
    (SOURCE.upper(), GOOD),
    (SOURCE.replace("dari", "di"), GOOD.replace("從後面來的", "在後面的")),
    (SOURCE.replace("ukuran", "diameter"), GOOD.replace("55 mm", "直徑 55 mm")),
    (SOURCE.replace("ukuran", "panjang"), GOOD.replace("55 mm", "長度 55 mm")),
    (SOURCE.replace("ukuran", "\nukuran"), GOOD),
    ("@All " + SOURCE, "@All " + GOOD),
    ("__MENTION_0__ " + SOURCE, "__MENTION_0__ " + GOOD),
])
def test_local_repair_uses_current_direction_dimension_and_mention(source, expected):
    fixed = quality.canonicalize_source_terms(source, BAD, "id", "zh")
    assert fixed == expected
    report = quality.validate_translation(source, fixed, "id", "zh")
    assert report.ok, report.issues


@pytest.mark.parametrize("target", [
    GOOD,
    "來自後方的 55 mm 料件，有不少已經彎曲。",
    "後方送來的 55 mm 材料，彎曲的很多。",
    "從後邊來的尺寸為 55 mm 的材料，很多都有彎曲。",
])
def test_natural_origin_paraphrases_are_not_forced_into_one_sentence(target):
    assert quality.validate_translation(SOURCE, target, "id", "zh").ok
    assert quality.canonicalize_source_terms(SOURCE, target, "id", "zh") == target
    assert app.finalize_factory_translation(SOURCE, target, "id", "zh") == target.rstrip("。")


@pytest.mark.parametrize("target,issue", [
    (BAD, "origin_missing"),
    ("在後面的 55 mm 材料，很多都有彎曲。", "origin_missing"),
    ("55 mm 材料的後端，很多都有彎曲。", "origin_missing"),
    (GOOD.replace("後面", "前面"), "origin_missing"),
    ("從後面來的 55 mm 材料，彎得很嚴重。", "many_pieces_as_degree_or_missing"),
    ("從後面來的 55 mm 材料彎曲很多。", "many_pieces_as_degree_or_missing"),
    ("從後面來的 55 mm 材料，很多沒有彎曲。", "many_pieces_as_degree_or_missing"),
    ("從後面來的材料，很多都在尾端 55 mm 處彎曲。", "size_as_end_distance"),
    ("從後面來的 55 mm 材料，很多都有彎曲，都是上游送來的。", "unstated_process_inferred"),
    (GOOD + "這些都是 480 站送來的。", "unstated_process_inferred"),
    (GOOD + "來自削皮站。", "unstated_process_inferred"),
    (GOOD.replace("55 mm", "直徑 55 mm"), "dimension_type_invented"),
    (GOOD.replace("55", "56"), "size_attachment_mismatch"),
])
def test_relations_quantity_and_dimensional_attachment_are_checked(target, issue):
    report = quality.validate_translation(SOURCE, target, "id", "zh")
    assert "factory_material_relation:" + issue in report.issues


@pytest.mark.parametrize("source,target", [
    (SOURCE + ". Tolong periksa dahulu.", GOOD + "請先檢查。"),
    (SOURCE + ", kecuali barang untuk ABC.", GOOD + "ABC 的材料除外。"),
    (SOURCE.replace("banyak", "tidak banyak"), "從後面來的 55 mm 材料，彎曲的不多。"),
    (SOURCE + "?", "從後面來的 55 mm 材料，很多都有彎曲嗎？"),
    ("Jika " + SOURCE + ", laporkan.", "若從後面來的 55 mm 材料有很多彎曲，請回報。"),
    (SOURCE + " di ujungnya", "從後面來的 55 mm 材料，很多尾端都有彎曲。"),
])
def test_partial_condition_or_unknown_clause_is_never_rebuilt_as_complete(source, target):
    # Even a bad candidate cannot authorize dropping an extra source clause.
    assert quality.canonicalize_source_terms(source, BAD, "id", "zh") == BAD
    assert quality.canonicalize_source_terms(source, target, "id", "zh") == target


@pytest.mark.parametrize("source,target,wrong", [
    (SOURCE.replace("dari", "di"), GOOD.replace("從後面來的", "在後面的"), GOOD),
    ("Bagian belakang barang ukuran 55 mm bengkok.", "55 mm 材料的後端彎曲。", GOOD),
    ("Ujung depan batang ukuran 60 mm bengkok.", "60 mm 棒材的前端彎曲。", GOOD.replace("55", "60")),
])
def test_current_location_and_material_ends_do_not_become_origin(source, target, wrong):
    report = quality.validate_translation(source, target, "id", "zh")
    assert report.ok, report.issues
    assert not quality.validate_translation(source, wrong, "id", "zh").ok
    assert app.finalize_factory_translation(source, target, "id", "zh") == target.rstrip("。")


def test_short_origin_report_cannot_enter_the_legacy_end_slot_translation():
    assert app.factory_semantic_translate_id_zh("Barang dari belakang bengkok") is None
    assert app.factory_semantic_translate_id_zh("Barang di belakang bengkok") is None
    assert app.factory_semantic_translate_id_zh("batang rusak dari belakang") == "棒材後端損傷"


def test_multiple_reports_cannot_borrow_another_materials_origin():
    source = SOURCE + "; " + SOURCE.replace("55", "62").replace("belakang", "depan")
    good = GOOD + GOOD.replace("55", "62").replace("後面", "前面")
    swapped = GOOD.replace("後面", "前面") + GOOD.replace("55", "62")
    assert quality.validate_translation(source, good, "id", "zh").ok
    assert "factory_material_relation:origin_missing" in quality.validate_translation(source, swapped, "id", "zh").issues
    assert quality.canonicalize_source_terms(source, swapped, "id", "zh") == swapped


@pytest.mark.parametrize("source", [
    "Belakangan ini ukuran 55 mm banyak yang bengkok.",
    "Saya datang dari belakang, barang ukuran 55 mm banyak yang bengkok.",
    'Label bertuliskan "Barang dari belakang ukuran 55 mm banyak yang bengkok".',
    "@Barang DariBelakang Halo.",
    "https://example.test/barang/dari/belakang",
])
def test_time_people_names_and_literal_labels_do_not_invent_material_origin(source):
    assert relations.build_facts(source, "id") == []
    assert quality.canonicalize_source_terms(source, BAD, "id", "zh") == BAD


def test_locale_aliases_and_reference_changes_preserve_relation_type():
    assert quality.canonicalize_source_terms(SOURCE, BAD, "id-ID", "zh-TW") == GOOD
    assert not quality.validate_translation(SOURCE, BAD, "id-ID", "zh-TW").ok
    changes = understanding.reference_fact_changes(SOURCE.replace("dari", "di"), SOURCE, "id")
    assert "material_relations" in changes
