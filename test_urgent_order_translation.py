"""Urgent order attributes and pending requests must survive every exit path."""
import pytest
import time
from types import SimpleNamespace

import factory_message_semantics as semantics
import factory_translation_guard as guard
import translation_quality_gate as quality
import expressive_engine as expressive
import factory_order_semantics as orders
import translation_extras

SOURCE = '急單看一下，幫忙處理'
BAD = 'Tolong segera periksa order ini dan bantu proses.'
GOOD = 'Tolong periksa work order mendesak ini dan bantu menanganinya.'


@pytest.mark.parametrize('target', [BAD, BAD + ' ✅'])
def test_reported_error_is_rejected_at_both_shared_quality_boundaries(target):
    assert not quality.validate_translation(SOURCE, target, 'zh', 'id').ok
    assert not guard.validate_translation(SOURCE, target, 'zh', 'id').ok


def test_short_request_has_a_complete_source_based_translation():
    frame = semantics.build_frame(SOURCE, 'zh', 'id')
    assert frame['active'] and frame['complete']
    result = semantics.translate_source_directly(SOURCE, 'zh', 'id')
    assert 'work order mendesak' in result
    assert 'periksa' in result and 'bantu' in result and 'menanganinya' in result
    assert '✅' not in result
    assert quality.validate_translation(SOURCE, result, 'zh', 'id').ok


@pytest.mark.parametrize('intensity', ['natural', 'lively'])
def test_formal_work_request_never_gets_an_invented_success_icon(intensity):
    result = expressive.enhance_translation(SOURCE, GOOD, source_language='zh',
        settings=expressive.ExpressiveSettings(display_mode='emoji', intensity=intensity, images_enabled=False))
    assert result.text == GOOD and result.decorated_count == 0


def test_final_delivery_cannot_return_the_reported_bad_translation(monkeypatch):
    import app
    result = app._final_delivery_guard(SOURCE, BAD + ' ✅', 'zh', 'id')
    assert result and 'work order mendesak' in result and '✅' not in result


@pytest.mark.parametrize('source,required', [
    ('急單看一下，幫忙處理', ('periksa', 'bantu menanganinya')),
    ('這張急單麻煩看一下，協助處理一下', ('ini', 'periksa', 'bantu menanganinya')),
    ('请看一下这笔加急工单，帮忙处理', ('ini', 'periksa', 'bantu menanganinya')),
    ('麻煩幫忙處理這筆緊急訂單', ('bantu menangani', 'ini')),
    ('那份急單請先處理', ('itu', 'terlebih dahulu')),
    ('急單請立刻看一下，幫忙處理', ('segera', 'periksa', 'bantu menanganinya')),
    ('急單請檢查一下', ('periksa',)),
    ('急單幫忙處理一下', ('bantu menangani',)),
    ('急單 看一下，幫忙處理！', ('periksa', 'bantu menanganinya')),
    ('急用的工單請優先處理', ('terlebih dahulu',)),
])
def test_complete_grammar_generalizes_without_a_sentence_lookup(source, required):
    result = semantics.translate_source_directly(source, 'zh', 'id')
    assert result and 'work order mendesak' in result
    assert all(word in result for word in required), result
    assert quality.validate_translation(source, result, 'zh', 'id').ok
    assert guard.validate_translation(source, result, 'zh', 'id').ok


@pytest.mark.parametrize('target', [
    GOOD,
    'Mohon cek work order yang mendesak ini dan bantu prosesnya.',
    'Tolong lihat pesanan mendesak ini dan bantu menanganinya.',
    'Mohon periksa order mendesak ini dan tangani order tersebut.',
    'Tolong periksa work order ini yang mendesak dan bantu menanganinya.',
    'Tolong periksa urgent work order ini dan bantu prosesnya.',
])
def test_natural_equivalents_do_not_trigger_rewrite_or_rejection(target):
    assert quality.validate_translation(SOURCE, target, 'zh', 'id').ok
    assert guard.validate_translation(SOURCE, target, 'zh', 'id').ok


@pytest.mark.parametrize('bad', [
    BAD,
    'Tolong periksa work order ini dan bantu proses. Pengirimannya mendesak.',
    'Tolong periksa work order biasa ini dan bantu menanganinya.',
    'Work order mendesak ini sudah selesai ditangani.',
    'Tolong periksa work order mendesak ini.',
    'Tolong bantu menangani work order mendesak ini.',
    GOOD + ' ✅',
])
def test_missing_relations_and_invented_completion_are_not_fluent_successes(bad):
    assert not quality.validate_translation(SOURCE, bad, 'zh', 'id').ok
    assert not guard.validate_translation(SOURCE, bad, 'zh', 'id').ok


