"""Missing title concepts use the existing glossary in both directions.

Provider and OCR outputs are fixtures, not live model quality measurements.
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import pytest
import app
import factory_terminology as terms
import glossary_enforcement as enforcement

SOURCE='明天董事長可能會來，上班要帶長袖制服，有通知確定會到現場再穿'
TARGET=('Besok Ketua Dewan Direksi mungkin akan datang. Bawa seragam lengan panjang saat berangkat kerja. '
        'Kenakan setelah ada pemberitahuan yang memastikan beliau akan datang ke lokasi.')

@pytest.mark.parametrize('source,want',[
 ('董事長可能會來',('董事長','Ketua Dewan Direksi')),
 ('董事长今天不會來',('董事长','Ketua Dewan Direksi')),
 ('副董事長還沒到',('副董事長','Wakil Ketua Dewan Direksi')),
 ('副董事长明天到',('副董事长','Wakil Ketua Dewan Direksi')),
])
def test_title_and_deputy_are_distinct_atomic_concepts(source,want):
    pairs=terms.collect_applicable_pairs(source,app.GLOSSARY_LOOKUP,'zh','id')
    assert want in pairs
    if source.startswith(('副董事長','副董事长')):
        assert not any(x[1]=='Ketua Dewan Direksi' for x in pairs)

@pytest.mark.parametrize('source', ['Ketua kelas akan datang','Ketua rapat belum hadir','Ketua datang'])
def test_generic_chair_does_not_reverse_to_corporate_chairman(source):
    pairs=terms.collect_applicable_pairs(source,app.GLOSSARY_LOOKUP,'id','zh',safe_reverse_index=enforcement.build_safe_reverse_index(app.GLOSSARY_LOOKUP))
    assert not any(x[1] in {'董事長','副董事長'} for x in pairs)

@pytest.mark.parametrize('source,want',[
 ('Ketua Dewan Direksi mungkin datang','董事長'),
 ('Wakil Ketua Dewan Direksi belum datang','副董事長'),
])
def test_full_title_reverses_with_hierarchy_preserved(source,want):
    pairs=terms.collect_applicable_pairs(source,app.GLOSSARY_LOOKUP,'id','zh',safe_reverse_index=enforcement.build_safe_reverse_index(app.GLOSSARY_LOOKUP))
    assert {x[1] for x in pairs} == {want}


def test_screenshot_generic_ketua_is_detected_by_existing_term_validator():
    assert enforcement.enforce_glossary(SOURCE,TARGET,app.GLOSSARY_LOOKUP,'zh','id')['compliant']
    assert not enforcement.enforce_glossary(SOURCE,TARGET.replace('Ketua Dewan Direksi','Ketua'),app.GLOSSARY_LOOKUP,'zh','id')['compliant']


def test_editable_fallback_and_runtime_agree():
    root=Path(__file__).parent
    tree=ast.parse((root/'app.py').read_text())
    n=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_GLOSSARY_JSON' for t in n.targets))
    fallback=json.loads(ast.literal_eval(n.value));editable=json.loads((root/'glossary_data.json').read_text())
    for term in ['董事長','副董事長']:
        assert fallback[term]==editable[term]
        assert app.GLOSSARY_LOOKUP[term]['canonical_idn']==editable[term]['canonical_idn']

@pytest.mark.parametrize('ocr',[False,True])
def test_text_and_ocr_receive_title_and_complete_condition_in_one_call(monkeypatch,ocr):
    monkeypatch.setattr(app,'_tl',threading.local())
    monkeypatch.setattr(app,'translation_cache',{})
    monkeypatch.setattr(app,'_event_log_write',lambda *a,**k:None)
    monkeypatch.setattr(app,'_post_translation_async',lambda *a,**k:None)
    app._tl.disable_tone_emoji=True
    app._tl.group_id='G-board-title'
    calls=[]
    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(model='offline-fixture',choices=[SimpleNamespace(message=SimpleNamespace(content=TARGET),finish_reason='stop')],usage=None)
    monkeypatch.setattr(app.ai.chat.completions,'create',create)
    if ocr:
        monkeypatch.setattr(app,'ocr_image_openai',lambda *a,**k:SOURCE)
        result=app.ocr_and_translate_image('offline-image','id')
        assert TARGET in str(result)
    else:
        assert app.translate(SOURCE,'zh','id')==TARGET
    assert len(calls)==1 and calls[0]['translation_max_generations']==1
    assert 'Ketua Dewan Direksi' in calls[0]['messages'][0]['content']
    assert SOURCE in str(calls[0]['messages'])


def test_compound_shadowing_is_per_occurrence_not_per_message():
    source='Wakil Ketua Dewan Direksi dan Ketua Dewan Direksi akan datang.'
    pairs=terms.collect_applicable_pairs(source,app.GLOSSARY_LOOKUP,'id','zh',safe_reverse_index=enforcement.build_safe_reverse_index(app.GLOSSARY_LOOKUP))
    assert {x[1] for x in pairs}=={'董事長','副董事長'}


def test_same_span_rule_handles_new_glossary_compounds_without_code_changes():
    glossary={'測試長詞':{'idn':'alat baru panjang','translation_mode':'hard','reverse_safe':True},'測試短詞':{'idn':'alat baru','translation_mode':'hard','reverse_safe':True}}
    index=enforcement.build_safe_reverse_index(glossary)
    pairs=terms.collect_applicable_pairs('alat baru panjang rusak',glossary,'id','zh',safe_reverse_index=index)
    assert [x[1] for x in pairs]==['測試長詞']
