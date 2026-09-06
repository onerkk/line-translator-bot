import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app.py"


def _source():
    return APP.read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    source = _source()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"function not found: {name}")


def test_image_direct_translation_route_is_removed():
    """Image tables must not bypass the normal text translation pipeline."""
    source = _source()
    assert "def translate_sensitive_image_table" not in source
    assert "_looks_like_sensitive_ocr_table" not in source
    assert "before_direct_image_table_translate" not in source
    assert "direct_image_table_translate" not in source


def test_ocr_prompt_outputs_source_table_not_translation():
    source = _source()
    ocr_fn = _function_source("ocr_image_openai")
    assert "不要翻譯" in ocr_fn
    assert "表格 / Excel / ERP 截圖" in ocr_fn
    assert "` | ` 分隔欄位" in ocr_fn
    assert "同一水平格線" in ocr_fn
    assert "不可先讀整欄再自行配對" in ocr_fn
    assert "ID / 料號 / 爐號 / TAG / 批號保護" in ocr_fn
    assert "不得翻譯、不得自動校正、不得補字" in ocr_fn


def test_auto_image_translation_uses_standard_translate_with_context_snapshot():
    fn = _function_source("_handle_image_background")
    assert "translate_sensitive_image_table" not in fn
    assert "result = translate(" not in fn  # timeout worker must use context wrapper
    assert "_tl.group_id = group_id or" in fn
    assert "_tl.user_id = user_id or" in fn
    assert "_tl.from_image_ocr = True" in fn
    assert "_ctx_snapshot = _snapshot_translation_thread_context()" in fn
    assert "_translate_with_thread_context(" in fn
    assert "translate, extracted, \"zh\", actual_tgt" in fn
    assert "translate, extracted, lang, actual_tgt" in fn


def test_ask_mode_image_translation_uses_same_standard_pipeline():
    fn = _function_source("_process_pending_image_translate_inner")
    assert "translate_sensitive_image_table" not in fn
    assert "_looks_like_sensitive_ocr_table" not in fn
    assert "_handle_image_background(ctx)" in fn
    assert "_schedule_image_translation_retry(ctx" in fn
    assert '"user_id": info.get("user_id")' in fn
    assert "ocr_image_openai(" not in fn  # no divergent ask-mode pipeline


def _exec_factory_reason_semantic_subset():
    source = _source()
    tree = ast.parse(source)
    wanted_assigns = {
        "_FACTORY_REASON_ACTIONS",
        "_FACTORY_REASON_WRONG_ID_PATTERNS",
        "_FACTORY_REASON_ID_RE",
        "_FACTORY_REASON_OCR_FAIL_SENTINEL",
    }
    wanted_defs = {
        "_compact_factory_reason_text",
        "_factory_reason_action_map",
        "_match_factory_reason_action",
        "_is_factory_reason_header_line",
        "_parse_factory_reason_table_row",
        "_factory_reason_line_id_only",
        "_parse_factory_reason_split_rows",
        "_parse_factory_reason_column_major_rows",
        "_factory_reason_table_alignment_issue",
        "_factory_reason_alignment_failure_message",
        "_is_factory_reason_header_only_text",
        "_factory_reason_entries_in_text",
        "_looks_like_factory_reason_table_text",
        "_factory_reason_semantic_translate_zh_id",
        "_factory_reason_translation_contains",
        "_factory_reason_contract_risk",
        "_build_factory_reason_contract_lines",
        "translation_satisfies_semantic_contract",
        "enforce_translation_semantic_contract",
        "_factory_reason_ocr_row_count",
        "_factory_reason_ocr_incomplete_against_visual_count",
        "_should_run_factory_reason_table_ocr",
        "_factory_reason_parsed_rows",
        "_factory_reason_ocr_has_uncertain_cells",
        "_factory_reason_ocr_rows_conflict",
        "_factory_reason_ocr_strict_safety_issue",
        "_strict_factory_reason_ocr_is_better",
        "_factory_reason_ocr_failure_payload",
        "_is_factory_reason_ocr_failure_text",
        "_factory_reason_user_failure_reply",
    }
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & wanted_assigns:
                nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_defs:
            nodes.append(node)
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    class DummyLogger:
        def warning(self, *args, **kwargs):
            pass

    ns = {
        "re": __import__("re"),
        "logger": DummyLogger(),
        "_event_log_write": lambda *args, **kwargs: None,
    }
    exec(compile(module, str(APP), "exec"), ns)
    return ns


def test_factory_reason_table_translates_with_user_provided_semantics():
    ns = _exec_factory_reason_semantic_subset()
    text = "ID | 原因\n7H385503A | 改端漆\n7H347507 | 削皮\n7H110003 | 補毛重\n7G319512B | 取樣"
    out = ns["_factory_reason_semantic_translate_zh_id"](text)
    assert out == (
        "ID | Alasan\n"
        "7H385503A | Ubah warna cat ujung\n"
        "7H347507 | Kembalikan ke stasiun peeling\n"
        "7H110003 | Timbang ulang berat kotor\n"
        "7G319512B | Bongkar packing, serahkan ke station 480 untuk sampling"
    )



