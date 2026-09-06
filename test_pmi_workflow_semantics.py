"""PMI is a grade inspection process; keywords alone do not prove meaning."""
import pytest
import factory_translation_guard as guard
import factory_source_understanding as source_understanding


CASES = [
    ("zh", "id", "這把沒有檢驗鋼種就包裝了。",
     "Bundel ini dikemas tanpa diperiksa grade bajanya dengan PMI.",
     "Bundel ini sudah diperiksa grade bajanya dengan PMI lalu dikemas."),
    ("zh", "id", "PMI還沒做，不能包裝。",
     "PMI belum dilakukan, tidak boleh dikemas.",
     "PMI sudah dilakukan, boleh dikemas."),
    ("zh", "id", "檢驗鋼種後才能包裝。",
     "Periksa grade baja dengan PMI sebelum dikemas.",
     "Periksa grade baja dengan PMI setelah dikemas."),
    ("zh", "id", "驗剛種完再包裝。",
     "Periksa grade baja dengan PMI sebelum dikemas.",
     "Cetak grade baja lalu kemas."),
    ("id", "zh", "Grade baja setiap bundel harus diperiksa dengan PMI.",
     "每把都要做PMI鋼種檢驗。", "每把都要用PMI列印鋼種標籤。"),
    ("id", "zh", "PMI belum dilakukan, material sudah dikemas.",
     "PMI還沒做，材料已經包裝。", "PMI已經做完，材料已經包裝。"),
    ("id", "zh", "Periksa grade baja dengan PMI sebelum dikemas.",
     "先檢驗PMI鋼種再包裝。", "先包裝再檢驗PMI鋼種。"),
    ("id", "zh", "Bundel ini dikemas tanpa pemeriksaan PMI.",
     "這把未做PMI檢驗就包裝了。", "這把已做PMI檢驗才包裝。"),
]


@pytest.mark.parametrize("src,tgt,source,good,bad", CASES)
def test_process_action_status_and_sequence_are_preserved(src, tgt, source, good, bad):
    report = guard.validate_translation(source, good, src, tgt)
    assert report.ok, report.issues
    report = guard.validate_translation(source, bad, src, tgt)
    assert not report.ok, (source, bad)


def test_inspection_and_label_printing_can_both_be_present():
    source = "先打鋼種，再打印鋼種標籤。"
    good = "Periksa grade baja dengan PMI dulu, lalu mencetak grade baja pada label produk."
    assert guard.validate_translation(source, good, "zh", "id").ok
    bad = "Cetak grade baja dengan PMI dulu, lalu periksa label produk."
    assert not guard.validate_translation(source, bad, "zh", "id").ok


def test_numbered_inspections_cannot_borrow_another_items_state():
    source = "1. PMI還沒做。\n2. PMI已經做完。"
    good = "1. PMI belum dilakukan.\n2. PMI sudah dilakukan."
    bad = "1. PMI sudah dilakukan.\n2. PMI belum dilakukan."
    assert guard.validate_translation(source, good, "zh", "id").ok
    assert not guard.validate_translation(source, bad, "zh", "id").ok


def test_equipment_cannot_borrow_another_machines_inspection():
    source = "I5 PMI還沒做；I15 PMI做完了。"
    good = "I5 PMI belum dilakukan; I15 PMI sudah dilakukan."
    bad = "I5 PMI sudah dilakukan; I15 PMI belum dilakukan."
    assert guard.validate_translation(source, good, "zh", "id").ok
    assert not guard.validate_translation(source, bad, "zh", "id").ok


@pytest.mark.parametrize("source", ["請打印鋼種標籤。", "檢查鋼種標籤印得清不清楚。",
                                    "請確認材質標籤。", "PMI設備故障，請維修。", "PMI"])
def test_marking_equipment_and_bare_codes_do_not_invent_inspection(source):
    analysis = source_understanding.analyze(source, "zh")
    assert not [f for f in analysis["factory_terms"] if f["sense"] == "pmi_inspection"]


def test_pmi_meaning_is_in_actual_source_prompt():
    analysis = source_understanding.analyze("PMI blm dilakukan, jangan dikemas.", "id")
    prompt = source_understanding.build_prompt(analysis)
    assert "PMI" in prompt and "鋼種" in prompt


@pytest.mark.parametrize("src,tgt,source,good,bad", CASES)
def test_actual_final_delivery_boundary_accepts_good_and_blocks_bad(src, tgt, source, good, bad):
    import app
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    try:
        assert app._final_delivery_guard(source, good, src, tgt) == good
        assert app._final_delivery_guard(source, bad, src, tgt) is None
    finally:
        app._tl.__dict__.clear()
        app._tl.__dict__.update(previous)


def test_actual_delivery_accepts_combined_inspection_and_printing():
    import app
    source = "先打鋼種，再打印鋼種標籤。"
    good = "Periksa grade baja dengan PMI dulu, lalu mencetak grade baja pada label produk."
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    try:
        assert app._final_delivery_guard(source, good, "zh", "id") == good
    finally:
        app._tl.__dict__.clear()
        app._tl.__dict__.update(previous)


def test_built_in_examples_do_not_conflict_with_current_factory_guard():
    import app
    for ex in app.BUILTIN_EXAMPLES:
        src, tgt = ("id", "zh") if ex.get("dir") == "id2zh" else ("zh", "id")
        report = guard.validate_translation(ex[src], ex[tgt], src, tgt)
        assert report.ok, (ex, report.issues)
    pmi = next(ex for ex in app.BUILTIN_EXAMPLES if ex["zh"] == "PMI作業")
    assert "PMI" in pmi["id"]
    assert not guard.validate_translation("PMI作業", "Mencetak grade baja.", "zh", "id").ok


def test_natural_attention_wording_is_not_mistaken_for_missing_translation():
    source = "請大家注意：看到材料後端有損傷要馬上回報。"
    good = "Mohon perhatian semua: jika material bagian belakang rusak, segera lapor."
    bad = "Jika material bagian belakang rusak, segera lapor."
    assert guard.validate_translation(source, good, "zh", "id").ok
    assert not guard.validate_translation(source, bad, "zh", "id").ok
