"""Fault-injection tests for lossless, resumable factory translation delivery."""
import concurrent.futures
import copy
import sqlite3
import time
from types import SimpleNamespace

import pytest

import ai_provider
import app
import line_translation_delivery as framing
import translation_retry_queue as queue


class LineError(Exception):
    def __init__(self, status, body="", headers=None):
        super().__init__(body)
        self.status, self.body, self.headers = status, body, headers or {}


class FakeClient:
    def __init__(self, *_a, **_k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "queue.db"))
    monkeypatch.setattr(app, "_ensure_translation_retry_worker", lambda: False)
    monkeypatch.setattr(app, "_TRANSLATION_RETRY_INFLIGHT", set())
    monkeypatch.setattr(app, "_translation_cache_asset_fingerprint", lambda: "verified-assets-v1")
    monkeypatch.setattr(app, "_stats_inc", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_event_log_write", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_group_tone", lambda _gid: ("natural", ""))
    monkeypatch.setattr(app, "ApiClient", FakeClient)
    before, delivery_before = dict(app._tl.__dict__), dict(app._translation_delivery_state.__dict__)
    app._tl.__dict__.clear()
    app._translation_delivery_state.__dict__.clear()
    calls = []

    class Api:
        on_push = None
        on_reply = None

        def push_message(self, request, **kwargs):
            calls.append(("push", copy.deepcopy(request), kwargs.copy()))
            if self.on_push:
                return self.on_push(request, kwargs)
            return SimpleNamespace(sent_messages=[])

        def reply_message(self, request, **kwargs):
            calls.append(("reply", copy.deepcopy(request), kwargs.copy()))
            if self.on_reply:
                return self.on_reply(request, kwargs)
            return SimpleNamespace(sent_messages=[])

    api = Api()
    monkeypatch.setattr(app, "MessagingApi", lambda _client: api)
    yield api, calls
    app._tl.__dict__.clear()
    app._tl.__dict__.update(before)
    app._translation_delivery_state.__dict__.clear()
    app._translation_delivery_state.__dict__.update(delivery_before)


def enqueue(key="group:msg", *, kind="text", **updates):
    payload = {"group_id": "group", "user_id": "worker", "message_id": "msg",
               "source_text": "停機檢查", "src_lang": "zh", "target_langs": ["id"],
               "job_kind": kind}
    payload.update(updates)
    queue.enqueue(key, payload, job_kind=kind)
    return payload


def run_job(key="group:msg", owner="worker"):
    assert queue.claim_job(key, owner=owner)
    return app._run_translation_retry_job(queue.get(key), owner)


@pytest.mark.parametrize("text", ["甲😀\n" * 15000, "a" * 60000, "\n" * 26000,
                                  "  leading\n\n尾端 \n", "🧑🏽‍🔧" * 5000])
def test_line_framing_keeps_every_character_and_utf16_limit(text):
    batches = framing.text_batches(text)
    assert "".join(chunk for batch in batches for chunk in batch) == text
    assert all(1 <= len(batch) <= 5 for batch in batches)
    assert all(framing.utf16_units(chunk) <= 4700 for batch in batches for chunk in batch)


def test_long_delivery_resumes_after_last_accepted_batch(runtime, monkeypatch):
    api, calls = runtime
    payload = enqueue()
    text = "完整譯文😀\n" * 10000
    pushes = 0

    def fail_second(_req, _kwargs):
        nonlocal pushes
        pushes += 1
        if pushes == 2:
            raise LineError(503, "temporarily unavailable")

    api.on_push = fail_second
    with pytest.raises(LineError):
        app._translation_retry_push("group:msg", payload, text)
    assert queue.get("group:msg")["payload"]["delivery"]["next_batch"] == 1
    api.on_push = None
    monkeypatch.setattr(app, "translate", lambda *_a, **_k: pytest.fail("paid regeneration on send retry"))
    assert run_job()
    accepted = [calls[0], *calls[2:]]
    assert "".join(msg.text for _, req, _ in accepted for msg in req.messages) == text
    assert calls[1][2]["x_line_retry_key"] == calls[2][2]["x_line_retry_key"]
    assert len({call[2]["x_line_retry_key"] for call in accepted}) == len(accepted)
    assert queue.get("group:msg") is None
    assert queue.was_delivered("group:msg")


