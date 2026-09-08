"""User screenshots, natural alternatives and changed facts; AI is a fixture."""
import pytest
import app
import ai_provider
import factory_sequence_semantics as semantics
import factory_instruction_semantics as instructions
import factory_semantic_audit as audit
from test_translation_instruction_cost_quality import offline_transport, response

SOURCE = "@All\n7J821007\n7J821008\n7J821009\n先不要生產，等研發單位量過圓度在生產"
PREFIX = "@All\n7J821007\n7J821008\n7J821009\n"
GOOD = "Jangan produksi dulu. Tunggu bagian R&D mengukur kebulatan terlebih dahulu, baru produksi."
REPORT = "@福迪 昨天拋光手寫報表放在哪裡"
REPORT_GOOD = "@福迪 Laporan tulis tangan proses polishing kemarin diletakkan di mana?"


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    monkeypatch.setattr(app, "translation_cache", {})
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(previous)


@pytest.mark.parametrize("target", [
    GOOD,
    "Jangan mulai produksi dulu. Tunggu tim litbang selesai mengukur kebulatan, kemudian mulai produksi.",
    "Produksi baru boleh dimulai setelah bagian R&D selesai mengukur roundness.",
    "Setelah bagian penelitian dan pengembangan mengukur kebulatan, baru mulai produksi.",
    "Jangan produksi sebelum kebulatan diukur oleh bagian R&D.",
    "Tunggu bagian R&D mengukur kebulatan. Setelah itu baru produksi.",
])
def test_natural_roundness_sequences_pass_without_rewriting(target):
    assert instructions.validate_relations(instructions.build_relations(SOURCE), PREFIX + target) == []
    assert app._final_delivery_guard(SOURCE, PREFIX + target, "zh", "id") == PREFIX + target


@pytest.mark.parametrize("target", [
    GOOD.replace("kebulatan", "kekasaran permukaan"),
    GOOD.replace("kebulatan", "diameter"),
    GOOD.replace("R&D", "QC"),
    "Produksi dulu, kemudian bagian R&D mengukur kebulatan.",
    "Jangan produksi dulu. Bagian R&D tidak perlu mengukur kebulatan, baru produksi.",
    "Jangan produksi dulu. Tunggu bagian R&D mengukur kebulatan dan menyatakan lulus, baru produksi.",
    "Jangan produksi dulu. Tunggu bagian R&D mengukur kebulatan, tidak perlu produksi lagi.",
    "Tunggu bagian R&D mengukur kebulatan, baru jangan produksi.",
    "Tunggu bagian R&D mengukur kebulatan, baru tidak perlu produksi.",
])
def test_wrong_metric_department_order_or_invented_approval_is_rejected(target):
    app._tl.semantic_contract = app.build_translation_semantic_contract(SOURCE, "zh", "id")
    assert not app._build_translation_response_validator(SOURCE, "zh", "id")(response(PREFIX + target), "test")[0]
    assert app._final_delivery_guard(SOURCE, PREFIX + target, "zh", "id") is None


@pytest.mark.parametrize("source,metric,department", [
    ("等研發量完直徑後再生產。", "diameter", "R&D"),
    ("先不要生產，等品管量完長度才生產。", "panjang", "QC"),
    ("等研发单位测量圆度后再生产。", "kebulatan", "R&D"),
])
def test_structural_parser_handles_other_metrics_departments_and_wording(source, metric, department):
    rows = instructions.build_relations(source)
    assert rows
    assert instructions.validate_relations(rows, f"Tunggu bagian {department} mengukur {metric}, baru produksi.") == []


@pytest.mark.parametrize("source", [
    "不用等研發量過圓度再生產。", "要不要等研發量過圓度再生產？",
    "先生產再等研發量過圓度。", "研發已量完圓度。", "圓度不合格不要生產。",
])
def test_other_instructions_do_not_acquire_a_wait_then_produce_relation(source):
    assert not semantics.build_relations(source)


@pytest.mark.parametrize("target", [
    REPORT_GOOD,
    "@福迪 Di mana laporan pemolesan kemarin yang ditulis tangan disimpan?",
    "@福迪 Laporan polishing tulisan tangan kemarin ada di mana?",
])
def test_report_question_accepts_natural_wording(target):
    assert app._final_delivery_guard(REPORT, target, "zh", "id") == target


@pytest.mark.parametrize("target", [
    REPORT_GOOD.replace("kemarin", "hari ini"),
    REPORT_GOOD.replace("kemarin", "besok"),
    REPORT_GOOD.replace("tulis tangan", "digital"),
    REPORT_GOOD.replace("polishing", "grinding"),
    REPORT_GOOD.replace("diletakkan di mana?", "sudah diletakkan di meja."),
])
def test_report_date_process_medium_and_question_cannot_be_changed(target):
    app._tl.semantic_contract = app.build_translation_semantic_contract(REPORT, "zh", "id")
    assert not app._build_translation_response_validator(REPORT, "zh", "id")(response(target), "test")[0]
    assert app._final_delivery_guard(REPORT, target, "zh", "id") is None


def test_new_relation_is_in_generation_prompt_not_only_posthoc_checks():
    prompt = audit.build_prompt(audit.build_source_frame(SOURCE, "zh", "id"))
    assert "kebulatan" in prompt and "R&D" in prompt


def test_prerequisite_does_not_waive_a_separate_prohibition():
    source = SOURCE + "。不要移動材料。"
    frame = audit.build_source_frame(source, "zh", "id")
    assert frame["flags"]["prohibition"]
    assert not audit.validate_translation(frame, PREFIX +
        "Produksi baru boleh dimulai setelah bagian R&D selesai mengukur kebulatan. Pindahkan material.")[0]


@pytest.mark.parametrize("source,target", [(SOURCE, PREFIX + GOOD), (REPORT, REPORT_GOOD)])
def test_screenshot_translation_preserves_identifiers_and_needs_one_generation(offline_transport, monkeypatch, source, target):
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(provider)
        result = target
        for token, literal in app.protect_mentions(source)[1].items():
            result = result.replace(literal, token)
        return response(result)
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    result = app.translate(source, "zh", "id")
    assert result == target and calls == ["anthropic"]
