"""Meaning-preservation audit through the real pipeline; all transports offline.

The provider fixture supplies an independently written valid translation. These
tests verify that local shortcuts/rewriters preserve it, not model accuracy.
"""
import time

import pytest

import app
import factory_message_semantics as relations
import factory_source_understanding as source_understanding
import glossary_policy
import translation_quality_gate as quality
from test_translation_notice_availability import runtime  # noqa: F401


PIPELINE_CASES = [('id', 'zh', 'barang tidak rusak', '料件沒有損傷。'),
 ('id', 'zh', 'barang rusak kemarin', '料件昨天損壞了。'),
 ('id', 'zh', 'barang rusak 2 batang', '有 2 支料件損傷。'),
 ('id', 'zh', 'barang rusak dan kotor', '料件損傷而且髒污。'),
 ('id', 'zh', 'i15 rusak?', 'I15 機台故障了嗎？'),
 ('id', 'zh', 'Barang ini sebelum diproses bagian belakang tidak ada masalah', '這個料件在加工前，後端沒有問題。'),
 ('id',
  'zh',
  'Barang ini sebelum diproses ada masalah dari depan, tolong periksa',
  '這個料件在加工前，前端就有問題，請檢查。'),
 ('zh', 'id', '品保還沒吊去', 'QC belum memindahkan material itu.'),
 ('zh', 'id', '不要把料吊去品保', 'Jangan bawa material ke QC.'),
 ('zh',
  'id',
  '入儲後EH33峰作金屬不要集中放這格',
  'Setelah masuk ke penyimpanan, jangan tempatkan material EH33 milik 峰作金屬 bersama di slot ini.'),
 ('zh',
  'id',
  '週末大成儲格放不下，照片的位置不能放',
  'Pada akhir pekan, slot penyimpanan 大成 tidak muat. Material tidak boleh diletakkan di lokasi '
  'pada foto.'),
 ('zh', 'id', '夜點費與夜班加班費分開計算', 'Uang shift malam dan uang lembur malam dihitung terpisah.'),
 ('zh',
  'id',
  '有好處理的料先做，已完成的料先入儲',
  'Proses dulu material yang mudah diproses. Material yang sudah selesai masuk ke area penyimpanan '
  'terlebih dahulu.'),
 ('zh',
  'id',
  '明天去台中出差，後天回鹽水',
  'Besok pergi ke Taichung untuk perjalanan dinas, lusa kembali ke Yanshui.'),
 ('zh', 'id', '機台零件被偷走，請回報班長', 'Komponen mesin dicuri, harap lapor kepada kepala regu.'),
 ('zh', 'id', '操作時化學品會反應', 'Bahan kimia akan bereaksi saat dioperasikan.'),
 ('zh', 'id', '清洗時不要吞入清洗液', 'Jangan sampai cairan pembersih tertelan saat membersihkan.'),
 ('zh',
  'id',
  '職安人員確認已被警方逮捕',
  'Petugas K3 memastikan bahwa orang itu sudah tertangkap oleh polisi.'),
 ('zh', 'id', '生產日報工作表已更新', 'Lembar kerja laporan produksi harian sudah diperbarui.'),
 ('zh', 'id', '短尺規不要拿來量棒材', 'Jangan gunakan penggaris pendek untuk mengukur batang baja.')]


@pytest.fixture(autouse=True)
def clean_context():
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    app._tl.group_id = "scope-audit"
    app._tl.user_id = "offline-user"
    app._tl.disable_tone_emoji = True
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(previous)


@pytest.mark.parametrize("src,tgt,source,expected", PIPELINE_CASES)
def test_actual_pipeline_preserves_source_meaning(runtime, src, tgt, source, expected):
    runtime.provider_result = expected
    result = app.translate(source, src, tgt)
    # ZH presentation drops the final full stop; question marks remain semantic.
    assert result.rstrip("。.") == expected.rstrip("。.")
    assert len(runtime.generations) == 1


@pytest.mark.parametrize("source,expected", [
    ("barang rusak", "料件損傷"),
    ("batang rusak dari belakang", "棒材後端損傷"),
    ("Mesin ya kebakar,", "機台著火了"),
    ("Mesin I15 rusak.", "I15 機台故障"),
    ("Mesin I9 belum selesai diperbaiki", "I9 機台尚未維修完成"),
    ("Barang ini sebelum dijalankan ada masalah dari depan", "這個料件在加工前，前端就已經有問題了"),
])
def test_complete_short_statements_keep_zero_provider_cost(runtime, source, expected):
    runtime.provider_result = expected
    assert app.translate(source, "id", "zh") == expected
    assert runtime.generations == []


@pytest.mark.parametrize("subject", ["barang", "material", "batang", "mesin"])
@pytest.mark.parametrize("negative", ["tidak", "belum", "tidak pernah"])
def test_negation_never_disappears_in_legacy_slots(subject, negative):
    assert app.factory_semantic_translate_id_zh(f"{subject} {negative} rusak") is None


@pytest.mark.parametrize("source", [
    "barang rusak?", "barang rusak kemarin", "barang rusak 2 batang",
    "barang rusak dan kotor", "barang rusak, tolong periksa", "barang rusak lagi",
    "I15 rusak atau I9 rusak", "barang rusak karena jatuh",
    "Barang ini sebelum diproses bagian belakang tidak ada masalah",
    "Barang ini sebelum diproses ada masalah dari depan, tolong periksa",
    "Barang ini sebelum diproses ada masalah dari depan kemarin",
    "Barang ini sebelum diproses ada masalah dari depan?",
])
def test_unconsumed_time_quantity_question_and_actions_use_normal_translation(source):
    assert app.factory_semantic_translate_id_zh(source) is None


