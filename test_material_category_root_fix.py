"""User-confirmed 轉用 is an inventory material category in stock reports.

Use local fake transports to verify the actual pipeline and cache boundaries;
these checks do not claim live provider accuracy.
"""
import time

import pytest

import active_learning as learning
import ai_provider
import app
import factory_material_category as category
import factory_source_understanding as understanding
import factory_translation_guard as guard
import translation_quality_gate as quality
from test_translation_instruction_cost_quality import offline_transport, response
from test_translation_learned_policy import database, public_pipeline


SOURCE = '轉用全入庫了'
BAD = 'Sudah dialihkan dan seluruhnya sudah masuk stok.'
GOOD = 'Seluruh material alih guna sudah masuk stok.'


def test_reported_verb_translation_is_rejected_at_shared_boundaries():
    assert not quality.validate_translation(SOURCE, BAD, 'zh', 'id').ok
    assert not guard.validate_translation(SOURCE, BAD, 'zh', 'id').ok
    assert not learning.validate_correction(SOURCE, BAD, 'zh', 'id')['ok']
    assert not app._tm_bypass_integrity_ok(SOURCE, BAD, 'zh', 'id')[0]


def test_source_complete_repair_preserves_all_and_completed_stock_status():
    assert quality.canonicalize_source_terms(SOURCE, BAD, 'zh', 'id') == GOOD
    assert quality.validate_translation(SOURCE, GOOD, 'zh', 'id').ok
    assert guard.validate_translation(SOURCE, GOOD, 'zh', 'id').ok
    assert quality.canonicalize_source_terms(SOURCE, GOOD, 'zh', 'id') == GOOD


def test_first_prompt_explains_category_not_another_order():
    prompt = understanding.build_prompt(understanding.analyze(SOURCE, 'zh'))
    assert 'material_category' in prompt and 'material alih guna' in prompt
    assert '品質異常' in prompt
    hint = app.build_factory_context_hint(SOURCE, 'zh', 'id')
    assert '轉用=dialihkan untuk order lain' not in hint
    assert '轉用 → dialihkan untuk order lain' not in hint


def test_public_pipeline_repairs_screenshot_with_one_provider_call(public_pipeline, monkeypatch):
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(kwargs)
        return response(BAD)
    monkeypatch.setattr(ai_provider, '_dispatch_provider', dispatch)
    assert app.translate(SOURCE, 'zh', 'id').rstrip('.') == GOOD.rstrip('.')
    assert len(calls) == 1
    assert 'material_category' in str(calls[0]['messages'])
    assert app.cache_get(SOURCE, 'zh', 'id').rstrip('.') == GOOD.rstrip('.')


def test_old_wrong_translation_cannot_reenter_current_cache(public_pipeline, monkeypatch):
    key = (SOURCE, 'zh', 'id', app._translation_cache_scope())
    cache = {key: (BAD, time.time(), app._translation_cache_asset_fingerprint())}
    monkeypatch.setattr(app, 'translation_cache', cache)
    assert app.cache_get(SOURCE, 'zh', 'id') is None
    assert key not in cache


@pytest.mark.parametrize('source,expected', [
    ('轉用料已入庫', 'Material alih guna sudah masuk stok.'),
    ('轉用物料全數已經入庫完成。', GOOD),
    ('转用料全部已经入库了', GOOD),
    ('轉 用全都入庫了', GOOD),
    ('轉用料全部尚未入庫', 'Seluruh material alih guna belum masuk stok.'),
    ('轉用料尚未全部入庫', 'Material alih guna belum seluruhnya masuk stok.'),
    ('轉用料尚未入庫', 'Material alih guna belum masuk stok.'),
    ('轉用料還沒全入庫', 'Material alih guna belum seluruhnya masuk stok.'),
])
def test_material_noun_receipt_and_quantifier_are_separate_facts(source, expected):
    assert quality.canonicalize_source_terms(source, BAD, 'zh', 'id') == expected
    assert quality.validate_translation(source, expected, 'zh', 'id').ok
    assert guard.validate_translation(source, expected, 'zh', 'id').ok


@pytest.mark.parametrize('source,bad', [
    (SOURCE, 'Material alih guna sudah masuk stok.'),  # lost 全
    (SOURCE, 'Seluruh material alih guna belum masuk stok.'),
    (SOURCE, 'Seluruh material alih guna sudah dikirim.'),  # receipt is not shipment
    (SOURCE, 'Seluruh material alih guna sudah masuk stok dan dialihkan untuk order lain.'),
    (SOURCE, 'Seluruh material alih guna yang cacat sudah masuk stok.'),  # unreported specific cause
    ('轉用料尚未全部入庫', 'Seluruh material alih guna belum masuk stok.'),
    ('轉用料全部尚未入庫', 'Material alih guna belum seluruhnya masuk stok.'),
    ('轉用料已入庫', GOOD),  # all may not be invented
])
def test_receipt_state_scope_or_extra_event_cannot_be_accepted(source, bad):
    assert not quality.validate_translation(source, bad, 'zh', 'id').ok
    assert not guard.validate_translation(source, bad, 'zh', 'id').ok
    assert not learning.validate_correction(source, bad, 'zh', 'id')['ok']


