"""Reported evidence plus unseen composition/contradiction tests; no live APIs."""
import pytest

import factory_input_semantics as inputs
import factory_quantity_semantics as quantities
import factory_record_contract as records
import factory_translation_guard as guard
import translation_quality_gate as quality

SOURCE = ('最新新系統問題很多沒錯，磅秤收集後，近期多互相提醒存檔前要檢視一下重量 ID 支數異常。'
          '目前支數換算異常是另一個問題，但像這樣0支明顯的標籤異常你們一定要有辦法主動發現。'
          '貼TAG本來就要核對資訊，這樣很危險。')
GOOD = ('Memang benar, sistem baru ini memiliki banyak masalah. Setelah data diperoleh dari timbangan, '
        'belakangan ini kalian perlu saling mengingatkan untuk memeriksa apakah berat, ID, dan jumlah batang '
        'tidak normal sebelum menyimpan data. Masalah konversi jumlah batang adalah masalah lain. '
        'Namun, kalian harus dapat secara aktif menemukan label yang jelas tidak normal, seperti jumlah '
        '0 batang ini. Informasi pada TAG memang harus dicocokkan saat memasangnya. Kondisi seperti ini sangat berbahaya.')
BAD = GOOD.replace('sebelum menyimpan data', 'sebelum menyimpan 1 buah data')
SOURCE_ENTRY = ('@All 包裝的重量嚴禁手打，雖然欄位目前沒鎖欄位，但是一律要磅秤收集，'
                '手打系統會有異常重量，目前已經提報七筆')
GOOD_ENTRY = ('@All Data berat hasil pengemasan dilarang keras diinput secara manual. '
              'Meskipun kolom saat ini belum dikunci, data berat wajib diperoleh langsung dari timbangan. '
              'Jika diinput secara manual, sistem akan mencatat berat yang tidak normal. '
              'Saat ini sudah dilaporkan tujuh kasus.')


def test_reported_false_acceptance_and_false_rejection_are_reversed_at_both_gates():
    for check in (quality.validate_translation, guard.validate_translation):
        result = check(SOURCE, GOOD, 'zh', 'id')
        assert result.ok, result.issues
        for candidate in (BAD, BAD.replace('1 buah data', 'sebuah data')):
            result = check(SOURCE, candidate, 'zh', 'id')
            assert not result.ok
            assert any('unsupported_referent_count' in str(i) for i in result.hard_issues)


@pytest.mark.parametrize('source,good,bad', [
    ('這是另一個問題，標籤是0支', 'Ini masalah lain, label menunjukkan 0 batang.',
     'Ini masalah, label menunjukkan 0 batang.'),
    ('這是另外一個原因，少了2支', 'Ini penyebab yang berbeda, kurang 2 batang.',
     'Ini penyebab yang sama, kurang 2 batang.'),
    ('每個問題都要處理', 'Setiap masalah harus ditangani.', 'Satu masalah harus ditangani.'),
    ('有兩個嚴重問題', 'Ada dua masalah serius.', 'Ada tiga masalah serius.'),
    ('這是同一個問題', 'Ini masalah yang sama.', 'Ini masalah lain.'),
    ('只有一個問題', 'Hanya ada satu masalah.', 'Hanya ada dua masalah.'),
    ('有一個新的問題', 'Ada masalah baru.', 'Ada dua masalah baru.'),
    ('第二個問題要處理', 'Masalah kedua perlu ditangani.', 'Dua masalah perlu ditangani.'),
    ('目前已提報十二筆', 'Saat ini sudah dilaporkan dua belas kasus.', 'Sudah dilaporkan dua kasus.'),
    ('資料有七筆，另一個問題是0支', 'Ada tujuh data, masalah lain adalah 0 batang.',
     'Ada delapan data, masalah lain adalah 0 batang.'),
    ('兩個問題，新增一筆資料', 'Dua masalah, tambahkan satu data.', 'Satu masalah, tambahkan dua data.'),
])
def test_quantities_remain_bound_to_referents_across_unseen_wording(source, good, bad):
    frame = quantities.build_frame(source)
    assert frame['active']
    assert quantities.validate_translation(frame, good) == (True, [])
    assert not quantities.validate_translation(frame, bad)[0]


@pytest.mark.parametrize('source,target', [
    ('另有兩把，包3支', 'Ada dua bundel lain, packing 3 batang.'),
    ('另一個箱子裝5支', 'Kotak lain berisi 5 batang.'),
    ('確認一下，標籤是0支', 'Periksa, label menunjukkan 0 batang.'),
    ('一笔一画写清楚', 'Tulis dengan jelas setiap goresan.'),
])
def test_concrete_units_and_non_count_softeners_are_not_abstract_counts(source, target):
    # Concrete 個 retains its original classifier policy; an actual physical
    # object must not borrow a required count from an unrelated data noun.
    if '箱子' in source:
        target = 'Sebuah kotak lain berisi 5 batang.'
    assert quantities.validate_translation(quantities.build_frame(source), target)[0]


