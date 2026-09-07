"""The photographed notice, with natural paraphrases at real runtime boundaries.

Only external AI/LINE transports are replaced. No paid requests or LINE
messages are made; a fixed, preferred translation is not a production shortcut.
"""
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

import ai_provider
import app
import factory_pmi_semantics as pmi
import translation_retry_queue as queue
from test_translation_instruction_cost_quality import offline_transport, response
from test_translation_notice_availability import (
    SOURCE, TARGET, runtime, event, delivered_text, retry_pending,
)


NOTICE = SOURCE.split("\n\n", 1)[1].replace("不太會", "不大會")
TRANSLATION = TARGET.split("\n\n", 1)[1]
REAL_TRANSLATE_OPENAI = app.translate_openai

NATURAL_VARIANTS = [
    ("Pemeriksaan PMI wajib dilakukan", "PMI wajib dites"),
    ("Pemeriksaan PMI wajib dilakukan", "PMI harus selalu dilakukan"),
    ("Pemeriksaan PMI wajib dilakukan", "Pemeriksaan PMI untuk memastikan jenis baja wajib dilakukan"),
    ("Pemeriksaan PMI wajib dilakukan", "PMI untuk memeriksa jenis baja wajib dilakukan"),
    ("Pemeriksaan PMI wajib dilakukan", "PMI wajib tetap diperiksa"),
    ("Pemeriksaan PMI wajib dilakukan", "Pengujian PMI harus dilaksanakan"),
    ("Pemeriksaan PMI wajib dilakukan", "Wajib melakukan inspeksi PMI"),
    ("memeriksa rekaman CCTV", "mengambil rekaman CCTV"),
    ("memeriksa rekaman CCTV", "meminta rekaman CCTV"),
    ("mengawasi waktu masuk gudang", "memperhatikan waktu masuk gudang"),
    ("bukan untuk bulan ini", "non-bulan berjalan"),
]


@pytest.fixture(autouse=True)
def clean_context():
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(previous)


@pytest.mark.parametrize("old,new", NATURAL_VARIANTS)
def test_natural_notice_passes_provider_and_final_boundary(old, new):
    target = TRANSLATION.replace(old, new)
    assert target != TRANSLATION
    app._tl.semantic_contract = app.build_translation_semantic_contract(NOTICE, "zh", "id")
    validate = app._build_translation_response_validator(NOTICE, "zh", "id")
    assert validate(response(target), "offline") == (True, "ok")
    assert app._final_delivery_guard(NOTICE, target, "zh", "id") == target


@pytest.mark.parametrize("target", [
    "PMI tidak perlu dilakukan.", "PMI boleh dilewati.",
    "Jangan melakukan PMI.", "PMI wajib dicetak pada label.",
    "PMI sudah dilakukan.", "PMI belum dilakukan.",
])
def test_mandatory_inspection_cannot_become_optional_printed_or_a_status(target):
    source = "PMI一定要檢測。"
    app._tl.semantic_contract = app.build_translation_semantic_contract(source, "zh", "id")
    assert not app._build_translation_response_validator(source, "zh", "id")(response(target), "offline")[0]
    assert app._final_delivery_guard(source, target, "zh", "id") is None


@pytest.mark.parametrize("source,target", [
    ("PMI還沒做。", "PMI belum pernah diperiksa."),
    ("PMI已經檢查完了。", "PMI sudah selesai diperiksa."),
    ("PMI一定要檢測。", "PMI mesti diuji."),
    ("PMI一定要檢測。", "Pastikan PMI dilakukan."),
    ("一定要做PMI。", "Pastikan untuk melakukan PMI."),
    ("PMI一定要檢測。", "Lakukan pemeriksaan PMI."),
    ("PMI一定要檢測。", "Periksa PMI."),
    ("PMI一定要檢測。", "Harap selalu melakukan PMI."),
    ("PMI一定要檢測。", "Pemeriksaan PMI jangan sampai terlewat."),
    ("PMI一定要檢測。", "PMI tidak boleh dilewatkan."),
    ("PMI一定要檢測。", "Jangan melewatkan pemeriksaan PMI."),
    ("PMI一定要檢測。", "Jangan lewatkan PMI."),
])
def test_pmi_aspect_and_modal_grammar(source, target):
    assert pmi.validate(pmi.build_facts(source, "zh"), target, "id") == []


def test_inspection_requirement_cannot_borrow_another_items_modal():
    facts = pmi.build_facts("1. PMI一定要檢測。\n2. PMI已經做完。", "zh")
    assert pmi.validate(facts, "1. PMI sudah dilakukan.\n2. PMI harus dilakukan.", "id")