def test_reply_timeout_then_acknowledged_push_reuses_prepared_translation(runtime, monkeypatch):
    api, calls = runtime
    enqueue()
    api.on_reply = lambda *_a: (_ for _ in ()).throw(TimeoutError("reply response lost"))
    api.on_push = lambda *_a: (_ for _ in ()).throw(TimeoutError("push response lost"))
    text = "🇮🇩 Hentikan mesin dan periksa."
    with pytest.raises(TimeoutError):
        app._send_reply_with_push_fallback(
            reply_token="token", target_id="group", message_obj=app.TextMessage(text="preview"),
            fallback_text=text, job_key="group:msg",
        )
    assert calls[0][2] == {}  # unsupported retry headers must not reach Reply
    assert queue.get("group:msg")["payload"]["delivery"]["text"] == text
    api.on_push = lambda *_a: (_ for _ in ()).throw(LineError(
        409, "already accepted", {"X-Line-Accepted-Request-Id": "accepted-id"}
    ))
    monkeypatch.setattr(app, "translate", lambda *_a, **_k: pytest.fail("must reuse completed translation"))
    assert run_job()
    assert calls[1][2]["x_line_retry_key"] == calls[2][2]["x_line_retry_key"]
    assert queue.pending_count() == 0


def test_unacknowledged_conflict_stays_pending(runtime):
    api, calls = runtime
    payload = enqueue()
    api.on_push = lambda *_a: (_ for _ in ()).throw(LineError(409, "conflict"))
    with pytest.raises(LineError):
        app._translation_retry_push("group:msg", payload, "譯文")
    assert len(calls) == 1
    assert queue.get("group:msg")["payload"]["delivery"]["next_batch"] == 0


def test_expired_quote_is_removed_only_for_quote_validation_error(runtime):
    api, calls = runtime
    payload = enqueue(quote_token="expired-quote")

    def reject_quote(req, _kwargs):
        if req.messages[0].quote_token:
            raise LineError(400, '{"details":[{"property":"messages[0].quoteToken"}]}')

    api.on_push = reject_quote
    app._translation_retry_push("group:msg", payload, "安全完整譯文")
    assert len(calls) == 2
    assert calls[0][1].messages[0].quote_token == "expired-quote"
    assert calls[1][1].messages[0].quote_token is None
    assert calls[0][2]["x_line_retry_key"] == calls[1][2]["x_line_retry_key"]


def test_partial_multilingual_success_retries_only_missing_languages(runtime, monkeypatch):
    _, calls = runtime
    enqueue(target_langs=["id", "en"])
    targets_seen = []

    def translate_multi(_text, _src, targets, _mentions):
        targets_seen.append(list(targets))
        return [("id", "Hentikan mesin.")] if "id" in targets else [("en", "Stop the machine.")]

    monkeypatch.setattr(app, "translate_multi", translate_multi)
    assert run_job()
    pending = queue.get("group:msg")
    assert pending["payload"]["target_langs"] == ["en"]
    assert pending["status"] == "pending"
    assert run_job(owner="worker-2")
    assert targets_seen == [["id", "en"], ["en"]]
    assert len(calls) == 2
    assert calls[0][2]["x_line_retry_key"] != calls[1][2]["x_line_retry_key"]
    assert queue.pending_count() == 0


def test_foreground_work_is_leased_until_exit_and_then_recoverable(runtime):
    with app._translation_job_scope():
        assert app._schedule_text_translation_retry(
            {"group_id": "group", "message_id": "msg"}, source_text="停機",
            src_lang="zh", target_langs=["id"], delay_seconds=90,
        )
        row = queue.get("group:msg")
        assert row["status"] == "leased"
        assert row["lease_owner"].startswith("foreground-")
        assert queue.claim_due_jobs(owner="other", now=time.time() + 91) == []
    assert queue.get("group:msg")["status"] == "pending"


