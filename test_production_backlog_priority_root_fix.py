import time
import unittest

import factory_message_semantics as semantics


SOURCE = (
    "@All 拋光小棒這兩個月來不及出貨的遞延料很多，"
    "系統上藍底備註跟交期6、7月的料優先生產"
)
EXPECTED = (
    "@All Dalam dua bulan terakhir, banyak material batang berukuran kecil "
    "untuk proses polishing yang tertunda karena tidak sempat dikirim tepat waktu. "
    "Prioritaskan produksi material yang catatannya berlatar biru di sistem serta "
    "material dengan jadwal pengiriman bulan Juni dan Juli."
)
SCREENSHOT_BAD = (
    "@All Material tunda batang kecil polishing yang belum sempat dikirim dalam dua "
    "bulan ini banyak. Prioritaskan produksi material dengan catatan latar biru di "
    "sistem dan tanggal pengiriman bulan 6 dan 7."
)


class ProductionBacklogPriorityTests(unittest.TestCase):
    def test_reported_message_is_source_first_and_natural(self):
        frame = semantics.build_frame(SOURCE, "zh", "id")
        self.assertTrue(frame["active"])
        self.assertTrue(frame["complete"])
        self.assertEqual(frame["unparsed"], "")
        self.assertEqual(frame["slots"]["process_id"], "polishing")
        self.assertEqual(frame["slots"]["backlog_period_count"], 2)
        self.assertEqual(frame["slots"]["delivery_months"], [6, 7])
        self.assertEqual(
            semantics.translate_source_directly(SOURCE, "zh", "id"), EXPECTED
        )
        self.assertEqual(semantics.validate_translation(frame, EXPECTED), (True, []))

    def test_screenshot_translation_is_rejected_for_semantics_and_register(self):
        frame = semantics.build_frame(SOURCE, "zh", "id")
        ok, issues = semantics.validate_translation(frame, SCREENSHOT_BAD)
        self.assertFalse(ok)
        self.assertIn(
            "factory_message_semantics:priority_groups_collapsed", issues
        )
        self.assertIn(
            "factory_message_semantics:unnatural_indonesian_compound", issues
        )

    def test_paraphrase_extracts_current_process_period_and_months(self):
        source = (
            "@All 研磨小尺寸棒材最近三個月未能如期出貨的延遲材料不少，"
            "請優先排產系統中藍色標記備註及交期8月、9月、10月的材料"
        )
        expected = (
            "@All Dalam tiga bulan terakhir, banyak material batang berukuran kecil "
            "untuk proses grinding yang tertunda karena tidak sempat dikirim tepat waktu. "
            "Prioritaskan produksi material yang catatannya berlatar biru di sistem serta "
            "material dengan jadwal pengiriman bulan Agustus, September, dan Oktober."
        )
        frame = semantics.build_frame(source, "zh-TW", "id-ID")
        self.assertTrue(frame["complete"])
        self.assertEqual(frame["slots"]["delivery_months"], [8, 9, 10])
        self.assertEqual(
            semantics.translate_source_directly(source, "zh-TW", "id-ID"),
            expected,
        )

    def test_simplified_variant_is_supported_without_sentence_lookup(self):
        source = (
            "@All 抛光小棒近2个月来不及出货的递延料很多，"
            "系统中蓝底备注和交期11、12月的材料优先生产"
        )
        result = semantics.translate_source_directly(source, "zh-CN", "id")
        self.assertIn("dua bulan terakhir", result)
        self.assertIn("November dan Desember", result)
        self.assertIn("serta material", result)

    def test_unparsed_extra_instruction_never_gets_dropped(self):
        source = SOURCE + "，明天I5停機保養"
        frame = semantics.build_frame(source, "zh", "id")
        self.assertTrue(frame["active"])
        self.assertFalse(frame["complete"])
        self.assertIn("明天I5停機保養", frame["unparsed"])
        self.assertEqual(
            semantics.translate_source_directly(source, "zh", "id"), ""
        )

    def test_unrelated_partial_phrases_do_not_activate_specialized_route(self):
        controls = (
            "拋光小棒今天很多，系統藍底備註請確認。",
            "這兩個月來不及出貨的遞延料很多，請大家加班。",
            "交期6、7月的料優先生產。",
        )
        for source in controls:
            with self.subTest(source=source):
                self.assertFalse(
                    semantics.build_frame(source, "zh", "id")["active"]
                )

    def test_direct_route_has_no_network_wait(self):
        # A generous ceiling catches accidental provider/sleep calls without
        # making the test dependent on micro-benchmark noise.
        started = time.perf_counter()
        for _ in range(200):
            self.assertEqual(
                semantics.translate_source_directly(SOURCE, "zh", "id"),
                EXPECTED,
            )
        self.assertLess(time.perf_counter() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
