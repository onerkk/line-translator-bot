import ast
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class StationAliasRootFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        wanted_assigns = {
            'STATION_DEPARTMENTS', 'STATION_NAMES', 'STATION_CODES',
            'FACTORY_STATION_ALIAS_RULES',
        }
        wanted_defs = {
            'resolve_factory_station_aliases', 'detect_station_context',
            '_classify_factory_station_alias_zh_id',
            'build_translation_semantic_contract', 'semantic_contract_requires_llm',
            'build_translation_semantic_contract_prompt',
            'translation_satisfies_semantic_contract',
            '_repair_factory_station_canonical_name',
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
            '_SEMANTIC_CONTRACT_VERSION': 'test',
            '_classify_qing_sense_zh_id': lambda text: None,
        }
        exec(compile(module, str(ROOT / 'app.py'), 'exec'), ns)
        cls.ns = ns

    def test_shorthand_is_normalized_to_official_station(self):
        fn = self.ns['resolve_factory_station_aliases']
        text, matches = fn('異型那站可以支援裝箱')
        self.assertEqual(text, '異型包裝站可以支援裝箱')
        self.assertEqual(matches[0]['canonical_id'], 'Stasiun packing barang bentuk khusus')

    def test_product_and_equipment_terms_are_not_misclassified(self):
        fn = self.ns['resolve_factory_station_aliases']
        for source in ('異型棒不擋', '異型矯直機異常', '異型拋光機停機'):
            with self.subTest(source=source):
                text, matches = fn(source)
                self.assertEqual(text, source)
                self.assertEqual(matches, [])

    def test_station_context_injects_official_name_without_fake_station_number(self):
        hint = self.ns['detect_station_context']('異型那站可以支援裝箱')
        self.assertIn('異型包裝站', hint)
        self.assertIn('Stasiun packing barang bentuk khusus', hint)
        self.assertNotIn('站號 None', hint)

    def test_semantic_contract_blocks_stale_or_generic_station_translation(self):
        build = self.ns['build_translation_semantic_contract']
        validate = self.ns['translation_satisfies_semantic_contract']
        contract = build('異型包裝站可以支援裝箱', 'zh', 'id')
        self.assertTrue(contract['has_risk'])
        self.assertTrue(contract['requires_llm'])
        self.assertFalse(contract['tm_bypass_allowed'])
        self.assertFalse(contract['nmt_allowed'])
        ok, _ = validate(contract, 'Stasiun barang khusus bisa bantu packing.')
        self.assertFalse(ok)
        ok, _ = validate(contract, 'Stasiun packing barang bentuk khusus bisa bantu packing.')
        self.assertTrue(ok)

    def test_prompt_contains_exact_official_station_name(self):
        build = self.ns['build_translation_semantic_contract']
        prompt = self.ns['build_translation_semantic_contract_prompt'](
            build('異型那站可以支援裝箱', 'zh', 'id')
        )
        self.assertIn('Stasiun packing barang bentuk khusus', prompt)
        self.assertIn('異型棒 means batang bentuk khusus', prompt)

    def test_last_safety_net_only_replaces_station_name_fragment(self):
        repair = self.ns['_repair_factory_station_canonical_name']
        bad = 'Stasiun barang khusus bisa bantu kasih barang untuk dikemas.'
        fixed = repair(bad, 'stasiun packing barang bentuk khusus')
        self.assertEqual(
            fixed,
            'stasiun packing barang bentuk khusus bisa bantu kasih barang untuk dikemas.'
        )

    def test_external_glossary_contains_persistent_factory_terms(self):
        glossary = json.loads((ROOT / 'glossary_data.json').read_text(encoding='utf-8'))
        expected = {
            '異型包裝站': 'Stasiun packing barang bentuk khusus',
            '前站': 'stasiun sebelumnya',
            '料源不足': 'pasokan material tidak cukup',
            '木箱': 'peti kayu',
            '裝箱': 'memasukkan material ke dalam peti kayu',
            '支援裝箱': 'membantu proses pengemasan ke dalam peti kayu',
            '木箱包': 'pengemasan dengan peti kayu',
        }
        for source, target in expected.items():
            with self.subTest(source=source):
                self.assertEqual(glossary[source]['idn'], target)


if __name__ == '__main__':
    unittest.main()
