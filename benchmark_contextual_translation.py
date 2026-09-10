"""Replay the same reviewed outputs through real provider acceptance, offline.

python benchmark_contextual_translation.py --repo /path/to/tree --output /tmp/result.json

No actual model requests or LINE deliveries. Generation counts describe this
controlled replay, not a live latency/quality benchmark. The --repo option runs
this identical corpus/harness against a previous or updated code tree.
"""
if __name__ == '__main__':
    import argparse
    import os
    from pathlib import Path
    import socket
    import sys
    import tempfile
    from unittest.mock import patch
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo, output, script = args.repo.resolve(), args.output.resolve(), Path(__file__).resolve()
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    with tempfile.TemporaryDirectory(prefix='quantity-replay-') as state:
        from benchmark_translation_cp import _offline_environment
        _offline_environment(state)
        os.environ['FACTORY_ACK_WORKER_ENABLED'] = '0'
        os.environ['QUANTITY_REPLAY_OUTPUT'] = str(output)
        os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        def blocked(*args, **kwargs):
            raise RuntimeError('Network disabled in offline translation replay')
        with patch.object(socket.socket, 'connect', blocked), patch.object(socket.socket, 'connect_ex', blocked):
            import ai_provider
            ai_provider.PROVIDER_CONFIG_PATH = str(Path(state) / 'providers.json')
            import pytest
            raise SystemExit(pytest.main(['-q', '--tb=short', '--import-mode=importlib', str(script)]))

import json
import os
from pathlib import Path
import statistics
import time

import app
import ai_provider
import factory_quantity_semantics as quantities
import translation_request_cache as memo
import translation_quality_gate as quality
from test_translation_instruction_cost_quality import offline_transport, response


def test_same_reviewed_candidates(offline_transport, monkeypatch):
    corpus = json.loads((Path(__file__).resolve().parent / 'tests/data/contextual_translation_20260910.json').read_text())
    rows = []
    for sample in corpus:
        source, good, bad = sample['source'], sample['good'], sample['bad']
        app._tl.__dict__.clear()
        # The mock provider obeys the same native-mention copy contract as a
        # real provider. Return tokens here; translate_openai restores labels.
        _, mentions = app.protect_mentions(source)
        provider_good = good
        for token, label in mentions.items():
            provider_good = provider_good.replace(label, token)
        calls, payload_chars = [], []
        def dispatch(provider, **kwargs):
            calls.append(provider)
            payload_chars.append(sum(len(str(m.get('content', ''))) for m in kwargs.get('messages', [])))
            return response(provider_good)
        monkeypatch.setattr(ai_provider, '_dispatch_provider', dispatch)
        @ai_provider.translation_request_budget
        def run():
            app._tl.semantic_contract = app.build_translation_semantic_contract(source, 'zh', 'id')
            return app.translate_openai(source, 'zh', 'id')
        result = run()
        good_check = quality.validate_translation(source, good, 'zh', 'id')
        bad_check = quality.validate_translation(source, bad, 'zh', 'id')
        timings = []
        for _ in range(80):
            start = time.perf_counter()
            with memo.scope():
                for _ in range(8):
                    quantities.validate_translation(quantities.build_frame(source), good)
            timings.append((time.perf_counter() - start) * 1000)
        rows.append({'id': sample['id'], 'generations_for_reviewed_output': len(calls),
                     'reviewed_output_returned': result == good,
                     'good_accepted': good_check.ok, 'bad_rejected': not bad_check.ok,
                     'good_issues': list(good_check.hard_issues),
                     'payload_chars_per_generation': payload_chars,
                     'quantity_prompt_chars': len(quantities.build_prompt(quantities.build_frame(source))),
                     'quantity_eight_checks_median_ms': round(statistics.median(timings), 3)})
    output = Path(os.environ['QUANTITY_REPLAY_OUTPUT'])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'mode': 'offline_fixed_provider_output', 'repo': str(Path(app.__file__).parent),
                                  'live_api_calls': 0, 'rows': rows}, ensure_ascii=False, indent=2))
