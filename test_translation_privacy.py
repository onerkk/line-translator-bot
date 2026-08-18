import unittest

import translation_privacy as privacy
import translation_quality_gate as quality_gate


class TranslationPrivacyTests(unittest.TestCase):
    def test_masks_and_restores_supported_sensitive_values(self):
        source = (
            "請寄到 worker@example.com，手機 0912-345-678，"
            "NIK: 3173051201900001，通知王小明。"
        )
        envelope = privacy.mask_sensitive_text(
            source, extra_literals=["王小明"], enabled=True
        )
        self.assertTrue(envelope.has_sensitive_data)
        for literal in (
            "worker@example.com", "0912-345-678", "3173051201900001", "王小明",
        ):
            self.assertNotIn(literal, envelope.masked)
        self.assertIn("NIK: ", envelope.masked)

        provider_output = "Hubungi " + " / ".join(envelope.mapping.keys())
        ok, missing = privacy.placeholders_preserved(provider_output, envelope)
        self.assertTrue(ok, missing)
        restored = privacy.restore_sensitive_text(provider_output, envelope)
        for literal in envelope.mapping.values():
            self.assertIn(literal, restored)

    def test_restoration_tolerates_provider_spacing_drift(self):
        envelope = privacy.mask_sensitive_text(
            "email: worker@example.com", enabled=True
        )
        placeholder = next(iter(envelope.mapping))
        match = privacy._PLACEHOLDER_RE.fullmatch(placeholder)
        drifted = "QG KEEP %d %s" % (int(match.group(1)), match.group(2))
        self.assertEqual(
            privacy.restore_sensitive_text(drifted, envelope),
            "worker@example.com",
        )

    def test_unlabelled_factory_number_is_not_overmasked(self):
        source = "工單 3173051201900001 已經放行"
        envelope = privacy.mask_sensitive_text(source, enabled=True)
        self.assertEqual(envelope.masked, source)
        self.assertFalse(envelope.mapping)

    def test_message_batch_masks_historical_example_pii(self):
        current = privacy.mask_sensitive_text("普通文字", enabled=True)
        messages = [
            {"role": "system", "content": "只輸出翻譯"},
            {"role": "user", "content": "聯絡 old@example.com"},
        ]
        masked = privacy.mask_messages(messages, current)
        self.assertNotIn("old@example.com", masked[1]["content"])
        self.assertIn("__QG_KEEP_", masked[1]["content"])

    def test_missing_placeholder_is_detected(self):
        envelope = privacy.mask_sensitive_text("0912-345-678", enabled=True)
        ok, missing = privacy.placeholders_preserved("nomor hilang", envelope)
        self.assertFalse(ok)
        self.assertEqual(len(missing), 1)

    def test_source_bound_placeholder_is_not_reported_as_a_leak(self):
        envelope = privacy.mask_sensitive_text("電話 0912-345-678", enabled=True)
        placeholder = next(iter(envelope.mapping))
        report = quality_gate.validate_translation(
            envelope.masked,
            "Nomor telepon " + placeholder,
            "zh",
            "id",
            require_paragraph_fidelity=False,
        )
        self.assertNotIn("placeholder_leak", report.hard_issues)
        self.assertFalse(any(
            issue.startswith("missing_pipeline_token:") for issue in report.hard_issues
        ))


if __name__ == "__main__":
    unittest.main()
