import ast
import json
import unittest
from pathlib import Path

import factory_knowledge
import factory_message_semantics as semantics
import factory_translation_guard as guard
import glossary_enforcement
import translation_quality_gate as quality_gate


ROOT = Path(__file__).resolve().parent

WEIGHT_DIFFERENCE_SOURCE = (
    "Kg di layar monitor dengan di timbangan katrol selisih 6 kg. "
    "Saya laporan dengan id Ketu kelas"
)
WEIGHT_DIFFERENCE_TARGET = (
    "螢幕顯示的重量與天車電子磅秤相差 6 公斤。我用班長的 ID 回報。"
)
READINGS_SOURCE = "Di layar monitor 995 kg sedangkan di timbangan katrol 989 kg"
READINGS_TARGET = "螢幕顯示 995 公斤，而天車電子磅秤顯示 989 公斤。"
MOVEMENT_SOURCE = "我過去了了解看看"
MOVEMENT_TARGET = "Saya ke sana dulu untuk mengecek situasinya."


class BidirectionalSourceRelationAccuracyRootFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        guard.reload()
        cls.knowledge = factory_knowledge.FactoryKnowledgeStore(
            ROOT / "factory_knowledge.json"
        )

    def test_semantic_module_self_test_and_locale_aliases(self):
        self.assertTrue(semantics.health()["self_test"]["ok"])
        self.assertEqual(
            semantics.translate_source_directly(
                WEIGHT_DIFFERENCE_SOURCE, "id-ID", "zh-TW"
            ),
            WEIGHT_DIFFERENCE_TARGET,
        )
        self.assertEqual(
            semantics.translate_source_directly(MOVEMENT_SOURCE, "zh-Hant", "id-ID"),
            MOVEMENT_TARGET,
        )

    def test_screenshot_weight_difference_preserves_linked_meaning(self):
        frame = semantics.build_frame(WEIGHT_DIFFERENCE_SOURCE, "id", "zh")
        self.assertTrue(frame["active"])
        self.assertTrue(frame["complete"])
        self.assertEqual(frame["slots"]["difference"], "6")
        self.assertEqual(frame["slots"]["leader_term"], "ketu kelas")
        self.assertTrue(frame["slots"]["leader_id_relation"])
        self.assertEqual(semantics.deterministic_translation(frame), WEIGHT_DIFFERENCE_TARGET)

        bad = "螢幕上的公斤數與滑輪秤相差 6 kg。我已用 Ketu kelas 的 ID 回報。"
        ok, issues = semantics.validate_translation(frame, bad)
        self.assertFalse(ok)
        self.assertIn(
            "factory_message_semantics:overhead_crane_scale_term_missing", issues
        )
        self.assertIn("factory_message_semantics:leader_id_relation_missing", issues)

    def test_screenshot_readings_remain_attached_to_their_devices(self):
        frame = semantics.build_frame(READINGS_SOURCE, "id", "zh")
        self.assertEqual(frame["slots"]["monitor_weight"], "995")
        self.assertEqual(frame["slots"]["scale_weight"], "989")
        self.assertEqual(semantics.deterministic_translation(frame), READINGS_TARGET)

        swapped = "螢幕顯示 989 公斤，而天車電子磅秤顯示 995 公斤。"
        ok, issues = semantics.validate_translation(frame, swapped)
        self.assertFalse(ok)
        self.assertIn(
            "factory_message_semantics:weight_readings_attached_to_wrong_devices",
            issues,
        )

    def test_reading_parser_supports_both_word_orders_and_current_values(self):
        before_device = "995 kg di layar monitor, 989 kg di timbangan gantung"
        self.assertEqual(
            semantics.translate_source_directly(before_device, "id", "zh"),
            READINGS_TARGET,
        )

        paraphrase = (
            "Monitor menunjukkan 1000 kg, sedangkan timbangan gantung elektronik "
            "994 kg. Saya sudah lapor pakai ID ketua regu."
        )
        self.assertEqual(
            semantics.translate_source_directly(paraphrase, "id", "zh"),
            "螢幕顯示 1000 公斤，而天車電子磅秤顯示 994 公斤。我已用班長的 ID 回報。",
        )

    def test_direct_route_never_drops_an_unparsed_extra_clause(self):
        source = READINGS_SOURCE + ". Besok mesin dihentikan."
        frame = semantics.build_frame(source, "id", "zh")
        self.assertTrue(frame["active"])
        self.assertFalse(frame["complete"])
        self.assertEqual(frame["unparsed"], "besok mesin dihentikan")
        self.assertEqual(semantics.translate_source_directly(source, "id", "zh"), "")

        extra_number = READINGS_SOURCE + " 77"
        self.assertEqual(
            semantics.build_frame(extra_number, "id", "zh")["unparsed"], "77"
        )
        self.assertEqual(
            semantics.translate_source_directly(extra_number, "id", "zh"), ""
        )

    def test_decimal_measurements_are_not_split_as_sentence_punctuation(self):
        source = (
            "Di layar monitor 995,5 kg sedangkan di timbangan katrol 989,25 kg"
        )
        frame = semantics.build_frame(source, "id", "zh")
        self.assertEqual(frame["slots"]["monitor_weight"], "995,5")
        self.assertEqual(frame["slots"]["scale_weight"], "989,25")
        self.assertEqual(
            semantics.deterministic_translation(frame),
            "螢幕顯示 995,5 公斤，而天車電子磅秤顯示 989,25 公斤。",
        )

    def test_movement_and_inspection_are_both_preserved(self):
        frame = semantics.build_frame(MOVEMENT_SOURCE, "zh", "id")
        self.assertTrue(frame["active"])
        self.assertTrue(frame["complete"])
        self.assertEqual(semantics.deterministic_translation(frame), MOVEMENT_TARGET)

        ok, issues = semantics.validate_translation(frame, "Saya lihat dulu situasinya.")
        self.assertFalse(ok)
        self.assertIn("factory_message_semantics:movement_to_location_missing", issues)

    def test_movement_paraphrase_preserves_destination_and_object(self):
        source = "@All 我先到現場確認一下機台狀況"
        target = "@All Saya ke lokasi dulu untuk memeriksa kondisi mesin."
        self.assertEqual(
            semantics.translate_source_directly(source, "zh-TW", "id-ID"), target
        )
        frame = semantics.build_frame(source, "zh", "id")
        self.assertEqual(frame["slots"]["destination"], "location")
        self.assertEqual(frame["slots"]["object"], "machine")

    def test_movement_frame_preserves_aspect_modality_and_later_timing(self):
        cases = (
            (
                "我會過去了解看看",
                "Saya akan pergi ke sana terlebih dahulu untuk mengecek situasinya.",
            ),
            (
                "我再過去了解看看",
                "Nanti saya akan pergi ke sana untuk mengecek situasinya.",
            ),
            (
                "我已經到現場確認機台狀況",
                "Saya sudah pergi ke lokasi untuk memeriksa kondisi mesin.",
            ),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(
                    semantics.translate_source_directly(source, "zh", "id"), expected
                )

        future = semantics.build_frame("我會過去了解看看", "zh", "id")
        ok, issues = semantics.validate_translation(
            future, "Saya pergi ke sana untuk mengecek situasinya."
        )
        self.assertFalse(ok)
        self.assertIn("factory_message_semantics:future_modality_missing", issues)

    def test_context_controls_do_not_reinterpret_unrelated_language(self):
        controls = (
            ("Saya ketua kelas di sekolah.", "id", "zh"),
            ("Katrol rusak.", "id", "zh"),
            ("我先看看情況。", "zh", "id"),
            ("我過去拿工具。", "zh", "id"),
        )
        for source, src, tgt in controls:
            with self.subTest(source=source):
                self.assertFalse(semantics.build_frame(source, src, tgt)["active"])
                self.assertEqual(
                    semantics.translate_source_directly(source, src, tgt), ""
                )

    def test_quality_gate_checks_relations_in_both_directions(self):
        good = quality_gate.validate_translation(
            WEIGHT_DIFFERENCE_SOURCE, WEIGHT_DIFFERENCE_TARGET, "id", "zh"
        )
        self.assertTrue(good.ok, good.issues)
        bad = quality_gate.validate_translation(
            READINGS_SOURCE,
            "螢幕顯示 989 公斤，而天車電子磅秤顯示 995 公斤。",
            "id",
            "zh",
        )
        self.assertFalse(bad.ok)
        self.assertIn(
            "factory_message_semantics:weight_readings_attached_to_wrong_devices",
            bad.issues,
        )

        movement_bad = quality_gate.validate_translation(
            MOVEMENT_SOURCE, "Saya lihat dulu situasinya.", "zh", "id"
        )
        self.assertFalse(movement_bad.ok)
        self.assertIn(
            "factory_message_semantics:movement_to_location_missing",
            movement_bad.issues,
        )

    def test_unified_guard_accepts_verified_and_rejects_semantic_loss(self):
        for source, target, src, tgt in (
            (WEIGHT_DIFFERENCE_SOURCE, WEIGHT_DIFFERENCE_TARGET, "id", "zh"),
            (READINGS_SOURCE, READINGS_TARGET, "id", "zh"),
            (MOVEMENT_SOURCE, MOVEMENT_TARGET, "zh", "id"),
        ):
            with self.subTest(source=source):
                report = guard.validate_translation(source, target, src, tgt)
                self.assertTrue(report.ok, report.issues)
                self.assertEqual(guard.exact_verified_target(source, src, tgt), target)

        self.assertFalse(
            guard.validate_translation(
                MOVEMENT_SOURCE, "Saya lihat dulu situasinya.", "zh", "id"
            ).ok
        )

    def test_knowledge_retrieval_is_paraphrase_based_not_exact_only(self):
        probes = (
            (
                "Monitor menunjukkan 1000 kg dan timbangan crane 994 kg",
                "id",
                "zh",
                "monitor_overhead_crane_scale_weight_relation",
            ),
            (
                "我先去那裡檢查一下材料狀況",
                "zh",
                "id",
                "movement_then_on_site_inspection_relation",
            ),
        )
        for source, src, tgt, expected_id in probes:
            with self.subTest(source=source):
                cards = self.knowledge.retrieve(source, src, tgt, limit=10)
                self.assertIn(expected_id, {card["id"] for card in cards})

    def test_factory_glossary_has_one_canonical_crane_scale_concept(self):
        glossary = json.loads((ROOT / "glossary_data.json").read_text(encoding="utf-8"))
        row = glossary["天車電子磅秤"]
        self.assertEqual(row["canonical_idn"], "timbangan gantung elektronik")
        self.assertTrue(row["reverse_safe"])
        self.assertIn("電子磅秤", row["aliases_zh"])
        self.assertIn("timbangan katrol", row["aliases_id"])
        reverse = glossary_enforcement.build_safe_reverse_index(glossary)
        self.assertEqual(reverse["timbangan katrol"]["target_term"], "天車電子磅秤")

    def test_embedded_glossary_and_external_glossary_are_identical(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        embedded = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_GLOSSARY_JSON"
                for target in node.targets
            ):
                embedded = json.loads(ast.literal_eval(node.value))
                break
        external = json.loads((ROOT / "glossary_data.json").read_text(encoding="utf-8"))
        self.assertEqual(embedded, external)

    def test_app_source_first_route_precedes_provider_and_mentions(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        start = source.index("def translate(text, src, tgt):")
        end = source.index("def ", start + len("def translate(text, src, tgt):"))
        body = source[start:end]
        direct = body.index("factory_message_semantics_module.translate_source_directly")
        mention = body.index("protect_mentions")
        provider = body.index("_translate_core")
        self.assertLess(direct, mention)
        self.assertLess(direct, provider)
        self.assertIn('pipeline_status="source_first_relation_translation"', body)
        self.assertIn('openai_status="not_needed"', body)

    def test_regression_assets_include_all_three_exposed_failures(self):
        regression = json.loads(
            (ROOT / "factory_translation_regression.json").read_text(encoding="utf-8")
        )
        rows = {row["id"]: row for row in regression["cases"]}
        expected = {
            "monitor_overhead_crane_scale_difference_and_report": WEIGHT_DIFFERENCE_TARGET,
            "monitor_overhead_crane_scale_two_readings": READINGS_TARGET,
            "movement_then_inspection_purpose": MOVEMENT_TARGET,
        }
        for case_id, target in expected.items():
            with self.subTest(case_id=case_id):
                self.assertEqual(rows[case_id]["verified_target"], target)


if __name__ == "__main__":
    unittest.main()
