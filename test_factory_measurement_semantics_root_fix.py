import ast
import types
import unittest
from pathlib import Path

import factory_measurement_semantics as measurement

ROOT = Path(__file__).resolve().parent
APP_SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")


def _literal_assignment(name):
    tree = ast.parse(APP_SOURCE)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError(f"missing literal assignment: {name}")


def _extract_app_namespace(assigns=(), defs=(), extra=None):
    tree = ast.parse(APP_SOURCE)
    nodes = []
    assign_names = set(assigns)
    def_names = set(defs)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & assign_names:
                nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in def_names:
            nodes.append(node)
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    ns = dict(extra or {})
    exec(compile(module, str(ROOT / "app.py"), "exec"), ns)
    return ns


class FactoryMeasurementSemanticsRootFixTests(unittest.TestCase):
    def test_app_equipment_extractor_feeds_existing_station_code_asset(self):
        ns = _extract_app_namespace(
            assigns=("STATION_CODES", "_EQUIPMENT_CODE_DASHES", "_KNOWN_EQUIPMENT_CODE_PATTERNS"),
            defs=(
                "_build_known_equipment_code_pattern",
                "normalize_known_equipment_codes",
                "_extract_known_equipment_codes",
            ),
            extra={
                "re": __import__("re"),
                "protect_mentions": lambda text, line_mentions=None: (text, {}),
                "restore_mentions": lambda text, mention_map: text,
            },
        )
        station_codes = ns["STATION_CODES"]
        for code in station_codes:
            with self.subTest(code=code):
                self.assertEqual(
                    ns["_extract_known_equipment_codes"](f"Mesin {code} mikro kecil"),
                    [code],
                )
        self.assertEqual(
            ns["_extract_known_equipment_codes"]("Mesin i 5 mikro kecil"),
            ["I5"],
        )
        self.assertEqual(
            ns["_extract_known_equipment_codes"]("mesin bf 3 mikro kecil"),
            ["BF3"],
        )

    def test_app_frame_builder_resolves_code_and_recent_work_order_context_together(self):
        ns = _extract_app_namespace(
            assigns=("STATION_CODES", "_EQUIPMENT_CODE_DASHES", "_KNOWN_EQUIPMENT_CODE_PATTERNS"),
            defs=(
                "_build_known_equipment_code_pattern",
                "normalize_known_equipment_codes",
                "_extract_known_equipment_codes",
                "_current_work_order_media_context",
                "_build_id_zh_measurement_frame",
            ),
            extra={
                "re": __import__("re"),
                "protect_mentions": lambda text, line_mentions=None: (text, {}),
                "restore_mentions": lambda text, mention_map: text,
                "factory_measurement_semantics_module": measurement,
                "_tl": types.SimpleNamespace(group_id="G", user_id="U"),
                "get_recent_work_order_media_context": lambda group_id, user_id: (group_id, user_id) == ("G", "U"),
                "logger": types.SimpleNamespace(warning=lambda *a, **k: None),
            },
        )
        frame = ns["_build_id_zh_measurement_frame"]("Mesin i 5 mikro kecil")
        self.assertEqual(frame["equipment_codes"], ["I5"])
        self.assertTrue(frame["work_order_context"])
        self.assertEqual(
            measurement.deterministic_translation(frame),
            "I5 這台設備的這張工單，尺寸偏小",
        )

    def test_all_canonical_equipment_codes_share_the_same_compositional_rule(self):
        station_codes = _literal_assignment("STATION_CODES")
        self.assertGreaterEqual(len(station_codes), 80)
        for code in station_codes:
            with self.subTest(code=code):
                frame = measurement.build_frame(
                    f"Mesin {code} mikro kecil",
                    equipment_codes=[code],
                    work_order_context=True,
                )
                self.assertTrue(frame["active"])
                self.assertTrue(frame["complete"])
                self.assertEqual(frame["state"], "undersize")
                self.assertEqual(
                    measurement.deterministic_translation(frame),
                    f"{code} 這台設備的這張工單，尺寸偏小",
                )

    def test_i5_case_is_measurement_not_machine_scale(self):
        frame = measurement.build_frame(
            "Mesin i5 mikro kecil", equipment_codes=["I5"], work_order_context=True
        )
        self.assertEqual(
            measurement.deterministic_translation(frame),
            "I5 這台設備的這張工單，尺寸偏小",
        )
        for bad in (
            "I5 微型小機台",
            "I5 小型設備",
            "I5 這台設備很小",
            "I5 是迷你機台",
        ):
            with self.subTest(bad=bad):
                ok, issues = measurement.validate_translation(frame, bad)
                self.assertFalse(ok)
                self.assertTrue(issues)

    def test_without_photo_context_never_fabricates_work_order(self):
        frame = measurement.build_frame(
            "Mesin I5 mikro kecil", equipment_codes=["I5"], work_order_context=False
        )
        self.assertEqual(
            measurement.deterministic_translation(frame),
            "I5 這台設備量測尺寸偏小",
        )
        self.assertNotIn("工單", measurement.deterministic_translation(frame))

    def test_mikro_is_not_globally_redefined_without_equipment_anchor(self):
        self.assertFalse(
            measurement.build_frame("produk mikro kecil", equipment_codes=[])["active"]
        )
        self.assertFalse(
            measurement.build_frame("mesin kecil", equipment_codes=[])["active"]
        )
        self.assertFalse(
            measurement.build_frame("I5 ukuran kecil", equipment_codes=["I5"])["active"]
        )

    def test_oversize_in_tolerance_and_conflict_are_modeled(self):
        big = measurement.build_frame("BF3 mikro besar", equipment_codes=["BF3"])
        self.assertEqual(measurement.deterministic_translation(big), "BF3 這台設備量測尺寸偏大")

        passed = measurement.build_frame("I15 mikro masuk", equipment_codes=["I15"])
        self.assertEqual(
            measurement.deterministic_translation(passed),
            "I15 這台設備量測尺寸在公差內",
        )

        conflict = measurement.build_frame(
            "I15 mikro kecil besar", equipment_codes=["I15"]
        )
        self.assertTrue(conflict["active"])
        self.assertTrue(conflict["state_ambiguous"])
        self.assertFalse(conflict["complete"])
        self.assertIsNone(measurement.deterministic_translation(conflict))

    def test_work_order_classifier_does_not_depend_on_customer_name(self):
        ns = _extract_app_namespace(
            assigns=("WORK_ORDER_OCR_KEYWORDS",),
            defs=("analyze_work_order", "detect_work_order"),
            extra={
                "re": __import__("re"),
                "logger": types.SimpleNamespace(info=lambda *a, **k: None),
                "CUSTOMER_NAMES": [],
            },
        )
        ocr = "冷精棒製造指示書\n訂單編號:12345\n成品尺寸MIN 10.00 MAX 10.02"
        analysis = ns["analyze_work_order"](ocr)
        self.assertTrue(analysis["is_work_order"])
        self.assertIsNone(analysis["customer"])
        self.assertGreaterEqual(analysis["keyword_count"], 2)
        self.assertIsNone(ns["detect_work_order"](ocr))

    def test_app_wiring_uses_existing_equipment_asset_and_context_safe_cache_policy(self):
        self.assertIn("codes = _extract_known_equipment_codes(normalized_text)", APP_SOURCE)
        self.assertIn("get_recent_work_order_media_context", APP_SOURCE)
        self.assertIn("_tl.user_id = user_id or \"\"", APP_SOURCE)
        self.assertIn("if _context_bound_translation:", APP_SOURCE)
        self.assertIn("_quality_cacheable = False", APP_SOURCE)
        self.assertIn(
            '_EXPECTED_FACTORY_MEASUREMENT_SEMANTICS_BUILD_ID = "2026-08-08.2-id-zh-equipment-measurement-frame"',
            APP_SOURCE,
        )

    def test_module_health_self_test_is_green(self):
        health = measurement.health()
        self.assertTrue(health["self_test"]["ok"])
        self.assertGreaterEqual(health["self_test"].get("checks", 0), 10)


if __name__ == "__main__":
    unittest.main()
