"""Synthetic customer repro: singles ¥50 vs equivalent bundle ¥37.50; no account calls."""
import copy
from pathlib import Path
import tempfile
import unittest

import test_catalog_builder as fixtures
import test_customer_scenarios as customers
import catalog_builder as builder
import fit_core as core
import mcp_adapter as adapter
from demo import sample_profile


class BundleFirstTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DetailCatalogTests(); self.f.setUp()
        f = self.f
        f.menu["data"]["meals"]["fries"] = {"name": "中薯条"}
        f.selection["items"] = [{"product_code": "burger", "category": "main"}, {"product_code": "fries", "category": "side"}, {"product_code": "sprite", "category": "drink"}]
        f.cache["responses"].update({
            "burger": fixtures.response({"code": "burger", "name": "测试汉堡", "rounds": []}),
            "fries": fixtures.response({"code": "fries", "name": "中薯条", "rounds": []}),
            "sprite": fixtures.response({"code": "sprite", "name": "雪碧", "rounds": [fixtures.selected_round(1, [fixtures.choice("sprite-small", "雪碧（小杯）", 1, 1)])]}),
        })

    def setup_store(self, folder, preserve=True):
        store = core.Store(Path(folder) / "test.sqlite3")
        profile = sample_profile(core.today())
        profile["defaults"].update(store_code="TEST", context=self.f.selection["context"], required_ids=["burger", "fries", "sprite"] if preserve else [])
        for template in profile["templates"].values(): template["meals"]["lunch"].update(target={}, budget_cents=6000)
        store.configure(profile)
        return store

    def quotes(self, store, snapshot):
        wanted = core.recommend(store, core.today(), "lunch", snapshot)
        requests = adapter.price_requests(snapshot, wanted)
        recipes = {c["key"]: c for c in wanted["quote_requests"]}
        responses = {r["request_id"]: fixtures.response({"price": 3750 if recipes[r["key"]]["ordering_mode"] == "bundle" else 5000, "takeWayList": [{"code": "locker-out"}]}) for r in requests["requests"]}
        return adapter.normalize_quotes(requests, responses)

    def test_discovery_queries_bundle_even_if_agent_only_supplies_singles(self):
        original = copy.deepcopy(self.f.selection)
        requests = builder.detail_requests(self.f.selection, menu=self.f.menu, now=self.f.now)
        self.assertIn("combo", {r["request_id"] for r in requests["requests"]})
        self.assertEqual(self.f.selection, original)

    def test_builder_automatically_adds_discovered_bundle(self):
        snapshot = self.f.build()
        self.assertIn("combo", snapshot["bundle_comparison"]["built_codes"])
        self.assertTrue(any(i["category"] == "bundle" for i in snapshot["items"]))

    def test_missing_bundle_detail_blocks_review_even_if_agent_says_checked(self):
        self.f.cache["responses"].pop("combo")
        snapshot = self.f.build()
        with tempfile.TemporaryDirectory() as tmp:
            store = self.setup_store(tmp)
            try:
                quotes = self.quotes(store, snapshot)
                selected = {"items": [{"id": x, "quantity": 1} for x in ("burger", "fries", "sprite")], "quote": quotes["quotes"][0]}
                review = core.review_basket(snapshot, selected, [], adapter.review_checks(quotes, True, True))
                self.assertEqual(review["status"], "incomplete")
                self.assertEqual(review["bundle_readiness"]["missing_detail_codes"], ["combo"])
                with self.assertRaises(ValueError): store.preview(core.today(), "lunch", snapshot, quotes, selected["quote"]["key"], review)
            finally: store.db.close()

    def test_missing_bundle_quote_cannot_be_hidden_by_checked_flags(self):
        snapshot = self.f.build()
        with tempfile.TemporaryDirectory() as tmp:
            store = self.setup_store(tmp)
            try:
                quotes = self.quotes(store, snapshot)
                key = core.candidate_key([{"id": x, "quantity": 1} for x in ("burger", "fries", "sprite")])
                single = next(q for q in quotes["quotes"] if q["key"] == key)
                basket = {"items": [{"id": x, "quantity": 1} for x in ("burger", "fries", "sprite")], "quote": single}
                review = core.review_basket(snapshot, basket, [], adapter.review_checks(quotes, True, True), must_keep=["burger", "fries", "sprite"])
                self.assertFalse(review["can_finalize"])
                self.assertEqual(review["bundle_readiness"]["unquoted_feasible_bundle_codes"], ["combo"])
            finally: store.db.close()

    def test_equivalent_bundle_preferred_and_full_checkout_uses_bundle_sku(self):
        snapshot = self.f.build()
        with tempfile.TemporaryDirectory() as tmp:
            store = self.setup_store(tmp)
            try:
                quotes = self.quotes(store, snapshot)
                key = core.recommend(store, core.today(), "lunch", snapshot, quotes)["preferred"]["key"]
                quotes = adapter.prepare_order(quotes, key, {"takeWayCode": "locker-out"})
                result = core.recommend(store, core.today(), "lunch", snapshot, quotes)
                self.assertEqual(result["preferred"]["total_cents"], 3750)
                self.assertEqual(result["preferred"]["ordering_mode"], "bundle")
                baseline_items = [{"id": x, "quantity": 1} for x in ("burger", "fries", "sprite")]
                baseline_key = core.candidate_key(baseline_items)
                baseline = {"items": baseline_items, "quote": next(q for q in quotes["quotes"] if q["key"] == baseline_key)}
                alternative = {"items": result["preferred"]["items"], "quote": result["preferred"]["quote"]}
                review = core.review_basket(snapshot, baseline, [alternative], adapter.review_checks(quotes, True, True), must_keep=["burger", "fries", "sprite"], exact_contents=True)
                self.assertEqual(review["status"], "better_equivalent_found")
                self.assertEqual(review["equivalent_recommendations"][0]["price_delta_cents"], -1250)
                preview = store.preview(core.today(), "lunch", snapshot, quotes, key, review)
                payload = store.authorize(preview["confirmation_id"], quotes)["create_once_payload"]
                self.assertEqual(payload["items"][0]["productCode"], "combo")
                self.assertEqual(len(payload["items"]), 1)
            finally: store.db.close()

    def test_bundle_is_preferred_without_hiding_cheaper_single(self):
        snapshot = self.f.build()
        with tempfile.TemporaryDirectory() as tmp:
            store = self.setup_store(tmp, preserve=False)
            try:
                quotes = self.quotes(store, snapshot)
                # Simulated: one burger costs less than a full meal. Do not call
                # it an equivalent substitute; retain both distinct choices.
                key = core.candidate_key([{"id": "burger", "quantity": 1}])
                next(q for q in quotes["quotes"] if q["key"] == key)["total_cents"] = 1500
                result = core.recommend(store, core.today(), "lunch", snapshot, quotes)
                self.assertEqual(result["choices"][0]["ordering_mode"], "bundle")
                self.assertEqual(result["cheapest"]["total_cents"], 1500)
            finally: store.db.close()

    def test_single_item_only_policy_requires_reason_and_does_not_add_bundles(self):
        self.f.selection["bundle_policy"] = "single_items_only"
        with self.assertRaises(ValueError): self.f.build()
        self.f.selection["bundle_policy_reason"] = "用户明确只单点这些商品，不加套餐"
        snapshot = self.f.build()
        self.assertFalse(any(i["category"] == "bundle" for i in snapshot["items"]))
        self.assertEqual(snapshot["bundle_comparison"]["required_codes"], [])

    def test_drink_only_does_not_add_a_meal(self):
        self.f.selection["items"] = [{"product_code": "sprite", "category": "drink"}]
        self.assertEqual(self.f.build()["bundle_comparison"]["required_codes"], [])

    def test_excluded_missing_bundle_does_not_block_recommendation(self):
        self.f.cache["responses"].pop("combo")
        snapshot = self.f.build()
        with tempfile.TemporaryDirectory() as tmp:
            store = self.setup_store(tmp)
            try:
                result = core.recommend(store, core.today(), "lunch", snapshot, excluded=["combo"])
                self.assertEqual(result["bundle_readiness"]["status"], "complete")
            finally: store.db.close()

    def test_related_and_value_bundles_are_discovered_with_bounded_scope(self):
        meals = self.f.menu["data"]["meals"]
        meals.update({"B" + str(i): {"name": "无关套餐" + str(i)} for i in range(10)})
        meals["Z"] = {"name": "测试汉堡三件套"}
        meals["V"] = {"name": "大堡口福三件套"}
        plan = builder.discover_bundles(self.f.menu, self.f.selection)["bundle_plan"]
        self.assertIn("Z", plan["required_codes"]); self.assertIn("V", plan["required_codes"])
        self.assertEqual(len(plan["required_codes"]), 6)
        self.assertGreater(plan["unselected_count"], 0)

    def test_original_named_main_cannot_be_replaced_by_cheaper_other_burger(self):
        self.f.menu["data"]["meals"]["value"] = {"name": "大堡口福三件套"}
        detail = copy.deepcopy(self.f.detail); detail["code"] = "value"
        detail["rounds"][0]["choices"] = [fixtures.choice("chicken", "合成鸡排堡", 1, 1)]
        self.f.cache["responses"]["value"] = fixtures.response(detail)
        self.f.nutrition["data"].append(fixtures.row("合成鸡排堡", 400, 25, 40, 15))
        snapshot = self.f.build()
        with tempfile.TemporaryDirectory() as tmp:
            store = self.setup_store(tmp)
            try:
                wanted = core.recommend(store, core.today(), "lunch", snapshot)
                catalog = core.validate_snapshot(snapshot, core.today())
                self.assertTrue(any("combo" in core.bundle_codes(c, catalog) for c in wanted["quote_requests"]))
                self.assertFalse(any("value" in core.bundle_codes(c, catalog) for c in wanted["quote_requests"]))
            finally: store.db.close()

    def test_quote_limit_reserves_bundle_family_and_original_basket(self):
        snapshot = self.f.build()
        catalog = core.validate_snapshot(snapshot, core.today())
        singles = []
        for n in range(20):
            root = copy.deepcopy(catalog["burger"]); root["id"] = "single-" + str(n)
            catalog[root["id"]] = root
            singles.append(core.aggregate([{"id": root["id"], "quantity": 1}], catalog))
        bundle = core.aggregate([{"id": "combo", "quantity": 1}], catalog)
        original = singles[-1]
        snapshot["requested_baskets"] = [{"items": original["items"]}]
        selected = core.quote_shortlist(singles + [bundle], snapshot, catalog, True)
        self.assertEqual(len(selected), 12)
        self.assertIn(bundle["key"], {c["key"] for c in selected})
        self.assertIn(original["key"], {c["key"] for c in selected})

    def test_old_snapshot_without_menu_discovery_cannot_preview(self):
        snapshot = self.f.build()
        with tempfile.TemporaryDirectory() as tmp:
            store = self.setup_store(tmp)
            try:
                quotes = self.quotes(store, snapshot)
                snapshot.pop("bundle_comparison")
                with self.assertRaisesRegex(ValueError, "套餐发现记录"):
                    store.preview(core.today(), "lunch", snapshot, quotes, quotes["quotes"][0]["key"], {"can_finalize": True})
            finally: store.db.close()

    def test_over_budget_bundle_does_not_override_budget(self):
        snapshot = self.f.build()
        with tempfile.TemporaryDirectory() as tmp:
            store = self.setup_store(tmp)
            try:
                quotes = self.quotes(store, snapshot)
                bundle_key = core.candidate_key([{"id": "combo", "quantity": 1}])
                next(q for q in quotes["quotes"] if q["key"] == bundle_key)["total_cents"] = 7000
                result = core.recommend(store, core.today(), "lunch", snapshot, quotes)
                self.assertEqual(result["preferred"]["ordering_mode"], "a_la_carte")
                self.assertEqual(result["preferred"]["total_cents"], 5000)
                self.assertIsNotNone(result["bundle_fallback_reason"])
                self.assertEqual(result["bundle_readiness"]["status"], "complete")
            finally: store.db.close()


