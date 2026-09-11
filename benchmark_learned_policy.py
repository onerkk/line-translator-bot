"""Offline latency probe for the local learned-rule path, not LINE/model speed.

Run: python benchmark_learned_policy.py
Uses an isolated temporary database and synthetic rule-index load. No credentials
or network calls; existing production memory is never modified.
"""
import json
import os
from pathlib import Path
import statistics
import tempfile
import time


def main():
    with tempfile.TemporaryDirectory(prefix='learned-policy-bench-') as directory:
        os.environ['ACTIVE_LEARNING_DB_PATH'] = str(Path(directory) / 'learning.db')
        os.environ['ACTIVE_LEARNING_VECTOR_SYNC'] = '0'
        import active_learning as learning
        import translation_learning_policy as policy
        source = '支數入庫前檢查一下再按。'
        bad = 'Periksa jumlah batang sebelum memasukkannya ke gudang.'
        good = 'Periksa jumlah batang sebelum mencatatnya dalam sistem.'
        lessons = policy.derive(source, bad, good, 'zh', 'id', reviewed=True,
                                cacheable=True, validator=learning.validate_correction)
        assert lessons, 'benchmark requires a measured local correction'
        fingerprint = learning._validator_fingerprint()
        with learning._connect() as conn:
            # Index-load fixtures, not claims of 300 independent real corrections.
            for index in range(300):
                policy.store(conn, lessons, source=source + str(index), src='zh', tgt='id',
                             group='offline-benchmark', policy=fingerprint, event_id=index + 1)
        query = '先核對數量再登錄。'
        for _ in range(10): learning.prepare_translation(query, 'zh', 'id', 'offline-benchmark')
        durations = []
        for _ in range(300):
            start = time.perf_counter()
            snapshot = learning.prepare_translation(query, 'zh', 'id', 'offline-benchmark')
            prompt = policy.build_prompt(snapshot)
            durations.append((time.perf_counter() - start) * 1000)
        durations.sort()
        print(json.dumps({
            'scope': 'SQLite rule selection + prompt assembly only; no network',
            'synthetic_index_sources': 300, 'iterations': len(durations),
            'p50_ms': round(statistics.median(durations), 3),
            'p95_ms': round(durations[int(len(durations) * 0.95) - 1], 3),
            'max_ms': round(max(durations), 3),
            'prompt_chars': len(prompt), 'learned_categories': len(snapshot['rules']),
            'translation_api_calls': 0, 'embedding_api_calls': 0,
        }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
