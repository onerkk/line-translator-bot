"""Observed screenshot defects, adversarial controls and actual entry paths.

No live provider/LINE requests. Model outputs are deliberate fixtures, not
evidence of provider accuracy; local repairs and admission are exercised.
"""
import json
import threading
from types import SimpleNamespace

import pytest
import factory_chat_operation_semantics as operations
import factory_translation_guard as guard
import prompt_optimizer as prompts
import translation_alignment as alignment
import translation_extras as extras
import translation_learning_policy as policy
import translation_quality_gate as quality
import active_learning as learning

SOURCE = "如果工單是200系的，上面寫N"
BAD = "Jika work order adalah seri 200, di atasnya tertulis N."
GOOD = "Jika work order adalah seri 200, tulis N di atasnya."
UNPACK_SOURCE = "包裝要注意一下訂單超產的問題，超過太多該拆就要拆了。"
UNPACK_BAD = ("Bagian packing perlu memperhatikan masalah produksi melebihi pesanan. "
              "Jika kelebihannya terlalu banyak, harus dipisahkan menjadi beberapa kemasan.")
UNPACK_GOOD = UNPACK_BAD.replace("dipisahkan menjadi beberapa kemasan", "dibongkar kembali")
DISCIPLINE_SOURCE = (
    "福迪已經被公司解聘，不要忘記來這邊工作的目的，什麼該做、什麼不該做要有界限。\n"
    "近期很多管理層都在關注部門這邊的狀況，自己要會察言觀色。\n"
    "類似上班帶耳機或是主管走到旁邊還在滑手機，晨會處長很在意他宣導過的工作紀律一直沒改善，"
    "他已經比前任處長好相處，不要讓他覺得只能用懲處來管理。")
DISCIPLINE_TARGET = (
    "福迪 sudah diberhentikan oleh perusahaan. Jangan lupa tujuan kalian datang bekerja di sini. "
    "Kalian harus tahu batas antara hal yang boleh dan tidak boleh dilakukan.\n"
    "Belakangan ini, banyak pihak manajemen sedang memperhatikan kondisi departemen ini. "
    "Kalian harus bisa membaca situasi.\n"
    "Contohnya, memakai earphone saat bekerja atau tetap bermain ponsel saat atasan sudah berada di sebelah kalian. "
    "Dalam briefing pagi, kepala divisi sangat memperhatikan bahwa disiplin kerja yang sudah disosialisasikan masih belum membaik. "
    "Dia sudah lebih mudah diajak bekerja sama dibandingkan kepala divisi sebelumnya. "
    "Jangan sampai dia merasa harus menggunakan hukuman untuk mengelola kalian.")


@pytest.mark.parametrize("source,bad,good", [(SOURCE, BAD, GOOD), (UNPACK_SOURCE, UNPACK_BAD, UNPACK_GOOD)])
def test_observed_semantic_defects_are_rejected_and_locally_repaired(source, bad, good):
    assert not quality.validate_translation(source, bad, "zh", "id").ok
    assert not guard.validate_translation(source, bad, "zh", "id").ok
    assert quality.canonicalize_source_terms(source, bad, "zh", "id") == good
    assert quality.validate_translation(source, good, "zh", "id").ok
    assert guard.validate_translation(source, good, "zh", "id").ok


@pytest.mark.parametrize("predicate", ["tertulis", "sudah tertulis", "telah tertera", "sudah ditulis"])
def test_completed_annotation_wording_does_not_survive_as_a_false_command(predicate):
    bad = "Jika work order adalah seri 200, di atasnya " + predicate + " N."
    assert not quality.validate_translation(SOURCE, bad, "zh", "id").ok
    assert quality.canonicalize_source_terms(SOURCE, bad, "zh", "id") == GOOD


def test_uncertain_or_negative_annotation_state_is_not_blindly_rewritten():
    for predicate in ["belum tertulis", "sedang ditulis", "belum tulis"]:
        bad = "Jika work order adalah seri 200, di atasnya " + predicate + " N."
        assert not quality.validate_translation(SOURCE, bad, "zh", "id").ok
        assert operations.canonicalize(SOURCE, bad, "zh", "id") == bad


