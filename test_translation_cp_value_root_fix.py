import ast
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import ai_provider
import factory_translation_policy as policy
import translation_memory as tm
import translation_quality_gate as qg


ROOT = Path(__file__).resolve().parent


class _FakeReviewClient:
    def __init__(self, output=""):
        self.output = output
        self.calls = []

    def chat_complete(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.output)
                )
            ]
        )


class TranslationCpValueRootFixTests(unittest.TestCase):
    def test_balanced_model_tiers_keep_sol_out_of_normal_spend(self):
        self.assertEqual(ai_provider.DEFAULT_OPENAI_MODEL, "gpt-5.6-luna")
        self.assertEqual(ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL, "gpt-5.6-terra")
        self.assertNotEqual(ai_provider.DEFAULT_OPENAI_MODEL, "gpt-5.6-sol")
        self.assertNotEqual(ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL, "gpt-5.6-sol")

    def test_cost_dashboard_uses_current_56_tier_prices(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        price_table = None
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if any(
                isinstance(target, ast.Name)
                and target.id == "OPENAI_PRICE_PER_M"
                for target in node.targets
            ):
                price_table = ast.literal_eval(node.value)
                break
        self.assertIsNotNone(price_table)
        self.assertEqual(price_table["gpt-5.6-luna"], (0.20, 1.20))
        self.assertEqual(price_table["gpt-5.6-terra"], (2.00, 12.00))
        self.assertEqual(price_table["gpt-5.6-sol"], (5.00, 30.00))
        self.assertIn("Luna($0.20/$1.20", source)
        self.assertIn("Terra($2/$12", source)

    def test_adaptive_policy_keeps_clean_routine_text_single_call(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FACTORY_TRANSLATION_REVIEW_MODE", None)
            os.environ.pop("FACTORY_REVIEW_CLEAN_HIGH_CONSEQUENCE", None)
            self.assertEqual(policy.review_mode(), "adaptive")
            self.assertFalse(
                policy.adaptive_review_risk(
                    "請確認材料已包裝完成。",
                    "zh",
                    "id",
                    quality_critical=False,
                )
            )
            self.assertTrue(
                policy.adaptive_review_risk(
                    "發生混料，請立即停線並通知班長。",
                    "zh",
                    "id",
                    quality_critical=False,
                )
            )

        client = _FakeReviewClient("unused")
        result = qg.gate_and_revise(
            "請確認材料已包裝完成。",
            "Mohon pastikan material sudah selesai dikemas.",
            "zh",
            "id",
            critical=False,
            model="gpt-5.6-terra",
            ai_client=client,
            force_review=False,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["path"], "single_api_local_validation")
        self.assertFalse(result["review_requested"])
        self.assertEqual(client.calls, [])

    def test_stale_always_review_setting_cannot_double_every_request(self):
        with mock.patch.dict(os.environ, {
            "FACTORY_TRANSLATION_REVIEW_MODE": "always",
            "FACTORY_ALLOW_ALWAYS_REVIEW": "0",
        }):
            self.assertEqual(policy.review_mode(), "adaptive")
            self.assertFalse(policy.require_source_review(
                "請確認材料已包裝完成。", "zh", "id", adaptive_risk=False
            ))
            self.assertTrue(policy.require_source_review(
                "發生混料，請立即停線。", "zh", "id", adaptive_risk=True
            ))

        with mock.patch.dict(os.environ, {
            "FACTORY_TRANSLATION_REVIEW_MODE": "always",
            "FACTORY_ALLOW_ALWAYS_REVIEW": "1",
        }):
            self.assertEqual(policy.review_mode(), "always")
            self.assertTrue(policy.require_source_review(
                "請確認材料已包裝完成。", "zh", "id", adaptive_risk=False
            ))

    def test_persistent_tm_requires_exact_current_policy_fingerprint(self):
        old_path = tm.TM_DB_PATH
        old_init = tm._init_done
        old_env = os.environ.get("TM_DB_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = str(Path(temp_dir) / "verified-tm.db")
                os.environ["TM_DB_PATH"] = db_path
                tm.TM_DB_PATH = None
                tm._init_done = False
                tm.init()

                source = "請確認 I15 的材料重量是 995 kg。"
                target = "Mohon pastikan berat material I15 adalah 995 kg."
                self.assertTrue(
                    tm.tm_store(
                        source,
                        target,
                        "zh",
                        "id",
                        "group-a",
                        "gpt-5.6-terra",
                        100,
                    )
                )
                self.assertIsNone(
                    tm.tm_lookup_verified_exact(
                        source,
                        "zh",
                        "id",
                        "group-a",
                        policy_fingerprint="policy-a",
                    )
                )

                self.assertTrue(
                    tm.tm_store(
                        source,
                        target,
                        "zh",
                        "id",
                        "group-a",
                        "gpt-5.6-terra",
                        100,
                        policy_fingerprint="policy-a",
                        verified=True,
                    )
                )
                self.assertIsNone(
                    tm.tm_lookup_verified_exact(
                        source,
                        "zh",
                        "id",
                        "group-a",
                        policy_fingerprint="policy-b",
                    )
                )
                self.assertIsNone(
                    tm.tm_lookup_verified_exact(
                        source,
                        "zh",
                        "id",
                        "group-b",
                        policy_fingerprint="policy-a",
                    )
                )
                hit = tm.tm_lookup_verified_exact(
                    source,
                    "zh",
                    "id",
                    "group-a",
                    policy_fingerprint="policy-a",
                )
                self.assertIsNotNone(hit)
                self.assertEqual(hit["match_type"], "verified_exact")
                self.assertEqual(hit["tgt_text"], target)
        finally:
            tm.TM_DB_PATH = old_path
            tm._init_done = old_init
            if old_env is None:
                os.environ.pop("TM_DB_PATH", None)
            else:
                os.environ["TM_DB_PATH"] = old_env

    def test_existing_translation_memory_schema_is_migrated_in_place(self):
        old_path = tm.TM_DB_PATH
        old_init = tm._init_done
        old_env = os.environ.get("TM_DB_PATH")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                db_path = str(Path(temp_dir) / "legacy-tm.db")
                with sqlite3.connect(db_path) as conn:
                    conn.execute(
                        """
                        CREATE TABLE tm_entries (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            src_lang TEXT NOT NULL,
                            tgt_lang TEXT NOT NULL,
                            src_text TEXT NOT NULL,
                            src_text_hash TEXT NOT NULL,
                            tgt_text TEXT NOT NULL,
                            group_id TEXT DEFAULT '',
                            model TEXT,
                            hit_count INTEGER DEFAULT 0,
                            quality_score REAL,
                            created_at INTEGER NOT NULL,
                            last_used_at INTEGER NOT NULL,
                            UNIQUE(src_lang, tgt_lang, src_text_hash, group_id)
                        )
                        """
                    )
                os.environ["TM_DB_PATH"] = db_path
                tm.TM_DB_PATH = None
                tm._init_done = False
                tm.init()
                with sqlite3.connect(db_path) as conn:
                    columns = {
                        row[1]
                        for row in conn.execute(
                            "PRAGMA table_info(tm_entries)"
                        ).fetchall()
                    }
                self.assertIn("policy_fingerprint", columns)
                self.assertIn("verified", columns)
        finally:
            tm.TM_DB_PATH = old_path
            tm._init_done = old_init
            if old_env is None:
                os.environ.pop("TM_DB_PATH", None)
            else:
                os.environ["TM_DB_PATH"] = old_env

    def test_app_uses_versioned_cache_before_any_paid_translation(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("def _translation_cp_tier_models", source)
        self.assertIn('"TRANSLATION_CP_ROUTINE_MODEL"', source)
        self.assertIn('"TRANSLATION_CP_QUALITY_MODEL"', source)
        self.assertIn("return _cp_quality_model", source)
        self.assertIn("return _cp_routine_model", source)
        self.assertIn("def _translation_cache_asset_fingerprint", source)
        self.assertIn("def _translation_cache_persistent_fingerprint", source)
        self.assertIn("tm_module.tm_lookup_verified_exact(", source)
        self.assertIn('pipeline_status="verified_policy_cache"', source)
        self.assertIn('pipeline_status="verified_persistent_tm"', source)
        self.assertIn("cached = cache_get(text, src, tgt)", source)
        self.assertNotIn(
            "cached = None if (_quality_critical or _force_factory) else cache_get",
            source,
        )


if __name__ == "__main__":
    unittest.main()
