"""Recap calculations use isolated ledgers and deliberately fictional nutrition."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import demo
import fit_core as core
import period_recap as recap
import recap_render as render


class PeriodRecapTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "ledger.sqlite3"
        self.store = core.Store(self.path)
        self.profile = demo.sample_profile("2024-01-01")
        self.profile["cycle"] = ["训练A"]
        self.store.configure(self.profile)

    def tearDown(self):
        self.store.db.close()
        self.folder.cleanup()

    def intake(self, identifier="food", day="2024-02-03", meal="lunch", venue="mcdonalds", kcal=600, **extra):
        entry = {"id": identifier, "day": day, "meal": meal, "venue": venue,
            "items": [{"name": "合成餐", "quantity": 1, "source": "official",
                       "nutrition": {"kcal": kcal, "protein_g": 35, "carb_g": 75, "fat_g": 20}}], **extra}
        self.store.record_intake(entry)
        return entry

    def result(self, period="2024-02"):
        return recap.make_recap(self.store, period)

    def test_empty_period_is_not_zero_intake_or_perfect_compliance(self):
        r = self.result()
        self.assertEqual(r["billing"]["net_cents"], 0)
        self.assertEqual(r["mcd_intake"]["item_count"], 0)
        self.assertIsNone(r["mcd_intake"]["nutrition_bounds"]["kcal"]["max"])
        self.assertIsNone(r["meal_assessment"]["verified_within_percent"])

    def test_month_and_leap_year_boundaries(self):
        self.intake(day="2024-02-29")
        self.intake("outside", day="2024-03-01")
        self.assertEqual(len(self.result()["spending_buckets"]), 29)
        self.assertEqual(self.result()["mcd_recorded_days"], 1)
        self.assertEqual(self.result("2024")["mcd_recorded_days"], 2)
        for value in ("2024-13", "2024-00", "0000", "2024-2", "2024-02-01", "../test"):
            with self.assertRaises(ValueError): recap.period_dates(value)

    def test_bill_is_not_food_and_pending_is_not_spent(self):
        self.store.bill({"id": "paid", "day": "2024-02-01", "status": "paid", "paid_cents": 2290,
            "items": [{"name": "演示套餐", "quantity": 1}]})
        self.store.bill({"id": "pending", "day": "2024-02-02", "status": "pending", "paid_cents": 0})
        r = self.result()
        self.assertEqual(r["billing"]["net_cents"], 2290)
        self.assertEqual(r["mcd_recorded_days"], 0)
        self.assertEqual(r["meal_assessment"]["total"], 0)

    def test_refunds_are_original_month_net_and_favorites_are_purchase_counts(self):
        for identifier, status, refund in [("partial", "partial_refund", 500), ("full", "refunded", 2500)]:
            self.store.bill({"id": identifier, "day": "2024-02-03", "status": status,
                "paid_cents": 2500, "refund_cents": refund,
                "items": [{"name": identifier, "quantity": 2}]})
        r = self.result()
        self.assertEqual(r["billing"]["net_cents"], 2000)
        self.assertEqual(sum(b["net_cents"] for b in r["spending_buckets"]), 2000)
        self.assertEqual(r["favorites"], [{"name": "partial", "quantity": 2}])

    def test_actual_half_portions_and_other_venues_separate(self):
        e = self.intake()
        e["items"][0]["quantity"] = .5
        self.store.record_intake(e)
        self.intake("outside", meal="breakfast", venue="other", kcal=400)
        self.intake("legacy", meal="dinner", venue="unspecified", kcal=500)
        r = self.result()
        self.assertEqual(r["mcd_intake"]["nutrition_bounds"]["kcal"]["min"], 300)
        self.assertEqual(r["days"][0]["totals"]["nutrition_bounds"]["kcal"]["min"], 1200)
        self.assertEqual(r["unclassified_entry_count"], 1)

    def test_other_food_same_meal_counts_against_meal_target(self):
        self.intake()
        self.intake("dessert", venue="other", kcal=300)
        r = self.result()
        self.assertEqual(r["mcd_intake"]["nutrition_bounds"]["kcal"]["min"], 600)
        self.assertEqual(r["meals"][0]["assessment"]["status"], "above")

    def test_lunch_compliance_does_not_claim_full_day(self):
        self.intake()
        self.store.mark_complete("2024-02-03", True)
        r = self.result()
        self.assertEqual(r["meal_assessment"]["counts"]["within"], 1)
        self.assertEqual(r["daily_assessment"]["total"], 0)
        self.assertEqual(r["unclosed_recorded_days"], 1)

    def test_close_day_uses_other_food_and_invalidates_on_correction(self):
        self.intake()
        self.intake("outside", meal="dinner", venue="other", kcal=1500)
        self.store.close_day("2024-02-03", True)
        self.assertEqual(self.result()["daily_assessment"]["counts"]["within"], 1)
        self.intake("outside", meal="dinner", venue="other", kcal=1900)
        self.assertEqual(self.result()["daily_assessment"]["total"], 0)
        self.store.close_day("2024-02-03", True)
        self.assertEqual(self.result()["daily_assessment"]["counts"]["above"], 1)

    def test_move_record_invalidates_both_days(self):
        self.intake("first", day="2024-02-03")
        moved = self.intake("second", day="2024-02-04")
        self.store.close_day("2024-02-03", True)
        self.store.close_day("2024-02-04", True)
        moved["day"] = "2024-02-03"
        self.store.record_intake(moved)
        self.assertEqual(self.result()["daily_assessment"]["total"], 0)

    def test_historical_target_survives_profile_change_and_same_id_correction(self):
        e = self.intake()
        changed = copy.deepcopy(self.profile)
        changed["templates"]["训练A"]["meals"]["lunch"]["target"]["kcal_max"] = 550
        self.store.configure(changed)
        self.assertEqual(self.result()["meals"][0]["assessment"]["status"], "within")
        e["assessment_context"] = {"meal_target": {"kcal_max": 1}}
        self.store.record_intake(e)
        self.assertEqual(self.result()["meals"][0]["assessment"]["status"], "within")

    def test_conflicting_targets_are_not_arbitrarily_selected(self):
        self.intake()
        changed = copy.deepcopy(self.profile)
        changed["templates"]["训练A"]["meals"]["lunch"]["target"]["kcal_max"] = 550
        self.store.configure(changed)
        self.intake("second", kcal=50)
        self.assertEqual(self.result()["meals"][0]["assessment"]["status"], "conflict")

    def test_legacy_no_history_is_not_rejudged_by_current_goals(self):
        e = self.intake()
        with self.store.db:
            self.store.db.execute("UPDATE intake SET payload=? WHERE id=?", (json.dumps(e), e["id"]))
        self.assertEqual(self.result()["meal_assessment"]["counts"]["no_history"], 1)

    def test_estimated_range_crossing_limit_not_counted_as_compliant(self):
        e = self.intake()
        item = e["items"][0]
        item["source"] = "estimated"
        item["nutrition"]["kcal"] = None
        item["nutrition_bounds"] = {"kcal": {"min": 650, "max": 850}}
        self.store.record_intake(e)
        self.assertEqual(self.result()["meal_assessment"]["counts"]["uncertain"], 1)
        self.assertIsNone(self.result()["meal_assessment"]["verified_within_percent"])

    def test_estimated_fit_separate_from_verified_fit(self):
        e = self.intake()
        e["items"][0]["source"] = "estimated"
        self.store.record_intake(e)
        self.assertEqual(self.result()["meal_assessment"]["counts"]["estimated_within"], 1)
        self.assertIsNone(self.result()["meal_assessment"]["verified_within_percent"])

    def test_estimated_excess_not_in_verified_denominator(self):
        e = self.intake(kcal=850)
        e["items"][0]["source"] = "estimated"
        self.store.record_intake(e)
        self.assertEqual(self.result()["meal_assessment"]["counts"]["above"], 1)
        self.assertEqual(self.result()["meal_assessment"]["verified_count"], 0)

    def test_unknown_is_lower_bound_not_zero_and_zero_calorie_is_valid(self):
        e = self.intake(kcal=None)
        self.assertIsNone(self.result()["mcd_intake"]["nutrition_bounds"]["kcal"]["max"])
        self.assertEqual(self.result()["meal_assessment"]["counts"]["uncertain"], 1)
        e["items"][0]["nutrition"]["kcal"] = 0
        self.store.record_intake(e)
        self.assertEqual(self.result()["mcd_intake"]["nutrition_bounds"]["kcal"], {"min": 0, "max": 0})
        self.assertEqual(self.result()["meal_assessment"]["counts"]["below"], 1)

    def test_high_carb_day_checks_carb_target(self):
        self.profile["templates"]["训练A"]["meals"]["lunch"]["target"] = {"carb_g_min": 90}
        self.store.configure(self.profile)
        self.intake()
        self.assertEqual(self.result()["meals"][0]["assessment"]["nutrients"]["carb_g"]["status"], "below")

    def test_no_goal_is_explicit_and_not_compliant(self):
        self.profile["templates"]["训练A"]["meals"]["lunch"]["target"] = {}
        self.store.configure(self.profile)
        self.intake()
        self.assertEqual(self.result()["meal_assessment"]["counts"]["no_goal"], 1)
        self.assertIsNone(self.result()["meal_assessment"]["verified_within_percent"])

    def test_rest_override_is_captured_and_later_changes_do_not_rejudge(self):
        self.store.override("2024-02-03", "rest")
        self.intake(kcal=500)
        before = self.result()["meals"][0]
        self.store.override("2024-02-03", "normal")
        after = self.result()["meals"][0]
        self.assertEqual(before, after)
        self.assertEqual(after["templates"], [self.profile["rest_template"]])

    def test_mixed_over_and_under_preserves_both_reasons(self):
        e = self.intake(kcal=850)
        e["items"][0]["nutrition"]["protein_g"] = 10
        self.store.record_intake(e)
        a = self.result()["meals"][0]["assessment"]
        self.assertEqual(a["status"], "mixed")
        self.assertEqual(a["nutrients"]["kcal"]["status"], "above")
        self.assertEqual(a["nutrients"]["protein_g"]["status"], "below")

    def test_year_has_twelve_buckets_and_excludes_adjacent_year(self):
        for index, day in enumerate(["2024-01-01", "2024-12-31", "2025-01-01"]):
            self.store.bill({"id": str(index), "day": day, "status": "paid", "paid_cents": 1000})
        r = self.result("2024")
        self.assertEqual(len(r["spending_buckets"]), 12)
        self.assertEqual(r["billing"]["net_cents"], 2000)
        self.assertEqual(sum(b["net_cents"] for b in r["spending_buckets"]), 2000)

    def test_invalid_intake_bounds_rejected_before_saving(self):
        e = self.intake()
        e["items"][0]["nutrition_bounds"] = {"kcal": {"min": 900, "max": 500}}
        with self.assertRaises(ValueError): self.store.record_intake(e)
        self.assertEqual(self.result()["meal_assessment"]["counts"]["within"], 1)

    def test_full_day_unknown_nutrient_stays_uncertain_after_confirmation(self):
        self.intake(kcal=None)
        self.store.close_day("2024-02-03", True)
        self.assertEqual(self.result()["daily_assessment"]["counts"]["uncertain"], 1)

    def test_zero_quantity_not_counted_as_an_eaten_meal(self):
        e = self.intake()
        e["items"][0]["quantity"] = 0
        self.store.record_intake(e)
        self.assertEqual(self.result()["meal_assessment"]["total"], 0)
        self.assertEqual(self.result()["mcd_recorded_days"], 0)
        with self.assertRaises(ValueError): self.store.close_day(e["day"], True)

    def test_full_day_cannot_close_future_or_empty_day(self):
        with self.assertRaises(ValueError): self.store.close_day("2024-02-03", True)
        self.intake(day="2099-01-01")
        with self.assertRaises(ValueError): self.store.close_day("2099-01-01", True)

    def test_readonly_recap_does_not_create_or_mutate_ledger(self):
        self.intake()
        self.store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = hashlib.sha256(self.path.read_bytes()).digest()
        ro = recap.open_readonly(self.path)
        try:
            recap.make_recap(ro, "2024")
            with self.assertRaises(sqlite3.OperationalError): ro.db.execute("DELETE FROM intake")
        finally: ro.db.close()
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())
        missing = self.path.parent / "missing.sqlite3"
        with self.assertRaises(ValueError): recap.open_readonly(missing)
        self.assertFalse(missing.exists())

    def test_escaping_and_sensitive_fields_not_exported(self):
        e = self.intake()
        e["id"] = "SECRET_ID"
        e["phone"] = "SECRET_PHONE"
        e["items"][0]["name"] = '<script>alert("x")</script>'
        self.store.record_intake(e)
        self.store.bill({"id": "PRIVATE_BILL", "day": e["day"], "status": "paid", "paid_cents": 1000,
            "items": [{"name": e["items"][0]["name"], "quantity": 1}]})
        r = self.result()
        svg = render.render_svg(r)
        ET.fromstring(svg)
        html = render.render_html(r, svg)
        for secret in ("SECRET_ID", "SECRET_PHONE", "PRIVATE_BILL"):
            self.assertNotIn(secret, json.dumps(r))
            self.assertNotIn(secret, html)
        self.assertNotIn('<script>alert', svg)
        self.assertNotIn('<script>alert', html)

    def test_cli_rejects_output_overwriting_db_or_duplicate_paths(self):
        script = Path(core.__file__).with_name("period_recap.py")
        for extra in (["--output", str(self.path)], ["--output", "same.html", "--svg", "same.html"]):
            result = subprocess.run([sys.executable, str(script), "--db", str(self.path), "--period", "2024-02", *extra], capture_output=True)
            self.assertEqual(result.returncode, 2)

    def test_html_svg_artifacts_and_browser_fallback(self):
        r = self.result()
        folder = Path(self.folder.name)
        with patch.object(render, "render_png", return_value="browser unavailable"):
            result = render.write_artifacts(r, folder / "recap.html", folder / "recap.svg", folder / "recap.png", True)
        self.assertIn("png_note", result)
        self.assertNotIn("png", result)
        self.assertIn("合成示例", (folder / "recap.html").read_text(encoding="utf-8"))
        self.assertTrue((folder / "recap.svg").exists())


if __name__ == "__main__":
    unittest.main()