@pytest.mark.parametrize("series,label", [("200", "N"), ("300", "D"), ("400", "Z7"), ("600", "AB2")])
def test_annotation_relation_generalizes_to_other_families_and_markers(series, label):
    source = f"如果工單是{series}系的，上面寫{label}"
    bad = f"Jika work order adalah seri {series}, di atasnya tertulis {label}."
    fixed = quality.canonicalize_source_terms(source, bad, "zh", "id")
    assert fixed == f"Jika work order adalah seri {series}, tulis {label} di atasnya."
    assert quality.validate_translation(source, fixed, "zh", "id").ok
    for wrong in [fixed.replace("Jika ", ""), fixed.replace("seri " + series, "seri 999"),
                  fixed.replace("tulis", "jangan tulis"), fixed.replace("work order", "material")]:
        assert not quality.validate_translation(source, wrong, "zh", "id").ok


@pytest.mark.parametrize("source,target", [
    ("209 幫忙寫N", "209, tolong bantu tulis N."),
    ("請在工單上填寫D", "Tolong tulis D pada work order."),
    ("上面要寫N", "Tulis N di atasnya."),
    ("寫209N嗎", "Tulis 209N?"),
    ("不要寫N", "Jangan tulis N."),
    ("工單已寫N", "N sudah ditulis pada work order."),
    ("不確定是否要寫N", "Belum pasti apakah perlu menulis N."),
])
def test_real_instructions_questions_prohibitions_and_statuses_stay_distinct(source, target):
    assert quality.validate_translation(source, target, "zh", "id").ok
    assert quality.canonicalize_source_terms(source, target, "zh", "id") == target


def test_description_and_uncertainty_are_never_rewritten_to_a_command():
    for source in ["工單已寫N", "不確定是否要寫N", "請確認工單已寫N", "如果工單已寫N，請檢查"]:
        assert operations.canonicalize(source, "Pada work order sudah ditulis N.", "zh", "id") == "Pada work order sudah ditulis N."


def test_reverse_annotation_retains_instruction_and_family():
    source = "Jika grade baja pada work order adalah seri 200, tulis N di atasnya."
    rows = operations.relations(source, "id", "zh")
    assert not operations.validate(rows, SOURCE, "id", "zh")
    assert operations.validate(rows, "如果工單是200系的，上面已寫著N。", "id", "zh")


@pytest.mark.parametrize("label", ["209/N", "209N", "174/H", "A1/B2"])
def test_approval_preserves_notation_and_does_not_become_a_question(label):
    source, good = label + "也可以", label + " juga boleh."
    bad = "Apakah " + label + " juga boleh?"
    assert not quality.validate_translation(source, bad, "zh", "id").ok
    assert quality.canonicalize_source_terms(source, bad, "zh", "id") == good
    for intensity in ["subtle", "natural", "lively"]:
        decorated = extras.enrich_translation_with_tone_emoji(source, good, source_language="zh", intensity=intensity)
        assert not any(symbol in decorated for symbol in ["?", "❓", "❔", "🤔"])
    assert quality.validate_translation(source, good, "zh", "id").ok
    if "/" in label:
        assert not quality.validate_translation(source, good.replace("/", ""), "zh", "id").ok


@pytest.mark.parametrize("text", ["209/N juga boleh.", "Perlu memperhatikan packing.",
    "Jangan menggunakan ponsel.", "Tidak boleh mengangkat lebih dari 4.5 ton.",
    "Silakan lihat https://example.com/?a=1.", "主管詢問為什麼沒有改善", "標籤是「N?」。"])
def test_permission_necessity_reported_questions_and_url_marks_are_not_questions(text):
    assert not operations.is_direct_question(text)
    assert extras._analysis_for_question(text, False) is None


@pytest.mark.parametrize("text", ["200系嗎", "寫209N嗎", "209/N也可以嗎", "可以請他先放一把嗎",
    "Apakah ini seri 200?", "Bisa minta dia release satu bundel terlebih dahulu?", "Boleh saya tulis N"])
def test_genuine_questions_keep_question_syntax(text):
    assert operations.is_direct_question(text)
    assert extras._analysis_for_question(text, False).primary == "question"


def test_target_question_cue_cannot_invent_a_question_emoji():
    text = extras.enrich_translation_with_tone_emoji("209/N也可以", "209/N juga boleh?", source_language="zh", intensity="lively")
    assert not any(symbol in text for symbol in ["❓", "❔", "🤔"])


