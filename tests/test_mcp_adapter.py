import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill/mcd-fit-planner/scripts"))
import mcp_adapter as adapter
import test_core as planner_tests


def response(data, at=None):
    at = at or adapter.datetime.now(adapter.CN_TIME).strftime("%Y-%m-%d %H:%M:%S")
    return {"success": True, "code": 200, "datetime": at, "data": data}


def nutrition_response():
    return response('[2]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:\n  演示主食,标准份,1757.28,420,32,14,42,500,100\n  "演示饮料,无糖",中杯,0,0,0,0,0,0,0\n')


class AdapterTests(unittest.TestCase):
    def test_toon_quoted_comma_and_numbers(self):
        rows = adapter.nutrition_rows(nutrition_response())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["productName"], "演示饮料,无糖")
        values, evidence, source = adapter.match_nutrition(rows, {"nutrition_description": "标准份"}, "演示主食")
        self.assertEqual(values["kcal"], 420)
        self.assertEqual(source, "official")

    def test_mcp_text_wrapper(self):
        raw = {"content": [{"type": "text", "text": json.dumps(nutrition_response())}]}
        self.assertEqual(len(adapter.nutrition_rows(raw)), 2)
        with self.assertRaises(ValueError): adapter.envelope({"isError": True, **raw})

    def test_toon_malformed_or_missing_fields(self):
        for data in ('[2]{productName}:\n  foo', '[1]{productName,energyKcal}:\n a,1,2', '[1]{productName}:\n a'):
            with self.assertRaises(ValueError): adapter.nutrition_rows(response(data))

    def test_ambiguous_and_unverified_size_stays_unknown(self):
        rows = adapter.nutrition_rows(nutrition_response())
        rows.append({**rows[0], "nutritionDescription": "大份"})
        values, evidence, source = adapter.match_nutrition(rows, {}, "演示主食")
        self.assertIsNone(values["kcal"])
        self.assertEqual(evidence["match"], "ambiguous")
        rows = [{**rows[0], "nutritionDescription": None}]
        self.assertEqual(adapter.match_nutrition(rows, {}, "演示主食")[1]["match"], "serving_unverified")

    def test_menu_conversion_and_customization_unknown(self):
        menu = response({"meals": {"123": {"name": "演示主食", "currentPrice": "22"}}})
        selection = {"day": "2026-10-14", "meal": "lunch", "context": "演示自取", "parameters": {"storeCode": "DEMO", "orderType": 1, "beType": 1}, "items": [{"product_code": "123", "category": "main", "serving": "标准份"}]}
        snapshot = adapter.normalize_menu(menu, nutrition_response(), selection)
        self.assertEqual(snapshot["items"][0]["nutrition"]["protein_g"], 32)
        self.assertNotIn("total_cents", snapshot["items"][0])
        selection["items"][0]["options"] = {"modification": {"values": [{"code": "sauce", "quantity": 0}]}}
        snapshot = adapter.normalize_menu(menu, nutrition_response(), selection)
        self.assertIsNone(snapshot["items"][0]["nutrition"]["kcal"])

    def test_cents_and_yuan_are_different(self):
        requests = {"kind": "official", "context_id": "ctx", "store_code": "DEMO", "context": "自取", "requests": [{"key": "a", "arguments": {"storeCode": "DEMO", "items": [{"productCode": "123", "quantity": 1}]}}]}
        quotes = adapter.normalize_quotes(requests, {"a": response({"price": 3350, "discount": 500})})
        self.assertEqual(quotes["quotes"][0]["total_cents"], 3350)
        self.assertEqual(adapter.yuan_to_cents("33.50"), 3350)
        self.assertTrue(quotes["quotes"][0]["quoted_at"].endswith("+08:00"))
        for amount in ("NaN", "-1", "1.001", True):
            with self.assertRaises(ValueError): adapter.yuan_to_cents(amount)

    def test_quote_response_error_not_used_as_price(self):
        with self.assertRaises(ValueError): adapter.envelope({"success": False, "code": 401, "data": {"price": 0}})

    def test_live_empty_orders_are_valid_but_unknown_schema_is_not(self):
        self.assertEqual(adapter.normalize_orders(response({}), "account")["bills"], [])
        self.assertEqual(adapter.normalize_orders(response({"list": []}), "account")["bills"], [])
        with self.assertRaises(ValueError): adapter.normalize_orders(response({"changed": []}), "account")

    def test_explicit_zero_kcal_does_not_silently_fallback(self):
        row = adapter.nutrition_rows(nutrition_response())[0]
        row["energyKcal"] = 0
        values, _, _ = adapter.match_nutrition([row], {}, "演示主食")
        self.assertEqual(values["kcal"], 0)
        row["energyKcal"] = None
        self.assertAlmostEqual(adapter.match_nutrition([row], {}, "演示主食")[0]["kcal"], 420)

    def test_order_privacy_pending_and_cancelled_uncertain(self):
        orders = [
            {"orderId": "PRIVATE-ID", "createTime": "2026-10-14 12:00:00", "orderStatus": "订单已完成", "realTotalAmount": "33.5", "deliveryInfo": {"mobilePhone": "PRIVATE-PHONE"}},
            {"orderId": "pending", "createTime": "2026-10-14 12:00:00", "orderStatus": "待支付", "realTotalAmount": "50"},
            {"orderId": "cancelled", "orderStatus": "订单已取消"}]
        result = adapter.normalize_orders(response({"list": orders}), "my-account")
        self.assertEqual(result["bills"][0]["paid_cents"], 3350)
        self.assertEqual(result["bills"][1]["paid_cents"], 0)
        self.assertEqual(len(result["unresolved"]), 1)
        serialized = json.dumps(result)
        self.assertNotIn("PRIVATE-ID", serialized)
        self.assertNotIn("PRIVATE-PHONE", serialized)
        second = adapter.normalize_orders(response({"list": orders}), "other-account")
        self.assertNotEqual(second["bills"][0]["id"], result["bills"][0]["id"])


