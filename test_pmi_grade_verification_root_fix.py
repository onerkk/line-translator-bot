import json
import unittest
from pathlib import Path

import factory_knowledge
import factory_terminology
import glossary_enforcement


ROOT = Path(__file__).resolve().parent
SOURCE = (
    "再宣導一下，每一把都一定要打鋼種，"
    "出貨這把是A班異型站包裝時嫌麻煩沒檢驗PMI就包了"
)


class PmiGradeVerificationRootFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.glossary = json.loads((ROOT / "glossary_data.json").read_text(encoding="utf-8"))
        glossary_enforcement.invalidate_glossary_cache()

    def _cards(self, source=SOURCE):
        return factory_knowledge.retrieve(source, "zh", "id", limit=5)

    def test_plant_idiom_is_retrieved_as_pmi_grade_verification(self):
        cards = self._cards()
        self.assertTrue(
            any(card.get("id") == "pmi_grade_verification_bundle_packaging" for card in cards),
            cards,
        )

    def test_good_translation_preserves_all_semantic_roles(self):
        candidate = (
            "Saya ingatkan sekali lagi, grade baja setiap bundel wajib diperiksa dengan PMI. "
            "Bundel yang sudah dikirim ini dikemas oleh shift A di Stasiun packing barang bentuk khusus "
            "tanpa pemeriksaan PMI karena mereka merasa pemeriksaan tersebut merepotkan."
        )
        ok, issues = factory_knowledge.validate_translation(self._cards(), SOURCE, candidate)
        self.assertTrue(ok, issues)

    def test_marking_or_labeling_mistranslation_is_rejected(self):
        candidate = (
            "Saya ingatkan lagi, setiap bundel wajib diberi tanda grade baja. "
            "Bundel yang sudah dikirim ini dikemas oleh shift A di Stasiun packing barang bentuk khusus."
        )
        ok, issues = factory_knowledge.validate_translation(self._cards(), SOURCE, candidate)
        self.assertFalse(ok)
        joined = " | ".join(issues)
        self.assertIn("forbidden:diberi tanda grade baja", joined)
        self.assertIn("missing_pmi_grade_verification_method", joined)

    def test_old_generic_test_wording_without_pmi_is_rejected(self):
        candidate = (
            "Saya ingatkan lagi, setiap bundel wajib diuji jenis bajanya. "
            "Bundel yang dikirim ini dikemas oleh shift A di Stasiun packing barang bentuk khusus "
            "karena dianggap merepotkan."
        )
        ok, issues = factory_knowledge.validate_translation(self._cards(), SOURCE, candidate)
        self.assertFalse(ok)
        joined = " | ".join(issues)
        self.assertIn("missing_pmi_grade_verification_method", joined)
        self.assertIn("missing_material_grade_semantics", joined)

    def test_every_bundle_and_mandatory_scope_cannot_be_dropped(self):
        candidate = (
            "Saya ingatkan lagi, beberapa bundel sebaiknya diperiksa grade bajanya dengan PMI. "
            "Bundel yang sudah dikirim ini dikemas oleh shift A di Stasiun packing barang bentuk khusus "
            "tanpa pemeriksaan PMI karena pemeriksaan itu merepotkan."
        )
        ok, issues = factory_knowledge.validate_translation(self._cards(), SOURCE, candidate)
        self.assertFalse(ok)
        joined = " | ".join(issues)
        self.assertIn("missing_every_bundle_scope", joined)
        self.assertIn("missing_mandatory_strength", joined)

    def test_packaging_without_pmi_cause_actor_and_station_are_required(self):
        candidate = (
            "Grade baja setiap bundel wajib diperiksa dengan PMI. "
            "Bundel yang sudah dikirim ini sudah dikemas."
        )
        ok, issues = factory_knowledge.validate_translation(self._cards(), SOURCE, candidate)
        self.assertFalse(ok)
        joined = " | ".join(issues)
        self.assertIn("missing_packaged_without_pmi_semantics", joined)
        self.assertIn("missing_inconvenience_cause", joined)
        self.assertIn("missing_shift_a_actor", joined)
        self.assertIn("missing_special_shape_packaging_station", joined)

    def test_glossary_exposes_idiom_as_soft_semantic_hint(self):
        prompt = factory_terminology.build_translation_prompt(SOURCE, self.glossary, "zh", "id")
        self.assertIn("打鋼種", prompt)
        self.assertIn("pemeriksaan grade baja dengan PMI", prompt)
        self.assertIn("bukan memberi tanda", prompt)

    def test_every_bundle_is_a_hard_classifier_mapping(self):
        pairs = factory_terminology.collect_applicable_pairs(
            "每一把都要檢查。", self.glossary, "zh", "id"
        )
        self.assertIn(("每一把", "setiap bundel"), pairs)

    def test_marking_sentence_does_not_trigger_pmi_knowledge(self):
        source = "請打印鋼種標籤後貼在每一把材料上。"
        cards = factory_knowledge.retrieve(source, "zh", "id", limit=5)
        self.assertFalse(
            any(card.get("id") == "pmi_grade_verification_bundle_packaging" for card in cards),
            cards,
        )


if __name__ == "__main__":
    unittest.main()
