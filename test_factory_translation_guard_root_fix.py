import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import factory_translation_guard as guard
import factory_translation_policy as policy
import translation_casebook as casebook

ROOT = Path(__file__).resolve().parent


class FactoryTranslationGuardRootFixTests(unittest.TestCase):
    def setUp(self):
        guard.reload()

    def test_boot_self_test_compiles_all_approved_assets(self):
        health = guard.health()
        self.assertEqual(health["api_version"], 1)
        self.assertTrue(health["self_test"]["ok"])
        self.assertGreaterEqual(health["self_test"]["approved_examples_validated"], 20)
        self.assertGreaterEqual(health["self_test"]["known_bad_examples_rejected"], 5)
        self.assertGreaterEqual(health["exact_case_count"], 20)
        self.assertEqual(len(health["asset_fingerprint"]), 64)

    def test_punctuation_and_spacing_variant_gets_verified_exact_target(self):
        source = "大成週一抓帳 還有160噸會陸續到料 有看到大成麻煩優先安排包裝"
        target = guard.exact_verified_target(source, "zh", "id")
        self.assertIsNotNone(target)
        self.assertIn("tutup buku", target)
        self.assertIn("160 ton", target)
        self.assertIn("大成", target)

    def test_semantic_paraphrase_does_not_copy_exact_sentence(self):
        source = "大成星期一要關帳，後續仍有160噸材料分批進廠，請先包裝大成的材料。"
        self.assertIsNone(guard.exact_verified_target(source, "zh", "id"))

    def test_all_regression_targets_pass_and_forbidden_targets_fail(self):
        document = json.loads((ROOT / "factory_translation_regression.json").read_text(encoding="utf-8"))
        for row in document["cases"]:
            src, tgt = row["direction"].split("-", 1)
            with self.subTest(row=row["id"], variant="verified"):
                report = guard.validate_translation(row["source"], row["verified_target"], src, tgt)
                self.assertTrue(report.ok, report.issues)
            if row.get("forbidden_target"):
                bad = row["verified_target"] + " " + row["forbidden_target"][0]
                with self.subTest(row=row["id"], variant="forbidden"):
                    report = guard.validate_translation(row["source"], bad, src, tgt)
                    self.assertFalse(report.ok)

    def test_known_screenshot_mistranslations_are_rejected(self):
        account_source = "大成週一抓帳，還有160噸會陸續到料，有看到大成麻煩優先安排包裝。"
        account_bad = (
            "Besar pada hari Senin akan cek data. Masih ada 160 ton material yang akan tiba bertahap. "
            "Jika melihat material 大成, mohon prioritaskan pengaturan pengemasannya."
        )
        report = guard.validate_translation(account_source, account_bad, "zh", "id", protected_names=["大成"])
        self.assertFalse(report.ok)
        self.assertTrue(any("accounting_close" in issue or "cek data" in issue for issue in report.issues))

        pull_source = "以前這是自動的，但自動運轉沒比較好，所以拔除電子系統，改自然拉動。"
        pull_bad = "Dulu ini otomatis. Sistem elektroniknya dilepas dan sekarang ditarik secara manual."
        report = guard.validate_translation(pull_source, pull_bad, "zh", "id")
        self.assertFalse(report.ok)
        self.assertTrue(any("manual" in issue or "natural_passive" in issue for issue in report.issues))

    def test_identity_code_number_and_unit_are_hard_invariants(self):
        source = "大成的 I15 還有160噸材料。"
        good = "Material 大成 untuk I15 masih tersisa 160 ton."
        self.assertTrue(
            guard.validate_translation(source, good, "zh", "id", protected_names=["大成"]).ok
        )
        probes = (
            "Material untuk I15 masih tersisa 160 ton.",
            "Material 大成 masih tersisa 160 ton.",
            "Material 大成 untuk I15 masih tersisa 16 ton.",
            "Material 大成 untuk I15 masih tersisa 160 kg.",
        )
        for candidate in probes:
            with self.subTest(candidate=candidate):
                self.assertFalse(
                    guard.validate_translation(
                        source, candidate, "zh", "id", protected_names=["大成"]
                    ).ok
                )

    def test_id_to_zh_number_and_unit_are_preserved(self):
        source = "Masih ada 160 ton material, dengan toleransi 0,5 mm."
        good = "還有160噸材料，公差為0.5毫米。"
        self.assertTrue(guard.validate_translation(source, good, "id", "zh").ok)
        self.assertFalse(
            guard.validate_translation(source, "還有160份材料，公差為0.5。", "id", "zh").ok
        )

    def test_dominant_card_selection_avoids_incidental_overlap(self):
        source = "入庫時間再平均一點，不要全部移完才一次入帳。"
        good = "Waktu pencatatan masuk ke sistem dibuat lebih merata; jangan menunggu semuanya selesai dipindahkan baru dicatat sekaligus."
        report = guard.validate_translation(source, good, "zh", "id")
        self.assertTrue(report.ok, report.issues)
        self.assertIn("erp_station_record_transfer_timing", report.matched_cards)
        self.assertNotIn("warehouse_intake_target_and_forecast", report.matched_cards)

    def test_asset_fingerprint_changes_when_approved_data_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_path = Path(temp_dir) / "factory_knowledge.json"
            regression_path = Path(temp_dir) / "factory_translation_regression.json"
            shutil.copy2(ROOT / "factory_knowledge.json", knowledge_path)
            shutil.copy2(ROOT / "factory_translation_regression.json", regression_path)
            first = guard.FactoryTranslationGuard(knowledge_path, regression_path)
            first_hash = first.asset_fingerprint
            data = json.loads(regression_path.read_text(encoding="utf-8"))
            data["description"] += " fingerprint probe"
            regression_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            second = guard.FactoryTranslationGuard(knowledge_path, regression_path)
            self.assertNotEqual(first_hash, second.asset_fingerprint)

    def test_conflicting_verified_targets_fail_startup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_path = Path(temp_dir) / "factory_knowledge.json"
            regression_path = Path(temp_dir) / "factory_translation_regression.json"
            shutil.copy2(ROOT / "factory_knowledge.json", knowledge_path)
            data = json.loads((ROOT / "factory_translation_regression.json").read_text(encoding="utf-8"))
            duplicate = dict(data["cases"][0])
            duplicate["id"] = "conflicting_duplicate"
            duplicate["source"] = duplicate["source"].replace("，", " ").replace("。", "")
            duplicate["verified_target"] = "Target yang bertentangan."
            data["cases"].append(duplicate)
            regression_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.assertRaises(guard.GuardConfigurationError):
                guard.FactoryTranslationGuard(knowledge_path, regression_path)

    def test_casebook_exact_lookup_uses_same_safe_canonicalization(self):
        cases = casebook.collect_cases([
            {"zh": "本月木箱暫不裝箱。", "id": "Peti kayu bulan ini sementara jangan dikemas.", "dir": "zh2id"}
        ])
        self.assertEqual(
            casebook.exact_verified_target("本月木箱 暫不裝箱", cases),
            "Peti kayu bulan ini sementara jangan dikemas.",
        )
        self.assertIsNone(casebook.exact_verified_target("下個月木箱暫不裝箱", cases))

    def test_factory_policy_separates_delivery_availability_from_learning_admission(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FACTORY_TRANSLATION_FAIL_CLOSED", None)
            os.environ.pop("FACTORY_BLOCK_UNVERIFIED_DELIVERY", None)
            os.environ.pop("FACTORY_REQUIRE_VERIFIED_FOR_CACHE", None)
            self.assertFalse(policy.fail_closed("zh", "id"))
            self.assertFalse(policy.fail_closed("id", "zh"))
            self.assertTrue(policy.require_verified_for_cache("zh", "id"))
            self.assertTrue(policy.require_verified_for_cache("id", "zh"))
        # The legacy delivery-block switch is intentionally ignored so an old
        # deployment variable cannot resurrect the generic failure notice.
        with mock.patch.dict(os.environ, {"FACTORY_TRANSLATION_FAIL_CLOSED": "1"}):
            os.environ.pop("FACTORY_BLOCK_UNVERIFIED_DELIVERY", None)
            self.assertFalse(policy.fail_closed("zh", "id"))
        with mock.patch.dict(os.environ, {"FACTORY_BLOCK_UNVERIFIED_DELIVERY": "1"}):
            self.assertFalse(policy.fail_closed("zh", "id"))

    def test_factory_policy_reviews_every_message_without_vetoing_delivery_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FACTORY_TRANSLATION_REVIEW_MODE", None)
            os.environ.pop("FACTORY_TRANSLATION_REQUIRE_REVIEW_SUCCESS", None)
            self.assertEqual(policy.review_mode(), "always")
            self.assertTrue(policy.require_source_review(
                "普通現場訊息", "zh", "id", adaptive_risk=False
            ))
            self.assertTrue(policy.require_source_review(
                "高風險現場訊息", "zh", "id", adaptive_risk=True
            ))
            self.assertFalse(policy.require_review_success("zh", "id"))
        with mock.patch.dict(os.environ, {
            "FACTORY_TRANSLATION_REVIEW_MODE": "adaptive",
            "FACTORY_TRANSLATION_REQUIRE_REVIEW_SUCCESS": "0",
        }):
            self.assertFalse(policy.require_source_review(
                "普通現場訊息", "zh", "id", adaptive_risk=False
            ))
            self.assertTrue(policy.require_source_review(
                "高風險現場訊息", "zh", "id", adaptive_risk=True
            ))
            self.assertFalse(policy.require_review_success("zh", "id"))

    def test_expanded_historical_regression_corpus_is_guarded(self):
        document = json.loads((ROOT / "factory_translation_regression.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(document["cases"]), 16)
        expected_ids = {
            "work_order_announcement_terms",
            "id_machine_fire_not_ui_label",
            "id_pre_operation_rear_issue_boundary",
            "special_shape_packaging_station_alias",
            "warehouse_intake_output_forecast_units",
            "organization_hierarchy_discipline_notice",
        }
        self.assertTrue(expected_ids.issubset({row["id"] for row in document["cases"]}))
        for row in document["cases"]:
            src, tgt = row["direction"].split("-", 1)
            with self.subTest(case=row["id"]):
                self.assertEqual(
                    guard.exact_verified_target(row["source"], src, tgt),
                    row["verified_target"],
                )
                self.assertTrue(
                    guard.validate_translation(row["source"], row["verified_target"], src, tgt).ok
                )

    def test_app_wires_guard_into_every_delivery_and_learning_boundary(self):
        app_text = (ROOT / "app.py").read_text(encoding="utf-8")
        required_fragments = (
            "import factory_translation_guard as factory_translation_guard_module",
            "factory_translation_guard_module.build_prompt(text, src, tgt)",
            "def _factory_guard_report(",
            "def _final_delivery_guard(",
            "def _tm_bypass_integrity_ok(",
            "def cache_set(",
            "def _post_translation_async(",
            "factory_translation_guard_module.exact_verified_target(text, src, tgt)",
            "degraded_translation_delivered_not_cached",
            "factory_translation_policy_module.block_unverified_delivery(src, tgt)",
            "factory_translation_policy_module.require_verified_for_cache(src, tgt)",
            "factory_translation_policy_module.require_source_review(",
            "require_review_success=_require_review_success",
            '"factory_translation_guard": factory_translation_guard_module.health()',
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, app_text)
        self.assertNotIn(
            "[FinalDeliveryGuard] advisory validation issues=%s; delivering provider result",
            app_text,
        )
        self.assertIn(
            "inner_candidate_deferred_to_authoritative_gate",
            app_text,
        )


if __name__ == "__main__":
    unittest.main()
