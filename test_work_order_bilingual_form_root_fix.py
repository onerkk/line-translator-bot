"""Offline regressions based on the user's 210755.jpg work-order form.

The strings below are manually transcribed/constructed OCR layouts, not a claim
that a live vision provider returned them. The original photo is retained at
tests/fixtures/work_order_20260906.jpg for deployment-side OCR acceptance.
"""

from types import SimpleNamespace

import pytest

import app
from work_order_detection import analyze_work_order_text, resolve_storage_customer


SAMPLE_OCR = """冷精棒製造指示書 Petunjuk produksi Cold Finished Bar
訂單編號 / No.Pesan | 客戶名稱 / Nama Pelanggan | 收貨人 / Penerima Barang | 生計交期 / Estimasi pengiriman | 性質碼 / Kode Jenis | 倒角 / Chamfer
Y1224523-034 | 大成 | 大成 | 20260930 | 0C07 | 不倒
成品尺寸MIN / Ukuran MIN produk jadi | MAX | 短邊MIN / Sisi pendek MIN | MAX
9.48 | 9.56 | 0 | 0
訂單流程 / Alur Pemasangan | 成品MC / Produk jadi MC
CHRAPD | A-CDR21-S304CA-0C07-01
ID_NO | 尺寸1 / Uk.1 | 尺寸2 / Uk.2 | 厚度 | 長度 | 型態 | 重量 Berat
7J846310 | 10 | 0 | 0 | 0 | D | 1505
眼模 / Cetakan lubang
項目 | 尺寸1MIN/MAX | 編號 / No. Kode
1CD | 9.48 / 9.56 | 76591192
工作站 / stasiun kerja | 機台 / mesin
"""

NAMES = {"大成": [[">4200", "EH32"]], "HAKUDO": [["<=3200", "EG14"]],
         "ALCONIX JP": [["<=3200", "EG14"]], "B&B": [["<=3200", "EH28"]]}


def test_new_reference_uses_uploaded_storage_data():
    analysis = app.analyze_work_order(SAMPLE_OCR)
    assert analysis["is_work_order"] is True
    assert analysis["customer"] == "大成"
    assert app.detect_work_order(SAMPLE_OCR) == "大成"
    assert {area for _length, area in app.STORAGE_LOOKUP["大成"]} == {"EH32"}
    reply = app.format_storage_for_work_order(analysis["customer"])
    assert "客戶：大成" in reply and "EH32" in reply
    assert "Nama" not in reply and "7J846310" not in reply


@pytest.mark.parametrize("body", [
    "客戶名稱：大成\n收貨人：HAKUDO",
    "客戶名稱 / Nama Pelanggan：大成\n收貨人：HAKUDO",
    "客戶名稱 (Nama Pelanggan)：大成\n收貨人：HAKUDO",
    "客戶名稱\nNama\nPelanggan\n大成\n收貨人\nHAKUDO",
    "客戶名稱 Nama Pelanggan 大成 收貨人 Penerima Barang HAKUDO",
    "客 戶 名 稱：大 成\n收貨人：HAKUDO",
    "客户名称：大成\n收货人：HAKUDO",
    "訂單編號 | 客戶名稱 | 收貨人\nNo.Pesan | Nama | Penerima\n | Pelanggan | Barang\nY1224523-034 | 大成 | HAKUDO",
    "| 訂單編號 | 客戶名稱<br>Nama Pelanggan | 收貨人 |\n| --- | --- | --- |\n| Y1224523-034 | 大成 | HAKUDO |",
    "No.Pesan | Nama Pelanggan | Penerima Barang\nY1224523-034 | 大成 | HAKUDO",
    "訂單編號\t客戶名稱\t收貨人\nY1224523-034\t大成\tHAKUDO",
    "客戶名稱 | 大成\n收貨人 | HAKUDO",
])
def test_new_bilingual_and_legacy_layouts_select_customer_column(body):
    result = analyze_work_order_text("冷精棒製造指示書\n訂單編號：Y1224523-034\n" + body, NAMES)
    assert result["is_work_order"]
    assert result["customer"] == "大成"


@pytest.mark.parametrize(("raw", "expected"), [
    ("ALCONIX JP", "ALCONIX JP"),
    ("alconix jp", "ALCONIX JP"),
    ("ＡＬＣＯＮＩＸ　ＪＰ", "ALCONIX JP"),
    ("ALCONIXJP", "ALCONIX JP"),
    ("B&B", "B&B"),
])
def test_whole_names_keep_spaces_punctuation_and_width_equivalence(raw, expected):
    result = analyze_work_order_text("製造指示書\n客戶名稱：" + raw, NAMES)
    assert result["customer"] == expected


def test_indonesian_only_and_cropped_form():
    text = "No.Pesan | Nama Pelanggan | Penerima Barang\nY1224523-034 | 大成 | HAKUDO\nProduk jadi MC | A-CDR21"
    assert analyze_work_order_text(text, NAMES)["customer"] == "大成"


