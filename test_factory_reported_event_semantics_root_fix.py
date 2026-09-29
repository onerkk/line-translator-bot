import unittest

import factory_reported_event_semantics as reported_events
import factory_translation_guard as guard
import factory_translation_policy as policy
import translation_quality_gate as quality_gate


SCREENSHOT_SOURCE = (
    "這個是之前異型站A班沒打PMI那件。\n"
    "阿堂反應異常在廠內發現後，已包裝那捆D班處理完再出貨，"
    "結果裡面圓標籤沒換。\n"
    "記得重新入庫的料標籤要換，木箱或包在包材裡面標籤有可能不同也要拆開處理。"
)
OLD_SCREENSHOT_TRANSLATION = (
    "Ini adalah kasus sebelumnya di Stasiun packing barang bentuk khusus shift A "
    "yang tidak melakukan PMI.\n\n"
    "阿堂 melaporkan masalah. Setelah ditemukan di pabrik, bundel yang sudah "
    "dikemas ditangani oleh shift D lalu dikirim. Ternyata label bulat di "
    "dalamnya tidak diganti.\n\n"
    "Ingat, label material yang masuk gudang kembali harus diganti. Jika label "
    "di dalam peti kayu atau di dalam bahan kemasan mungkin berbeda, harus "
    "dibuka dan ditangani terpisah."
)
CORRECTED_SCREENSHOT_TRANSLATION = (
    "Ini adalah kasus sebelumnya di Stasiun packing barang bentuk khusus saat "
    "shift A tidak melakukan pemeriksaan PMI.\n\n"
    "阿堂 menemukan kelainan tersebut di pabrik lalu melaporkannya. Bundel yang "
    "sudah dikemas itu baru dikirim setelah selesai ditangani oleh shift D. "
    "Ternyata label bulat di bagian dalam bundel itu tidak diganti.\n\n"
    "Ingat, label material yang dimasukkan kembali ke gudang harus diganti. "
    "Label pada material di dalam peti kayu atau yang dibungkus dengan bahan "
    "kemasan mungkin berbeda, jadi peti atau kemasannya harus dibuka dan "
    "materialnya ditangani secara terpisah."
)


class FactoryReportedEventSemanticsRootFixTests(unittest.TestCase):
    def test_screenshot_relation_keeps_reporter_and_discovery_actor(self):
        frame = reported_events.build_frame(SCREENSHOT_SOURCE, "zh", "id")
        self.assertTrue(frame["active"])
        self.assertEqual(frame["facts"][0]["reporter"], "阿堂")
        self.assertEqual(frame["facts"][0]["finder"], "阿堂")
        self.assertEqual(frame["facts"][0]["agent_resolution"], "topic_continuity")

    def test_agentless_discovery_is_rejected_at_both_shared_acceptance_gates(self):
        frame = reported_events.build_frame(SCREENSHOT_SOURCE, "zh", "id")
        self.assertIn(
            "reported_event:discovery_actor_missing",
            reported_events.validate_translation(frame, OLD_SCREENSHOT_TRANSLATION),
        )

        quality = quality_gate.validate_translation(
            SCREENSHOT_SOURCE, OLD_SCREENSHOT_TRANSLATION, "zh", "id"
        )
        self.assertIn("reported_event:discovery_actor_missing", quality.issues)

        guarded = guard.validate_translation(SCREENSHOT_SOURCE, OLD_SCREENSHOT_TRANSLATION, "zh", "id")
        self.assertTrue(any(
            "reported_event:discovery_actor_missing" in issue
            for issue in guarded.hard_issues
        ))

    def test_corrected_translation_and_both_prompts_preserve_the_event_agent(self):
        frame = reported_events.build_frame(SCREENSHOT_SOURCE, "zh", "id")
        self.assertEqual(
            reported_events.validate_translation(frame, CORRECTED_SCREENSHOT_TRANSLATION),
            [],
        )
        quality = quality_gate.validate_translation(
            SCREENSHOT_SOURCE, CORRECTED_SCREENSHOT_TRANSLATION, "zh", "id"
        )
        self.assertTrue(quality.ok, quality.issues)
        self.assertNotIn("reported_event:discovery_actor_missing", quality.issues)
        guarded = guard.validate_translation(
            SCREENSHOT_SOURCE, CORRECTED_SCREENSHOT_TRANSLATION, "zh", "id"
        )
        self.assertTrue(guarded.ok, guarded.hard_issues)
        for prompt in (
            policy.build_prompt(SCREENSHOT_SOURCE, "zh", "id"),
            guard.build_prompt(SCREENSHOT_SOURCE, "zh", "id"),
            reported_events.build_prompt(SCREENSHOT_SOURCE, "zh", "id"),
        ):
            self.assertIn("discovery", prompt)
            self.assertIn("agentless passive", prompt)

    def test_role_frame_generalizes_to_other_people_and_locations(self):
        source = "王師傅回報標籤問題是在包裝區發現的，隨後交由夜班處理。"
        target = (
            "王師傅 menemukan masalah label tersebut di area pengemasan dan "
            "melaporkannya. Setelah itu, masalah tersebut ditangani oleh shift malam."
        )
        frame = reported_events.build_frame(source, "zh", "id")
        self.assertEqual(frame["facts"][0]["reporter"], "王師傅")
        self.assertEqual(frame["facts"][0]["finder"], "王師傅")
        self.assertEqual(reported_events.validate_translation(frame, target), [])

        bad = (
            "王師傅 melaporkan masalah label yang ditemukan di area pengemasan. "
            "Setelah itu, masalah tersebut ditangani oleh shift malam."
        )
        self.assertIn(
            "reported_event:discovery_actor_missing",
            reported_events.validate_translation(frame, bad),
        )
        pronoun_target = (
            "王師傅 melaporkan masalah label yang dia temukan di area pengemasan. "
            "Setelah itu, masalah tersebut ditangani oleh shift malam."
        )
        self.assertEqual(reported_events.validate_translation(frame, pronoun_target), [])

    def test_explicitly_named_different_finder_is_not_replaced_by_reporter(self):
        source = "阿堂回報異常是阿明在廠內發現。"
        target = "阿堂 melaporkan kelainan yang ditemukan oleh 阿明 di pabrik."
        frame = reported_events.build_frame(source, "zh", "id")
        self.assertEqual(frame["facts"][0]["reporter"], "阿堂")
        self.assertEqual(frame["facts"][0]["finder"], "阿明")
        self.assertEqual(reported_events.validate_translation(frame, target), [])

    def test_report_without_an_embedded_discovery_does_not_trigger_this_rule(self):
        frame = reported_events.build_frame(
            "阿堂回報標籤需要更換，等包裝結束後再處理。", "zh", "id"
        )
        self.assertFalse(frame["active"])


if __name__ == "__main__":
    unittest.main()
