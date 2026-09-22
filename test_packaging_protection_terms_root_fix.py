"""September 22 screenshots: packaging sense, labels and one-call delivery.

External transports are replaced locally; these test pipeline behavior, not
live model accuracy. Rings and sleeves are different objects.
"""
import time

import pytest

import active_learning as learning
import ai_provider
import app
import factory_terminology as terminology
import factory_translation_guard as guard
import translation_quality_gate as quality
from test_translation_instruction_cost_quality import offline_transport, response
from test_translation_learned_policy import database, public_pipeline


SOURCE = ('Untuk konsumen lainnya, meskipun pada lembar kerja terdapat '
          'tanda “N”, tetap wajib menggunakan kondom.')
BAD = '其他客戶即使在工作單上有「N」標記，仍必須使用保險套。'
GOOD = '其他客戶即使在工單上有「N」標記，仍必須使用保護套。'


def test_screenshot_sexual_sense_cannot_enter_quality_memory_or_learning():
    assert not quality.validate_translation(SOURCE, BAD, 'id', 'zh').ok
    assert not guard.validate_translation(SOURCE, BAD, 'id', 'zh').ok
    assert not learning.validate_correction(SOURCE, BAD, 'id', 'zh')['ok']
    assert not app._tm_bypass_integrity_ok(SOURCE, BAD, 'id', 'zh')[0]


def test_screenshot_repair_preserves_rule_and_does_not_turn_sleeve_into_ring():
    fixed = quality.canonicalize_source_terms(SOURCE, BAD, 'id', 'zh')
    assert fixed == GOOD
    assert quality.canonicalize_source_terms(SOURCE, fixed, 'id', 'zh') == fixed
    assert quality.validate_translation(SOURCE, fixed, 'id', 'zh').ok
    assert guard.validate_translation(SOURCE, fixed, 'id', 'zh').ok


def test_protective_ring_alias_reaches_shared_glossary():
    pairs = terminology.collect_applicable_pairs('保護環必須裝上。', app.GLOSSARY_LOOKUP, 'zh', 'id')
    assert ('保護環', 'Cincin Pelindung') in pairs


def test_public_translation_repairs_with_one_generation(public_pipeline, monkeypatch):
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(kwargs)
        return response(BAD)
    monkeypatch.setattr(ai_provider, '_dispatch_provider', dispatch)
    assert app.translate(SOURCE, 'id', 'zh').rstrip('。') == GOOD.rstrip('。')
    assert len(calls) == 1
    assert 'factory_packaging_terms' in str(calls[0]['messages'])


def test_current_fingerprint_does_not_allow_stale_bad_cache(public_pipeline, monkeypatch):
    key = (SOURCE, 'id', 'zh', app._translation_cache_scope())
    rows = {key: (BAD, time.time(), app._translation_cache_asset_fingerprint())}
    monkeypatch.setattr(app, 'translation_cache', rows)
    assert app.cache_get(SOURCE, 'id', 'zh') is None
    assert key not in rows


@pytest.mark.parametrize('source,bad,good,src,tgt', [
    ('Jika work order bertanda N, kondom tetap wajib dipasang.',
     '若工單標示 N，仍必須裝保險套。', '若工單標示 N，仍必須裝保護套。', 'id', 'zh'),
    ('Untuk packing batang ini jangan menggunakan kondom.',
     '包裝這些棒材時不得使用保險套。', '包裝這些棒材時不得使用保護套。', 'id', 'zh'),
    ('Pada work order ada "NO Kondom", tetapi kondom wajib dipasang jika bertanda Y.',
     '工單有「NO Kondom」，但標示 Y 時必須装保險套。',
     '工單有「NO Kondom」，但標示 Y 時必須装保護套。', 'id', 'zh'),
    ('Work order meminta selubung pelindung untuk batang ini.',
     '工單要求這些棒材使用保護環。', '工單要求這些棒材使用保護套。', 'id', 'zh'),
    ('Cincin Pelindung tidak perlu digunakan jika work order menyatakan demikian.',
     '若工單如此註明，就不必使用保護套。', '若工單如此註明，就不必使用保護環。', 'id', 'zh'),
    ('保護環不需要裝。', 'Kondom tidak perlu dipasang.',
     'Cincin Pelindung tidak perlu dipasang.', 'zh', 'id'),
    ('工單註明要裝保護套。', 'Work order meminta cincin pelindung dipasang.',
     'Work order meminta selubung pelindung dipasang.', 'zh', 'id'),
    ('套環要補上。', 'Kondom pelindung perlu dipasang.',
     'Cincin Pelindung perlu dipasang.', 'zh', 'id'),
])
def test_unseen_variants_fix_only_accessory_not_conditions(source, bad, good, src, tgt):
    assert terminology.packaging_translation_issues(source, bad, src, tgt)
    assert quality.canonicalize_source_terms(source, bad, src, tgt) == good
    assert not terminology.packaging_translation_issues(source, good, src, tgt)