def test_legacy_final_mic_no_form():
    text = "冷精棒製造指示書\n客戶名稱：HAKUDO\nFINAL流程：HRITABPDIL\nMIC_NO：A123\nID_NO：7J846310"
    assert analyze_work_order_text(text, NAMES)["customer"] == "HAKUDO"


@pytest.mark.parametrize("body", [
    "客戶名稱\nNama Pelanggan\n收貨人\nHAKUDO",
    "訂單編號 | 客戶名稱 | 收貨人\nY1224523-034 | | HAKUDO\n成品尺寸MIN | 9.48 | 9.56",
    "訂單編號 | 客戶名稱 | 收貨人\nY1224523-034 | ? | HAKUDO",
    "訂單編號 | 客戶名稱 | 收貨人\nY1224523-034 | HAKUDO",
    "客戶名稱：大?\n收貨人：HAKUDO",
    "客戶名稱：看不清\n收貨人：HAKUDO",
    "收貨人：HAKUDO\n備註：大成",
    "客戶名稱：\n收貨人：HAKUDO\n備註：大成",
    "客戶名稱：大成\n客戶名稱：HAKUDO",
    "客戶名稱：大成\n客戶名稱：?",
])
def test_uncertain_or_conflicting_fields_never_guess_from_recipient_or_notes(body):
    result = analyze_work_order_text("製造指示書\n訂單編號：Y1224523-034\n" + body, NAMES)
    assert result["is_work_order"]
    assert result["customer"] is None


@pytest.mark.parametrize("text", [
    None, "", "品保特殊要求，削皮後確認短尺。", "成品尺寸MIN/MAX 請再檢查。",
    "@All 請按照工單作業，收貨人是大成。",
])
def test_generic_notices_and_empty_inputs_are_not_work_orders(text):
    assert not analyze_work_order_text(text, NAMES)["is_work_order"]


def test_unknown_customer_does_not_fall_back_to_recipient(monkeypatch):
    monkeypatch.setattr(app, "STORAGE_LOOKUP", NAMES)
    analysis = app.analyze_work_order("製造指示書\n客戶名稱：大成新客戶\n收貨人：HAKUDO")
    assert analysis["customer"] == "大成新客戶"
    assert app.format_storage_for_work_order(analysis["customer"]) is None


@pytest.mark.parametrize("query", [None, "", "ALCONIX", "大成新客戶", "B", "HAKUD0"])
def test_automatic_lookup_never_uses_partial_or_fuzzy_customer_match(query):
    assert resolve_storage_customer(query, NAMES) is None


def test_normalized_collision_is_not_selected_arbitrarily():
    names = ["AB C", "A BC"]
    assert resolve_storage_customer("abc", names) is None
    assert resolve_storage_customer("AB C", names) == "AB C"


@pytest.mark.parametrize("name", ["PACKER(ISRAEL)", "STIRLINGS(5%)", "YIEH CORP LTD(HK)", "力常(觀音)", "營三備庫(外)"])
def test_customer_branch_parentheses_are_preserved(name):
    text = "製造指示書\n客戶名稱：" + name
    assert analyze_work_order_text(text, app.STORAGE_LOOKUP)["customer"] == name


def test_updated_storage_data_is_used_immediately(monkeypatch):
    monkeypatch.setattr(app, "STORAGE_LOOKUP", {"大成": [[">4200", "NEW01"]]})
    assert "NEW01" in app.format_storage_for_work_order(app.detect_work_order(SAMPLE_OCR))
    assert "EH32" not in app.format_storage_for_work_order("大成")


@pytest.mark.parametrize("material_label", ["ID_NO", "ID NO", "ID\nNO", "ID"])
def test_manufacturing_form_does_not_trigger_erp_reason_ocr(material_label):
    assert not app._should_run_factory_reason_table_ocr(SAMPLE_OCR.replace("ID_NO", material_label))


def test_real_erp_reason_table_still_uses_row_ocr():
    assert app._should_run_factory_reason_table_ocr("ID | 原因\n7J846310 | 倒角\n7H347507 | 削皮")


def test_ocr_hint_is_wired_into_one_vision_call_without_sample_defaults(monkeypatch):
    calls = []

    def vision(messages, **kwargs):
        calls.append(messages)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=SAMPLE_OCR))])

    monkeypatch.setattr(app, "_has_ai_capability", lambda *_a: True)
    monkeypatch.setattr(app, "_vision_call", vision)
    monkeypatch.setattr(app, "track_tokens", lambda *_a: None)
    result = app.ocr_image_openai("offline-placeholder")
    assert len(calls) == 1
    system_prompt = calls[0][0]["content"]
    assert "Nama Pelanggan" in system_prompt
    assert "Penerima Barang" in system_prompt
    assert "空白格也要保留分隔符" in system_prompt
    assert "Y1224523-034" not in system_prompt and "EH32" not in system_prompt
    assert app.detect_work_order(result) == "大成"