def test_question_and_statement_units_cannot_borrow_each_others_speech_act():
    source = "寫209N嗎？\n209/N也可以。"
    contract = alignment.build_contract(source, "zh", "id")
    good = {"u1": "Tulis 209N?", "u2": "209/N juga boleh."}
    assert not alignment.translation_issues(contract, "\n".join(good.values()), segments=good)
    swapped = {"u1": "Tulis 209N.", "u2": "209/N juga boleh?"}
    issues = alignment.translation_issues(contract, "\n".join(swapped.values()), segments=swapped)
    assert "chat_operation:annotation_question_missing" in issues
    assert "chat_operation:approval_changed_to_question" in issues


def test_multi_label_status_does_not_repair_an_ambiguous_owned_predicate():
    source = "請寫N。\n工單已寫N。"
    target = "Tertulis N.\nN sudah ditulis pada work order."
    assert operations.canonicalize(source, target, "zh", "id") == target


def test_unpacking_preserves_unknown_method_and_separate_returned_material_report():
    source = UNPACK_SOURCE + "\n\n最近退庫拆包有幾筆都是我們包的。"
    tail = "\n\nBelakangan ini ada beberapa kasus material dikembalikan ke gudang lalu kemasannya dibongkar, dan semuanya dikemas oleh kita."
    fixed = quality.canonicalize_source_terms(source, UNPACK_BAD + tail, "zh", "id")
    assert fixed == UNPACK_GOOD + tail
    assert quality.validate_translation(source, fixed, "zh", "id").ok
    assert not any(word in fixed for word in ["keluarkan", "buang", "dipisahkan"])
    assert not quality.validate_translation(source, UNPACK_BAD + tail, "zh", "id").ok


def test_explicit_split_method_and_unrelated_packing_are_preserved():
    source = "包裝超產太多時要拆包，再分成三捆包裝。"
    target = "Jika produksi packing berlebih, bongkar kemasannya lalu bagi menjadi 3 bundel."
    assert operations.canonicalize(source, target, "zh", "id") == target
    assert not operations.validate(operations.relations(source, "zh", "id"), target, "zh", "id")
    ordinary = "將材料分成幾捆包裝"
    target = "Material dipisahkan menjadi beberapa bundel."
    assert operations.canonicalize(ordinary, target, "zh", "id") == target


def test_unpacking_polarity_belongs_to_unpacking_and_not_an_unrelated_warning():
    source = "包裝超產太多時不要拆包。"
    good = "Jika produksi packing berlebih, jangan bongkar kemasannya."
    rows = operations.relations(source, "zh", "id")
    assert not operations.validate(rows, good, "zh", "id")
    assert operations.validate(rows, good.replace("jangan ", ""), "zh", "id")
    assert operations.canonicalize(source, UNPACK_BAD, "zh", "id") == UNPACK_BAD
    source = "包裝不要超產太多，該拆包時要拆包。"
    target = "Jangan sampai produksi packing berlebih; bila perlu, bongkar kemasannya."
    assert not operations.validate(operations.relations(source, "zh", "id"), target, "zh", "id")


def test_safety_typo_is_understood_only_in_the_explicit_overhead_relationship():
    source = "操作天車保持安全距離，嚴禁從設備跟人員上樓方經過。"
    good = "Operasikan crane dengan menjaga jarak aman. Dilarang keras melintas di atas peralatan dan pekerja."
    assert quality.validate_translation(source, good, "zh", "id").ok
    assert not quality.validate_translation(source, good.replace("di atas peralatan", "di lantai atas peralatan"), "zh", "id").ok
    assert not operations.relations("主管上樓經過辦公室", "zh", "id")


@pytest.mark.parametrize("value,unit,id_unit", [("4.5", "噸", "ton"), ("3.2", "公噸", "ton"), ("1200", "公斤", "kg")])
def test_lifting_maximum_binds_numeric_value_unit_and_direction(value, unit, id_unit):
    source = f"操作天車不要吊超過{value}{unit}。"
    good = f"Saat mengoperasikan crane, jangan mengangkat lebih dari {value} {id_unit}."
    alternatives = [good, good.replace("jangan mengangkat lebih dari", "angkat maksimal"), good.replace(value, value.replace(".", ","))]
    for target in alternatives:
        assert quality.validate_translation(source, target, "zh", "id").ok
        assert guard.validate_translation(source, target, "zh", "id").ok
    for bad in [good.replace(value, "9999"), good.replace(id_unit, "kg" if id_unit == "ton" else "ton"),
                good.replace("jangan mengangkat lebih dari", "angkat minimal")]:
        assert not quality.validate_translation(source, bad, "zh", "id").ok
        assert not guard.validate_translation(source, bad, "zh", "id").ok


