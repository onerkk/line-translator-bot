"""Reported screenshot errors, counterexamples, learning and single-call delivery.

All provider/LINE I/O is fake. These prove local behavior, not model accuracy.
"""
import json
from pathlib import Path

import pytest

import active_learning as learning
import ai_provider
import app
import conversation_context as context
import factory_knowledge as knowledge
import factory_semantic_audit as audit
import factory_workflow_semantics as workflow
import translation_learning_policy as policy
import translation_quality_gate as quality
import translation_retry_queue as queue
from test_translation_learned_policy import database, public_pipeline
from test_translation_instruction_cost_quality import offline_transport, response
from test_translation_notice_availability import runtime, clean_translation_context, event, delivered_text


FIELD_SOURCE = "存檔入庫都沒儲區"
FIELD_BAD = "Penyimpanan arsip dan proses masuk gudang sama-sama belum memiliki area penyimpanan."
MANUAL_SOURCE = "包裝存檔如果跳驗證有誤，先檢查是不是儲區預設不見了，如果你明確知道這間客戶的儲區，先手動維護，我明天跟儲運反應一下。"
MANUAL_BAD = ("Jika saat menyimpan data kemasan muncul kesalahan validasi, periksa terlebih dahulu apakah pengaturan default area penyimpanan hilang. "
              "Jika kalian sudah mengetahui dengan jelas area penyimpanan pelanggan ini, lakukan pemeliharaan secara manual terlebih dahulu. "
              "Besok saya akan menyampaikan masalah ini kepada bagian penyimpanan dan transportasi.")
CRATE_SOURCE = "@All 有設備待料我會拉人安排分擔線外工作，如果沒有再麻煩當天線外人員要注意一下待裝木箱。"
CRATE_BAD = ("@All Jika ada peralatan yang menunggu material, saya akan mengatur orang untuk membantu pekerjaan di luar lini. "
             "Jika tidak ada, mohon personel di luar lini pada hari itu memperhatikan peti kayu yang masih menunggu dipasang.")
ZONE_SOURCE = "@All 大成今晚包的不論哪一站都幫忙EC51"
ZONE_BAD = "@All 大成Malam ini, siapa pun yang melakukan pengemasan di stasiun mana pun, harap bantu EC51."
ZONE_GOOD = ("@All Untuk material 大成 yang dikemas malam ini, setelah selesai, angkat dan pindahkan semua produk jadi ke area penyimpanan EC51, "
             "terlepas dari lokasi penyimpanan yang tercetak pada TAG.")


@pytest.mark.parametrize("source,bad,required", [
    (FIELD_SOURCE, FIELD_BAD, ["Penyimpanan data", "informasi lokasi penyimpanan"]),
    (MANUAL_SOURCE, MANUAL_BAD, ["Jika kalian", "perbarui data lokasi penyimpanan secara manual", "Besok saya"]),
    (CRATE_SOURCE, CRATE_BAD, ["Jika ada", "Jika tidak ada", "material yang masih menunggu dikemas ke dalam peti kayu"]),
    ("月底前人力會優先開三站。", "Sampai akhir bulan, tenaga kerja akan diprioritaskan untuk membuka tiga stasiun.", ["mengoperasikan tiga stasiun", "Sampai akhir bulan"]),
    (ZONE_SOURCE, ZONE_BAD, ["Untuk material 大成", "malam ini", "angkat dan pindahkan", "EC51", "tercetak pada TAG"]),
])
def test_screenshot_repairs_are_local_idempotent_and_measurable(source, bad, required):
    assert workflow.issues(source, bad)
    corrected = quality.canonicalize_source_terms(source, bad, "zh", "id")
    assert corrected != bad
    assert all(value in corrected for value in required)
    assert quality.canonicalize_source_terms(source, corrected, "zh", "id") == corrected
    assert not workflow.issues(source, corrected)
    assert not audit.validate_translation(audit.build_source_frame(source, "zh", "id"), bad)[0]
    assert audit.validate_translation(audit.build_source_frame(source, "zh", "id"), corrected)[0]
    assert "<source_semantic_frame>" in audit.build_prompt(audit.build_source_frame(source, "zh", "id"))
    assert learning.validate_correction(source, corrected, "zh", "id")["ok"]


