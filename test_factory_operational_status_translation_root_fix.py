import ast
import json
import unittest
from pathlib import Path

import factory_knowledge
import factory_message_semantics as message_semantics
import factory_quantity_semantics as quantity_semantics
import factory_semantic_audit as semantic_audit
import factory_translation_guard as guard
import prompt_optimizer
import translation_quality_gate as quality_gate


ROOT = Path(__file__).resolve().parent
ERP_SOURCE = "@budi santoso 山多 成功發料"
ERP_TARGET = "@budi santoso 山多 sudah berhasil mengubah status data menjadi OL."
ATTENDANCE_SOURCE = "下週開始取消夜間點名，改成安衛不定時入廠抽查。"
ATTENDANCE_TARGET = (
    "Mulai minggu depan, pengecekan kehadiran malam ditiadakan. "
    "Sebagai gantinya, bagian K3 akan melakukan pemeriksaan acak di pabrik "
    "tanpa jadwal tetap."
)
STAFFING_SOURCE = (
    "月底前有確認請假的再提前告知，上面要統計月底生產人力。\n"
    "本月目標3800，目前進度比上個月還落後，月底前應該都是開三站追量，"
    "麻煩盡量一天請假不超過2員。"
)
STAFFING_TARGET = (
    "Bagi yang sudah memastikan akan mengambil cuti sebelum akhir bulan, mohon beri tahu lebih awal. "
    "Pihak manajemen perlu menghitung tenaga kerja produksi untuk akhir bulan. "
    "Target bulan ini adalah 3800. Saat ini progresnya lebih tertinggal dibandingkan bulan lalu. "
    "Hingga akhir bulan, kemungkinan tiga stasiun akan terus dioperasikan untuk mengejar target. "
    "Mohon usahakan agar jumlah karyawan yang mengambil cuti tidak lebih dari 2 orang per hari."
)
QIWO_SOURCE = (
    "@All 下班前來領奇沃衣服（兩件）跟帽子（一頂），麻煩按照單子上的尺寸領取。\n"
    "#領完單子上一定要打勾。"
)
QIWO_TARGET = (
    "@All Sebelum pulang kerja, silakan ambil pakaian QIWO (2 potong) dan topi (1 buah). "
    "Mohon ambil sesuai ukuran yang tercantum pada daftar. "
    "Setelah selesai mengambil, wajib beri tanda centang pada daftar."
)


class FactoryOperationalStatusTranslationRootFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        guard.reload()

    def test_faliao_is_one_erp_data_status_action(self):
        frame = semantic_audit.build_source_frame(ERP_SOURCE, "zh", "id")
        self.assertTrue(frame["active"])
        self.assertTrue(frame["flags"]["erp_release_to_ol"])
        self.assertEqual(frame["operational"]["erp_actor"], "@budi santoso 山多")
        self.assertEqual(
            semantic_audit.translate_source_directly(ERP_SOURCE, "zh", "id"),
            ERP_TARGET,
        )
        self.assertTrue(semantic_audit.validate_translation(frame, ERP_TARGET)[0])

        old_bad = "@budi santoso 山多 berhasil mengeluarkan material."
        ok, issues = semantic_audit.validate_translation(frame, old_bad)
        self.assertFalse(ok)
        self.assertIn(
            "factory_semantic_audit:erp_release_mistranslated_as_physical_material",
            issues,
        )

        prompt_rules = prompt_optimizer._matching_historical_rules(
            ERP_SOURCE, "zh>id", limit=10
        )
        self.assertTrue(any("[erp-data-status-ol]" in rule for rule in prompt_rules))
        self.assertFalse(any("[factory-material]" in rule for rule in prompt_rules))
        self.assertTrue(
            any(
                "[factory-material]" in rule
                for rule in prompt_optimizer._matching_historical_rules(
                    "來料已到", "zh>id", limit=10
                )
            )
        )

    def test_erp_frame_composes_with_aspect_and_never_drops_extra_clauses(self):
        source = "這筆工單已經發料完成"
        target = "Status data work order ini sudah diubah menjadi OL."
        self.assertEqual(
            semantic_audit.translate_source_directly(source, "zh", "id"),
            target,
        )
        frame = semantic_audit.build_source_frame(source, "zh", "id")
        self.assertTrue(semantic_audit.validate_translation(frame, target)[0])
        ok, issues = semantic_audit.validate_translation(
            frame, "Status data sudah diubah menjadi OL."
        )
        self.assertFalse(ok)
        self.assertIn(
            "factory_semantic_audit:erp_work_order_record_missing", issues
        )
        self.assertEqual(
            semantic_audit.translate_source_directly(
                ERP_SOURCE + "，明天停機。", "zh", "id"
            ),
            "",
        )

    def test_night_roll_call_is_attendance_not_assembly(self):
        frame = semantic_audit.build_source_frame(ATTENDANCE_SOURCE, "zh", "id")
        self.assertEqual(
            semantic_audit.translate_source_directly(
                ATTENDANCE_SOURCE, "zh", "id"
            ),
            ATTENDANCE_TARGET,
        )
        old_bad = (
            "Mulai minggu depan, apel malam ditiadakan dan diganti dengan "
            "pemeriksaan acak oleh bagian K3 yang datang ke pabrik tanpa jadwal tetap."
        )
        ok, issues = semantic_audit.validate_translation(frame, old_bad)
        self.assertFalse(ok)
        self.assertIn(
            "factory_semantic_audit:night_roll_call_mistranslated_as_assembly",
            issues,
        )

    def test_bare_monthly_target_cannot_gain_a_unit(self):
        frame = semantic_audit.build_source_frame(STAFFING_SOURCE, "zh", "id")
        self.assertEqual(frame["counts"]["monthly_production_target"], 3800)
        self.assertEqual(frame["units"]["monthly_production_target"], "")
        self.assertEqual(
            semantic_audit.translate_source_directly(
                STAFFING_SOURCE, "zh", "id"
            ),
            STAFFING_TARGET,
        )
        old_bad = STAFFING_TARGET.replace("3800.", "3800 ton.")
        ok, issues = semantic_audit.validate_translation(frame, old_bad)
        self.assertFalse(ok)
        self.assertIn(
            "factory_semantic_audit:unsupported_monthly_target_unit_inference",
            issues,
        )

        explicit_source = STAFFING_SOURCE.replace("目標3800", "目標3800噸")
        explicit_target = semantic_audit.translate_source_directly(
            explicit_source, "zh", "id"
        )
        self.assertIn("Target bulan ini adalah 3800 ton.", explicit_target)

    def test_equipment_code_rusak_is_a_functional_machine_failure(self):
        frame = message_semantics.build_frame("i15 rusak", "id", "zh")
        self.assertEqual(frame["kind"], "id_zh_equipment_code_failure")
        self.assertTrue(frame["complete"])
        self.assertEqual(
            message_semantics.translate_source_directly(
                "i15 rusak", "id", "zh"
            ),
            "I15 機台故障",
        )
        self.assertEqual(
            message_semantics.translate_source_directly(
                "@小麥 i15 rusak", "id", "zh"
            ),
            "@小麥 I15 機台故障",
        )
        self.assertEqual(
            message_semantics.translate_source_directly(
                "i15 rusak besok diperbaiki", "id", "zh"
            ),
            "",
        )
        self.assertFalse(
            message_semantics.validate_translation(frame, "i15 損壞")[0]
        )

    def test_qiwo_items_counts_size_and_checkbox_are_linked(self):
        self.assertEqual(
            semantic_audit.translate_source_directly(QIWO_SOURCE, "zh", "id"),
            QIWO_TARGET,
        )
        frame = quantity_semantics.build_frame("帽子一頂", "zh", "id")
        self.assertEqual(frame["atoms"][0]["classifier"], "頂")
        self.assertEqual(frame["atoms"][0]["canonical_id"], "buah")
        self.assertTrue(
            quantity_semantics.validate_translation(frame, "satu buah topi")[0]
        )

    def test_all_acceptance_boundaries_reject_the_known_bad_outputs(self):
        for source, good, bad, src, tgt in (
            (
                ERP_SOURCE,
                ERP_TARGET,
                "@budi santoso 山多 berhasil mengeluarkan material.",
                "zh",
                "id",
            ),
            (
                ATTENDANCE_SOURCE,
                ATTENDANCE_TARGET,
                "Mulai minggu depan, apel malam ditiadakan dan diganti dengan pemeriksaan acak oleh bagian K3.",
                "zh",
                "id",
            ),
            ("i15 rusak", "I15 機台故障", "i15 損壞", "id", "zh"),
        ):
            with self.subTest(source=source):
                self.assertTrue(
                    quality_gate.validate_translation(source, good, src, tgt).ok
                )
                self.assertTrue(
                    guard.validate_translation(source, good, src, tgt).ok
                )
                self.assertFalse(
                    quality_gate.validate_translation(source, bad, src, tgt).ok
                )
                self.assertFalse(
                    guard.validate_translation(source, bad, src, tgt).ok
                )

    def test_glossary_knowledge_and_regression_assets_are_synchronized(self):
        glossary = json.loads(
            (ROOT / "glossary_data.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            glossary["發料"]["canonical_idn"],
            "mengubah status data menjadi OL",
        )
        self.assertIn(
            "mengeluarkan material", glossary["發料"]["forbidden_idn"]
        )

        module = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        embedded = next(
            ast.literal_eval(node.value)
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_GLOSSARY_JSON"
                for target in node.targets
            )
        )
        self.assertEqual(json.loads(embedded), glossary)

        store = factory_knowledge.FactoryKnowledgeStore(
            str(ROOT / "factory_knowledge.json")
        )
        cards = store.retrieve(ERP_SOURCE, "zh", "id", limit=10)
        self.assertIn(
            "erp_release_data_to_ol_status",
            {card["id"] for card in cards},
        )
        self.assertEqual(
            guard.exact_verified_target(ERP_SOURCE, "zh", "id"), ERP_TARGET
        )

    def test_deployment_build_contracts_match_changed_modules(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn(semantic_audit.FACTORY_SEMANTIC_AUDIT_BUILD_ID, app_source)
        self.assertIn(
            message_semantics.FACTORY_MESSAGE_SEMANTICS_BUILD_ID, app_source
        )
        self.assertIn(
            quantity_semantics.FACTORY_QUANTITY_SEMANTICS_BUILD_ID, app_source
        )
        self.assertNotIn("發料=issue material", app_source)
        self.assertNotIn("領料/發料 → issue material", app_source)
        self.assertIn("發料=mengubah status data menjadi OL", app_source)
        self.assertNotIn("點名=ada pengawas yang datang", app_source)
        self.assertIn("點名=pengecekan kehadiran/absensi", app_source)
        self.assertNotIn('"roll call": "inspeksi pengawas"', app_source)
        self.assertIn('"roll call": "pengecekan kehadiran"', app_source)
        self.assertIn('"rusak": "故障",', app_source)
        self.assertIn(
            "operational_fallback = factory_semantic_audit_module.translate_source_directly",
            app_source,
        )


if __name__ == "__main__":
    unittest.main()
