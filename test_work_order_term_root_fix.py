import ast
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = (
    '@All 公司宣導過的規定不要自己省略，現在很多噴漆也不噴，工單也不寫，這些狀況越來越多。\n'
    '麻煩一切按照工單資訊執行，各站該做的基本工作：來料尺寸、表面品質、短尺維護、重量確認，'
    '這些都是作業流程的一部分。'
)
BAD_ID = (
    '@All Regulasi yang sudah disosialisasikan perusahaan jangan dihilangkan sendiri, '
    'sekarang banyak yang tidak spray paint, tidak menulis Tempat Buku bahan, situasi seperti ini semakin banyak. '
    'Mohon semuanya mengikuti informasi pesanan, setiap stasiun harus melakukan pekerjaan dasar: ukuran bahan masuk, '
    'kualitas permukaan, perawatan ukuran pendek, konfirmasi bobot, semua ini adalah bagian dari alur kerja.'
)


class WorkOrderTermRootFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        wanted_assigns = {
            '_SEMANTIC_CONTRACT_VERSION',
            '_FACTORY_WORK_ORDER_BAD_ID_PATTERNS',
            '_FACTORY_WORK_ORDER_CONTEXT_ZH',
            '_FACTORY_WORK_ORDER_REQUIRED_ID',
            '_FACTORY_WORK_ORDER_TERM_NOTES',
        }
        wanted_defs = {
            '_compact_zh_for_work_order', '_classify_factory_work_order_zh_id',
            '_translation_has_any', '_repair_factory_work_order_terms_zh_id',
            'build_translation_semantic_contract', 'build_translation_semantic_contract_prompt',
            'translation_satisfies_semantic_contract', 'enforce_translation_semantic_contract',
        }
        nodes = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = {t.id for t in node.targets if isinstance(t, ast.Name)}
                if names & wanted_assigns:
                    nodes.append(node)
            elif isinstance(node, ast.FunctionDef) and node.name in wanted_defs:
                nodes.append(node)
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        ns = {
            're': re,
            'logger': type('L', (), {'warning': staticmethod(lambda *a, **k: None)})(),
            '_classify_qing_sense_zh_id': lambda text: None,
            '_classify_factory_station_alias_zh_id': lambda text: None,
            '_build_factory_reason_contract_lines': lambda risk: [],
            '_FACTORY_REASON_WRONG_ID_PATTERNS': (),
            '_factory_reason_action_map': lambda: {},
            '_factory_reason_translation_contains': lambda entry, low: True,
            '_event_log_write': lambda *a, **k: None,
            '_fix_zh_temporal_direction_boundary': lambda src, trans: trans,
            'factory_semantic_translate_pre_operation_issue_id_zh': lambda src: None,
            '_repair_factory_station_canonical_name': lambda trans, required_id: trans,
            '_factory_reason_semantic_translate_zh_id': lambda src: None,
        }
        exec(compile(module, str(ROOT / 'app.py'), 'exec'), ns)
        cls.ns = ns

    def test_glossary_root_term_is_work_order_not_material_book_place(self):
        glossary = json.loads((ROOT / 'glossary_data.json').read_text(encoding='utf-8'))
        self.assertEqual(glossary['工單']['idn'], 'work order')
        self.assertEqual(glossary['工單資訊']['idn'], 'informasi pada work order')
        self.assertEqual(glossary['短尺維護']['idn'], 'penanganan material pendek')
        self.assertNotIn('Tempat Buku bahan', json.dumps(glossary['工單'], ensure_ascii=False))

    def test_contract_detects_work_order_announcement_and_blocks_bad_terms(self):
        build = self.ns['build_translation_semantic_contract']
        validate = self.ns['translation_satisfies_semantic_contract']
        prompt = self.ns['build_translation_semantic_contract_prompt'](build(SOURCE, 'zh', 'id'))
        contract = build(SOURCE, 'zh', 'id')
        self.assertTrue(contract['has_risk'])
        self.assertFalse(contract['tm_bypass_allowed'])
        self.assertFalse(contract['nmt_allowed'])
        self.assertIn("Translate 工單 exactly as 'work order'", prompt)
        ok, reason = validate(contract, BAD_ID)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith('factory_work_order_'))

    def test_deterministic_repair_removes_old_wrong_glossary_terms(self):
        repair = self.ns['_repair_factory_work_order_terms_zh_id']
        fixed = repair(SOURCE, BAD_ID)
        self.assertIn('work order', fixed)
        self.assertIn('informasi pada work order', fixed)
        self.assertIn('ukuran material masuk', fixed)
        self.assertIn('penanganan material pendek', fixed)
        self.assertIn('konfirmasi berat', fixed)
        self.assertNotIn('Tempat Buku bahan', fixed)
        self.assertNotIn('perawatan ukuran pendek', fixed)

    def test_enforcement_accepts_repaired_translation(self):
        build = self.ns['build_translation_semantic_contract']
        enforce = self.ns['enforce_translation_semantic_contract']
        validate = self.ns['translation_satisfies_semantic_contract']
        contract = build(SOURCE, 'zh', 'id')
        fixed = enforce(contract, SOURCE, BAD_ID)
        ok, reason = validate(contract, fixed)
        self.assertTrue(ok, reason)

    def test_ui_label_context_does_not_force_work_order_in_every_field_label(self):
        build = self.ns['build_translation_semantic_contract']
        contract = build('工單訂單資訊「訂單編號」', 'zh', 'id')
        self.assertFalse(contract['has_risk'])


if __name__ == '__main__':
    unittest.main()
