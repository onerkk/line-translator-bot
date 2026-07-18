import json
import unittest
from pathlib import Path

import factory_knowledge
import factory_terminology
import glossary_enforcement


ROOT = Path(__file__).resolve().parent


class FactoryTerminologyIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.glossary = json.loads((ROOT / "glossary_data.json").read_text(encoding="utf-8"))
        glossary_enforcement.invalidate_glossary_cache()

    def test_numbered_factory_units_are_not_romanized(self):
        pairs = factory_terminology.collect_applicable_pairs(
            "一課最近被釘很緊，樓上是一股股長。",
            self.glossary,
            "zh",
            "id",
        )
        self.assertIn(("一課", "Seksi 1"), pairs)
        self.assertIn(("一股股長", "kepala regu 1"), pairs)
        self.assertFalse(any("Yigu" in target for _, target in pairs))

    def test_alias_and_longest_match_use_one_index(self):
        pairs = factory_terminology.collect_applicable_pairs(
            "第一課的第一股股長在樓上。",
            self.glossary,
            "zh",
            "id",
        )
        self.assertIn(("第一課", "Seksi 1"), pairs)
        self.assertIn(("第一股股長", "kepala regu 1"), pairs)
        # The longer leader term must suppress a conflicting nested 一股 match.
        self.assertNotIn(("第一股", "Regu 1"), pairs)

    def test_prompt_explains_factory_unit_logic(self):
        prompt = factory_terminology.build_translation_prompt(
            "一課最近被釘很緊，樓上是一股股長。",
            self.glossary,
            "zh",
            "id",
        )
        self.assertIn("一股 is Regu 1, not Yigu", prompt)
        self.assertIn("一課 => Seksi 1", prompt)
        self.assertIn("一股股長 => kepala regu 1", prompt)
        self.assertIn("被釘很緊", prompt)

    def test_ocr_spacing_normalization_is_lossless(self):
        source = "一 課最近被盯很緊\n樓上是一 股 股 長\nID | 原因"
        normalized = factory_terminology.normalize_ocr_text(source)
        self.assertEqual(
            normalized,
            "一課最近被盯很緊\n樓上是一股股長\nID | 原因",
        )

    def test_ocr_hint_contains_source_forms_not_translations(self):
        hint = factory_terminology.build_ocr_hint(self.glossary, max_items=200)
        self.assertIn("一股股長", hint)
        self.assertIn("CYA矯直切斷機", hint)
        self.assertNotIn("kepala regu 1", hint)
        self.assertIn("不可翻譯", hint)

    def test_reverse_alias_is_only_enabled_on_explicitly_safe_rows(self):
        index = glossary_enforcement.build_safe_reverse_index(self.glossary)
        self.assertEqual(index["kepala regu satu"]["target_term"], "一股股長")
        self.assertEqual(index["seksi satu"]["target_term"], "一課")

    def test_large_same_prefix_glossary_uses_trie_index(self):
        synthetic = {
            f"工廠設備術語{i:05d}": {
                "idn": f"istilah pabrik {i:05d}",
                "translation_mode": "hard",
            }
            for i in range(5000)
        }
        engine = factory_terminology.FactoryTerminologyEngine(synthetic)
        health = engine.health()
        self.assertEqual(health["glossary_entries"], 5000)
        self.assertEqual(health["trie_roots"], 1)
        self.assertGreater(health["trie_nodes"], 5000)
        matches = engine.match_zh("請檢查工廠設備術語04999。")
        self.assertEqual(
            [(item.matched_text, item.target_term) for item in matches],
            [("工廠設備術語04999", "istilah pabrik 04999")],
        )

    def test_factory_knowledge_catches_yigu_and_overstatement(self):
        cards = factory_knowledge.retrieve(
            "一課最近被釘很緊，上週被處長抓到人在控制室休息。樓上是一股股長，他蠻公司派的。",
            "zh",
            "id",
            limit=3,
        )
        self.assertTrue(any(card.get("id") == "organization_unit_discipline_notice" for card in cards))
        bad = (
            "Akhir-akhir ini Departemen 1 diawasi sangat ketat. "
            "Di lantai atas ada Kepala Bagian Yigu. Dia sangat berpihak kepada perusahaan."
        )
        ok, issues = factory_knowledge.validate_translation(cards, "一課最近被釘很緊，樓上是一股股長，他蠻公司派的。", bad)
        self.assertFalse(ok)
        self.assertTrue(any("forbidden:Yigu" in issue or "missing_section_1_unit" in issue for issue in issues))

        good = (
            "Akhir-akhir ini Seksi 1 diawasi dengan ketat. "
            "Di lantai atas ada kepala regu 1. Dia cukup berpihak kepada perusahaan."
        )
        ok2, issues2 = factory_knowledge.validate_translation(cards, "一課最近被釘很緊，樓上是一股股長，他蠻公司派的。", good)
        self.assertTrue(ok2, issues2)


if __name__ == "__main__":
    unittest.main()
