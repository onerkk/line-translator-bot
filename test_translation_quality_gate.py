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

    def test_noncritical_invalid_candidate_gets_one_targeted_repair_call(self):
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
        self.assertFalse(result["degraded"])
        self.assertTrue(result["cacheable"])
        self.assertTrue(result["reviewed"])
        self.assertEqual(result["path"], "independent_source_review_passed")
        self.assertEqual(len(client.calls), 1)

    def test_required_source_review_outage_cannot_veto_clean_candidate(self):
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
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["text"], "Mohon pastikan material sudah selesai dikemas.")
        self.assertTrue(result["review_requested"])
        self.assertFalse(result["review_succeeded"])
        self.assertTrue(result["degraded"])
        self.assertFalse(result["cacheable"])
        self.assertEqual(result["path"], "review_unavailable_original_kept")

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

    def test_repeated_document_label_and_parenthetical_alias_are_not_false_leakage(self):
        source = """@budi santoso 山多 @Irwan 布納萬 @伊努滿 Sumertha @迪弟 kampret @Hasim

📢 PEMBERITAHUAN PENTING – STANDAR PEMASANGAN TAG

Mulai saat ini, seluruh operator WAJIB memasang TAG sesuai dengan ketentuan dari pihak manajemen.

Ketentuan pemasangan TAG:

1. Mesin Grinding dan Polishing
    * TAG harus dipasang pada barang pertama yang keluar dari mesin.
    * TAG juga harus dipasang pada barang terakhir dari proses produksi.
    * Ketentuan ini berlaku untuk seluruh mesin Grinding dan Polishing tanpa pengecualian.
2. Cleaning Station
    * Work Order dan TAG wajib dijepit pada tali crane menggunakan penjepit (clip) yang telah disediakan.
    * Dilarang meletakkan Work Order atau TAG di sembarang tempat.
    * Apabila penjepit hilang atau rusak, segera minta penggantinya kepada Ketua Regu agar standar kerja tetap terjaga.

Mohon seluruh rekan kerja menjalankan ketentuan ini dengan disiplin. Hal-hal yang terlihat sederhana seperti pemasangan TAG sangat berpengaruh terhadap ketertelusuran produk, kelancaran proses produksi, dan hasil audit. Jangan menunggu ditegur atau terjadi masalah terlebih dahulu. Mari bersama-sama menjaga standar kerja yang telah ditetapkan oleh manajemen."""
        candidate = """@budi santoso 山多 @Irwan 布納萬 @伊努滿 Sumertha @迪弟 kampret @Hasim

📢 重要通知－TAG 安裝標準

從現在起，所有操作員都必須依照管理階層的規定安裝 TAG。

TAG 安裝規定：

1. Grinding 與 Polishing 機台
    * TAG 必須裝在機台產出的第一件產品上。
    * TAG 也必須裝在生產流程的最後一件產品上。
    * 此規定適用於所有 Grinding 與 Polishing 機台，沒有例外。
2. Cleaning Station
    * Work Order 與 TAG 必須使用已提供的夾具（clip）夾在天車繩索上。
    * 禁止將 Work Order 或 TAG 隨意放置。
    * 夾具遺失或損壞時，請立即向班長申請更換，以維持作業標準。

請所有同仁確實遵守這項規定。安裝 TAG 看似簡單，卻會直接影響產品追溯、製程順暢與稽核結果。不要等到被提醒或發生問題才處理。請大家共同維護管理階層所制定的作業標準。"""
        envelope = qg.inspect_immutable_spans(source)

        self.assertIn("TAG", envelope.mapping.values())
        report = qg.validate_translation(
            source,
            candidate,
            "id",
            "zh",
            immutable_literals=envelope.mapping.values(),
            require_paragraph_fidelity=True,
        )

        self.assertTrue(report.ok, report.issues)
        self.assertNotIn("untranslated_source_word:TAG", report.issues)
        self.assertNotIn("untranslated_source_word:clip", report.issues)

    def test_document_label_inference_does_not_relax_common_source_words(self):
        report = qg.validate_translation(
            "TIDAK BOLEH masuk. BOLEH hanya dengan izin.",
            "不BOLEH進入，只有獲准才BOLEH。",
            "id",
            "zh",
        )

        self.assertFalse(report.ok)
        self.assertIn("untranslated_source_word:BOLEH", report.hard_issues)

    def test_repeated_document_label_is_tokenized_without_trailing_punctuation(self):
        source = "Harap pasang LOTX. LOTX: wajib terlihat."
        candidate = "請安裝 LOTX，並確保 LOTX 清楚可見。"
        envelope = qg.inspect_immutable_spans(source)

        self.assertEqual(
            [value for value in envelope.mapping.values() if value == "LOTX"],
            ["LOTX", "LOTX"],
        )
        report = qg.validate_translation(
            source,
            candidate,
            "id",
            "zh",
            immutable_literals=envelope.mapping.values(),
            require_paragraph_fidelity=True,
        )
        self.assertTrue(report.ok, report.issues)

    def test_document_label_scanner_never_matches_inside_pipeline_placeholders(self):
        source = "__QG_KEEP_000_ABCDEF12__ Harap pasang LOTX. LOTX wajib terlihat."

        self.assertEqual(qg._document_defined_uppercase_labels(source), ["LOTX"])

    def test_single_factory_tag_label_is_a_stable_technical_literal(self):
        source = "Harap pasang TAG pada barang pertama."
        candidate = "請將 TAG 裝在第一件產品上。"
        envelope = qg.inspect_immutable_spans(source)
        report = qg.validate_translation(
            source,
            candidate,
            "id",
            "zh",
            immutable_literals=envelope.mapping.values(),
        )

        self.assertIn("TAG", envelope.mapping.values())
        self.assertTrue(report.ok, report.issues)

    def test_critical_document_does_not_spend_second_api_call_after_local_rejection(self):
        client = FakeClient(["不BOLEH使用工具。", "不得使用工具。"])
        result = qg.translate_quality_critical_document(
            "TIDAK BOLEH menggunakan alat.",
            "id",
            "zh",
            model="fake-model",
            ai_client=client,
        )
        # A one-call document path may defer delivery, but must never report a
        # known mixed-language error as successful merely to avoid a retry.
        self.assertFalse(result["ok"], result)
        self.assertIsNone(result["text"])
        self.assertIn("untranslated_source_word:BOLEH", result["hard_issues"])
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
