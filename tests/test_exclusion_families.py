"""Menu product exclusion is distinct from excluding one recipe variant."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_catalog_builder as fixtures
import fit_core as core

ROOT = Path(__file__).resolve().parents[1]


class ExclusionFamilyTests(unittest.TestCase):
    def setUp(self):
        f = fixtures.DetailCatalogTests(); f.setUp()
        f.selection["items"][0]["expand"] = True
        f.selection["items"].append({"product_code": "burger", "category": "main"})
        f.cache["responses"]["burger"] = fixtures.response({"code": "burger", "name": "测试汉堡", "rounds": []})
        self.snapshot = f.build()
        self.catalog = core.validate_snapshot(self.snapshot, core.today())
        self.variants = [i["id"] for i in self.snapshot["items"] if i["category"] == "bundle"]

    def candidates(self, excluded, required=None):
        return core.expand_candidates(self.snapshot, self.catalog, required or [], excluded)

    def selected_ids(self, results):
        return {i["id"] for c in results for i in c["items"]}

    def test_root_excludes_all_variants_but_keeps_standalone_main(self):
        self.assertGreater(len(self.variants), 1)
        ids = self.selected_ids(self.candidates(["combo"]))
        self.assertFalse(set(self.variants) & ids)
        self.assertIn("burger", ids)

    def test_specific_variant_excludes_only_that_recipe(self):
        chosen = self.variants[1]
        ids = self.selected_ids(self.candidates([chosen]))
        self.assertNotIn(chosen, ids)
        self.assertEqual(set(self.variants) - {chosen}, set(self.variants) & ids)

    def test_root_exclusion_works_when_default_variant_is_missing(self):
        self.snapshot["items"] = [i for i in self.snapshot["items"] if i["id"] != "combo"]
        self.catalog = core.validate_snapshot(self.snapshot, core.today())
        self.assertFalse(set(self.variants) & self.selected_ids(self.candidates(["combo"])))

    def test_two_menu_codes_exclude_sixteen_synthetic_internal_ids(self):
        # Reproduce the reported identifier shape; recipes/stock are synthetic.
        roots = []
        for code in ("9900005462", "9900005466"):
            for n in range(8):
                item = copy.deepcopy(self.catalog[self.variants[n % len(self.variants)]])
                item["id"] = code + "~" + f"{n:012x}"
                item["mcp_item"]["productCode"] = code
                roots.append(item)
        self.snapshot["items"] = [i for i in self.snapshot["items"] if i["category"] != "bundle"] + roots
        self.catalog = core.validate_snapshot(self.snapshot, core.today())
        self.assertEqual(len({i["id"] for i in roots}), 16)
        self.assertEqual(self.selected_ids(self.candidates(["9900005462", "9900005466"])), {"burger"})

    def test_root_matching_is_delimited_not_arbitrary_string_prefix(self):
        sibling = copy.deepcopy(self.catalog["combo"]); sibling["id"] = "combo2~abcdef"
        sibling["mcp_item"]["productCode"] = "combo2"
        self.snapshot["items"].append(sibling); self.catalog[sibling["id"]] = sibling
        self.assertIn(sibling["id"], self.selected_ids(self.candidates(["combo"])))

    def test_menu_code_works_with_custom_internal_root_alias(self):
        for item in self.snapshot["items"]:
            if item["category"] == "bundle": item["id"] = item["id"].replace("combo", "alias", 1)
        self.catalog = core.validate_snapshot(self.snapshot, core.today())
        ids = self.selected_ids(self.candidates(["combo"]))
        self.assertFalse(any(i.startswith("alias") for i in ids))

    def test_explicit_basket_cannot_reintroduce_excluded_variant(self):
        self.snapshot["requested_baskets"] = [{"items": [{"id": self.variants[-1], "quantity": 2}]}]
        self.assertFalse(set(self.variants) & self.selected_ids(self.candidates(["combo"])))

    def test_required_variant_conflicts_with_excluded_root(self):
        with self.assertRaises(ValueError): self.candidates(["combo"], [self.variants[-1]])
        self.snapshot["required_quantities"] = {self.variants[-1]: 1}
        with self.assertRaises(ValueError): self.candidates(["combo"])

    def test_component_product_code_excludes_only_bundles_containing_it(self):
        result = self.candidates(["fries"])
        roots = self.selected_ids(result) & set(self.variants)
        self.assertTrue(roots)
        for c in result:
            codes = {self.catalog[i].get("evidence", {}).get("detail_code") for i in core.physical_contents(c["items"], self.catalog)}
            self.assertNotIn("fries", codes)

    def test_review_rejects_family_and_does_not_authorize_excluded_baseline(self):
        def selected(identifier, amount):
            items = [{"id": identifier, "quantity": 1}]
            return {"items": items, "quote": {"key": core.candidate_key(items), "kind": "official", "store_code": "TEST", "context": self.snapshot["context"], "total_cents": amount, "quoted_at": core.datetime.now(core.CN_TIME).isoformat()}}
        baseline = selected("combo", 3000)
        result = core.review_basket(self.snapshot, baseline, [selected(self.variants[-1], 2000)], {"bundles_checked": True, "coupons_checked": True}, excluded_ids=["combo"])
        self.assertEqual(result["replacement_recommendations"], [])
        self.assertEqual(result["equivalent_recommendations"], [])
        self.assertIn("含用户排除的餐品", result["rejected"][0]["violations"])
        self.assertNotIn(baseline["quote"]["key"], result["reviewed_prices"])

    def test_cli_exclude_accepts_menu_root_code(self):
        from demo import sample_profile
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); store = core.Store(folder / "test.sqlite3")
            profile = sample_profile(core.today()); profile["defaults"].update(store_code="TEST", context=self.snapshot["context"])
            for t in profile["templates"].values(): t["meals"]["lunch"]["target"] = {}
            store.configure(profile); store.db.close()
            (folder / "snapshot.json").write_text(json.dumps(self.snapshot), encoding="utf-8")
            process = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "skill/mcd-fit-planner/scripts/fit_core.py"), "--db", str(folder / "test.sqlite3"), "recommend", "--snapshot", str(folder / "snapshot.json"), "--meal", "lunch", "--exclude", "combo"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertFalse(set(self.variants) & self.selected_ids(json.loads(process.stdout)["quote_requests"]))


if __name__ == "__main__": unittest.main()
