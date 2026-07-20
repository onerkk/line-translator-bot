import io
import json
import time
from types import SimpleNamespace

from PIL import Image

import app
import translation_extras


def _fake_chat_response(payload):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)),
            finish_reason="stop",
        )],
        usage=None,
    )


def test_personal_language_normalization_and_command(monkeypatch):
    assert translation_extras.normalize_personal_language("繁體中文") == "zh"
    assert translation_extras.normalize_personal_language("Indonesian") == "id"
    assert translation_extras.normalize_personal_language("tl") == "tl"
    assert translation_extras.normalize_personal_language("unknown") is None

    monkeypatch.setattr(app, "save_settings", lambda *args, **kwargs: None)
    uid = "U_TEST_PERSONAL_LANG"
    app.user_languages.pop(uid, None)
    app.dm_target_lang.pop(uid, None)
    result = app.handle_personal_language_command("/mylang vi", uid)
    assert "越南" in result
    assert app.user_languages[uid] == "vi"
    assert app.dm_target_lang[uid] == "vi"
    app.user_languages.pop(uid, None)
    app.dm_target_lang.pop(uid, None)


def test_handover_prompt_compaction_and_summary(monkeypatch):
    now = time.time()
    entries = [
        {
            "timestamp": now - 60,
            "sender": "A",
            "source_language": "zh",
            "source_text": "I9先停機，等品保確認後再開。",
            "translations": {"id": "Hentikan I9 dulu dan nyalakan kembali setelah QC mengonfirmasi."},
        },
        {
            "timestamp": now - 30,
            "sender": "B",
            "source_language": "id",
            "source_text": "R28.57 belum selesai, berat 1250kg.",
            "translations": {"zh": "R28.57 尚未完成，重量 1250kg。"},
        },
    ]
    compact = translation_extras.compact_handover_entries(entries)
    messages = translation_extras.build_handover_messages(compact)
    assert "I9" in messages[1]["content"]
    assert "1250kg" in messages[1]["content"]
    assert "Never invent" in messages[0]["content"]

    parsed = translation_extras.parse_handover_response(
        '```json\n{"zh":"I9停機待品保確認。","id":"I9 dihentikan dan menunggu konfirmasi QC."}\n```'
    )
    assert parsed and parsed["zh"].startswith("I9")

    gid = "C_TEST_HANDOVER"
    app._recent_group_messages[gid] = entries
    fake = _fake_chat_response({
        "zh": "【設備】I9停機，待品保確認後再開。\n【未完成】R28.57，1250kg。",
        "id": "【Peralatan】I9 dihentikan, tunggu konfirmasi QC.\n【Belum selesai】R28.57, 1250kg.",
    })
    monkeypatch.setattr(app.ai.chat.completions, "create", lambda **kwargs: fake)
    monkeypatch.setattr(app, "track_tokens", lambda response: None)
    result = app.build_group_handover_summary(gid, hours=12)
    assert "交班摘要" in result
    assert "I9" in result and "1250kg" in result
    app._recent_group_messages.pop(gid, None)


def test_translation_comparison_image_is_valid_jpeg():
    original = Image.new("RGB", (420, 260), "white")
    buf = io.BytesIO()
    original.save(buf, format="PNG")
    rendered = translation_extras.render_translation_comparison_image(
        buf.getvalue(),
        "I9先停機\n重量1250kg",
        "Hentikan I9 terlebih dahulu\nBerat 1250kg",
        source_label="原文 🇹🇼",
        target_label="譯文 🇮🇩",
    )
    assert rendered.startswith(b"\xff\xd8")
    with Image.open(io.BytesIO(rendered)) as out:
        assert out.format == "JPEG"
        assert out.width > original.width
        assert out.height >= original.height


def test_overlay_store_and_media_route(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_image_overlay_dir", lambda: str(tmp_path))
    original = Image.new("RGB", (200, 100), "white")
    raw = io.BytesIO()
    original.save(raw, format="PNG")
    token = app._store_image_overlay_context(raw.getvalue(), "原文", "譯文", "zh", "id", "C1")
    assert token
    context = app._load_image_overlay_context(token, "C1")
    assert context and context["translated_text"] == "譯文"
    rendered = translation_extras.render_translation_comparison_image(
        context["image_bytes"], context["source_text"], context["translated_text"]
    )
    media_token = app._store_generated_overlay_image(rendered)
    client = app.app.test_client()
    response = client.get(f"/media/translation/{media_token}.jpg")
    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"


def test_browser_audio_suffix_and_transcription_tempfile(monkeypatch):
    assert app._audio_upload_suffix("speech.webm", "audio/webm;codecs=opus") == ".webm"
    assert app._audio_upload_suffix("", "audio/mp4") == ".mp4"
    captured = {}

    class FakeTranscriptions:
        @staticmethod
        def create(model, file, **kwargs):
            captured["name"] = file.name
            return SimpleNamespace(text="I9先停機")

    fake_oai = SimpleNamespace(audio=SimpleNamespace(transcriptions=FakeTranscriptions()))
    monkeypatch.setattr(app, "oai", fake_oai)
    monkeypatch.setattr(app.ai_provider, "get_active_provider", lambda: "openai")
    result = app.transcribe_audio_openai(b"fake-webm", suffix=".webm")
    assert result == "I9先停機"
    assert captured["name"].endswith(".webm")


def test_interpreter_page_and_api(monkeypatch):
    client = app.app.test_client()
    page = client.get("/liff/settings?view=interpreter&nonce=test")
    assert page.status_code == 200
    assert "即時雙向口譯" in page.get_data(as_text=True)
    assert "MediaRecorder" in page.get_data(as_text=True)

    nonce = app.issue_liff_nonce("C_INTERPRETER", "U_INTERPRETER")
    monkeypatch.setattr(app, "transcribe_audio_openai", lambda raw, **kwargs: "I9先停機")
    monkeypatch.setattr(app, "detect_language", lambda text: "zh")
    monkeypatch.setattr(app, "translate", lambda text, src, tgt: "Hentikan I9 terlebih dahulu")
    monkeypatch.setattr(app, "_final_delivery_guard", lambda source, result, src, tgt: result)
    monkeypatch.setattr(app, "generate_tts", lambda text, lang: ("https://example.invalid/a.m4a", 1000))
    response = client.post(
        "/api/interpreter/translate",
        data={
            "nonce": nonce,
            "source": "zh",
            "target": "id",
            "speak": "1",
            "audio": (io.BytesIO(b"fake-audio"), "speech.webm"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["translation"] == "Hentikan I9 terlebih dahulu"
    assert data["audio_url"].endswith(".m4a")


def test_translation_flex_has_personal_language_button():
    row = app._flex_v2_button_row(
        "C_TEST", "I9先停機", "Hentikan I9", "id", "M1", "zh"
    )
    labels = []
    for content in row["contents"]:
        for button in content.get("contents", []):
            label = button.get("action", {}).get("label")
            if label:
                labels.append(label)
    assert "👤 我的語言/Bahasa" in labels