def test_indonesian_imperative_accepts_a_direct_chinese_instruction():
    facts = pmi.build_facts("Lakukan pemeriksaan PMI.", "id")
    assert pmi.validate(facts, "做PMI檢查。", "zh") == []
    assert pmi.validate(facts, "已經做PMI檢查了。", "zh")


def _signed_webhook(text):
    body = json.dumps({"destination": "offline-bot", "events": [{
        "type": "message", "mode": "active", "timestamp": 1788739140000,
        "webhookEventId": "notice-webhook", "replyToken": "reply",
        "deliveryContext": {"isRedelivery": False},
        "source": {"type": "group", "groupId": "notice-group", "userId": "supervisor"},
        "message": {"type": "text", "id": "notice-message", "text": text,
                    "quoteToken": "quote"},
    }]}, ensure_ascii=False)
    # Use the SDK's actual parser and signature verifier, not handle_message()
    # directly, so routing and durable event wrappers participate in the test.
    secret = app.handler.parser.signature_validator.channel_secret
    if isinstance(secret, str):
        secret = secret.encode()
    signature = base64.b64encode(hmac.new(secret, body.encode(), hashlib.sha256).digest()).decode()
    return body, signature


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
@pytest.mark.parametrize("reply_down", [False, True])
@pytest.mark.parametrize("pmi_wording", ["PMI harus selalu dilakukan", "Pemeriksaan PMI untuk memastikan jenis baja wajib dilakukan"])
def test_signed_webhook_with_real_provider_pipeline_delivers_once(runtime, offline_transport, monkeypatch, reply_down, provider, pmi_wording):
    target = TRANSLATION.replace("Pemeriksaan PMI wajib dilakukan", pmi_wording)
    calls = []
    monkeypatch.setattr(app, "translate_openai", REAL_TRANSLATE_OPENAI)
    offline_transport["active_provider"] = provider
    offline_transport["provider_failover"] = False
    def create(**kwargs):
        calls.append(kwargs)
        if provider == "anthropic":
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=target, citations=None)],
                stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                    cache_read_input_tokens=0, cache_creation_input_tokens=0))
        return response(target)
    transport = SimpleNamespace(messages=SimpleNamespace(create=create),
                                chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    # Keep the actual coordinator and all three SDK adapters. Replace only the
    # last network boundary; unexpected failover must never contact a real API.
    for name in ("_get_anthropic_client", "_get_openai_client", "_get_gemini_client"):
        monkeypatch.setattr(ai_provider, name, lambda: transport)
    monkeypatch.setattr(ai_provider, "_client_with_limits", lambda client, _timeout: client)
    monkeypatch.setattr(app, "get_recent_media_scene", lambda *_a, **_k: "")
    monkeypatch.setattr(app, "get_conv_context_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr(app.al_module, "assess_review_risk", lambda *_a, **_k: {"requires_review": False, "matches": []})
    runtime.reply_down = reply_down
    body, signature = _signed_webhook(NOTICE)
    client = app.app.test_client()
    result = client.post("/callback", data=body, headers={"X-Line-Signature": signature}, content_type="application/json")
    assert result.status_code == 200
    assert target in delivered_text(runtime)
    assert len(calls) == 1
    assert len(runtime.sends) == 1
    assert runtime.sends[0][0] == ("push" if reply_down else "reply")
    assert queue.pending_count() == 0
    client.post("/callback", data=body, headers={"X-Line-Signature": signature}, content_type="application/json")
    assert len(calls) == len(runtime.sends) == 1


def test_reply_transport_has_a_deadline_before_push_recovery(runtime, monkeypatch):
    attempts = []
    original_api = app.MessagingApi
    def api(client):
        result = original_api(client)
        def reply(request, **kwargs):
            attempts.append(kwargs)
            assert kwargs.get("_request_timeout") == (5, 15)
            raise TimeoutError("bounded LINE read timeout")
        result.reply_message = reply
        return result
    monkeypatch.setattr(app, "MessagingApi", api)
    runtime.provider_result = TRANSLATION
    app.handle_message(event(NOTICE))
    assert len(attempts) == 1
    assert attempts[0]["_request_timeout"] == (5, 15)
    assert runtime.sends[0][0] == "push"
    assert TRANSLATION in delivered_text(runtime)
    assert queue.pending_count() == 0


def test_corrected_pmi_candidate_recovers_an_already_pending_notice(runtime):
    runtime.provider_down = True
    app.handle_message(event(NOTICE))
    assert not runtime.sends
    runtime.provider_down = False
    runtime.provider_result = TRANSLATION.replace("Pemeriksaan PMI wajib dilakukan", "PMI wajib dites")
    assert retry_pending()
    assert runtime.provider_result in delivered_text(runtime)
    assert queue.pending_count() == 0
