import ast
import json
from pathlib import Path

import glossary_policy
import nmt_provider
import prompt_optimizer

ROOT = Path(__file__).resolve().parent


def _representative_full_prompt():
    vocab = ", ".join(
        [
            "I9=Mesin I9",
            "品保=QC",
            "停機=hentikan mesin",
            "研磨棒=batang grinding",
            "工單=work order",
        ]
        + [f"無關詞{i}=istilah{i}" for i in range(400)]
    )
    contexts = "\n".join(
        [
            "10. CRITICAL CONTEXT RULES:",
            "a) I9、品保、停機屬設備與品質語境，必須保留命令和否定。",
            "b) 研磨棒、重量、kg 屬製程與數據語境，數值不可改。",
        ]
        + [f"{chr(99 + i % 20)}) 無關歷史案例 {i}。" for i in range(150)]
    )
    return f"""固定前綴
<role>legacy role</role>
<semantic_contract>Preserve actor/action/object/negation/data.</semantic_contract>
<factory_vocabulary>9. FACTORY VOCABULARY: {vocab}</factory_vocabulary>
<context_disambiguation>{contexts}</context_disambiguation>
<format_rules>Keep line breaks and symbols.</format_rules>
<output_format>Only translation.</output_format>
"""


def test_prompt_compiler_keeps_principles_but_removes_irrelevant_bulk():
    full = _representative_full_prompt()
    compiled, stats = prompt_optimizer.compile_translation_prompt(
        full,
        "I9先停機，等品保確認後再開。",
        "zh",
        "id",
    )
    assert not stats.fallback_used
    assert len(compiled) < len(full) * 0.45
    assert prompt_optimizer.prompt_contains_required_invariants(compiled)
    assert "equipment-code" in compiled
    assert "quality-defect" in compiled
    assert "I9" in compiled
    assert "無關詞399" not in compiled


def test_prompt_compiler_preserves_large_prompt_logic_as_compact_principles():
    full = _representative_full_prompt()
    zh_id, _ = prompt_optimizer.compile_translation_prompt(
        full, "請把I9停機，等品保確認後再開。", "zh", "id"
    )
    assert "standard spelling" in zh_id
    assert "Preserve @mentions exactly" in zh_id
    assert "Preserve emoji, line breaks, blank lines" in zh_id
    assert "Taiwanese rhetorical questions" in zh_id

    id_zh, _ = prompt_optimizer.compile_translation_prompt(
        full, "Pelindung mesin rusak, tidak bisa dipakai.", "id", "zh"
    )
    assert "Traditional Chinese used in Taiwan" in id_zh
    assert "equipment or safety devices use 損壞/故障" in id_zh
    assert "equipment-material-damage" in id_zh


def test_indonesian_vocab_selection_avoids_generic_mesin_fanout():
    full = """<role>x</role>
<factory_vocabulary>9. FACTORY VOCABULARY: 護罩=pelindung mesin/safety guard, 機台=mesin, 研磨機=mesin grinding, 調機=penyetelan mesin, 物料=material, 工單=work order</factory_vocabulary>
<context_disambiguation>10. CRITICAL CONTEXT RULES: a) 護罩是安全裝置。</context_disambiguation>
<format_rules>Keep lines.</format_rules><output_format>Only translation.</output_format>"""
    compiled, stats = prompt_optimizer.compile_translation_prompt(
        full, "Pelindung mesin rusak.", "id", "zh"
    )
    assert "護罩=pelindung mesin" in compiled
    assert "機台=mesin" not in compiled
    assert "物料=material" not in compiled
    assert stats.vocab_items <= 2


def test_prompt_variants_change_style_without_weakening_invariants():
    full = _representative_full_prompt()
    for variant, expected in {
        "natural": "natural native workplace phrasing",
        "literal": "close, transparent translation",
        "formal": "formal, professional announcement wording",
        "backcheck": "semantic reversibility",
    }.items():
        compiled, _ = prompt_optimizer.compile_translation_prompt(
            full, "品保確認後再開機。", "zh", "id", variant=variant
        )
        assert expected in compiled
        assert "Preserve @mentions" in compiled
        assert "runtime semantic contract" in compiled


def test_nmt_routes_only_explicitly_safe_chat_for_zh_id():
    assert nmt_provider.should_use_nmt("早安", "zh", "id")
    assert nmt_provider.should_use_nmt("吃飯了嗎", "zh", "id")
    for text in (
        "I9先停機",
        "品保確認後再開機",
        "這批料先不要做",
        "今天晚上8點下班",
        "請把R28.57移到I9，重量1250kg",
    ):
        ok, reason = nmt_provider.nmt_route_reason(text, "zh", "id")
        assert not ok, (text, reason)


def test_nmt_blocks_indonesian_factory_and_negative_messages():
    for text in ("Mesin I9 rusak", "Jangan nyalakan mesin", "QC belum konfirmasi"):
        ok, reason = nmt_provider.nmt_route_reason(text, "id", "zh")
        assert not ok, (text, reason)


def test_corrected_glossary_entries_are_canonical_and_old_phrases_forbidden():
    glossary = json.loads((ROOT / "glossary_data.json").read_text(encoding="utf-8"))
    expected = {
        "標籤": "label produk",
        "研磨機": "mesin grinding",
        "工單訂單資訊「長度 MIN」": "Panjang MIN",
        "工單訂單資訊「長度 MAX」": "Panjang MAX",
        "工單製程紀錄「長度」": "Panjang",
    }
    for term, target in expected.items():
        row = glossary_policy.normalize_entry(term, glossary[term])
        assert glossary_policy.canonical_target(row) == target
        assert glossary_policy.translation_mode(row) == "hard"
    forbidden = {x.casefold() for x in glossary_policy.deprecated_indonesian_phrases()}
    assert "faktur pemesanan" in forbidden
    assert "mesin penghalus cetakkan" in forbidden


def _function_source(name):
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"function not found: {name}")


def test_line_result_buttons_and_postback_modes_are_integrated():
    row = _function_source("_flex_v2_button_row")
    button = _function_source("_translation_variant_button")
    handler = _function_source("handle_postback")
    assert "✨ 更自然" in row
    assert "🔎 直譯" in row
    assert "📢 正式" in row
    assert "↩ 回譯" in row
    assert "action=translation_variant" in button
    assert "_translation_variant_button" in row
    assert 'action == "translation_variant"' in handler
    for mode in ("natural", "literal", "formal", "backcheck"):
        assert f'"{mode}"' in handler
    assert "translate_openai" in handler
    assert "_final_delivery_guard" in handler


def test_audio_tts_is_nonblocking_and_text_file_translation_uses_main_pipeline():
    audio = _function_source("handle_audio")
    file_handler = _function_source("handle_file")
    assert "_threading.Thread" in audio
    assert "push_tts_message" in audio
    assert "get_tts_enabled" in audio
    image_background = _function_source("_handle_image_background")
    assert "if _overlay_qr:\n                            msg_obj.quick_reply = _overlay_qr\n                        _img_sender" in image_background
    assert "api.push_message(PushMessageRequest" in image_background
    assert "translate(part, lang, actual_tgt)" in file_handler
    assert "_TEXT_FILE_EXTENSIONS" in (ROOT / "app.py").read_text(encoding="utf-8")
    assert "quality_gate_critical" in file_handler
