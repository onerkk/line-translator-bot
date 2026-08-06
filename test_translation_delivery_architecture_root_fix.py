from __future__ import annotations

import inspect
import sqlite3
import time
from pathlib import Path

import translation_retry_queue as queue


APP = Path(__file__).with_name("app.py").read_text(encoding="utf-8")


def _fresh_db(tmp_path):
    queue.DB_PATH = str(tmp_path / "translation_retry_queue.db")
    queue.initialize()


def test_queue_claims_are_exclusive_across_workers(tmp_path):
    _fresh_db(tmp_path)
    queue.enqueue("job-1", {"job_kind": "text", "source_text": "測試"}, job_kind="text")
    first = queue.claim_due_jobs(owner="worker-a", now=time.time(), lease_seconds=60)
    second = queue.claim_due_jobs(owner="worker-b", now=time.time(), lease_seconds=60)
    assert [job["job_key"] for job in first] == ["job-1"]
    assert second == []


def test_expired_lease_is_reclaimed_after_worker_crash(tmp_path):
    _fresh_db(tmp_path)
    queue.enqueue("job-2", {"job_kind": "image"}, job_kind="image")
    base = time.time()
    first = queue.claim_due_jobs(owner="dead-worker", now=base, lease_seconds=15)
    assert first and first[0]["lease_owner"] == "dead-worker"
    reclaimed = queue.claim_due_jobs(owner="live-worker", now=base + 16.0, lease_seconds=15)
    assert reclaimed and reclaimed[0]["lease_owner"] == "live-worker"


def test_retry_queue_has_no_terminal_exhausted_state(tmp_path):
    _fresh_db(tmp_path)
    queue.enqueue("job-3", {"job_kind": "text"}, job_kind="text")
    base = time.time()
    for attempt in range(25):
        claimed = queue.claim_due_jobs(owner=f"worker-{attempt}", now=base + attempt * 10, lease_seconds=15)
        assert claimed
        queue.reschedule(
            "job-3",
            delay_seconds=1,
            error=f"temporary-{attempt}",
            owner=f"worker-{attempt}",
        )
    row = queue.get("job-3")
    assert row is not None
    assert row["status"] == "pending"
    assert row["attempts"] == 25
    source = inspect.getsource(queue).lower()
    assert "status='exhausted'" not in source
    assert 'status="exhausted"' not in source
    assert "where status in ('pending','leased')" in source


def test_text_is_persisted_before_immediate_provider_call():
    group = APP[APP.index("def handle_message(event):"):APP.index("@handler.add(MessageEvent, message=ImageMessageContent)")]
    persist = group.index("# Durable text outbox")
    provider = group.index("translate_multi(", persist)
    assert persist < provider
    assert "_complete_durable_text_job(_outbox_key)" in group
    assert "uuid.uuid5" in group


def test_media_jobs_are_durable_and_worker_dispatches_all_supported_kinds():
    assert 'kind == "image"' in APP
    assert 'kind == "audio"' in APP
    assert 'kind == "video"' in APP
    assert 'kind == "file"' in APP
    assert '_schedule_media_translation_retry(' in APP
    assert 'job_kind="image"' in APP


def test_unknown_languages_use_provider_auto_detection_in_every_handler():
    assert APP.count('else "auto"') >= 6
    assert '"auto": "Auto-detected language"' in APP
    assert '"auto": "自動偵測語言"' in APP


def test_operational_failure_notice_is_logging_only():
    start = APP.index("def _send_background_failure_notice")
    end = APP.index("\ndef translate(", start)
    body = APP[start:end]
    assert "MessagingApi(" not in body
    assert "TextMessage(" not in body
    assert "reply_message(" not in body
    assert "push_message(" not in body


def test_old_failure_payloads_are_only_quarantined_not_sent():
    # Legacy strings remain in the sentinel matcher so old cache/TM rows are
    # purged.  Their only runtime failure-notice function is logging-only.
    assert "_is_translation_failure_sentinel" in APP
    file_handler = APP[APP.index("if FileMessageContent:"):APP.index("if LocationMessageContent:")]
    assert "檔案翻譯失敗" not in file_handler
    assert "翻譯結果為空" not in file_handler
    assert "無法判斷檔案語言" not in file_handler


def test_offline_route_precedes_public_fallbacks():
    start = APP.index("def _emergency_translation_fallback")
    end = APP.index("\ndef ", start + 40)
    body = APP[start:end]
    assert body.index("offline_translation_module.translate") < body.index("nmt_module.nmt_translate")
    assert body.index("nmt_module.nmt_translate") < body.index("translate_google")
