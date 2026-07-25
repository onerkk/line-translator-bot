import unittest
from types import SimpleNamespace

import translation_quality_gate as qg


SOURCE_ANNOUNCEMENT = """@All 📢 PENGUMUMAN UNTUK SEMUA OPERATOR

Mohon diperhatikan dan dipahami oleh seluruh pekerja terkait kolom yang sudah ditandai merah pada lembar kerja atau book order (套罩).

✅ Jika pada kolom tersebut tertulis “Y”, maka WAJIB menggunakan kondom pelindung (套罩) sesuai standar kerja.

❌ Jika pada kolom tersebut tertulis ”-”, maka TIDAK BOLEH menggunakan kondom pelindung (套罩).

Setiap operator wajib mengecek dan memahami tanda tersebut sebelum mulai produksi untuk menghindari kesalahan proses dan menjaga kualitas produk.

Harap informasi ini diperhatikan dan dijalankan oleh seluruh pekerja tanpa terkecuali.

Terima kasih atas kerja samanya. 🙏"""

BAD_TRANSLATION = """@All 📢 給所有作業員的公告

請全體相關人員注意並理解工作單或訂單本（套罩）上已標示紅色的欄位。

✅ 若該欄位寫著「Y」，則必須依照作業標準使用保護套（套罩）。

❌ 若該欄位寫著「-」，則不BOLEH使用保護套（套罩）。

每位作業員在開始生產前都必須檢查並理解該標示，以避免製程錯誤並維持產品品質。

請所有員工務必留意並執行此資訊，無一例外。

感謝您的配合。🙏"""

CLEAN_TRANSLATION = BAD_TRANSLATION.replace("不BOLEH使用", "不得使用")


class FakeClient:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def chat_complete(self, **kwargs):
        self.calls.append(kwargs)
        if not self.outputs:
            raise RuntimeError("no fake output left")
        text = self.outputs.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
        )


