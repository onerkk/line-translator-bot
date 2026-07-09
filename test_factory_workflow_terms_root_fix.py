import ast
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class _Logger:
    def warning(self, *args, **kwargs):
        pass


class FactoryWorkflowTermsRootFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        wanted_assigns = {
            '_SEMANTIC_CONTRACT_VERSION',
            '_FACTORY_DOMAIN_TERM_RULES_ZH_ID', '_FACTORY_DOMAIN_TERM_MAP_ZH_ID',
            '_FACTORY_DOMAIN_CONTEXT_ZH', 'ZH_TO_ID_HARD', 'FACTORY_ZH_ID_BAD_PATTERNS',
        }
        wanted_defs = {
            '_semantic_compact_zh', '_factory_domain_terms_in_text_zh_id',
            '_classify_factory_domain_terms_zh_id', '_factory_domain_translation_contains',
            '_factory_domain_forbidden_hit', '_build_factory_domain_term_contract_lines',
            '_repair_factory_domain_term_translation', 'build_translation_semantic_contract',
            'semantic_contract_requires_llm', 'build_translation_semantic_contract_prompt',
            'translation_satisfies_semantic_contract', 'enforce_translation_semantic_contract',
            'pre_replace_zh', 'detect_factory_semantic_error_zh_id',
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
            'logger': _Logger(),
            '_event_log_write': lambda *a, **k: None,
            '_classify_qing_sense_zh_id': lambda text: None,
            '_classify_factory_station_alias_zh_id': lambda text: None,
            '_factory_reason_contract_risk': lambda text: None,
            'CUSTOMER_NAMES': [],
        }
        exec(compile(module, str(ROOT / 'app.py'), 'exec'), ns)
        cls.ns = ns

    def test_contract_activates_for_work_order_announcement_terms(self):
        src = '@All 公司宣導過的規定不要自己省略，現在很多噴漆也不噴，工單也不寫。麻煩一切按照工單資訊執行，各站該做的基本工作：來料尺寸、表面品質、短尺維護、重量確認。'
        contract = self.ns['build_translation_semantic_contract'](src, 'zh', 'id')
        self.assertTrue(contract['has_risk'])
        self.assertTrue(contract['requires_llm'])
        self.assertFalse(contract['tm_bypass_allowed'])
        self.assertFalse(contract['nmt_allowed'])
        risks = [r for r in contract['risks'] if r.get('sense') == 'factory_domain_term_semantics']
        self.assertEqual(len(risks), 1)
        self.assertIn('work_order', risks[0]['entries'])
        self.assertIn('short_material_handling', risks[0]['entries'])
        self.assertIn('incoming_material_size', risks[0]['entries'])
        self.assertIn('surface_quality', risks[0]['entries'])
        self.assertIn('weight_confirmation', risks[0]['entries'])

    def test_contract_blocks_generic_dictionary_drift(self):
        src = '請按照工單資訊執行：來料尺寸、表面品質、短尺維護、重量確認。'
        contract = self.ns['build_translation_semantic_contract'](src, 'zh', 'id')
        bad = 'Silakan ikuti informasi perintah kerja: ukuran material yang masuk, kualitas permukaan, pemeliharaan penggaris pendek, dan konfirmasi berat.'
        ok, reason = self.ns['translation_satisfies_semantic_contract'](contract, bad)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith('factory_domain_'))

    def test_contract_repair_uses_source_side_term_registry(self):
        src = '請按照工單資訊執行：來料尺寸、表面品質、短尺維護、重量確認。'
        contract = self.ns['build_translation_semantic_contract'](src, 'zh', 'id')
        bad = 'Silakan ikuti informasi perintah kerja: ukuran material yang masuk, kualitas permukaan, pemeliharaan penggaris pendek, dan konfirmasi berat.'
        fixed = self.ns['enforce_translation_semantic_contract'](contract, src, bad)
        self.assertIn('work order', fixed)
        self.assertIn('ukuran material masuk', fixed)
        self.assertIn('penanganan material pendek', fixed)
        self.assertIn('konfirmasi berat', fixed)
        self.assertNotIn('perintah kerja', fixed.lower())
        self.assertNotIn('penggaris', fixed.lower())

    def test_pre_replace_preserves_compound_term_by_longest_match(self):
        replaced, _ = self.ns['pre_replace_zh']('短尺維護和工單資訊都要填')
        self.assertIn('[penanganan material pendek]', replaced)
        self.assertIn('[informasi pada work order]', replaced)
        self.assertNotIn('[ukuran pendek]維護', replaced)

    def test_semantic_detector_flags_bad_short_material_and_work_order_terms(self):
        detect = self.ns['detect_factory_semantic_error_zh_id']
        bad, reason, _ = detect('短尺維護要確認', 'pemeliharaan penggaris pendek harus dikonfirmasi')
        self.assertTrue(bad)
        self.assertIn('factory_short_material_literal', reason)
        bad, reason, _ = detect('工單也不寫', 'perintah kerja juga tidak ditulis')
        self.assertTrue(bad)
        self.assertIn('factory_work_order_literal', reason)

    def test_external_glossary_contains_correct_authoritative_terms(self):
        glossary = json.loads((ROOT / 'glossary_data.json').read_text(encoding='utf-8'))
        self.assertEqual(glossary['工單']['idn'], 'work order')
        self.assertNotIn('Tempat Buku bahan', json.dumps(glossary, ensure_ascii=False))
        self.assertEqual(glossary['工單資訊']['idn'], 'informasi pada work order')
        self.assertEqual(glossary['短尺維護']['idn'], 'penanganan material pendek')
        self.assertEqual(glossary['來料尺寸']['idn'], 'ukuran material masuk')
        self.assertEqual(glossary['表面品質']['idn'], 'kualitas permukaan')
        self.assertEqual(glossary['重量確認']['idn'], 'konfirmasi berat')


if __name__ == '__main__':
    unittest.main()
