import ast
import re
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent


SAMPLE_OCR = """ID | 原因
7H885503A | 改價格
7H847507 | 改價格
7H110003 | 判色
7G319512B | 加火
7G681208A | 削皮
7H347508 | 改TAG"""


def load_image_table_namespace():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted_assigns = {
        "_IMAGE_TABLE_CODE_RE",
        "_IMAGE_TABLE_HINT_RE",
        "_IMAGE_TABLE_SEPARATOR_RE",
        "_IMAGE_TABLE_META_RE",
    }
    wanted_defs = {
        "_extract_sensitive_table_codes",
        "_looks_like_sensitive_ocr_table",
        "_has_cjk_text",
        "_validate_sensitive_image_ocr",
        "extract_sensitive_image_table_text",
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
    ns = {
        "re": re,
        "logger": SimpleNamespace(
            warning=lambda *a, **k: None,
            info=lambda *a, **k: None,
        ),
        "oai": object(),
        "_tl": SimpleNamespace(group_id="G1"),
        "_build_cache_key": lambda *parts: "|".join(str(p) for p in parts),
        "track_tokens": lambda r: None,
    }
    exec(compile(module, str(ROOT / "app.py"), "exec"), ns)
    return ns


def _fake_response(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


def test_sensitive_ocr_table_detector_hits_erp_id_reason_table():
    ns = load_image_table_namespace()
    assert ns["_looks_like_sensitive_ocr_table"](SAMPLE_OCR) is True
    assert ns["_extract_sensitive_table_codes"](SAMPLE_OCR)[:3] == [
        "7H885503A",
        "7H847507",
        "7H110003",
    ]


def test_sensitive_ocr_table_detector_ignores_plain_chat():
    ns = load_image_table_namespace()
    plain = "今天加班到八點\n麻煩通知印尼同仁"
    assert ns["_looks_like_sensitive_ocr_table"](plain) is False


def test_precision_image_table_ocr_keeps_source_text_not_translation():
    ns = load_image_table_namespace()
    captured = {}

    def fake_vision_call(messages, max_tokens, cache_key=None, task_type=None, reasoning_override=None):
        captured["messages"] = messages
        captured["max_tokens"] = max_tokens
        captured["cache_key"] = cache_key
        captured["task_type"] = task_type
        captured["reasoning_override"] = reasoning_override
        return _fake_response(
            "ID | 原因\n"
            "7H885503A | 改價格\n"
            "7H847507 | 改價格\n"
            "7H110003 | 判色\n"
            "7G319512B | 加火\n"
            "7G681208A | 削皮"
        )

    ns["_vision_call"] = fake_vision_call

    result = ns["extract_sensitive_image_table_text"](
        "BASE64DATA",
        mime_type="image/png",
        ocr_hint=SAMPLE_OCR,
    )

    assert "7H885503A" in result
    assert "原因" in result
    assert "改價格" in result
    assert "Alasan" not in result
    assert "Ubah harga" not in result
    assert "Nomor Material" not in result
    assert "Sedang" not in result
    assert captured["max_tokens"] == 3000
    assert captured["task_type"] == "ocr"
    assert captured["reasoning_override"] == "minimal"

    system_prompt = captured["messages"][0]["content"]
    user_content = captured["messages"][1]["content"]
    assert "OCR ONLY" in system_prompt
    assert "Do NOT translate" in system_prompt
    assert "原因 stays 原因" in system_prompt
    assert "UNTRUSTED OCR HINT" in user_content[1]["text"]
    assert user_content[0]["image_url"]["url"].startswith("data:image/png;base64,BASE64DATA")


def test_precision_image_table_ocr_rejects_translated_output():
    ns = load_image_table_namespace()
    ns["_vision_call"] = lambda *a, **k: _fake_response(
        "ID | Alasan\n7H885503A | Ubah harga\n7H847507 | Ubah harga"
    )

    assert ns["extract_sensitive_image_table_text"](
        "BASE64DATA", mime_type="image/jpeg", ocr_hint=SAMPLE_OCR
    ) is None
