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

    def test_noncritical_invalid_candidate_gets_source_grounded_review(self):
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
        self.assertEqual(result["text"], "不得進入。")
        self.assertTrue(result["reviewed"])
        prompt = client.calls[0]["messages"][0]["content"]
        self.assertIn("No ordinary source-language word may remain untranslated", prompt)

    def test_critical_document_retries_from_source_after_language_leak(self):
        client = FakeClient(["不BOLEH使用工具。", "不得使用工具。"])
        result = qg.translate_quality_critical_document(
            "TIDAK BOLEH menggunakan alat.",
            "id",
            "zh",
            model="fake-model",
            ai_client=client,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["text"], "不得使用工具。")
        self.assertEqual(result["path"], "protected_fresh_retry")
        self.assertEqual(len(client.calls), 2)


if __name__ == "__main__":
    unittest.main()
