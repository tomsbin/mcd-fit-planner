"""Offline demo; all prices and food nutrition are synthetic. Never uses a real ledger."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "skill/mcd-fit-planner/scripts"))
spec = importlib.util.spec_from_file_location("fit_core", ROOT / "skill/mcd-fit-planner/scripts/fit_core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def sample_profile(day="2026-10-14"):
    def template(target, daily):
        return {"daily": {"kcal_max": daily}, "meals": {
            "breakfast": {"budget_cents": 2500, "target": {"kcal_min": 300, "kcal_max": 550}},
            "lunch": {"budget_cents": 3500, "target": target},
            "dinner": {"budget_cents": 3500, "target": {"kcal_min": 450, "kcal_max": 750, "protein_g_min": 25}}}}
    return {"start_date": day, "cycle": ["训练A", "高碳训练B", "计划休息", "训练C"], "rest_template": "计划休息", "defaults": {"store_code": "DEMO", "context": "演示自取", "excluded_ids": [], "required_ids": []}, "templates": {
        "训练A": template({"kcal_min": 500, "kcal_max": 750, "protein_g_min": 30, "fat_g_max": 25}, 2200),
        "高碳训练B": template({"kcal_min": 600, "kcal_max": 850, "protein_g_min": 25, "carb_g_min": 80}, 2400),
        "计划休息": template({"kcal_min": 400, "kcal_max": 650, "protein_g_min": 25}, 2000),
        "训练C": template({"kcal_min": 500, "kcal_max": 750, "protein_g_min": 30}, 2200)}}


def sample_snapshot(day="2026-10-14", kind="demo"):
    def item(identifier, name, category, kcal, protein, carb, fat, components=None):
        value = {"id": identifier, "name": "演示·" + name, "category": category, "nutrition_source": "synthetic", "nutrition": {"kcal": kcal, "protein_g": protein, "carb_g": carb, "fat_g": fat}}
        if components:
            value["components"] = components
        return value
    snapshot = {"kind": kind, "day": day, "meal": "lunch", "store_code": "DEMO", "context": "演示自取", "items": [
        item("chicken", "鸡肉主食", "main", 420, 32, 42, 14),
        item("beef", "牛肉主食", "main", 510, 30, 46, 23),
        item("side", "小份配餐", "side", 150, 3, 25, 5),
        item("zero", "无糖饮料", "drink", 0, 0, 0, 0),
        item("juice", "果味饮料", "drink", 130, 0, 32, 0),
        item("bundle-zero", "鸡肉套餐（无糖）", "bundle", 570, 35, 67, 19, ["chicken", "side", "zero"]),
        item("bundle-juice", "鸡肉套餐（果味）", "bundle", 700, 35, 99, 19, ["chicken", "side", "juice"]),
        item("unknown", "新品（营养未收录）", "main", None, None, None, None)]}

    if kind == "official":
        snapshot["bundle_comparison"] = {"policy": "bundle_first", "required_codes": ["bundle-zero", "bundle-juice"], "built_codes": ["bundle-zero", "bundle-juice"], "missing_detail_codes": [], "scope": "合成菜单的两个套餐；不代表真实菜单"}
    return snapshot


def sample_quotes(request, snapshot):
    prices = {"chicken": 2200, "beef": 2500, "side": 900, "zero": 500, "juice": 600, "bundle-zero": 2800, "bundle-juice": 2900}
    return {"kind": snapshot["kind"], "context_id": request["context_id"], "store_code": snapshot["store_code"], "context": snapshot["context"], "quotes": [{"key": c["key"], "total_cents": sum(prices[i["id"]] * i["quantity"] for i in c["items"]), "quoted_at": snapshot["day"] + "T12:00:00+08:00"} for c in request["quote_requests"]]}


def run_demo():
    with tempfile.TemporaryDirectory(prefix="mcd-fit-demo-") as tmp:
        store = core.Store(Path(tmp) / "demo.sqlite3")
        try:
            store.configure(sample_profile())
            print("麦当劳饮食计划助手 · 离线演示\n所有餐品、价格、营养、个人目标均为合成示例，不连接账号。\n")
            store.override("2026-10-15", "rest")
            print("【临时休息顺延】")
            for plan in store.schedule("2026-10-14", 5):
                print(f"{plan['day']}  {plan['active_template']}  ({plan['mode']})")
            snapshot = sample_snapshot()
            request = core.recommend(store, snapshot["day"], "lunch", snapshot)
            result = core.recommend(store, snapshot["day"], "lunch", snapshot, sample_quotes(request, snapshot))
            print("\n【今天午餐】")
            for c in result["choices"]:
                n = c["nutrition"]
                print(f"{' / '.join(c['labels'])}：{' + '.join(i['name'] for i in c['items'])}")
                print(f"  ¥{c['total_cents']/100:.2f} | {n['kcal']} kcal | 蛋白质 {n['protein_g']}g | 碳水 {n['carb_g']}g | 脂肪 {n['fat_g']}g")
            print("  提示：" + "；".join(result["warnings"]))
            before = {"items": [{"id": "bundle-juice", "quantity": 1}], "total_cents": 2900}
            after = {"items": [{"id": "bundle-zero", "quantity": 1}], "total_cents": 2800}
            difference = core.compare(snapshot, before, after)
            print("\n【替换饮料】")
            print(f"果味 → 无糖：价差 ¥{difference['price_delta_cents']/100:.2f}；营养变化 {difference['nutrition_delta_after_minus_before']}")
            print("高碳日不能仅凭热量较少就推荐替换，应先核对用户碳水目标。")
            catalog = {i["id"]: i for i in snapshot["items"]}
            intake = {"id": "demo-lunch", "day": "2026-10-14", "meal": "lunch", "items": [{"name": catalog[i]["name"], "quantity": qty, "source": "synthetic", "nutrition": catalog[i]["nutrition"]} for i, qty in (("chicken", 1), ("side", 0.5), ("zero", 1))]}
            day_result = store.record_intake(intake)
            print("\n【实际摄入】鸡肉主食吃完、配餐吃半份、无糖饮料喝完")
            print(f"已记录营养：{day_result['known_totals']}；其他餐食尚未确认完整。消费仍按整份付款记。")
            for identifier, day, amount in (("demo-1", "2026-10-14", 2800), ("demo-2", "2026-10-15", 2900), ("demo-3", "2026-09-20", 2500)):
                store.bill({"id": identifier, "day": day, "status": "paid", "paid_cents": amount, "items": [{"name": "演示套餐", "quantity": 1}]})
            store.bill({"id": "demo-2", "day": "2026-10-15", "status": "partial_refund", "paid_cents": 2900, "refund_cents": 500})
            print("\n【消费账本】")
            for period in ("2026-10", "2026"):
                report = store.report(period)
                print(f"{period}：{report['paid_order_count']} 笔付款，净支出 ¥{report['net_cents']/100:.2f}，退款 ¥{report['refund_cents']/100:.2f}")
            print("\n演示库自动清理，真实账本未被修改。")
            return result
        finally:
            store.db.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    run_demo()
