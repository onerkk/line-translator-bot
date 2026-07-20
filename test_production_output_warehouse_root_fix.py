import json
import pathlib
import unittest

import factory_knowledge
import translation_quality_gate as tqg


ROOT = pathlib.Path(__file__).resolve().parent


class ProductionOutputWarehouseRootFixTests(unittest.TestCase):
    def setUp(self):
        with (ROOT / "glossary_data.json").open(encoding="utf-8") as handle:
            self.glossary = json.load(handle)
        self.store = factory_knowledge.FactoryKnowledgeStore(
            str(ROOT / "factory_knowledge.json")
        )

    def test_equipment_output_is_production_not_material_movement(self):
        cards = self.store.retrieve(
            "目前出料狀況估計平均每天可以維持130噸。", "zh", "id", limit=5
        )
        self.assertTrue(
            any(card["id"] == "equipment_output_production_semantics" for card in cards),
            cards,
        )
        bad = "Menurut kondisi material keluar saat ini, rata-ratanya 130 ton per hari."
        ok, issues = self.store.validate_translation(cards, "目前出料狀況估計平均每天可以維持130噸。", bad)
        self.assertFalse(ok)
        self.assertTrue(any("forbidden:kondisi material keluar" in issue for issue in issues), issues)

        good = "Menurut kondisi hasil produksi mesin saat ini, rata-ratanya dapat dipertahankan pada 130 ton per hari."
        ok2, issues2 = self.store.validate_translation(cards, "目前出料狀況估計平均每天可以維持130噸。", good)
        self.assertTrue(ok2, issues2)

    def test_implicit_ton_is_inherited_for_every_metric_but_not_grade_422(self):
        source = (
            "本月入庫目標3750，到月底平均一天需147噸。\n\n"
            "目前出料狀況如果平均維持每日130～135入庫量，最後能入到3600就不錯了。"
        )
        requirements = tqg.infer_implicit_quantity_units(source, "zh", "id")
        values = {item["value"] for item in requirements}
        self.assertEqual(values, {"3750", "130-135", "3600"})
        self.assertTrue(all(item["target_unit"] == "ton" for item in requirements))

        bad = (
            "Target pemasukan gudang bulan ini 3750. Sampai akhir bulan diperlukan 147 ton per hari. "
            "Jika hasil produksi mesin tetap 130–135 per hari, total pemasukan gudang bisa mencapai 3600."
        )
        report = tqg.validate_translation(source, bad, "zh", "id")
        self.assertFalse(report.ok)
        self.assertIn("missing_inherited_unit:3750:ton", report.hard_issues)
        self.assertIn("missing_inherited_unit:130-135:ton", report.hard_issues)
        self.assertIn("missing_inherited_unit:3600:ton", report.hard_issues)

        good = (
            "Target pemasukan gudang bulan ini adalah 3.750 ton. Sampai akhir bulan, rata-rata yang dibutuhkan "
            "adalah 147 ton per hari. Jika hasil produksi mesin tetap 130–135 ton per hari, total pemasukan "
            "gudang bisa mencapai 3.600 ton."
        )
        report2 = tqg.validate_translation(source, good, "zh", "id")
        self.assertTrue(report2.ok, report2.issues)

        grade_source = "422待洗庫存量低於40噸時，S、H異型棒要協助一股清洗。"
        self.assertEqual(tqg.infer_implicit_quantity_units(grade_source, "zh", "id"), [])

    def test_operational_notice_keeps_modality_and_management_meaning(self):
        source = "研磨各站該趕的急單優先處理，該開的設備不要隨便停機，庫存注意不要暴增，這樣上面也沒話可說了。"
        cards = self.store.retrieve(source, "zh", "id", limit=5)
        self.assertTrue(
            any(card["id"] == "operational_priority_runtime_inventory_accountability" for card in cards),
            cards,
        )
        bad = (
            "Setiap stasiun grinding prioritaskan order urgent yang harus dikejar. Mesin jangan dihentikan. "
            "Perhatikan stok. Dengan begitu atasan tidak akan banyak bicara."
        )
        ok, issues = self.store.validate_translation(cards, source, bad)
        self.assertFalse(ok)
        self.assertTrue(any("forbidden:atasan tidak akan banyak bicara" in issue for issue in issues), issues)
        self.assertTrue(any("missing_urgent_work_order_semantics" in issue for issue in issues), issues)

        good = (
            "Setiap stasiun grinding harus memprioritaskan work order mendesak yang perlu dipercepat. "
            "Mesin yang seharusnya beroperasi jangan dihentikan sembarangan. Pastikan stok tidak meningkat "
            "drastis. Dengan begitu, pihak atasan juga tidak punya alasan untuk mempermasalahkannya."
        )
        ok2, issues2 = self.store.validate_translation(cards, source, good)
        self.assertTrue(ok2, issues2)

    def test_washing_is_process_and_s_h_are_profile_shapes(self):
        source = "422待洗庫存量低於40噸時，S、H異型棒要協助一股清洗。"
        cards = self.store.retrieve(source, "zh", "id", limit=5)
        self.assertTrue(
            any(card["id"] == "material_washing_support_semantics" for card in cards),
            cards,
        )
        bad = "Jika stok 422 kurang dari 40 ton, H, S, batang bentuk khusus membantu Bagian Cold Drawing 1 untuk pembersihan."
        ok, issues = self.store.validate_translation(cards, source, bad)
        self.assertFalse(ok)
        self.assertTrue(any("forbidden:untuk pembersihan" in issue for issue in issues), issues)
        self.assertTrue(any("forbidden:H, S, batang bentuk khusus" in issue for issue in issues), issues)

        good = (
            "Jika jumlah stok material 422 yang menunggu proses pencucian kurang dari 40 ton, batang profil "
            "khusus berbentuk S dan H harus membantu proses pencucian di Bagian Cold Drawing 1."
        )
        ok2, issues2 = self.store.validate_translation(cards, source, good)
        self.assertTrue(ok2, issues2)

    def test_glossary_contains_canonical_root_terms(self):
        expected = {
            "出料狀況": "kondisi hasil produksi mesin",
            "出料量": "jumlah hasil produksi mesin",
            "入庫量": "jumlah pemasukan gudang",
            "待洗庫存量": "jumlah stok material yang menunggu proses pencucian",
            "協助一股清洗": "membantu proses pencucian di Bagian Cold Drawing 1",
            "該趕的急單": "work order mendesak yang perlu dipercepat",
            "不要隨便停機": "jangan menghentikan mesin sembarangan",
            "上面也沒話可說": "pihak atasan juga tidak punya alasan untuk mempermasalahkannya",
            "S、H異型棒": "batang profil khusus berbentuk S dan H",
        }
        for source, target in expected.items():
            self.assertEqual(self.glossary[source]["canonical_idn"], target)
            self.assertEqual(self.glossary[source]["translation_mode"], "hard")

    def test_app_prompt_no_longer_forces_wrong_polysemy(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("出料狀況/出料量/每日出料/設備出料", source)
        self.assertIn("hasil produksi mesin / output produksi", source)
        self.assertNotIn("H、S異型棒=H, S, batang bentuk khusus(three separate types)", source)
        self.assertIn("batang profil khusus berbentuk S dan H", source)
        self.assertIn("implicit_quantity_unit_instruction", source)
        self.assertIn("limit=5", source)


if __name__ == "__main__":
    unittest.main()
