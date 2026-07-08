import ast
import re
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class IndonesianTemporalDirectionRootFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        wanted_assigns = {
            'FACTORY_ID_ZH_OBJECTS', 'FACTORY_ID_ZH_DEFECTS',
            'FACTORY_ID_ZH_EQUIPMENT_OBJECTS', 'FACTORY_ID_ZH_EQUIPMENT_STATES',
            'FACTORY_ID_ZH_POSITIONS', 'FACTORY_ID_ZH_PRE_OPERATION_PATTERNS',
            'FACTORY_ID_ZH_ISSUE_PHRASES', 'FACTORY_DOMAIN_KEYWORDS_ID',
            'FACTORY_ZH_LITERAL_RISK', 'FACTORY_BAD_ZH_PATTERNS',
            'ANNOUNCEMENT_SIGNALS', 'SHORT_INCIDENT_MAX_LEN',
            '_SEMANTIC_CONTRACT_VERSION',
        }
        wanted_defs = {
            '_clean_factory_id', '_find_longest_phrase', '_factory_find_phrase',
            '_factory_material_subject_zh_id_to_zh', '_has_factory_pre_operation_marker',
            '_fix_zh_temporal_direction_boundary',
            'factory_semantic_translate_pre_operation_issue_id_zh',
            'detect_factory_domain', 'classify_factory_message',
            'factory_semantic_translate_id_zh', 'is_equipment_rusak_context',
            'post_fix_factory_id_to_zh', 'detect_factory_semantic_error',
            'validate_factory_translation', 'build_translation_semantic_contract',
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
            'logger': types.SimpleNamespace(warning=lambda *a, **k: None),
            '_event_log_write': lambda *a, **k: None,
            '_classify_qing_sense_zh_id': lambda text: None,
            '_classify_factory_station_alias_zh_id': lambda text: None,
            '_repair_factory_station_canonical_name': lambda translation, required_id: translation,
            '_semantic_rebuild_qing_treat_translation': lambda src_text, contract, current_translation='': current_translation,
        }
        exec(compile(module, str(ROOT / 'app.py'), 'exec'), ns)
        cls.ns = ns

    def test_pre_operation_back_end_issue_gets_complete_slot_translation(self):
        src = 'Barang id ini sebelum di jalankan juga ada sedikit masalah dari belakang'
        self.assertEqual(
            self.ns['factory_semantic_translate_id_zh'](src),
            '這個料件 ID 在加工前，後端就已經有一點問題了',
        )

    def test_pre_operation_variants_keep_time_and_direction_separate(self):
        cases = {
            'Barang ini sebelum dijalankan ada masalah dari depan': '這個料件在加工前，前端就已經有問題了',
            'Batang ini sebelum diproses ada sedikit masalah bagian belakang': '這支棒材在加工前，後端就已經有一點問題了',
        }
        for src, expected in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.ns['factory_semantic_translate_id_zh'](src), expected)

    def test_post_fix_repairs_stale_or_model_output_without_overwriting_sentence(self):
        src = 'Barang id ini sebelum di jalankan juga ada sedikit masalah dari belakang'
        bad = '這個料件 ID 在加工前後端就有一點問題了'
        self.assertEqual(
            self.ns['post_fix_factory_id_to_zh'](src, bad),
            '這個料件 ID 在加工前，後端就有一點問題了',
        )

    def test_semantic_contract_blocks_merged_temporal_direction(self):
        src = 'Barang id ini sebelum di jalankan juga ada sedikit masalah dari belakang'
        contract = self.ns['build_translation_semantic_contract'](src, 'id', 'zh')
        self.assertTrue(contract['has_risk'])
        self.assertFalse(contract['tm_bypass_allowed'])
        self.assertFalse(contract['vector_bypass_allowed'])
        self.assertFalse(contract['nmt_allowed'])
        ok, reason = self.ns['translation_satisfies_semantic_contract'](
            contract,
            '這個料件 ID 在加工前後端就有一點問題了',
        )
        self.assertFalse(ok)
        self.assertEqual(reason, 'id_zh_temporal_direction_boundary_missing')
        fixed = self.ns['enforce_translation_semantic_contract'](
            contract,
            src,
            '這個料件 ID 在加工前後端就有一點問題了',
        )
        self.assertEqual(fixed, '這個料件 ID 在加工前，後端就有一點問題了')

    def test_unrelated_before_sentence_is_not_forced_into_factory_template(self):
        self.assertIsNone(
            self.ns['factory_semantic_translate_id_zh']('Sebelum makan ada sedikit masalah')
        )


if __name__ == '__main__':
    unittest.main()
