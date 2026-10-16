"""Observed failures and counterexamples; all provider responses are fixtures.

These tests verify retrieval, source-owned decoration, request composition and
cache invalidation. They do not measure an external model's translation quality.
"""
import ast
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import app
import factory_terminology as terms
import glossary_enforcement as enforcement
import prompt_optimizer as prompts
import translation_extras as extras
import translation_casebook as casebook


@pytest.mark.parametrize("source", [
    "作業環境掛牌掉落、套環、油漬、水漬，作業基本環境要主動維持一下。",
    "地上掛牌、工單掉落要馬上處理掉",
    "材料上的掛牌不見了，請檢查",
    "工單資料跟掛牌不同，請確認",
    "掛牌掉在地上了",
    "作业环境挂牌掉落，需要清理",
    "材料識別牌上的資料有誤",
])
def test_confirmed_atomic_term_is_retrieved_without_the_old_whole_notice(source):
    matches = terms.get_engine(app.GLOSSARY_LOOKUP).match_zh(source)
    assert any(m.target_term == "TAG" for m in matches)
    hint = app.inject_glossary_hint(source, "zh", "id")
    assert "=> TAG" in hint
    assert "9/23" not in hint and "佳東" not in hint


@pytest.mark.parametrize("source", [
    "公司明天掛牌上市", "股票掛牌價格提高", "工廠的公司掛牌上市",
    "門口掛牌掉了", "安全標示的掛牌掉了", "@掛牌掉落 請回覆",
    '欄位字串是「掛牌掉落」', "https://example.com/掛牌掉落",
    "工單先放好。公司掛牌上市了。",
])
def test_other_senses_and_literal_spans_do_not_acquire_a_material_tag(source):
    assert not any(m.target_term == "TAG" for m in terms.get_engine(app.GLOSSARY_LOOKUP).match_zh(source))


def test_new_term_uses_existing_glossary_admission_not_sentence_replacement():
    source = "作業環境掛牌掉落、套環、油漬、水漬，作業基本環境要主動維持一下。"
    correct = "Di area kerja terdapat TAG yang jatuh, Cincin Pelindung, noda minyak dan noda air. Jaga kebersihan area kerja secara proaktif."
    wrong = correct.replace("TAG", "penanda area kerja")
    assert enforcement.enforce_glossary(source, correct, app.GLOSSARY_LOOKUP, "zh", "id")["compliant"]
    assert not enforcement.enforce_glossary(source, wrong, app.GLOSSARY_LOOKUP, "zh", "id")["compliant"]
    # A later approved database edit is consumed by the same index; no code rule.
    updated = dict(app.GLOSSARY_LOOKUP)
    updated["材料識別牌"] = dict(updated["材料識別牌"], aliases_zh=["已核准的識別牌簡稱"])
    assert ("已核准的識別牌簡稱", "TAG") in terms.collect_applicable_pairs(
        "請查看已核准的識別牌簡稱", updated, "zh", "id")


def test_embedded_and_editable_glossary_share_the_same_approved_concept():
    root = Path(__file__).parent
    tree = ast.parse((root / "app.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_GLOSSARY_JSON" for t in n.targets))
    embedded = json.loads(ast.literal_eval(node.value))
    editable = json.loads((root / "glossary_data.json").read_text())
    assert embedded["材料識別牌"] == editable["材料識別牌"]


def test_shared_objects_do_not_retrieve_a_different_multi_step_workflow():
    import factory_knowledge
    store = factory_knowledge.FactoryKnowledgeStore(Path(__file__).with_name("factory_knowledge.json"))
    examples = store.casebook_examples()
    query = "作業環境掛牌掉落、套環、油漬、水漬，作業基本環境要主動維持一下。"
    selected = casebook.retrieve(query, "zh", "id", examples=examples, glossary=app.GLOSSARY_LOOKUP)
    assert "work_order_tag_color_and_ring_notice_20260923" not in {x["case_id"] for x in selected}
    # Exact approved sources retain their original learning behavior.
    own = next(x for x in examples if x["case_id"] == "work_order_tag_color_and_ring_notice_20260923")
    exact = casebook.retrieve(own["zh"], "zh", "id", examples=examples, glossary=app.GLOSSARY_LOOKUP)
    assert exact[0]["case_id"] == own["case_id"]


@pytest.mark.parametrize("intensity", ["subtle", "natural", "lively"])
@pytest.mark.parametrize("source,target", [
    ("昨天我中招 新詐騙手法 請小心", "Kemarin saya menjadi korban modus penipuan baru. Harap berhati-hati."),
    ("請小心", "Mohon berhati-hati."),
    ("Harap berhati-hati.", "請小心。"),
])
def test_warning_cannot_become_a_cheerful_polite_request(source, target, intensity):
    assert extras.analyze_message_tone(source).primary == "urgent_warning"
    result = extras.build_expression_plan(source, target, intensity=intensity)
    assert not any(e in result.text for e in ("🙂", "😊", "🥰", "🎉", "😄", "✅"))
    assert "⚠" in result.text or "🚨" in result.text


@pytest.mark.parametrize("source,target", [
    ("資料放桌上", "Mohon taruh data di meja."),
    ("請檢查設備", "Bagus sekali!"),
    ("還沒處理", "Selamat, berhasil!"),
    ("今天收到資料", "Harap berhati-hati."),
])
def test_target_alone_cannot_authorize_a_social_emotion_or_warning(source, target):
    result = extras.build_expression_plan(source, target, intensity="lively")
    assert not any(e in result.text for e in ("🙂", "😊", "🥰", "🎉", "🥳", "🙏", "⚠", "🚨", "👏"))


