"""Synthetic coupons only. No network, account, real redemption, or real ledger."""
import copy
from datetime import timedelta
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo import core, sample_profile
import test_customer_scenarios as fixtures
import mcp_adapter as adapter


class CouponHandoffTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.CouponScenarios(); self.f.setUp()
        self.requests = self.f.requests([fixtures.coupon()])
        self.key = self.requests["requests"][0]["key"]
        self.quotes = self.f.quotes(self.requests, [
            {"price": 2600, "takeWayList": [{"code": "TEST-LOCKER"}]},
            {"price": 1900, "discount": 700, "takeWayList": [{"code": "TEST-LOCKER"}]},
        ])

    def prepare(self, quotes=None, fulfillment=None):
        return adapter.prepare_order(quotes or self.quotes, self.key, fulfillment or {"takeWayCode": "TEST-LOCKER"})

    def test_coupon_and_product_preserved_without_mutating_quote(self):
        original = copy.deepcopy(self.quotes)
        prepared = self.prepare()
        q = prepared["quotes"][0]
        self.assertEqual(q["order_payload"]["items"], q["calculate_payload"]["items"])
        self.assertEqual(q["order_payload"]["items"][0]["couponCode"], "FAKE-A")
        self.assertEqual(q["total_cents"], 1900)
        self.assertEqual(self.quotes, original)

    def test_cannot_override_coupon_sku_price_or_membership(self):
        for field in ("items", "couponCode", "storeCode", "withOrder", "needTableware", "price"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.prepare(fulfillment={"takeWayCode": "TEST-LOCKER", field: "CHANGE"})

    def test_pickup_must_be_from_current_price_response(self):
        with self.assertRaises(ValueError): self.prepare(fulfillment={"takeWayCode": "INVENTED"})
        self.quotes["quotes"][0]["take_way_options"] = []
        with self.assertRaises(ValueError): self.prepare()

    def test_partial_empty_or_double_coupon_rejected(self):
        for items in ([{"quantity": 1, "couponId": "A"}], [{"quantity": 1, "couponId": "", "couponCode": ""}],
                      [{"quantity": 2, "couponId": "A", "couponCode": "C"}],
                      [{"quantity": 1, "couponId": "A", "couponCode": "C"}] * 2):
            with self.subTest(items=items), self.assertRaises(ValueError): core.validate_coupon_items(items)

    def test_half_bound_coupon_blocked_before_price_request(self):
        self.f.snapshot["items"][0]["mcp_item"]["couponId"] = "HALF"
        result = self.f.requests([])
        self.assertEqual(result["requests"], [])
        self.assertTrue(result["blocked_candidates"])

    def test_tampered_pricing_payload_rejected(self):
        self.quotes["quotes"][0]["calculate_payload"]["items"][0].pop("couponCode")
        with self.assertRaises(ValueError): self.prepare()

    def test_stale_and_future_price_rejected(self):
        for minutes in (-16, 2):
            q = copy.deepcopy(self.quotes)
            q["quotes"][0]["quoted_at"] = (core.datetime.now(core.CN_TIME) + timedelta(minutes=minutes)).isoformat()
            with self.subTest(minutes=minutes), self.assertRaises(ValueError): self.prepare(q)

    def test_empty_wallet_still_prepares_full_price_order(self):
        req = self.f.requests([])
        q = self.f.quotes(req, [{"price": 2600, "takeWayList": [{"code": "TEST-LOCKER"}]}])
        result = self.prepare(q)["quotes"][0]
        self.assertEqual(result["total_cents"], 2600)
        self.assertNotIn("couponId", result["order_payload"]["items"][0])

    def test_threshold_units_and_unapplied_status(self):
        hint = adapter.extract_threshold_gap({"data": {"enjoyable": {"balance": 350, "realDiscount": "12.00", "amountType": 3}}})
        self.assertEqual(hint["balance_cents"], 350)
        self.assertEqual(hint["potential_discount_cents"], 1200)
        self.assertFalse(hint["applied"])

    def test_missing_potential_discount_remains_unknown(self):
        hint = adapter.extract_threshold_gap({"data": {"enjoyable": {"balance": 0}}})
        self.assertIsNone(hint["potential_discount_cents"])

    def test_bad_optional_threshold_does_not_discard_price(self):
        result = self.f.quotes(self.requests, [{"price": 2600}, {"price": 1900, "enjoyable": {"balance": -1}}])
        self.assertEqual(result["quotes"][0]["total_cents"], 1900)
        self.assertIn("promotion_hint_warning", result["quotes"][0])
        self.assertEqual(result["quote_failures"], [])

    def test_promotion_hint_does_not_reduce_payable(self):
        result = self.f.quotes(self.requests, [{"price": 2600}, {"price": 2600, "enjoyable": {"balance": 350, "realDiscount": 12}}])
        self.assertEqual(result["quotes"][0]["total_cents"], 2600)

    def test_delivery_preserves_coupon_and_requires_selected_address(self):
        self.f.snapshot["parameters"].update(orderType=2, beType=2, beCode="TEST-DELIVERY")
        requests = self.f.requests([fixtures.coupon()])
        quotes = self.f.quotes(requests, [{"price": 3100}, {"price": 2400, "deliveryPrice": 500}])
        prepared = self.prepare(quotes, {"addressId": "SYNTHETIC-ADDRESS", "remark": "测试"})
        self.assertEqual(prepared["quotes"][0]["order_payload"]["items"][0]["couponCode"], "FAKE-A")
        with self.assertRaises(ValueError): self.prepare(quotes)

    def pipeline(self, store):
        profile = sample_profile(core.today())
        profile["defaults"].update(store_code="TEST", context=self.f.snapshot["context"])
        store.configure(profile)
        wanted = core.recommend(store, core.today(), "lunch", self.f.snapshot)
        coupons = adapter.normalize_store_coupons(fixtures.response([fixtures.coupon()]), self.f.snapshot["parameters"])
        requests = adapter.price_requests(self.f.snapshot, wanted, coupons)
        quotes = self.f.quotes(requests, [{"price": 2600, "takeWayList": [{"code": "TEST-LOCKER"}]}, {"price": 1900, "takeWayList": [{"code": "TEST-LOCKER"}]}])
        prepared = self.prepare(quotes)
        target = store.target(core.today(), "lunch")["target"]
        chosen = {"items": self.f.items, "quote": prepared["quotes"][0]}
        review = core.review_basket(self.f.snapshot, chosen, [], adapter.review_checks(prepared, True, True), target)
        manifest = store.preview(core.today(), "lunch", self.f.snapshot, prepared, self.key, review)
        return prepared, manifest

    def test_complete_coupon_preview_authorize_is_single_use(self):
        with tempfile.TemporaryDirectory() as temp:
            store = core.Store(Path(temp) / "test.sqlite3")
            try:
                prepared, manifest = self.pipeline(store)
                result = store.authorize(manifest["confirmation_id"], prepared)
                self.assertEqual(result["create_once_payload"]["items"][0]["couponCode"], "FAKE-A")
                self.assertEqual(result["total_cents"], 1900)
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM bills").fetchone()[0], 0)
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM intake").fetchone()[0], 0)
                with self.assertRaises(ValueError): store.authorize(manifest["confirmation_id"], prepared)
            finally: store.db.close()

    def test_coupon_invalidated_or_price_changed_requires_new_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = core.Store(Path(temp) / "test.sqlite3")
            try:
                prepared, manifest = self.pipeline(store)
                failed = copy.deepcopy(prepared); failed["quotes"] = []
                with self.assertRaises(ValueError): store.authorize(manifest["confirmation_id"], failed)
                changed = copy.deepcopy(prepared); changed["quotes"][0]["total_cents"] = 2600
                with self.assertRaises(ValueError): store.authorize(manifest["confirmation_id"], changed)
                self.assertEqual(store.authorize(manifest["confirmation_id"], prepared)["total_cents"], 1900)
            finally: store.db.close()


if __name__ == "__main__":
    unittest.main()
