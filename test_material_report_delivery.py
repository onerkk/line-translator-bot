"""Reported missing translation: real routing/guards/queue, offline transports."""
import json
import pytest
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent

import app
import factory_structured_report as reports
import translation_quality_gate as quality
import translation_retry_queue as queue
import webhook_runtime as webhooks
from test_translation_notice_availability import runtime, event, delivered_text, retry_pending
from test_webhook_latency_recovery import SECRET, signed, sign

SOURCE = "R: 14.47mm\nPanjang 6040\nBerat 1377"
TARGET = "R：14.47mm\n長度：6040\n重量：1377"


@pytest.fixture(autouse=True)
def clear_request_context():
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(previous)


def test_report_delivers_with_all_ai_providers_unavailable(runtime):
    runtime.provider_down = True
    assert app.detect_language(SOURCE) == "id"
    app.handle_message(event(SOURCE))
    text = delivered_text(runtime)
    assert "長度：6040" in text and "重量：1377" in text and "R：14.47mm" in text
    assert not runtime.generations
    assert queue.pending_count() == 0


def test_report_cannot_invent_units_or_swap_field_values():
    for wrong in [TARGET.replace("6040", "1377").replace("重量：1377", "重量：6040"),
                  TARGET.replace("6040", "6040mm"), TARGET.replace("1377", "1377kg"),
                  TARGET.replace("R：", "半徑："), TARGET.replace("R：", "直徑：")]:
        assert not quality.validate_translation(SOURCE, wrong, "id", "zh").ok


@pytest.mark.parametrize("source,expected", [
    (SOURCE, TARGET),
    ("r = 18,20 mm; pANJAng: 5800; BERAT=1200", "R：18,20mm\n長度：5800\n重量：1200"),
    ("\tPanjang\t6040\r\n\r\nBerat\t1377\t", "長度：6040\n重量：1377"),
    ("Panjang6040\nBerat1377", "長度：6040\n重量：1377"),
    ("I15\nDiameter 12.50mm\nPanjang 6000mm\nBerat 1.234,50kg",
     "I15\n直徑：12.50mm\n長度：6000mm\n重量：1.234,50kg"),
    ("Panjang 5.8m\nBerat 0.85ton\nJumlah batang 17", "長度：5.8m\n重量：0.85ton\n支數：17"),
    ("Ukuran 19.3 mm\nLebar 30mm\nKetebalan 2mm", "尺寸：19.3mm\n寬度：30mm\n厚度：2mm"),
    ("Berat 0kg", "重量：0kg"),
    ("Panjang +6040mm\nBerat -1,25kg", "長度：+6040mm\n重量：-1,25kg"),
    ("Jumlah 12 batang", "數量：12支"),
])
def test_field_grammar_generalizes_without_number_or_unit_inference(source, expected):
    assert reports.translate_material_report(source, "id", "zh") == expected
    assert quality.validate_translation(source, expected, "id", "zh").ok


@pytest.mark.parametrize("source,expected", [
    (TARGET, "R: 14.47 mm\nPanjang: 6040\nBerat: 1377"),
    ("長度6040公釐 重量1377公斤 支數12", "Panjang: 6040 mm\nBerat: 1377 kg\nJumlah batang: 12"),
    ("长度6000毫米；重量+682.5公斤", "Panjang: 6000 mm\nBerat: +682.5 kg"),
])
def test_same_contract_works_in_reverse(source, expected):
    assert reports.translate_material_report(source, "zh", "id") == expected
    assert not reports.validate_material_report(source, expected, "zh", "id")


@pytest.mark.parametrize("source", [
    "R: 14.47mm", "Panjang 6040?", "Panjang 6040\nBerat 1377\nJangan produksi dulu.",
    "Panjang 6040\nBerat 1377\nOperator Budi", "Panjang 6040\nPanjang 6050",
    "Panjang 6040 atau 6050", "Panjang 6040 lbs", "Berat turun 1377", "Belum 6040",
    "Panjang 6040\nXYZ 1377", "@All\nPanjang 6040", "Panjang " + "1" * 4000,
])
def test_unparsed_or_ambiguous_content_never_becomes_a_partial_local_translation(source):
    assert reports.translate_material_report(source, "id", "zh") is None