@pytest.mark.parametrize("source", ["被騙了！", "真的嗎！", "Aduh!", "出事了!!!"])
def test_exclamation_is_not_evidence_of_happiness(source):
    tone = extras.analyze_message_tone(source)
    assert tone.primary not in {"positive", "joy", "celebration", "praise"}


@pytest.mark.parametrize("source", [
    "上層開會說現在鹽水廠5s環境越來越差遠遠輸給新莊廠。",
    "Dalam rapat manajemen disampaikan bahwa kondisi lingkungan sekarang semakin buruk.",
    "The meeting discussed the poor condition of the workplace.",
])
def test_reported_meeting_content_does_not_announce_a_schedule(source):
    assert extras.analyze_message_tone(source).primary != "meeting_schedule"
    result = extras.build_expression_plan(source, source, intensity="lively")
    assert not any(e in result.text for e in ("📅", "⏰", "📍"))


@pytest.mark.parametrize("source", ["明天下午三點開會", "會議延期", "Rapat besok pukul 14.00", "The meeting starts at 14:00."])
def test_actual_scheduled_meetings_keep_their_schedule_intent(source):
    assert extras.analyze_message_tone(source).primary == "meeting_schedule"


def test_source_emotion_and_existing_symbols_are_retained():
    plan = extras.build_expression_plan("好開心", "Saya senang sekali.", intensity="lively")
    assert plan.decorated_count == 1
    target = "Harap berhati-hati. 🙂"
    assert extras.build_expression_plan("請小心 🙂", target).text == target


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(app, "_tl", threading.local())
    monkeypatch.setattr(app, "translation_cache", {})
    monkeypatch.setattr(app, "_event_log_write", lambda *a, **k: None)
    monkeypatch.setattr(app, "_post_translation_async", lambda *a, **k: None)
    app._tl.disable_tone_emoji = True
    app._tl.group_id = "G-pragmatic-review"


@pytest.mark.parametrize("source,target", [
    ("改善好的照片再麻煩傳給我", "Mohon kirimkan foto hasil perbaikannya kepada saya."),
    ("照片修好後再傳給我", "Kirimkan fotonya kepada saya setelah selesai diedit."),
    ("照片請再傳一次", "Mohon kirimkan fotonya sekali lagi."),
    ("軌道這邊下午幫忙清掃一下", "Tolong bantu bersihkan area rel di sini setelah tengah hari nanti."),
    ("如果掛牌掉落，請撿起來", "Jika TAG jatuh, tolong ambil."),
    ("掛牌已經掉落，請撿起來", "TAG sudah jatuh. Tolong ambil."),
])
def test_contrasting_meanings_reach_one_provider_call_without_posthoc_rewriting(runtime, monkeypatch, source, target):
    calls = []
    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=target), finish_reason="stop")], usage=None)
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    assert app.translate_openai(source, "zh", "id") == target
    assert len(calls) == 1 and calls[0]["translation_max_generations"] == 1
    prompt = calls[0]["messages"][0]["content"]
    assert "polite 再麻煩/再請" in prompt
    assert "observed defects remains factual" in prompt
    assert "missing technical identity" in prompt


def test_tone_engine_updates_invalidate_old_cached_wording(runtime, monkeypatch):
    original = app._translation_cache_asset_fingerprint()
    monkeypatch.setattr(extras, "TRANSLATION_EXTRAS_VERSION", "future-approved-tone-policy")
    assert app._translation_cache_asset_fingerprint() != original


@pytest.mark.parametrize('source', [
    '安衛科技執法查核缺失：', '安衛人員今天來抽查',
    '安卫检查发现缺失', '職業安全衛生管理',
])
def test_existing_safety_concept_is_available_outside_old_attendance_scenario(source):
    hint = app.inject_glossary_hint(source, 'zh', 'id')
    assert '=> K3' in hint
    assert '取消夜間點名' not in hint
    wrong = 'Temuan pemeriksaan oleh Teknologi Anwei.'
    assert not enforcement.enforce_glossary(source, wrong, app.GLOSSARY_LOOKUP, 'zh', 'id')['compliant']


@pytest.mark.parametrize('source', ['安衛科技公司來訪', '「安衛科技」是品牌', '@安衛人員 請回覆'])
def test_safety_alias_does_not_rename_literal_company_or_mention(source):
    assert not any(m.target_term == 'K3' for m in terms.get_engine(app.GLOSSARY_LOOKUP).match_zh(source))


@pytest.mark.parametrize('source,target', [
    ('安衛科技執法查核缺失：', 'Temuan ketidaksesuaian dalam pemeriksaan kepatuhan K3 berbantuan teknologi:'),
    ('處長下班了', 'Kepala divisi sudah selesai jam kerja.'),
    ('處長工作做完了，但還沒下班', 'Kepala divisi sudah menyelesaikan pekerjaannya, tetapi belum selesai jam kerja.'),
    ('來找我一下', 'Datang temui saya sebentar.'),
    ('操作員哈新，起吊前扶吊帶動作正確，移動時站立斜後方位置正確，但未用治具手接觸棒材有夾傷風險',
     'Operator 哈新 sudah benar saat memegang sling sebelum pengangkatan dan berdiri serong di belakang saat pemindahan. Namun, ia tidak menggunakan alat bantu dan menyentuh batang material dengan tangan, sehingga berisiko terjepit.'),
])
def test_new_screenshots_use_shared_composition_with_one_generation(runtime, monkeypatch, source, target):
    calls = []
    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=target), finish_reason='stop')], usage=None)
    monkeypatch.setattr(app.ai.chat.completions, 'create', create)
    assert app.translate_openai(source, 'zh', 'id') == target
    assert len(calls) == 1
    prompt = calls[0]['messages'][0]['content']
    assert 'domain/method/action' in prompt
    assert 'end-of-shift' in prompt
