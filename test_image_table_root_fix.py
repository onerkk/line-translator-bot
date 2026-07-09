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
    assert "_tl.group_id = group_id or" in fn
    assert "_tl.user_id = (info or {}).get(\"user_id\", \"\")" in fn
    assert "_tl.from_image_ocr = True" in fn
    assert "result = translate(extracted, \"zh\", actual_tgt)" in fn
    assert "result = translate(extracted, lang, actual_tgt)" in fn
