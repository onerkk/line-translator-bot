"""Compare actual adapter payloads and exact-context reuse, entirely offline.

python benchmark_translation_speed_economy.py --output /tmp/economy-after.json
python benchmark_translation_speed_economy.py --repo /path/to/original --output /tmp/economy-before.json

The same script/corpus runs against either code tree. SDK and LINE responses
are fixed fixtures; no paid requests, live translation scores or bill estimates.
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
    with tempfile.TemporaryDirectory(prefix='translation-economy-') as state:
        from benchmark_translation_cp import _offline_environment
        _offline_environment(state)
        os.environ['ECONOMY_OUTPUT'] = str(output)
        os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        def blocked(*a, **k):
            raise RuntimeError('Network disabled in offline economy benchmark')
        with patch.object(socket.socket, 'connect', blocked), patch.object(socket.socket, 'connect_ex', blocked):
            import ai_provider
            ai_provider.PROVIDER_CONFIG_PATH = str(Path(state) / 'providers.json')
            import app
            import pytest
            raise SystemExit(pytest.main(['-q', '--tb=short', '--import-mode=importlib', str(script)]))

import copy
import hashlib
import json
import os
from pathlib import Path
import statistics
import time
from types import SimpleNamespace

import app
import ai_provider
import pytest
from test_original_conversation_context import isolated, pipeline, runtime, offline_transport, pair, CURRENT, GOOD, PRIOR, G, A, B

REAL_TRANSLATE_OPENAI = app.translate_openai


def text_chars(content):
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(text_chars(part.get('text', '')) for part in content if isinstance(part, dict))
    return 0


def test_offline_economy(pipeline, monkeypatch):
    root = Path(app.__file__).resolve().parent
    corpus = json.loads((root / 'tests/data/cp_holdout_20260907.json').read_text())
    rows = []
    for sample in corpus['samples']:
        source, target, src, tgt = sample['source'], sample['target'], sample['src'], sample['tgt']
        app._tl.__dict__.clear()
        payloads = []
        def capture(**kwargs):
            payloads.append(kwargs)
            return SimpleNamespace(model=kwargs['model'], choices=[SimpleNamespace(
                message=SimpleNamespace(content=target), finish_reason='stop')],
                usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))
        app._tl.semantic_contract = app.build_translation_semantic_contract(source, src, tgt)
        with monkeypatch.context() as m:
            m.setattr(app.ai.chat.completions, 'create', capture)
            REAL_TRANSLATE_OPENAI(source, src, tgt)
        assert len(payloads) == 1
        request = payloads[0]
        # Only translation-adapter arguments; the coordinator owns validation,
        # failover and privacy before dispatch in production.
        adapter_kwargs = {k: v for k, v in request.items() if k not in {
            'response_validator', 'provider_preference', 'latency_profile',
            'translation_max_generations', 'translation_repair_model',
        }}
        for provider in ('anthropic', 'openai', 'gemini'):
            sdk_calls = []
            def sdk_create(**kwargs):
                sdk_calls.append(copy.deepcopy(kwargs))
                if provider == 'anthropic':
                    return SimpleNamespace(model=kwargs['model'], stop_reason='end_turn',
                        content=[SimpleNamespace(type='text', text=target, citations=None)],
                        usage=SimpleNamespace(input_tokens=0, output_tokens=0,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0))
                return SimpleNamespace(model=kwargs['model'], choices=[SimpleNamespace(
                    message=SimpleNamespace(content=target), finish_reason='stop')],
                    usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))
            client = SimpleNamespace(messages=SimpleNamespace(create=sdk_create),
                chat=SimpleNamespace(completions=SimpleNamespace(create=sdk_create)))
            with monkeypatch.context() as m:
                m.setattr(ai_provider, '_get_' + provider + '_client', lambda: client)
                m.setattr(ai_provider, '_client_with_limits', lambda c, t: c)
                # Bypass the fixture's coordinator substitute and execute the
                # real adapter; only its final SDK I/O returns a fixed fixture.
                if provider == 'anthropic':
                    ai_provider._chat_complete_anthropic(request['model'], request['messages'],
                        request.get('max_tokens') or request.get('max_completion_tokens'), fast_quality=True)
                elif provider == 'gemini':
                    ai_provider._chat_complete_gemini(request['model'], request['messages'],
                        request.get('max_tokens') or request.get('max_completion_tokens'), fast_quality=True)
                else:
                    args = {k: v for k, v in adapter_kwargs.items() if k != 'translation_fast_quality'}
                    ai_provider._chat_complete_openai(**args)
            assert len(sdk_calls) == 1
            sent = sdk_calls[0]
            chars = text_chars(sent.get('system')) + sum(text_chars(m.get('content')) for m in sent['messages'])
            rows.append({'sample': sample['id'], 'provider': provider,
                'model': sent['model'], 'input_text_characters': chars,
                'sdk_requests': len(sdk_calls), 'reasoning_effort': sent.get('reasoning_effort'),
                'thinking': sent.get('thinking'),
                'prompt_cache_options': (sent.get('extra_body') or {}).get('prompt_cache_options'),
                'payload_sha256': hashlib.sha256(json.dumps(sent, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()})
    # Exercise the complete public translation route, including the real source
    # journal, privacy, local checks and context admission. Hold evidence fixed.
    snapshot = pair(app._conversation_journal())
    pipeline.calls.clear()
    pipeline.candidates = ['__MENTION_0__ ' + GOOD]
    times, calls_per_turn = [], []
    for i in range(6):
        app._tl.__dict__.clear()
        app._tl.group_id, app._tl.user_id = G, B
        app._tl.conversation_snapshot = {**copy.deepcopy(snapshot), 'message_id': 'delivery-' + str(i)}
        before = len(pipeline.calls)
        start = time.perf_counter()
        result = app.translate(CURRENT, 'zh', 'id')
        times.append(round((time.perf_counter() - start) * 1000, 3))
        assert result == '@十元 ' + GOOD
        calls_per_turn.append(len(pipeline.calls) - before)
    # Isolate the false-learning fix from cache savings in a separate group.
    other_group = 'economy-cache-disabled'
    journal, now = app._conversation_journal(), time.time()
    journal.capture(other_group, 'prior', PRIOR, author=A, recipients=[B], timestamp=now-900)
    other_snapshot = journal.capture(other_group, 'current', CURRENT, author=B, recipients=[A], timestamp=now)
    uncached_calls = []
    with monkeypatch.context() as m:
        m.setenv('TRANSLATION_CONTEXT_CACHE_ENABLED', '0')
        for i in range(6):
            app._tl.__dict__.clear()
            app._tl.group_id, app._tl.user_id = other_group, B
            app._tl.conversation_snapshot = copy.deepcopy(other_snapshot)
            before = len(pipeline.calls)
            assert app.translate(CURRENT, 'zh', 'id') == '@十元 ' + GOOD
            uncached_calls.append(len(pipeline.calls) - before)
    result = {'mode': 'offline', 'network': 'blocked', 'live_ai_requests': 0, 'line_messages_sent': 0,
        'metrics': 'Adapter SDK request counts and text characters; context scenarios count generation requests at the mocked coordinator. Not billed tokens/costs; timing is local CPU/I/O with fixed AI responses',
        'corpus_sha256': hashlib.sha256(json.dumps(corpus, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        'rows': rows, 'summary': {
            'input_text_characters_by_provider': {p: sum(r['input_text_characters'] for r in rows if r['provider'] == p)
                for p in ('anthropic', 'openai', 'gemini')},
            'samples_per_provider': len(corpus['samples']),
            'exact_context_calls_per_turn': calls_per_turn,
            'exact_context_calls_total': sum(calls_per_turn),
            'exact_context_local_ms': times,
            'exact_context_repeated_median_local_ms': statistics.median(times[1:]),
            'cache_disabled_context_calls_per_turn': uncached_calls,
            'cache_disabled_context_calls_total': sum(uncached_calls),
        }}
    Path(os.environ['ECONOMY_OUTPUT']).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
