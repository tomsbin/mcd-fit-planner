"""Cross-module regressions derived from the WorkBuddy retest and competitor contract review."""
import copy
from datetime import timedelta
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo import sample_profile
import test_catalog_builder as fixtures
import test_customer_scenarios as customer_fixtures
import catalog_builder as builder
import fit_core as core
import mcp_adapter as adapter
import nutrition_report as nr
import nutrition_support as ns

response = fixtures.response
coupon = customer_fixtures.coupon


class V06AdapterTests(unittest.TestCase):
    def test_same_coupon_id_multiple_codes_are_distinct_not_schema_error(self):
        """同一券ID多张券码：保留每个实际计价参数，不把它当成重复错误。"""
        a, b = coupon("A"), coupon("A"); b["couponCode"] = "SECOND-CODE"
        parsed = adapter.normalize_store_coupons(response([a, b]), {"storeCode": "TEST", "orderType": 1, "beType": 1})
        self.assertEqual(len(parsed["coupons"]), 2)

    def test_conflicting_duplicate_coupon_code_not_silently_first_wins(self):
        a, b = coupon("A"), coupon("A", product="OTHER")
        parsed = adapter.normalize_store_coupons(response([a, b]), {"storeCode": "TEST", "orderType": 1, "beType": 1})
        self.assertEqual(parsed["coupons"], [])
        self.assertEqual(len(parsed["unresolved"]), 1)

    def test_existing_coupon_on_another_item_prevents_two_coupon_order(self):
        """另一项已有券：不能给主食再挂第二张券。"""
        fixture = customer_fixtures.CouponScenarios(); fixture.setUp()
        extra = {"id": "other", "name": "测试配餐", "category": "side", "nutrition_source": "official", "evidence": {"nutrition_name": "测试配餐", "serving": "一份"}, "nutrition": {"kcal": 0}, "mcp_item": {"productCode": "999", "quantity": 1, "couponId": "OLD", "couponCode": "OLD-CODE"}}
        fixture.snapshot["items"].append(extra)
        fixture.recommendation["quote_requests"] = [core.aggregate([{"id": "main", "quantity": 1}, {"id": "other", "quantity": 1}], {i["id"]: i for i in fixture.snapshot["items"]})]
        self.assertEqual(len(fixture.requests([coupon()])["requests"]), 1)

    def test_multiple_coupon_skus_are_explicitly_blocked_before_network(self):
        """多券根商品同单：明确阻止构造请求，不用服务器报错来消耗调用。"""
        fixture = customer_fixtures.CouponScenarios(); fixture.setUp()
        fixture.snapshot["items"][0]["mcp_item"].update(couponId="A", couponCode="CODE")
        fixture.recommendation["quote_requests"] = [core.aggregate([{"id": "main", "quantity": 2}], {i["id"]: i for i in fixture.snapshot["items"]})]
        result = fixture.requests([])
        self.assertEqual(result["requests"], [])
        self.assertTrue(result["blocked_candidates"])

    def test_size_and_container_wrappers_only_not_sugar_annotation(self):
        """只移除杯型/容器的格式括号，不把无糖配方归成普通饮料。"""
        self.assertEqual(adapter.canonical_name("雪碧（小杯）"), adapter.canonical_name("雪碧小杯"))
        self.assertEqual(adapter.canonical_name("100% 苹果汁(盒装)"), adapter.canonical_name("100%苹果汁"))
        self.assertNotEqual(adapter.canonical_name("可乐（无糖）"), adapter.canonical_name("可乐"))

    def test_review_checks_populates_all_fields_without_claiming_queries(self):
        """checks自动填全，但未实际查询不能自动说查完。"""
        checks = adapter.review_checks({"quote_failures": [{"reason": "error"}], "coupon_comparison": {"unresolved": [{}]}}, bundles_checked=True)
        self.assertFalse(checks["coupons_checked"])
        self.assertEqual(checks["quote_failure_count"], 1)
        self.assertEqual(checks["coupon_unresolved_count"], 1)
        self.assertIn("coupon_pages_complete", checks)