@pytest.mark.parametrize("source,bad", [
    ("機台需要手動維護。", "Mesin memerlukan pemeliharaan secara manual."),
    ("儲區旁的設備需要手動維護。", "Peralatan di dekat area penyimpanan memerlukan pemeliharaan manual."),
    ("紙本檔案存檔入庫。", "Penyimpanan arsip dilakukan di gudang."),
    ("倉庫沒有儲存空間。", "Gudang tidak memiliki area penyimpanan."),
    ("線外人員注意待組裝木箱。", "Perhatikan peti kayu yang masih menunggu dipasang."),
    ("明年人力會增加並新開三站。", "Tahun depan tenaga kerja akan ditambah dan akan membuka tiga stasiun."),
    ("資料入庫後維護機台。", "Setelah input data, lakukan pemeliharaan manual pada mesin."),
    ("存檔沒儲區，另外紙本檔案也要入庫。", "Penyimpanan arsip dan data dilakukan di gudang."),
    ("先吊到EC51，再支援EC55。", "Pindahkan ke EC51, lalu bantu EC55."),
])
def test_different_or_mixed_senses_are_never_silently_rewritten(source, bad):
    assert workflow.canonicalize(source, bad) == bad


@pytest.mark.parametrize("source,target", [
    (FIELD_SOURCE, "Saat data disimpan maupun dicatat masuk gudang, kolom lokasi penyimpanan kosong."),
    (MANUAL_SOURCE, "Jika ada kesalahan validasi saat menyimpan data pengemasan, cek apakah lokasi penyimpanan default hilang. Jika tahu pasti lokasi pelanggan, isi data itu secara manual dahulu. Besok saya laporkan ke bagian pergudangan dan transportasi."),
    (CRATE_SOURCE, "@All Jika mesin menunggu material, saya akan mengatur bantuan pekerjaan di luar lini. Jika tidak, personel di luar lini hari itu perlu memperhatikan material yang menunggu dimasukkan ke peti kayu."),
])
def test_natural_equivalents_do_not_require_one_exact_target_phrase(source, target):
    assert not workflow.issues(source, target)
    assert workflow.canonicalize(source, target) == target


@pytest.mark.parametrize("customer,when,zone", [("大成", "今晚", "EC51"), ("客戶甲", "明天", "EH28"), ("ABE", "明晚", "EG14")])
def test_confirmed_zone_shorthand_binds_current_values(customer, when, zone):
    source = f"@All {customer}{when}包裝的，不管哪站都幫忙{zone}。"
    bad = f"@All {customer} bantu {zone}."
    result = workflow.canonicalize(source, bad)
    assert customer in result and zone in result and "tercetak pada TAG" in result
    assert {"今晚": "malam ini", "明天": "besok", "明晚": "besok malam"}[when] in result
    assert "801" not in result
    if zone != "EC51":
        assert "EC51" not in result and "大成" not in result


@pytest.mark.parametrize("source", [
    ZONE_SOURCE + "，但超過3噸的例外。", ZONE_SOURCE + "，明天改EH32。",
    "除了大成今晚包的不論哪一站都幫忙EC51", "@All 大成今晚包的不論哪一站都幫忙I18",
    "請EC51的人幫忙包裝。", "EC51", "大成要用預設儲區。",
])
def test_partial_or_ambiguous_source_cannot_trigger_complete_destination_rendering(source):
    assert workflow.destination_override(source) is None
    assert workflow.canonicalize(source, "bantu EC51.") == "bantu EC51."


def test_zone_instruction_does_not_mutate_customer_defaults():
    before = json.loads((Path(__file__).parent / "storage_data.json").read_text())
    assert workflow.canonicalize(ZONE_SOURCE, ZONE_BAD) == ZONE_GOOD
    assert before["大成"] == [[">=3200", "EH32"], [">3200<=4200", "EH32"], [">4200", "EH32"]]
    assert json.loads((Path(__file__).parent / "storage_data.json").read_text()) == before


def test_empty_input_is_not_fabricated_into_success():
    assert workflow.canonicalize(ZONE_SOURCE, "") == ""


def test_data_station_and_physical_storage_are_distinct_first_pass_facts():
    source = "包裝完成資料進801後，成品吊入指定儲區EC51。"
    cards = knowledge.retrieve(source, "zh", "id", limit=3)
    assert "finished_goods_801_storage_destination" in [card["id"] for card in cards]
    target = app.factory_translation_guard_module.exact_verified_target(source, "zh", "id")
    assert "datanya masuk ke stasiun 801" in target
    assert "angkat dan pindahkan produk jadi ke area penyimpanan EC51" in target
    assert learning.validate_correction(source, target, "zh", "id")["ok"]


