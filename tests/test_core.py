import copy
import csv
import importlib.util
import sys
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo import core, sample_profile, sample_snapshot, sample_quotes


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.Store(Path(self.tmp.name) / "test.sqlite3")
        self.store.configure(sample_profile())

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_rest_holds_but_planned_rest_advances(self):
        self.store.override("2026-10-15", "rest")
        self.store.override("2026-10-16", "rest")
        self.assertEqual(self.store.plan("2026-10-17")["active_template"], "高碳训练B")
        self.assertEqual(self.store.plan("2026-10-18")["active_template"], "计划休息")
        self.assertEqual(self.store.plan("2026-10-19")["active_template"], "训练C")

    def test_skip_consumes_step_and_revert_rest(self):
        self.store.override("2026-10-15", "skip")
        self.assertEqual(self.store.plan("2026-10-16")["scheduled_template"], "计划休息")
        self.store.override("2026-10-15", "rest")
        self.store.override("2026-10-15", "normal")
        self.assertEqual(self.store.plan("2026-10-16")["scheduled_template"], "计划休息")

    def test_days_without_use_advance_and_prestart_rejected(self):
        self.assertEqual(self.store.plan("2026-10-19")["scheduled_template"], "高碳训练B")
        with self.assertRaises(ValueError): self.store.plan("2026-10-13")

    def test_retrospective_override_does_not_change_intake(self):
        entry = {"id": "a", "day": "2026-10-15", "meal": "lunch", "items": [{"name": "food", "quantity": 1, "source": "manual", "nutrition": {"kcal": 600}}]}
        self.store.record_intake(entry)
        self.store.override("2026-10-15", "rest")
        self.assertEqual(self.store.day_intake("2026-10-15")["known_totals"]["kcal"], 600)

    def test_cannot_reinterpret_old_rest_under_new_cycle(self):
        self.store.override("2026-10-15", "rest")
        profile = sample_profile(); profile["cycle"].reverse()
        with self.assertRaises(ValueError): self.store.configure(profile)

    def test_partial_portion_unknown_and_idempotent(self):
        entry = {"id": "a", "day": "2026-10-14", "meal": "breakfast", "items": [{"name": "food", "quantity": 0.5, "source": "estimated", "nutrition": {"kcal": 300, "protein_g": None}}]}
        self.store.record_intake(entry); self.store.record_intake(entry)
        result = self.store.day_intake(entry["day"])
        self.assertEqual(result["known_totals"]["kcal"], 150)
        self.assertIn("protein_g", result["unknown_nutrients"])
        self.assertEqual(len(result["entries"]), 1)
        self.assertTrue(result["estimated"])

    def test_daily_budget_requires_coverage(self):
        entry = {"id": "a", "day": "2026-10-14", "meal": "breakfast", "items": [{"name": "food", "quantity": 1, "source": "manual", "nutrition": {"kcal": 1900}}]}
        self.store.record_intake(entry)
        self.assertEqual(self.store.target(entry["day"], "lunch")["target"]["kcal_max"], 750)
        self.store.mark_complete(entry["day"], True)
        self.assertEqual(self.store.target(entry["day"], "lunch")["target"]["kcal_max"], 300)

    def test_unknown_food_is_not_zero_calorie_recommendation(self):
        snapshot = sample_snapshot()
        request = core.recommend(self.store, snapshot["day"], "lunch", snapshot)
        self.assertGreater(request["rejected_count"], 0)
        self.assertFalse(any(i["id"] == "unknown" for c in request["quote_requests"] for i in c["items"]))

    def test_kj_conversion(self):
        self.assertAlmostEqual(core.nutrition({"nutrition": {"energy_kj": 418.4}})["kcal"], 100)

    def test_bundle_nutrition_is_recomputed_from_components(self):
        snapshot = sample_snapshot()
        bundle = next(i for i in snapshot["items"] if i["id"] == "bundle-zero")
        bundle["nutrition"]["kcal"] = 1
        catalog = core.validate_snapshot(snapshot, snapshot["day"])
        self.assertEqual(core.aggregate([{"id": "bundle-zero", "quantity": 1}], catalog)["nutrition"]["kcal"], 570)
        catalog["side"]["nutrition"]["fat_g"] = None
        self.assertIsNone(core.aggregate([{"id": "bundle-zero", "quantity": 1}], catalog)["nutrition"]["fat_g"])

    def test_invalid_bundle_components_rejected(self):
        snapshot = sample_snapshot()
        next(i for i in snapshot["items"] if i["category"] == "bundle")["components"] = ["not-real"]
        with self.assertRaises(ValueError): core.validate_snapshot(snapshot, snapshot["day"])

    def test_high_carb_does_not_favor_lowest_calories(self):
        snapshot = sample_snapshot("2026-10-15")
        request = core.recommend(self.store, snapshot["day"], "lunch", snapshot)
        self.assertTrue(request["quote_requests"])
        self.assertTrue(all(c["nutrition"]["carb_g"] >= 80 for c in request["quote_requests"]))

    def test_required_and_excluded_apply_to_bundle_components(self):
        snapshot = sample_snapshot()
        request = core.recommend(self.store, snapshot["day"], "lunch", snapshot, required=["chicken"], excluded=["juice"])
        self.assertFalse(any(i["id"] == "bundle-juice" for c in request["quote_requests"] for i in c["items"]))
        self.assertTrue(any(i["id"] == "bundle-zero" for c in request["quote_requests"] for i in c["items"]))

    def test_quotes_context_and_budget(self):
        snapshot = sample_snapshot()
        request = core.recommend(self.store, snapshot["day"], "lunch", snapshot)
        quotes = sample_quotes(request, snapshot)
        result = core.recommend(self.store, snapshot["day"], "lunch", snapshot, quotes)
        self.assertTrue(result["choices"])
        self.assertIsNotNone(result["closest"])
        self.assertEqual(result["cheapest"]["total_cents"], min(c["total_cents"] for c in result["choices"]))
        for q in quotes["quotes"]: q["total_cents"] = 9999
        self.assertFalse(core.recommend(self.store, snapshot["day"], "lunch", snapshot, quotes)["choices"])
        self.assertIsNone(core.recommend(self.store, snapshot["day"], "lunch", snapshot, quotes)["cheapest"])
        quotes["context_id"] = "bad"
        with self.assertRaises(ValueError): core.recommend(self.store, snapshot["day"], "lunch", snapshot, quotes)

    def test_wrong_meal_or_store_fails(self):
        snapshot = sample_snapshot(); snapshot["meal"] = "dinner"
        with self.assertRaises(ValueError): core.recommend(self.store, snapshot["day"], "lunch", snapshot)
        snapshot["meal"] = "lunch"; snapshot["store_code"] = "other"
        with self.assertRaises(ValueError): core.recommend(self.store, snapshot["day"], "lunch", snapshot)

    def test_compare_preserves_unknown(self):
        result = core.compare(sample_snapshot(), {"items": [{"id": "unknown", "quantity": 1}]}, {"items": [{"id": "chicken", "quantity": 1}]})
        self.assertIsNone(result["nutrition_delta_after_minus_before"]["kcal"])
        self.assertIsNone(result["price_delta_cents"])

    def test_bill_sync_refunds_pending_and_year_rollover(self):
        bill = {"id": "order", "day": "2026-10-14", "status": "paid", "paid_cents": 3000}
        self.store.bill(bill); self.store.bill(bill)
        self.store.bill({**bill, "status": "partial_refund", "refund_cents": 500})
        self.store.bill({"id": "pending", "day": "2026-10-15", "status": "pending", "paid_cents": 0})
        self.store.bill({"id": "last", "day": "2025-12-31", "status": "paid", "paid_cents": 2000})
        report = self.store.report("2026")
        self.assertEqual(report["paid_order_count"], 1)
        self.assertEqual(report["net_cents"], 2500)
        self.assertEqual(report["pending_count"], 1)
        self.assertEqual(self.store.report("2025")["net_cents"], 2000)

    def test_invalid_refund_and_money_rejected(self):
        for status, paid, refund in (("refunded", 3000, 200), ("paid", 3000, 200), ("pending", 3000, 0), ("partial_refund", 3000, 4000), ("paid", 30.5, 0)):
            with self.assertRaises(ValueError): self.store.bill({"id": "x", "day": "2026-10-14", "status": status, "paid_cents": paid, "refund_cents": refund})

    def test_old_sync_does_not_erase_refund_or_payment(self):
        bill = {"id": "x", "day": "2026-10-14", "status": "partial_refund", "paid_cents": 3000, "refund_cents": 500, "discount_cents": 800, "items": [{"name": "套餐", "quantity": 1}]}
        self.store.bill(bill)
        self.store.bill({**bill, "status": "paid", "refund_cents": 0})
        self.store.bill({**bill, "status": "pending", "refund_cents": 0, "paid_cents": 0})
        report = self.store.report("2026-10")
        self.assertEqual(report["net_cents"], 2500)
        self.assertEqual(report["known_discount_cents"], 800)
        self.assertEqual(report["paid_order_count"], 1)

    def test_original_bill_date_and_amount_cannot_silently_change(self):
        bill = {"id": "x", "day": "2026-10-14", "status": "paid", "paid_cents": 3000}
        self.store.bill(bill)
        with self.assertRaises(ValueError): self.store.bill({**bill, "day": "2026-11-14"})
        with self.assertRaises(ValueError): self.store.bill({**bill, "paid_cents": 2000})

    def test_sensitive_fields_not_persisted(self):
        self.store.bill({"id": "safe", "day": "2026-10-14", "status": "paid", "paid_cents": 1000, "token": "SECRET", "address": "PRIVATE", "payH5Url": "PAY"})
        value = self.store.db.execute("SELECT payload FROM bills").fetchone()[0]
        self.assertNotIn("SECRET", value); self.assertNotIn("PRIVATE", value); self.assertNotIn("PAY", value)

    def test_nan_and_invalid_targets_rejected(self):
        with self.assertRaises(ValueError): core.number(float("nan"), "value")
        with self.assertRaises(ValueError): core.validate_target({"kcal_min": 800, "kcal_max": 500})
        with self.assertRaises(ValueError): core.validate_target({"calorie": 500})

    def test_export_formula_escaped(self):
        self.store.bill({"id": "=1+1", "day": "2026-10-14", "status": "paid", "paid_cents": 1000, "items": [{"name": "=HYPERLINK(1)", "quantity": 1}]})
        path = Path(self.tmp.name) / "export.csv"
        self.store.export(path)
        with path.open(encoding="utf-8-sig", newline="") as f:
            row = list(csv.reader(f))[1]
        self.assertTrue(row[0].startswith("'=")); self.assertTrue(row[-1].startswith("'="))

    def official_preview(self):
        day = core.today()
        profile = sample_profile(day); self.store.configure(profile)
        snapshot = sample_snapshot(day, "official")
        for item in snapshot["items"]:
            item["nutrition_source"] = "official"
            item["evidence"] = {"nutrition_name": item["name"], "serving": "test fixture only"}
        request = core.recommend(self.store, day, "lunch", snapshot)
        quotes = sample_quotes(request, snapshot)
        for q in quotes["quotes"]:
            q["quoted_at"] = datetime.now(core.CN_TIME).isoformat()
            q["order_payload"] = {"test_only": q["key"], "store": "DEMO"}
        result = core.recommend(self.store, day, "lunch", snapshot, quotes)
        key = result["choices"][0]["key"]
        quote_map = {q["key"]: q for q in quotes["quotes"]}
        def selected(candidate):
            return {"items": [{"id": i["id"], "quantity": i["quantity"]} for i in candidate["items"]], "quote": {**quote_map[candidate["key"]], "kind": "official", "store_code": snapshot["store_code"], "context": snapshot["context"]}}
        baseline = selected(result["choices"][0])
        alternatives = [selected(c) for c in request["quote_requests"] if c["key"] != baseline["quote"]["key"]]
        review = core.review_basket(snapshot, baseline, alternatives, {"bundles_checked": True, "coupons_checked": True}, self.store.target(day, "lunch")["target"])
        return snapshot, quotes, self.store.preview(day, "lunch", snapshot, quotes, key, review)

    def test_checkout_requires_completed_review(self):
        snapshot, quotes, preview = self.official_preview()
        with self.assertRaises(ValueError): self.store.preview(snapshot["day"], "lunch", snapshot, quotes, preview["key"])

    def test_demo_cannot_preview_real_order(self):
        snapshot = sample_snapshot()
        request = core.recommend(self.store, snapshot["day"], "lunch", snapshot)
        with self.assertRaises(ValueError): self.store.preview(snapshot["day"], "lunch", snapshot, sample_quotes(request, snapshot), request["quote_requests"][0]["key"])

    def test_confirmation_single_use(self):
        _, quotes, preview = self.official_preview()
        result = self.store.authorize(preview["confirmation_id"], quotes)
        self.assertIn("create_once_payload", result)
        with self.assertRaises(ValueError): self.store.authorize(preview["confirmation_id"], quotes)

    def test_changed_price_or_payload_rejected(self):
        _, quotes, preview = self.official_preview()
        for q in quotes["quotes"]: q["total_cents"] += 1
        with self.assertRaises(ValueError): self.store.authorize(preview["confirmation_id"], quotes)
        for q in quotes["quotes"]:
            q["total_cents"] -= 1; q["order_payload"]["extra"] = True
        with self.assertRaises(ValueError): self.store.authorize(preview["confirmation_id"], quotes)

    def test_changed_plan_rejected_at_confirmation(self):
        _, quotes, preview = self.official_preview()
        self.store.override(core.today(), "rest")
        with self.assertRaises(ValueError): self.store.authorize(preview["confirmation_id"], quotes)

    def test_stale_official_quote_rejected(self):
        snapshot, quotes, _ = self.official_preview()
        for q in quotes["quotes"]: q["quoted_at"] = (datetime.now(core.CN_TIME) - timedelta(minutes=16)).isoformat()
        with self.assertRaises(ValueError): core.recommend(self.store, snapshot["day"], "lunch", snapshot, quotes)

    def test_cli_report_and_error_are_json(self):
        import json
        script = str(ROOT / "skill/mcd-fit-planner/scripts/fit_core.py")
        command = [sys.executable, script, "--db", str(Path(self.tmp.name) / "test.sqlite3")]
        result = subprocess.run(command + ["report", "--period", "2026"], capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["net_cents"], 0)
        result = subprocess.run(command + ["plan", "--date", "2026-10-13"], capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))
        output = str(Path(self.tmp.name) / "result.json")
        result = subprocess.run(command + ["--output", output, "report", "--period", "2026"], capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(Path(output).read_text(encoding="utf-8"))["net_cents"], 0)

    def test_cli_export_preserves_csv_and_legacy_alias(self):
        import json
        self.store.bill({"id": "csv-order", "day": "2026-10-14", "status": "paid", "paid_cents": 3350})
        script = str(ROOT / "skill/mcd-fit-planner/scripts/fit_core.py")
        command = [sys.executable, script, "--db", str(Path(self.tmp.name) / "test.sqlite3")]
        for flag in ("--csv", "--output"):
            path = Path(self.tmp.name) / (flag[2:] + ".csv")
            result = subprocess.run(command + ["export", flag, str(path)], capture_output=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["exported"], str(path.resolve()))
            with path.open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows[0][0], "账单标识")
            self.assertEqual(rows[1][0], "csv-order")
            self.assertEqual(rows[1][3], "33.50")
        protected = Path(self.tmp.name) / "protected.csv"
        protected.write_text("keep me", encoding="utf-8")
        result = subprocess.run(command + ["--output", str(protected), "export", "--csv", str(protected)], capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(protected.read_text(encoding="utf-8"), "keep me")


if __name__ == "__main__":
    unittest.main()
