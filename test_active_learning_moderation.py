import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import active_learning
import translation_casebook


class ActiveLearningModerationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "corrections.db")
        self.env = mock.patch.dict(
            os.environ,
            {
                "ACTIVE_LEARNING_DB_PATH": self.db_path,
                "ACTIVE_LEARNING_VECTOR_SYNC": "0",
            },
        )
        self.env.start()
        active_learning.AL_DB_PATH = self.db_path
        active_learning._init_done = False
        active_learning.init()
        translation_casebook.invalidate_active_cache()

    def tearDown(self):
        self.env.stop()
        self.tempdir.cleanup()

    def _submit(self, **kwargs):
        payload = {
            "src_text": "課長今晚巡視",
            "original_tgt": "Patroli malam",
            "corrected_tgt": "Kepala seksi melakukan inspeksi malam ini",
            "src_lang": "zh",
            "tgt_lang": "id",
            "corrected_by": "worker-U1",
            "group_id": "G1",
        }
        payload.update(kwargs)
        return active_learning.submit_correction(**payload)

    def test_pending_feedback_cannot_enter_learning_assets_or_casebook(self):
        with mock.patch("translation_memory.tm_store") as tm_store, mock.patch(
            "vector_tm.vector_store"
        ) as vector_store:
            result = self._submit()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "pending")
        tm_store.assert_not_called()
        vector_store.assert_not_called()
        self.assertEqual(active_learning.list_corrections(status="approved"), [])
        self.assertEqual(
            translation_casebook.active_corrections_snapshot(
                active_learning, ttl_seconds=5, limit=20
            ),
            [],
        )

    def test_approval_syncs_exact_tm_and_rejection_rolls_it_back(self):
        pending = self._submit()
        with mock.patch("translation_memory.tm_store", return_value=True) as tm_store, mock.patch(
            "vector_tm.vector_store", return_value=True
        ) as vector_store:
            approved = active_learning.approve_correction(
                pending["correction_id"], approved_by="admin-U9"
            )
        self.assertTrue(approved["ok"])
        self.assertEqual(approved["status"], "approved")
        self.assertTrue(approved["tm_updated"])
        self.assertTrue(approved["vec_skipped"])
        tm_store.assert_called_once()
        vector_store.assert_not_called()

        with mock.patch("translation_memory.tm_delete_exact", return_value=1) as tm_delete, mock.patch(
            "vector_tm.vector_delete_exact", return_value=0
        ) as vector_delete:
            rejected = active_learning.reject_correction(
                pending["correction_id"], rejected_by="admin-U9", reason="wrong role"
            )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["tm_removed"], 1)
        tm_delete.assert_called_once()
        vector_delete.assert_called_once()
        self.assertEqual(active_learning.list_corrections(status="approved"), [])

    def test_duplicate_pending_feedback_is_deduplicated(self):
        first = self._submit()
        second = self._submit()
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["correction_id"], second["correction_id"])
        self.assertEqual(len(active_learning.list_corrections()), 1)

    def test_admin_resubmission_promotes_matching_pending_row(self):
        pending = self._submit()
        with mock.patch("translation_memory.tm_store", return_value=True):
            approved = self._submit(
                corrected_by="admin-U9", auto_approve=True,
                approved_by="admin-U9",
            )
        self.assertTrue(approved["promoted_from_pending"])
        self.assertEqual(approved["correction_id"], pending["correction_id"])
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(len(active_learning.list_corrections()), 1)

    def test_pre_moderation_rows_migrate_as_approved(self):
        legacy_path = os.path.join(self.tempdir.name, "legacy.db")
        with sqlite3.connect(legacy_path) as conn:
            conn.execute(
                """
                CREATE TABLE corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src_lang TEXT NOT NULL, tgt_lang TEXT NOT NULL,
                    src_text TEXT NOT NULL, src_text_hash TEXT NOT NULL,
                    original_translation TEXT NOT NULL,
                    corrected_translation TEXT NOT NULL,
                    correction_reason TEXT, corrected_by TEXT,
                    group_id TEXT DEFAULT '', created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO corrections VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",
                (
                    "zh", "id", "舊資料", active_learning._hash_text("舊資料"),
                    "lama", "data lama", None, "legacy-admin", "G1", 123,
                ),
            )
        os.environ["ACTIVE_LEARNING_DB_PATH"] = legacy_path
        active_learning.AL_DB_PATH = legacy_path
        active_learning._init_done = False
        active_learning.init()
        rows = active_learning.list_corrections()
        self.assertEqual(rows[0]["status"], "approved")
        self.assertEqual(rows[0]["updated_at"], 123)


if __name__ == "__main__":
    unittest.main()
