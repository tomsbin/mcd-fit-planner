"""Read-only monthly/yearly recap. Facts are computed locally, never invented by a model."""
from __future__ import annotations

import argparse
import calendar
from collections import Counter, defaultdict
from datetime import date
import json
from pathlib import Path
import re
import sqlite3
import sys

import fit_core as core
import nutrition_support as ns

STATUS_NAMES = {"within": "达标", "estimated_within": "估算达标", "above": "超出目标",
    "below": "低于目标", "mixed": "部分超出且有不足", "uncertain": "营养待核实",
    "no_goal": "未设目标", "no_history": "缺少历史目标", "conflict": "目标记录不一致"}


def period_dates(period):
    if not re.fullmatch(r"\d{4}(-\d{2})?", period):
        raise ValueError("period 格式应为 YYYY 或 YYYY-MM")
    year = int(period[:4])
    if len(period) == 7:
        month = int(period[5:])
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    return date(year, 1, 1), date(year, 12, 31)


def sum_food(entries):
    result = {"nutrition_bounds": {n: {"min": 0, "max": 0} for n in core.NUTRIENTS},
              "estimated": False, "item_count": 0}
    for entry in entries:
        for item in entry["items"]:
            quantity = core.number(item["quantity"], "实际食用份数")
            if quantity == 0:
                continue
            result["item_count"] += 1
            intervals = ns.bounds(item)
            result["estimated"] |= item.get("source") not in ("official", "official_web")
            result["estimated"] |= any(b["min"] != b["max"] for b in intervals.values() if b["max"] is not None)
            for n, b in intervals.items():
                total = result["nutrition_bounds"][n]
                total["min"] += b["min"] * quantity
                total["max"] = None if total["max"] is None or b["max"] is None else total["max"] + b["max"] * quantity
    if not result["item_count"]:
        result["nutrition_bounds"] = {n: {"min": 0, "max": None} for n in core.NUTRIENTS}
    return result


def assess(entries, scope):
    totals = sum_food(entries)
    contexts = [e.get("assessment_context") for e in entries if any(i["quantity"] > 0 for i in e["items"])]
    if not contexts or any(c is None for c in contexts):
        return {"status": "no_history", "nutrients": {}}
    targets = [c.get(scope + "_target", {}) for c in contexts]
    if any(t != targets[0] for t in targets[1:]):
        return {"status": "conflict", "nutrients": {}}
    target = targets[0]
    core.validate_target(target)
    result = ns.target_assessment(totals, target)
    result["estimated"] = totals["estimated"]
    if result["status"] == "not_configured":
        result["status"] = "no_goal"
    elif result["status"] == "outside":
        states = {v["status"] for v in result["nutrients"].values()}
        result["status"] = "mixed" if {"above", "below"} <= states else "above" if "above" in states else "below"
    return result


def assessment_summary(assessments):
    counts = {s: 0 for s in STATUS_NAMES}
    for a in assessments:
        counts[a["status"]] += 1
    # Estimated compliance is visible but never included in verified compliance.
    verified = sum(a["status"] in {"within", "above", "below", "mixed"} and not a.get("estimated", False) for a in assessments)
    return {"counts": counts, "total": len(assessments), "verified_count": verified,
        "verified_within_percent": round(counts["within"] / verified * 100, 1) if verified else None}