def test_expired_or_released_owner_cannot_modify_another_attempt(runtime):
    enqueue()
    assert queue.claim_job("group:msg", owner="old")
    with sqlite3.connect(queue.DB_PATH) as conn:
        conn.execute("UPDATE translation_retry_jobs SET lease_until=0")
    assert not queue.renew_lease("group:msg", owner="old")
    assert not queue.mark_delivered("group:msg", owner="old")
    assert not queue.reschedule("group:msg", owner="old", delay_seconds=1)
    assert queue.claim_job("group:msg", owner="new")
    assert not queue.mark_delivered("group:msg")
    assert not queue.checkpoint("group:msg", {"wrong": True}, owner="old")
    assert queue.reschedule("group:msg", owner="new", delay_seconds=1)
    assert not queue.reschedule("group:msg", owner="new", delay_seconds=1)
    assert queue.get("group:msg")["attempts"] == 1


def test_repeated_enqueue_preserves_source_and_delivery_progress(runtime):
    payload = enqueue()
    queue.checkpoint("group:msg", {"target_langs": ["en"], "delivery": {"text": "prepared", "next_batch": 2}})
    assert not queue.enqueue("group:msg", payload)
    assert queue.get("group:msg")["payload"]["target_langs"] == ["en"]
    assert queue.get("group:msg")["payload"]["delivery"]["next_batch"] == 2
    assert queue.mark_delivered("group:msg")
    assert not queue.enqueue("group:msg", payload)
    assert queue.get("group:msg") is None


def test_duplicate_webhook_after_delivery_does_not_translate_again(runtime):
    enqueue()
    queue.mark_delivered("group:msg")
    invoked = []

    @app._durable_translation_event("text")
    def handler(event):
        invoked.append(event)

    handler(SimpleNamespace(source=SimpleNamespace(group_id="group"), message=SimpleNamespace(id="msg")))
    assert invoked == []


def test_reused_translation_worker_cannot_inherit_previous_message_settings(runtime, monkeypatch):
    seen = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        def contaminate():
            app._tl.force_model = "stale-model"
            app._tl.from_image_ocr = True
            app._tl.translation_variant = "backcheck"
            app._tl.quoted_context_source = "a different person's message"

        executor.submit(contaminate).result()
        monkeypatch.setattr(app, "_MULTI_TGT_EXECUTOR", executor)
        app._tl.group_id = "current-group"
        app._tl.from_file = True

        def translate(_text, _src, tgt):
            seen.append(dict(app._tl.__dict__))
            app._tl.per_message_analysis = "must not survive"
            return "translation " + tgt

        monkeypatch.setattr(app, "translate", translate)
        assert len(app.translate_multi("測試", "zh", ["id", "en", "id"])) == 2
    assert len(seen) == 2
    assert all(row["group_id"] == "current-group" and row["from_file"] for row in seen)
    assert all(not any(key in row for key in ["force_model", "translation_variant", "from_image_ocr",
                                            "quoted_context_source", "per_message_analysis"]) for row in seen)


def test_retry_media_context_and_single_character_transcript_are_preserved(runtime, monkeypatch):
    enqueue("group:msg:audio", kind="audio", target_langs=[], transcribed_text="停", tgt="id")
    app._tl.group_id = "previous-group"
    app._tl.translation_variant = "backcheck"
    monkeypatch.setattr(app, "download_line_audio", lambda *_a: pytest.fail("transcript already saved"))
    monkeypatch.setattr(app, "detect_language", lambda _text: "zh")
    seen = []
    monkeypatch.setattr(app, "translate", lambda *args: seen.append((args, dict(app._tl.__dict__))) or "Berhenti.")
    assert run_job("group:msg:audio")
    assert seen[0][0] == ("停", "zh", "id")
    assert seen[0][1]["group_id"] == "group"
    assert "translation_variant" not in seen[0][1]
    assert app._tl.group_id == "previous-group"


def test_empty_transcription_after_many_outages_never_discards_source_job(runtime, monkeypatch):
    enqueue("group:msg:audio", kind="audio", target_langs=[])
    monkeypatch.setattr(app, "download_line_audio", lambda *_a: b"audio")
    monkeypatch.setattr(app, "transcribe_audio_openai", lambda *_a: None)
    with sqlite3.connect(queue.DB_PATH) as conn:
        conn.execute("UPDATE translation_retry_jobs SET attempts=8")
    assert run_job("group:msg:audio") is False
    assert queue.get("group:msg:audio") is not None