class V06PipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DetailCatalogTests(); self.fixture.setUp()

    def test_detail_to_recommend_quote_review_nutrition_and_preview(self):
        """详情自动组装→营养筛选→计价→直接review→整餐报告→预览，无人工补组件/合并quote字段。"""
        f = self.fixture
        f.selection["items"][0]["expand"] = True
        f.selection["items"].append({"product_code": "burger", "category": "main"})
        f.cache["responses"]["burger"] = response({"code": "burger", "name": "测试汉堡", "rounds": []})
        target = {"kcal_min": 500, "kcal_max": 750, "protein_g_min": 30, "fat_g_max": 25}
        snapshot = f.build(target=target)
        with tempfile.TemporaryDirectory() as temp:
            store = core.Store(Path(temp) / "test.sqlite3")
            try:
                profile = sample_profile(core.today())
                profile["defaults"].update(store_code="TEST", context="测试自取", required_ids=["burger"])
                store.configure(profile)
                wanted = core.recommend(store, core.today(), "lunch", snapshot)
                self.assertTrue(wanted["quote_requests"])
                self.assertTrue(any(len(c["items"]) == 1 and c["items"][0]["id"] != "burger" for c in wanted["quote_requests"]))
                requests = adapter.price_requests(snapshot, wanted)
                # Independent simulated cashier totals for these fixture recipes.
                amounts = {500: 2600, 560: 2400, 660: 2500}
                nutrients = {c["key"]: c["nutrition"]["kcal"] for c in wanted["quote_requests"]}
                replies = {r["request_id"]: response({"price": amounts[nutrients[r["key"]]]}) for r in requests["requests"]}
                quotes = adapter.normalize_quotes(requests, replies)
                for quote in quotes["quotes"]:
                    quote["order_payload"] = {**copy.deepcopy(quote["calculate_payload"]), "takeWayCode": "TEST-LOCKER"}
                result = core.recommend(store, core.today(), "lunch", snapshot, quotes)
                baseline = {"items": result["closest"]["items"], "quote": result["closest"]["quote"]}
                alternative = {"items": result["cheapest"]["items"], "quote": result["cheapest"]["quote"]}
                checks = adapter.review_checks(quotes, True, True)
                review = core.review_basket(snapshot, baseline, [alternative], checks, store.target(core.today(), "lunch")["target"], must_keep=["burger"])
                self.assertEqual(review["status"], "changes_required")
                manifest = store.preview(core.today(), "lunch", snapshot, quotes, result["cheapest"]["key"], review)
                self.assertEqual(manifest["total_cents"], 2400)
                report = nr.meal_report(snapshot, {"items": manifest["items"]}, store)
                self.assertEqual(report["total"]["nutrition"]["kcal"], 560)
                self.assertEqual(report["personal_assessment"]["status"], "within")
            finally:
                store.db.close()

    def test_unknown_side_can_be_web_supplemented_then_re_ranked(self):
        """先未知玉米杯→补网页来源→自动重建候选，不用手写整套快照。"""
        f = self.fixture
        f.selection["items"][0]["expand"] = True
        f.nutrition["data"] = [r for r in f.nutrition["data"] if r["productName"] != "玉米杯小杯"]
        snapshot = f.build(target={"kcal_min": 550, "kcal_max": 600}, max_variants=2)
        missing = next(i for i in snapshot["items"] if i["name"] == "玉米杯小杯")
        record = {"item_id": missing["id"], "match": {"name": missing["name"], "serving": missing["serving"]}, "source": "official_web", "basis": "per_serving", "nutrition": {"kcal": 60, "protein_g": 2, "carb_g": 8, "fat_g": 1}, "evidence": {"url": "https://www.mcdonalds.com.cn/product/Corn-Cup/", "product_name": "测试合成玉米值，非真实官网数据", "serving": "测试标准小杯", "region": "CN", "checked_at": core.today(), "method": "仅验证来源格式的合成值，不作为真实营养证据"}}
        enriched = ns.supplement(snapshot, [record], core.today())["snapshot"]
        rebuilt = f.build(target={"kcal_min": 550, "kcal_max": 600}, max_variants=2, enriched=enriched)
        self.assertIn(560, [f.total(rebuilt, i["id"])["nutrition"]["kcal"] for i in rebuilt["items"] if i.get("available")])

    def test_same_physical_meal_via_coupon_sku_is_equivalent(self):
        """券套餐SKU不同但全部实际组件相同：识别为原样更省。"""
        f = self.fixture
        f.selection["items"].append({"product_code": "voucher", "category": "bundle", "coupon": {"coupon_id": "A", "coupon_code": "CODE"}})
        detail = copy.deepcopy(f.detail); detail["code"] = "voucher"
        f.cache["responses"]["voucher"] = response(detail)
        at = f.now
        coupons = adapter.normalize_store_coupons(response([{"couponId": "A", "couponCode": "CODE", "title": "测试同餐品券", "tradeDateTime": (at - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S") + " - " + (at + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"), "products": [{"productCode": "voucher"}]}]), f.selection["parameters"])
        snapshot = f.build(coupons=coupons)
        now = core.datetime.now(core.CN_TIME).isoformat()
        def selected(identifier, amount):
            items = [{"id": identifier, "quantity": 1}]
            return {"items": items, "quote": {"key": core.candidate_key(items), "kind": "official", "store_code": "TEST", "context": "测试自取", "total_cents": amount, "quoted_at": now}}
        result = core.review_basket(snapshot, selected("combo", 3000), [selected("voucher", 2500)], {"bundles_checked": True, "coupons_checked": True}, exact_contents=True)
        self.assertEqual(result["status"], "better_equivalent_found")
        self.assertEqual(result["equivalent_recommendations"][0]["nutrition_delta"]["kcal"], 0)

    def test_same_price_goal_improvement_is_not_hidden_as_no_recommendation(self):
        """同价换无糖可乐能符合当天目标：独立返回营养建议，不谎称省钱。"""
        from demo import sample_snapshot
        snapshot = sample_snapshot()
        def selected(identifier):
            items = [{"id": identifier, "quantity": 1}]
            return {"items": items, "quote": {"key": core.candidate_key(items), "kind": "demo", "store_code": "DEMO", "context": "演示自取", "total_cents": 3000, "quoted_at": "2026-10-14T12:00:00+08:00"}}
        result = core.review_basket(snapshot, selected("bundle-juice"), [selected("bundle-zero")], {"bundles_checked": True, "coupons_checked": True}, {"kcal_max": 650}, budget_cents=3500)
        self.assertEqual(result["status"], "no_better_found")
        self.assertEqual(result["nutrition_recommendation_status"], "goal_improvement_available")
        self.assertEqual(result["nutrition_recommendations"][0]["price_delta_cents"], 0)
        high_carb = core.review_basket(snapshot, selected("bundle-juice"), [selected("bundle-zero")], {"bundles_checked": True, "coupons_checked": True}, {"carb_g_min": 80})
        self.assertEqual(high_carb["nutrition_recommendations"], [])

    def test_sanitized_real_capture_builds_complete_recipes_without_manual_mapping(self):
        """真实捕获回放：不手填serving/components，全部组件求和。"""
        data = core.read_json(ROOT / "tests/fixtures/official-detail-capture.json")
        selection = {"day": "2026-10-14", "meal": "dinner", "context": "回放", "parameters": {"storeCode": "STORE_A", "orderType": 1, "beType": 1}, "items": [{"product_code": "9900005466", "category": "bundle", "expand": True, "expand_rounds": ["2"]}]}
        cache = {"scope": builder.scope(selection), "responses": data["details"]}
        snapshot = builder.build_catalog(data["menu"], data["nutrition"], selection, cache, now=core.datetime(2026, 10, 14, 19, 42, tzinfo=core.CN_TIME))
        catalog = core.validate_snapshot(snapshot, "2026-10-14")
        totals = [core.aggregate([{"id": i["id"], "quantity": 1}], catalog)["nutrition"]["kcal"] for i in snapshot["items"] if i.get("available")]
        self.assertIn(949, totals)
        self.assertIn(713, totals)
        self.assertIn(None, totals)

    def test_unknown_original_is_not_claimed_worse_than_known_alternative(self):
        from demo import sample_snapshot
        snapshot = sample_snapshot()
        def selected(identifier, price):
            items = [{"id": identifier, "quantity": 1}]
            return {"items": items, "quote": {"key": core.candidate_key(items), "kind": "demo", "store_code": "DEMO", "context": "演示自取", "total_cents": price, "quoted_at": "2026-10-14T12:00:00+08:00"}}
        review = core.review_basket(snapshot, selected("unknown", 1000), [selected("bundle-zero", 2000)], {"bundles_checked": True, "coupons_checked": True}, {"kcal_max": 650})
        self.assertEqual(review["nutrition_recommendation_status"], "target_fit_alternative_available")
        candidate = review["nutrition_recommendations"][0]
        self.assertFalse(candidate["decision"]["nutrition_goal_improved"])
        self.assertIsNone(candidate["nutrition_delta"]["kcal"])


if __name__ == "__main__":
    unittest.main()
