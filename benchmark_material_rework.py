"""Reproduce the screenshot failure against either code tree, without the network.

python benchmark_material_rework.py --output rework-after.json
python benchmark_material_rework.py --repo /path/to/previous/version --output rework-before.json

Fixed AI responses test actual coordinator/guard behavior; no live quality or
latency measurement is implied. Uses the repository's existing test fixtures.
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
    repo, script, output = args.repo.resolve(), Path(__file__).resolve(), args.output.resolve()
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    from benchmark_translation_cp import _offline_environment
    with tempfile.TemporaryDirectory(prefix='rework-verification-') as state:
        _offline_environment(state)
        os.environ['REWORK_VERIFICATION_OUTPUT'] = str(output)
        os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        def blocked(*a, **k):
            raise RuntimeError('External network disabled in rework verification')
        with patch.object(socket.socket, 'connect', blocked), patch.object(socket.socket, 'connect_ex', blocked):
            import ai_provider
            ai_provider.PROVIDER_CONFIG_PATH = str(Path(state) / 'providers.json')
            import app
            import pytest
            raise SystemExit(pytest.main(['-q', '--tb=short', '--import-mode=importlib', str(script)]))

import json
import os
from pathlib import Path

import app
import ai_provider
import factory_knowledge as knowledge
import translation_quality_gate as quality
from test_translation_instruction_cost_quality import offline_transport, response

SOURCE = '這把噴漆錯誤的記得重洗'
REPORTED = 'Yang salah pengecatan semprot, ingat untuk dicuci ulang.'
GOOD = 'Jangan lupa cuci ulang bundel yang salah dicat semprot ini.'


def test_reported_rework_comparison(offline_transport, monkeypatch):
    rows = []
    for candidate in [REPORTED, GOOD,
            'Ingat untuk mencuci ulang bundel yang pengecatan semprotnya salah ini.',
            'Bundel yang salah dicat semprot ini sudah dicuci ulang.',
            'Jangan lupa cuci ulang batang yang salah dicat semprot ini.',
            'Jangan lupa mengecat semprot ulang bundel yang salah dicat semprot ini.']:
        app._tl.__dict__.clear()
        contract = app.build_translation_semantic_contract(SOURCE, 'zh', 'id')
        check = quality.validate_translation(SOURCE, candidate, 'zh', 'id')
        rows.append({'candidate': candidate,
            'raw_quality_ok': check.ok, 'raw_quality_issues': list(check.issues),
            'raw_knowledge': knowledge.validate_translation(knowledge.retrieve(SOURCE, 'zh', 'id'), SOURCE, candidate),
            'raw_contract': app.translation_satisfies_semantic_contract(contract, candidate),
            'final_output': app._final_delivery_guard(SOURCE, candidate, 'zh', 'id')})
    calls, candidate = [], [GOOD]
    def dispatch(provider, **kwargs):
        calls.append(provider)
        return response(candidate[0])
    monkeypatch.setattr(ai_provider, '_dispatch_provider', dispatch)
    app._tl.__dict__.clear()
    app._tl.semantic_contract = app.build_translation_semantic_contract(SOURCE, 'zh', 'id')
    natural = app.translate_openai(SOURCE, 'zh', 'id')
    natural_calls = len(calls)
    calls.clear()
    candidate[0] = REPORTED
    app._tl.__dict__.clear()
    monkeypatch.setattr(app, 'translation_cache', {})
    reported = app.translate(SOURCE, 'zh', 'id')
    proof = {'mode': 'offline', 'network': 'blocked', 'live_ai_requests': 0, 'line_messages_sent': 0,
        'source': SOURCE, 'rows': rows,
        'natural_candidate_coordinator_calls': natural_calls,
        'natural_candidate_output': natural,
        'reported_candidate_full_route_calls': len(calls),
        'reported_candidate_full_route_output': reported,
        'limitations': 'All model responses are supplied fixtures. This tests guards, bounded local correction and retries; it does not estimate live translation accuracy, cost or latency.'}
    Path(os.environ['REWORK_VERIFICATION_OUTPUT']).write_text(json.dumps(proof, ensure_ascii=False, indent=2)+'\n')