def test_document_resume_preserves_paragraphs_without_retranslating_completed_parts(runtime, monkeypatch):
    enqueue("group:msg:file", kind="file", target_langs=[])
    monkeypatch.setattr(app, "_TEXT_FILE_CHUNK_CHARS", 400)
    source = " " + "A" * 399 + "\n\n" + "B" * 399 + "\n\n尾端\n"
    assert "".join(app._split_text_for_translation(source)) == source
    calls = []
    failed = False

    def translate(text, *_a):
        nonlocal failed
        calls.append(text)
        if text.startswith("B") and not failed:
            failed = True
            return None
        return text.lower()

    monkeypatch.setattr(app, "translate", translate)
    assert app._translate_document_parts("group:msg:file", source, "en", "zh") is None
    result = app._translate_document_parts("group:msg:file", source, "en", "zh")
    assert result == source.lower()
    assert calls.count("A" * 399) == 1


def test_document_code_only_row_is_preserved_when_translation_intentionally_skips(runtime, monkeypatch):
    enqueue("group:msg:file", kind="file", target_langs=[])

    def translate(*_a):
        app._set_translation_outcome("skipped", "factory_code_only")
        return None

    monkeypatch.setattr(app, "translate", translate)
    assert app._translate_document_parts("group:msg:file", "BF2\n", "zh", "id") == "BF2\n"


def test_worker_claims_only_the_job_it_can_start(runtime, monkeypatch):
    for index in range(3):
        enqueue("group:msg" + str(index))
    handled = []

    def run(job, owner):
        assert sum(row["status"] == "leased" for row in queue.list_pending()) == 1
        handled.append(job["job_key"])
        return queue.mark_delivered(job["job_key"], owner=owner)

    monkeypatch.setattr(app, "_run_translation_retry_job", run)
    app._translation_retry_worker_loop()
    assert len(handled) == 3
    assert queue.pending_count() == 0


def test_enqueue_racing_worker_exit_wakes_a_replacement(runtime, monkeypatch):
    reads = 0
    restarts = []
    original = queue.pending_count

    def count():
        nonlocal reads
        reads += 1
        if reads == 1:
            enqueue()
            return 0  # empty observation immediately before the new enqueue
        return original()

    monkeypatch.setattr(queue, "pending_count", count)
    monkeypatch.setattr(app, "_ensure_translation_retry_worker", lambda: restarts.append(True))
    app._translation_retry_worker_loop()
    assert restarts == [True]


def test_failed_foreground_persistence_propagates_for_webhook_redelivery(runtime, monkeypatch):
    monkeypatch.setattr(queue, "enqueue", lambda *_a, **_k: (_ for _ in ()).throw(sqlite3.OperationalError("disk error")))
    with app._translation_job_scope(), pytest.raises(sqlite3.OperationalError):
        app._schedule_text_translation_retry(
            {"group_id": "group", "message_id": "msg"}, source_text="停機", src_lang="zh",
            target_langs=["id"], delay_seconds=90,
        )


def test_postback_variant_retries_the_same_requested_mode(runtime, monkeypatch):
    _, calls = runtime
    event = SimpleNamespace(webhook_event_id="variant-event", reply_token="token")
    context = {"original": "停機", "translated": "Berhenti.", "src": "zh", "tgt": "id"}
    with app._translation_job_scope():
        key = app._schedule_variant_translation(event, context, "formal", "group", "user")
    seen = []
    monkeypatch.setattr(app, "_execute_translation_variant", lambda ctx, mode, group, user:
                        seen.append((ctx, mode, group, user)) or ("Mohon hentikan mesin.", "zh", "id"))
    assert run_job(key)
    assert seen == [(context, "formal", "group", "user")]
    assert "Mohon hentikan mesin." in calls[0][1].messages[0].text
    assert app._schedule_variant_translation(event, context, "formal", "group", "user") is None


def test_failed_webhook_releases_postback_claim_for_redelivery(runtime):
    app._release_processed_message("pbk:postback-token")
    assert app._is_duplicate_message("pbk:postback-token") is False
    app._release_webhook_message_claims('{"events":[{"type":"postback","replyToken":"postback-token"}]}')
    assert app._is_duplicate_message("pbk:postback-token") is False
    app._release_processed_message("pbk:postback-token")


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32", "utf-8-sig", "cp950"])
def test_office_text_encoding_does_not_send_mojibake_to_translation(encoding):
    source = "I9\t停機\n檢查 ID 7J846310"
    assert app._decode_uploaded_text(source.encode(encoding)) == source


