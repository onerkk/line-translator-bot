import unittest

import translation_quality_gate as quality_gate


SCREENSHOT_ID = """@All Teman-teman, mohon diperhatikan.
Jika ada barang yang rusak dan perlu dipotong atau dibuang, harap cek terlebih dahulu jenis barang dan kodenya. Jangan membuang barang sembarangan.

Contohnya, barang jenis 310 dengan kode A7 dibuang ke tempat pembuangan 316. Padahal, tempat pembuangan 316 hanya boleh digunakan hntuk barang dengan kode A3, A4, A8, dan FA.

Jadi, sebelum memotong atau membuang barang, wajib periksa jenis dan kodenya terlebih dahulu agar tidak salah tempat dan tidak terjadi kesalahan dalam proses kerja.

Mohon semua rekan kerja lebih teliti dan mematuhi aturan ini. Terima kasih atas kerja samanya."""

SCREENSHOT_ZH = """@All 各位同事，請注意。
如果有損壞且需要切割或丟棄的物品，請先確認物品種類及其代碼。請勿隨意丟棄物品。

例如，種類為310、代碼A7的物品被丟到316廢棄區。然而，316廢棄區僅可供代碼A3、A4、A8及FA的物品使用。

因此，在切割或丟棄物品前，務必先檢查物品種類和代碼，以免放錯位置，並避免作業過程出錯。

請所有同仁更加仔細並遵守此規定。感謝大家的配合。"""


class ContextualFactoryIdentifierRootFixTests(unittest.TestCase):
    def test_screenshot_identifiers_are_in_the_immutable_inventory(self):
        values = list(
            quality_gate.inspect_immutable_spans(SCREENSHOT_ID).mapping.values()
        )

        self.assertIn("FA", values)
        self.assertIn("310", values)
        self.assertEqual(values.count("316"), 2)
        for code in ("A7", "A3", "A4", "A8"):
            self.assertIn(code, values)

    def test_correct_indonesian_to_chinese_announcement_is_deliverable(self):
        report = quality_gate.validate_translation(
            SCREENSHOT_ID,
            SCREENSHOT_ZH,
            "id",
            "zh",
            require_paragraph_fidelity=True,
        )

        self.assertTrue(report.ok, report.issues)
        self.assertNotIn("untranslated_source_word:FA", report.issues)

    def test_missing_bare_code_or_disposal_location_is_rejected(self):
        without_fa = quality_gate.validate_translation(
            SCREENSHOT_ID,
            SCREENSHOT_ZH.replace("及FA", ""),
            "id",
            "zh",
        )
        without_one_316 = quality_gate.validate_translation(
            SCREENSHOT_ID,
            SCREENSHOT_ZH.replace("316廢棄區", "廢棄區", 1),
            "id",
            "zh",
        )

        self.assertIn("missing_literal:FA", without_fa.hard_issues)
        self.assertIn("missing_literal:316", without_one_316.hard_issues)

    def test_chinese_to_indonesian_uses_the_same_identifier_contract(self):
        envelope = quality_gate.inspect_immutable_spans(SCREENSHOT_ZH)

        report = quality_gate.validate_translation(
            SCREENSHOT_ZH,
            SCREENSHOT_ID.replace("hntuk", "untuk"),
            "zh",
            "id",
            immutable_literals=envelope.mapping.values(),
            require_paragraph_fidelity=True,
        )

        self.assertTrue(report.ok, report.issues)
        self.assertIn("FA", envelope.mapping.values())
        self.assertEqual(list(envelope.mapping.values()).count("316"), 2)

    def test_unlabelled_uppercase_indonesian_words_are_not_whitelisted(self):
        source = "TIDAK BOLEH masuk."
        envelope = quality_gate.inspect_immutable_spans(source)
        report = quality_gate.validate_translation(
            source,
            "不BOLEH進入。",
            "id",
            "zh",
            immutable_literals=envelope.mapping.values(),
        )

        self.assertNotIn("BOLEH", envelope.mapping.values())
        self.assertIn("untranslated_source_word:BOLEH", report.hard_issues)


if __name__ == "__main__":
    unittest.main()
