"""Whole-meal nutrition, adult population references and saved training-plan assessment."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import fit_core as core
import nutrition_support as ns

REFERENCE_URL = "https://www.nhc.gov.cn/wjw/yingyang/201710/fdade20feb8144ba921b412944ffb779/files/1739783563332_90405.pdf"
ENERGY_REFERENCES = (
    (18, 49, {"male": (2250, 2600, 3000), "female": (1800, 2100, 2400)}),
    (50, 64, {"male": (2100, 2450, 2800), "female": (1750, 2050, 2350)}),
    (65, 79, {"male": (2050, 2350, None), "female": (1700, 1950, None)}),
    (80, 120, {"male": (1900, 2200, None), "female": (1500, 1750, None)}),
)
ACTIVITIES = ("light", "moderate", "heavy")


def adult_reference(totals, age=None, activity="light"):
    if activity not in ACTIVITIES:
        raise ValueError("activity 应为 light / moderate / heavy")
    if age is not None and (isinstance(age, bool) or not isinstance(age, int) or not 18 <= age <= 120):
        raise ValueError("年龄需为 18–120 岁整数；此报告只使用成人参考")
    band = next(b for b in ENERGY_REFERENCES if b[0] <= (age if age is not None else 18) <= b[1])
    energy = totals["nutrition_bounds"]["kcal"]
    rows = {}
    for sex, data in band[2].items():
        daily = data[ACTIVITIES.index(activity)]
        rows[sex] = {"daily_kcal": daily, "meal_share_percent": None if daily is None else {"min": round(100 * energy["min"] / daily, 1), "max": None if energy["max"] is None else round(100 * energy["max"] / daily, 1)}}
    return {"kind": "population_reference", "version": "WS/T 578.1—2017，表1", "source_url": REFERENCE_URL, "age_band": f"{band[0]}–{band[1]}" if band[0] < 80 else "80+", "activity": activity, "assumed_example_age_band": age is None, "comparisons": rows, "suitability": "不能仅凭性别或单餐热量判断满足个体全天需求；参考值不自动写入用户目标", "scope": "健康成人普通生理状态的群体参考；不用于孕哺期、未成年人或疾病相关饮食目标"}


def meal_report(snapshot, selection, store=None, age=None, activity=None):
    catalog = core.validate_snapshot(snapshot, snapshot["day"])
    totals = core.aggregate(selection["items"], catalog)
    # Save the numerical result rather than assigning a made-up average to unknown products.
    status = "partial_lower_bound" if any(v["max"] is None for v in totals["nutrition_bounds"].values()) else "estimated_range" if totals["estimated"] else "complete_reference"
    result = {"day": snapshot["day"], "meal": snapshot.get("meal"), "label": selection.get("label", "本餐"), "nutrition_status": status, "total": totals, "personal_assessment": {"status": "not_configured", "message": "没有预录个人目标，暂不能判断是否符合个人健身能量需求"}, "warnings": []}
    if selection.get("total_cents") is not None:
        result["total_cents"] = core.cents(selection["total_cents"])
    if store is not None:
        configured = store.db.execute("SELECT 1 FROM settings WHERE key='profile'").fetchone()
        if configured:
            context = store.target(snapshot["day"], snapshot["meal"])
            reference = store.profile().get("nutrition_reference", {})
            age = reference.get("age") if age is None else age
            activity = activity or reference.get("activity", "light")
            intake = context["intake"]
            daily = context["plan"]["template"].get("daily", {})
            projected = {}
            remaining = {}
            for n, b in totals["nutrition_bounds"].items():
                used = intake["known_totals"][n]
                projected[n] = {"min": round(used + b["min"], 2), "max": None if b["max"] is None or n in intake["unknown_nutrients"] else round(used + b["max"], 2)}
                maximum = daily.get(n + "_max")
                remaining[n] = None if maximum is None else {"min": None if projected[n]["max"] is None else round(maximum - projected[n]["max"], 2), "max": round(maximum - projected[n]["min"], 2)}
            assessment = ns.target_assessment(totals, context["target"])
            result["personal_assessment"] = {"status": assessment["status"], "active_template": context["plan"]["active_template"], "meal_target": context["target"], "meal_fit": assessment, "daily_target": daily, "recorded_before_meal": intake["known_totals"], "projected_recorded_plus_this_meal": projected, "remaining_to_daily_max": remaining, "all_consumption_so_far_recorded": intake["all_consumption_so_far_recorded"], "message": "按已保存的当日模板评价本餐；全天结果为既有摄入加候选餐，不代表已实际吃完或覆盖完整一天"}
            result["warnings"].extend(context["warnings"])
    result["adult_reference"] = adult_reference(totals, age, activity or "light")
    if status == "partial_lower_bound":
        result["warnings"].append("仍有未知营养：显示已知下限，max=null 表示无可靠上界；不能当作整餐总值或据此承诺替换后更低")
    if totals["estimated"]:
        result["warnings"].append("包含第三方数据或估算；即使区间落在目标内，也只表示估算符合")
    for source in totals["nutrition_sources"]:
        item = catalog[source["id"]]
        data = item.get("nutrition", {})
        if data.get("kcal") == 0 and data.get("energy_kj", 0) > 0:
            result["warnings"].append(f"{item['name']} 的 kcal=0 但 kJ>0，需核实官方 0 的含义；未自动覆盖")
    return result


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="网页营养补充与整餐/训练目标汇总，无网络与订单操作")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("supplement")
    p.add_argument("--snapshot", required=True)
    p.add_argument("--records", required=True)
    p.add_argument("--as-of", default=core.today())
    p.add_argument("--output", required=True, help="写出可直接推荐使用的补充后菜单快照")
    p = sub.add_parser("report")
    p.add_argument("--snapshot", required=True)
    p.add_argument("--selection", required=True)
    p.add_argument("--db", help="已有固定账本，只读；不传则仅给群体参考")
    p.add_argument("--age", type=int)
    p.add_argument("--activity", choices=ACTIVITIES)
    p.add_argument("--output")
    args = parser.parse_args()
    store = None
    try:
        snapshot = core.read_json(args.snapshot)
        if args.command == "supplement":
            result = ns.supplement(snapshot, core.read_json(args.records), args.as_of)
            core.validate_snapshot(result["snapshot"], snapshot["day"])
            Path(args.output).write_text(json.dumps(result["snapshot"], ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            print(json.dumps({"output": str(Path(args.output).resolve()), "changes": result["changes"]}, ensure_ascii=False))
        else:
            if args.db:
                path = Path(args.db).resolve()
                if not path.is_file():
                    raise ValueError("账本路径不存在；营养报告不会创建新账本")
                store = core.Store.__new__(core.Store)
                store.db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
                store.db.row_factory = sqlite3.Row
            result = meal_report(snapshot, core.read_json(args.selection), store, args.age, args.activity)
            serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
            if args.output:
                Path(args.output).write_text(serialized, encoding="utf-8")
                print(json.dumps({"output": str(Path(args.output).resolve())}, ensure_ascii=False))
            else:
                print(serialized)
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