class ZeroQuoteTests(unittest.TestCase):
    def setUp(self):
        self.f = customers.CouponScenarios(); self.f.setUp()

    def test_zero_quote_quarantined_normal_price_preserved(self):
        req = self.f.requests([customers.coupon()])
        result = self.f.quotes(req, [{"price": 2600}, {"price": 0}])
        self.assertEqual(result["quotes"][0]["total_cents"], 2600)
        self.assertEqual(result["quote_failures"][0]["reason"], "zero_price_requires_verification")
        self.assertFalse(result["comparison_complete"])

    def test_coupon_field_does_not_automatically_prove_a_free_order(self):
        req = self.f.requests([customers.coupon()])
        result = self.f.quotes(req, [{"price": 0}, {"price": 0, "discount": 2600, "productPrice": 2600}])
        self.assertEqual(result["quotes"], [])
        self.assertEqual(len(result["quote_failures"]), 2)

    def test_review_rejects_injected_zero_price(self):
        quote = {"key": core.candidate_key(self.f.items), "kind": "official", "store_code": "TEST", "context": self.f.snapshot["context"], "total_cents": 0, "quoted_at": core.datetime.now(core.CN_TIME).isoformat()}
        with self.assertRaisesRegex(ValueError, "零元"):
            core.review_basket(self.f.snapshot, {"items": self.f.items, "quote": quote}, [], {"bundles_checked": True, "coupons_checked": True})

    def test_prepare_order_rejects_injected_zero_price(self):
        req = self.f.requests([])
        quotes = self.f.quotes(req, [{"price": 2600, "takeWayList": [{"code": "locker-out"}]}])
        quotes["quotes"][0]["total_cents"] = 0
        with self.assertRaisesRegex(ValueError, "零元"):
            adapter.prepare_order(quotes, quotes["quotes"][0]["key"], {"takeWayCode": "locker-out"})


if __name__ == "__main__": unittest.main()