class TranslationQualityGateTests(unittest.TestCase):
    def test_repeated_inline_annotation_infers_runtime_glossary(self):
        self.assertEqual(
            qg.infer_inline_bilingual_terms(SOURCE_ANNOUNCEMENT, "id", "zh"),
            [("kondom pelindung", "套罩")],
        )

    def test_single_untranslated_source_word_is_hard_failure(self):
        report = qg.validate_translation(
            SOURCE_ANNOUNCEMENT,
            BAD_TRANSLATION,
            "id",
            "zh",
            immutable_literals=["@All", "Y", "-"],
        )
        self.assertFalse(report.ok)
        self.assertIn("untranslated_source_word:BOLEH", report.hard_issues)

    def test_clean_chinese_translation_passes_language_purity(self):
        report = qg.validate_translation(
            SOURCE_ANNOUNCEMENT,
            CLEAN_TRANSLATION,
            "id",
            "zh",
            immutable_literals=["@All", "Y", "-"],
        )
        self.assertTrue(report.ok, report.issues)

    def test_codes_and_real_names_are_not_false_positives(self):
        source = "Mohon cek BF3, SOP, LINE, OpenAI, dan Taipei."
        candidate = "請檢查 BF3、SOP、LINE、OpenAI 與 Taipei。"
        report = qg.validate_translation(source, candidate, "id", "zh")
        self.assertTrue(report.ok, report.issues)

    def test_uppercase_sentence_word_is_not_misclassified_as_code(self):
        source = "TIDAK BOLEH masuk."
        candidate = "不BOLEH進入。"
        report = qg.validate_translation(source, candidate, "id", "zh")
        self.assertFalse(report.ok)
        self.assertIn("untranslated_source_word:BOLEH", report.hard_issues)

    def test_noncritical_invalid_candidate_is_delivered_without_second_api_call(self):
        client = FakeClient(["不得進入。"])
        result = qg.gate_and_revise(
            "TIDAK BOLEH masuk.",
            "不BOLEH進入。",
            "id",
            "zh",
            critical=False,
            model="fake-model",
            ai_client=client,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["text"], "不BOLEH進入。")
        self.assertTrue(result["degraded"])
        self.assertFalse(result["cacheable"])
        self.assertEqual(client.calls, [])

    def test_required_source_review_blocks_clean_candidate_when_provider_is_unavailable(self):
        result = qg.gate_and_revise(
            "請確認材料已經包裝完成。",
            "Mohon pastikan material sudah selesai dikemas.",
            "zh",
            "id",
            critical=False,
            model="fake-model",
            ai_client=None,
            force_review=True,
            require_review_success=True,
        )
        self.assertFalse(result["ok"], result)
        self.assertIsNone(result["text"])
        self.assertTrue(result["review_requested"])
        self.assertFalse(result["review_succeeded"])
        self.assertEqual(result["path"], "required_source_review_failed")

    def test_required_source_review_accepts_valid_reviewed_candidate(self):
        client = FakeClient(["Mohon pastikan material sudah selesai dikemas."])
        result = qg.gate_and_revise(
            "請確認材料已經包裝完成。",
            "Pastikan material selesai packing.",
            "zh",
            "id",
            critical=False,
            model="fake-model",
            ai_client=client,
            force_review=True,
            require_review_success=True,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["review_requested"])
        self.assertTrue(result["review_succeeded"])
        self.assertEqual(result["path"], "independent_source_review_passed")

    def test_critical_document_does_not_spend_second_api_call_after_local_rejection(self):
        client = FakeClient(["不BOLEH使用工具。", "不得使用工具。"])
        result = qg.translate_quality_critical_document(
            "TIDAK BOLEH menggunakan alat.",
            "id",
            "zh",
            model="fake-model",
            ai_client=client,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["text"], "不BOLEH使用工具。")
        self.assertEqual(result["path"], "best_effort_whole_document")
        self.assertFalse(result["cacheable"])
        self.assertEqual(len(client.calls), 1)

    def test_factory_notice_uppercase_words_are_not_immutable_codes(self):
        source = (
            "DAN BATU GERINDA. ROUGH GRINDING minimal 0,04 mm. "
            "FREE END. Mesin I13 minimal 0,05 mm."
        )
        envelope = qg.inspect_immutable_spans(source)
        literals = set(envelope.mapping.values())

        self.assertNotIn("DAN", literals)
        self.assertNotIn("BATU", literals)
        self.assertNotIn("ROUGH", literals)
        self.assertNotIn("GRINDING", literals)
        self.assertIn("I13", literals)
        self.assertIn("0.04 mm", literals)
        self.assertIn("0.05 mm", literals)

    def test_source_grounded_bilingual_process_labels_are_allowed(self):
        source = (
            "1 ROUGH GRINDING minimal 0,04 mm. "
            "2 FREE END jangan digerinda berulang. Mesin I13 minimal 0,05 mm."
        )
        candidate = (
            "1 粗磨（ROUGH GRINDING）最低 0.04 mm。"
            "2 FREE END 部位不可重複研磨。I13 機台最低 0.05 mm。"
        )
        envelope = qg.inspect_immutable_spans(source)
        report = qg.validate_translation(
            source,
            candidate,
            "id",
            "zh",
            immutable_literals=envelope.mapping.values(),
        )

        self.assertTrue(report.ok, report.issues)
        self.assertFalse(any("ROUGH" in issue for issue in report.issues))
        self.assertFalse(any("GRINDING" in issue for issue in report.issues))

    def test_uppercase_indonesian_phrase_is_still_rejected(self):
        report = qg.validate_translation(
            "TIDAK BOLEH masuk.",
            "不得（TIDAK BOLEH）進入。",
            "id",
            "zh",
        )
        self.assertFalse(report.ok)
        self.assertIn("untranslated_source_word:TIDAK", report.hard_issues)
        self.assertIn("untranslated_source_word:BOLEH", report.hard_issues)


if __name__ == "__main__":
    unittest.main()