def make_recap(store, period):
    start, end = period_dates(period)
    entries = [json.loads(r[0]) for r in store.db.execute(
        "SELECT payload FROM intake WHERE day BETWEEN ? AND ? ORDER BY day,id", (start.isoformat(), end.isoformat()))]
    bills = [json.loads(r[0]) for r in store.db.execute(
        "SELECT payload FROM bills WHERE day BETWEEN ? AND ? ORDER BY day,id", (start.isoformat(), end.isoformat()))]
    mcd_entries = [e for e in entries if e.get("venue") == "mcdonalds"]
    by_day, by_meal = defaultdict(list), defaultdict(list)
    for e in entries:
        by_day[e["day"]].append(e)
        by_meal[(e["day"], e["meal"])].append(e)
    meals = []
    for (day, meal), foods in sorted(by_meal.items()):
        if not any(e.get("venue") == "mcdonalds" and any(i["quantity"] > 0 for i in e["items"]) for e in foods):
            continue
        templates = sorted({e["assessment_context"]["template"] for e in foods if e.get("assessment_context")})
        meals.append({"day": day, "meal": meal, "templates": templates,
            "assessment": assess(foods, "meal"), "totals": sum_food(foods)})
    days = []
    for day, foods in sorted(by_day.items()):
        closure = store.db.execute("SELECT value FROM settings WHERE key=?", ("closed:" + day,)).fetchone()
        closed = bool(closure and json.loads(closure[0])["entries_hash"] == core.digest(foods))
        days.append({"day": day, "full_day_confirmed": closed, "totals": sum_food(foods),
            "assessment": assess(foods, "daily") if closed else None})
    favorites = Counter()
    paid_statuses = {"paid", "partial_refund", "refunded"}
    for bill in bills:
        if bill["status"] in {"paid", "partial_refund"}:
            for item in bill.get("items", []):
                favorites[item["name"]] += item["quantity"]
    buckets = []
    monthly = len(period) == 7
    for i in range(1, end.day + 1 if monthly else 13):
        prefix = f"{period}-{i:02d}" if monthly else f"{period}-{i:02d}"
        matched = [b for b in bills if b["day"].startswith(prefix) and b["status"] in paid_statuses]
        buckets.append({"label": str(i), "net_cents": sum(b["paid_cents"] - b["refund_cents"] for b in matched)})
    templates = Counter(t for m in meals for t in m["templates"])
    return {"schema_version": 1, "period": period, "kind": "month" if monthly else "year",
        "billing": {k: v for k, v in store.report(period).items() if k != "recorded_range"},
        "spending_buckets": buckets,
        "favorites": [{"name": name, "quantity": quantity} for name, quantity in favorites.most_common(3)],
        "mcd_intake": sum_food(mcd_entries),
        "mcd_recorded_days": len({e["day"] for e in mcd_entries if any(i["quantity"] > 0 for i in e["items"])}),
        "unclassified_entry_count": sum(e.get("venue", "unspecified") == "unspecified" for e in entries),
        "meal_assessment": assessment_summary([m["assessment"] for m in meals]),
        "daily_assessment": assessment_summary([d["assessment"] for d in days if d["full_day_confirmed"]]),
        "unclosed_recorded_days": sum(not d["full_day_confirmed"] for d in days),
        "templates": dict(templates), "meals": meals, "days": days,
        "notes": ["账单只计本地记录；退款计回原消费日期，待支付不计支出。",
            "常点榜来自已付款且未全额退款的购买份数；部分退款无法推断退掉哪道菜。",
            "吃麦摄入仅计明确标记 mcdonalds 的实际食用记录，不从账单推断。",
            "餐食评估包含该餐已记录的其他食物，按录入时目标判断；目标后改不追溯改判。",
            "全天仅评估明确 close-day 确认的饮食日；这不证明训练完成或长期健身效果。",
            "缺失、跨目标区间及估算结果分开显示，不算入可核实达标率。"]}


def open_readonly(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("账本不存在；先配置并录入数据，或使用 --demo")
    store = core.Store.__new__(core.Store)
    store.db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    store.db.row_factory = sqlite3.Row
    store.db.execute("BEGIN")
    return store


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="生成吃麦月度／年度网页、SVG 与 PNG 总结卡")
    parser.add_argument("--db", help="只读现有账本；不会创建或更改数据库")
    parser.add_argument("--period", required=True, help="YYYY 或 YYYY-MM")
    parser.add_argument("--output", required=True, help="离线 HTML 网页")
    parser.add_argument("--svg", help="分享长图 SVG")
    parser.add_argument("--png", help="分享长图 PNG（需要本机 Edge 或 Chrome）")
    parser.add_argument("--json", dest="json_path", help="可选脱敏统计 JSON，不含记录 ID 或凭证")
    parser.add_argument("--demo", action="store_true", help="临时合成数据，不读取真实账本")
    args = parser.parse_args()
    try:
        period_dates(args.period)
        if args.demo == bool(args.db):
            raise ValueError("二选一：--db 现有账本，或 --demo 合成示例")
        outputs = [Path(p).resolve() for p in (args.output, args.svg, args.png, args.json_path) if p]
        if len(outputs) != len(set(outputs)):
            raise ValueError("输出文件路径不能相同")
        if args.db:
            protected = {Path(args.db).resolve(), Path(str(Path(args.db).resolve()) + "-wal"), Path(str(Path(args.db).resolve()) + "-shm")}
            if protected.intersection(outputs):
                raise ValueError("输出路径不能覆盖账本文件")
        from recap_render import write_artifacts
        if args.demo:
            import tempfile
            from recap_demo import populate
            with tempfile.TemporaryDirectory(prefix="mcd-recap-demo-") as folder:
                store = core.Store(Path(folder) / "demo.sqlite3")
                try:
                    populate(store, args.period)
                    recap = make_recap(store, args.period)
                finally:
                    store.db.close()
        else:
            store = open_readonly(args.db)
            try:
                recap = make_recap(store, args.period)
            finally:
                store.db.close()
        result = write_artifacts(recap, args.output, args.svg, args.png, demo=args.demo)
        if args.json_path:
            Path(args.json_path).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_path).write_text(json.dumps(recap, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            result["json"] = str(Path(args.json_path).resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