@pytest.mark.parametrize("source,target", [
    ("昨天洪副總有進來突擊檢查，開車進來，下車直接進來巡視。昨天晚班反應很快沒人被抓到違規，大家再注意一下！",
     "Kemarin Wakil Direktur 洪 melakukan pemeriksaan mendadak. Beliau masuk dengan mobil, turun lalu langsung masuk untuk melakukan inspeksi. Shift malam kemarin cepat merespons, sehingga tidak ada yang kedapatan melanggar aturan. Semuanya harap lebih memperhatikan lagi!"),
    ("班長桌這邊有中秋禮盒，台灣同仁記得抽空領取。\n\n#印尼同仁的部分應該會在宿舍發放。",
     "Di meja kepala regu ada kotak hadiah Festival Pertengahan Musim Gugur. Rekan kerja Taiwan, harap luangkan waktu untuk mengambilnya.\n\n#Untuk rekan kerja Indonesia, sepertinya akan dibagikan di asrama."),
    ("我再請營業明天早點過來取", "Saya akan meminta bagian Sales datang lebih awal besok untuk mengambilnya."),
    ("沒有人要取樣 牌子掛著就沒他們的事", "Tidak ada yang mau mengambil sampel. Setelah tag dipasang, mereka menganggap itu bukan urusan mereka."),
    ("要補印才有", "Harus mencetak ulang dulu baru ada."),
])
def test_other_screenshots_are_not_marked_wrong_or_rewritten_for_style(source, target):
    assert not workflow.issues(source, target)
    assert workflow.canonicalize(source, target) == target


def test_daily_intake_notice_keeps_all_numbers_periods_and_paragraphs():
    from test_month_end_notice_delivery import SOURCE
    candidate = ("Shift tengah bantu input 19 ton, target hari ini tembus 130 ton.\n\n"
                 "Target pemasukan gudang bulan ini dinaikkan menjadi 3600 ton. "
                 "Mulai besok sampai akhir bulan, rata-rata 143 ton per hari. "
                 "Sampai akhir bulan, tenaga kerja akan diprioritaskan untuk membuka tiga stasiun. "
                 "Harap atur waktu input gudang lebih merata.")
    result = quality.canonicalize_source_terms(SOURCE, candidate, "zh", "id")
    assert "proses pemasukan 19 ton ke gudang" in result
    assert "mengoperasikan tiga stasiun" in result
    assert all(token in result for token in ["130 ton", "3600 ton", "143 ton per hari", "Mulai besok sampai akhir bulan", "\n\n"])
    assert learning.validate_correction(SOURCE, result, "zh", "id")["ok"]


def test_local_correction_preserves_prohibition_and_does_not_change_language_direction():
    source = "包裝存檔儲區欄位空白，不知道客戶儲區就不要手動維護。"
    bad = "Jika lokasi pelanggan tidak diketahui, jangan lakukan pemeliharaan secara manual."
    good = quality.canonicalize_source_terms(source, bad, "zh", "id")
    assert "tidak diketahui, jangan perbarui data lokasi penyimpanan secara manual" in good
    assert quality.canonicalize_source_terms(source, bad, "id", "zh") == bad


@pytest.mark.parametrize("text,names,expected", [
    ("大成Malam ini", ["大成"], "大成 Malam ini"),
    ("material大成ditaruh", ["大成"], "material 大成 ditaruh"),
    ("洪 melakukan inspeksi.", ["洪"], "洪 melakukan inspeksi."),
    ("material A大成I18", ["A大成I18", "大成"], "material A大成I18"),
    ("https://example.com/大成Malam", ["大成"], "https://example.com/大成Malam"),
])
def test_complete_name_identity_and_urls_survive_spacing(text, names, expected):
    assert workflow.separate_name_boundaries(text, names) == expected
    assert workflow.separate_name_boundaries(expected, names) == expected


def test_legacy_placeholder_restoration_spaces_only_outside_names():
    assert app.restore_names("__PERSON_1__Malam", {"__PERSON_1__": "大成"}) == "大成 Malam"
    assert app.restore_customers("__CUST_1__Malam", {"__CUST_1__": "大成"}) == "大成 Malam"


@pytest.mark.parametrize("source,card_id", [
    (FIELD_SOURCE, "packaging_storage_fields_and_manual_update"),
    (MANUAL_SOURCE, "packaging_storage_fields_and_manual_update"),
    (CRATE_SOURCE, "pending_crate_packing_staffing"),
    (ZONE_SOURCE, "customer_packaging_code_scope"),
    ("要補印才有", "elliptical_reprint_result"),
    ("明天人力優先開兩站", "staffed_station_operation"),
])
def test_lessons_are_retrieved_into_first_prompt(source, card_id):
    cards = knowledge.retrieve(source, "zh", "id", limit=3)
    assert card_id in [card["id"] for card in cards]
    assert card_id in knowledge.build_prompt(cards)