class ImportTests(unittest.TestCase):
    setUp = planner_tests.PlannerTests.setUp
    tearDown = planner_tests.PlannerTests.tearDown
    def test_atomic_import(self):
        valid = {"id": "valid", "day": "2026-10-14", "status": "paid", "paid_cents": 2000}
        invalid = {"id": "bad", "day": "not-a-date", "status": "paid", "paid_cents": 2000}
        with self.assertRaises(ValueError): self.store.import_bills({"bills": [valid, invalid]})
        self.assertEqual(self.store.report("2026")["net_cents"], 0)

    def test_documented_responses_to_recommendation_and_ledger(self):
        core = planner_tests.core
        day = core.today()
        profile = planner_tests.sample_profile(day)
        profile["templates"]["训练A"]["meals"]["lunch"]["target"] = {"kcal_min": 300, "kcal_max": 500, "protein_g_min": 30, "fat_g_max": 20}
        self.store.configure(profile)
        menu = response({"meals": {"123": {"name": "演示主食", "currentPrice": "22"}}})
        selection = {"day": day, "meal": "lunch", "context": "演示自取", "parameters": {"storeCode": "DEMO", "orderType": 1, "beType": 1}, "items": [{"product_code": "123", "category": "main", "serving": "标准份"}]}
        snapshot = adapter.normalize_menu(menu, nutrition_response(), selection)
        candidates = core.recommend(self.store, day, "lunch", snapshot)
        requests = adapter.price_requests(snapshot, candidates)
        self.assertEqual(requests["requests"][0]["arguments"]["items"], [{"productCode": "123", "quantity": 1}])
        at = core.datetime.now(core.CN_TIME).strftime("%Y-%m-%d %H:%M:%S")
        quotes = adapter.normalize_quotes(requests, {r["key"]: response({"price": 2200, "discount": 300}, at) for r in requests["requests"]})
        result = core.recommend(self.store, day, "lunch", snapshot, quotes)
        self.assertEqual(result["choices"][0]["total_cents"], 2200)
        orders = response({"list": [{"orderId": "synthetic-1", "orderStatus": "订单已完成", "createTime": day + " 12:00:00", "realTotalAmount": "22", "totalDiscountAmount": "3", "orderProductList": [{"productName": "演示主食", "quantity": 1}]}]})
        bills = adapter.normalize_orders(orders, "test-account")
        self.store.import_bills(bills); self.store.import_bills(bills)
        report = self.store.report(day[:7])
        self.assertEqual(report["net_cents"], 2200)
        self.assertEqual(report["known_discount_cents"], 300)
        self.assertEqual(report["paid_order_count"], 1)


if __name__ == "__main__":
    unittest.main()
