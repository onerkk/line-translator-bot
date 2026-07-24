import json
import unittest
from types import SimpleNamespace

import factory_knowledge
import factory_semantic_audit as fsa
import translation_quality_gate as tqg


SOURCE = "月底前拋光機大尺寸棒材會集中大量到料，I5、I15要先從本月份的大尺寸優先生產，不可以吊小尺寸慢慢跑。"
BAD = (
    "Sebelum akhir bulan, material batang ukuran besar untuk mesin polishing akan datang "
    "dalam jumlah besar secara bersamaan. I5 dan I15 harus terlebih dahulu memprioritaskan "
    "produksi ukuran besar dari bulan ini. Jangan mengangkat material ukuran kecil lalu "
    "memprosesnya secara perlahan."
)
GOOD = (
    "Sebelum akhir bulan, batang berukuran besar untuk mesin polishing akan tiba "
    "dalam jumlah besar dalam waktu yang berdekatan. I5 dan I15 harus mendahulukan produksi "
    "batang berukuran besar yang dijadwalkan untuk bulan ini. Jangan mengangkat dan memasukkan "
    "batang berukuran kecil ke mesin lalu menjalankan produksinya secara lambat."
)


def payload_for(frame, translation=GOOD):
    return {
        "source_claims": [
            {
                "claim_id": c["claim_id"],
                "source_span": c["source_evidence"],
                "meaning_zh": c["meaning_zh"],
                "required_target_meaning_id": c["required_target_meaning_id"],
            }
            for c in frame["claims"]
        ],
        "ambiguity_resolutions": [
            {
                "source_term": a["source_term"],
                "resolved_meaning_zh": a["resolved_meaning_zh"],
                "rejected_interpretations": list(a["rejected_interpretations"]),
            }
            for a in frame["ambiguities"]
        ],
        "corrected_translation": translation,
        "claim_coverage": [
            {"claim_id": c["claim_id"], "preserved": True, "target_evidence": translation}
            for c in frame["claims"]
        ],
        "unsupported_additions": [],
        "verdict": "corrected",
    }


class FakeAI:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat_complete(self, **kwargs):
        self.calls.append(kwargs)
        msg = SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))
        choice = SimpleNamespace(message=msg)
        return SimpleNamespace(choices=[choice], _jy_provider="fake-independent")

    def get_available_providers(self, *args, **kwargs):
        return ["fake-independent"]


class SemanticFrameTests(unittest.TestCase):
    def setUp(self):
        self.frame = fsa.build_source_frame(SOURCE, "zh", "id")

    def test_source_is_decomposed_into_operational_claims(self):
        self.assertTrue(self.frame["active"])
        self.assertTrue(fsa.should_force_review(self.frame))
        claim_ids = {c["claim_id"] for c in self.frame["claims"]}
        self.assertTrue({
            "deadline_month_end", "material_arrival", "large_bar_material",
            "polishing_process", "machine_assignment", "production_priority",
            "prohibited_small_size_schedule",
        }.issubset(claim_ids))
        self.assertEqual(self.frame["machine_ids"], ["I5", "I15"])

    def test_known_bad_translation_is_rejected(self):
        ok, issues = fsa.validate_translation(self.frame, BAD)
        self.assertFalse(ok)
        self.assertIn("factory_semantic_audit:known_bad_current_month_scope", issues)

    def test_verified_natural_translation_passes(self):
        self.assertEqual(fsa.validate_translation(self.frame, GOOD), (True, []))

    def test_invented_crane_is_rejected_when_source_only_says_hoist(self):
        candidate = GOOD.replace(
            "Jangan mengangkat dan memasukkan batang",
            "Jangan mengangkat dengan overhead crane dan memasukkan batang",
        )
        ok, issues = fsa.validate_translation(self.frame, candidate)
        self.assertFalse(ok)
        self.assertIn("factory_semantic_audit:unsupported_crane_inference", issues)

    def test_invented_rpm_is_rejected(self):
        candidate = GOOD + " Atur mesin pada RPM rendah."
        ok, issues = fsa.validate_translation(self.frame, candidate)
        self.assertFalse(ok)
        self.assertIn("factory_semantic_audit:unsupported_machine_speed_inference", issues)

    def test_unrelated_crane_or_low_speed_instructions_do_not_activate_frame(self):
        controls = (
            "天車吊小尺寸棒材時要慢速操作。",
            "小尺寸棒材必須用低速運轉。",
            "月底前倉庫會集中大量到貨，請先整理庫位。",
            "拋光機今天保養，不可以啟動。",
        )
        for source in controls:
            with self.subTest(source=source):
                self.assertFalse(fsa.build_source_frame(source, "zh", "id")["active"])

    def test_paraphrase_activates_same_claim_class(self):
        source = "月底前大尺寸拋光棒材會密集大量進料，I5、I15本月先跑大尺寸，禁止先吊小尺寸占機慢跑。"
        frame = fsa.build_source_frame(source, "zh", "id")
        self.assertTrue(frame["active"])
        self.assertTrue(fsa.should_force_review(frame))
        self.assertIn("production_priority", {c["claim_id"] for c in frame["claims"]})

    def test_deterministic_rebuild_uses_current_source_slots(self):
        source = "月底前大尺寸拋光棒材會密集大量進料，P7、P8本月先跑大尺寸，禁止先吊小尺寸占機慢跑。"
        frame = fsa.build_source_frame(source, "zh", "id")
        rebuilt = fsa.deterministic_rebuild(frame)
        self.assertTrue(rebuilt)
        self.assertIn("P7 dan P8", rebuilt)
        self.assertNotIn("I5", rebuilt)
        self.assertEqual(fsa.validate_translation(frame, rebuilt), (True, []))

    def test_relation_checks_reject_scattered_keywords(self):
        candidate = (
            "Sebelum akhir bulan, batang berukuran besar untuk mesin polishing akan tiba. "
            "Jumlah besar tersedia di gudang. Dalam waktu yang berdekatan ada rapat. "
            "I5 dan I15 sedang diperiksa. Produksi harus diprioritaskan oleh tim lain. "
            "Jangan mengangkat batang berukuran kecil. Produksi lain berjalan secara lambat bulan ini."
        )
        ok, issues = fsa.validate_translation(self.frame, candidate)
        self.assertFalse(ok)
        self.assertIn("factory_semantic_audit:bulk_not_attached_to_arrival", issues)
        self.assertIn("factory_semantic_audit:machine_priority_relation_missing", issues)
        self.assertIn("factory_semantic_audit:prohibited_small_size_slow_run_relation_missing", issues)


