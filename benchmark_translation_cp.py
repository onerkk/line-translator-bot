"""Reproducible offline CP comparison; no AI billing or LINE messages.

python benchmark_translation_cp.py --output cp-result.json
Use --repo /path/to/previous/version to measure that version with the SAME
corpus and harness. Model names and prompt characters are observed payloads;
local timings exclude cloud/model/LINE latency. Targets are fixed fixtures.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent


def _offline_environment(directory):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                "GOOGLE_TRANSLATE_API_KEY", "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"):
        os.environ[key] = ""
    os.environ.update(LINE_CHANNEL_SECRET="offline-cp-probe", LINE_CHANNEL_ACCESS_TOKEN="offline-cp-probe",
                      REQUIRE_CLOUD_SETTINGS="0", REMINDERS_WORKER_ENABLED="0", LINE_FACTORY_STORE="sqlite")
    for key in ("LINE_FACTORY_DB_PATH", "TM_DB_PATH", "VECTOR_TM_DB_PATH", "ACTIVE_LEARNING_DB_PATH",
                "TRANSLATION_RETRY_DB_PATH", "BOT_SETTINGS_CACHE_FILE", "CUSTOM_EXAMPLES_FILE",
                "PHASE_CONFIG_PATH", "LAST_TRANSLATE_DEBUG_FILE", "TRANSLATION_LOG_FILE"):
        os.environ[key] = str(Path(directory) / key.lower())


def run(repo, corpus, runs):
    repo = Path(repo).resolve()
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    with tempfile.TemporaryDirectory(prefix="line-cp-bench-") as state, ExitStack() as stack:
        _offline_environment(state)
        # Block external transport even if a module loads old saved credentials.
        def no_network(*_args, **_kwargs):
            raise RuntimeError("Network disabled in offline CP benchmark")
        stack.enter_context(patch.object(socket.socket, "connect", no_network))
        stack.enter_context(patch.object(socket.socket, "connect_ex", no_network))
        import ai_provider
        ai_provider.PROVIDER_CONFIG_PATH = str(Path(state) / "providers.json")
        import app
        for name in ("_event_log_write", "_replace_last_translate_debug", "_ensure_translation_retry_worker"):
            stack.enter_context(patch.object(app, name, lambda *_a, **_k: None))
        stack.enter_context(patch.object(app, "translate_google", lambda *_a, **_k: None))
        stack.enter_context(patch.object(app.nmt_module, "nmt_translate", lambda *_a, **_k: None))
        rows = []
        for sample in corpus["samples"]:
            source, src, tgt = sample["source"], sample["src"], sample["tgt"]
            app._tl.__dict__.clear()
            request = []
            def capture(**kwargs):
                request.append(kwargs)
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content=sample["target"]), finish_reason="stop")],
                    usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))
            with patch.object(app.ai.chat.completions, "create", capture):
                app._tl.semantic_contract = app.build_translation_semantic_contract(source, src, tgt)
                app.translate_openai(source, src, tgt)
            if not request:
                raise AssertionError("Provider payload was not captured: " + sample["id"])
            payload = request[0]
            chars = sum(len(m.get("content", "")) for m in payload["messages"])
            prompt_bytes = json.dumps(payload["messages"], ensure_ascii=False).encode()
            durations = []
            for _ in range(runs):
                # Deliberately do not enable the per-request memo: measure the
                # real lookup work, with normal bounded process caches warmed.
                started = time.perf_counter()
                references = app._retrieve_verified_translation_cases(source, src, tgt)
                durations.append((time.perf_counter() - started) * 1000)
            good = app._final_delivery_guard(source, sample["target"], src, tgt)
            bad_rejected = None
            if sample.get("bad_target"):
                app._tl.__dict__.clear()
                bad_rejected = app._final_delivery_guard(source, sample["bad_target"], src, tgt) is None
            rows.append({
                "id": sample["id"], "source": source, "model_selected": payload["model"],
                "prompt_chars": chars, "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
                "mocked_generation_calls": len(request), "reference_ids": [r["case_id"] for r in references],
                "reference_lookup_median_ms": round(statistics.median(durations), 3),
                "good_fixture_accepted": good is not None, "bad_fixture_rejected": bad_rejected,
            })
        return {"mode": "offline", "network": "blocked", "live_ai_calls": 0, "line_messages_sent": 0,
                "timing_scope": "local reference retrieval only; warmed process caches, no request memo",
                "prompt_metric": "characters, not billed tokens", "runs_per_sample": runs,
                "corpus_sha256": hashlib.sha256(json.dumps(corpus, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                "repo": str(repo), "rows": rows,
                "summary": {"samples": len(rows), "prompt_chars_total": sum(r["prompt_chars"] for r in rows),
                    "reference_lookup_median_ms": round(statistics.median(r["reference_lookup_median_ms"] for r in rows), 3),
                    "good_fixtures_accepted": sum(r["good_fixture_accepted"] for r in rows),
                    "bad_fixtures_rejected": sum(r["bad_fixture_rejected"] is True for r in rows),
                    "bad_fixture_count": sum(r["bad_fixture_rejected"] is not None for r in rows)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--corpus", type=Path, default=ROOT / "tests/data/cp_holdout_20260907.json")
    parser.add_argument("--runs", type=int, default=9)
    parser.add_argument("--output", type=Path, default=Path("cp-result.json"))
    args = parser.parse_args()
    if not 3 <= args.runs <= 50:
        parser.error("--runs must be between 3 and 50")
    output = args.output.resolve()
    corpus = json.loads(args.corpus.resolve().read_text(encoding="utf-8"))
    # Startup logs can include unrelated diagnostics. Keep the machine-readable
    # report separate, and retain only the exception type if the probe fails.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        result = run(args.repo, corpus, args.runs)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    print(str(output))


if __name__ == "__main__":
    main()