def test_factory_reason_semantics_survive_paragraph_split_and_ocr_split_columns():
    ns = _exec_factory_reason_semantic_subset()
    assert ns["_factory_reason_semantic_translate_zh_id"]("ID | 原因") == "ID | Alasan"
    assert ns["_factory_reason_semantic_translate_zh_id"]("7H385503A | 改端漆") == "7H385503A | Ubah warna cat ujung"
    split_ocr = "ID | 原因\n7H385503A\n改端漆\n7H110003\n補毛重\n7G681208A\n倒角"
    assert ns["_factory_reason_semantic_translate_zh_id"](split_ocr) == (
        "ID | Alasan\n"
        "7H385503A | Ubah warna cat ujung\n"
        "7H110003 | Timbang ulang berat kotor\n"
        "7G681208A | Ujung perlu di-chamfer"
    )


def test_factory_reason_column_major_ocr_pairs_only_when_counts_match():
    ns = _exec_factory_reason_semantic_subset()
    column_ocr = (
        "ID | 原因\n"
        "7H385503A\n"
        "7H347507\n"
        "7H110003\n"
        "改端漆\n"
        "改端漆\n"
        "補毛重"
    )
    assert ns["_factory_reason_semantic_translate_zh_id"](column_ocr) == (
        "ID | Alasan\n"
        "7H385503A | Ubah warna cat ujung\n"
        "7H347507 | Ubah warna cat ujung\n"
        "7H110003 | Timbang ulang berat kotor"
    )

    unsafe_shift = (
        "ID | 原因\n"
        "7H385503A\n"
        "7H347507\n"
        "7H110003\n"
        "改端漆\n"
        "改端漆"
    )
    assert ns["_parse_factory_reason_column_major_rows"](unsafe_shift.splitlines()) == []
    assert ns["_factory_reason_table_alignment_issue"](unsafe_shift) == "id_reason_count_mismatch"


def test_dedicated_factory_reason_ocr_guard_is_source_only_and_row_grid():
    source = _source()
    strict_fn = _function_source("ocr_factory_reason_table_openai")
    assert "同一條水平格線逐列讀取" in strict_fn
    assert "不可先讀完整 ID 欄再讀原因欄後自行配對" in strict_fn
    assert "不得翻譯" in strict_fn
    assert "ocr_factory_reason" in strict_fn

    ns = _exec_factory_reason_semantic_subset()
    generic_bad = "ID | 原因\n7H385503A | 改端漆\n7H347507 | 補毛重"
    strict_good = "ID | 原因\n7H385503A | 改端漆\n7H347507 | 補毛重\n7G681208A | 倒角"
    assert ns["_should_run_factory_reason_table_ocr"](generic_bad) is True
    assert ns["_strict_factory_reason_ocr_is_better"](generic_bad, strict_good) is True
    translated_strict = "ID | Alasan\n7H385503A | Ubah warna cat ujung"
    assert ns["_strict_factory_reason_ocr_is_better"](generic_bad, translated_strict) is False


def test_translation_boundary_and_paragraph_worker_have_reason_semantic_guard():
    translate_fn = _function_source("translate")
    para_fn = _function_source("_translate_single_paragraph")
    assert "_factory_reason_semantic_translate_zh_id(canonical_text)" in translate_fn
    assert "cache、TM、NMT、LLM、final guard 之前先決定" in translate_fn
    assert "_factory_reason_semantic_translate_zh_id(text)" in para_fn
    assert "Nomor Material / Air Palsu / Gerinda" in para_fn


def test_factory_reason_semantic_contract_blocks_old_bad_outputs():
    ns = _exec_factory_reason_semantic_subset()
    source = "ID | 原因\n7H385503A | 改端漆\n7H347507 | 削皮"
    risk = ns["_factory_reason_contract_risk"](source)
    assert risk and risk["sense"] == "factory_reason_action_semantics"
    contract = {"has_risk": True, "risks": [risk]}
    bad = "ID | Nomor Material\n7H385503A | Sedang Mengubah Cetakan\n7H347507 | Air Palsu"
    ok, reason = ns["translation_satisfies_semantic_contract"](contract, bad)
    assert not ok
    assert reason.startswith("factory_reason_wrong_output")
    fixed = ns["enforce_translation_semantic_contract"](contract, source, bad)
    assert "Nomor Material" not in fixed
    assert "Air Palsu" not in fixed
    assert "ID | Alasan" in fixed
    assert "Ubah warna cat ujung" in fixed
    assert "Kembalikan ke stasiun peeling" in fixed


