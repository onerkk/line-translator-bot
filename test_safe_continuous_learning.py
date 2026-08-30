import os
import tempfile
import unittest
from unittest import mock

import active_learning
import factory_translation_policy
import translation_casebook
import translation_memory
import vector_tm


class SafeContinuousLearningTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ,
            {
                "ACTIVE_LEARNING_DB_PATH": os.path.join(self.tempdir.name, "learning.db"),
                "ACTIVE_LEARNING_VECTOR_SYNC": "0",
                "TM_DB_PATH": os.path.join(self.tempdir.name, "tm.db"),
                "VECTOR_TM_DB_PATH": os.path.join(self.tempdir.name, "vector.db"),
            },
        )
        self.env.start()
        active_learning.AL_DB_PATH = os.environ["ACTIVE_LEARNING_DB_PATH"]
        active_learning._init_done = False
        translation_memory.TM_DB_PATH = os.environ["TM_DB_PATH"]
        translation_memory._init_done = False
        vector_tm.VECTOR_DB_PATH = os.environ["VECTOR_TM_DB_PATH"]
        vector_tm._init_done = False
        active_learning.init()
        translation_memory.init()
        vector_tm.init()
        translation_casebook.invalidate_active_cache()

    def tearDown(self):
        translation_casebook.invalidate_active_cache()
        self.env.stop()
        self.tempdir.cleanup()

    def _submit(self, corrected, *, original="Patroli malam", group="G1", auto=True):
        return active_learning.submit_correction(
            src_text="課長今晚巡視",
            original_tgt=original,
            corrected_tgt=corrected,
            src_lang="zh",
            tgt_lang="id",
            corrected_by="admin-U9",
            approved_by="admin-U9",
            group_id=group,
            auto_approve=auto,
        )

    def _app_or_skip(self):
        try:
            import app as app_module
        except ModuleNotFoundError as exc:
            self.skipTest(f"full application dependencies are unavailable: {exc}")
        return app_module

    def test_known_bad_feedback_cannot_poison_approved_memory(self):
        queued = active_learning.submit_correction(
            src_text="點名進來了",
            original_tgt="Petugas absensi sudah datang.",
            corrected_tgt="Absen sudah dimulai.",
            src_lang="zh",
            tgt_lang="id",
            corrected_by="worker-U1",
            group_id="G1",
        )
        self.assertTrue(queued["ok"])
        self.assertEqual(queued["validation_state"], "failed")
        blocked = active_learning.approve_correction(
            queued["correction_id"], approved_by="admin-U9"
        )
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"], "correction_validation_failed")
        self.assertTrue(any("attendance_checker" in issue for issue in blocked["validation_issues"]))
        self.assertEqual(active_learning.list_corrections(status="approved"), [])

    def test_new_revision_supersedes_and_rejection_restores_previous(self):
        first = self._submit("Kepala seksi akan melakukan inspeksi malam ini.")
        self.assertTrue(first["ok"], first)
        second = self._submit(
            "Malam ini kepala seksi akan melakukan pemeriksaan.",
            original="Kepala seksi akan melakukan inspeksi malam ini.",
        )
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["superseded_ids"], [first["correction_id"]])
        old = [row for row in active_learning.list_corrections() if row["id"] == first["correction_id"]][0]
        self.assertEqual(old["status"], "superseded")

        rejected = active_learning.reject_correction(
            second["correction_id"], rejected_by="admin-U9", reason="prefer revision 1"
        )
        self.assertEqual(rejected["restored_correction_id"], first["correction_id"])
        restored = active_learning.list_corrections(status="approved")
        self.assertEqual([row["id"] for row in restored], [first["correction_id"]])

    def test_quality_failures_only_raise_future_review_risk(self):
        source = "今天的車很多來不及延到明天，處長等等應該會進來看，通知現場注意一下。"
        paraphrase = "今天車太多做不完延到明天，處長等一下會進來看，請通知現場注意。"
        for _ in range(2):
            result = active_learning.record_translation_outcome(
                source_text=source,
                candidate_text="Terjemahan salah",
                final_text="Terjemahan diperbaiki",
                src_lang="zh",
                tgt_lang="id",
                group_id="G1",
                issues=["factory_message_semantics:vehicle_workload_actor_missing"],
                path="independent_source_review_passed",
                reviewed=True,
                cacheable=True,
            )
            self.assertTrue(result["recorded"])
        risk = active_learning.assess_review_risk(paraphrase, "zh", "id", "G1")
        self.assertTrue(risk["requires_review"], risk)
        self.assertTrue(risk["matches"])
        self.assertIn("<continuous_learning_risk>", active_learning.build_review_context(risk))
        other_group = active_learning.assess_review_risk(paraphrase, "zh", "id", "G2")
        self.assertFalse(other_group["requires_review"], other_group)

        # A risk event does not create a verified translation target.
        self.assertEqual(active_learning.list_corrections(status="approved"), [])

    def test_adaptive_policy_honors_learned_and_correction_risk_only(self):
        self.assertTrue(factory_translation_policy.adaptive_review_risk(
            "一般訊息", "zh", "id", learned_risk=True
        ))
        self.assertTrue(factory_translation_policy.adaptive_review_risk(
            "一般訊息", "zh", "id",
            semantic_contract={
                "risks": [{"sense": "verified_correction_cases"}],
                "requires_independent_review": True,
            },
        ))
        self.assertFalse(factory_translation_policy.adaptive_review_risk(
            "一般訊息", "zh", "id",
            semantic_contract={
                "risks": [{"sense": "factory_quantity_semantics"}],
                "requires_independent_review": True,
            },
        ))

    def test_casebook_does_not_leak_group_specific_corrections(self):
        approved = self._submit("Kepala seksi akan melakukan inspeksi malam ini.")
        self.assertTrue(approved["ok"], approved)
        group_one = translation_casebook.active_corrections_snapshot(
            active_learning, group_id="G1", ttl_seconds=5
        )
        group_two = translation_casebook.active_corrections_snapshot(
            active_learning, group_id="G2", ttl_seconds=5
        )
        self.assertEqual(len(group_one), 1)
        self.assertEqual(group_one[0]["revision"], 1)
        self.assertEqual(group_two, [])

    def test_reviewed_exact_tm_survives_policy_change_but_generated_row_does_not(self):
        self.assertTrue(translation_memory.tm_store(
            "課長今晚巡視",
            "Kepala seksi akan melakukan inspeksi malam ini.",
            "zh", "id", "G1", model="human_corrected", quality_score=100,
            policy_fingerprint="human-reviewed:old", verified=True,
        ))
        reviewed = translation_memory.tm_lookup_verified_exact(
            "課長今晚巡視", "zh", "id", "G1",
            policy_fingerprint="current-policy",
        )
        self.assertEqual(reviewed["verification_kind"], "reviewed_correction")

        self.assertTrue(translation_memory.tm_store(
            "一般訊息", "Pesan umum", "zh", "id", "G1",
            model="provider", quality_score=100,
            policy_fingerprint="old-policy", verified=True,
        ))
        self.assertIsNone(translation_memory.tm_lookup_verified_exact(
            "一般訊息", "zh", "id", "G1",
            policy_fingerprint="current-policy",
        ))

    def test_reviewed_vector_memory_is_reference_only(self):
        unit = [1.0, 0.0, 0.0]
        with mock.patch.object(vector_tm, "_generate_embedding", return_value=unit):
            self.assertTrue(vector_tm.vector_store(
                "課長今晚巡視",
                "Kepala seksi akan melakukan inspeksi malam ini.",
                "zh", "id", "G1", model="human_corrected", quality_score=100,
                verified=True, allow_bypass=False,
                policy_fingerprint="human-reviewed:test",
            ))
            found = vector_tm.vector_lookup("課長今晚會巡視", "zh", "id", "G1")
        self.assertEqual(found["match_type"], "vector_inject")
        self.assertTrue(found["reviewed_reference"])
        self.assertNotIn("tgt_text", found)

    def test_legacy_custom_examples_are_revalidated_before_runtime_use(self):
        app_module = self._app_or_skip()
        examples = [
            {
                "zh": "點名進來了",
                "id": "Absen sudah dimulai.",
                "dir": "zh2id",
                "source": "reaction_positive",
            },
            {
                "zh": "課長今晚巡視",
                "id": "Kepala seksi akan melakukan inspeksi malam ini.",
                "dir": "zh2id",
            },
        ]
        with mock.patch.object(app_module, "custom_translation_examples", examples):
            verified = app_module._verified_custom_translation_examples()
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]["zh"], "課長今晚巡視")
        self.assertTrue(verified[0]["verified_correction"])

    def test_positive_reaction_uses_validated_approval_path_not_raw_examples(self):
        app_module = self._app_or_skip()
        entry = {
            "id": "entry-1",
            "src": "課長今晚巡視",
            "tgt": "Kepala seksi akan melakukan inspeksi malam ini.",
            "src_lang": "zh",
            "tgt_lang": "id",
            "group_id": "G1",
        }
        sent = {"message-1": {"entry_id": "entry-1", "group_id": "G1"}}
        learned = {
            "ok": True,
            "correction_id": 41,
            "status": "approved",
            "validation_state": "passed",
            "validation_issues": [],
        }
        custom_examples = [{"zh": "既有", "id": "Ada", "dir": "zh2id"}]
        with (
            mock.patch.object(app_module, "translation_log", [entry]),
            mock.patch.object(app_module, "sent_message_to_entry", sent),
            mock.patch.object(app_module, "custom_translation_examples", custom_examples),
            mock.patch.object(app_module, "is_group_admin", side_effect=lambda uid: uid == "admin"),
            mock.patch.object(app_module.al_module, "submit_correction", return_value=learned) as submit,
            mock.patch.object(app_module, "_save_translation_log_to_disk"),
        ):
            self.assertEqual(
                app_module._apply_reaction_feedback("message-1", "like", "worker", "G1"),
                (True, "positive"),
            )
            self.assertEqual(
                app_module._apply_reaction_feedback("message-1", "like", "admin", "G1"),
                (True, "positive"),
            )
        submit.assert_called_once()
        submitted = submit.call_args.kwargs
        self.assertTrue(submitted["auto_approve"])
        self.assertEqual(submitted["group_id"], "G1")
        self.assertEqual(submitted["original_tgt"], "")
        self.assertTrue(entry["promoted_to_examples"])
        self.assertEqual(custom_examples, [{"zh": "既有", "id": "Ada", "dir": "zh2id"}])

    def test_negative_reaction_learns_review_risk_without_a_target(self):
        app_module = self._app_or_skip()
        entry = {
            "id": "entry-2",
            "src": "今天車太多做不完延到明天",
            "tgt": "Kendaraan hari ini banyak.",
            "src_lang": "zh",
            "tgt_lang": "id",
            "group_id": "G1",
        }
        sent = {"message-2": {"entry_id": "entry-2", "group_id": "G1"}}
        with (
            mock.patch.object(app_module, "translation_log", [entry]),
            mock.patch.object(app_module, "sent_message_to_entry", sent),
            mock.patch.object(
                app_module.al_module, "record_translation_outcome",
                return_value={"recorded": True, "event_id": 52, "risk_updated": True},
            ) as record,
            mock.patch.object(app_module.al_module, "submit_correction") as submit,
            mock.patch.object(app_module, "_save_translation_log_to_disk"),
        ):
            result = app_module._apply_reaction_feedback(
                "message-2", "sad", "worker", "G1"
            )
        self.assertEqual(result, (True, "negative"))
        submit.assert_not_called()
        record.assert_called_once()
        outcome = record.call_args.kwargs
        self.assertEqual(outcome["candidate_text"], outcome["final_text"])
        self.assertFalse(outcome["cacheable"])
        self.assertEqual(outcome["issues"], ("user_negative_translation_feedback",))
        self.assertEqual(entry["reaction_risk_event_id"], 52)


if __name__ == "__main__":
    unittest.main()
