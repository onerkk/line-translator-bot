"""Real reported candidates, nearby wording and counterexamples; offline only."""
import socket
from types import SimpleNamespace

import pytest

import app
import ai_provider
import factory_instruction_semantics as instructions
import factory_record_semantics as records
import factory_semantic_audit as audit
import factory_terminology as terminology
import translation_quality_gate as quality
import translation_retry_queue as queue
import translation_extras
import expressive_engine
from test_translation_notice_availability import runtime, event, delivered_text
from test_translation_instruction_cost_quality import offline_transport, response


SOURCE = '木箱有些沒有裝箱 小心別移到帳，非本月這兩捆也不要移出'
REPORTED = ('Sebagian material belum dimasukkan ke dalam peti kayu. '
    'Hati-hati, jangan memindahkannya ke area akun. '
    'Dua bundel yang bukan untuk bulan ini juga jangan dipindahkan keluar.')
GOOD = ('Sebagian material belum dimasukkan ke dalam peti kayu. '
    'Hati-hati, jangan pindahkan pencatatannya ke catatan stok. '
    'Catatan untuk dua bundel dalam kategori bukan bulan ini juga jangan dikeluarkan.')
MARKER_SOURCE = '木箱前面有 * 代表有裝箱，共13把10.7噸可以移出'
MARKER_REPORTED = ('Bagian depan peti kayu yang bertanda * berarti sudah dimasukkan ke dalam peti kayu. '
    'Total 13 bundel, 10.7 ton, dapat dipindahkan keluar.')
RUMOR_SOURCE = '一股說今天可能會有人進來巡廠，我不知道消息可信度，自己留意一點'
RUMOR_TARGET = ('Bagian Cold Drawing 1 bilang hari ini mungkin ada orang datang melakukan inspeksi pabrik. '
    'Saya tidak tahu seberapa dapat dipercaya informasi ini, jadi harap lebih waspada.')


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    monkeypatch.setattr(app, 'translation_cache', {})
    def offline(*_a, **_k):
        raise AssertionError('Regression tests may not connect to production')
    monkeypatch.setattr(socket.socket, 'connect', offline)
    try:
        yield
    finally:
        app._tl.__dict__.clear()
        app._tl.__dict__.update(previous)


def test_reported_accounting_error_is_detected_instead_of_keyword_approved():
    relations = instructions.build_relations(SOURCE)
    transfers = [r for r in relations if r['kind'] == 'record_transfer']
    assert [(r['direction'], r['prohibited']) for r in transfers] == [('in', True), ('out', True)]
    assert transfers[1]['noncurrent']
    assert instructions.validate_relations(relations, REPORTED)
    assert not instructions.validate_relations(relations, GOOD)
    assert not quality.validate_translation(SOURCE, REPORTED, 'zh', 'id').ok
    prompt = audit.build_prompt(audit.build_source_frame(SOURCE, 'zh', 'id'))
    assert 'pencatatan stok' in prompt
    assert '禁止此動作' in prompt


def test_bounded_record_term_normalization_preserves_packaging_quantity_and_negation():
    fixed = quality.canonicalize_source_terms(SOURCE, REPORTED, 'zh', 'id')
    assert fixed.startswith('Sebagian material belum dimasukkan ke dalam peti kayu.')
    assert 'area akun' not in fixed
    assert 'memindahkan catatannya ke pencatatan stok' in fixed
    assert 'Catatan untuk dua bundel' in fixed
    assert fixed.count('jangan') == 2
    assert 'bukan untuk bulan ini' in fixed
    assert quality.validate_translation(SOURCE, fixed, 'zh', 'id').ok
    assert quality.canonicalize_source_terms(SOURCE, fixed, 'zh', 'id') == fixed


def test_actual_handler_corrects_record_terms_with_one_generation(runtime):
    runtime.provider_result = REPORTED
    app.handle_message(event(SOURCE))
    delivered = delivered_text(runtime)
    assert 'area akun' not in delivered
    assert 'pencatatan stok' in delivered
    assert 'Catatan untuk dua bundel' in delivered
    assert 'peti kayu' in delivered
    assert len(runtime.generations) == 1
    assert len(runtime.sends) == 1
    assert queue.pending_count() == 0