@pytest.mark.parametrize("source", [
    "品保還沒吊去", "不要把料吊去品保", "品保沒有偷跑",
    "入儲後EH33峰作金屬不要集中放這格",
    "入儲時EH33峰作金屬集中放這格，再確認標籤",
    "入儲時EH33峰作金屬集中放這格？",
    "週末大成儲格放不下，照片的位置不能放",
    "週末大成儲格能放就放，放不下再放照片裡這些位置，完成後回報。",
])
def test_chinese_keyword_presence_cannot_replace_an_instruction(source):
    assert app.factory_semantic_translate_zh_id(source) is None


@pytest.mark.parametrize("source,expected", [
    ("I15 rusak?", "I15 機台故障了嗎？"),
    ("I9 oli bocor?", "I9 機台漏油了嗎？"),
    ("Orang malam belum buang sampah?", "晚班人員還沒倒垃圾嗎？"),
])
def test_relation_renderers_do_not_turn_questions_into_assertions(runtime, source, expected):
    assert relations.translate_source_directly(source, "id", "zh") == ""
    runtime.provider_result = expected
    assert app.translate(source, "id", "zh") == expected
    assert len(runtime.generations) == 1


@pytest.mark.parametrize("source,before,after", [
    ("夜點費增加", "Uang lembur malam meningkat.", "Uang shift malam meningkat."),
    ("日點費增加", "Uang lembur siang meningkat.", "Uang shift siang meningkat."),
    ("中班津貼增加", "Uang lembur sore meningkat.", "Uang shift sore meningkat."),
    ("有好處理的料先做", "Proses bahan yang sudah selesai.", "Proses bahan yang mudah diproses."),
    ("鹽水廠今天停工", "Yanshui berhenti bekerja hari ini.", "Pabrik Yanshui (鹽水廠) berhenti bekerja hari ini."),
    ("工單要填寫", "Isi lembar kerja.", "Isi work order."),
    ("品保偷跑，馬上反應", "Material dicuri oleh QC, harus bereaksi.", "Material dibawa oleh QC duluan, harus segera lapor."),
    ("清洗後刮傷會被吃掉", "Goresan akan tertelan setelah dibersihkan.", "Goresan bisa hilang atau tertutup setelah dibersihkan."),
])
def test_source_supported_term_repairs_remain_available(source, before, after):
    assert app.post_fix_factory_zh_to_id(source, before).casefold() == after.casefold()


@pytest.mark.parametrize("source,target", [
    ("請確認資料", "Periksa data."),
    ("台車改為手動操作", "Troli diubah menjadi pengoperasian manual."),
    ("工作表已更新", "Lembar kerja sudah diperbarui."),
    ("我要一個木製盒子", "Saya ingin sebuah kotak kayu."),
])
def test_deprecated_glossary_phrases_are_not_a_language_wide_ban(source, target):
    issues = quality._indonesian_readability_issues(source, target, "zh")
    assert not any(issue.startswith("deprecated_glossary_phrase:") for issue in issues)


@pytest.mark.parametrize("source,target", [("工單", "Lembar kerja"), ("抓帳", "Periksa data")])
def test_deprecated_translation_of_the_actual_source_term_is_still_detected(source, target):
    assert any(issue.startswith("deprecated_glossary_phrase:")
               for issue in quality._indonesian_readability_issues(source, target, "zh"))
    assert glossary_policy.deprecated_indonesian_phrases(source)


def test_old_semantic_cache_is_invalidated_without_deleting_approved_sources(runtime, monkeypatch):
    source = "barang tidak rusak"
    key = (source, "id", "zh", app._translation_cache_scope())
    with monkeypatch.context() as old:
        old.setattr(app, "_FACTORY_SEMANTIC_SCOPE_BUILD_ID", "", raising=False)
        old_fingerprint = app._translation_cache_asset_fingerprint()
        old_persistent = app._translation_cache_persistent_fingerprint()
    app.translation_cache[key] = ("料件損傷", time.time(), old_fingerprint)
    assert app._translation_cache_persistent_fingerprint() != old_persistent
    assert app.cache_get(source, "id", "zh") is None


def test_current_safe_cache_remains_reusable(runtime):
    source, expected = "barang tidak rusak", "料件沒有損傷"
    runtime.provider_result = expected
    assert app.translate(source, "id", "zh") == expected
    assert app.translate(source, "id", "zh") == expected
    assert len(runtime.generations) == 1


@pytest.mark.parametrize("source,good,bad,src,tgt", [
    ("Mesin I9 belum selesai diperbaiki", "I9 機台尚未維修完成", "I9 機台已維修完成", "id", "zh"),
    ("Mesin I9 belum juga selesai diperbaiki", "I9 機台尚未維修完成", "I9 機台已維修完成", "id", "zh"),
    ("I9 尚未完成維修", "Perbaikan I9 belum selesai.", "Perbaikan I9 sudah selesai.", "zh", "id"),
    ("I9 維修沒有完成", "Perbaikan I9 belum selesai.", "Perbaikan I9 sudah selesai.", "zh", "id"),
    ("Mesin I9 sudah selesai diperbaiki", "I9 已維修完成", "I9 尚未維修完成", "id", "zh"),
])
def test_negation_governs_completion_in_both_languages(source, good, bad, src, tgt):
    analysis = source_understanding.analyze(source, src)
    assert source_understanding.validate_operational_states(analysis, good, src, tgt)[0]
    assert not source_understanding.validate_operational_states(analysis, bad, src, tgt)[0]
