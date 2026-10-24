import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo import core, sample_profile, sample_snapshot, sample_quotes
import meal_report


class MealReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.Store(Path(self.tmp.name) / "test.sqlite3")
        self.store.configure(sample_profile())
        self.snapshot = sample_snapshot()
        self.request = core.recommend(self.store, self.snapshot["day"], "lunch", self.snapshot)
        self.quotes = sample_quotes(self.request, self.snapshot)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def result(self):
        return core.recommend(self.store, self.snapshot["day"], "lunch", self.snapshot, self.quotes)

    def test_all_over_budget_keeps_reference_separate_from_recommendations(self):
        for q in self.quotes["quotes"]:
            q["total_cents"] = 9999
        result = self.result()
        self.assertEqual(result["choices"], [])
        self.assertIsNone(result["preferred"])
        self.assertIsNone(result["cheapest"])
        candidate = result["closest_over_budget"]
        self.assertTrue(candidate["requires_budget_confirmation"])
        self.assertEqual(candidate["over_budget_cents"], 9999 - result["budget_cents"])
        self.assertLessEqual(len(result["over_budget_options"]), 3)
        self.assertTrue(all(not c["violations"] for c in result["over_budget_options"]))

    def test_over_budget_sorted_by_actual_price(self):
        for i, q in enumerate(self.quotes["quotes"]):
            q["total_cents"] = 10000 - i
        result = self.result()
        self.assertEqual(result["closest_over_budget"]["total_cents"], min(q["total_cents"] for q in self.quotes["quotes"]))

    def test_regular_choice_has_budget_remainder(self):
        result = self.result()
        for c in result["choices"]:
            self.assertEqual(c["budget_remaining_cents"], result["budget_cents"] - c["total_cents"])

    def test_empty_and_unpriced_never_invent_fallback(self):
        self.quotes["quotes"] = []
        result = self.result()
        self.assertIsNone(result["closest_over_budget"])
        self.assertEqual(result["over_budget_options"], [])
        self.assertEqual(meal_report.report_data(result)["plans"], [])

    def test_public_report_whitelists_fields_and_uses_physical_components(self):
        result = self.result()
        result["private_token"] = "SECRET"
        for c in result["choices"]:
            c["quote"]["order_payload"] = {"addressId": "SECRET", "couponCode": "SECRET"}
        data = meal_report.report_data(result)
        self.assertNotIn("SECRET", json.dumps(data))
        plan = next(p for p in data["plans"] if "鸡肉套餐" in p["title"])
        self.assertEqual(len(plan["items"]), 3)
        self.assertFalse(any("套餐" in i["name"] for i in plan["items"]))

    def test_unsafe_names_cannot_close_embedded_json(self):
        result = self.result()
        result["choices"][0]["items"][0]["name"] = '</script><img src=x onerror="alert(1)">'
        html = meal_report.render_report(result)
        self.assertNotIn('</script><img', html)
        self.assertIn('\\u003c/script>', html)

    def test_unknown_nutrition_preserved_and_invalid_candidate_rejected(self):
        result = self.result()
        result["choices"][0]["nutrition"]["kcal"] = None
        self.assertIsNone(meal_report.report_data(result)["plans"][0]["nutrition"]["kcal"])
        result["choices"][0]["violations"] = ["蛋白质不足"]
        with self.assertRaises(ValueError):
            meal_report.report_data(result)

    def test_waiting_for_quotes_is_not_a_report(self):
        with self.assertRaises(ValueError):
            meal_report.report_data(self.request)


if __name__ == "__main__":
    unittest.main()
