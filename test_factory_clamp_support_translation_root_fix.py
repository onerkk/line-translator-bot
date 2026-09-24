"""Regression tests for retaining the factory term 夾靠 in translations."""
from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest import mock

import app
import factory_translation_guard as guard


SOURCE = "@Irwan 布納蘭 缺夾靠幫忙處理一下"
BAD_TRANSLATION = "@Irwan 布納蘭 Ada kurang, tolong bantu proses. 🙂"
GOOD_TRANSLATION = (
    "@Irwan 布納蘭 Dudukan penjepit batang kurang, mohon ditangani. 🙂"
)
SHORT_BAR_SOURCE = "短尺圖維護"
SHORT_BAR_BAD_TRANSLATION = "Pemeliharaan data ukuran pendek tidak teratur"
SHORT_BAR_TRANSLATION = "Pemeliharaan data spesifikasi material pendek"
REPEATED_DEFECT_SOURCE = "常整理的應該有發現都是重複缺失，麻煩注意不要累犯被拍照"
REPEATED_DEFECT_BAD_TRANSLATION = (
    "Yang sering melakukan penataan seharusnya sudah menyadari bahwa "
    "kekurangannya selalu berulang. 🙂 Mohon diperhatikan agar tidak "
    "mengulangi pelanggaran dan difoto lagi. 🙏"
)