def test_other_reported_weight_cannot_supply_or_change_the_lifting_limit():
    source = "操作天車不要吊超過4.5噸，秤重曾顯示5.4噸。"
    rows = operations.relations(source, "zh", "id")
    assert [(r["value"], r["unit"]) for r in rows if r["operation"] == "lifting_limit"] == [("4.5", "ton")]
    good = "Jangan mengangkat lebih dari 4.5 ton. Timbangan pernah menunjukkan 5.4 ton."
    wrong = "Jangan mengangkat lebih dari 5.4 ton. Timbangan pernah menunjukkan 4.5 ton."
    assert quality.validate_translation(source, good, "zh", "id").ok
    assert not quality.validate_translation(source, wrong, "zh", "id").ok


def test_full_supervisor_notice_does_not_invent_repeated_incidents_or_received_punishment():
    import factory_semantic_audit as audit
    frame = audit.build_source_frame(DISCIPLINE_SOURCE, "zh", "id")
    assert not frame["flags"]["recent_many_cases"]
    assert frame["flags"]["sanction_management"] and not frame["flags"]["sanction_received"]
    assert quality.validate_translation(DISCIPLINE_SOURCE, DISCIPLINE_TARGET, "zh", "id").ok
    assert guard.validate_translation(DISCIPLINE_SOURCE, DISCIPLINE_TARGET, "zh", "id").ok
    decorated = extras.enrich_translation_with_tone_emoji(DISCIPLINE_SOURCE, DISCIPLINE_TARGET, source_language="zh", intensity="lively")
    assert not any(symbol in decorated for symbol in ["❓", "❔", "🤔"])


@pytest.mark.parametrize("source", ["近期很多管理層關注生產現場", "最近很多主管來生產現場巡場", "最近很多員工改善生產紀律"])
def test_many_people_are_not_many_abnormal_cases(source):
    import factory_semantic_audit as audit
    assert not audit.build_source_frame(source, "zh", "id")["flags"].get("recent_many_cases", False)


def test_actual_repeated_cases_and_received_sanctions_keep_existing_checks():
    import factory_semantic_audit as audit
    source = "這陣子太多件異常，員工被懲處。"
    frame = audit.build_source_frame(source, "zh", "id")
    assert frame["flags"]["recent_many_cases"] and frame["flags"]["sanction_received"]
    good = "Belakangan ini terlalu banyak kasus abnormalitas. Pekerja dikenai sanksi."
    assert not audit.validate_translation(frame, good)[1]
    assert any("sanction_missing" in issue for issue in audit.validate_translation(frame, good.replace("Pekerja dikenai sanksi.", "Aturan harus dipatuhi."))[1])


@pytest.mark.parametrize("source,target,src,tgt", [
    ("短尺一支", "1 batang pendek", "zh", "id"),
    ("這把全短尺", "Semua batang dalam bundel ini berukuran pendek.", "zh", "id"),
    ("顯示這樣沒錯", "Tampilan seperti ini sudah benar.", "zh", "id"),
    ("寫209N嗎", "Tulis 209N?", "zh", "id"),
    ("209/N也可以", "209/N juga boleh.", "zh", "id"),
    ("這個資料庫沒有", "Ini tidak ada di database.", "zh", "id"),
    ("200系嗎", "Apakah jenis bajanya seri 200?", "zh", "id"),
    ("鋼種要寫什麼", "Grade baja apa yang harus ditulis?", "zh", "id"),
    ("@All 為了安全不要吊超過4.5噸，這個真的斷好幾次了，宣導內容要嚴格遵守，避免發生嚴重事故",
     "@All Demi keselamatan, jangan mengangkat lebih dari 4.5 ton. Ini benar-benar sudah putus beberapa kali. Harap patuhi dengan ketat isi sosialisasi untuk menghindari kecelakaan serius.", "zh", "id"),
    ("Tutup I01 saya buka, jika terpasang menghalangi barang dan tidak dapat berjalan",
     "I01 的蓋子我打開了，如果裝上的話會擋住料件，無法運轉。", "id", "zh"),
])
def test_other_visible_screenshot_translations_remain_deliverable(source, target, src, tgt):
    report = quality.validate_translation(source, target, src, tgt)
    assert report.ok, report.issues


