import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import factory_knowledge
import translation_casebook as casebook
import translation_quality_gate as qg


ROOT = Path(__file__).resolve().parent


def factory_examples():
    document = json.loads((ROOT / "factory_knowledge.json").read_text(encoding="utf-8"))
    examples = []
    for entry in document.get("entries", []):
        for example in entry.get("examples", []):
            common = {
                "origin": "factory_knowledge",
                "case_id": entry["id"],
                "bad_target": example.get("bad_target", ""),
                "reason": example.get("reason", ""),
                "source_match": dict(entry.get("match") or {}),
            }
            if "zh-id" in entry.get("directions", []):
                examples.append(dict(common, zh=example["source"], id=example["target"], dir="zh2id"))
            if "id-zh" in entry.get("directions", []):
                examples.append(dict(common, id=example["source"], zh=example["target"], dir="id2zh"))
    return examples


class FakeReviewClient:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def get_available_providers(self, *_args, **_kwargs):
        return ["openai", "anthropic"]

    def chat_complete(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.output))],
            _jy_provider="anthropic",
        )


class TranslationCasebookRootFixTests(unittest.TestCase):
    def setUp(self):
        self.examples = factory_examples()
        self.paraphrase = "從今天開始會不定期查核上、下料時是否確實秤重，請每個班別督導人員落實。"
        self.old = (
            "Mulai hari ini akan dilakukan pemeriksaan acak terhadap pelaksanaan penimbangan "
            "saat memasukkan dan mengeluarkan material. Mohon setiap shift menegaskan hal ini."
        )
        self.correct = (
            "Mulai hari ini, akan dilakukan pemeriksaan acak untuk memastikan pelaksanaan penimbangan "
            "pada saat material dimasukkan ke mesin maupun dikeluarkan dari mesin. Mohon setiap shift "
            "memastikan operator menjalankan prosedur ini dengan benar."
        )

    def test_paraphrase_retrieves_verified_case(self):
        cases = casebook.retrieve(
            self.paraphrase, "zh", "id", examples=self.examples, max_cases=5, min_score=0.22
        )
        self.assertTrue(any(c["case_id"] == "loading_unloading_weighing_audit" for c in cases), cases)
        self.assertTrue(casebook.casebook_requires_review(cases))

    def test_unrelated_notices_do_not_retrieve_machine_case(self):
        probes = (
            "貨車卸貨後請司機到月台秤重。",
            "今日起會抽查員工出勤，請各班要求準時打卡。",
            "監視器監看發現設備漏油，請各班要求維修人員立即處理。",
            "現場觀察後請各班要求操作員清掃機台。",
            "今日起抽查秤重設備校正，會用監視器監看。",
        )
        for probe in probes:
            with self.subTest(probe=probe):
                cases = casebook.retrieve(
                    probe, "zh", "id", examples=self.examples,
                    max_cases=5, min_score=0.22,
                )
                self.assertFalse(
                    any(c["case_id"] == "loading_unloading_weighing_audit" for c in cases),
                    cases,
                )

    def test_semantically_correct_wording_variants_are_not_overconstrained(self):
        cases = casebook.retrieve(
            self.paraphrase, "zh", "id", examples=self.examples, max_cases=5, min_score=0.22
        )
        variants = (
            "Mulai hari ini, penimbangan ketika material masuk ke mesin dan keluar dari mesin akan diperiksa secara acak. Setiap shift wajib memastikan para operator mematuhi prosedur tersebut.",
            "Mulai hari ini, pemeriksaan acak akan dilakukan untuk memastikan bahan ditimbang saat masuk dan keluar dari mesin. Masing-masing shift harus memastikan prosedur ini dipatuhi oleh operator.",
        )
        for candidate in variants:
            with self.subTest(candidate=candidate):
                self.assertTrue(casebook.validate_translation_cases(cases, candidate)[0])

    def test_unguarded_generic_example_does_not_force_second_provider_review(self):
        cases = casebook.retrieve(
            "今日起會抽查員工出勤，請各班要求準時打卡。",
            "zh", "id",
            examples=[{
                "zh": "今日起會抽查上下料秤重作業落實性，請各班要求。",
                "id": self.correct,
                "bad_id": self.old,
                "dir": "zh2id",
                "origin": "human_correction",
            }],
            max_cases=5, min_score=0.22,
        )
        self.assertFalse(casebook.casebook_requires_review(cases), cases)

    def test_known_wrong_output_is_rejected_but_correct_output_passes(self):
        cases = casebook.retrieve(
            self.paraphrase, "zh", "id", examples=self.examples, max_cases=5, min_score=0.22
        )
        self.assertFalse(casebook.validate_translation_cases(cases, self.old)[0])
        self.assertTrue(casebook.validate_translation_cases(cases, self.correct)[0])

    def test_latest_human_correction_wins_conflict(self):
        corrections = [
            {"source": "請確認重量", "target": "Mohon pastikan beratnya.", "direction": "zh2id", "bad_target": "", "case_id": "new"},
            {"source": "請確認重量", "target": "Silakan cek berat.", "direction": "zh2id", "bad_target": "", "case_id": "old"},
        ]
        cases = casebook.collect_cases([], corrections)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["case_id"], "new")

    def test_force_review_prefers_other_provider_and_applies_case_validator(self):
        cases = casebook.retrieve(
            self.paraphrase, "zh", "id", examples=self.examples, max_cases=5, min_score=0.22
        )
        client = FakeReviewClient(self.correct)
        result = qg.gate_and_revise(
            self.paraphrase,
            self.old,
            "zh",
            "id",
            critical=True,
            model="review-model",
            ai_client=client,
            force_review=True,
            used_provider="openai",
            review_context=casebook.build_prompt(cases),
            semantic_validator=lambda candidate: casebook.validate_translation_cases(cases, candidate),
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["path"], "independent_source_review_passed")
        self.assertEqual(result["text"], self.correct)
        self.assertEqual(client.calls[0]["provider_preference"][0], "anthropic")

    def test_every_factory_example_retrieves_its_own_card_and_validates(self):
        store = factory_knowledge.FactoryKnowledgeStore(ROOT / "factory_knowledge.json")
        document = store.document()
        for entry in document.get("entries", []):
            for example in entry.get("examples", []):
                src, tgt = ("zh", "id") if "zh-id" in entry.get("directions", []) else ("id", "zh")
                cards = store.retrieve(example["source"], src, tgt, limit=10)
                self.assertTrue(any(card["id"] == entry["id"] for card in cards), (entry["id"], example))
                own = [card for card in cards if card["id"] == entry["id"]]
                ok, issues = store.validate_translation(own, example["source"], example["target"])
                self.assertTrue(ok, (entry["id"], issues))


if __name__ == "__main__":
    unittest.main()