@pytest.mark.parametrize('source,target,src,tgt', [
    ('Saya ingin membeli kondom.', '我想買保險套。', 'id', 'zh'),
    ('Gunakan kondom saat hubungan seksual.', '性行為時使用保險套。', 'id', 'zh'),
    ('Lembar kerja kesehatan ini menjelaskan penggunaan kondom untuk kontrasepsi.',
     '這份健康學習單說明如何使用保險套避孕。', 'id', 'zh'),
    ('Work order pabrik kondom sudah selesai.', '保險套工廠的工單已完成。', 'id', 'zh'),
    ('Beli sarung pelindung untuk handphone.', '購買手機保護套。', 'id', 'zh'),
    ('手機保護套要更換。', 'Ganti sarung pelindung handphone.', 'zh', 'id'),
    ('保險套工廠的包裝作業。', 'Pengemasan di pabrik kondom.', 'zh', 'id'),
    ('@kondom Tolong baca work order.', '@kondom 請閱讀工單。', 'id', 'zh'),
    ('Periksa https://example.invalid/kondom dan work order.',
     '檢查 https://example.invalid/kondom 與工單。', 'id', 'zh'),
    ('Pada work order tertulis "NO Kondom".', '工單上寫著「NO Kondom」。', 'id', 'zh'),
])
def test_personal_health_identities_and_literal_only_labels_are_untouched(source, target, src, tgt):
    assert not terminology.packaging_term_senses(source, src)
    assert terminology.canonicalize_packaging_translation(source, target, src, tgt) == target
    assert not terminology.packaging_translation_issues(source, target, src, tgt)
    assert not terminology.build_packaging_prompt(source, src, tgt)


def test_two_accessories_are_not_merged_by_global_replacement():
    source = '工單要求保護環與保護套都裝上。'
    good = 'Work order mewajibkan Cincin Pelindung dan selubung pelindung dipasang.'
    bad = 'Work order mewajibkan kondom dan selubung pelindung dipasang.'
    assert terminology.packaging_term_senses(source, 'zh') == {'ring', 'sleeve'}
    assert terminology.canonicalize_packaging_translation(source, bad, 'zh', 'id') == bad
    assert terminology.packaging_translation_issues(source, bad, 'zh', 'id')
    assert not terminology.packaging_translation_issues(source, good, 'zh', 'id')


def test_names_urls_labels_and_unrelated_clauses_survive_repair():
    source = ('__MENTION_0__ Work order "NO Kondom" untuk ID7 bertanda Y: '
              'kondom tetap wajib. https://example.invalid/kondom')
    bad = ('__MENTION_0__ ID7 的工單標示 Y，並寫「NO Kondom」：仍必須使用保險套。'
           ' https://example.invalid/kondom')
    good = bad.replace('保險套', '保護套')
    assert quality.canonicalize_source_terms(source, bad, 'id', 'zh') == good
    assert quality.validate_translation(source, good, 'id', 'zh').ok


@pytest.mark.parametrize('source,bad,src,tgt,required', [
    (SOURCE, BAD, 'id', 'zh', '保護套'),
    ('保護環要裝上。', 'Kondom harus dipasang.', 'zh', 'id', 'Cincin Pelindung'),
])
def test_ocr_text_uses_same_final_terms(source, bad, src, tgt, required, public_pipeline, monkeypatch):
    # public_pipeline restores the whole thread-local dictionary at teardown.
    app._tl.from_image_ocr = True
    result = app._final_delivery_guard(source, bad, src, tgt)
    assert required in result
    assert not terminology.packaging_translation_issues(source, result, src, tgt)


