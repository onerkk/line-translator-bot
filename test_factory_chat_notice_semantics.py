"""Regressions from the three September 23 shop-floor screenshots."""

import factory_chat_notice_semantics as notice
import factory_translation_policy as policy
import translation_quality_gate as gate


AUDIT = ('@All 等等外稽人員會在現場巡察，麻煩報表補到今天，不要寫未來日期。'
         '各站自主檢查表注意一下')
AUDIT_BAD = ('@All dan auditor eksternal lainnya akan melakukan pemeriksaan di area kerja. '
             'Mohon lengkapi laporan sampai hari ini, jangan menulis tanggal di masa depan. '
             'Mohon perhatikan formulir pemeriksaan mandiri di setiap stasiun.')
AUDIT_GOOD = ('@All auditor eksternal akan melakukan pemeriksaan di area kerja. '
              'Mohon lengkapi laporan sampai hari ini, jangan menulis tanggal di masa depan. '
              'Mohon perhatikan formulir pemeriksaan mandiri di setiap stasiun.')
ORDER_CHAT = ('@蘇比 sobirin 這工單你看的懂嗎？特殊備註標註9/30急單 '
              '為什麼你會看成不需套環？')
ORDER_TARGET = ('@蘇比 sobirin Kamu mengerti work order ini? Catatan khususnya ditandai '
                'sebagai pesanan urgent tanggal 9/30. Mengapa kamu membacanya sebagai '
                'tidak perlu Cincin Pelindung?')


def test_broadcast_is_an_addressee_and_extra_auditors_are_removed():
    prompt = policy.build_prompt(AUDIT, 'zh', 'id')
    assert '@All is a LINE broadcast addressee' in prompt
    assert '報表補到今天' in prompt and '不要寫未來日期' in prompt
    issues = notice.translation_issues(AUDIT, AUDIT_BAD, 'zh', 'id')
    assert 'factory_chat_notice:broadcast_treated_as_actor' in issues
    assert 'factory_chat_notice:auditors_unsupported_others' in issues
    assert gate.canonicalize_source_terms(AUDIT, AUDIT_BAD, 'zh', 'id') == AUDIT_GOOD
    assert notice.canonicalize(AUDIT, '🇮🇩 ' + AUDIT_BAD, 'zh', 'id') == '🇮🇩 ' + AUDIT_GOOD
    assert not gate.validate_translation(AUDIT, AUDIT_BAD, 'zh', 'id').ok
    assert gate.validate_translation(AUDIT, AUDIT_GOOD, 'zh', 'id').ok
    assert not notice.translation_issues(AUDIT, AUDIT_GOOD, 'zh', 'id')


def test_final_line_delivery_uses_same_source_grounded_correction():
    import app
    assert app._final_delivery_guard(AUDIT, AUDIT_BAD, 'zh', 'id') == AUDIT_GOOD


def test_no_correction_of_explicit_other_auditors_or_valid_conjunction():
    source = '@All 等等外稽人員與其他稽核人員會到現場巡察。'
    candidate = '@All auditor eksternal dan auditor lainnya akan melakukan pemeriksaan di area kerja.'
    assert notice.canonicalize(source, candidate, 'zh', 'id') == candidate
    source = '@All 外稽人員會巡察，並請注意自主檢查表。'
    candidate = '@All auditor eksternal akan berkeliling, dan mohon perhatikan formulir pemeriksaan.'
    assert notice.canonicalize(source, candidate, 'zh', 'id') == candidate
    assert notice.canonicalize('@All 明天會檢查。', '@All dan auditor eksternal lainnya ...', 'zh', 'id') == '@All dan auditor eksternal lainnya ...'
    assert notice.canonicalize(AUDIT, AUDIT_BAD, 'id', 'zh') == AUDIT_BAD


def test_report_date_boundaries_do_not_promote_future_date():
    wrong = AUDIT_GOOD.replace('sampai hari ini', 'sampai besok').replace(
        'jangan menulis tanggal di masa depan', 'tuliskan tanggal di masa depan')
    issues = notice.translation_issues(AUDIT, wrong, 'zh', 'id')
    assert 'factory_chat_notice:report_today_cutoff_missing' in issues
    assert 'factory_chat_notice:future_date_prohibition_missing' in issues


def test_short_report_peeling_is_organizational_role_not_a_peeling_process():
    source = '已反應削皮主管'
    good = 'Sudah disampaikan kepada supervisor bagian peeling.'
    assert '削皮 names the factory unit' in policy.build_prompt(source, 'zh', 'id')
    assert not notice.translation_issues(source, good, 'zh', 'id')
    assert 'factory_chat_notice:peeling_supervisor_missing' in notice.translation_issues(
        source, 'Sudah dikupas oleh mesin peeling.', 'zh', 'id')


def test_chat_about_work_order_is_preserved_as_speaker_claim_and_question():
    assert not notice.translation_issues(ORDER_CHAT, ORDER_TARGET, 'zh', 'id')
    prompt = policy.build_prompt(ORDER_CHAT, 'zh', 'id')
    assert 'must not silently change this chat speaker' in prompt
    assert 'never assert it as the actual instruction' in prompt
    bad = ('@蘇比 sobirin Catatan khususnya adalah perintah tidak perlu memasang Cincin Pelindung 9/30.')
    issues = notice.translation_issues(ORDER_CHAT, bad, 'zh', 'id')
    assert 'factory_chat_notice:urgent_order_missing' in issues
    assert 'factory_chat_notice:question_modality_missing' in issues
    assert 'factory_chat_notice:ring_statement_attribution_missing' in issues
