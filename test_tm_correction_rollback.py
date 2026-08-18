import os
import tempfile
import unittest
from unittest import mock

import translation_memory
import vector_tm


class CorrectionRollbackTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.tm_path = os.path.join(self.tempdir.name, "tm.db")
        self.vec_path = os.path.join(self.tempdir.name, "vec.db")

        translation_memory.TM_DB_PATH = self.tm_path
        translation_memory._init_done = False
        vector_tm.VECTOR_DB_PATH = self.vec_path
        vector_tm._init_done = False
        translation_memory.init()
        vector_tm.init()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_lexical_compare_and_delete_preserves_newer_target(self):
        self.assertTrue(translation_memory.tm_store(
            "課長巡視", "Kepala seksi melakukan inspeksi", "zh", "id", "G1",
            model="human_corrected", quality_score=100,
        ))
        self.assertEqual(translation_memory.tm_delete_exact(
            "課長巡視", "zh", "id", "G1", model="human_corrected",
            target_text="target lama",
        ), 0)
        self.assertEqual(translation_memory.tm_delete_exact(
            "課長巡視", "zh", "id", "G1", model="human_corrected",
            target_text="Kepala seksi melakukan inspeksi",
        ), 1)

    def test_vector_rows_store_provenance_and_compare_delete(self):
        with mock.patch.object(vector_tm, "_generate_embedding", return_value=[0.0] * 1536):
            self.assertTrue(vector_tm.vector_store(
                "課長巡視", "Kepala seksi melakukan inspeksi", "zh", "id", "G1",
                model="human_corrected", quality_score=100,
            ))
        self.assertEqual(vector_tm.vector_delete_exact(
            "課長巡視", "zh", "id", "G1", model="human_corrected",
            target_text="target lama",
        ), 0)
        self.assertEqual(vector_tm.vector_delete_exact(
            "課長巡視", "zh", "id", "G1", model="human_corrected",
            target_text="Kepala seksi melakukan inspeksi",
        ), 1)


if __name__ == "__main__":
    unittest.main()
