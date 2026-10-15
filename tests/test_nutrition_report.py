import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo import core, sample_profile, sample_snapshot
import nutrition_support as ns
import nutrition_report as nr


class NutritionReportTests(unittest.TestCase):
    def snapshot(self):
        return {"kind": "official", "day": "2026-10-14", "meal": "lunch", "store_code": "TEST", "context": "test", "parameters": {}, "items": [{"id": "corn", "name": "小杯玉米杯", "serving": "小杯", "category": "side", "nutrition": {}, "nutrition_source": "unknown", "mcp_item": {"productCode": "KEEP"}}]}

    def record(self):
        record = core.read_json(ROOT / "skill/mcd-fit-planner/assets/corn-small-cn.json")
        record.update({"item_id": "corn", "match": {"name": "小杯玉米杯", "serving": "小杯"}})
        return record

    def apply_record(self, record=None, snapshot=None):
        # Historical meal date differs from the date we rechecked web evidence.
        as_of = self.record()["evidence"]["checked_at"]
        return ns.supplement(snapshot or self.snapshot(), [record or self.record()], as_of)["snapshot"]

    def test_official_web_kj_conversion_and_provenance(self):
        snapshot = self.snapshot()
        snapshot["nutrition_warnings"] = [{"id": "corn", "reason": "not_found", "name": "小杯玉米杯"}]
        snapshot = self.apply_record(snapshot=snapshot)
        self.assertEqual(snapshot["nutrition_warnings"], [])
        self.assertEqual(snapshot["items"][0]["mcp_item"], {"productCode": "KEEP"})
        report = nr.meal_report(snapshot, {"items": [{"id": "corn", "quantity": 2}]})
        self.assertAlmostEqual(report["total"]["nutrition"]["kcal"], 106.6, places=2)
        self.assertEqual(report["total"]["nutrition"]["protein_g"], 4)
        self.assertEqual(report["nutrition_status"], "complete_reference")
        self.assertIn("Corn-Cup", report["total"]["nutrition_sources"][0]["supplements"][0]["evidence"]["url"])

    def test_cannot_overwrite_mcp_or_explicit_zero(self):
        snapshot = self.snapshot()
        snapshot["items"][0].update({"nutrition_source": "official", "nutrition": {"kcal": 0, "energy_kj": 50, "protein_g": 8}, "evidence": {"nutrition_name": "corn", "serving": "小杯"}})
        amended = self.apply_record(snapshot=snapshot)
        self.assertEqual(core.nutrition(amended["items"][0])["kcal"], 0)
        self.assertEqual(core.nutrition(amended["items"][0])["protein_g"], 8)
        core.validate_snapshot(amended, amended["day"])

    def test_wrong_serving_region_unofficial_domain_and_stale_cache_rejected(self):
        for mutation in (lambda r: r["match"].update(serving="大杯"), lambda r: r["evidence"].update(region="US"), lambda r: r["evidence"].update(url="https://example.com/a"), lambda r: r["evidence"].update(checked_at="2026-08-01")):
            record = self.record(); mutation(record)
            with self.assertRaises(ValueError): self.apply_record(record)

    def test_per_100g_requires_portion_evidence(self):
        record = self.record(); record.update(basis="per_100g", portion_g=50)
        with self.assertRaises(ValueError): self.apply_record(record)
        record["evidence"]["portion_evidence"] = "包装标注单份50克，核实与选定规格一致"
        snapshot = self.apply_record(record)
        self.assertAlmostEqual(core.nutrition(snapshot["items"][0])["kcal"], 223 / 4.184 / 2)

    def test_estimate_spanning_limit_cannot_claim_fit(self):
        record = self.record()
        record.update(source="database", nutrition={}, nutrition_bounds={"kcal": {"min": 100, "max": 200}})
        record["evidence"].update(url="https://example.com/nutrition", uncertainty_reason="测试：来源给出份量区间，不是产品实值")
        snapshot = self.apply_record(record)
        candidate = core.aggregate([{"id": "corn", "quantity": 1}], core.validate_snapshot(snapshot, snapshot["day"]))
        self.assertIsNone(candidate["nutrition"]["kcal"])
        self.assertEqual(ns.target_assessment(candidate, {"kcal_max": 150})["status"], "uncertain")
        self.assertTrue(core.violations(candidate, {"kcal_max": 150}))
        self.assertEqual(ns.target_assessment(candidate, {"kcal_max": 250})["status"], "estimated_within")

    def test_target_check_does_not_round_a_small_excess_away(self):
        record = self.record(); record["nutrition"]["kcal"] = 100.004
        snapshot = self.apply_record(record)
        candidate = core.aggregate([{"id": "corn", "quantity": 1}], core.validate_snapshot(snapshot, snapshot["day"]))
        self.assertEqual(candidate["nutrition"]["kcal"], 100)
        self.assertTrue(core.violations(candidate, {"kcal_max": 100}))

    def test_customized_menu_cannot_silently_use_standard_recipe(self):
        snapshot = self.snapshot()
        snapshot["items"][0]["mcp_item"]["modification"] = [{"name": "去酱"}]
        with self.assertRaises(ValueError): self.apply_record(snapshot=snapshot)

    def test_web_and_mcp_intake_preserve_reference_status(self):
        with tempfile.TemporaryDirectory() as temp:
            store = core.Store(Path(temp) / "test.sqlite3")
            try:
                intake = store.record_intake({"id": "test", "day": "2026-10-14", "meal": "lunch", "items": [{"name": "玉米", "quantity": 1, "source": "official_web", "nutrition": {"kcal": 53.3}}]})
                self.assertFalse(intake["estimated"])
                self.assertEqual(intake["known_totals"]["kcal"], 53.3)
            finally:
                store.db.close()

    def test_unknown_bundle_preserves_known_lower_bound(self):
        snapshot = self.apply_record()
        snapshot["items"].extend([{"id": "missing", "name": "测试未知酱", "category": "side", "nutrition": {}}, {"id": "bundle", "name": "测试套餐", "category": "bundle", "components": ["corn", "missing"]}])
        report = nr.meal_report(snapshot, {"items": [{"id": "bundle", "quantity": 1}]})
        self.assertEqual(report["nutrition_status"], "partial_lower_bound")
        self.assertEqual(report["total"]["known_subtotal"]["protein_g"], 2)
        self.assertIsNone(report["total"]["nutrition_bounds"]["kcal"]["max"])
        self.assertEqual(report["personal_assessment"]["status"], "not_configured")

    def test_adult_references_vary_age_activity_not_personal_prescription(self):
        total = {"nutrition_bounds": {"kcal": {"min": 900, "max": 900}}}
        default = nr.adult_reference(total)
        self.assertEqual(default["comparisons"]["female"]["meal_share_percent"]["max"], 50)
        self.assertTrue(default["assumed_example_age_band"])
        reference = nr.adult_reference(total, 55, "moderate")
        self.assertEqual(reference["comparisons"]["male"]["daily_kcal"], 2450)
        self.assertIsNone(nr.adult_reference(total, 70, "heavy")["comparisons"]["male"]["daily_kcal"])
        with self.assertRaises(ValueError): nr.adult_reference(total, 17)

    def test_saved_training_rest_and_high_carb_targets_drive_report(self):
        with tempfile.TemporaryDirectory() as temp:
            store = core.Store(Path(temp) / "test.sqlite3")
            try:
                profile = sample_profile()
                profile["nutrition_reference"] = {"age": 55, "activity": "moderate"}
                store.configure(profile)
                snapshot = sample_snapshot()
                report = nr.meal_report(snapshot, {"items": [{"id": "bundle-juice", "quantity": 1}]}, store)
                self.assertEqual(report["personal_assessment"]["active_template"], "训练A")
                self.assertEqual(report["adult_reference"]["age_band"], "50–64")
                store.override(snapshot["day"], "rest")
                report = nr.meal_report(snapshot, {"items": [{"id": "bundle-juice", "quantity": 1}]}, store)
                self.assertEqual(report["personal_assessment"]["active_template"], "计划休息")
                snapshot["day"] = "2026-10-16"
                report = nr.meal_report(snapshot, {"items": [{"id": "bundle-juice", "quantity": 1}]}, store)
                self.assertEqual(report["personal_assessment"]["active_template"], "高碳训练B")
            finally:
                store.db.close()

    def test_cli_missing_db_never_creates_another_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            (temp / "snapshot.json").write_text(json.dumps(self.apply_record()), encoding="utf-8")
            (temp / "selection.json").write_text(json.dumps({"items": [{"id": "corn", "quantity": 1}]}), encoding="utf-8")
            process = subprocess.run([sys.executable, str(ROOT / "skill/mcd-fit-planner/scripts/nutrition_report.py"), "report", "--snapshot", str(temp / "snapshot.json"), "--selection", str(temp / "selection.json"), "--db", str(temp / "absent.sqlite3")], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(process.returncode, 2)
            self.assertFalse((temp / "absent.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