@pytest.mark.parametrize("wrong", [
    "R：14.47mm\n長度：1377\n重量：6040",
    "R：14.47mm\n長度：6040", "長度：6040\n重量：1377",
    "R：14.47mm\n長度：6040\n重量：1377\n支數：17",
    "R：14.47cm\n長度：6040\n重量：1377",
    "R：14.47mm\n長度：6040\n重量：1377kg",
])
def test_all_delivery_and_cache_boundaries_reject_corrupt_material_fields(runtime, wrong):
    assert not quality.validate_translation(SOURCE, wrong, "id", "zh").ok
    assert app._final_delivery_guard(SOURCE, wrong, "id", "zh") is None
    app.cache_set(SOURCE, "id", "zh", wrong)
    assert app.cache_get(SOURCE, "id", "zh") is None


@pytest.mark.parametrize("colon", [":", "：", "="])
def test_preserved_r_field_is_not_mistaken_for_untranslated_prose(colon):
    candidate = TARGET.replace("R：", "R" + colon)
    assert quality.validate_translation(SOURCE, candidate, "id", "zh").ok
    assert app._final_delivery_guard(SOURCE, candidate, "id", "zh") == candidate
    # Ordinary Indonesian still requires translation even in an all-caps report.
    assert not quality.validate_translation(SOURCE, TARGET.replace("重量", "BERAT"), "id", "zh").ok


def test_report_with_followup_instruction_uses_full_translation(runtime):
    source = "Panjang 6040\nBerat 1377\nJangan produksi dulu."
    runtime.provider_result = "長度：6040\n重量：1377\n先不要生產。"
    app.handle_message(event(source))
    assert "先不要生產" in delivered_text(runtime)
    assert len(runtime.generations) == 1


@pytest.mark.parametrize("blocked_by", ["group_off", "skip_user"])
def test_material_fast_path_respects_existing_group_controls(runtime, monkeypatch, blocked_by):
    if blocked_by == "group_off":
        monkeypatch.setitem(app.group_settings, "notice-group", False)
    else:
        monkeypatch.setitem(app.group_skip_users, "notice-group", {"supervisor"})
    app.handle_message(event(SOURCE))
    assert not runtime.sends and not runtime.generations and queue.pending_count() == 0


def test_line_outage_retains_completed_report_and_retry_avoids_ai(runtime):
    runtime.provider_down = runtime.reply_down = runtime.push_down = True
    original = event(SOURCE)
    with pytest.raises(TimeoutError):
        app.handle_message(original)
    job = queue.list_pending()[0]
    assert TARGET in job["payload"]["delivery"]["text"]
    assert not runtime.generations
    runtime.push_down = False
    retry_pending()
    assert "長度：6040" in delivered_text(runtime)
    assert queue.pending_count() == 0 and not runtime.generations
    count = len(runtime.sends)
    app.handle_message(original)
    assert len(runtime.sends) == count


def test_material_revision_invalidates_previously_cached_answers(runtime, monkeypatch):
    app.cache_set(SOURCE, "id", "zh", TARGET)
    assert app.cache_get(SOURCE, "id", "zh") == TARGET
    monkeypatch.setattr(reports, "FACTORY_STRUCTURED_REPORT_BUILD_ID", "next-material-contract")
    assert app.cache_get(SOURCE, "id", "zh") is None


def test_existing_verified_translation_keeps_priority_over_local_report_rendering(runtime, monkeypatch):
    approved = "重量：1377；長度：6040；R: 14.47mm"
    monkeypatch.setattr(app.factory_translation_guard_module, "exact_verified_target", lambda *_a: approved)
    assert app.translate(SOURCE, "id", "zh") == approved
    assert not runtime.generations