class FactoryClampSupportTranslationRootFixTests(unittest.TestCase):
    def test_shared_knowledge_explains_the_component_and_requires_it_in_output(self):
        cards = guard.retrieve_cards(SOURCE, "zh", "id", limit=10)
        matching = [card for card in cards if card.get("id") == "factory_clamp_support_component"]

        self.assertEqual(len(matching), 1)
        contract = app.build_translation_semantic_contract(SOURCE, "zh", "id")
        contextual_cards = app._factory_knowledge_cards_from_contract(contract)
        self.assertIn(
            "dudukan penjepit batang",
            app.factory_knowledge_module.build_prompt(contextual_cards),
        )
        self.assertIsNone(guard.exact_verified_target(SOURCE, "zh", "id"))

        good_report = guard.validate_translation(SOURCE, GOOD_TRANSLATION, "zh", "id")
        self.assertTrue(good_report.ok, good_report.issues)

        bad_report = guard.validate_translation(SOURCE, BAD_TRANSLATION, "zh", "id")
        self.assertFalse(bad_report.ok)
        self.assertTrue(
            any("clamp_support_component_missing" in issue for issue in bad_report.issues),
            bad_report.issues,
        )

    def test_component_rule_covers_paraphrases_and_reverse_translation(self):
        zh_cases = (
            ("夾靠不夠，請幫忙處理", "Dudukan penjepit batang tidak mencukupi, mohon ditangani."),
            ("夾靠不足，麻煩處理一下", "Dudukan penjepit batang kurang, mohon ditangani."),
        )
        for source, target in zh_cases:
            with self.subTest(source=source):
                report = guard.validate_translation(source, target, "zh", "id")
                self.assertTrue(report.ok, report.issues)
                self.assertFalse(guard.validate_translation(source, "Ada kurang, mohon ditangani.", "zh", "id").ok)

        source_id = "Dudukan penjepit batangnya kurang, tolong ditangani."
        target_zh = "夾靠不足，麻煩處理一下。"
        report = guard.validate_translation(source_id, target_zh, "id", "zh")
        self.assertTrue(report.ok, report.issues)
        self.assertFalse(guard.validate_translation(source_id, "東西不夠，請處理。", "id", "zh").ok)

    def test_known_bad_cached_answer_is_evicted_under_current_asset_fingerprint(self):
        key = (SOURCE, "zh", "id", app._translation_cache_scope())
        rows = {
            key: (
                BAD_TRANSLATION,
                time.time(),
                app._translation_cache_asset_fingerprint(),
            )
        }
        old_rows = app.translation_cache
        app.translation_cache = rows
        try:
            self.assertIsNone(app.cache_get(SOURCE, "zh", "id"))
            self.assertNotIn(key, rows)
        finally:
            app.translation_cache = old_rows

    def test_delivery_validation_reports_the_missing_component(self):
        issues = app._delivery_validation_issues(
            SOURCE, BAD_TRANSLATION, "zh", "id"
        )
        self.assertTrue(
            any("clamp_support_component_missing" in issue for issue in issues),
            issues,
        )
        self.assertFalse(
            app._delivery_validation_issues(SOURCE, GOOD_TRANSLATION, "zh", "id")
        )

    def test_semantic_contract_repairs_a_safe_generic_omission(self):
        contract = app.build_translation_semantic_contract(SOURCE, "zh", "id")
        repaired = app.enforce_translation_semantic_contract(
            contract, SOURCE, BAD_TRANSLATION
        )
        self.assertIn("dudukan penjepit batang kurang", repaired.lower())
        self.assertTrue(app.translation_satisfies_semantic_contract(contract, repaired)[0])

    def test_final_delivery_repairs_cached_or_provider_candidate(self):
        repaired = app._final_delivery_guard(SOURCE, BAD_TRANSLATION, "zh", "id")
        self.assertIn("dudukan penjepit batang kurang", repaired.lower())
        self.assertFalse(app._delivery_validation_issues(SOURCE, repaired, "zh", "id"))

    def test_reverse_direction_repair_restores_the_component_name(self):
        source = "Dudukan penjepit batangnya kurang, tolong ditangani."
        cards = app.factory_knowledge_module.retrieve(source, "id", "zh", limit=5)
        repaired = app.factory_knowledge_module.repair_translation(
            cards, source, "東西不夠，請處理。", "id", "zh"
        )
        self.assertEqual(repaired, "夾靠不足，請處理。")

    def test_short_bar_system_data_is_distinct_from_physical_short_bar_handling(self):
        cards = app.factory_knowledge_module.retrieve(SHORT_BAR_SOURCE, "zh", "id", limit=5)
        self.assertIn(
            "factory_short_bar_specification_data_maintenance",
            {card.get("id") for card in cards},
        )
        prompt = app.factory_knowledge_module.build_prompt(cards)
        self.assertIn("pemeliharaan data spesifikasi material pendek", prompt)
        self.assertIn("penanganan material pendek", prompt)

        bad_report = guard.validate_translation(
            SHORT_BAR_SOURCE, SHORT_BAR_BAD_TRANSLATION, "zh", "id"
        )
        self.assertFalse(bad_report.ok)
        self.assertTrue(
            any("short_bar_specification_data_maintenance_missing" in issue
                for issue in bad_report.issues),
            bad_report.issues,
        )
        repaired = app._final_delivery_guard(
            SHORT_BAR_SOURCE, SHORT_BAR_BAD_TRANSLATION, "zh", "id"
        )
        self.assertEqual(repaired, SHORT_BAR_TRANSLATION)

    def test_repeated_defect_warning_keeps_photo_consequence_and_workplace_tone(self):
        cards = app.factory_knowledge_module.retrieve(
            REPEATED_DEFECT_SOURCE, "zh", "id", limit=5
        )
        self.assertIn(
            "factory_repeat_defect_photo_warning",
            {card.get("id") for card in cards},
        )
        fixed = app.factory_knowledge_module.repair_translation(
            cards,
            REPEATED_DEFECT_SOURCE,
            REPEATED_DEFECT_BAD_TRANSLATION,
            "zh",
            "id",
        )
        self.assertIn("jika terulang, akan difoto lagi", fixed.lower())
        self.assertTrue(app.factory_knowledge_module.validate_translation(
            cards, REPEATED_DEFECT_SOURCE, fixed
        )[0])

        expressive = app.expressive_engine_module.enhance_translation(
            REPEATED_DEFECT_SOURCE,
            REPEATED_DEFECT_BAD_TRANSLATION,
            source_language="zh",
            settings=app.expressive_engine_module.ExpressiveSettings(
                enabled=True,
                display_mode="emoji",
                emoji_enabled=True,
                images_enabled=False,
                formal_safety_enabled=True,
            ),
        )
        self.assertNotIn("🙂", expressive.text)
        self.assertNotIn("🙏", expressive.text)
        self.assertEqual(expressive.decorated_count, 0)

    def test_shared_provider_prompt_receives_the_term_mapping(self):
        source = "缺夾靠幫忙處理一下"
        target = "Ada kurang, tolong bantu proses. 🙂"
        requests = []

        def fake_provider(**kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=target), finish_reason="stop"
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                model="offline",
                _provider="openai",
            )

        contract = app.build_translation_semantic_contract(source, "zh", "id")
        previous = getattr(app._tl, "semantic_contract", None)
        app._tl.semantic_contract = contract
        try:
            with mock.patch.object(app.ai.chat.completions, "create", side_effect=fake_provider):
                translated = app.translate_openai(source, "zh", "id")
        finally:
            if previous is None:
                delattr(app._tl, "semantic_contract")
            else:
                app._tl.semantic_contract = previous

        self.assertIn("dudukan penjepit batang kurang", translated.lower())
        self.assertIn("tolong bantu proses", translated.lower())
        self.assertEqual(len(requests), 1)
        prompt = "\n".join(str(item["content"]) for item in requests[0]["messages"])
        self.assertIn("dudukan penjepit batang", prompt)
        self.assertIn("夾靠", prompt)


if __name__ == "__main__":
    unittest.main()