class KnowledgeAndStructuredAuditTests(unittest.TestCase):
    def test_factory_card_retrieval_and_bad_target_rejection(self):
        store = factory_knowledge.FactoryKnowledgeStore("factory_knowledge.json")
        cards = store.retrieve(SOURCE, "zh", "id", limit=5)
        self.assertTrue(any(c["id"] == "polishing_large_bar_month_end_priority" for c in cards))
        card = [c for c in cards if c["id"] == "polishing_large_bar_month_end_priority"]
        ok, issues = store.validate_translation(card, SOURCE, BAD)
        self.assertFalse(ok)
        self.assertTrue(any("forbidden:produksi ukuran besar dari bulan ini" in x for x in issues))
        self.assertEqual(store.validate_translation(card, SOURCE, GOOD), (True, []))

    def test_review_uses_strict_schema_and_accepts_only_audited_translation(self):
        frame = fsa.build_source_frame(SOURCE, "zh", "id")
        fake = FakeAI(payload_for(frame))
        result = tqg.review_translation(
            SOURCE,
            BAD,
            "zh",
            "id",
            model="test-model",
            issues=["known semantic defect"],
            ai_client=fake,
            review_context="verified factory context",
        )
        self.assertEqual(result, GOOD)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["structured_name"], "factory_translation_source_audit")
        schema = fake.calls[0]["structured_schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("claim_coverage", schema["required"])

    def test_review_rejects_model_self_approval_with_uncovered_claim(self):
        frame = fsa.build_source_frame(SOURCE, "zh", "id")
        payload = payload_for(frame, BAD)
        payload["claim_coverage"][0]["preserved"] = False
        fake = FakeAI(payload)
        self.assertIsNone(tqg.review_translation(
            SOURCE, BAD, "zh", "id", model="test", ai_client=fake
        ))

    def test_review_rejects_evidence_not_present_in_translation(self):
        frame = fsa.build_source_frame(SOURCE, "zh", "id")
        payload = payload_for(frame)
        payload["claim_coverage"][0]["target_evidence"] = "evidence that is absent"
        fake = FakeAI(payload)
        self.assertIsNone(tqg.review_translation(
            SOURCE, BAD, "zh", "id", model="test", ai_client=fake
        ))

    def test_gate_forces_source_audit_for_high_risk_frame(self):
        frame = fsa.build_source_frame(SOURCE, "zh", "id")
        fake = FakeAI(payload_for(frame))
        result = tqg.gate_and_revise(
            SOURCE,
            BAD,
            "zh",
            "id",
            critical=False,
            model="test-model",
            ai_client=fake,
            force_review=False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], GOOD)
        self.assertTrue(result["reviewed"])
        self.assertEqual(result["path"], "independent_source_review_passed")

    def test_gate_rebuilds_safely_when_review_provider_is_unavailable(self):
        result = tqg.gate_and_revise(
            SOURCE,
            BAD,
            "zh",
            "id",
            critical=False,
            model="test-model",
            ai_client=None,
            force_review=False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], GOOD)
        self.assertEqual(result["path"], "deterministic_source_frame_rebuild")


if __name__ == "__main__":
    unittest.main()
