"""Offline presentation of a planner recommendation; no account or order actions."""
import argparse
from collections import Counter
from datetime import datetime
from html import escape
import json
from pathlib import Path
from string import Template

from fit_core import NUTRIENTS, cents, payable_cents


def report_data(result):
    if result.get("phase") != "recommended" or result.get("kind") not in ("demo", "official"):
        raise ValueError("需要 recommend 完成核价后的 JSON，不能使用待核价请求")
    budget = cents(result["budget_cents"])
    plans, seen = [], set()
    for candidate in result.get("choices", []) + result.get("over_budget_options", []):
        if candidate["key"] in seen:
            continue
        if candidate.get("violations"):
            raise ValueError("报告不能包含违反营养或餐品限制的方案")
        stamp = datetime.fromisoformat(candidate["quoted_at"])
        if stamp.tzinfo is None:
            raise ValueError("报价时间需要时区")
        actual = Counter()
        names = {}
        for part in candidate.get("nutrition_sources", []):
            actual[part["id"]] += part["quantity"]
            names[part["id"]] = part["name"]
        if not actual:
            raise ValueError("缺少实际组成，不能把套餐名称当成完整餐品明细")
        plans.append({"title": " + ".join(i["name"] + " ×" + str(i["quantity"]) for i in candidate["items"]),
                      "labels": candidate.get("labels", ["超预算参考"]),
                      "price": payable_cents(candidate["total_cents"]), "quoted_at": stamp.isoformat(),
                      "items": [{"id": k, "name": names[k], "quantity": q} for k, q in actual.items()],
                      "nutrition": {n: candidate["nutrition"].get(n) for n in NUTRIENTS},
                      "bounds": {n: candidate.get("nutrition_bounds", {}).get(n) for n in NUTRIENTS},
                      "estimated": bool(candidate.get("estimated"))})
        seen.add(candidate["key"])
    return {"kind": result["kind"], "day": result["day"], "budget": budget,
            "plans": plans, "warnings": result.get("warnings", []),
            "target": {k: v for k, v in result.get("target", {}).items() if k in {n+s for n in NUTRIENTS for s in ("_min", "_max")}},
            "coverage": {k: result.get(k, 0) for k in ("priced_count", "unquoted_count", "quote_failure_count")},
            "scope": "仅展示本次候选，不代表全菜单最低价。调预算只筛选已有报价，不查询新价格、不下单，也不放宽营养目标。"}


def render_report(result):
    data = report_data(result)
    template = Path(__file__).resolve().parent.parent / "assets" / "meal-report.html"
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return Template(template.read_text(encoding="utf-8")).substitute(data=payload, budget=f"{data['budget']/100:.2f}", label=escape("合成示例 · 非真实售价" if data["kind"] == "demo" else "已核价快照 · 价格有效性见每张卡片"))


def main():
    parser = argparse.ArgumentParser(description="将 recommend 结果生成离线选餐比较页")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("输出不能覆盖输入 JSON")
    result = json.loads(args.input.read_text(encoding="utf-8-sig"))
    html = render_report(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(json.dumps({"html": str(args.output.resolve()), "synthetic_demo": result["kind"] == "demo"}, ensure_ascii=True))


if __name__ == "__main__":
    main()
