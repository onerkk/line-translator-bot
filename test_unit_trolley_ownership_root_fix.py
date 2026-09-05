import re
import unittest
from pathlib import Path

import factory_message_semantics as semantics
import factory_translation_guard as guard
import translation_quality_gate as quality_gate


ROOT = Path(__file__).resolve().parent
SOURCE = "削皮需要G8G9台車 麻煩一下"
TARGET = (
    "Bagian Peeling membutuhkan troli dari unit G8 dan G9. "
    "Mohon bantuannya."
)
SCREENSHOT_BAD_TARGET = (
    "Untuk proses kupas kulit, diperlukan troli angkut batang G8G9. "
    "Mohon bantuannya."
)


class UnitTrolleyOwnershipRootFixTests(unittest.TestCase):
    def test_screenshot_sentence_is_translated_from_source_roles(self):
        frame = semantics.build_frame(SOURCE, "zh", "id")
        self.assertTrue(frame["active"])
        self.assertTrue(frame["complete"])
        self.assertEqual(frame["kind"], "zh_id_factory_unit_trolley_request")
        self.assertEqual(frame["slots"]["receiver_id"], "Bagian Peeling")
        self.assertEqual(frame["slots"]["owner_unit_codes"], ["G8", "G9"])
        self.assertEqual(
            semantics.translate_source_directly(SOURCE, "zh", "id"), TARGET
        )

    def test_compact_and_separated_unit_spellings_have_the_same_roles(self):
        cases = (
            ("削皮需要 G8、G9 的台車，麻煩一下", TARGET),
            ("削皮需要 G8/G9 台車，麻煩一下", TARGET),
            ("削皮需要 G8 G9 台車，麻煩一下", TARGET),
            (
                "削皮那邊目前還需要 G8/9 台車，請幫忙",
                "Saat ini, Bagian Peeling masih membutuhkan troli dari unit "
                "G8 dan G9. Mohon bantuannya.",
            ),
            (
                "@法比恩 Fabian 削皮站要借 G8G9 台車",
                "@法比恩 Fabian Bagian Peeling membutuhkan troli dari unit "
                "G8 dan G9.",
            ),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                frame = semantics.build_frame(source, "zh-TW", "id-ID")
                self.assertEqual(frame["slots"]["owner_unit_codes"], ["G8", "G9"])
                self.assertEqual(
                    semantics.translate_source_directly(source, "zh-TW", "id-ID"),
                    expected,
                )

    def test_screenshot_output_is_rejected_for_every_changed_relation(self):
        frame = semantics.build_frame(SOURCE, "zh", "id")
        ok, issues = semantics.validate_translation(frame, SCREENSHOT_BAD_TARGET)
        self.assertFalse(ok)
        self.assertIn(
            "factory_message_semantics:trolley_receiving_section_missing", issues
        )
        self.assertIn(
            "factory_message_semantics:peeling_section_mistranslated_as_process",
            issues,
        )
        self.assertIn(
            "factory_message_semantics:unit_trolley_ownership_relation_missing",
            issues,
        )
        self.assertIn(
            "factory_message_semantics:ungrounded_trolley_function_added", issues
        )
        self.assertIn(
            "factory_message_semantics:factory_unit_code_missing:G8", issues
        )
        self.assertIn(
            "factory_message_semantics:factory_unit_code_missing:G9", issues
        )

    def test_codes_must_be_separate_units_not_one_trolley_label(self):
        frame = semantics.build_frame(SOURCE, "zh", "id")
        glued = "Bagian Peeling membutuhkan troli G8G9. Mohon bantuannya."
        ok, issues = semantics.validate_translation(frame, glued)
        self.assertFalse(ok)
        self.assertIn(
            "factory_message_semantics:unit_trolley_ownership_relation_missing",
            issues,
        )
        self.assertIn(
            "factory_message_semantics:factory_unit_code_missing:G8", issues
        )
        self.assertIn(
            "factory_message_semantics:factory_unit_code_missing:G9", issues
        )

    def test_unparsed_extra_clause_is_never_dropped_by_direct_route(self):
        source = SOURCE + "，晚班先不要拿"
        frame = semantics.build_frame(source, "zh", "id")
        self.assertTrue(frame["active"])
        self.assertFalse(frame["complete"])
        self.assertEqual(frame["unparsed"], "晚班先不要拿")
        self.assertEqual(semantics.translate_source_directly(source, "zh", "id"), "")
        prompt = semantics.build_prompt(frame)
        self.assertIn("factory-unit trolley request", prompt)
        self.assertIn("G8G9", prompt)

    def test_unrelated_g_codes_and_peeling_products_do_not_activate(self):
        controls = (
            "G8G9台車已經滿了",
            "這批削皮棒需要台車",
            "這批料需要G8G9台車",
            "削皮需要一台普通台車",
            "G8G9要削皮",
        )
        for source in controls:
            with self.subTest(source=source):
                frame = semantics.build_frame(source, "zh", "id")
                self.assertFalse(frame["active"])
                self.assertEqual(
                    semantics.translate_source_directly(source, "zh", "id"), ""
                )

    def test_shared_quality_boundaries_accept_fixed_and_reject_bad_target(self):
        protected = quality_gate.protect_immutable_spans(SOURCE)
        self.assertEqual(
            quality_gate.restore_immutable_spans(
                protected.protected, protected.mapping
            ),
            SOURCE,
        )
        immutable = quality_gate.inspect_immutable_spans(SOURCE)
        immutable_values = list(immutable.mapping.values())
        self.assertIn("G8", immutable_values)
        self.assertIn("G9", immutable_values)
        self.assertNotIn("G8G9", immutable_values)
        unrelated = quality_gate.inspect_immutable_spans("產品代碼G8G9請保留")
        self.assertIn("G8G9", unrelated.mapping.values())
        self.assertNotIn("G8", unrelated.mapping.values())
        self.assertNotIn("G9", unrelated.mapping.values())
        immutable_check = quality_gate.validate_translation(
            SOURCE,
            TARGET,
            "zh",
            "id",
            immutable_literals=immutable_values,
        )
        self.assertTrue(immutable_check.ok, immutable_check.issues)

        good_quality = quality_gate.validate_translation(
            SOURCE, TARGET, "zh", "id"
        )
        self.assertTrue(good_quality.ok, good_quality.issues)
        bad_quality = quality_gate.validate_translation(
            SOURCE, SCREENSHOT_BAD_TARGET, "zh", "id"
        )
        self.assertFalse(bad_quality.ok)
        self.assertIn(
            "factory_message_semantics:unit_trolley_ownership_relation_missing",
            bad_quality.issues,
        )

        good_guard = guard.validate_translation(SOURCE, TARGET, "zh", "id")
        self.assertTrue(good_guard.ok, good_guard.issues)
        bad_guard = guard.validate_translation(
            SOURCE, SCREENSHOT_BAD_TARGET, "zh", "id"
        )
        self.assertFalse(bad_guard.ok)

    def test_deployment_revision_and_builtin_examples_are_consistent(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        expected = "2026-09-02.1-operational-data-continuity"
        self.assertEqual(semantics.FACTORY_MESSAGE_SEMANTICS_BUILD_ID, expected)
        self.assertIn(
            f'_EXPECTED_FACTORY_MESSAGE_SEMANTICS_BUILD_ID = "{expected}"',
            app_source,
        )
        quality_build = "2026-09-04.1-validated-delivery-only"
        self.assertEqual(quality_gate.QUALITY_GATE_BUILD_ID, quality_build)
        self.assertIn(
            f'_EXPECTED_QG_BUILD_ID = "{quality_build}"', app_source
        )
        self.assertIn('{"zh": "台車", "id": "troli", "dir": "zh2id"}', app_source)
        self.assertNotIn(
            '{"zh": "台車", "id": "troli angkut batang", "dir": "zh2id"}',
            app_source,
        )
        self.assertIsNone(
            re.search(
                r'\{"zh": "台車再幫忙一下", "id": "[^"]*turunkan batangnya',
                app_source,
            )
        )
        self.assertTrue(semantics.health()["self_test"]["ok"])


if __name__ == "__main__":
    unittest.main()
