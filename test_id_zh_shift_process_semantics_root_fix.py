import json
import unittest
from pathlib import Path

import factory_message_semantics as semantics
import factory_translation_guard as guard
import translation_quality_gate as quality_gate


ROOT = Path(__file__).resolve().parent
SCREENSHOT_SOURCE = "Sip pagi tida mengasih warna cat"
SCREENSHOT_TARGET = "早班沒有噴漆"


class IndonesianShiftProcessSemanticsRootFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        guard.reload()

    def test_screenshot_claim_is_parsed_by_roles_not_isolated_words(self):
        frame = semantics.build_frame(SCREENSHOT_SOURCE, "id", "zh")
        self.assertTrue(frame["active"])
        self.assertTrue(frame["complete"])
        self.assertEqual(frame["kind"], "id_zh_shift_process_status")
        self.assertEqual(frame["slots"]["shift_alias"], "sip")
        self.assertEqual(frame["slots"]["shift_target"], "早班")
        self.assertEqual(frame["slots"]["process"], "spray_painting")
        self.assertEqual(frame["slots"]["completion"], "not_done")
        self.assertEqual(
            semantics.translate_source_directly(SCREENSHOT_SOURCE, "id", "zh"),
            SCREENSHOT_TARGET,
        )

    def test_shift_alias_period_negation_and_paint_variants_compose(self):
        cases = (
            ("Sif malam tidak mengecat", "夜班沒有噴漆"),
            ("shif siang tdk menyemprot cat", "中班沒有噴漆"),
            (
                "shift sore belum melakukan pengecatan semprot",
                "小夜班還沒有噴漆",
            ),
            ("sip pagi gak semprot cat", "早班沒有噴漆"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(
                    semantics.translate_source_directly(source, "id-ID", "zh-TW"),
                    expected,
                )

    def test_contextual_preprocessor_runs_locally_and_preserves_meaning(self):
        normalized, replacements = (
            semantics.normalize_indonesian_factory_colloquialisms(
                SCREENSHOT_SOURCE
            )
        )
        self.assertEqual(
            normalized,
            "shift pagi tidak melakukan pengecatan semprot",
        )
        self.assertEqual(replacements, 3)

    def test_acknowledgement_greeting_and_colour_request_are_not_reinterpreted(self):
        controls = (
            "Sip, terima kasih.",
            "Sip pagi!",
            "Selamat pagi, Pak.",
            "Tolong memberi warna cat biru.",
            "Pagi tidak memberi warna cat.",
        )
        for source in controls:
            with self.subTest(source=source):
                frame = semantics.build_frame(source, "id", "zh")
                self.assertFalse(frame["active"])
                self.assertEqual(
                    semantics.translate_source_directly(source, "id", "zh"),
                    "",
                )

    def test_incomplete_extra_clause_is_never_dropped_by_fast_path(self):
        source = SCREENSHOT_SOURCE + ", karena mesinnya rusak"
        frame = semantics.build_frame(source, "id", "zh")
        self.assertTrue(frame["active"])
        self.assertFalse(frame["complete"])
        self.assertEqual(frame["unparsed"], "karena mesinnya rusak")
        self.assertEqual(
            semantics.translate_source_directly(source, "id", "zh"), ""
        )
        prompt = semantics.build_prompt(frame)
        self.assertIn("早班, not 早上好", prompt)
        self.assertIn("never as not supplying/providing", prompt)

    def test_validators_reject_greeting_supply_and_lost_negation(self):
        frame = semantics.build_frame(SCREENSHOT_SOURCE, "id", "zh")
        probes = {
            "早上好，沒有提供油漆顏色。": {
                "factory_message_semantics:shift_mistranslated_as_greeting",
                "factory_message_semantics:paint_action_mistranslated_as_supply",
            },
            "早班有噴漆": {
                "factory_message_semantics:process_negation_missing",
            },
            "早班有噴漆，沒有問題": {
                "factory_message_semantics:process_negation_missing",
            },
        }
        for candidate, expected_issues in probes.items():
            with self.subTest(candidate=candidate):
                ok, issues = semantics.validate_translation(frame, candidate)
                self.assertFalse(ok)
                self.assertTrue(expected_issues.issubset(set(issues)))
                self.assertFalse(
                    quality_gate.validate_translation(
                        SCREENSHOT_SOURCE, candidate, "id", "zh"
                    ).ok
                )
                self.assertFalse(
                    guard.validate_translation(
                        SCREENSHOT_SOURCE, candidate, "id", "zh"
                    ).ok
                )

    def test_verified_target_passes_all_boundaries_and_regression_asset(self):
        self.assertTrue(
            quality_gate.validate_translation(
                SCREENSHOT_SOURCE, SCREENSHOT_TARGET, "id", "zh"
            ).ok
        )
        report = guard.validate_translation(
            SCREENSHOT_SOURCE, SCREENSHOT_TARGET, "id", "zh"
        )
        self.assertTrue(report.ok, report.issues)
        self.assertEqual(
            guard.exact_verified_target(SCREENSHOT_SOURCE, "id", "zh"),
            SCREENSHOT_TARGET,
        )
        regression = json.loads(
            (ROOT / "factory_translation_regression.json").read_text(
                encoding="utf-8"
            )
        )
        row = next(
            item
            for item in regression["cases"]
            if item["id"] == "id_colloquial_morning_shift_spray_paint_status"
        )
        self.assertEqual(row["verified_target"], SCREENSHOT_TARGET)

    def test_app_preprocesses_context_before_generic_sip_mapping(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        start = source.index("def normalize_indonesian_text(text):")
        end = source.index("def normalize_indonesian_text_with_nano", start)
        body = source[start:end]
        contextual = body.index(
            "normalize_indonesian_factory_colloquialisms"
        )
        generic = body.index("sorted_map = sorted(ID_NORMALIZATION_MAP.items()")
        self.assertLess(contextual, generic)
        self.assertIn(
            '"sip": "baik"',
            source,
        )


if __name__ == "__main__":
    unittest.main()