@pytest.mark.parametrize('good', [
    'Semua barang alih guna telah diterima di gudang.',
    'Material yang dialihkan penggunaannya sudah seluruhnya masuk stok.',
    'Material alih guna sudah masuk stok seluruhnya.',
    'Seluruh material alih guna sudah tercatat sebagai stok.',
])
def test_faithful_noun_paraphrases_do_not_require_one_fixed_wording(good):
    assert not category.validate(category.build_facts(SOURCE, 'zh'), good, 'id')
    assert category.canonicalize(SOURCE, good, 'zh', 'id') == good


@pytest.mark.parametrize('prefix', ['@小麥 （研磨股班長）', '__MENTION_0__', '@All'])
def test_raw_screenshot_and_protected_mentions_survive_public_translation(prefix, public_pipeline, monkeypatch):
    source = prefix + ' 轉用全入庫了'
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(kwargs)
        return response(prefix + ' ' + BAD)
    monkeypatch.setattr(ai_provider, '_dispatch_provider', dispatch)
    translated = app.translate(source, 'zh', 'id')
    assert translated.rstrip('.') == (prefix + ' ' + GOOD).rstrip('.')
    assert len(calls) == 1
    # The public entry point uses protected mention keys for its cache.
    assert app.translate(source, 'zh', 'id') == translated
    assert len(calls) == 1


@pytest.mark.parametrize('source,target', [
    ('轉用料明天入庫。', 'Material alih guna akan masuk stok besok.'),
    ('轉用料全部入庫了嗎？', 'Apakah seluruh material alih guna sudah masuk stok?'),
    ('請將轉用料全部入庫。', 'Mohon masukkan seluruh material alih guna ke stok.'),
    ('轉用料尚未入庫，先檢查工單。', 'Material alih guna belum masuk stok; periksa work order dahulu.'),
    ('轉用料全入庫了，正常料還沒。', 'Seluruh material alih guna sudah masuk stok, sedangkan material normal belum.'),
    ('如果轉用料已入庫，就通知班長。', 'Jika material alih guna sudah masuk stok, beri tahu pemimpin shift.'),
    ('轉用料10支已入庫。', '10 batang material alih guna sudah masuk stok.'),
    ('轉用料都還沒全部入庫。', 'Belum seluruh material alih guna masuk stok.'),
    ('轉用料已入庫，https://example.invalid/ID7', 'Material alih guna sudah masuk stok, https://example.invalid/ID7'),
])
def test_partial_facts_do_not_erase_dates_questions_conditions_quantities_or_other_clauses(source, target):
    facts = category.build_facts(source, 'zh')
    assert facts and facts[0]['receipt'] is None
    assert category.canonicalize(source, target, 'zh', 'id') == target
    assert not category.validate(facts, target, 'id')


@pytest.mark.parametrize('source,target,src,tgt', [
    ('把這批料轉用到別的訂單。', 'Alihkan material ini untuk order lain.', 'zh', 'id'),
    ('已經轉用入庫了。', 'Sudah dialihkan dan masuk stok.', 'zh', 'id'),
    ('改為轉用入庫。', 'Ubah menjadi penerimaan untuk penggunaan lain.', 'zh', 'id'),
    ('@轉用料 請檢查工單。', '@轉用料 Mohon periksa work order.', 'zh', 'id'),
    ('欄位寫著「轉用料」。', 'Kolom bertuliskan "轉用料".', 'zh', 'id'),
    ('查看 https://example.invalid/轉用料', 'Lihat https://example.invalid/轉用料', 'zh', 'id'),
    ('Material ini sudah dialihkan untuk order lain.', '這批材料已經轉用到別的訂單。', 'id', 'zh'),
])
def test_real_verbs_and_opaque_names_or_labels_are_not_forced_into_category(source, target, src, tgt):
    assert not category.build_facts(source, src)
    assert category.canonicalize(source, target, src, tgt) == target


def test_reverse_category_keeps_noun_and_stock_status():
    facts = category.build_facts(GOOD, 'id')
    assert facts
    assert not category.validate(facts, '轉用料已全部入庫。', 'zh')
    assert category.validate(facts, '已經轉用，且全部入庫了。', 'zh')
    assert category.validate(facts, '轉用料尚未全部入庫。', 'zh')


def test_protected_display_name_cannot_supply_category_evidence():
    facts = understanding.factory_term_facts('轉用料 請看工單。', 'zh', protected_names=('轉用料',))
    assert not any(fact['sense'] == 'material_category' for fact in facts)


def test_ocr_final_delivery_uses_same_category_repair(public_pipeline):
    app._tl.from_image_ocr = True
    result = app._final_delivery_guard(SOURCE, BAD, 'zh', 'id')
    assert result.rstrip('.') == GOOD.rstrip('.')
