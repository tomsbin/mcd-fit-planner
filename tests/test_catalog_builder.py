"""Official-shaped synthetic details. No private data or real coupon redemption."""
import copy
from datetime import timedelta
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill/mcd-fit-planner/scripts"))
import catalog_builder as builder
import fit_core as core
import mcp_adapter as adapter


def response(data, now=None):
    return {"success": True, "code": 200, "datetime": (now or core.datetime.now(core.CN_TIME)).strftime("%Y-%m-%d %H:%M:%S"), "data": data}


def row(name, kcal, protein=0, carb=0, fat=0):
    return {"productName": name, "nutritionDescription": None, "energyKcal": kcal, "energyKj": kcal * 4.184, "protein": protein, "carbohydrate": carb, "fat": fat}


def choice(code, name, quantity=0, default=0):
    return {"code": code, "name": name, "quantity": quantity, "isDefault": default, "maxQuantity": -1, "diffPrice": "+ ¥0"}


def selected_round(number, choices):
    return {"id": number, "name": "测试选配", "quantity": 1, "minQuantity": 1, "maxQuantity": 1, "choices": choices}


class DetailCatalogTests(unittest.TestCase):
    def setUp(self):
        self.now = core.datetime.now(core.CN_TIME)
        self.selection = {"day": core.today(), "meal": "lunch", "context": "测试自取", "parameters": {"storeCode": "TEST", "orderType": 1, "beType": 1}, "items": [{"product_code": "combo", "category": "bundle"}]}
        self.menu = response({"meals": {"combo": {"name": "测试三件套"}, "burger": {"name": "测试汉堡"}, "sprite": {"name": "雪碧"}}})
        self.nutrition = response([row("测试汉堡", 500, 30, 45, 20), row("中薯条", 300, 4, 40, 12), row("玉米杯小杯", 60, 2, 8, 1), row("雪碧小杯", 100, 0, 25), row("无糖可口可乐小杯", 0)])
        self.detail = {"code": "combo", "name": "测试三件套", "rounds": [selected_round(1, [choice("burger", "测试汉堡", 1, 1)]), selected_round(2, [choice("fries", "中薯条", 1, 1), choice("corn", "玉米杯小杯")]), selected_round(3, [choice("sprite-small", "雪碧（小杯）", 1, 1), choice("zero-small", "无糖可口可乐（小杯）")])]}
        self.cache = {"scope": builder.scope(self.selection), "responses": {"combo": response(self.detail)}}

    def build(self, **kwargs):
        return builder.build_catalog(self.menu, self.nutrition, self.selection, self.cache, now=self.now, **kwargs)

    def total(self, snapshot, identifier=None):
        root = identifier or next(i["id"] for i in snapshot["items"] if i.get("available"))
        return core.aggregate([{"id": root, "quantity": 1}], {i["id"]: i for i in snapshot["items"]})

    def test_default_bundle_sums_all_rounds_without_manual_serving(self):
        snapshot = self.build()
        self.assertEqual(self.total(snapshot)["nutrition"]["kcal"], 900)
        self.assertEqual(self.total(snapshot)["nutrition"]["protein_g"], 34)
        self.assertEqual(len(snapshot["items"][0]["components"]), 3)
        self.assertIn("roundList", snapshot["items"][0]["mcp_item"])

    def test_selected_corn_and_zero_not_default_fries_and_sprite(self):
        self.selection["items"][0]["options"] = {"roundList": [{"round": "1", "comboItemList": [{"code": "burger", "quantity": 1}]}, {"round": "2", "comboItemList": [{"code": "corn", "quantity": 1}]}, {"round": "3", "comboItemList": [{"code": "zero-small", "quantity": 1}]}]}
        snapshot = self.build()
        self.assertEqual(self.total(snapshot)["nutrition"]["kcal"], 560)
        self.assertEqual(self.total(snapshot)["nutrition"]["fat_g"], 21)

    def test_generic_detail_name_matching_is_not_four_family_whitelist(self):
        self.selection["items"] = [{"product_code": "sprite", "category": "drink"}]
        self.cache = {"scope": builder.scope(self.selection), "responses": {"sprite": response({"code": "sprite", "name": "雪碧（小杯）", "rounds": []})}}
        snapshot = self.build()
        self.assertEqual(self.total(snapshot)["nutrition"]["kcal"], 100)
        self.assertEqual(snapshot["nutrition_warnings"], [])

    def test_expands_joint_side_and_drink_changes_not_only_one_round(self):
        self.selection["items"][0]["expand"] = True
        snapshot = self.build(target={"kcal_min": 550, "kcal_max": 600})
        self.assertEqual(snapshot["catalog_status"]["built_variant_count"], 4)
        roots = [i for i in snapshot["items"] if i.get("available")]
        self.assertIn(560, [self.total(snapshot, i["id"])["nutrition"]["kcal"] for i in roots])

    def test_variant_cap_keeps_default_and_best_goal_fit(self):
        self.selection["items"][0]["expand"] = True
        snapshot = self.build(target={"kcal_min": 550, "kcal_max": 600}, max_variants=2)
        self.assertEqual(self.total(snapshot, "combo")["nutrition"]["kcal"], 900)
        self.assertIn(560, [self.total(snapshot, i["id"])["nutrition"]["kcal"] for i in snapshot["items"] if i.get("available")])
        self.assertTrue(snapshot["catalog_status"]["truncated"])

    def test_only_change_side_preserves_original_drink(self):
        self.selection["items"][0].update(expand=True, expand_rounds=["2"])
        snapshot = self.build()
        roots = [i for i in snapshot["items"] if i.get("available")]
        self.assertEqual(len(roots), 2)
        self.assertEqual({self.total(snapshot, i["id"])["nutrition"]["kcal"] for i in roots}, {900, 660})

    def test_unknown_sauce_prevents_claiming_complete_bundle(self):
        self.detail["rounds"].append(selected_round(4, [choice("sauce", "测试未知蘸酱", 1, 1)]))
        self.cache["responses"]["combo"] = response(self.detail)
        snapshot = self.build()
        total = self.total(snapshot)
        self.assertIsNone(total["nutrition"]["kcal"])
        self.assertEqual(total["known_subtotal"]["kcal"], 900)
        self.assertIn("测试未知蘸酱", snapshot["nutrition_warnings"][0]["name"])

    def test_default_quantity_takes_precedence_over_conflicting_default_bits(self):
        self.detail["rounds"][1]["choices"][1]["isDefault"] = 1
        self.cache["responses"]["combo"] = response(self.detail)
        self.assertEqual(self.total(self.build())["nutrition"]["kcal"], 900)

    def test_ambiguous_defaults_are_blocked_not_guessed(self):
        for c in self.detail["rounds"][1]["choices"]:
            c.update(quantity=0, isDefault=1)
        self.cache["responses"]["combo"] = response(self.detail)
        snapshot = self.build()
        self.assertEqual(snapshot["items"], [])
        self.assertIn("不唯一", snapshot["catalog_status"]["blocked"][0]["reason"])

    def test_incomplete_round_selection_rejected(self):
        self.selection["items"][0]["options"] = {"roundList": [{"round": "1", "comboItemList": [{"code": "burger", "quantity": 1}]}]}
        self.assertTrue(self.build()["catalog_status"]["blocked"])

    def test_invalid_or_too_many_chosen_items_rejected(self):
        self.detail["rounds"][0]["choices"][0]["quantity"] = 2
        self.cache["responses"]["combo"] = response(self.detail)
        self.assertTrue(self.build()["catalog_status"]["blocked"])

    def test_customization_is_unknown_not_standard_nutrition(self):
        self.selection["items"][0]["options"] = {"modification": {"values": [{"code": "sauce", "quantity": 0}]}}
        snapshot = self.build()
        self.assertIsNone(self.total(snapshot)["nutrition"]["kcal"])
        self.assertTrue(snapshot["nutrition_warnings"])

    def test_single_drink_wrapper_and_bundle_share_physical_component(self):
        self.selection["items"].append({"product_code": "sprite", "category": "drink"})
        self.cache["responses"]["sprite"] = response({"code": "sprite", "name": "雪碧", "rounds": [selected_round(1, [choice("sprite-small", "雪碧（小杯）", 1, 1)])]})
        snapshot = self.build()
        catalog = {i["id"]: i for i in snapshot["items"]}
        self.assertEqual(core.nutrition(catalog["sprite"])["kcal"], None)
        self.assertEqual(self.total(snapshot, "sprite")["nutrition"]["kcal"], 100)
        self.assertEqual(core.content_units(core.physical_contents([{"id": "combo", "quantity": 1}], catalog), "sprite", catalog), 1)

    def test_wrong_scope_cache_rejected(self):
        self.cache["scope"] = "OTHER-STORE"
        with self.assertRaises(ValueError): self.build()

    def test_stale_detail_requests_refresh(self):
        self.cache["responses"]["combo"] = response(self.detail, self.now - timedelta(minutes=31))
        requested = builder.detail_requests(self.selection, self.cache, now=self.now)
        self.assertEqual(requested["requests"][0]["arguments"]["code"], "combo")
        self.assertNotIn("productCode", requested["requests"][0]["arguments"])
        self.assertTrue(self.build()["catalog_status"]["blocked"])

    def test_fresh_cache_reuses_one_root_not_every_component(self):
        request = builder.detail_requests(self.selection, self.cache, now=self.now)
        self.assertEqual(request["requests"], [])
        self.assertEqual(request["reused"], ["combo"])

    def test_collection_isolates_one_failed_detail(self):
        self.selection["items"].append({"product_code": "burger", "category": "main"})
        requested = builder.detail_requests(self.selection, now=self.now)
        cached = builder.collect_details(requested, {"combo": response(self.detail), "burger": {"success": False, "code": 500, "data": {}}})
        self.assertEqual(cached["failed"], ["burger"])
        self.assertIn("combo", cached["responses"])

    def test_detail_request_limit_reports_unrequested_roots(self):
        self.selection["items"].append({"product_code": "burger", "category": "main"})
        requested = builder.detail_requests(self.selection, limit=1, now=self.now)
        self.assertEqual(len(requested["requests"]), 1)
        self.assertEqual(requested["deferred"], ["burger"])

    def test_detail_response_product_code_mismatch_rejected(self):
        self.cache["responses"]["combo"] = response({"code": "OTHER", "name": "别的餐品", "rounds": []})
        self.assertTrue(self.build()["catalog_status"]["blocked"])

    def test_coupon_exclusive_sku_not_in_menu_resolves_from_owned_coupon(self):
        self.selection["items"] = [{"product_code": "coupon-combo", "category": "bundle", "coupon": {"coupon_id": "OWNED", "coupon_code": "FAKE-CODE"}}]
        detail = copy.deepcopy(self.detail); detail["code"] = "coupon-combo"
        self.cache = {"scope": builder.scope(self.selection), "responses": {"coupon-combo": response(detail)}}
        coupons = adapter.normalize_store_coupons(response([{"couponId": "OWNED", "couponCode": "FAKE-CODE", "title": "测试专属套餐券", "tradeDateTime": (self.now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S") + " - " + (self.now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"), "products": [{"productCode": "coupon-combo", "productName": "测试专属套餐"}]}]), self.selection["parameters"])
        snapshot = self.build(coupons=coupons)
        self.assertEqual(self.total(snapshot)["nutrition"]["kcal"], 900)
        self.assertEqual(snapshot["items"][0]["mcp_item"]["productCode"], "coupon-combo")
        self.assertEqual(snapshot["items"][0]["mcp_item"]["couponId"], "OWNED")
        self.assertEqual(self.build()["items"], [])


if __name__ == "__main__":
    unittest.main()
