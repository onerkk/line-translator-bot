"""User-confirmed knowledge is retrieved by context, never sentence-replaced."""
from pathlib import Path
from types import SimpleNamespace
import threading
import pytest
import app
import factory_knowledge as knowledge

BOSA = 'plant_bosa_equipment_sense'
STAGE = 'person_stage_figurative_attention'

@pytest.fixture
def store():
    return knowledge.FactoryKnowledgeStore(Path(__file__).with_name('factory_knowledge.json'))

@pytest.mark.parametrize('text', ['Bosa i5 rusak', 'Bosa I9 belum diperbaiki', 'Tolong periksa bosa ini', 'Bosa itu sudah hidup'])
def test_bosa_generalizes_across_equipment_codes_and_statuses(store, text):
    cards = store.retrieve(text, 'id', 'zh')
    assert BOSA in {c['id'] for c in cards}
    prompt = store.build_prompt(cards)
    assert '設備' in prompt and '尚未修理' in prompt

@pytest.mark.parametrize('text', ['Pak Bosa datang', 'Bosa adalah nama orang', 'Bosa bilang mesin i5 rusak', 'Bosan i5 rusak'])
def test_equipment_sense_does_not_claim_person_or_different_word(store, text):
    assert BOSA not in {c['id'] for c in store.retrieve(text, 'id', 'zh')}

@pytest.mark.parametrize('text', ['尤豐雄又有舞臺了', '他又有舞台了', '這個人再次獲得舞臺', '不小心他又有舞臺了'])
def test_confirmed_metaphor_generalizes_without_binding_a_person(store, text):
    cards = store.retrieve(text, 'zh', 'id')
    assert STAGE in {c['id'] for c in cards}
    assert 'kembali menjadi sorotan' in store.build_prompt(cards)

@pytest.mark.parametrize('text', ['演唱會又有舞臺了', '搭建完又有舞台了', '他又紅了', '他有舞臺了'])
def test_unrelated_and_literal_sources_do_not_force_confirmed_phrase(store, text):
    assert STAGE not in {c['id'] for c in store.retrieve(text, 'zh', 'id')}

@pytest.mark.parametrize('source,target,src,tgt,needle', [
    ('Bosa i5 rusak','i5 設備故障。','id','zh','本廠設備訊息中的 Bosa'),
    ('Bosa I9 belum diperbaiki','I9 設備尚未修理。','id','zh','本廠設備訊息中的 Bosa'),
    ('尤豐雄又有舞臺了','尤豐雄 kembali menjadi sorotan.','zh','id','這人又紅了'),
])
def test_runtime_receives_confirmed_context_without_an_extra_generation(monkeypatch, source,target,src,tgt,needle):
    monkeypatch.setattr(app, '_tl', threading.local())
    app._tl.disable_tone_emoji=True
    calls=[]
    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=target),finish_reason='stop')],usage=None)
    monkeypatch.setattr(app.ai.chat.completions,'create',create)
    app._tl.semantic_contract=app.build_translation_semantic_contract(source,src,tgt)
    assert app.translate_openai(source,src,tgt).rstrip('。')==target.rstrip('。')
    assert len(calls)==1
    assert needle in calls[0]['messages'][0]['content']


def test_knowledge_edit_changes_cache_fingerprint(monkeypatch, tmp_path, store):
    import json
    path=tmp_path/'knowledge.json'
    document=store.document()
    path.write_text(json.dumps(document,ensure_ascii=False))
    local=knowledge.FactoryKnowledgeStore(path)
    monkeypatch.setattr(app,'_FACTORY_KNOWLEDGE_STORE',local)
    first=app._translation_cache_asset_fingerprint()
    # Same version label, changed approved content, no process restart.
    document['entries'][-1]['context'].append('經確認的新說明')
    local.replace_document(document)
    assert app._translation_cache_asset_fingerprint()!=first
    assert app._translation_cache_asset_fingerprint()==app._translation_cache_asset_fingerprint()