@pytest.mark.parametrize('source, candidate', [
    ('這兩捆的帳先移出。', 'Keluarkan dulu catatan untuk dua bundel ini.'),
    ('這兩捆的帳不要移出。', 'Catatan untuk dua bundel ini jangan dikeluarkan.'),
    ('資料不要轉入庫存帳。', 'Jangan masukkan data ke pencatatan stok.'),
    ('资料不要转入库存账。', 'Data jangan dipindahkan ke pencatatan stok.'),
    ('帳先移出，材料搬出倉庫。', 'Keluarkan catatan dahulu. Pindahkan material keluar dari gudang.'),
])
def test_equivalent_wordings_preserve_record_action(source, candidate):
    relations = instructions.build_relations(source)
    assert any(r['kind'] == 'record_transfer' for r in relations)
    assert not instructions.validate_relations(relations, candidate)


@pytest.mark.parametrize('source', [
    '非本月的材料暫存在儲區，請把材料移出儲區。',
    '資料已核對，把材料移出木箱。',
    '請移到帳篷旁邊。',
    '把錢轉入帳戶。',
    '請登入帳號。',
])
def test_record_context_does_not_redefine_physical_actions_or_login(source):
    assert not records.build_transfers(source)
    target = 'Pindahkan material keluar dari gudang.'
    assert instructions.canonicalize_record_terms(source, target) == target


@pytest.mark.parametrize('source, candidate', [
    ('帳不要移出。', 'Keluarkan catatan.'),
    ('帳先移出。', 'Jangan keluarkan catatan.'),
    ('資料不要轉入庫存帳。', 'Masukkan data ke pencatatan stok.'),
    ('帳不要移出。', 'Data sudah benar. Jangan pindahkan material keluar.'),
    ('資料不要轉入庫存帳。', 'Jangan pindahkan data keluar dari pencatatan stok.'),
])
def test_wrong_polarity_direction_or_borrowed_record_words_are_rejected(source, candidate):
    assert instructions.validate_relations(instructions.build_relations(source), candidate)


@pytest.mark.parametrize('candidate', [
    GOOD.replace('dua bundel', 'tiga bundel'),
    GOOD.replace('juga jangan dikeluarkan', 'juga harus dikeluarkan'),
    GOOD.replace('bukan bulan ini', 'bulan lalu'),
])
def test_normalization_does_not_excuse_wrong_quantity_prohibition_or_period(candidate):
    assert app._final_delivery_guard(SOURCE, candidate, 'zh', 'id') is None


def test_numbered_shipping_item_does_not_borrow_accounting_scope():
    source = '1.非本月木箱不要移出倉庫。\n2.非本月的帳不要移出。'
    relations = instructions.build_relations(source)
    assert not [r for r in relations if r['kind'] == 'record_transfer' and r['item'] == '1']
    assert [r for r in relations if r['kind'] == 'record_transfer' and r['item'] == '2']
    wrong = ('1. Jangan keluarkan catatan kategori bukan bulan ini.\n'
             '2. Bundel bukan bulan ini jangan dipindahkan keluar.')
    assert instructions.validate_relations(relations, wrong)


@pytest.mark.parametrize('source, candidate, expected', [
    ('tolong bukakan komputer tengah', '請開啟中間計算機', '請開啟中間電腦'),
    ('Komputer sebelah kiri rusak.', '左邊的計算機故障。', '左邊的電腦故障。'),
    ('komputer I01 belum menyala', 'I01 計算機還沒開機', 'I01 電腦還沒開機'),
    ('kalkulator rusak', '計算機故障', '計算機故障'),
    ('Komputer dan kalkulator rusak.', '電腦和計算機故障。', '電腦和計算機故障。'),
    ('Buka aplikasi「計算機」di komputer.', '在電腦開啟「計算機」。', '在電腦開啟「計算機」。'),
])
def test_computer_is_distinct_from_calculator_and_ui_labels(source, candidate, expected):
    assert terminology.canonicalize_computer_translation(source, candidate, 'id', 'zh') == expected