@pytest.mark.parametrize('source', [
    '急單看一下，幫忙處理，下午四點前完成',
    '急單 A-001 看一下，幫忙處理',
    '急單有3支，幫忙處理',
    '急單看一下，幫忙處理？',
    '如果是急單，請幫忙處理',
    '急單先不要處理',
    '我已經處理完急單',
    '這張急單處理完再看下一張',
    '急單處理，檢查一下',
    '急單看一下，Budi幫忙處理',
    '急件看一下，幫忙處理',
    '急單看一下\n幫忙處理，另外先停機',
])
def test_incomplete_grammar_never_drops_unknown_details_to_take_the_fast_path(source):
    assert orders.build_frame(source)['active']
    assert orders.deterministic_translation(orders.build_frame(source)) == ''
    result = semantics.translate_source_directly(source, 'zh', 'id')
    assert result == ''


@pytest.mark.parametrize('source,target', [
    ('不是急單，幫忙處理', 'Ini bukan work order mendesak. Tolong bantu menanganinya.'),
    ('這是不急的工單', 'Ini work order yang tidak mendesak.'),
    ('這是普通工單', 'Ini work order biasa.'),
    ('這不是急件', 'Ini bukan dokumen mendesak.'),
])
def test_negated_urgency_and_ordinary_orders_keep_their_polarity(source, target):
    frame = orders.build_frame(source)
    assert frame['active']
    assert orders.validate_translation(frame, target)[0]
    assert not orders.validate_translation(frame, 'Ini work order mendesak.')[0]
    assert orders.deterministic_translation(frame) == ''


def test_order_codes_and_numbered_items_cannot_borrow_another_orders_urgency():
    source = '1. A-01 是急單。\n2. B-02 不是急單。'
    good = '1. A-01 adalah work order mendesak.\n2. B-02 bukan work order mendesak.'
    swapped = '1. A-01 bukan work order mendesak.\n2. B-02 adalah work order mendesak.'
    wrong_ids = '1. B-02 adalah work order mendesak.\n2. A-01 bukan work order mendesak.'
    frame = orders.build_frame(source)
    assert orders.validate_translation(frame, good)[0]
    assert not orders.validate_translation(frame, swapped)[0]
    assert not orders.validate_translation(frame, wrong_ids)[0]
    assert orders.validate_translation(orders.build_frame('A-01 是急單'), 'Work order A-01 ini mendesak.')[0]


@pytest.mark.parametrize('source,target,bad', [
    ('Tolong periksa work order mendesak ini.', '請查看這張急單。', '請立刻查看這张訂單。'),
    ('Ini bukan work order mendesak.', '這不是急單。', '這是急單。'),
    ('Ini work order biasa.', '這是普通工單。', '這是急單。'),
    ('Work order ini sangat mendesak.', '這張工單非常緊急。', '這張工單不緊急。'),
    ('Pesanan ini tidak mendesak.', '這筆訂單不急。', '這筆訂單很急。'),
])
def test_reverse_translation_preserves_the_same_order_attribute(source, target, bad):
    frame = orders.build_frame(source, 'id', 'zh')
    assert frame['active'] and orders.validate_translation(frame, target)[0]
    assert not orders.validate_translation(frame, bad)[0]
    assert not guard.validate_translation(source, bad, 'id', 'zh').ok


@pytest.mark.parametrize('source,src,tgt', [
    ('這不是普通工單，幫忙處理', 'zh', 'id'),
    ('這張訂單不是一般訂單', 'zh', 'id'),
    ('Ini bukan work order biasa.', 'id', 'zh'),
])
def test_unusual_orders_are_not_assumed_to_be_urgent_or_nonurgent(source, src, tgt):
    assert not orders.build_frame(source, src, tgt)['active']


def test_requests_do_not_get_success_marks_even_when_the_formal_switch_is_off():
    for source, target in [(SOURCE, GOOD), ('麻煩處理一下', 'Tolong bantu proses.'),
                           ('Tolong bantu proses.', '麻煩協助處理。')]:
        for intensity in ('subtle', 'natural', 'lively'):
            result = expressive.enhance_translation(source, target,
                settings=expressive.ExpressiveSettings(display_mode='emoji', intensity=intensity,
                                                       formal_safety_enabled=False, images_enabled=False))
            assert not any(marker in result.text for marker in ('✅', '☑', '✔', '🎉', '🥳'))


def test_original_symbols_and_casual_expression_are_preserved():
    source, target = '急單已完成 ✅', 'Work order mendesak sudah selesai. ✅'
    result = expressive.enhance_translation(source, target,
        settings=expressive.ExpressiveSettings(images_enabled=False))
    assert result.text == target
    casual = expressive.enhance_translation('謝謝你的幫忙', 'Terima kasih atas bantuannya.',
        settings=expressive.ExpressiveSettings(display_mode='emoji', images_enabled=False))
    assert casual.decorated_count == 1 and '🙏' in casual.text


@pytest.mark.parametrize('source,target', [
    ('恭喜你，太好了！', 'Selamat!'),
    ('成功了！', 'Berhasil!'),
])
def test_explicit_celebration_still_uses_the_existing_expression_feature(source, target):
    result = translation_extras.build_expression_plan(source, target, mode='emoji')
    assert result.decorated_count == 1
    assert any(marker in result.text for marker in ('🎉', '🥳', '✨'))