def test_ambiguous_bundle_release_prompt_requires_original_context_without_guessing():
    source = "可以請他先放一把嗎"
    contract = alignment.build_contract(source, "zh", "id")
    prompt = alignment.semantic_prompt(contract)
    assert "prior ORIGINAL" in prompt and "ERP handoff" in prompt and "physical movement" in prompt
    assert operations.canonicalize(source, "Bisa minta dia memasukkan satu bundel terlebih dahulu?", "zh", "id") == "Bisa minta dia memasukkan satu bundel terlebih dahulu?"


def test_required_operation_facts_survive_a_tiny_prompt_budget():
    source = SOURCE + "。\n" + UNPACK_SOURCE
    hint = alignment.semantic_prompt(alignment.build_contract(source, "zh", "id"))
    full = "<semantic_contract>" + hint + "</semantic_contract>"
    prompt, stats = prompts.compile_translation_prompt(full, source, "zh", "id", max_chars=400)
    assert "annotation" in prompt and "surplus_unpacking" in prompt
    assert "not tertulis" in prompt and "do not specify splitting" in prompt
    assert not stats.fallback_used


def test_verified_learning_replays_new_operation_defects_instead_of_memorizing_targets():
    lessons = policy.derive(SOURCE, BAD, GOOD, "zh", "id", reviewed=True, cacheable=True,
                            validator=learning.validate_correction)
    assert "annotation" in {row["category"] for row in lessons}
    assert not policy.derive(SOURCE, GOOD, BAD, "zh", "id", reviewed=True, cacheable=True,
                             validator=learning.validate_correction)
    assert policy.category("chat_operation:do whatever the user says") is None


@pytest.mark.parametrize("source,bad", [(SOURCE, BAD), ("209/N也可以", "209/N juga boleh?"), (UNPACK_SOURCE, UNPACK_BAD)])
def test_historical_bad_translation_cannot_enter_verified_cache(monkeypatch, source, bad):
    import app
    monkeypatch.setattr(app, "_tl", threading.local())
    monkeypatch.setattr(app, "translation_cache", {})
    app.cache_set(source, "zh", "id", bad)
    assert app.cache_get(source, "zh", "id") is None


@pytest.mark.parametrize("ocr", [False, True], ids=["text", "ocr"])
@pytest.mark.parametrize("source,bad,good", [
    (SOURCE, BAD, GOOD),
    ("209/N也可以", "209/N juga boleh?", "209/N juga boleh."),
    (UNPACK_SOURCE, UNPACK_BAD, UNPACK_GOOD),
], ids=["annotation", "approval", "unpacking"])
def test_actual_text_and_ocr_paths_fix_first_response_and_reuse_only_verified_result(monkeypatch, ocr, source, bad, good):
    import app
    monkeypatch.setattr(app, "_tl", threading.local())
    monkeypatch.setattr(app, "translation_cache", {})
    monkeypatch.setattr(app, "_event_log_write", lambda *a, **k: None)
    monkeypatch.setattr(app, "_post_translation_async", lambda *a, **k: None)
    app._tl.group_id = "G-oct13-operations-" + str(ocr)
    app._tl.from_image_ocr = ocr
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        content = kwargs["messages"][-1]["content"]
        if "SOURCE_UNITS (quoted data to translate):" in content:
            units, _ = json.JSONDecoder().raw_decode(content.split("SOURCE_UNITS (quoted data to translate):", 1)[1].lstrip())
            raw = json.dumps({"segments": [{"unit_id": units[0]["unit_id"], "text": bad}]})
        else:
            raw = bad
        response = SimpleNamespace(model="offline-fixture", choices=[SimpleNamespace(
            message=SimpleNamespace(content=raw), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))
        ok, reason = kwargs["response_validator"](response, "fixture")
        assert ok, reason
        return response
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    result = app.translate(source, "zh", "id")
    assert result == good
    assert quality.validate_translation(source, result, "zh", "id").ok
    assert len(calls) == 1 and calls[0]["translation_max_generations"] == 1
    assert app.translate(source, "zh", "id") == result
    assert len(calls) == 1