@pytest.mark.parametrize("source,bad", [(FIELD_SOURCE, FIELD_BAD), (MANUAL_SOURCE, MANUAL_BAD), (CRATE_SOURCE, CRATE_BAD), (ZONE_SOURCE, ZONE_BAD)])
def test_measured_local_corrections_teach_without_review_api(database, source, bad):
    good = quality.canonicalize_source_terms(source, bad, "zh", "id")
    result = learning.record_translation_outcome(source_text=source, candidate_text=bad, final_text=good,
        src_lang="zh", tgt_lang="id", group_id="screenshot", issues=workflow.issues(source, bad),
        reviewed=False, cacheable=True, path="source_bound_local_correction")
    assert result["learned_rules"] > 0
    prompt = policy.build_prompt(learning.prepare_translation(source, "zh", "id", "screenshot"))
    assert prompt and bad not in prompt and good not in prompt
    assert learning.list_corrections(status="approved") == []
    assert not learning.prepare_translation(source, "zh", "id", "another-group")["rules"]


def test_destination_lesson_generalizes_without_copying_customer_or_zone(database):
    result = learning.record_translation_outcome(source_text=ZONE_SOURCE, candidate_text=ZONE_BAD, final_text=ZONE_GOOD,
        src_lang="zh", tgt_lang="id", group_id="screenshot", issues=workflow.issues(ZONE_SOURCE, ZONE_BAD),
        reviewed=False, cacheable=True, path="source_bound_local_correction")
    assert result["learned_rules"]
    query = "@All ABE明天包裝的，不管哪站都幫忙EH28。"
    prompt = policy.build_prompt(learning.prepare_translation(query, "zh", "id", "screenshot"))
    assert "storage" in prompt and "TAG" in prompt
    assert "EC51" not in prompt and "大成" not in prompt


def test_public_pipeline_repair_learns_and_costs_only_once(public_pipeline, monkeypatch):
    calls = []
    monkeypatch.setattr(ai_provider, "_dispatch_provider", lambda provider, **kw: calls.append(kw) or response(MANUAL_BAD))
    source = MANUAL_SOURCE.replace("包裝存檔如果跳驗證有誤", "包裝存檔若出現驗證錯誤")
    result = app.translate(source, "zh", "id")
    assert "perbarui data lokasi penyimpanan secara manual" in result
    assert len(calls) == 1
    assert learning.prepare_translation(source, "zh", "id", "G1")["rules"]
    prompt = "\n".join(str(m["content"]) for m in calls[0]["messages"])
    assert "memperbarui data lokasi penyimpanan secara manual" in prompt


ORIGINAL_TRANSLATOR = app.translate_openai


@pytest.mark.parametrize("when,expected_calls", [("今晚", 0), ("明天", 1)])
def test_confirmed_ec51_meaning_is_delivered_once_no_retry(runtime, offline_transport, monkeypatch, when, expected_calls):
    calls = []
    monkeypatch.setattr(ai_provider, "_dispatch_provider", lambda provider, **kw: calls.append(kw) or response(ZONE_BAD))
    monkeypatch.setattr(app, "translate_openai", ORIGINAL_TRANSLATOR)
    source = ZONE_SOURCE.replace("今晚", when)
    app.handle_message(event(source))
    delivered = delivered_text(runtime)
    assert "angkat dan pindahkan" in delivered and "EC51" in delivered
    assert "tercetak pada TAG" in delivered and "bantu EC51" not in delivered
    assert len(calls) == expected_calls and len(runtime.sends) == 1
    assert queue.pending_count() == 0
    app.handle_message(event(source))
    assert len(calls) == expected_calls and len(runtime.sends) == 1


def test_reprint_context_is_bound_to_original_group_and_is_not_a_global_fact(tmp_path):
    journal = context.SourceJournal(tmp_path / "context.db")
    journal.capture("G1", "1", FIELD_SOURCE, author="A")
    snapshot = journal.capture("G1", "2", "要補印才有", author="A")
    assert FIELD_SOURCE in context.prompt_data(snapshot)
    assert "without inventing its type" in context.PROMPT_RULES
    other = journal.capture("G2", "3", "要補印才有", author="A")
    assert not other["entries"]
    assert workflow.canonicalize("要補印才有", "Harus dicetak ulang dulu, baru ada.") == "Harus dicetak ulang dulu, baru ada."
