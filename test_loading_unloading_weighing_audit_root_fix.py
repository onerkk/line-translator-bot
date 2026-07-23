import json
import unittest
from pathlib import Path

import factory_knowledge


ROOT = Path(__file__).resolve().parent
SOURCE = (
    "今日起會抽查上下料秤重作業落實性，請各班要求。"
    "會以監視器監看方式及現場觀察進行查核作業。"
)


class LoadingUnloadingWeighingAuditRootFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads((ROOT / "factory_knowledge.json").read_text(encoding="utf-8"))
        factory_knowledge.get_store().reload(force=True)

    def _cards(self, source=SOURCE):
        return factory_knowledge.retrieve(source, "zh", "id", limit=5)

    def test_runtime_retrieves_loading_unloading_weighing_audit_context(self):
        cards = self._cards()
        self.assertTrue(
            any(card.get("id") == "loading_unloading_weighing_audit" for card in cards),
            cards,
        )

    def test_correct_translation_preserves_machine_direction_shift_enforcement_and_audit_methods(self):
        candidate = (
            "Mulai hari ini, akan dilakukan pemeriksaan acak untuk memastikan pelaksanaan "
            "penimbangan pada saat material dimasukkan ke mesin maupun dikeluarkan dari mesin. "
            "Mohon setiap shift memastikan operator menjalankan prosedur ini dengan benar. "
            "Pemeriksaan akan dilakukan melalui pemantauan CCTV dan observasi langsung di lapangan."
        )
        ok, issues = factory_knowledge.validate_translation(self._cards(), SOURCE, candidate)
        self.assertTrue(ok, issues)

    def test_old_translation_is_rejected_for_generic_material_movement_and_weak_shift_instruction(self):
        candidate = (
            "Mulai hari ini akan dilakukan pemeriksaan acak terhadap pelaksanaan penimbangan "
            "saat memasukkan dan mengeluarkan material. Mohon setiap shift menegaskan hal ini. "
            "Pemeriksaan akan dilakukan melalui pemantauan kamera pengawas dan observasi langsung di lapangan."
        )
        ok, issues = factory_knowledge.validate_translation(self._cards(), SOURCE, candidate)
        self.assertFalse(ok)
        joined = " | ".join(issues)
        self.assertIn("forbidden:menegaskan hal ini", joined)
        self.assertIn("forbidden:saat memasukkan dan mengeluarkan material", joined)
        self.assertIn("missing_material_loading_into_machine_semantics", joined)
        self.assertIn("missing_material_unloading_from_machine_semantics", joined)
        self.assertIn("missing_shift_enforcement_semantics", joined)

    def test_physical_truck_loading_does_not_trigger_machine_audit_context(self):
        source = "貨車上下料後要秤重，司機在倉庫月台等待。"
        cards = self._cards(source)
        self.assertFalse(
            any(card.get("id") == "loading_unloading_weighing_audit" for card in cards),
            cards,
        )

    def test_cctv_and_direct_field_observation_are_both_required_when_present(self):
        candidate = (
            "Pemeriksaan akan dilakukan melalui pemantauan CCTV."
        )
        cards = self._cards("會以監視器監看方式及現場觀察進行查核作業。")
        ok, issues = factory_knowledge.validate_translation(cards, "會以監視器監看方式及現場觀察進行查核作業。", candidate)
        self.assertFalse(ok)
        self.assertIn(
            "factory_knowledge:loading_unloading_weighing_audit:missing_direct_field_observation_semantics",
            issues,
        )

    def test_knowledge_example_contains_the_corrected_translation(self):
        entry = next(e for e in self.document["entries"] if e["id"] == "loading_unloading_weighing_audit")
        targets = [example["target"] for example in entry.get("examples", [])]
        self.assertTrue(any("dimasukkan ke mesin" in target for target in targets))
        self.assertTrue(any("setiap shift memastikan operator" in target for target in targets))
        self.assertTrue(any("pemantauan CCTV" in target for target in targets))


if __name__ == "__main__":
    unittest.main()
