"""Compare either source tree with identical artificial I/O delays.

python benchmark_webhook_lanes.py --repo /path/to/repo --output result.json
This measures local scheduling, not live AI, LINE latency, accuracy or billing.
"""
import argparse
from pathlib import Path
import sys
import os
import tempfile
import socket
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo, output = args.repo.resolve(), args.output.resolve()
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    from benchmark_translation_cp import _offline_environment
    with tempfile.TemporaryDirectory(prefix="webhook-lane-verification-") as state:
        _offline_environment(state)
        def blocked(*args, **kwargs):
            raise RuntimeError("External network disabled")
        with patch.object(socket.socket, "connect", blocked), patch.object(socket.socket, "connect_ex", blocked):
            import ai_provider
            ai_provider.PROVIDER_CONFIG_PATH = str(Path(state) / "providers.json")
            import app
            output.write_text(__import__("json").dumps(measure(app), ensure_ascii=False, indent=2) + "\n")


def measure(app):
    import base64
    import hashlib
    import hmac
    import json
    import statistics
    import threading
    import time
    from linebot.v3.webhook import WebhookHandler
    from linebot.v3.webhooks import MessageEvent, TextMessageContent
    import translation_retry_queue as queue
    os.environ.pop("RENDER", None)
    os.environ.pop("LINE_WEBHOOK_ASYNC", None)

    def wait_for(predicate):
        end = time.monotonic() + 4
        wake = threading.Event()
        while not predicate():
            if time.monotonic() > end:
                raise AssertionError("work did not finish")
            wake.wait(0.002)

    ack_samples, recovery_samples = [], []
    for run in range(5):
        handler = WebhookHandler("benchmark-secret")
        @handler.add(MessageEvent, message=TextMessageContent)
        def handle(event):
            threading.Event().wait(0.12)
        app.handler = handler
        if hasattr(app, "_WEBHOOK_INBOX"):
            app._WEBHOOK_INBOX.handler = handler
        body = json.dumps({"destination": "Ubench", "events": [{
            "type": "message", "mode": "active", "timestamp": int(time.time() * 1000),
            "webhookEventId": f"benchmark-{run}", "deliveryContext": {"isRedelivery": False},
            "replyToken": f"reply-{run}", "source": {"type": "user", "userId": "Ubench"},
            "message": {"type": "text", "id": str(100 + run), "text": "本機測試", "quoteToken": "quote"},
        }]})
        signature = base64.b64encode(hmac.new(b"benchmark-secret", body.encode(), hashlib.sha256).digest()).decode()
        start = time.monotonic()
        assert app.app.test_client().post("/callback", data=body, headers={"X-Line-Signature": signature}).status_code == 200
        ack_samples.append(round((time.monotonic() - start) * 1000, 3))
        wait_for(lambda: queue.pending_count() == 0)

        text_done = threading.Event()
        completed_at = []
        def slow_media(job, owner):
            threading.Event().wait(0.10 if job["job_kind"] == "image" else 0.005)
            queue.mark_delivered(job["job_key"], owner=owner)
            if job["job_kind"] == "text":
                completed_at.append(time.monotonic())
                text_done.set()
            return True
        with patch.object(app, "_run_translation_retry_job", slow_media):
            queue.enqueue(f"media-{run}-1", {}, job_kind="image")
            queue.enqueue(f"media-{run}-2", {}, job_kind="image")
            queue.enqueue(f"text-{run}", {}, job_kind="text")
            start = time.monotonic()
            app._ensure_translation_retry_worker()
            assert text_done.wait(3)
            recovery_samples.append(round((completed_at[0] - start) * 1000, 3))
            wait_for(lambda: queue.pending_count() == 0)

    # Exercise the actual Flask synchronous fallback with the thread count in
    # this tree's Dockerfile. This is a local thread executor, not a deployed
    # Gunicorn or a Render machine benchmark.
    import re
    from concurrent.futures import ThreadPoolExecutor
    threads = int(re.search(r'"--threads",\s*"(\d+)"', Path("Dockerfile").read_text()).group(1))
    bursts = []
    os.environ["RENDER"] = "true"
    for run in range(3):
        completed = []
        @handler.add(MessageEvent, message=TextMessageContent)
        def handle(event):
            threading.Event().wait(0.12)
            completed.append(event.message.id)
        def send(index):
            payload = json.loads(body)
            payload["events"][0]["message"]["id"] = f"burst-{run}-{index}"
            payload["events"][0]["webhookEventId"] = f"burst-{run}-{index}"
            raw = json.dumps(payload)
            sig = base64.b64encode(hmac.new(b"benchmark-secret", raw.encode(), hashlib.sha256).digest()).decode()
            return app.app.test_client().post("/callback", data=raw, headers={"X-Line-Signature": sig}).status_code
        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=threads) as pool:
            assert list(pool.map(send, range(16))) == [200] * 16
        assert len(set(completed)) == 16
        bursts.append(round((time.monotonic() - start) * 1000, 3))
    os.environ.pop("RENDER", None)
    source = "@All\n7J821007\n7J821008\n7J821009\n先不要生產，等研發單位量過圓度在生產"
    prefix = "@All\n7J821007\n7J821008\n7J821009\n"
    good = "Jangan produksi dulu. Tunggu bagian R&D mengukur kebulatan terlebih dahulu, baru produksi."
    report = "@福迪 昨天拋光手寫報表放在哪裡"
    report_good = "@福迪 Laporan tulis tangan proses polishing kemarin diletakkan di mana?"
    cases = [("screenshot_production", source, prefix + good),
             ("wrong_metric", source, prefix + good.replace("kebulatan", "kekasaran permukaan")),
             ("reversed_sequence", source, prefix + "Produksi dulu, kemudian bagian R&D mengukur kebulatan."),
             ("equivalent_prerequisite", source, prefix + "Produksi baru boleh dimulai setelah bagian R&D selesai mengukur roundness."),
             ("screenshot_report", report, report_good),
             ("wrong_day", report, report_good.replace("kemarin", "hari ini")),
             ("question_changed_to_answer", report, report_good.replace("diletakkan di mana?", "sudah diletakkan di meja.")),
             ("natural_report_noun", report, "@福迪 Laporan polishing tulisan tangan kemarin ada di mana?")]
    rows = []
    for name, original, candidate in cases:
        app._tl.__dict__.clear()
        delivered = app._final_delivery_guard(original, candidate, "zh", "id")
        rows.append({"case": name, "source": original, "candidate": candidate,
                     "accepted": bool(delivered), "output": delivered})
    return {"mode": "offline controlled delay", "external_network": "blocked", "live_ai_requests": 0,
            "live_line_messages": 0, "runs": 5,
            "artificial_work_ms": {"webhook_handler": 120, "each_media_job": 100, "text_job": 5},
            "webhook_ack_ms": ack_samples, "webhook_ack_median_ms": statistics.median(ack_samples),
            "text_recovery_after_two_media_jobs_ms": recovery_samples,
            "text_recovery_median_ms": statistics.median(recovery_samples), "semantic_checks": rows,
            "render_ephemeral_mode": {"http_threads_from_dockerfile": threads,
                "messages_per_burst": 16, "burst_total_ms": bursts,
                "burst_median_ms": statistics.median(bursts),
                "note": "Real Flask callback, local thread executor with simulated 120 ms work. Not live Gunicorn/Render."},
            "limits": "Timing measures local scheduling with identical artificial delays, not online speed. Fixed candidates measure guard behavior, not model translation accuracy. No bill or production logs were available."}


if __name__ == "__main__":
    main()