def test_volatile_unfinished_translation_is_not_acknowledged(monkeypatch, tmp_path):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "volatile.db"))
    handler = WebhookHandler(SECRET)
    @handler.add(MessageEvent, message=TextMessageContent)
    def translate_later(message):
        queue.enqueue("Ctest:1001", {"group_id": "Ctest", "message_id": "1001",
                                    "source_text": message.message.text}, job_kind="text")
    inbox = webhooks.WebhookInbox(handler, lambda body: None)
    monkeypatch.setattr(app, "_WEBHOOK_INBOX", inbox)
    body, signature = signed(text=SOURCE)
    result = app.app.test_client().post("/callback", data=body,
                                       headers={"X-Line-Signature": signature})
    assert result.status_code == 503
    assert queue.get("Ctest:1001") is not None


@pytest.mark.parametrize("key,expected", [
    ("Ctest:1001", True), ("Ctest:1001:image", True), ("Ctest:1001:edit:abc", True),
    ("Ctest:10010", False), ("Cother:1001", False), ("Ctest:100", False),
])
def test_pending_source_matches_exact_message_and_media_revision_suffixes(monkeypatch, tmp_path, key, expected):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "queue.db"))
    queue.enqueue(key, {"source_text": SOURCE})
    assert queue.has_pending_source("Ctest", "1001") is expected
    assert not queue.has_pending_source("Ctest", "")


def test_actual_callback_stays_retryable_until_failed_send_recovers(runtime, monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    handler = WebhookHandler(SECRET)
    handler.add(MessageEvent, message=TextMessageContent)(app.handle_message)
    inbox = webhooks.WebhookInbox(handler, app._release_webhook_message_claims)
    monkeypatch.setattr(app, "_WEBHOOK_INBOX", inbox)
    body = json.loads(signed(text=SOURCE)[0])
    body["events"][0]["source"]["groupId"] = "notice-group"
    body = json.dumps(body, ensure_ascii=False)
    runtime.provider_down = runtime.reply_down = runtime.push_down = True
    def post(value):
        return app.app.test_client().post("/callback", data=value,
                                         headers={"X-Line-Signature": sign(value)})
    # An uncaught transport exception keeps the existing HTTP 500 contract;
    # a completed handler with pending work uses the new explicit HTTP 503.
    assert post(body).status_code == 500
    assert queue.pending_count() == 1
    # A redelivery while the job is pending neither creates a second job nor
    # regenerates the already prepared translation.
    payload = json.loads(body)
    payload["events"][0]["deliveryContext"]["isRedelivery"] = True
    redelivery = json.dumps(payload, ensure_ascii=False)
    assert post(redelivery).status_code == 503 and queue.pending_count() == 1
    runtime.push_down = False
    key = "notice-group:1001"
    assert queue.claim_job(key, owner="recovery")
    assert app._run_translation_retry_job(queue.get(key), "recovery")
    assert post(redelivery).status_code == 200
    assert len(runtime.sends) == 1 and not runtime.generations


def test_other_chats_pending_work_does_not_prevent_ack(monkeypatch, tmp_path):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "queue.db"))
    queue.enqueue("Cother:1001", {"source_text": SOURCE})
    inbox = webhooks.WebhookInbox(WebhookHandler(SECRET), lambda body: None)
    body, signature = signed(text=SOURCE)
    assert inbox.accept(body, signature) is None


def test_synchronous_persistent_queue_can_ack_pending_work(monkeypatch, tmp_path):
    monkeypatch.setenv("LINE_WEBHOOK_ASYNC", "0")
    monkeypatch.setattr(webhooks, "persistent_outbox", lambda: True)
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "queue.db"))
    queue.enqueue("Ctest:1001", {"source_text": SOURCE})
    inbox = webhooks.WebhookInbox(WebhookHandler(SECRET), lambda body: None)
    body, signature = signed(text=SOURCE)
    assert inbox.accept(body, signature) is None
