import ast
import json
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

import glossary_enforcement as ge
import translation_memory as tm


ROOT = Path(__file__).resolve().parent
BAD_LABEL = '工單製程紀錄「機台」'


class ReverseGlossaryRootFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.glossary = json.loads((ROOT / 'glossary_data.json').read_text(encoding='utf-8'))

    def test_common_mesin_is_not_reverse_enforced(self):
        safe = ge.build_safe_reverse_index(self.glossary)
        self.assertNotIn('mesin', safe)
        self.assertIn(BAD_LABEL, ge.build_unsafe_reverse_ui_targets(self.glossary))

    def test_forward_only_ui_label_leak_is_detected(self):
        leaked = ge.find_reverse_glossary_ui_leak(
            'Mesin ya kebakar,', BAD_LABEL, self.glossary, 'id', 'zh'
        )
        self.assertEqual(leaked, BAD_LABEL)
        self.assertIsNone(ge.find_reverse_glossary_ui_leak(
            'Mesin ya kebakar,', '機台著火了', self.glossary, 'id', 'zh'
        ))

    def test_factory_semantic_engine_translates_machine_fire(self):
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        wanted_assigns = {
            'FACTORY_ID_ZH_OBJECTS', 'FACTORY_ID_ZH_DEFECTS',
            'FACTORY_ID_ZH_EQUIPMENT_OBJECTS', 'FACTORY_ID_ZH_EQUIPMENT_STATES',
            'FACTORY_ID_ZH_POSITIONS', 'ANNOUNCEMENT_SIGNALS',
            'SHORT_INCIDENT_MAX_LEN',
        }
        wanted_defs = {
            '_clean_factory_id', '_find_longest_phrase',
            'classify_factory_message', 'factory_semantic_translate_id_zh',
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
        ns = {'re': re}
        exec(compile(module, str(ROOT / 'app.py'), 'exec'), ns)
        self.assertEqual(ns['factory_semantic_translate_id_zh']('Mesin ya kebakar,'), '機台著火了')
        self.assertEqual(ns['factory_semantic_translate_id_zh']('Mesinnya terbakar'), '機台著火了')

    def test_derived_tm_rows_can_be_synchronized_and_purged(self):
        old_env = os.environ.get('TM_DB_PATH')
        old_path, old_init = tm.TM_DB_PATH, tm._init_done
        try:
            with tempfile.TemporaryDirectory() as td:
                db = str(Path(td) / 'tm.db')
                os.environ['TM_DB_PATH'] = db
                tm.TM_DB_PATH = None
                tm._init_done = False
                tm.init()
                self.assertTrue(tm.tm_store('Mesin', BAD_LABEL, 'id', 'zh', None, 'glossary_seed', 100))
                self.assertTrue(tm.tm_store('Mesin ya kebakar,', BAD_LABEL, 'id', 'zh', None, 'old-model', 10))
                self.assertTrue(tm.tm_store('Siap', '準備好了', 'id', 'zh', None, 'old-model', 90))

                self.assertEqual(tm.tm_delete_by_model('glossary_seed'), 1)
                self.assertEqual(
                    tm.tm_delete_target_texts([BAD_LABEL], src_lang='id', tgt_lang='zh'),
                    1,
                )
                with sqlite3.connect(db) as conn:
                    rows = conn.execute('SELECT src_text, tgt_text FROM tm_entries').fetchall()
                self.assertEqual(rows, [('Siap', '準備好了')])
        finally:
            if old_env is None:
                os.environ.pop('TM_DB_PATH', None)
            else:
                os.environ['TM_DB_PATH'] = old_env
            tm.TM_DB_PATH = old_path
            tm._init_done = old_init


if __name__ == '__main__':
    unittest.main()