def test_same_primary_and_fallback_speech_model_is_called_once(runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "oai", True)
    monkeypatch.setattr(app, "STT_MODEL", "whisper-1")
    monkeypatch.setattr(app, "STT_FALLBACK_MODEL", "whisper-1")
    create = lambda **kwargs: calls.append(kwargs["model"]) or SimpleNamespace(text="停")
    monkeypatch.setattr(app, "ai", SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))))
    assert app.transcribe_audio_openai(b"audio") == "停"
    assert calls == ["whisper-1"]


def test_confirmed_silence_completes_without_paid_translation_or_endless_retries(runtime, monkeypatch):
    _, calls = runtime
    enqueue("group:msg:audio", kind="audio", target_langs=[])
    monkeypatch.setattr(app, "oai", True)
    monkeypatch.setattr(app, "download_line_audio", lambda *_a: b"audio")
    monkeypatch.setattr(app, "ai", SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(
        create=lambda **_kwargs: SimpleNamespace(text=""),
    ))))
    monkeypatch.setattr(app, "translate", lambda *_a: pytest.fail("silent audio has no source to translate"))
    assert run_job("group:msg:audio")
    assert queue.pending_count() == 0
    assert calls == []


@pytest.mark.parametrize("text,empty", [("NO_TEXT", True), ("圖中沒有異常", False), ("圖片中沒有標籤", False)])
def test_ocr_empty_marker_is_an_exact_response_not_a_substring(runtime, monkeypatch, text, empty):
    monkeypatch.setattr(app, "_has_ai_capability", lambda _kind: True)
    monkeypatch.setattr(app, "track_tokens", lambda *_a: None)
    monkeypatch.setattr(app, "_vision_call", lambda *_a, **_k: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
    ))
    monkeypatch.setattr(app, "_clean_ocr_status_bar", lambda value: value)
    monkeypatch.setattr(app, "_should_run_factory_reason_table_ocr", lambda _value: False)
    result = app.ocr_image_openai("image")
    assert result == (None if empty else text)
    assert (app._tl.ocr_extraction_state == "empty") is empty


@pytest.mark.parametrize("failover", [True, False])
def test_sole_provider_can_repair_quality_failure_within_two_generations(monkeypatch, failover):
    monkeypatch.setattr(ai_provider, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_provider, "_current_config", {"provider_failover": failover})
    monkeypatch.setattr(ai_provider, "get_available_providers", lambda *_a, **_k: ["openai"])
    monkeypatch.setattr(ai_provider, "get_active_provider", lambda: "openai")
    monkeypatch.setattr(ai_provider, "_record_provider_success", lambda *_a, **_k: None)
    requests = []

    def dispatch(_provider, **kwargs):
        requests.append(copy.deepcopy(kwargs))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content="wrong" if len(requests) == 1 else "I9 停機。"
        ))])

    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    response = ai_provider.chat_complete(
        "test", [{"role": "user", "content": "I9 stop"}], translation_max_generations=2,
        response_validator=lambda result, _provider: (result.choices[0].message.content != "wrong", "missing_literal:I9"),
    )
    assert response.choices[0].message.content == "I9 停機。"
    assert len(requests) == 2
    assert "missing_literal:I9" in str(requests[1]["messages"])


@pytest.mark.parametrize("finish", ["length", "max_tokens", "MAX_TOKENS"])
def test_truncated_model_output_cannot_be_delivered_as_a_complete_translation(monkeypatch, finish):
    monkeypatch.setattr(ai_provider, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_provider, "_current_config", {"provider_failover": True})
    monkeypatch.setattr(ai_provider, "get_available_providers", lambda *_a, **_k: ["openai"])
    requests = []

    def dispatch(_provider, **kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="First part only."), finish_reason=finish,
        )])

    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    with pytest.raises(RuntimeError, match="translation_output_truncated"):
        ai_provider.chat_complete(
            "test", [{"role": "user", "content": "Complete announcement"}],
            max_tokens=1024, translation_max_generations=2,
            response_validator=lambda *_a: (True, ""),
        )
    assert len(requests) == 2
    assert requests[1]["max_tokens"] == 2048
