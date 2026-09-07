"""Offline full LINE text-path benchmark with fixed AI/transport fixtures.

python benchmark_translation_latency.py --output /tmp/line-latency
python benchmark_translation_latency.py --repo /path/to/baseline --output /tmp/before

Requires development/test dependencies. Produces JSON and cProfile files.
No external requests or billable generations. Measures warmed local execution,
including source analysis, prompt construction, validation, durable outbox and
LINE payload assembly; excludes real cloud latency and async postprocessing.
"""

if __name__ == '__main__':
    import argparse
    from contextlib import ExitStack
    import os
    from pathlib import Path
    import socket
    import sys
    import tempfile
    from unittest.mock import patch

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path, default=Path('/tmp/line-latency'))
    options = parser.parse_args()
    output = options.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    sys.path.insert(0, str(options.repo.resolve()))
    os.chdir(options.repo.resolve())
    with tempfile.TemporaryDirectory(prefix='line-latency-') as state, ExitStack() as stack:
        from benchmark_translation_cp import _offline_environment
        _offline_environment(state)
        os.environ['LATENCY_OUTPUT'] = str(output)
        os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        def blocked(*args, **kwargs):
            raise RuntimeError('Network disabled in offline latency benchmark')
        stack.enter_context(patch.object(socket.socket, 'connect', blocked))
        stack.enter_context(patch.object(socket.socket, 'connect_ex', blocked))
        import ai_provider
        ai_provider.PROVIDER_CONFIG_PATH = str(Path(state) / 'providers.json')
        import app
        import pytest
        result = pytest.main(['-q', '-s', '--import-mode=importlib', str(script), '--tb=short'])
    raise SystemExit(result)

import cProfile
import hashlib
import json
import os
from pathlib import Path
import socket
import statistics
import time
from types import SimpleNamespace

import app
import pytest
from test_translation_notice_availability import runtime, event, SOURCE, TARGET, delivered_text

TRANSLATE_OPENAI = app.translate_openai
SAMPLES = [
    ('routine', '今天下午再確認包裝進度。', 'Sore ini, periksa lagi progres pengemasan.'),
    ('collar', '套環要補上', 'Cincin Pelindung perlu dipasang.'),
    ('notice', SOURCE, TARGET),
]

def test_latency_probe(runtime, monkeypatch):
    def blocked(*a, **k):
        raise RuntimeError('network disabled for offline latency benchmark')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    monkeypatch.setattr(app, '_BG_POST_EXECUTOR', SimpleNamespace(submit=lambda *a, **k: None))
    monkeypatch.setattr(app, '_build_translation_action_quick_reply', app._build_unified_translation_menu)
    monkeypatch.setattr(app, 'get_group_feature', lambda gid, feature: feature == 'flex')
    monkeypatch.setattr(app, 'translate_openai', TRANSLATE_OPENAI)
    calls=[]
    current=['']
    def capture(**kw):
        calls.append({'model':kw.get('model'),
            'prompt_chars':sum(len(m.get('content','')) for m in kw['messages']),
            'prompt_sha256':hashlib.sha256(json.dumps(kw['messages'],ensure_ascii=False,sort_keys=True).encode()).hexdigest()})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=current[0]), finish_reason='stop')],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))
    monkeypatch.setattr(app.ai.chat.completions, 'create', capture)
    rows=[]
    base=Path(os.environ.get('LATENCY_OUTPUT',str(Path(__file__).parent.parent/'latency-before')))
    for name,source,target in SAMPLES:
        current[0]=target
        for mode in ('miss','hit'):
            times=[];runs=[]
            for i in range(9):
                if mode=='miss':app.translation_cache.clear()
                app._tl.__dict__.clear()
                runtime.sends.clear();calls.clear()
                e=event(source);e.message.id=f'{name}-{mode}-{i}'
                profiler=cProfile.Profile() if i==8 else None
                start=time.perf_counter()
                if profiler:
                    with profiler:app.handle_message(e)
                else:app.handle_message(e)
                elapsed=(time.perf_counter()-start)*1000
                result=delivered_text(runtime)
                assert runtime.sends and target in result, (name,mode,'expected translation missing')
                runs.append({'ms':round(elapsed,3),'calls':list(calls),'text':result})
                if 1<=i<=7:times.append(elapsed)
                if profiler:profiler.dump_stats(str(base)+f'-{name}-{mode}.pstats')
            rows.append({'sample':name,'mode':mode,'median_ms':round(statistics.median(times),3),'runs':runs})
    Path(str(base)+'.json').write_text(json.dumps({'mode':'offline','network':'blocked','live_ai_calls':0,
        'line_messages_sent':0,'scope':'handle_message local execution; mocked AI and LINE transport; asynchronous postprocessing excluded',
        'repo':str(Path(app.__file__).resolve().parent),
        'app_sha256':hashlib.sha256(Path(app.__file__).read_bytes()).hexdigest(),
        'corpus_sha256':hashlib.sha256(json.dumps(SAMPLES,ensure_ascii=False).encode()).hexdigest(),
        'measured_runs_per_sample':7, 'rows':rows},ensure_ascii=False,indent=2)+'\n')
    print([(r['sample'],r['mode'],r['median_ms']) for r in rows])