@pytest.mark.parametrize('source,target', [
    ('請確認已完成', 'Mohon konfirmasi bahwa pekerjaan sudah selesai.'),
    ('尚未完成', 'Belum selesai.'),
    ('It is not completed.', 'Belum selesai.'),
    ('Tidak berhasil.', '沒有成功。'),
])
def test_unconfirmed_completion_never_receives_an_invented_success_marker(source, target):
    result = translation_extras.build_expression_plan(source, target, mode='emoji')
    assert not any(marker in result.text for marker in ('✅', '☑', '✔', '🎉', '🥳', '✨'))


def test_urgent_request_works_without_any_ai_or_image_call(monkeypatch):
    import app
    def forbidden(*args, **kwargs):
        pytest.fail('A fully consumed urgent-order request must not depend on provider or image I/O')
    for name in ('_translate_core', 'translate_google', 'translate_openai'):
        monkeypatch.setattr(app, name, forbidden)
    monkeypatch.setattr(app, 'translation_cache', {})
    monkeypatch.setattr(app._tl, 'from_image_ocr', False, raising=False)
    monkeypatch.setattr(app._tl, 'conversation_snapshot', None, raising=False)
    for _ in range(2):
        result = app.translate(SOURCE, 'zh', 'id')
        assert result == GOOD
        assert app._get_translation_outcome()['status'] == 'delivered'


def test_old_and_falsely_verified_cache_entries_cannot_reintroduce_bad_text(monkeypatch):
    import app
    monkeypatch.setattr(app, 'translation_cache', {})
    monkeypatch.setattr(app._tl, 'semantic_contract', {}, raising=False)
    monkeypatch.setattr(app, '_translation_cache_context_bound', lambda *_: False)
    key = (SOURCE, 'zh', 'id', app._translation_cache_scope())
    for fingerprint in ('previous-release', app._translation_cache_asset_fingerprint()):
        app.translation_cache[key] = (BAD, time.time(), fingerprint)
        assert app.cache_get(SOURCE, 'zh', 'id') is None
    app.cache_set(SOURCE, 'zh', 'id', BAD, force=True)
    assert key not in app.translation_cache
    app.cache_set(SOURCE, 'zh', 'id', GOOD, force=True)
    assert app.cache_get(SOURCE, 'zh', 'id') == GOOD


def test_rejected_output_is_not_learned_even_when_marked_human_corrected(monkeypatch):
    import app
    writes = []
    monkeypatch.setattr(app.tm_module, 'tm_store', lambda *a, **k: writes.append((a, k)))
    app._post_translation_async(SOURCE, BAD, 'zh', 'id', '', 'human_corrected', 1.0, {}, 'current-policy')
    assert writes == []
    app._post_translation_async(SOURCE, GOOD, 'zh', 'id', '', 'verified-model', 1.0, {}, 'current-policy')
    assert len(writes) == 1 and writes[0][0][1] == GOOD


@pytest.mark.parametrize('mention', ['@All', '@阿馬', '@Budi'])
def test_fast_path_keeps_the_actual_addressee(mention):
    source = mention + ' ' + SOURCE
    result = semantics.translate_source_directly(source, 'zh', 'id')
    assert result == mention + ' ' + GOOD
    assert quality.validate_translation(source, result, 'zh', 'id').ok


def test_source_rules_and_cache_identity_follow_the_actual_order_module(monkeypatch):
    import app
    prompt = semantics.build_prompt(semantics.build_frame(SOURCE, 'zh', 'id'))
    assert '<factory_order_relations>' in prompt and 'work order mendesak' in prompt
    assert 'Tolong periksa' not in prompt  # Relational constraints, not a copied answer.
    first = app._translation_cache_asset_fingerprint()
    monkeypatch.setattr(orders, 'BUILD_ID', 'test-next-order-semantics')
    assert app._translation_cache_asset_fingerprint() != first


@pytest.mark.parametrize('decoration', ['bad_symbol', 'empty', 'changed_wording'])
def test_failed_decoration_retains_already_verified_translation_without_retry(monkeypatch, decoration):
    import app
    source = SOURCE + '，請在下午4點前完成'
    base = GOOD + ' Mohon selesaikan sebelum pukul 4 sore.'
    changed = {'bad_symbol': base + ' ✅', 'empty': '', 'changed_wording': BAD}[decoration]
    calls = []
    monkeypatch.setattr(app._tl, 'from_image_ocr', False, raising=False)
    monkeypatch.setattr(app._tl, 'conversation_snapshot', None, raising=False)
    monkeypatch.setattr(app, '_translate_core', lambda *a, **k: calls.append('generation') or base)
    monkeypatch.setattr(app.expressive_engine_module, 'enhance_translation', lambda *a, **k: SimpleNamespace(
        text=changed, decorated_count=1, tone='request'))
    monkeypatch.setattr(app, '_emergency_translation_fallback', lambda *a, **k: pytest.fail('Decoration must not trigger provider retry'))
    assert app.translate(source, 'zh', 'id') == base
    assert calls == ['generation'] and app._get_translation_outcome()['status'] == 'delivered'
