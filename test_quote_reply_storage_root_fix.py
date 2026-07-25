import ast
import json
import re
from pathlib import Path

import factory_translation_guard as guard
from line_quote_context import get_quote_token, get_quoted_message_id, resolve_quote_context


SOURCE = "週末大成儲格能放就放，放不下再放照片裡這些位置。"
TARGET = (
    "Pada akhir pekan, jika slot penyimpanan 大成 masih dapat menampung material, "
    "letakkan material di sana. Jika tidak muat, letakkan material di lokasi-lokasi "
    "yang ditunjukkan pada foto-foto ini."
)

STORAGE_SOURCE = "入儲時EH33峰作金屬集中放這格"
STORAGE_TARGET = (
    "Saat material masuk ke area penyimpanan, semua material EH33 milik 峰作金屬 "
    "ditempatkan bersama di slot ini."
)


class SnakeMessage:
    quoted_message_id = "m-old"
    quote_token = "q-current"


class AliasDumpMessage:
    def model_dump(self, by_alias=False):
        if by_alias:
            return {"quotedMessageId": "m-alias", "quoteToken": "q-alias"}
        return {"quoted_message_id": "m-alias", "quote_token": "q-alias"}


def test_quote_metadata_supports_sdk_attribute_and_alias_shapes():
    assert get_quoted_message_id(SnakeMessage()) == "m-old"
    assert get_quote_token(SnakeMessage()) == "q-current"
    assert get_quoted_message_id(AliasDumpMessage()) == "m-alias"
    assert get_quote_token(AliasDumpMessage()) == "q-alias"
    assert get_quoted_message_id({"quotedMessageId": "m-dict"}) == "m-dict"


def test_quote_context_is_optional_and_never_replaces_current_message():
    cache = {
        "m-old": {
            "text": "大成週一抓帳，還有160噸會陸續到料。",
            "tr": {"id": "大成 akan melakukan tutup buku pada hari Senin."},
        }
    }
    zh_ctx = resolve_quote_context(SnakeMessage(), cache, source_language="zh")
    assert zh_ctx["quoted_message_id"] == "m-old"
    assert zh_ctx["context_text"].startswith("大成週一抓帳")

    id_ctx = resolve_quote_context(SnakeMessage(), cache, source_language="id")
    assert id_ctx["context_text"].startswith("大成 akan melakukan")

    missing = resolve_quote_context({"quotedMessageId": "not-cached"}, cache)
    assert missing["quoted_message_id"] == "not-cached"
    assert missing["context_text"] == ""
    assert missing["entry"] is None


def test_exact_factory_translation_for_reported_message_is_authoritative():
    guard.reload()
    assert guard.exact_verified_target(SOURCE, "zh", "id") == TARGET
    # Punctuation/spacing differences remain exact-addressable.
    assert guard.exact_verified_target(
        "週末 大成儲格能放就放；放不下，再放照片裡這些位置",
        "zh",
        "id",
    ) == TARGET


def test_storage_placement_translation_passes_and_known_bad_meanings_fail():
    guard.reload()
    good = guard.validate_translation(SOURCE, TARGET, "zh", "id")
    assert good.ok, good.issues

    for bad in (
        "Pada akhir pekan, jika data Besar bisa di-release, lakukan release data.",
        "Jika tidak muat, unggah foto ke lokasi ini.",
        "Simpan data Besar pada akhir pekan.",
    ):
        report = guard.validate_translation(SOURCE, bad, "zh", "id")
        assert not report.ok, bad


def test_reported_eh33_storage_instruction_is_natural_and_preserves_customer_name():
    guard.reload()
    assert guard.exact_verified_target(STORAGE_SOURCE, "zh", "id") == STORAGE_TARGET
    good = guard.validate_translation(STORAGE_SOURCE, STORAGE_TARGET, "zh", "id")
    assert good.ok, good.issues

    bad = (
        "Saat masuk gudang, EH33 punya Peng Zuo Metal (峰作金屬) "
        "dikumpulkan taruh di lokasi ini."
    )
    report = guard.validate_translation(STORAGE_SOURCE, bad, "zh", "id")
    assert not report.ok


def test_assets_contain_storage_slot_physical_placement_contract():
    root = Path(__file__).resolve().parent
    knowledge = json.loads((root / "factory_knowledge.json").read_text(encoding="utf-8"))
    card = next(
        row for row in knowledge["entries"]
        if row["id"] == "customer_storage_slot_photo_overflow_placement"
    )
    joined = json.dumps(card, ensure_ascii=False)
    assert "slot penyimpanan" in joined
    assert "release data" in joined
    assert "大成" in joined

    storage_card = next(
        row for row in knowledge["entries"]
        if row["id"] == "customer_eh33_concentrated_storage_slot"
    )
    storage_joined = json.dumps(storage_card, ensure_ascii=False)
    assert "峰作金屬" in storage_joined
    assert "Peng Zuo Metal" in storage_joined
    assert "ditempatkan bersama di slot ini" in storage_joined

    glossary = json.loads((root / "glossary_data.json").read_text(encoding="utf-8"))
    assert glossary["峰作金屬"]["canonical_idn"] == "峰作金屬"
    assert glossary["入儲"]["canonical_idn"] == "masuk ke area penyimpanan"


def test_actual_app_deterministic_rules_cover_both_reported_messages_without_inventing_weekend():
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    fn = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "factory_semantic_translate_zh_id"
    )
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "re": re,
        "_factory_reason_semantic_translate_zh_id": lambda _text: None,
    }
    exec(compile(module, "<factory_semantic_translate_zh_id>", "exec"), namespace)
    translate_known = namespace["factory_semantic_translate_zh_id"]

    assert translate_known(SOURCE) == TARGET
    assert translate_known(STORAGE_SOURCE) == STORAGE_TARGET
    # The deterministic fallback must not add a weekend that the source omitted.
    assert translate_known("大成儲格能放就放，放不下再放照片裡這些位置") is None


def test_app_does_not_silently_drop_rejected_quote_translation_or_attach_quote_to_flex():
    source = Path("app.py").read_text(encoding="utf-8")
    assert 'translate_empty_visible_notice' in source
    assert 'kind="translation"' in source
    assert "flex_msg.quote_token" not in source
    assert "quoted_context_source" in source
    assert "getattr(event.message, 'quote_token', None)" not in source
    assert "Translate only the " in source and "current user message" in source
