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
            "I5 現在生產的這個訂單，來料尺寸偏小",
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
                    f"{code} 現在生產的這個訂單，來料尺寸偏小",
                )

    def test_i5_case_is_measurement_not_machine_scale(self):
        frame = measurement.build_frame(
            "Mesin i5 mikro kecil", equipment_codes=["I5"], work_order_context=True
        )
        self.assertEqual(
            measurement.deterministic_translation(frame),
            "I5 現在生產的這個訂單，來料尺寸偏小",
        )
        self.assertEqual(frame["work_order_relation"], "current_production")
        self.assertEqual(frame["measurement_object"], "incoming_material_dimension")
        for bad in (
            "I5 微型小機台",
            "I5 小型設備",
            "I5 這台設備很小",
            "I5 是迷你機台",
            "I5 這台設備量測尺寸偏小",
            "I5 這台設備的這張工單，尺寸偏小",
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
            "I5 生產中的材料尺寸偏小",
        )
        self.assertNotIn("工單", measurement.deterministic_translation(frame))
        self.assertIn("材料尺寸偏小", measurement.deterministic_translation(frame))
        self.assertNotIn("設備量測尺寸偏小", measurement.deterministic_translation(frame))

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
        self.assertEqual(measurement.deterministic_translation(big), "BF3 生產中的材料尺寸偏大")

        passed = measurement.build_frame("I15 mikro masuk", equipment_codes=["I15"])
        self.assertEqual(
            measurement.deterministic_translation(passed),
            "I15 生產中的材料尺寸在公差內",
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
            '_EXPECTED_FACTORY_MEASUREMENT_SEMANTICS_BUILD_ID = "2026-08-08.3-id-zh-work-order-material-dimension"',
            APP_SOURCE,
        )

    def test_work_order_context_changes_relation_and_measurement_object_not_just_wording(self):
        frame = measurement.build_frame(
            "Mesin I5 mikro kecil", equipment_codes=["I5"], work_order_context=True
        )
        self.assertEqual(frame["work_order_relation"], "current_production")
        self.assertEqual(frame["measurement_object"], "incoming_material_dimension")
        prompt = measurement.build_prompt(frame)
        self.assertIn("現在正在生產/加工照片中的這個訂單", prompt)
        self.assertIn("來料尺寸", prompt)
        self.assertIn("不是設備本體", prompt)

    def test_pending_image_race_waits_only_for_measurement_dependency(self):
        state = {"value": "pending", "clock": 0.0, "sleeps": 0}

        class FakeTime:
            @staticmethod
            def monotonic():
                return state["clock"]

            @staticmethod
            def sleep(seconds):
                state["sleeps"] += 1
                state["clock"] += seconds
                state["value"] = "work_order"

        ns = _extract_app_namespace(
            defs=("_resolve_pending_work_order_context_for_measurement",),
            extra={
                "_tl": types.SimpleNamespace(group_id="G", user_id="U"),
                "os": types.SimpleNamespace(environ={"MEASUREMENT_MEDIA_CONTEXT_WAIT_SECONDS": "3"}),
                "time": FakeTime,
                "get_recent_work_order_media_context": lambda g, u: state["value"] == "work_order",
                "get_recent_pending_image_media_context": lambda g, u: state["value"] == "pending",
                "get_recent_media_context_state": lambda g, u: state["value"],
            },
        )
        self.assertTrue(ns["_resolve_pending_work_order_context_for_measurement"]())
        self.assertEqual(state["sleeps"], 1)

    def test_image_handler_persists_pending_context_before_background_ocr(self):
        pending_pos = APP_SOURCE.index(
            "store_pending_image_media_context(group_id, user_id, event.message.id)"
        )
        background_pos = APP_SOURCE.index(
            "_bg = _threading.Thread(target=_handle_image_background"
        )
        self.assertLess(pending_pos, background_pos)

    def test_translation_variants_share_measurement_semantic_preflight(self):
        ns = _extract_app_namespace(
            defs=("_translate_variant_preserving_mentions",),
            extra={
                "protect_mentions": lambda text: (text, {}),
                "restore_mentions": lambda text, mapping: text,
                "_post_restore_mentions_guard": lambda text, mapping: text,
                "_normalize_factory_operation_question": lambda src, out, a, b: out,
                "finalize_factory_translation": lambda src, out, a, b: out,
                "_final_delivery_guard": lambda src, out, a, b: out,
                "_build_id_zh_measurement_frame": lambda text: measurement.build_frame(
                    text, equipment_codes=["I5"], work_order_context=True
                ),
                "factory_measurement_semantics_module": measurement,
                "translate_openai": lambda *args, **kwargs: (_ for _ in ()).throw(
                    AssertionError("provider must not be called for complete frame")
                ),
                "logger": types.SimpleNamespace(warning=lambda *a, **k: None),
            },
        )
        for mode in ("natural", "literal", "formal"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    ns["_translate_variant_preserving_mentions"](
                        "Mesin I5 mikro kecil", "id", "zh"
                    ),
                    "I5 現在生產的這個訂單，來料尺寸偏小",
                )

    def test_action_cache_snapshots_context_bound_measurement_semantics(self):
        import secrets
        import threading
        import time

        ns = _extract_app_namespace(
            assigns=(
                "_translation_action_cache",
                "_translation_action_lock",
                "_TRANSLATION_ACTION_TTL",
                "_TRANSLATION_ACTION_MAX",
            ),
            defs=("_register_translation_action_context", "_get_translation_action_context"),
            extra={
                "threading": threading,
                "os": types.SimpleNamespace(environ={}),
                "secrets": secrets,
                "time": time,
                "logger": types.SimpleNamespace(warning=lambda *a, **k: None),
                "_build_id_zh_measurement_frame": lambda text: measurement.build_frame(
                    text, equipment_codes=["I5"], work_order_context=True
                ),
            },
        )
        token = ns["_register_translation_action_context"](
            "G",
            "Mesin I5 mikro kecil",
            "I5 現在生產的這個訂單，來料尺寸偏小",
            "id",
            "zh",
            "M1",
        )
        saved = ns["_get_translation_action_context"](token, "G")
        self.assertIs(saved["measurement_work_order_context"], True)

    def test_action_variant_reuses_saved_semantics_after_media_context_expires(self):
        tl = types.SimpleNamespace(existing="keep")

        def fake_variant(text, src, tgt):
            self.assertEqual((src, tgt), ("id", "zh"))
            self.assertIs(tl.measurement_work_order_context_override, True)
            return "I5 現在生產的這個訂單，來料尺寸偏小"

        ns = _extract_app_namespace(
            defs=("_execute_translation_variant",),
            extra={
                "_tl": tl,
                "user_languages": {},
                "dm_target_lang": {},
                "get_group_tone": lambda group_id: ("factory", ""),
                "id_preprocessing_enabled": False,
                "resolve_factory_station_aliases": lambda text: (text, []),
                "normalize_indonesian_text": lambda text: (text, []),
                "_translate_variant_preserving_mentions": fake_variant,
                "logger": types.SimpleNamespace(exception=lambda *a, **k: None),
            },
        )
        context = {
            "original": "Mesin I5 mikro kecil",
            "translated": "I5 現在生產的這個訂單，來料尺寸偏小",
            "src": "id",
            "tgt": "zh",
            "measurement_work_order_context": True,
        }
        result, src, tgt = ns["_execute_translation_variant"](
            context, "natural", "G", "U"
        )
        self.assertEqual(result, "I5 現在生產的這個訂單，來料尺寸偏小")
        self.assertEqual((src, tgt), ("id", "zh"))
        self.assertEqual(tl.__dict__, {"existing": "keep"})

    def test_live_context_honors_action_override_without_touching_normal_messages(self):
        tl = types.SimpleNamespace(
            group_id="G", user_id="U", measurement_work_order_context_override=True
        )
        ns = _extract_app_namespace(
            defs=("_current_work_order_media_context",),
            extra={
                "_tl": tl,
                "get_recent_work_order_media_context": lambda g, u: False,
            },
        )
        self.assertTrue(ns["_current_work_order_media_context"]())
        tl.measurement_work_order_context_override = None
        self.assertFalse(ns["_current_work_order_media_context"]())

    def test_module_health_self_test_is_green(self):
        health = measurement.health()
        self.assertTrue(health["self_test"]["ok"])
        self.assertGreaterEqual(health["self_test"].get("checks", 0), 10)


if __name__ == "__main__":
    unittest.main()