def test_factory_reason_short_labels_are_flexible_but_do_not_rewrite_normal_station_sentence():
    ns = _exec_factory_reason_semantic_subset()
    assert ns["_factory_reason_semantic_translate_zh_id"]("端漆") == "Ubah warna cat ujung"
    assert ns["_factory_reason_semantic_translate_zh_id"]("改TAG") == "Input ulang data dan tempel ulang TAG"
    assert ns["_factory_reason_semantic_translate_zh_id"]("毛重") == "Timbang ulang berat kotor"
    assert ns["_factory_reason_contract_risk"]("削皮那邊優先放行") is None



def test_factory_reason_visual_count_guard_blocks_incomplete_image_ocr():
    ns = _exec_factory_reason_semantic_subset()
    incomplete = (
        "ID | 原因\n"
        "7H885503A | 改端漆\n"
        "7H347507 | 改端漆\n"
        "7H110003 | 補毛重\n"
        "7G319512B | 退火\n"
        "7H431029 | 退火\n"
        "7H431029B | 退火\n"
        "7H431029A | 退火\n"
        "7H720110B | 退火\n"
        "7G726107 | 退火\n"
        "7G966501A | 削皮\n"
        "7G552302 | 削皮\n"
        "7H679210 | 削皮\n"
        "7H719113D | 削皮\n"
        "7H719113C | 削皮\n"
        "7H060307 | 削皮\n"
        "7H799315 | 削皮\n"
        "7H799315A | 削皮\n"
        "7B466010C | 削皮\n"
        "7H503104A | 削皮\n"
        "7I006004A | 改包裝\n"
        "7G830028B | 改包裝"
    )
    assert ns["_factory_reason_ocr_row_count"]("ID | 原因\n7H885503A | 改端漆\n7H347507 | 改端漆") == 2
    assert ns["_factory_reason_ocr_incomplete_against_visual_count"](incomplete, 24) is True
    assert ns["_factory_reason_ocr_incomplete_against_visual_count"](incomplete, 22) is False


def test_factory_reason_row_crop_ocr_path_exists_before_full_table_fallback():
    source = _source()
    strict_fn = _function_source("ocr_factory_reason_table_openai")
    row_fn = _function_source("ocr_factory_reason_table_rows_openai")
    translate_fn = _function_source("translate")
    core_fn = _function_source("_translate_core")
    assert "_factory_reason_visual_row_bands_from_image" in source
    assert "contact sheet" in row_fn
    assert "Rxx | <ID> | <原因>" in row_fn
    assert "row_result = ocr_factory_reason_table_rows_openai" in strict_fn
    assert "factory_reason_image_expected_rows" in row_fn
    assert "_factory_reason_ocr_incomplete_against_visual_count" in translate_fn
    assert "factory_reason_ocr_visual_count_failed" in core_fn



def test_factory_reason_image_ocr_fail_closed_on_conflict_and_incomplete_strict():
    ns = _exec_factory_reason_semantic_subset()
    generic = (
        "ID | 原因\n"
        "7G681208A | 倒角\n"
        "7H834313 | 倒角\n"
        "7H060307 | 拋光"
    )
    shifted = (
        "ID | 原因\n"
        "7G681208A | 併包\n"
        "7H834313 | 併包\n"
        "7H060307 | 削皮"
    )
    assert ns["_factory_reason_ocr_rows_conflict"](generic, shifted) is True
    assert ns["_factory_reason_ocr_strict_safety_issue"](generic, shifted, 3) == "strict_ocr_conflicts_with_generic"

    incomplete = "ID | 原因\n7G681208A | 倒角\n7H834313 | 倒角"
    assert ns["_factory_reason_ocr_strict_safety_issue"](generic, incomplete, 4) == "strict_ocr_visual_row_mismatch"


def test_factory_reason_image_ocr_fail_payload_is_interceptable():
    ns = _exec_factory_reason_semantic_subset()
    payload = ns["_factory_reason_ocr_failure_payload"]("strict_ocr_visual_row_mismatch")
    assert ns["_is_factory_reason_ocr_failure_text"](payload) is True
    reply = ns["_factory_reason_user_failure_reply"]()
    assert "已停止翻譯" in reply
    assert "ID 與原因欄" in reply
    assert "mencocokkan ID dan Alasan" in reply


def test_ocr_image_openai_keeps_generic_transcript_when_strict_alignment_is_uncertain():
    fn = _function_source("ocr_image_openai")
    assert "factory_reason_ocr_fail_closed" in fn
    assert "factory_reason_ocr_degraded" in fn
    assert "return result" in fn
    assert "return _factory_reason_ocr_failure_payload" not in fn


def test_image_handlers_never_send_factory_reason_failure_text_as_translation():
    bg_fn = _function_source("_handle_image_background")
    ask_fn = _function_source("_process_pending_image_translate_inner")
    assert "_is_factory_reason_ocr_failure_text(extracted)" in bg_fn
    assert "_factory_reason_user_failure_reply()" not in bg_fn
    assert "_handle_image_background(ctx)" in ask_fn
    assert "_factory_reason_user_failure_reply()" not in ask_fn
    assert "durable" in bg_fn.lower()
    assert "durable" in ask_fn.lower()