def test_entry_notice_keeps_weight_method_and_seven_reports():
    assert quality.validate_translation(SOURCE_ENTRY, GOOD_ENTRY, 'zh', 'id').ok
    for bad in (GOOD_ENTRY.replace('tujuh kasus', 'enam kasus'),
                GOOD_ENTRY.replace('dilarang keras', 'boleh'),
                GOOD_ENTRY.replace('wajib diperoleh', 'boleh diperoleh'),
                GOOD_ENTRY.replace('belum dikunci', 'sudah dikunci'),
                GOOD_ENTRY.replace('sistem akan mencatat berat yang tidak normal', 'sistem akan mencatat berat normal'),
                GOOD_ENTRY.replace('sistem akan mencatat', 'sistem tidak akan mencatat'),
                GOOD_ENTRY.replace('dari timbangan', 'dari komputer, bukan timbangan'),
                GOOD_ENTRY.replace('langsung dari timbangan', 'bukan dari timbangan'),
                GOOD_ENTRY.replace('data berat wajib diperoleh langsung dari timbangan',
                                   'kemasan wajib dikumpulkan melalui timbangan')):
        assert not quality.validate_translation(SOURCE_ENTRY, bad, 'zh', 'id').ok, bad


@pytest.mark.parametrize('source,good,bad', [
    ('重量禁止手動輸入，必須由電子秤讀取',
     'Berat tidak boleh diketik manual, berat harus dibaca dari timbangan.',
     'Berat boleh diketik manual, berat harus dibaca dari timbangan.'),
    ('系統測試可以手動輸入資料', 'Data boleh diinput manual untuk pengujian sistem.',
     'Data dilarang diinput manual untuk pengujian sistem.'),
    ('重量必須手動輸入', 'Berat harus diinput secara manual.', 'Berat tidak boleh diinput manual.'),
    ('Data berat tidak boleh diinput manual.', '重量資料禁止手動輸入。', '重量資料可以手動輸入。'),
])
def test_entry_permission_is_bound_to_its_own_action_in_both_directions(source, good, bad):
    src, tgt = ('id', 'zh') if source.isascii() else ('zh', 'id')
    frame = inputs.build_frame(source, src, tgt)
    assert frame['active']
    assert inputs.validate_translation(frame, good) == (True, [])
    assert not inputs.validate_translation(frame, bad)[0]


def test_unrelated_prohibition_or_an_added_permission_cannot_satisfy_manual_ban():
    frame = inputs.build_frame('重量嚴禁手打', 'zh', 'id')
    for bad in ('Dilarang mencetak label, berat boleh diinput manual.',
                'Berat dilarang diinput manual. Berat boleh diinput manual.'):
        assert not inputs.validate_translation(frame, bad)[0]
    assert not inputs.build_frame('手打麵條比較好吃', 'zh', 'id')['active']
    assert not inputs.build_frame('測試資料不是磅秤收集的', 'zh', 'id')['scale_data']
    assert not inputs.build_frame('重量不要透過磅秤收集', 'zh', 'id')['scale_data']


def test_same_frames_feed_generation_without_a_partial_local_translation():
    frame = records.build_frame(SOURCE_ENTRY, 'zh', 'id')
    assert 'Manual DATA ENTRY' in records.build_prompt(frame)
    assert not records.render_complete(frame)
    prompt = quantities.build_prompt(quantities.build_frame(SOURCE))
    assert 'referent=problem' in prompt and 'quantifier=other' in prompt
    assert '=> Indonesian classifier buah' not in prompt


def test_final_guard_and_cache_do_not_deliver_or_reuse_corrupted_facts(monkeypatch):
    import app
    for source, good, bad in ((SOURCE, GOOD, BAD),
                              (SOURCE_ENTRY, GOOD_ENTRY, GOOD_ENTRY.replace('dilarang keras', 'boleh'))):
        app._tl.__dict__.clear()
        assert app._final_delivery_guard(source, bad, 'zh', 'id') is None
        assert app._final_delivery_guard(source, good, 'zh', 'id') == good
        app.cache_set(source, 'zh', 'id', bad, force=True)
        assert app.cache_get(source, 'zh', 'id') is None
    original = app._translation_cache_asset_fingerprint()
    monkeypatch.setattr(quantities, 'FACTORY_QUANTITY_SEMANTICS_BUILD_ID', 'previous-policy')
    assert original != app._translation_cache_asset_fingerprint()


def test_clean_candidate_needs_one_provider_generation_and_bad_candidate_is_repaired(monkeypatch, offline_transport):
    import app
    import ai_provider
    from test_translation_instruction_cost_quality import response
    for candidates, expected_calls in (([GOOD], 1), ([BAD, GOOD], 2)):
        app._tl.__dict__.clear()
        calls = []
        def dispatch(provider, **kwargs):
            calls.append(provider)
            return response(candidates[min(len(calls)-1, len(candidates)-1)])
        monkeypatch.setattr(ai_provider, '_dispatch_provider', dispatch)
        @ai_provider.translation_request_budget
        def run():
            app._tl.semantic_contract = app.build_translation_semantic_contract(SOURCE, 'zh', 'id')
            reply = ai_provider.chat_complete(model=ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL,
                messages=[{'role': 'user', 'content': SOURCE}], translation_max_generations=2,
                response_validator=app._build_translation_response_validator(SOURCE, 'zh', 'id'))
            assert reply.choices[0].message.content == GOOD
            assert app._final_delivery_guard(SOURCE, GOOD, 'zh', 'id') == GOOD
        run()
        assert len(calls) == expected_calls


from test_translation_instruction_cost_quality import offline_transport
