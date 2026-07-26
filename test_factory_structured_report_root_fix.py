import unittest

import factory_structured_report as structured


class FactoryStructuredReportRootFixTests(unittest.TestCase):
    def test_screenshot_measurement_report_translates_without_ai(self):
        source = "i9\nDepan 22,17\nTengah 22,15\nBelakang 22,16\nKebulatan ok."

        def normalize(value):
            return value.replace("i9", "I9"), [("i9", "I9")]

        actual = structured.translate_id_zh_measurement_report(
            source,
            normalize_equipment_codes=normalize,
        )

        self.assertEqual(
            actual,
            "I9\n前端：22,17\n中間：22,15\n後端：22,16\n圓度：正常",
        )

    def test_spacing_colons_units_and_other_quality_field(self):
        source = (
            "Mesin I9:\n"
            "Bagian depan : 22.17 mm\n"
            "Bagian tengah=22.15mm\n"
            "Bagian belakang - 22.16 mm\n"
            "Kelurusan normal"
        )
        actual = structured.translate_id_zh_measurement_report(source)
        self.assertEqual(
            actual,
            "I9\n前端：22.17mm\n中間：22.15mm\n後端：22.16mm\n直線度：正常",
        )

    def test_ng_status_is_preserved_as_abnormal(self):
        source = "I9\nDepan 22,17\nTengah 22,15\nBelakang 22,16\nKebulatan NG"
        actual = structured.translate_id_zh_measurement_report(source)
        self.assertTrue(actual.endswith("圓度：異常"))

    def test_unknown_line_fails_closed(self):
        source = (
            "I9\nDepan 22,17\nTengah 22,15\nBelakang 22,16\n"
            "Kebulatan ok\nTolong segera produksi"
        )
        self.assertIsNone(structured.translate_id_zh_measurement_report(source))

    def test_incomplete_report_does_not_hijack_normal_translation(self):
        source = "I9\nDepan 22,17\nKebulatan ok"
        self.assertIsNone(structured.translate_id_zh_measurement_report(source))

    def test_duplicate_field_is_rejected(self):
        source = (
            "I9\nDepan 22,17\nDepan 22,18\nTengah 22,15\n"
            "Belakang 22,16\nKebulatan ok"
        )
        self.assertIsNone(structured.translate_id_zh_measurement_report(source))


if __name__ == "__main__":
    unittest.main()