def test_reported_computer_handler_preserves_mention_and_corrects_local_term(runtime):
    source = '@小麥（研磨股班長） tolong bukakan komputer tengah'
    runtime.provider_result = '__MENTION_0__ 請開啟中間計算機'
    app.handle_message(event(source))
    actual = delivered_text(runtime)
    assert '@小麥（研磨股班長）' in actual
    assert '中間電腦' in actual
    assert '計算機' not in actual
    assert len(runtime.generations) == 1
    assert queue.pending_count() == 0


def test_provider_admission_checks_local_correction_before_spending_on_retry():
    source = '@小麥（研磨股班長） tolong bukakan komputer tengah'
    candidate = '@小麥（研磨股班長） 請開啟中間計算機'
    assert not quality.validate_translation(source, candidate, 'id', 'zh').ok
    response = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=candidate), finish_reason='stop')])
    assert app._build_translation_response_validator(source, 'id', 'zh')(response, 'offline')[0]


def test_valid_alternative_record_wording_is_left_unchanged():
    assert quality.canonicalize_source_terms(SOURCE, GOOD, 'zh', 'id') == GOOD
    assert app._final_delivery_guard(SOURCE, GOOD, 'zh', 'id') == GOOD


@pytest.mark.parametrize('source', ['入帳時間分散一點。', '過帳日期不要修改。'])
def test_accounting_timestamp_is_not_a_separate_transfer_instruction(source):
    assert records.build_transfers(source) == []


def test_question_punctuation_does_not_create_an_affirmative_instruction():
    assert records.build_transfers('帳要移出？')[0]['prohibited'] is None


@pytest.mark.parametrize('source, candidate, src, tgt, required', [
    (SOURCE, REPORTED, 'zh', 'id', 'pencatatan stok'),
    ('tolong bukakan komputer tengah', '請開啟中間計算機', 'id', 'zh', '電腦'),
    (MARKER_SOURCE, MARKER_REPORTED, 'zh', 'id', 'keterangan peti kayu'),
])
def test_real_translation_pipeline_uses_one_mocked_provider_call(
        offline_transport, monkeypatch, source, candidate, src, tgt, required):
    calls = []
    sites = []
    def dispatch(*a, **k):
        import traceback
        sites.append([(f.name, f.lineno) for f in traceback.extract_stack() if f.filename.endswith('app.py')])
        calls.append(k)
        return response(candidate)
    monkeypatch.setattr(ai_provider, '_dispatch_provider', dispatch)
    monkeypatch.setattr(app, 'cache_get', lambda *_a, **_k: None)
    monkeypatch.setattr(app, 'cache_set', lambda *_a, **_k: None)
    monkeypatch.setattr(app, 'get_recent_media_scene', lambda *_a, **_k: '')
    monkeypatch.setattr(app, 'get_conv_context_enabled', lambda *_a, **_k: False)
    monkeypatch.setattr(app, 'translate_google',
        lambda *_a, **_k: pytest.fail('local term correction must not use NMT'))
    monkeypatch.setattr(app._BG_POST_EXECUTOR, 'submit', lambda *_a, **_k: None)
    monkeypatch.setattr(app.al_module, 'assess_review_risk',
        lambda *_a, **_k: {'requires_review': False, 'matches': []})
    app._tl.disable_tone_emoji = True
    app._tl.group_id = 'record-computer-offline'
    result = app.translate(source, src, tgt)
    assert required in result
    assert 'area akun' not in result and '計算機' not in result
    assert len(calls) == 1, sites
    prompt = '\n'.join(str(m['content']) for m in calls[0]['messages'])
    assert required in prompt
    assert quality.validate_translation(source, result, src, tgt).ok


def test_status_marker_describes_notation_and_preserves_all_numbers():
    assert not quality.validate_translation(MARKER_SOURCE, MARKER_REPORTED, 'zh', 'id').ok
    fixed = quality.canonicalize_source_terms(MARKER_SOURCE, MARKER_REPORTED, 'zh', 'id')
    assert fixed.startswith('Tanda * di depan keterangan peti kayu berarti material sudah dimasukkan')
    assert fixed.endswith('Total 13 bundel, 10.7 ton, dapat dipindahkan keluar.')
    assert quality.validate_translation(MARKER_SOURCE, fixed, 'zh', 'id').ok