SCREENSHOTS = [
    ('zh', 'id', '@All 本週有稽核，包裝三站的磅秤記錄表要填寫一下，應該已經補到近期了。\n\n生產報表麻煩確實填寫，避免稽核缺失',
     '@All Minggu ini ada audit. Mohon isi formulir catatan timbangan di tiga stasiun packing. Seharusnya catatannya sudah dilengkapi sampai data terbaru.\n\nMohon isi laporan produksi dengan benar dan lengkap agar tidak ada temuan saat audit.'),
    ('zh', 'id', '目前工單資訊異常 有跟相關單位及主管討論，大家先按照以上執行。',
     'Saat ini informasi pada work order bermasalah. Sudah dibahas dengan bagian terkait dan supervisor, sementara semua jalankan sesuai ketentuan di atas.'),
    ('zh', 'id', '@All 印尼同仁如果對於噴漆部分有疑慮，為了避免噴錯造成重工，可以拿工單去問一下台灣同仁。',
     '@All Rekan kerja Indonesia, jika ada keraguan mengenai bagian pengecatan semprot, untuk menghindari kesalahan pengecatan yang menyebabkan pengerjaan ulang, bawalah work order untuk menanyakannya kepada rekan kerja Taiwan.'),
    ('zh', 'id', '工單上噴漆位置欄位顯示不噴，即便有註明噴什麼顏色，一律不噴。',
     'Jika pada work order kolom posisi pengecatan semprot menunjukkan “tidak dicat”, jangan lakukan pengecatan semprot, meskipun tercantum warna yang harus disemprot.'),
    ('id', 'zh', 'Jika pada work order ada keterangan khusus bahwa tidak perlu menggunakan Cincin Pelindung, maka boleh tidak digunakan. Untuk yang lainnya, lakukan produksi sesuai ukuran yang sudah ditetapkan sebelumnya!',
     '如果工單上有特別註明不需要使用保護環，則可以不使用。其他情況請依照先前設定的尺寸進行生產！'),
]


@pytest.mark.parametrize('src,tgt,source,target', SCREENSHOTS)
def test_other_screenshots_meaning_is_not_rewritten(src, tgt, source, target):
    assert quality.validate_translation(source, target, src, tgt).ok
    assert guard.validate_translation(source, target, src, tgt).ok
    assert quality.canonicalize_source_terms(source, target, src, tgt) == target


def test_three_packing_stations_are_not_changed_to_station_number_three():
    source, target = SCREENSHOTS[0][2:]
    assert quality.canonicalize_source_terms(source, target, 'zh', 'id') == target
    assert 'tiga stasiun packing' in target


@pytest.mark.parametrize('source,flag', [
    ('Work order bertanda N, kondom tetap wajib.', 'N'),
    ('Kolom tertulis Y, pasang Cincin Pelindung.', 'Y'),
    ('工單標示 N，仍要装保護套。', 'N'),
])
def test_unquoted_flags_are_protected_by_field_context(source, flag):
    envelope = quality.inspect_immutable_spans(source)
    assert flag in envelope.mapping.values()
    bad = '工單要求使用保護套。' if source.isascii() else 'Work order meminta selubung pelindung.'
    src, tgt = ('id', 'zh') if source.isascii() else ('zh', 'id')
    assert 'missing_literal:' + flag in quality.validate_translation(source, bad, src, tgt).hard_issues


def test_ordinary_uppercase_words_remain_translatable():
    source = 'TANDA BAHAYA INI TIDAK BOLEH DIABAIKAN.'
    assert 'TANDA' not in quality.inspect_immutable_spans(source).mapping.values()
    assert not quality.validate_translation(source, '這個 TANDA 不可忽視。', 'id', 'zh').ok


@pytest.mark.parametrize('label', ["'NO Kondom'", '‘NO Kondom’', '＂NO Kondom＂', '`NO Kondom`'])
def test_label_quote_style_never_creates_an_extra_accessory_instruction(label):
    source = f'Pada work order tertulis {label}.'
    target = '工單上寫著「NO Kondom」。'
    assert not terminology.packaging_term_senses(source, 'id')
    assert not terminology.packaging_translation_issues(source, target, 'id', 'zh')
    assert quality.validate_translation(source, target, 'id', 'zh').ok
