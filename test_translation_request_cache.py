"""Latency reuse must never weaken current-source validation or leak state."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
import os
import sqlite3

import pytest

import app
import factory_knowledge as knowledge
import factory_message_semantics as relations
import factory_source_understanding as understanding
import translation_quality_gate as quality
import translation_request_cache as memo
import translation_retry_queue as queue
from test_translation_notice_availability import SOURCE, TARGET


def test_scope_detaches_nested_results_and_resets_on_worker_reuse():
    calls = []
    @memo.memoize
    def parse(source):
        calls.append(source)
        return {'items': [{'text': source}]}
    with memo.scope():
        first = parse('original')
        first['items'][0]['text'] = 'corrupt'
        assert parse('original') == {'items': [{'text': 'original'}]}
        with memo.scope():
            assert parse('original')['items'][0]['text'] == 'original'
        assert parse('original')['items'][0]['text'] == 'original'
    with memo.scope():
        parse('original')
    assert calls == ['original'] * 3
    assert memo._STATE.get() is None


def test_mutable_inputs_and_keyword_policies_are_part_of_the_key():
    calls = []
    @memo.memoize
    def calculate(data, *, enabled=True):
        calls.append(1)
        return copy.deepcopy((data, enabled))
    data = {'names': ['first']}
    with memo.scope():
        assert calculate(data) == calculate(data)
        data['names'].append('second')
        assert calculate(data)[0]['names'] == ['first', 'second']
        assert calculate(data, enabled=False)[1] is False
    assert len(calls) == 3


def test_failures_are_not_cached_and_scope_is_removed_after_exception():
    calls = []
    @memo.memoize
    def unreliable():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('temporary failure')
        return 'recovered'
    with pytest.raises(ValueError), memo.scope():
        with pytest.raises(RuntimeError):
            unreliable()
        assert unreliable() == 'recovered'
        assert unreliable() == 'recovered'
        raise ValueError('request failed')
    assert len(calls) == 2
    assert memo._STATE.get() is None


def test_copied_context_cannot_share_mutable_request_cache_across_workers():
    calls = []
    @memo.memoize
    def parse(source):
        calls.append(source)
        return source
    with memo.scope(), ThreadPoolExecutor(max_workers=1) as executor:
        parse('same')
        assert executor.submit(copy_context().run, parse, 'same').result() == 'same'
        assert parse('same') == 'same'
    assert calls == ['same', 'same']


def test_large_inputs_and_generators_take_the_uncached_path_without_consumption():
    calls = []
    @memo.memoize
    def first(values):
        calls.append(1)
        return next(values)
    values = iter(['one', 'two'])
    with memo.scope():
        assert first(values) == 'one'
        assert first(values) == 'two'
        for _ in range(2):
            assert memo.reuse('large', 'x' * (memo._MAX_TEXT + 1), lambda: 7) == 7
        assert memo._STATE.get()['values'] == {}
    assert len(calls) == 2


def test_request_memory_is_bounded():
    with memo.scope():
        for number in range(memo._MAX_ENTRIES + 5):
            assert memo.reuse('values', number, lambda: number) == number
        assert len(memo._STATE.get()['values']) == memo._MAX_ENTRIES


def test_same_source_analysis_is_reused_but_changed_candidate_is_rejected(monkeypatch):
    calls = []
    build = relations._build_zh_id_frame
    def count(*args):
        calls.append(args[0])
        return build(*args)
    monkeypatch.setattr(relations, '_build_zh_id_frame', count)
    with memo.scope():
        assert app._final_delivery_guard(SOURCE, TARGET, 'zh', 'id') == TARGET
        assert app._final_delivery_guard(SOURCE, TARGET, 'zh', 'id') == TARGET
        assert app._final_delivery_guard(SOURCE, TARGET.replace('PMI', ''), 'zh', 'id') is None
    assert calls.count(SOURCE) == 1


def test_glossary_edit_and_paragraph_policy_revalidate_same_candidate():
    with memo.scope():
        args = ('Simpan barang ini.', '請保留 barang。', 'id', 'zh')
        assert quality.validate_translation(*args, glossary_pairs=[('barang', 'barang')]).ok
        assert not quality.validate_translation(*args, glossary_pairs=[('barang', '料件')]).ok
        args = ('請先停機。\n\n再檢查。', 'Hentikan mesin dahulu. Kemudian periksa.', 'zh', 'id')
        assert quality.validate_translation(*args, require_paragraph_fidelity=False).ok
        strict = quality.validate_translation(*args, require_paragraph_fidelity=True)
        assert 'paragraph_count:2->1' in strict.issues
        strict.issues.clear()
        assert 'paragraph_count:2->1' in quality.validate_translation(*args, require_paragraph_fidelity=True).issues


def test_protected_name_input_never_reuses_an_unprotected_term_frame():
    with memo.scope():
        source = 'PMI檢測還沒完成'
        assert understanding.factory_term_facts(source, 'zh')
        assert understanding.factory_term_facts(source, 'zh', protected_names=['PMI']) == []


@pytest.mark.parametrize('source,target,bad', [
    ('PMI還沒檢測。', 'PMI belum diperiksa.', 'PMI sudah diperiksa.'),
    ('PMI檢測已經完成。', 'Pemeriksaan PMI sudah selesai.', 'Pemeriksaan PMI belum selesai.'),
    ('禁止混料，必須立即停機。', 'Dilarang mencampur material. Mesin harus segera dihentikan.',
     'Boleh mencampur material. Mesin harus segera dijalankan.'),
])
def test_opposite_states_cannot_borrow_previous_approval(source, target, bad):
    expected = app._final_delivery_guard(source, target, 'zh', 'id')
    assert expected
    with memo.scope():
        assert app._final_delivery_guard(source, target, 'zh', 'id') == expected
        assert app._final_delivery_guard(source, bad, 'zh', 'id') is None
        assert app._final_delivery_guard(source, target, 'zh', 'id') == expected


def test_knowledge_snapshots_and_retrieval_observe_edits_inside_same_request(tmp_path):
    path = tmp_path / 'knowledge.json'
    document = {'schema_version': 1, 'entries': [{
        'id': 'storage', 'directions': ['zh-id'], 'match': {'any_terms': ['儲格']},
        'examples': [{'source': '儲格', 'target': 'slot penyimpanan'}],
    }]}
    path.write_text(json.dumps(document))
    store = knowledge.FactoryKnowledgeStore(str(path))
    with memo.scope():
        examples = store.casebook_examples()
        examples[0]['source_match']['any_terms'].clear()
        assert store.casebook_examples()[0]['source_match']['any_terms'] == ['儲格']
        assert store.retrieve('儲格', 'zh', 'id')
        assert store.retrieve('儲格', 'id', 'zh') == []
        document['entries'][0]['enabled'] = False
        path.write_text(json.dumps(document))
        store.reload(force=True)
        assert store.retrieve('儲格', 'zh', 'id') == []
        assert store.casebook_examples() == []


def test_queue_reads_do_not_retake_migration_write_locks(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, 'DB_PATH', str(tmp_path / 'queue.db'))
    queue.enqueue('durable', {'source_text': '套環要補上'})
    statements = []
    connect = queue._connect
    def traced():
        conn = connect()
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(queue, '_connect', traced)
    assert queue.get('durable')['payload']['source_text'] == '套環要補上'
    assert not any(sql.startswith(('BEGIN', 'UPDATE', 'CREATE', 'ALTER')) for sql in statements)
    # Durability and exclusive ownership still apply to actual writes.
    with connect() as conn:
        assert conn.execute('PRAGMA synchronous').fetchone()[0] == 2
    assert queue.claim_job('durable', owner='first')
    assert not queue.claim_job('durable', owner='second')
    assert not queue.checkpoint('durable', {'source_text': 'wrong'}, owner='second')
    assert queue.get('durable')['payload']['source_text'] == '套環要補上'


def test_queue_migrates_legacy_jobs_after_database_replacement(tmp_path, monkeypatch):
    path = tmp_path / 'queue.db'
    monkeypatch.setattr(queue, 'DB_PATH', str(path))
    queue.initialize()
    legacy = tmp_path / 'legacy.db'
    with sqlite3.connect(legacy) as conn:
        conn.execute('''CREATE TABLE translation_retry_jobs (
            job_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
            attempts INTEGER DEFAULT 0, next_attempt_at REAL NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            last_error TEXT DEFAULT '', status TEXT DEFAULT 'pending',
            schema_version INTEGER DEFAULT 1)''')
        conn.execute('''INSERT INTO translation_retry_jobs
            (job_key,payload_json,attempts,next_attempt_at,created_at,updated_at,status)
            VALUES ('old','{"source_text":"preserved"}',9,1,1,1,'failed')''')
    os.replace(legacy, path)
    job = queue.get('old')
    assert job['payload']['source_text'] == 'preserved'
    assert job['attempts'] == 9 and job['status'] == 'pending'
    assert job['lease_owner'] == ''
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == queue._SCHEMA_VERSION
