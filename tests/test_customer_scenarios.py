"""Customer acceptance scenarios. Coupon prices are simulated server responses, not live promotions."""
import copy
from datetime import timedelta
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo import core, sample_profile, sample_snapshot
import mcp_adapter as adapter
import nutrition_report as nr


def response(data, offset_minutes=0):
    at = core.datetime.now(core.CN_TIME) + timedelta(minutes=offset_minutes)
    return {"success": True, "code": 200, "datetime": at.strftime("%Y-%m-%d %H:%M:%S"), "data": data}


def coupon(identifier="A", title="模拟立减券", product="123", start=None, end=None):
    at = core.datetime.now(core.CN_TIME)
    return {"couponId": identifier, "couponCode": "FAKE-" + identifier, "title": title, "products": [{"productCode": product, "productName": "模拟主食"}], "tradeDateTime": (start or at - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S") + " - " + (end or at + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")}


class CouponScenarios(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"kind": "official", "day": core.today(), "meal": "lunch", "store_code": "TEST", "context": "模拟自取", "parameters": {"storeCode": "TEST", "orderType": 1, "beType": 1, "needTableware": False}, "items": [{"id": "main", "name": "模拟主食", "category": "main", "nutrition_source": "official", "evidence": {"nutrition_name": "模拟主食", "serving": "测试标准份"}, "nutrition": {"kcal": 550, "protein_g": 30, "carb_g": 60, "fat_g": 18}, "mcp_item": {"productCode": "123", "quantity": 1}}]}
        self.snapshot["bundle_comparison"] = {"policy": "bundle_first", "required_codes": [], "missing_detail_codes": [], "scope": "合成菜单仅含一个单品，无套餐"}
        self.items = [{"id": "main", "quantity": 1}]
        self.recommendation = {"kind": "official", "day": core.today(), "meal": "lunch", "context_id": "fixture", "quote_requests": [core.aggregate(self.items, {i["id"]: i for i in self.snapshot["items"]})]}

    def requests(self, rows, **kwargs):
        coupons = adapter.normalize_store_coupons(response(rows), self.snapshot["parameters"])
        return adapter.price_requests(self.snapshot, self.recommendation, coupons, **kwargs)

    def quotes(self, requests, data):
        return adapter.normalize_quotes(requests, {r["request_id"]: response(d) if isinstance(d, dict) else d for r, d in zip(requests["requests"], data)})

    def test_empty_wallet_keeps_regular_price(self):
        """无券客户：正常原价，不虚构折扣。"""
        requests = self.requests([])
        self.assertEqual(len(requests["requests"]), 1)
        self.assertEqual(self.quotes(requests, [{"price": 2600}])["quotes"][0]["total_cents"], 2600)

    def test_flat_discount_keeps_verified_coupon_parameters(self):
        """立减券：取服务端应付价，计价券参数保留到预览。"""
        result = self.quotes(self.requests([coupon()]), [{"price": 2600}, {"price": 2100, "discount": 500}])
        quote = result["quotes"][0]
        self.assertEqual(quote["total_cents"], 2100)
        self.assertEqual(quote["calculate_payload"]["items"][0]["couponId"], "A")

    def test_percent_and_special_price_compete_without_guessing_headline(self):
        """折扣券与特价券：选择真实算价更省的那张。"""
        requests = self.requests([coupon("P", "模拟八折券"), coupon("S", "模拟19.9特价券")])
        quotes = self.quotes(requests, [{"price": 3000}, {"price": 2400}, {"price": 1990}])
        self.assertEqual(len(quotes["quotes"]), 1)
        self.assertEqual(quotes["quotes"][0]["total_cents"], 1990)
        self.assertEqual(quotes["successful_variant_count"], 3)

    def test_expired_coupon_never_enters_quote_requests(self):
        """过期券：不能拿宣传价参与推荐。"""
        now = core.datetime.now(core.CN_TIME)
        requests = self.requests([coupon(start=now - timedelta(days=2), end=now - timedelta(days=1))])
        self.assertEqual(len(requests["requests"]), 1)
        self.assertEqual(len(requests["coupon_comparison"]["blocked"]), 1)

    def test_not_started_coupon_never_enters_quote_requests(self):
        """尚未生效的券：今天不能用。"""
        now = core.datetime.now(core.CN_TIME)
        self.assertEqual(len(self.requests([coupon(start=now + timedelta(days=1), end=now + timedelta(days=2))])["requests"]), 1)

    def test_reservation_uses_pickup_time_for_coupon_validity(self):
        """预约取餐：现在有效、取餐时过期的券不使用。"""
        now = core.datetime.now(core.CN_TIME)
        self.snapshot["parameters"]["reservationDate"] = (now + timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(len(self.requests([coupon()])["requests"]), 1)

    def test_unclaimed_display_coupon_has_no_usable_identifier(self):
        """未领取展示券：只有券名不能当作已拥有券，也不自动领券。"""
        requests = self.requests([{"title": "可领模拟半价券"}])
        self.assertEqual(len(requests["requests"]), 1)
        self.assertEqual(len(requests["coupon_comparison"]["unresolved"]), 1)

    def test_unrelated_product_coupon_not_applied(self):
        """指定餐品券：不能挪用到别的汉堡。"""
        self.assertEqual(len(self.requests([coupon(product="OTHER")])["requests"]), 1)

    def test_wrong_store_coupon_context_rejected(self):
        """另一家门店的券：换店必须重查。"""
        coupons = adapter.normalize_store_coupons(response([coupon()]), {"storeCode": "OTHER", "orderType": 1, "beType": 1})
        with self.assertRaises(ValueError): adapter.price_requests(self.snapshot, self.recommendation, coupons)

    def test_pickup_coupon_not_reused_for_delivery(self):
        """到店券不能沿用到外送。"""
        coupons = adapter.normalize_store_coupons(response([coupon()]), self.snapshot["parameters"])
        self.snapshot["parameters"].update(orderType=2, beType=2, beCode="DELIVERY")
        with self.assertRaises(ValueError): adapter.price_requests(self.snapshot, self.recommendation, coupons)

    def test_missing_validity_or_product_mapping_needs_confirmation(self):
        """券信息不全：公开展示缺口，不凭券名算折扣。"""
        row = coupon(); row["tradeDateTime"] = "今天可用"
        self.assertEqual(len(self.requests([row])["coupon_comparison"]["unresolved"]), 1)

    def test_malformed_coupon_does_not_hide_another_valid_coupon(self):
        """一张券有效期类型错误：记录缺口，仍生成另一张有效券。"""
        row = coupon("BAD"); row["tradeDateTime"] = 123
        requests = self.requests([row, coupon("GOOD")])
        self.assertEqual(len(requests["requests"]), 2)
        self.assertEqual(len(requests["coupon_comparison"]["unresolved"]), 1)
        row = coupon(); row["products"] = []
        self.assertEqual(len(self.requests([row])["coupon_comparison"]["unresolved"]), 1)

    def test_coupon_not_stacked_on_preexisting_coupon(self):
        """不可叠券：一项已有券时不自动再加另一张。"""
        self.snapshot["items"][0]["mcp_item"].update(couponId="EXISTING", couponCode="FAKE-OLD")
        self.assertEqual(len(self.requests([coupon()])["requests"]), 1)

    def test_two_identical_meals_only_one_serving_uses_coupon(self):
        """买两份、券一次：一份用券，一份正常计价，不把优惠乘二。"""
        self.recommendation["quote_requests"] = [core.aggregate([{"id": "main", "quantity": 2}], {i["id"]: i for i in self.snapshot["items"]})]
        items = self.requests([coupon()])["requests"][1]["arguments"]["items"]
        self.assertEqual(sum(i["quantity"] for i in items), 2)
        self.assertEqual(sum(i["quantity"] for i in items if "couponId" in i), 1)

    def test_coupon_failure_does_not_hide_successful_regular_price(self):
        """券被服务器拒绝：原价仍可见，但标注优惠计价不完整。"""
        requests = self.requests([coupon()])
        responses = {requests["requests"][0]["request_id"]: response({"price": 2600}), requests["requests"][1]["request_id"]: {"success": False, "code": 600010, "data": {"price": 0}}}
        result = adapter.normalize_quotes(requests, responses)
        self.assertEqual(result["quotes"][0]["total_cents"], 2600)
        self.assertEqual(len(result["quote_failures"]), 1)
        self.assertFalse(result["comparison_complete"])

    def test_coupon_advertisement_not_discount_when_threshold_unmet(self):
        """满减未达门槛：展示价不等于本单折扣。"""
        result = self.quotes(self.requests([coupon(title="模拟满30减10")]), [{"price": 2600}, {"price": 2600, "discount": 0}])
        self.assertEqual(result["quotes"][0]["total_cents"], 2600)

    def test_delivery_and_packing_fees_included_in_budget(self):
        """外送券商品便宜但配送打包贵：按总应付选方案。"""
        self.snapshot["parameters"].update(orderType=2, beType=2, beCode="FAKE-DELIVERY")
        result = self.quotes(self.requests([coupon()]), [{"price": 3100, "productPrice": 2600, "deliveryPrice": 400, "packingPrice": 100}, {"price": 3250, "productPrice": 2100, "deliveryPrice": 1000, "packingPrice": 150}])
        self.assertEqual(result["quotes"][0]["total_cents"], 3100)
        self.assertEqual(result["quotes"][0]["price_breakdown"]["deliveryPrice"], 400)

    def test_negative_or_fractional_total_is_not_a_free_order(self):
        """异常负价/小数分：不误识别为超低价优惠。"""
        for invalid in (-1, 20.5):
            result = self.quotes(self.requests([coupon()]), [{"price": 2600}, {"price": invalid}])
            self.assertEqual(result["quotes"][0]["total_cents"], 2600)

    def test_stale_low_quote_does_not_block_fresh_higher_quote(self):
        """过期低价与新鲜较高价：保留仍有效的报价。"""
        requests = self.requests([coupon()])
        result = adapter.normalize_quotes(requests, {requests["requests"][0]["request_id"]: response({"price": 2600}), requests["requests"][1]["request_id"]: response({"price": 1}, -16)})
        self.assertEqual(result["quotes"][0]["total_cents"], 2600)
        self.assertFalse(result["comparison_complete"])

    def test_missing_response_explicitly_reports_unquoted_variant(self):
        """券请求超时：未返回不能说没有优惠。"""
        requests = self.requests([coupon()])
        result = adapter.normalize_quotes(requests, {requests["requests"][0]["request_id"]: response({"price": 2600})})
        self.assertEqual(result["quote_failures"][0]["reason"], "missing_response")

    def test_request_budget_limits_are_visible(self):
        """很多券时限额枚举：不能宣传遍历了全部优惠。"""
        requests = self.requests([coupon(str(i)) for i in range(10)], max_per_candidate=3, max_requests=3)
        self.assertEqual(len(requests["requests"]), 3)
        self.assertTrue(requests["coupon_comparison"]["truncated"])

    def test_same_price_different_coupons_keep_one_complete_payload(self):
        """同价券：返回唯一方案，不能拼接两张券参数。"""
        result = self.quotes(self.requests([coupon("A"), coupon("B")]), [{"price": 2600}, {"price": 2100}, {"price": 2100}])
        self.assertEqual(len(result["quotes"]), 1)
        self.assertEqual(sum("couponId" in i for i in result["quotes"][0]["calculate_payload"]["items"]), 1)

    def test_duplicate_variant_ids_cannot_overwrite_responses(self):
        """重复请求ID：禁止覆盖另一张券的报价。"""
        requests = self.requests([coupon()])
        requests["requests"][1]["request_id"] = requests["requests"][0]["request_id"]
        with self.assertRaises(ValueError): adapter.normalize_quotes(requests, {})


class CouponCheckoutJourneys(unittest.TestCase):
    def setUp(self):
        fixture = CouponScenarios(); fixture.setUp()
        self.snapshot = fixture.snapshot
        self.temp = tempfile.TemporaryDirectory()
        self.store = core.Store(Path(self.temp.name) / "test.sqlite3")
        self.prepare()

    def prepare(self, quantity=1):
        profile = sample_profile(core.today())
        profile["defaults"].update(store_code="TEST", context="模拟自取")
        profile["templates"]["训练A"]["meals"]["lunch"].update(target={"kcal_min": 500, "kcal_max": 600, "protein_g_min": 25} if quantity == 1 else {"kcal_min": 1000, "kcal_max": 1200, "protein_g_min": 50}, budget_cents=3500 if quantity == 1 else 5500)
        self.snapshot["requested_baskets"] = [{"items": [{"id": "main", "quantity": quantity}]}]
        self.snapshot["required_quantities"] = {"main": quantity}
        self.store.configure(profile)
        request = core.recommend(self.store, core.today(), "lunch", self.snapshot)
        coupons = adapter.normalize_store_coupons(response([coupon()]), self.snapshot["parameters"])
        requests = adapter.price_requests(self.snapshot, request, coupons)
        regular, discounted = (2600, 2100) if quantity == 1 else (5200, 4700)
        replies = {r["request_id"]: response({"price": regular if r["variant"] == "原参数" else discounted}) for r in requests["requests"]}
        self.quotes = adapter.normalize_quotes(requests, replies)
        quote = self.quotes["quotes"][0]
        quote["order_payload"] = {**copy.deepcopy(quote["calculate_payload"]), "takeWayCode": "TEST-LOCKER"}
        self.key = quote["key"]
        items = [{"id": "main", "quantity": quantity}]
        scope = {"kind": "official", "store_code": "TEST", "context": "模拟自取"}
        baseline = {"items": items, "quote": {**quote, **scope, "total_cents": regular}}
        alternative = {"items": items, "quote": {**quote, **scope}}
        self.review = core.review_basket(self.snapshot, baseline, [alternative], {"bundles_checked": True, "coupons_checked": True}, self.store.target(core.today(), "lunch")["target"], budget_cents=3500 if quantity == 1 else 5500, exact_contents=True)

    def tearDown(self):
        self.store.db.close(); self.temp.cleanup()

    def preview(self):
        return self.store.preview(core.today(), "lunch", self.snapshot, self.quotes, self.key, self.review)

    def test_coupon_customer_full_chain_to_single_use_authorization(self):
        """用券全链路：推荐→多价比较→营养汇总→确认标识，只输出参数不下单。"""
        preview = self.preview()
        self.assertEqual(preview["total_cents"], 2100)
        report = nr.meal_report(self.snapshot, {"items": preview["items"]}, self.store)
        self.assertEqual(report["personal_assessment"]["status"], "within")
        authorized = self.store.authorize(preview["confirmation_id"], self.quotes)
        self.assertEqual(authorized["create_once_payload"]["items"][0]["couponId"], "A")
        with self.assertRaises(ValueError): self.store.authorize(preview["confirmation_id"], self.quotes)

    def test_coupon_removed_between_quote_and_preview_is_rejected(self):
        """报价用券，下单漏券：即使金额文件未变也拒绝。"""
        self.quotes["quotes"][0]["order_payload"]["items"][0].pop("couponId")
        with self.assertRaisesRegex(ValueError, "计价"): self.preview()

    def test_cola_size_changed_after_price_is_rejected(self):
        """计价后偷偷改套餐选项：原报价不能授权新配餐。"""
        self.quotes["quotes"][0]["order_payload"]["items"][0]["roundList"] = [{"round": "1", "comboItemList": [{"code": "OTHER", "quantity": 1}]}]
        with self.assertRaises(ValueError): self.preview()

    def test_paid_membership_added_only_to_order_is_rejected(self):
        """计价无会员卡、下单新增卡：拒绝未核价的付费项。"""
        self.quotes["quotes"][0]["order_payload"]["withOrder"] = {"cardId": "FAKE-PAID"}
        with self.assertRaises(ValueError): self.preview()

    def test_tampered_price_request_cannot_change_physical_meal(self):
        """计价文件串餐：价格参数自洽也不能对应错误的营养餐品。"""
        quote = self.quotes["quotes"][0]
        quote["calculate_payload"]["items"][0]["productCode"] = "OTHER"
        quote["calculate_payload_hash"] = core.digest(quote["calculate_payload"])
        quote["order_payload"] = {**copy.deepcopy(quote["calculate_payload"]), "takeWayCode": "TEST"}
        with self.assertRaisesRegex(ValueError, "营养候选"): self.preview()

    def test_two_portions_keep_quantity_with_single_coupon_through_checkout(self):
        """明确要两份：候选、营养、单券计价与预览都保留实际数量。"""
        self.prepare(2)
        preview = self.preview()
        self.assertEqual(preview["total_cents"], 4700)
        self.assertEqual(preview["nutrition"]["kcal"], 1100)
        self.assertEqual(sum(i["quantity"] for i in preview["order_payload"]["items"]), 2)
        self.assertEqual(sum(i["quantity"] for i in preview["order_payload"]["items"] if i.get("couponId")), 1)

    def test_requested_two_portions_never_silently_becomes_one(self):
        """要两份但营养上限只够一份：说明冲突，不偷偷减量。"""
        self.snapshot["required_quantities"] = {"main": 2}
        self.snapshot["requested_baskets"] = [{"items": [{"id": "main", "quantity": 2}]}]
        result = core.recommend(self.store, core.today(), "lunch", self.snapshot)
        self.assertEqual(result["eligible_count"], 0)
        self.assertEqual(result["quote_requests"], [])


class MenuNamingScenarios(unittest.TestCase):
    def row(self, name, kcal=100):
        return {"productName": name, "nutritionDescription": None, "energyKcal": kcal, "energyKj": kcal * 4.184, "protein": 1, "fat": 1, "carbohydrate": 1}

    def selector(self, spec):
        return {"serving": "详情明确的标准份", "spec": spec, "spec_evidence": {"source": "query-meal-detail", "option_code": "TEST"}}

    def test_milk_parentheses_whitespace_normalization(self):
        """真实报告：纯牛奶(盒装)能匹配纯牛奶（盒装）。"""
        values, evidence, source = adapter.match_nutrition([self.row("纯牛奶（盒装）")], {"serving": "标准盒装一盒"}, " 纯牛奶 (盒装) ")
        self.assertEqual(values["kcal"], 100)
        self.assertEqual(source, "official")

    def test_fries_size_from_verified_detail(self):
        """菜单薯条＋详情中份：只匹配中薯，不混入大薯。"""
        values, _, _ = adapter.match_nutrition([self.row("中薯条", 300), self.row("大薯条", 500)], self.selector({"size": "medium"}), "薯条")
        self.assertEqual(values["kcal"], 300)

    def test_corn_small_not_large(self):
        """玉米杯详情小规格：不选大杯。"""
        values, _, _ = adapter.match_nutrition([self.row("小杯玉米杯", 53), self.row("大杯玉米杯", 87)], self.selector({"size": "small"}), "玉米杯")
        self.assertEqual(values["kcal"], 53)

    def test_nuggets_piece_count_changes_nutrition(self):
        """麦乐鸡4块与5块分开匹配。"""
        values, _, _ = adapter.match_nutrition([self.row("麦乐鸡4块", 170), self.row("麦乐鸡5块", 213)], self.selector({"pieces": 5}), "麦乐鸡")
        self.assertEqual(values["kcal"], 213)

    def test_sugar_free_drink_never_matches_sugared(self):
        """只喝无糖：不能因相似名称配到普通可乐。"""
        rows = [self.row("可乐中杯", 147), self.row("无糖可口可乐中杯", 0)]
        self.assertEqual(adapter.match_nutrition(rows, self.selector({"size": "medium"}), "无糖可口可乐")[0]["kcal"], 0)

    def test_missing_size_stays_unknown_with_actionable_warning(self):
        """菜单只写可乐：不知道杯型时先查详情。"""
        values, evidence, _ = adapter.match_nutrition([self.row("可乐中杯", 147)], {"serving": "一杯"}, "可乐")
        self.assertIsNone(values["kcal"])
        self.assertEqual(evidence["match"], "not_found")

    def test_unverified_size_not_accepted_as_official(self):
        """没有详情或用户确认的猜测规格不能当作官方匹配。"""
        selector = {"serving": "中杯", "spec": {"size": "medium"}}
        self.assertEqual(adapter.match_nutrition([self.row("可乐中杯")], selector, "可乐")[1]["match"], "spec_unverified")

    def test_explicit_alias_conflicting_with_size_rejected(self):
        """营养别名指向大杯、实际选中杯：显示规格冲突。"""
        selector = self.selector({"size": "medium"}); selector["nutrition_name"] = "可乐大杯"
        self.assertEqual(adapter.match_nutrition([self.row("可乐大杯")], selector, "可乐")[1]["match"], "spec_conflict")

    def test_normalized_collision_is_ambiguous(self):
        """字符归一后撞名：不能随便选第一条。"""
        self.assertEqual(adapter.match_nutrition([self.row("纯牛奶(盒装)", 100), self.row("纯牛奶（盒装）", 110)], {"serving": "一盒"}, "纯牛奶(盒装)")[1]["match"], "ambiguous")

    def test_real_duplicate_milk_records_deduplicated_if_all_values_identical(self):
        """真实营养表两条相同盒装奶：重复不应变成份量歧义。"""
        row = {"productName": "纯牛奶（盒装）", "nutritionDescription": None, "energyKcal": "129", "energyKj": "541", "protein": "7", "fat": "7", "carbohydrate": "10", "sodium": "73", "calcium": "231"}
        values, evidence, source = adapter.match_nutrition([row, dict(row)], {"serving": "盒装一盒，菜单确认"}, "纯牛奶(盒装)")
        self.assertEqual(source, "official")
        self.assertEqual(values["kcal"], 129)
        self.assertEqual(evidence["identical_duplicates_removed"], 1)

    def test_missing_serving_fails_before_recommendation(self):
        """营养描述null：菜单转换明确要求serving，避免全unknown空转。"""
        selection = {"day": core.today(), "meal": "lunch", "context": "测试", "parameters": {"storeCode": "TEST", "orderType": 1, "beType": 1}, "items": [{"product_code": "123", "category": "main"}]}
        with self.assertRaisesRegex(ValueError, "serving"): adapter.normalize_menu(response({"meals": {"123": {"name": "测试"}}}), response([self.row("测试")]), selection)

    def test_unknown_menu_item_includes_next_action_in_snapshot(self):
        """新品未收录：快照有明确诊断，不静默丢掉。"""
        selection = {"day": core.today(), "meal": "lunch", "context": "测试", "parameters": {"storeCode": "TEST", "orderType": 1, "beType": 1}, "items": [{"product_code": "123", "category": "main", "serving": "标准单份"}]}
        snapshot = adapter.normalize_menu(response({"meals": {"123": {"name": "模拟新品"}}}), response([self.row("测试旧品")]), selection)
        self.assertEqual(snapshot["nutrition_warnings"][0]["reason"], "not_found")
        self.assertIn("联网", snapshot["nutrition_warnings"][0]["action"])


class CustomerJourneys(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = core.Store(Path(self.temp.name) / "customer.sqlite3")
        self.store.configure(sample_profile())
        self.snapshot = sample_snapshot()
        self.checks = {"bundles_checked": True, "coupons_checked": True}

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def selection(self, items, price):
        if isinstance(items, str): items = [{"id": items, "quantity": 1}]
        return {"items": items, "quote": {"key": core.candidate_key(items), "kind": "demo", "store_code": "DEMO", "context": "演示自取", "total_cents": price, "quoted_at": "2026-10-14T12:00:00+08:00"}}

    def review(self, before="bundle-juice", after="bundle-zero", price=2000, **kwargs):
        return core.review_basket(self.snapshot, self.selection(before, 3000), [self.selection(after, price)], self.checks, **kwargs)

    def test_customer_wants_exact_three_items_no_replacement(self):
        """就要原来三样：便宜套餐也不能擅自换饮料。"""
        result = self.review(exact_contents=True)
        self.assertEqual(result["status"], "no_better_found")
        self.assertTrue(result["rejected"][0]["violations"])

    def test_exact_basket_accepts_coupon_with_same_contents(self):
        """原样不换：相同餐品用券更省可以推荐。"""
        self.assertEqual(self.review(after="bundle-juice", exact_contents=True)["status"], "better_equivalent_found")

    def test_customer_excludes_drink_inside_bundle(self):
        """不要果味饮料：套餐里的排除项也要检查。"""
        result = self.review(before="bundle-zero", after="bundle-juice", excluded_ids=["juice"])
        self.assertEqual(result["status"], "no_better_found")

    def test_customer_budget_even_cheaper_alternative_can_be_too_expensive(self):
        """预算20元：比原单便宜但仍25元也不能推荐。"""
        self.assertEqual(self.review(price=2500, budget_cents=2000)["status"], "no_better_found")

    def test_coupon_threshold_filler_breaks_energy_cap(self):
        """满减凑单：加配餐便宜但热量超标时不推荐。"""
        result = self.review(before="chicken", after="bundle-zero", target={"kcal_max": 500})
        self.assertEqual(result["status"], "no_better_found")

    def test_free_gift_still_counts_energy(self):
        """买赠：赠送配餐也要计入整餐营养。"""
        result = self.review(before="chicken", after="bundle-zero", target={"kcal_max": 600})
        self.assertEqual(result["replacement_recommendations"][0]["nutrition"]["kcal"], 570)
        self.assertEqual(result["status"], "changes_required")

    def test_high_carb_day_rejects_cheaper_low_carb_swap(self):
        """高碳日：更便宜、更低热量不自动更合适。"""
        self.assertEqual(self.review(target={"carb_g_min": 80})["status"], "no_better_found")

    def test_training_protein_floor_rejects_cheap_beef(self):
        """训练日蛋白目标：便宜方案蛋白不足就不推荐。"""
        result = self.review(before="bundle-zero", after="beef", target={"protein_g_min": 32})
        self.assertEqual(result["status"], "no_better_found")

    def test_rest_day_lower_energy_cap_used(self):
        """临时休息：报告按休息日目标评价，不沿用训练模板。"""
        self.store.override(self.snapshot["day"], "rest")
        report = nr.meal_report(self.snapshot, {"items": [{"id": "bundle-juice", "quantity": 1}]}, self.store)
        self.assertEqual(report["personal_assessment"]["status"], "outside")
        self.assertEqual(report["personal_assessment"]["active_template"], "计划休息")

    def test_unknown_sauce_blocks_nutrition_claim_but_not_price_comparison(self):
        """未知蘸酱：仍可看价格，但不能声称满足热量限制。"""
        self.assertEqual(self.review(before="unknown", after="unknown")["status"], "better_equivalent_found")
        self.assertEqual(self.review(before="unknown", after="unknown", target={"kcal_max": 600})["status"], "no_better_found")

    def test_must_keep_two_servings_not_reduced_to_one(self):
        """保留两份主食：不能用减一份的低价冒充优惠。"""
        before = [{"id": "chicken", "quantity": 2}]
        result = self.review(before=before, after="chicken", must_keep=["chicken"])
        self.assertEqual(result["status"], "no_better_found")

    def test_new_membership_purchase_not_assumed_owned(self):
        """会员展示价：不自动购买会员卡换低价。"""
        alternative = self.selection("bundle-zero", 1000); alternative["requires_membership_purchase"] = True
        result = core.review_basket(self.snapshot, self.selection("bundle-juice", 3000), [alternative], self.checks)
        self.assertEqual(result["status"], "no_better_found")

    def test_partial_coupon_query_not_reported_as_no_discount(self):
        """券页未查完：不能说没有优惠，也不能进入预览。"""
        self.checks["coupon_pages_complete"] = False
        result = self.review(after="bundle-juice", price=3000)
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["can_finalize"])

    def test_failed_variant_blocks_finalize_even_with_successful_baseline(self):
        """券报价失败：即使原单成功，比较完成状态也不能伪装。"""
        self.checks["quote_failure_count"] = 1
        self.assertFalse(self.review()["can_finalize"])

    def test_unresolved_coupon_conditions_do_not_count_as_finished(self):
        """有券但适用条件未核实：比较不能标成完整。"""
        self.checks["coupon_unresolved_count"] = 1
        self.assertFalse(self.review()["can_finalize"])

    def test_sold_out_bundle_not_recommended_from_old_price(self):
        """停售套餐：旧低价不意味着现在可以买。"""
        next(i for i in self.snapshot["items"] if i["id"] == "bundle-zero")["available"] = False
        self.assertEqual(self.review()["status"], "no_better_found")

    def test_partial_consumption_separate_from_full_purchase(self):
        """半份食用：营养按实际份量，账本保留整单付款。"""
        self.store.record_intake({"id": "meal", "day": "2026-10-14", "meal": "lunch", "items": [{"name": "模拟餐", "quantity": 0.5, "source": "official", "nutrition": {"kcal": 700}}]})
        self.store.bill({"id": "bill", "day": "2026-10-14", "status": "paid", "paid_cents": 3000})
        self.assertEqual(self.store.day_intake("2026-10-14")["known_totals"]["kcal"], 350)
        self.assertEqual(self.store.report("2026-10")["net_cents"], 3000)

    def test_already_consumed_near_daily_limit(self):
        """全天吃得接近上限：确认记录完整后午餐不能继续超额。"""
        self.store.record_intake({"id": "breakfast", "day": "2026-10-14", "meal": "breakfast", "items": [{"name": "模拟已吃", "quantity": 1, "source": "official", "nutrition": {n: 2100 if n == "kcal" else 0 for n in core.NUTRIENTS}}]})
        self.store.mark_complete("2026-10-14", True)
        self.assertEqual(core.recommend(self.store, "2026-10-14", "lunch", self.snapshot)["eligible_count"], 0)


if __name__ == "__main__":
    unittest.main()
