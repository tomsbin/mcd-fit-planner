"""Delivery/group/reservation contracts are simulated; web corn is a dated public capture."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta

import test_catalog_builder as fixtures
import test_customer_scenarios as customer
import fit_core as core
import catalog_builder as builder
import mcp_adapter as adapter
import nutrition_support as ns
from demo import sample_profile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skill/mcd-fit-planner/scripts"


class FulfillmentPipelineTests(unittest.TestCase):
    def journey(self, business_type, reservation=False):
        f = customer.CouponScenarios(); f.setUp()
        delivery = business_type in (2, 6)
        f.snapshot["parameters"].update(orderType=2 if delivery else 1, beType=business_type, beCode="TEST-BUSINESS")
        if business_type == 6: f.snapshot["parameters"]["gmServiceCode"] = "TEST-GROUP"
        if reservation: f.snapshot["parameters"]["reservationDate"] = (core.datetime.now(core.CN_TIME) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        with tempfile.TemporaryDirectory() as tmp:
            store = core.Store(Path(tmp) / "test.sqlite3")
            try:
                profile = sample_profile(core.today()); profile["defaults"].update(store_code="TEST", context=f.snapshot["context"])
                store.configure(profile)
                wanted = core.recommend(store, core.today(), "lunch", f.snapshot)
                coupons = adapter.normalize_store_coupons(customer.response([customer.coupon()]), f.snapshot["parameters"])
                requests = adapter.price_requests(f.snapshot, wanted, coupons)
                quotes = f.quotes(requests, [{"price": 3100, "takeWayList": [{"code": "TEST-DT"}]}, {"price": 2400, "deliveryPrice": 400 if delivery else 0, "packingPrice": 100, "takeWayList": [{"code": "TEST-DT"}]}])
                key = quotes["quotes"][0]["key"]
                fulfillment = {"addressId": "SYNTHETIC-ADDRESS"} if delivery else {"takeWayCode": "TEST-DT"}
                prepared = adapter.prepare_order(quotes, key, fulfillment)
                selection = {"items": f.items, "quote": prepared["quotes"][0]}
                review = core.review_basket(f.snapshot, selection, [], adapter.review_checks(prepared, True, True), store.target(core.today(), "lunch")["target"], budget_cents=3500)
                preview = store.preview(core.today(), "lunch", f.snapshot, prepared, key, review)
                result = store.authorize(preview["confirmation_id"], prepared)
                payload = result["create_once_payload"]
                self.assertEqual(payload["items"][0]["couponCode"], "FAKE-A")
                self.assertEqual(payload["beType"], business_type)
                self.assertEqual(payload.get("reservationDate"), f.snapshot["parameters"].get("reservationDate"))
                self.assertEqual(result["total_cents"], 2400)
                if delivery: self.assertNotIn("takeWayCode", payload)
                if business_type == 6: self.assertEqual(payload["gmServiceCode"], "TEST-GROUP")
            finally: store.db.close()

    def test_delivery_quote_to_authorize(self): self.journey(2)
    def test_group_order_quote_to_authorize(self): self.journey(6)
    def test_drive_through_quote_to_authorize(self): self.journey(5)
    def test_reservation_preserves_service_time(self): self.journey(2, True)

    def test_group_missing_service_code_and_delivery_missing_business_code_rejected(self):
        f = customer.CouponScenarios(); f.setUp()
        for fields in ({"orderType": 2, "beType": 6, "beCode": "GROUP"}, {"orderType": 2, "beType": 2}):
            f.snapshot["parameters"] = {"storeCode": "TEST", **fields}
            requests = f.requests([]); quotes = f.quotes(requests, [{"price": 3000}])
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                adapter.prepare_order(quotes, quotes["quotes"][0]["key"], {"addressId": "TEST"})


class EnrichedCliTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DetailCatalogTests(); self.f.setUp()
        self.f.selection["items"][0]["expand"] = True
        self.f.nutrition["data"] = [r for r in self.f.nutrition["data"] if r["productName"] != "玉米杯小杯"]
        self.target = {"kcal_min": 540, "kcal_max": 600}
        self.snapshot = self.f.build(target=self.target, max_variants=2)
        leaf = next(i for i in self.snapshot["items"] if i["name"] == "玉米杯小杯")
        self.record = core.read_json(ROOT / "skill/mcd-fit-planner/assets/corn-small-cn.json")
        self.record.update(item_id=leaf["id"], match={"name": leaf["name"], "serving": leaf["serving"]})
        self.enriched = ns.supplement(self.snapshot, [self.record], self.record["evidence"]["checked_at"])["snapshot"]

    def test_supplement_cli_then_build_enriched_cli_reranks(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            objects = {"snapshot": self.snapshot, "records": [self.record], "menu": self.f.menu, "nutrition": self.f.nutrition, "selection": self.f.selection, "cache": self.f.cache, "target": self.target}
            for name, value in objects.items(): (folder / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
            supplement = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "nutrition_report.py"), "supplement", "--snapshot", str(folder / "snapshot.json"), "--records", str(folder / "records.json"), "--as-of", self.record["evidence"]["checked_at"], "--output", str(folder / "enriched.json")], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(supplement.returncode, 0, supplement.stderr)
            command = [sys.executable, "-X", "utf8", str(SCRIPTS / "catalog_builder.py"), "--output", str(folder / "rebuilt.json"), "build"]
            for name in ("menu", "nutrition", "selection", "cache", "target", "enriched"): command.extend(["--" + name, str(folder / (name + ".json"))])
            command.extend(["--max-variants", "2"])
            built = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(built.returncode, 0, built.stderr)
            snapshot = core.read_json(folder / "rebuilt.json")
            totals = [self.f.total(snapshot, i["id"])["nutrition"]["kcal"] for i in snapshot["items"] if i.get("available")]
            self.assertIn(553.3, totals)  # synthetic 500kcal main + public web corn; not a real meal claim.
            self.assertNotIn(553.3, [self.f.total(self.snapshot, i["id"])["nutrition"]["kcal"] for i in self.snapshot["items"] if i.get("available")])

    def test_enriched_from_other_service_context_is_rejected(self):
        self.f.selection["parameters"].update(orderType=2, beType=2, beCode="TEST-DELIVERY")
        self.f.cache["scope"] = builder.scope(self.f.selection)
        with self.assertRaises(ValueError): self.f.build(enriched=self.enriched)

    def test_fresh_mcp_nutrition_takes_precedence_over_web_supplement(self):
        self.f.nutrition["data"].append(fixtures.row("玉米杯小杯", 60, 2, 8, 1))
        rebuilt = self.f.build(enriched=self.enriched, target=self.target, max_variants=2)
        leaf = next(i for i in rebuilt["items"] if i["name"] == "玉米杯小杯")
        self.assertEqual(leaf["nutrition"]["kcal"], 60)
        self.assertEqual(leaf["nutrition_source"], "official")


if __name__ == "__main__": unittest.main()
