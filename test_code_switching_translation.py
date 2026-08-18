import unittest
from unittest import mock

import language_detection
import nmt_provider


class CodeSwitchingTranslationTests(unittest.TestCase):
    def test_detects_chinese_indonesian_code_switching(self):
        profile = language_detection.analyze_code_switching(
            "今天 mesin ini rusak dan harus berhenti", "zh", "id"
        )
        self.assertTrue(profile["is_mixed"])
        self.assertIn("zh", profile["languages"])
        self.assertIn("id", profile["languages"])
        note = language_detection.code_switching_instruction(profile)
        self.assertIn("Translate every natural-language span", note)

    def test_equipment_codes_do_not_trigger_code_switching(self):
        profile = language_detection.analyze_code_switching(
            "請確認 BF2、QC、PM160", "zh", "id"
        )
        self.assertFalse(profile["is_mixed"])

    def test_protected_name_does_not_trigger_secondary_language(self):
        profile = language_detection.analyze_code_switching(
            "Tolong 阿明 cek mesin", "id", "zh", protected_literals=["阿明"]
        )
        self.assertFalse(profile["is_mixed"])

    def test_mixed_sentence_is_not_split_or_sent_to_nmt(self):
        with mock.patch.object(nmt_provider, "NMT_PROVIDER", "google"):
            use_nmt, reason = nmt_provider.nmt_route_reason(
                "今天 mesin ini rusak dan harus berhenti", "zh", "id"
            )
        self.assertFalse(use_nmt)
        self.assertEqual(reason, "mixed_language")


if __name__ == "__main__":
    unittest.main()
