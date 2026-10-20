import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo import core, sample_snapshot


class BasketReviewTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = sample_snapshot()

    def selection(self, identifier, price):
        items = [{"id": identifier, "quantity": 1}]
        return {"items": items, "quote": {"key": core.candidate_key(items), "kind": "demo", "store_code": "DEMO", "context": "演示自取", "total_cents": price, "quoted_at": "2026-10-14T12:00:00+08:00"}}

    def test_no_comparison_is_not_no_recommendation(self):
        result = core.review_basket(self.snapshot, self.selection("bundle-zero", 3000), [], {"bundles_checked": False, "coupons_checked": True})
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["can_finalize"])

    def test_checked_but_no_better_option_explicitly_returns_none(self):
        result = core.review_basket(self.snapshot, self.selection("bundle-zero", 3000), [self.selection("bundle-juice", 3300)], {"bundles_checked": True, "coupons_checked": True})
        self.assertEqual(result["status"], "no_better_found")
        self.assertEqual(result["replacement_recommendations"], [])
        self.assertTrue(result["can_finalize"])

    def test_replacement_is_not_same_basket_discount(self):
        result = core.review_basket(self.snapshot, self.selection("bundle-juice", 3000), [self.selection("bundle-zero", 2800)], {"bundles_checked": True, "coupons_checked": True}, must_keep=["chicken"])
        self.assertEqual(result["status"], "changes_required")
        candidate = result["replacement_recommendations"][0]
        self.assertEqual(candidate["removed"][0]["id"], "juice")
        self.assertEqual(candidate["added"][0]["id"], "zero")
        self.assertEqual(candidate["price_delta_cents"], -200)

    def test_equivalent_coupon_quote_and_unknown_delta(self):
        result = core.review_basket(self.snapshot, self.selection("unknown", 3000), [self.selection("unknown", 2500)], {"bundles_checked": True, "coupons_checked": True})
        self.assertEqual(result["status"], "better_equivalent_found")
        self.assertIsNone(result["equivalent_recommendations"][0]["nutrition_delta"]["kcal"])

    def test_missing_quote_and_invalid_membership_offer(self):
        missing = self.selection("bundle-zero", 2500); missing.pop("quote")
        result = core.review_basket(self.snapshot, self.selection("bundle-juice", 3000), [missing], {"bundles_checked": True, "coupons_checked": True})
        self.assertEqual(result["status"], "incomplete")
        member = self.selection("bundle-zero", 2500); member["requires_membership_purchase"] = True
        result = core.review_basket(self.snapshot, self.selection("bundle-juice", 3000), [member], {"bundles_checked": True, "coupons_checked": True})
        self.assertEqual(result["status"], "no_better_found")

    def test_required_food_and_nutrition_constraints(self):
        result = core.review_basket(self.snapshot, self.selection("bundle-juice", 3000), [self.selection("bundle-zero", 2500)], {"bundles_checked": True, "coupons_checked": True}, {"carb_g_min": 80}, ["juice"])
        self.assertEqual(result["status"], "no_better_found")
        self.assertTrue(result["rejected"][0]["violations"])

    def test_wrong_store_quote_rejected(self):
        original = self.selection("bundle-zero", 3000)
        original["quote"]["store_code"] = "other"
        with self.assertRaises(ValueError): core.review_basket(self.snapshot, original, [], {})

    def test_truncated_coupon_search_blocks_finalization(self):
        checks = {"bundles_checked": True, "coupons_checked": True, "coupon_variants_truncated": True}
        result = core.review_basket(self.snapshot, self.selection("bundle-zero", 3000), [], checks)
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["can_finalize"])
        self.assertEqual(result["comparison_summary"]["status"], "incomplete")

    def test_summary_keeps_equivalent_and_changed_baskets_separate(self):
        result = core.review_basket(self.snapshot, self.selection("bundle-juice", 3000),
                                   [self.selection("bundle-juice", 2700), self.selection("bundle-zero", 2400)],
                                   {"bundles_checked": True, "coupons_checked": True})
        summary = result["comparison_summary"]
        self.assertEqual(summary["same_contents"]["savings_percent"], 10)
        self.assertEqual(summary["same_contents"]["savings_cents"], 300)
        self.assertFalse(summary["same_contents"]["requires_change_confirmation"])
        changed = summary["lowest_quoted_alternative"]
        self.assertEqual(changed["basket_relation"], "changed_contents")
        self.assertEqual(changed["savings_percent"], 20)
        self.assertTrue(changed["requires_change_confirmation"])
        self.assertEqual(changed["removed"][0]["id"], "juice")
        self.assertEqual(changed["nutrition"], result["replacement_recommendations"][0]["nutrition"])

    def test_summary_unknown_nutrition_does_not_become_zero(self):
        result = core.review_basket(self.snapshot, self.selection("unknown", 3000),
                                   [self.selection("unknown", 2500)],
                                   {"bundles_checked": True, "coupons_checked": True})
        summary = result["comparison_summary"]
        self.assertIsNone(summary["same_contents"]["nutrition_delta"]["kcal"])
        self.assertIn("kcal", summary["same_contents"]["unknown_nutrients"])
        self.assertEqual(summary["same_contents"]["savings_percent"], 16.67)

    def test_summary_explicitly_reports_no_savings(self):
        result = core.review_basket(self.snapshot, self.selection("bundle-zero", 3000),
                                   [self.selection("bundle-juice", 3300)],
                                   {"bundles_checked": True, "coupons_checked": True})
        summary = result["comparison_summary"]
        self.assertEqual(summary["status"], "no_verified_savings")
        self.assertIsNone(summary["lowest_quoted_alternative"])


if __name__ == "__main__":
    unittest.main()
