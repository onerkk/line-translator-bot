import pathlib
import unittest

import factory_quantity_semantics as fqs
import factory_translation_guard as ftg
import translation_quality_gate as tqg


class FactoryQuantitySemanticsRootFixTests(unittest.TestCase):
    def test_current_distribution_message_is_parsed_as_atoms_and_addition(self):
        frame = fqs.build_frame("下班前記得領手套，一人一包又6雙", "zh", "id")
        self.assertTrue(frame["active"])
        self.assertTrue(frame["distributive"])
        self.assertEqual(
            [(a["value"], a["classifier"], a["canonical_id"]) for a in frame["atoms"]],
            [("1", "包", "bungkus"), ("6", "雙", "pasang")],
        )
        self.assertEqual(frame["relations"][0]["relation"], "addition")

    def test_current_good_translation_passes(self):
        source = "下班前記得領手套，一人一包又6雙"
        target = (
            "Sebelum pulang kerja, ingat ambil sarung tangan. "
            "Setiap orang mendapat satu bungkus ditambah 6 pasang."
        )
        ok, issues = fqs.validate_translation(fqs.build_frame(source), target)
        self.assertTrue(ok, issues)

    def test_plain_dan_does_not_satisfy_addition(self):
        source = "每人領一包又六雙"
        bad = "Setiap orang mengambil satu bungkus dan enam pasang."
        ok, issues = fqs.validate_translation(fqs.build_frame(source), bad)
        self.assertFalse(ok)
        self.assertTrue(any("addition_marker_missing" in issue for issue in issues), issues)

    def test_package_cannot_be_translated_as_material_bundle(self):
        source = "@All 不是兩包，是一包半"
        bad = "@All bukan dua bundel, tetapi satu setengah bundel."
        ok, issues = fqs.validate_translation(fqs.build_frame(source), bad)
        self.assertFalse(ok)
        self.assertIn("quantity_semantics:package_mistranslated_as_bundle", issues)
        self.assertTrue(any("atom_missing:q1:2:bungkus" in issue for issue in issues), issues)
        self.assertTrue(any("atom_missing:q2:1.5:bungkus" in issue for issue in issues), issues)

    def test_correction_and_half_quantity_pass_with_words_or_decimal(self):
        source = "更正：不是三箱，而是兩箱半"
        for target in (
            "Koreksi: bukan tiga kotak, melainkan dua setengah kotak.",
            "Koreksi: bukan 3 kotak, tetapi 2,5 kotak.",
        ):
            with self.subTest(target=target):
                ok, issues = fqs.validate_translation(fqs.build_frame(source), target)
                self.assertTrue(ok, issues)

    def test_half_prefix_and_added_pairs_generalize(self):
        source = "每位同仁領半包口罩，再加2雙手套"
        good = "Setiap orang mengambil setengah bungkus masker, ditambah dua pasang sarung tangan."
        bad = "Setiap orang mengambil setengah bundel masker dan dua sarung tangan."
        self.assertTrue(fqs.validate_translation(fqs.build_frame(source), good)[0])
        self.assertFalse(fqs.validate_translation(fqs.build_frame(source), bad)[0])

    def test_pack_is_a_verb_in_pack_two_bundles(self):
        frame = fqs.build_frame("今天包2把，明天再包3把", "zh", "id")
        self.assertTrue(frame["active"])
        self.assertEqual([a["classifier"] for a in frame["atoms"]], ["把", "把"])
        self.assertFalse(any(a["classifier"] == "包" for a in frame["atoms"]))
        good = "Hari ini packing dua bundel, besok packing tiga bundel lagi."
        self.assertTrue(fqs.validate_translation(frame, good)[0])

    def test_each_bundle_is_not_forced_to_satu_bundel(self):
        frame = fqs.build_frame("每一把都要確認TAG", "zh", "id")
        self.assertEqual(frame["atoms"][0]["quantifier"], "each")
        self.assertTrue(fqs.validate_translation(frame, "TAG setiap bundel harus diperiksa.")[0])
        self.assertFalse(fqs.validate_translation(frame, "TAG satu bundel harus diperiksa.")[0])

    def test_quality_gate_rejects_classifier_and_relation_loss(self):
        source = "下班前記得領手套，一人一包又6雙"
        bad = "Sebelum pulang kerja, ingat ambil sarung tangan. Satu orang satu bundel dan 6 pasang."
        result = tqg.validate_translation(source, bad, "zh", "id")
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.startswith("quantity_semantics:") for issue in result.hard_issues), result.issues)

    def test_factory_guard_is_fail_closed_for_same_semantic_family(self):
        source = "@All 不是兩包，是一包半"
        good = "@All bukan dua bungkus, melainkan satu setengah bungkus."
        bad = "@All bukan dua bundel, tetapi satu setengah bundel."
        self.assertTrue(ftg.validate_translation(source, good, "zh", "id").ok)
        report = ftg.validate_translation(source, bad, "zh", "id")
        self.assertFalse(report.ok)
        self.assertTrue(any("quantity_semantics" in issue for issue in report.hard_issues), report.hard_issues)

    def test_prompt_is_frame_based_not_sentence_replacement(self):
        prompt = fqs.build_prompt(fqs.build_frame("每人半包再加四雙"))
        self.assertIn("compositional quantity frame", prompt)
        self.assertIn("value=0.5", prompt)
        self.assertIn("classifier=包", prompt)
        self.assertIn("addition", prompt)
        self.assertNotIn("Sebelum pulang kerja", prompt)

    def test_app_deployment_contract_and_runtime_wiring_are_present(self):
        source = pathlib.Path("app.py").read_text(encoding="utf-8")
        self.assertIn("factory_quantity_semantics as factory_quantity_semantics_module", source)
        self.assertIn('"sense": "factory_quantity_semantics"', source)
        self.assertIn("factory_quantity_semantics_module.build_prompt", source)
        self.assertIn("factory_quantity_semantics_module.validate_translation", source)
        self.assertIn("2026-08-14.1-headwear-classifier", source)


if __name__ == "__main__":
    unittest.main()
