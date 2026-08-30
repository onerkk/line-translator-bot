from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import factory_message_semantics as semantics
import factory_translation_guard as guard
import translation_quality_gate as quality_gate

try:
    import app
except ModuleNotFoundError as exc:  # Minimal CI can validate the pure engine.
    app = None
    APP_IMPORT_ERROR = exc
else:
    APP_IMPORT_ERROR = None


ROOT = Path(__file__).resolve().parent
SUPERVISOR_SOURCE = (
    "處長剛剛說抓到二股滑手機，晚點可能還會下來，再注意一下"
)
SUPERVISOR_TARGET = (
    "Kepala divisi baru saja mengatakan bahwa dia memergoki seseorang dari "
    "Bagian Cold Drawing 2 sedang menggunakan ponsel. Nanti, dia mungkin akan "
    "turun lagi. Mohon lebih waspada."
)
SUPERVISOR_SCREENSHOT_BAD = (
    "Kepala divisi baru saja mengatakan menemukan Bagian Cold Drawing 2 "
    "bermain ponsel. Nanti mungkin akan turun lagi, harap lebih hati-hati."
)

WORKLOAD_SOURCE = (
    "今天的車很多來不及延到明天，處長等等應該會進來看，"
    "通知現場注意一下。"
)
WORKLOAD_TARGET = (
    "Hari ini ada banyak kendaraan. Yang tidak sempat ditangani akan ditunda "
    "sampai besok. Sebentar lagi, kepala divisi mungkin akan masuk untuk melihat "
    "keadaan. Tolong beri tahu personel di lapangan agar lebih waspada."
)
WORKLOAD_SCREENSHOT_BAD = (
    "Hari ini banyak kendaraan yang belum sempat ditangani, jadi ditunda sampai "
    "besok. Kepala divisi sebentar lagi kemungkinan akan masuk untuk melihat. "
    "Tolong beri tahu bagian lapangan agar memperhatikan."
)

ATTENDANCE_SOURCE = "點名進來了"
ATTENDANCE_TARGET = "Petugas pengecekan kehadiran sudah masuk."
ATTENDANCE_SCREENSHOT_BAD = "Absen sudah dimulai."


class ShopfloorAgentRolesRootFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        guard.reload()

    def test_screenshot_supervisor_notice_is_rebuilt_from_actor_relations(self):
        frame = semantics.build_frame(SUPERVISOR_SOURCE, "zh", "id")

        self.assertTrue(frame["active"])
        self.assertTrue(frame["complete"])
        self.assertEqual(frame["kind"], "zh_id_shopfloor_agent_roles")
        self.assertEqual(
            [item["type"] for item in frame["slots"]["segments"]],
            [
                "supervisor_observed_person_conduct",
                "supervisor_movement_inspection",
                "shopfloor_alert",
            ],
        )
        first = frame["slots"]["segments"][0]
        self.assertEqual(first["observer_id"], "kepala divisi")
        self.assertEqual(first["unit_id"], "Bagian Cold Drawing 2")
        self.assertEqual(first["conduct_id"], "menggunakan ponsel")
        self.assertEqual(
            semantics.translate_source_directly(SUPERVISOR_SOURCE, "zh", "id"),
            SUPERVISOR_TARGET,
        )
        self.assertEqual(
            semantics.validate_translation(frame, SUPERVISOR_TARGET),
            (True, []),
        )

    def test_section_cannot_be_promoted_to_the_phone_user(self):
        frame = semantics.build_frame(SUPERVISOR_SOURCE, "zh", "id")
        ok, issues = semantics.validate_translation(
            frame, SUPERVISOR_SCREENSHOT_BAD
        )

        self.assertFalse(ok)
        self.assertIn(
            "factory_message_semantics:organization_member_human_actor_missing",
            issues,
        )
        self.assertIn(
            "factory_message_semantics:organization_promoted_to_human_conduct_actor",
            issues,
        )
        self.assertIn(
            "factory_message_semantics:supervisor_movement_actor_missing",
            issues,
        )

    def test_attendance_metonym_is_a_person_entering_not_a_procedure_starting(self):
        frame = semantics.build_frame(ATTENDANCE_SOURCE, "zh-TW", "id-ID")

        self.assertTrue(frame["active"])
        self.assertTrue(frame["complete"])
        self.assertEqual(
            semantics.translate_source_directly(
                ATTENDANCE_SOURCE, "zh-TW", "id-ID"
            ),
            ATTENDANCE_TARGET,
        )
        ok, issues = semantics.validate_translation(
            frame, ATTENDANCE_SCREENSHOT_BAD
        )
        self.assertFalse(ok)
        self.assertIn(
            "factory_message_semantics:attendance_checker_human_actor_missing",
            issues,
        )
        self.assertIn(
            "factory_message_semantics:attendance_checker_movement_changed_to_procedure_start",
            issues,
        )
        self.assertIn(
            "factory_message_semantics:attendance_checker_movement_missing",
            issues,
        )

    def test_workload_supervisor_and_shopfloor_recipient_remain_separate_roles(self):
        frame = semantics.build_frame(WORKLOAD_SOURCE, "zh", "id")

        self.assertTrue(frame["active"])
        self.assertTrue(frame["complete"])
        self.assertEqual(
            semantics.translate_source_directly(WORKLOAD_SOURCE, "zh", "id"),
            WORKLOAD_TARGET,
        )
        self.assertTrue(
            semantics.validate_translation(frame, WORKLOAD_TARGET)[0]
        )
        ok, issues = semantics.validate_translation(
            frame, WORKLOAD_SCREENSHOT_BAD
        )
        self.assertFalse(ok)
        self.assertIn(
            "factory_message_semantics:shopfloor_people_recipient_missing",
            issues,
        )
        self.assertIn(
            "factory_message_semantics:shopfloor_location_mistranslated_as_department",
            issues,
        )

    def test_roles_units_conduct_and_motion_are_compositional(self):
        cases = {
            (
                "課長剛才看到一股有人睡覺，稍後可能會再下來，提醒現場注意",
                "Kepala seksi baru saja melihat seseorang dari Bagian Cold Drawing 1 "
                "sedang tidur. Nanti, dia mungkin akan turun lagi. Tolong beri tahu "
                "personel di lapangan agar lebih waspada.",
            ),
            (
                "主管發現研磨股有人抽菸",
                "Atasan mendapati seseorang dari Bagian Grinding sedang merokok.",
            ),
            (
                "處長說二股有人滑手機",
                "Kepala divisi mengatakan bahwa seseorang dari Bagian Cold Drawing 2 "
                "sedang menggunakan ponsel.",
            ),
            (
                "點名的人晚點會下來",
                "Nanti, petugas pengecekan kehadiran akan turun.",
            ),
        }

        for source, expected in cases:
            with self.subTest(source=source):
                frame = semantics.build_frame(source, "zh", "id")
                self.assertTrue(frame["active"])
                self.assertTrue(frame["complete"])
                self.assertEqual(
                    semantics.translate_source_directly(source, "zh", "id"),
                    expected,
                )
                self.assertTrue(
                    semantics.validate_translation(frame, expected)[0]
                )

    def test_unparsed_extra_clause_is_never_dropped_by_direct_route(self):
        source = SUPERVISOR_SOURCE + "，明天停機保養"
        frame = semantics.build_frame(source, "zh", "id")

        self.assertTrue(frame["active"])
        self.assertFalse(frame["complete"])
        self.assertIn("明天停機保養", frame["unparsed"])
        self.assertEqual(
            semantics.translate_source_directly(source, "zh", "id"), ""
        )
        prompt = semantics.build_prompt(frame)
        self.assertIn("Resolve human actors", prompt)
        self.assertIn("seseorang dari Bagian Cold Drawing 2", prompt)

    def test_literal_procedure_and_nonhuman_unit_sentences_do_not_trigger(self):
        controls = (
            "點名開始了",
            "二股今天要開會",
            "處長說二股產量增加",
            "滑手機很傷眼",
            "車進來了",
            "再注意一下",
            "用餐室有飲料自取",
            "各站急單幫忙優先處理，到站幫忙安排包裝",
        )
        for source in controls:
            with self.subTest(source=source):
                self.assertFalse(
                    semantics.build_frame(source, "zh", "id")["active"]
                )

    def test_both_shared_acceptance_boundaries_reject_reported_bad_outputs(self):
        for source, good, bad in (
            (SUPERVISOR_SOURCE, SUPERVISOR_TARGET, SUPERVISOR_SCREENSHOT_BAD),
            (WORKLOAD_SOURCE, WORKLOAD_TARGET, WORKLOAD_SCREENSHOT_BAD),
            (ATTENDANCE_SOURCE, ATTENDANCE_TARGET, ATTENDANCE_SCREENSHOT_BAD),
        ):
            with self.subTest(source=source):
                good_quality = quality_gate.validate_translation(
                    source, good, "zh", "id"
                )
                good_guard = guard.validate_translation(source, good, "zh", "id")
                self.assertTrue(good_quality.ok, good_quality.issues)
                self.assertTrue(good_guard.ok, good_guard.issues)
                self.assertFalse(
                    quality_gate.validate_translation(
                        source, bad, "zh", "id"
                    ).ok
                )
                self.assertFalse(
                    guard.validate_translation(source, bad, "zh", "id").ok
                )

    @unittest.skipIf(
        app is None,
        "full application dependencies are unavailable: " + str(APP_IMPORT_ERROR),
    )
    def test_public_pipeline_uses_source_roles_before_cache_tm_or_provider(self):
        with mock.patch.object(
            app,
            "_translate_inner",
            side_effect=AssertionError("provider must not run for complete frame"),
        ):
            self.assertEqual(
                app.translate(ATTENDANCE_SOURCE, "zh", "id"),
                ATTENDANCE_TARGET,
            )
        self.assertEqual(
            app._get_translation_outcome()["reason"],
            "source_first_relation_translation",
        )

    def test_deployment_build_id_and_behavioral_health_are_synchronized(self):
        expected = "2026-08-30.1-shopfloor-agent-roles"
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertEqual(semantics.FACTORY_MESSAGE_SEMANTICS_BUILD_ID, expected)
        self.assertIn(
            f'_EXPECTED_FACTORY_MESSAGE_SEMANTICS_BUILD_ID = "{expected}"',
            app_source,
        )
        health = semantics.health()
        self.assertTrue(health["self_test"]["ok"])
        self.assertGreaterEqual(health["self_test"]["checks"], 56)


if __name__ == "__main__":
    unittest.main()
