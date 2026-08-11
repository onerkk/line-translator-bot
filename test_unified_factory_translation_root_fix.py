import ast
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import factory_knowledge
import factory_translation_policy as policy
import factory_translation_guard as guard
import glossary_policy
import translation_casebook

ROOT = Path(__file__).resolve().parent


class UnifiedFactoryTranslationRootFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.glossary = json.loads((ROOT / "glossary_data.json").read_text(encoding="utf-8"))
        cls.knowledge_doc = json.loads((ROOT / "factory_knowledge.json").read_text(encoding="utf-8"))
        cls.regression = json.loads((ROOT / "factory_translation_regression.json").read_text(encoding="utf-8"))
        cls.store = factory_knowledge.FactoryKnowledgeStore(ROOT / "factory_knowledge.json")

    def test_policy_defaults_to_factory_route_for_both_directions(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FACTORY_TRANSLATION_MODE", None)
            self.assertTrue(policy.should_force_factory_pipeline("你好", "zh", "id"))
            self.assertTrue(policy.should_force_factory_pipeline("selamat pagi", "id", "zh"))
            self.assertFalse(policy.should_force_factory_pipeline("hello", "en", "zh"))
            self.assertFalse(policy.allow_generic_nmt_fallback("zh", "id"))

    def test_policy_can_be_operationally_overridden_without_code_change(self):
        with patch.dict(os.environ, {"FACTORY_TRANSLATION_MODE": "auto"}):
            self.assertFalse(policy.should_force_factory_pipeline("一般訊息", "zh", "id", heuristic_match=False))
            self.assertTrue(policy.should_force_factory_pipeline("上料", "zh", "id", heuristic_match=True))
        with patch.dict(os.environ, {"FACTORY_TRANSLATION_MODE": "off"}):
            self.assertFalse(policy.should_force_factory_pipeline("上料", "zh", "id", heuristic_match=True))
        with patch.dict(os.environ, {"FACTORY_ALLOW_GENERIC_NMT_FALLBACK": "1"}):
            self.assertTrue(policy.allow_generic_nmt_fallback("zh", "id"))

    def test_policy_prompt_declares_non_invention_and_customer_name_rules(self):
        prompt = policy.build_prompt("大成週一抓帳", "zh", "id")
        self.assertIn("accounting action", prompt)
        self.assertIn("manual operation", prompt)
        self.assertIn("Preserve customer names", prompt)
        self.assertIn("ordinary Indonesian adjective", prompt)

    def test_external_glossary_has_corrected_factory_canonicals(self):
        expected = {
            "木箱": ("peti kayu", "hard"),
            "裝箱": ("memasukkan material ke dalam peti kayu", "soft"),
            "支援裝箱": ("membantu proses pengemasan ke dalam peti kayu", "soft"),
            "木箱包": ("pengemasan dengan peti kayu", "soft"),
            "抓帳": ("tutup buku", "hard"),
            "會計結帳": ("tutup buku", "hard"),
            "陸續到料": ("material akan tiba secara bertahap", "soft"),
            "電子系統": ("sistem elektronik", "hard"),
            "自然拉動": ("tarikan alami/pasif", "soft"),
        }
        for source, (target, mode) in expected.items():
            with self.subTest(source=source):
                row = glossary_policy.normalize_entry(source, self.glossary[source])
                self.assertEqual(row["canonical_idn"], target)
                self.assertEqual(row["translation_mode"], mode)

    def test_embedded_glossary_is_byte_semantically_equal_to_external_file(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        embedded = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_GLOSSARY_JSON" for t in node.targets):
                embedded = ast.literal_eval(node.value)
                break
        self.assertIsNotNone(embedded)
        self.assertEqual(json.loads(embedded), self.glossary)

    def test_app_uses_unified_policy_before_stale_assets_and_blocks_generic_fallback(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        core_start = source.index("def _translate_core")
        inner_start = source.index("def _translate_inner")
        core = source[core_start:inner_start]
        self.assertIn("should_force_factory_pipeline", core)
        self.assertIn("verified_exact_factory_case", core)
        self.assertLess(core.index("verified_exact_factory_case"), core.index("tm_module.tm_lookup"))
        inner = source[inner_start:]
        self.assertIn("allow_generic_nmt_fallback", inner)
        self.assertIn("generic NMT fallback blocked", inner)
        self.assertIn("_force_factory", inner)
        self.assertIn("cached = cache_get(text, src, tgt)", inner)
        self.assertIn("Versioned verified cache", inner)
        self.assertIn("exact = None if (_quality_critical or _force_factory)", inner)
        self.assertIn("def _translation_cache_asset_fingerprint", source)
        self.assertIn("tm_module.tm_lookup_verified_exact(", core)

        paragraph_start = source.index("def _translate_single_paragraph")
        paragraph_end = source.index("def _snapshot_translation_thread_context")
        paragraph = source[paragraph_start:paragraph_end]
        self.assertIn("cached = cache_get(text, src, tgt)", paragraph)
        self.assertIn("exact = None if _force_factory", paragraph)

    def test_regression_cases_pass_the_unified_guard_and_any_matching_knowledge(self):
        for case in self.regression["cases"]:
            src, tgt = case["direction"].split("-", 1)
            with self.subTest(case=case["id"]):
                report = guard.validate_translation(
                    case["source"], case["verified_target"], src, tgt
                )
                self.assertTrue(report.ok, (case["id"], report.issues))
                self.assertEqual(
                    guard.exact_verified_target(case["source"], src, tgt),
                    case["verified_target"],
                )
                cards = self.store.retrieve(case["source"], src, tgt, limit=10)
                if cards:
                    ok, issues = self.store.validate_translation(
                        cards, case["source"], case["verified_target"]
                    )
                    self.assertTrue(ok, (case["id"], issues))
                low = case["verified_target"].casefold()
                for group in case.get("required_target_any_groups", []):
                    self.assertTrue(any(term.casefold() in low for term in group), (case["id"], group))
                for forbidden in case.get("forbidden_target", []):
                    self.assertNotIn(forbidden.casefold(), low, (case["id"], forbidden))

    def test_every_regression_forbidden_probe_is_rejected_by_unified_guard(self):
        for case in self.regression["cases"]:
            src, tgt = case["direction"].split("-", 1)
            forbidden = [x for x in case.get("forbidden_target", []) if str(x).strip()]
            if not forbidden:
                continue
            bad = case["verified_target"] + " " + forbidden[0]
            with self.subTest(case=case["id"], forbidden=forbidden[0]):
                report = guard.validate_translation(case["source"], bad, src, tgt)
                self.assertFalse(report.ok, (case["id"], report.issues))
                self.assertTrue(report.issues)

    def test_factory_examples_become_exact_verified_casebook_targets(self):
        examples = []
        for entry in self.knowledge_doc["entries"]:
            for example in entry.get("examples", []):
                if "zh-id" in entry.get("directions", []):
                    examples.append({
                        "zh": example["source"], "id": example["target"], "dir": "zh2id",
                        "origin": "factory_knowledge", "case_id": entry["id"],
                        "bad_target": example.get("bad_target", ""),
                        "reason": example.get("reason", ""),
                        "source_match": entry.get("match", {}),
                    })
        source = "大成週一抓帳，還有160噸會陸續到料，有看到大成麻煩優先安排包裝。"
        cases = translation_casebook.retrieve(source, "zh", "id", examples=examples, max_cases=8, min_score=0.22)
        exact = translation_casebook.exact_verified_target(source, cases)
        self.assertEqual(
            exact,
            "大成 akan melakukan tutup buku pada hari Senin. Masih ada 160 ton material yang akan tiba secara bertahap. Jika melihat material 大成, mohon prioritaskan pengaturan proses pengemasannya.",
        )


if __name__ == "__main__":
    unittest.main()
