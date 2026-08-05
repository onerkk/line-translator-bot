import unittest

import expressive_engine
import factory_semantic_audit as audit
import factory_translation_guard as guard


SCREENSHOT_PEELING = "@辰 @Dato:潘 不用找了，在削皮"
SCREENSHOT_PACKAGING = (
    "今天也一件前後貼錯，包裝作業再互相提醒一下，帳沒來的要標記清楚，貼TAG一把一把來。\n\n"
    "我自己貼TAG的習慣會懷疑自己，多看一次多一分保險"
)
SCREENSHOT_SHIFT = (
    "目前一件重量異常，兩件TAG貼錯都是我們班，大家都很辛苦，作業多留意一下。\n\n"
    "這陣子太多件，現況一旦客訴我也幫不上忙。被懲處會很傷。"
)


class FactoryOperationalClaimFrameRootFixTests(unittest.TestCase):
    def test_does_not_depend_on_exact_sentence_fallbacks(self):
        self.assertIsNone(guard.exact_verified_target(SCREENSHOT_PEELING, "zh", "id"))
        self.assertIsNone(guard.exact_verified_target(SCREENSHOT_PACKAGING, "zh", "id"))
        self.assertIsNone(guard.exact_verified_target(SCREENSHOT_SHIFT, "zh", "id"))

    def test_peeling_location_claim_is_compositional_and_paraphrase_safe(self):
        sources = (
            SCREENSHOT_PEELING,
            "不用再找，料放在削皮區。",
            "別找了，東西在削皮站。",
        )
        good_targets = (
            "Tidak usah dicari lagi, barangnya ada di stasiun peeling.",
            "Tidak perlu dicari lagi; materialnya berada di bagian peeling.",
            "Jangan dicari lagi. Barangnya terletak di area peeling.",
        )
        for source, target in zip(sources, good_targets):
            frame = audit.build_source_frame(source, "zh", "id")
            self.assertTrue(frame["active"], source)
            claim_ids = {item["claim_id"] for item in frame["claims"]}
            self.assertIn("stop_searching", claim_ids)
            self.assertIn("peeling_station_location", claim_ids)
            self.assertTrue(audit.validate_translation(frame, target)[0], target)

        bad = "Tidak perlu dicari, sedang dikupas."
        frame = audit.build_source_frame(SCREENSHOT_PEELING, "zh", "id")
        ok, issues = audit.validate_translation(frame, bad)
        self.assertFalse(ok)
        self.assertIn("factory_semantic_audit:missing_stop_searching", issues)
        self.assertIn("factory_semantic_audit:missing_peeling_location_relation", issues)
        self.assertIn("factory_semantic_audit:peeling_location_mistranslated_as_action", issues)

    def test_packaging_claims_work_for_unseen_wording(self):
        source = (
            "包裝時發現 TAG 前後貼反；系統資料還沒進來的先做清楚記號，"
            "每把分開貼。貼完我都不太放心自己，所以會再確認一次。"
        )
        target = (
            "Saat pengemasan ditemukan TAG bagian depan dan belakang tertukar. "
            "Barang yang datanya belum masuk harus diberi tanda dengan jelas. "
            "Tempel setiap bundel satu per satu. Setelah selesai, saya memeriksanya sekali lagi karena saya kurang yakin dengan hasil tempelan sendiri."
        )
        frame = audit.build_source_frame(source, "zh", "id")
        self.assertTrue(frame["active"])
        expected = {
            "front_rear_tag_swap",
            "pending_system_record",
            "clear_marking",
            "bundle_by_bundle",
            "self_result_double_check",
        }
        self.assertTrue(expected.issubset({item["claim_id"] for item in frame["claims"]}))
        self.assertTrue(audit.validate_translation(frame, target)[0])

        bad = (
            "Saat pengemasan label depan dan belakang tertukar. "
            "Yang belum ada catatan harus diberi tanda dengan jelas. "
            "Tempel semuanya sekaligus. Saya selalu mengecek diri sendiri."
        )
        ok, issues = audit.validate_translation(frame, bad)
        self.assertFalse(ok)
        joined = "\n".join(issues)
        self.assertIn("front_rear_tag_relation_missing", joined)
        self.assertIn("pending_record_mistranslated_as_note", joined)
        self.assertIn("bundle_by_bundle_missing", joined)
        self.assertIn("self_check_mistranslated_as_checking_person", joined)

    def test_quality_accountability_preserves_counts_and_consequences(self):
        source = (
            "現在有1件重量不正常，2件TAG貼錯，全部是本班的。"
            "最近同類異常很多；若客戶投訴，我沒辦法幫忙，受到處分後果很嚴重。"
        )
        target = (
            "Sekarang ada 1 barang yang beratnya tidak normal dan 2 barang dengan TAG salah ditempel; "
            "semuanya berasal dari shift kita. Belakangan ini kasus serupa sudah banyak. "
            "Jika ada komplain pelanggan, saya tidak dapat membantu. Jika dikenai sanksi, dampaknya akan sangat berat."
        )
        frame = audit.build_source_frame(source, "zh", "id")
        self.assertEqual(frame["counts"]["abnormal_weight"], 1)
        self.assertEqual(frame["counts"]["wrong_tag_attachment"], 2)
        self.assertTrue(audit.validate_translation(frame, target)[0])

        wrong_counts = target.replace("2 barang", "1 barang")
        ok, issues = audit.validate_translation(frame, wrong_counts)
        self.assertFalse(ok)
        self.assertIn("factory_semantic_audit:wrong_tag_count_relation_missing", issues)

        missing_consequence = (
            "Sekarang ada satu barang dengan berat tidak normal dan dua barang dengan TAG salah ditempel; "
            "semuanya dari shift kita."
        )
        ok, issues = audit.validate_translation(frame, missing_consequence)
        self.assertFalse(ok)
        joined = "\n".join(issues)
        self.assertIn("customer_complaint_missing", joined)
        self.assertIn("speaker_cannot_help_missing", joined)
        self.assertIn("sanction_missing", joined)
        self.assertIn("severe_consequence_missing", joined)

    def test_formal_factory_mode_never_appends_new_emoji(self):
        translated = "Saat ini ada satu barang dengan berat tidak normal."
        result = expressive_engine.enhance_translation(
            SCREENSHOT_SHIFT,
            translated,
            source_language="zh",
            settings=expressive_engine.ExpressiveSettings(
                enabled=True,
                display_mode="emoji",
                emoji_enabled=True,
                images_enabled=False,
                formal_safety_enabled=True,
            ),
        )
        self.assertEqual(result.text, translated)
        self.assertEqual(result.decorated_count, 0)

        non_formal = expressive_engine.enhance_translation(
            SCREENSHOT_SHIFT,
            translated,
            source_language="zh",
            settings=expressive_engine.ExpressiveSettings(
                enabled=True,
                display_mode="emoji",
                emoji_enabled=True,
                images_enabled=False,
                formal_safety_enabled=False,
            ),
        )
        self.assertNotEqual(non_formal.text, translated)
        self.assertGreaterEqual(non_formal.decorated_count, 1)


if __name__ == "__main__":
    unittest.main()