@pytest.mark.parametrize('change', [
    ('*', '#'), ('sudah dimasukkan', 'belum dimasukkan'),
    ('13 bundel', '15 bundel'), ('10.7 ton', '107 ton'),
    ('dapat dipindahkan keluar', 'jangan dipindahkan keluar'),
])
def test_status_marker_normalization_cannot_hide_different_facts(change):
    bad = MARKER_REPORTED.replace(*change)
    assert app._final_delivery_guard(MARKER_SOURCE, bad, 'zh', 'id') is None


@pytest.mark.parametrize('source', [
    '木箱前面放了兩把材料。', '木箱正面有紅色星號。',
    '木箱前面有 * 代表要裝箱。',
])
def test_physical_crate_position_and_future_packing_are_not_completed_marker(source):
    assert records.build_status_markers(source) == []
    assert quality.canonicalize_source_terms(source, MARKER_REPORTED, 'zh', 'id') == MARKER_REPORTED


def test_unpacked_marker_variant_preserves_negative_state():
    source = '紙箱前面有★表示尚未裝箱'
    target = 'Tanda ★ di depan keterangan kardus menunjukkan bahwa material belum dikemas dalam kardus.'
    relations = records.build_status_markers(source)
    assert relations and records.validate_status_marker(relations[0], target)
    assert not records.validate_status_marker(relations[0], target.replace('belum', 'sudah'))


@pytest.mark.parametrize('intensity', ['subtle', 'natural', 'lively'])
def test_uncertain_inspection_does_not_get_an_invented_success_tick(intensity):
    plan = translation_extras.build_expression_plan(RUMOR_SOURCE, RUMOR_TARGET, intensity=intensity)
    assert '✅' not in plan.text
    formal = expressive_engine.enhance_translation(RUMOR_SOURCE, RUMOR_TARGET,
        settings=expressive_engine.ExpressiveSettings(display_mode='emoji', intensity=intensity))
    assert formal.text == RUMOR_TARGET


def test_source_authored_emoji_is_still_preserved():
    text = '✅ 可能今天會來巡廠'
    assert '✅' in translation_extras.build_expression_plan(text, text, intensity='lively').text


def test_reported_spray_washing_instruction_keeps_both_actions_and_name(runtime):
    source = '@法比恩 Fabian 這把噴漆錯誤的記得重洗'
    runtime.provider_result = '__MENTION_0__ Bundel yang salah pengecatan semprot, ingat cuci ulang.'
    app.handle_message(event(source))
    actual = delivered_text(runtime)
    assert '@法比恩 Fabian' in actual
    assert 'Bundel yang salah dicat semprot ini' in actual and 'cuci ulang' in actual
    assert 'salah pengecatan semprot' not in actual
    assert len(runtime.generations) == 1 and queue.pending_count() == 0


@pytest.mark.parametrize('mention', ['@法比恩 Fabian', '@法比恩 FABIAN', '@山多 Budi Santoso'])
def test_mixed_script_capitalized_names_leave_the_following_sentence_translatable(mention):
    source = mention + ' Tolong cek I01'
    assert app.extract_mentions(source) == [mention]
    assert app.strip_mentions_for_detect(source).strip() == 'Tolong cek I01'


@pytest.mark.parametrize('source, good, bad', [
    ('可以移出', 'Boleh dipindahkan keluar.', 'Jangan dipindahkan keluar.'),
    ('不可以移入', 'Jangan masukkan.', 'Bisa dimasukkan.'),
    ('可以移出', 'Dapat dikeluarkan.', 'Harus dikeluarkan.'),
])
def test_general_movement_permission_is_not_inverted_or_made_mandatory(source, good, bad):
    relations = records.build_movement_permissions(source)
    assert relations and records.validate_movement_permission(relations[0], good)
    assert not records.validate_movement_permission(relations[0], bad)


@pytest.mark.parametrize('source', ['可以移出？', '這把可以移出，那把不可以移出。'])
def test_questions_and_opposite_permissions_do_not_receive_a_blanket_rule(source):
    assert records.build_movement_permissions(source) == []
