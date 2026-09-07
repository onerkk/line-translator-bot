"""Reference reuse keeps live corrections, group scopes and validation intact."""
import json
from pathlib import Path

import pytest

import translation_casebook as cases


@pytest.fixture(autouse=True)
def clear_ranking_cache():
    cases._rank_snapshot.cache_clear()
    yield
    cases._rank_snapshot.cache_clear()


def example(target='Periksa PMI sebelum produksi.'):
    return {'zh': '生產前要檢測PMI。', 'id': target, 'dir': 'zh2id'}


def test_retrieval_limits_share_ranking_without_reusing_mutable_results(monkeypatch):
    count = []
    real = cases._rank_references
    def rank(*args):
        count.append(1)
        return real(*args)
    monkeypatch.setattr(cases, '_rank_references', rank)
    examples = [example(), {'zh': '入庫前確認標籤。', 'id': 'Periksa label sebelum masuk gudang.'}]
    first = cases.retrieve('生產前要檢測PMI。', 'zh', 'id', examples=examples, max_cases=8)
    first[0]['target'] = 'poisoned'
    first[0]['distinctive_anchors'].append('poisoned')
    second = cases.retrieve('生產前要檢測PMI。', 'zh', 'id', examples=examples, max_cases=4)
    assert len(count) == 1
    assert second[0]['target'] == example()['id']
    assert 'poisoned' not in second[0]['distinctive_anchors']
    examples[0]['id'] = 'PMI wajib diperiksa sebelum produksi.'
    third = cases.retrieve('生產前要檢測PMI。', 'zh', 'id', examples=examples)
    assert len(count) == 2 and third[0]['target'] == examples[0]['id']


def test_edited_guards_glossary_and_scoped_corrections_invalidate_ranking():
    source = example()['zh']
    examples = [example()]
    glossary = {'PMI': {'idn': 'pemeriksaan PMI', 'aliases_id': ['PMI']}}
    def read(corrections=()):
        return cases.retrieve(source, 'zh', 'id', examples=examples, glossary=glossary, corrections=corrections)
    read(); read()
    assert cases._rank_snapshot.cache_info().misses == 1
    glossary['PMI']['aliases_id'].append('identifikasi material')
    read()
    assert cases._rank_snapshot.cache_info().misses == 2
    examples[0]['source_match'] = {'regex_any': ['['], 'min_score': 1}
    assert read() == []
    examples[0].pop('source_match')
    correction = {'src_text': source, 'src_lang': 'zh', 'tgt_lang': 'id',
                  'corrected_translation': 'Lakukan pemeriksaan PMI sebelum produksi.',
                  'status': 'approved', 'validation_state': 'passed', 'group_id': 'group-a', 'revision': 2}
    assert read([correction])[0]['target'] == correction['corrected_translation']
    # Another group's caller supplies only its own approved corrections.
    assert read()[0]['target'] == example()['id']
    correction['status'] = 'rejected'
    assert read([correction])[0]['target'] == example()['id']


def test_cached_and_uncached_results_match_full_holdout_corpus(monkeypatch):
    import app
    corpus = json.loads((Path(__file__).parent / 'tests/data/cp_holdout_20260907.json').read_text())
    examples, corrections = app._scoped_translation_casebook_inputs('reference-reuse-test')
    for sample in corpus['samples']:
        args = (sample['source'], sample['src'], sample['tgt'])
        kwargs = dict(examples=examples, corrections=corrections, glossary=app.GLOSSARY_LOOKUP, max_cases=8)
        cached = cases.retrieve(*args, **kwargs)
        with monkeypatch.context() as m:
            m.setattr(cases, '_rank_snapshot', cases._rank_snapshot.__wrapped__)
            uncached = cases.retrieve(*args, **kwargs)
        assert cached == uncached, sample['id']


def test_large_reference_snapshot_does_not_fill_process_cache():
    large = example()
    large['reason'] = 'x' * 270000
    result = cases.retrieve(large['zh'], 'zh', 'id', examples=[large])
    assert result[0]['target'] == large['id']
    assert cases._rank_snapshot.cache_info().currsize == 0


def test_reference_reuse_never_skips_current_validation(runtime, monkeypatch):
    import app
    from test_translation_notice_availability import SOURCE, TARGET
    app._tl.group_id = 'notice-group'
    assert app.translate(SOURCE, 'zh', 'id') == TARGET
    checked = []
    validate = app.tqg_module.validate_translation
    def current_validation(*args, **kwargs):
        checked.append(1)
        return validate(*args, **kwargs)
    monkeypatch.setattr(app.tqg_module, 'validate_translation', current_validation)
    assert app.translate(SOURCE, 'zh', 'id') == TARGET
    assert checked and len(runtime.generations) == 1


from test_translation_notice_availability import runtime  # shared offline transport fixture
