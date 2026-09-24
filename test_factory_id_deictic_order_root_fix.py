"""Regression tests for Indonesian post-nominal ID demonstratives."""

from pathlib import Path
import unittest

import factory_message_semantics as semantics


ROOT = Path(__file__).resolve().parent


class FactoryIdDeicticOrderRootFixTests(unittest.TestCase):
    def test_this_id_is_reordered_without_changing_station_or_other_facts(self):
        source = "ID ini belum dicuci tapi sudah diteruskan ke stasiun 452 🤔"
        translated = "ID 這支還沒清洗，卻已經送到 452 站了🤔"

        self.assertEqual(
            semantics.canonicalize_indonesian_id_deictic_order(
                source, translated, "id", "zh"
            ),
            "這個 ID 還沒清洗，卻已經送到 452 站了🤔",
        )

    def test_that_id_uses_the_matching_chinese_deictic(self):
        self.assertEqual(
            semantics.canonicalize_indonesian_id_deictic_order(
                "ID itu sudah dikirim ke stasiun 422.",
                "ID 那支已經送到 422 站。",
                "id-ID",
                "zh-TW",
            ),
            "那個 ID 已經送到 422 站。",
        )

    def test_already_correct_order_is_idempotent(self):
        translated = "這個 ID 還沒清洗，但已轉送至 452 站。"
        self.assertEqual(
            semantics.canonicalize_indonesian_id_deictic_order(
                "ID ini belum dicuci tapi sudah diteruskan ke stasiun 452.",
                translated,
                "ind",
                "zh-Hant",
            ),
            translated,
        )

    def test_does_not_rewrite_unrelated_codes_or_translation_directions(self):
        self.assertEqual(
            semantics.canonicalize_indonesian_id_deictic_order(
                "I15 ini belum siap.", "I15 這支尚未完成。", "id", "zh"
            ),
            "I15 這支尚未完成。",
        )
        self.assertEqual(
            semantics.canonicalize_indonesian_id_deictic_order(
                "ID ini belum dicuci.", "ID 這支尚未清洗。", "id", "en"
            ),
            "ID 這支尚未清洗。",
        )

    def test_translation_finalizer_applies_the_shared_normalizer(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        start = app_source.index("def finalize_factory_translation(")
        end = app_source.find("\ndef ", start + 1)
        finalizer = app_source[start:] if end < 0 else app_source[start:end]
        self.assertIn(
            "canonicalize_indonesian_id_deictic_order", finalizer
        )
        self.assertIn(
            '"2026-09-24.1-id-deictic-order"',
            app_source,
        )


if __name__ == "__main__":
    unittest.main()
