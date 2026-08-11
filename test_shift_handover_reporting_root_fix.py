import unittest
from pathlib import Path

import factory_knowledge
import factory_semantic_audit as audit
import factory_translation_guard as guard
import translation_quality_gate as quality_gate


ROOT = Path(__file__).resolve().parent
SOURCE = "@All 在交接班時遇到任何生產問題 需要在一個小時內反應給班長 班長比較好做異常反應"
SCREENSHOT_TRANSLATION = (
    "@All Kalau saat pergantian shift menemukan masalah produksi apa pun, harus dilaporkan "
    "ke kepala regu dalam waktu 1 jam, supaya kepala regu bisa lebih cepat menindaklanjuti "
    "kondisi yang tidak normal."
)
VERIFIED_TRANSLATION = (
    "@All Jika menemukan masalah produksi apa pun saat pergantian shift, masalah tersebut "
    "harus dilaporkan kepada kepala regu dalam waktu satu jam agar kepala regu dapat "
    "menindaklanjuti kondisi abnormal dengan lebih baik."
)


class ShiftHandoverReportingRootFixTests(unittest.TestCase):
    def setUp(self):
        self.frame = audit.build_source_frame(SOURCE, "zh", "id")

    def test_source_is_decomposed_into_linked_operational_claims(self):
        self.assertTrue(self.frame["active"])
        self.assertTrue(audit.should_force_review(self.frame))
        self.assertEqual(self.frame["counts"]["report_deadline"], 1)
        self.assertEqual(self.frame["units"]["report_deadline"], "hour")
        self.assertTrue({
            "shift_handover_context",
            "any_production_problem_scope",
            "report_to_shift_leader",
            "reporting_deadline",
            "abnormal_followup_purpose",
        }.issubset({claim["claim_id"] for claim in self.frame["claims"]}))

    def test_screenshot_translation_is_rejected_for_invented_speed(self):
        ok, issues = audit.validate_translation(self.frame, SCREENSHOT_TRANSLATION)
        self.assertFalse(ok)
        self.assertIn("factory_semantic_audit:abnormal_followup_purpose_missing", issues)
        self.assertIn("factory_semantic_audit:unsupported_followup_speed_inference", issues)

        passive_speed = (
            "@All Jika menemukan masalah produksi apa pun saat pergantian shift, laporkan kepada "
            "kepala regu dalam waktu satu jam agar kondisi abnormal dapat segera ditindaklanjuti "
            "dengan lebih baik oleh kepala regu."
        )
        ok, issues = audit.validate_translation(self.frame, passive_speed)
        self.assertFalse(ok)
        self.assertIn("factory_semantic_audit:unsupported_followup_speed_inference", issues)

    def test_verified_translation_preserves_scope_deadline_recipient_and_purpose(self):
        self.assertEqual(
            audit.validate_translation(self.frame, VERIFIED_TRANSLATION),
            (True, []),
        )
        self.assertEqual(audit.deterministic_rebuild(self.frame), VERIFIED_TRANSLATION)

    def test_paraphrases_use_the_current_deadline_instead_of_copying_the_example(self):
        source = "換班時若發現所有製程問題，必須在45分鐘內報告給班長，讓班長更容易做異常處理。"
        self.assertIsNone(guard.exact_verified_target(source, "zh", "id"))
        frame = audit.build_source_frame(source, "zh", "id")
        self.assertTrue(frame["active"])
        self.assertEqual(frame["counts"]["report_deadline"], 45)
        self.assertEqual(frame["units"]["report_deadline"], "minute")
        rebuilt = audit.deterministic_rebuild(frame)
        self.assertIn("dalam waktu 45 menit", rebuilt)
        self.assertNotIn("satu jam", rebuilt)
        self.assertNotIn("lebih cepat", rebuilt)
        self.assertEqual(audit.validate_translation(frame, rebuilt), (True, []))

    def test_unrelated_reaction_or_handover_text_does_not_activate_the_frame(self):
        controls = (
            "交接班紀錄已完成。",
            "材料遇熱會產生化學反應，一小時後再檢查。",
            "班長要求機台加快速度。",
        )
        for source in controls:
            with self.subTest(source=source):
                self.assertFalse(audit.build_source_frame(source, "zh", "id")["active"])

    def test_knowledge_and_exact_guard_reject_the_production_mistranslation(self):
        store = factory_knowledge.FactoryKnowledgeStore(str(ROOT / "factory_knowledge.json"))
        cards = store.retrieve(SOURCE, "zh", "id", limit=10)
        handover = [
            card for card in cards
            if card.get("id") == "shift_handover_production_problem_reporting"
        ]
        self.assertEqual(len(handover), 1)
        self.assertEqual(
            store.validate_translation(handover, SOURCE, VERIFIED_TRANSLATION),
            (True, []),
        )
        ok, issues = store.validate_translation(
            handover, SOURCE, SCREENSHOT_TRANSLATION
        )
        self.assertFalse(ok)
        self.assertTrue(any("lebih cepat" in issue for issue in issues))

        self.assertEqual(
            guard.exact_verified_target(SOURCE, "zh", "id"),
            VERIFIED_TRANSLATION,
        )
        self.assertTrue(
            guard.validate_translation(SOURCE, VERIFIED_TRANSLATION, "zh", "id").ok
        )
        self.assertFalse(
            guard.validate_translation(SOURCE, SCREENSHOT_TRANSLATION, "zh", "id").ok
        )

    def test_quality_gate_rebuilds_from_source_when_review_is_unavailable(self):
        result = quality_gate.gate_and_revise(
            SOURCE,
            SCREENSHOT_TRANSLATION,
            "zh",
            "id",
            critical=False,
            model="test-model",
            ai_client=None,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], VERIFIED_TRANSLATION)
        self.assertEqual(result["path"], "deterministic_source_frame_rebuild")

    def test_duplicate_role_rules_use_one_canonical_output(self):
        app_text = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('"kepala shift": "kepala regu",', app_text)
        self.assertIn('"kepala regu": "班長",', app_text)
        self.assertNotIn('"kepala shift": "kepala regu / 班長"', app_text)
        self.assertNotIn('"班長": "ketua shift / kepala regu"', app_text)


if __name__ == "__main__":
    unittest.main()
