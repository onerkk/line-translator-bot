"""Compare two resident code versions with adjacent, alternating requests.

python benchmark_translation_paired.py --before /path/to/before --output paired.json

Each worker uses the real LINE text pipeline with blocked external sockets,
isolated databases and fixed provider/LINE responses. Keeping both interpreters
warm and alternating request order reduces batch/host drift. This measures local
work only, never real model speed, translation accuracy or invoices.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

MARKER = "PAIRED_MEASUREMENT "


def emit(value):
    print(MARKER + json.dumps(value, ensure_ascii=False), flush=True)


def test_worker(runtime, monkeypatch):
    import app
    from test_translation_notice_availability import event, delivered_text
    from benchmark_translation_latency import SAMPLES
    monkeypatch.setattr(app, '_BG_POST_EXECUTOR', SimpleNamespace(submit=lambda *a, **k: None))
    monkeypatch.setattr(app, '_build_translation_action_quick_reply', app._build_unified_translation_menu)
    monkeypatch.setattr(app, 'get_group_feature', lambda gid, feature: feature == 'flex')
    monkeypatch.setattr(app, 'get_conv_context_enabled', lambda *_: False)
    monkeypatch.setattr(app, 'translate_openai', REAL_TRANSLATE)
    calls, current = [], ['']
    def capture(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=current[0]), finish_reason='stop')],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))
    monkeypatch.setattr(app.ai.chat.completions, 'create', capture)
    emit({'ready': True, 'samples': [sample[0] for sample in SAMPLES],
          'app_sha256': hashlib.sha256(Path(app.__file__).read_bytes()).hexdigest()})
    for line in sys.stdin:
        command = json.loads(line)
        if command.get('stop'):
            break
        name, source, target = SAMPLES[command['sample']]
        current[0] = target
        app._tl.__dict__.clear()
        app.translation_cache.clear()
        calls.clear()
        runtime.sends.clear()
        e = event(source)
        e.message.id = 'paired-' + str(command['id'])
        wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
        app.handle_message(e)
        result = {'sample': name, 'id': command['id'],
                  'wall_ms': (time.perf_counter_ns() - wall) / 1e6,
                  'caller_cpu_ms': (time.thread_time_ns() - cpu) / 1e6,
                  'generation_calls': len(calls),
                  'prompt_chars': [sum(len(m.get('content', '')) for m in call['messages']) for call in calls]}
        assert runtime.sends and target in delivered_text(runtime), name
        emit(result)


def read_measurement(process):
    for line in process.stdout:
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    raise RuntimeError('Benchmark worker ended unexpectedly; inspect its log')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path)
    parser.add_argument('--after', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', type=Path)
    parser.add_argument('--rounds', type=int, default=15)
    args = parser.parse_args()
    script = Path(__file__).resolve()
    if args.worker:
        repo = args.worker.resolve()
        os.chdir(repo)
        sys.path.insert(0, str(repo))
        from benchmark_translation_cp import _offline_environment
        from run_translation_offline_checks import offline_network
        with tempfile.TemporaryDirectory(prefix='translation-paired-') as state:
            _offline_environment(state)
            os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
            with offline_network():
                import ai_provider
                ai_provider.PROVIDER_CONFIG_PATH = str(Path(state) / 'providers.json')
                import app
                import pytest
                # Pytest imports this file independently; provide the preserved
                # real function and fixture in that imported module as well.
                os.environ['PAIRED_WORKER_ACTIVE'] = '1'
                return pytest.main(['-q', '-s', '--tb=short', '--import-mode=importlib', str(script)])
    if not args.before or not args.output or not 5 <= args.rounds <= 100:
        parser.error('--before, --output and 5..100 rounds are required')
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        workers, ready, rows = {}, {}, []
        for name, repo in [('before', args.before), ('after', args.after)]:
            log = stack.enter_context(output.with_suffix('.' + name + '.log').open('w'))
            process = subprocess.Popen([sys.executable, str(script), '--worker', str(repo.resolve())],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1)
            workers[name] = process
            stack.callback(lambda p=process: p.terminate() if p.poll() is None else None)
            ready[name] = read_measurement(process)
        assert ready['before']['samples'] == ready['after']['samples']
        samples = ready['before']['samples']
        for iteration in range(args.rounds + 2):
            for index, sample in enumerate(samples):
                order = ('before', 'after') if (iteration + index) % 2 == 0 else ('after', 'before')
                pair = {'sample': sample, 'iteration': iteration, 'warmup': iteration < 2, 'order': order}
                for name in order:
                    process = workers[name]
                    process.stdin.write(json.dumps({'sample': index, 'id': iteration * len(samples) + index}) + '\n')
                    process.stdin.flush()
                    pair[name] = read_measurement(process)
                assert pair['before']['generation_calls'] == pair['after']['generation_calls']
                rows.append(pair)
        for process in workers.values():
            process.stdin.write('{"stop":true}\n')
            process.stdin.flush()
            process.communicate(timeout=30)
            assert process.returncode == 0
    summaries = []
    for sample in samples:
        measured = [row for row in rows if row['sample'] == sample and not row['warmup']]
        summary = {'sample': sample, 'paired_runs': len(measured)}
        for clock in ('wall_ms', 'caller_cpu_ms'):
            summary[clock] = {name: statistics.median(row[name][clock] for row in measured) for name in ('before', 'after')}
            summary[clock]['median_paired_delta_after_minus_before'] = statistics.median(row['after'][clock] - row['before'][clock] for row in measured)
        summaries.append(summary)
    output.write_text(json.dumps({'scope': 'offline, fixed AI/LINE responses, no external I/O; source cache cleared per request; two resident workers; alternating adjacent pairs; first two rounds warm up',
        'live_ai_calls': 0, 'line_messages_sent': 0, 'worker_versions': ready,
        'harness_sha256': hashlib.sha256(script.read_bytes()).hexdigest(), 'rows': rows, 'summary': summaries}, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summaries, ensure_ascii=False))
    return 0


if __name__ != '__main__' and os.environ.get('PAIRED_WORKER_ACTIVE') == '1':
    import app
    REAL_TRANSLATE = app.translate_openai
    from test_translation_notice_availability import runtime

if __name__ == '__main__':
    raise SystemExit(main())
