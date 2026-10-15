"""Deterministic local nutrition planner. Python 3.11+, no network or credentials."""
from __future__ import annotations

import argparse
import calendar
import csv
import copy
from collections import Counter
from contextlib import nullcontext
import hashlib
import itertools
import json
import math
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import nutrition_support as ns

NUTRIENTS = ("kcal", "protein_g", "carb_g", "fat_g")
CN_TIME = timezone(timedelta(hours=8))


def today():
    return datetime.now(CN_TIME).date().isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def number(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{label} 必须是有限数字且 ≥ {minimum}")
    return value


def cents(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("金额必须是非负整数分")
    return value


def payable_cents(value):
    value = cents(value)
    if value == 0:
        raise ValueError("零元报价待核实：先核查套餐选配、可售状态与实际券权益，不能自动当免费订单")
    return value


def validate_coupon_items(items):
    coupon_items = []
    for item in items:
        # Presence matters: empty or half-bound fields are not a full-price item.
        if "couponId" not in item and "couponCode" not in item:
            continue
        if any(not isinstance(item.get(k), str) or not item[k].strip() for k in ("couponId", "couponCode")):
            raise ValueError("用券商品必须同时保留非空 couponId 和 couponCode")
        if type(item.get("quantity")) is not int or item["quantity"] != 1:
            raise ValueError("一张券仅用于一份商品")
        coupon_items.append(item)
    if len(coupon_items) > 1:
        raise ValueError("一单仅使用一张券；不可自行叠加")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def validate_target(target):
    if not isinstance(target, dict):
        raise ValueError("营养目标必须是对象")
    allowed = {f"{n}_{side}" for n in NUTRIENTS for side in ("min", "max")}
    if set(target) - allowed:
        raise ValueError(f"未知目标字段: {sorted(set(target) - allowed)}")
    for key, value in target.items():
        number(value, key)
    for nutrient in NUTRIENTS:
        low, high = target.get(nutrient + "_min"), target.get(nutrient + "_max")
        if low is not None and high is not None and low > high:
            raise ValueError(f"{nutrient} 下限大于上限")


def validate_profile(profile):
    reference = profile.get("nutrition_reference", {})
    if not isinstance(reference, dict) or set(reference) - {"age", "activity"}:
        raise ValueError("nutrition_reference 仅接受 age / activity")
    if "age" in reference and (isinstance(reference["age"], bool) or not isinstance(reference["age"], int) or not 18 <= reference["age"] <= 120):
        raise ValueError("成人参考年龄需为 18–120 岁整数")
    if reference.get("activity", "light") not in ("light", "moderate", "heavy"):
        raise ValueError("活动水平应为 light / moderate / heavy")
    cycle, templates = profile.get("cycle"), profile.get("templates")
    if not isinstance(cycle, list) or not cycle or not isinstance(templates, dict):
        raise ValueError("需要非空 cycle 和 templates")
    date.fromisoformat(profile["start_date"])
    if any(key not in templates for key in cycle):
        raise ValueError("循环引用了不存在的模板")
    if profile.get("rest_template") not in templates:
        raise ValueError("rest_template 不存在")
    for template in templates.values():
        validate_target(template.get("daily", {}))
        if not template.get("meals"):
            raise ValueError("每个模板需要 meals")
        for meal in template["meals"].values():
            validate_target(meal["target"])
            cents(meal["budget_cents"])
    return profile


class Store:
    def __init__(self, path):
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS overrides(day TEXT PRIMARY KEY, mode TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS intake(id TEXT PRIMARY KEY, day TEXT NOT NULL, meal TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bills(id TEXT PRIMARY KEY, day TEXT NOT NULL, status TEXT NOT NULL, paid_cents INTEGER NOT NULL, refund_cents INTEGER NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS previews(id TEXT PRIMARY KEY, expires_at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL);
        """)

    def log(self, action, payload):
        self.db.execute("INSERT INTO audit(at,action,payload) VALUES (?,?,?)", (datetime.now(CN_TIME).isoformat(), action, json.dumps(payload, ensure_ascii=False)))

    def profile(self):
        row = self.db.execute("SELECT value FROM settings WHERE key='profile'").fetchone()
        if not row:
            raise ValueError("尚未设置计划，请先执行 configure")
        return json.loads(row[0])

    def configure(self, profile):
        validate_profile(profile)
        old = self.db.execute("SELECT value FROM settings WHERE key='profile'").fetchone()
        if old:
            previous = json.loads(old[0])
            if (previous["start_date"], previous["cycle"]) != (profile["start_date"], profile["cycle"]):
                if self.db.execute("SELECT 1 FROM overrides LIMIT 1").fetchone():
                    raise ValueError("已有休息/跳过记录；调整循环或起点前请先逐日清除覆盖，避免历史顺延被重新解释")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('profile',?)", (json.dumps(profile, ensure_ascii=False),))
            self.log("configure", {"profile": profile})
        return {"configured": True, "start_date": profile["start_date"], "cycle": profile["cycle"]}

    def override(self, day, mode):
        profile = self.profile()
        if date.fromisoformat(day) < date.fromisoformat(profile["start_date"]):
            raise ValueError("日期早于计划起点")
        if mode not in ("rest", "skip", "normal"):
            raise ValueError("mode 应为 rest / skip / normal")
        with self.db:
            if mode == "normal":
                self.db.execute("DELETE FROM overrides WHERE day=?", (day,))
            else:
                self.db.execute("INSERT OR REPLACE INTO overrides VALUES (?,?)", (day, mode))
            self.log("override", {"day": day, "mode": mode})
        return self.plan(day)

    def plan(self, day):
        profile = self.profile()
        start, current = date.fromisoformat(profile["start_date"]), date.fromisoformat(day)
        if current < start:
            raise ValueError("日期早于计划起点")
        holds = self.db.execute("SELECT COUNT(*) FROM overrides WHERE day>=? AND day<? AND mode='rest'", (start.isoformat(), day)).fetchone()[0]
        step = ((current - start).days - holds) % len(profile["cycle"])
        scheduled = profile["cycle"][step]
        override = self.db.execute("SELECT mode FROM overrides WHERE day=?", (day,)).fetchone()
        mode = override[0] if override else "normal"
        actual = profile["rest_template"] if mode in ("rest", "skip") else scheduled
        return {"day": day, "cycle_index": step, "scheduled_template": scheduled, "active_template": actual, "mode": mode, "template": profile["templates"][actual], "defaults": profile.get("defaults", {})}

    def schedule(self, start, days):
        if not 1 <= days <= 366:
            raise ValueError("days 范围 1–366")
        return [self.plan((date.fromisoformat(start) + timedelta(days=i)).isoformat()) for i in range(days)]

    def record_intake(self, entry):
        entry = copy.deepcopy(entry)
        date.fromisoformat(entry["day"])
        if not entry.get("id") or not entry.get("meal") or not entry.get("items"):
            raise ValueError("饮食记录需要 id / meal / items")
        if entry.get("venue", "unspecified") not in ("mcdonalds", "other", "unspecified"):
            raise ValueError("venue 应为 mcdonalds / other / unspecified")
        entry.setdefault("venue", "unspecified")
        for item in entry["items"]:
            if not item.get("name") or not item.get("source"):
                raise ValueError("每项饮食需要 name 和 source")
            number(item["quantity"], "实际食用份数")
            for nutrient in NUTRIENTS:
                if item.get("nutrition", {}).get(nutrient) is not None:
                    number(item["nutrition"][nutrient], nutrient)
            ns.bounds(item)
        previous = self.db.execute("SELECT payload FROM intake WHERE id=?", (entry["id"],)).fetchone()
        previous = json.loads(previous[0]) if previous else None
        # A correction keeps its historical target; supplied contexts are never trusted.
        if previous and (previous["day"], previous["meal"]) == (entry["day"], entry["meal"]):
            entry["assessment_context"] = previous.get("assessment_context")
        else:
            try:
                plan = self.plan(entry["day"])
                entry["assessment_context"] = {"template": plan["active_template"],
                    "meal_target": plan["template"].get("meals", {}).get(entry["meal"], {}).get("target", {}),
                    "daily_target": plan["template"].get("daily", {}),
                    "captured_at": datetime.now(CN_TIME).isoformat()}
            except ValueError:
                entry["assessment_context"] = None
        # Updating the same id is an explicit correction, not another meal.
        with self.db:
            for changed_day in {entry["day"], previous["day"] if previous else entry["day"]}:
                self.db.execute("DELETE FROM settings WHERE key=?", ("closed:" + changed_day,))
            self.db.execute("INSERT OR REPLACE INTO intake VALUES (?,?,?,?)", (entry["id"], entry["day"], entry["meal"], json.dumps(entry, ensure_ascii=False)))
            self.log("intake", {"id": entry["id"], "day": entry["day"]})
        return self.day_intake(entry["day"])

    def day_intake(self, day):
        date.fromisoformat(day)
        entries = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM intake WHERE day=? ORDER BY id", (day,))]
        totals = {n: 0.0 for n in NUTRIENTS}
        unknown = set()
        estimated = False
        for entry in entries:
            for item in entry["items"]:
                if item["quantity"] == 0:
                    continue
                estimated |= item["source"] not in ("official", "official_web")
                for n in NUTRIENTS:
                    value = item.get("nutrition", {}).get(n)
                    if value is None:
                        unknown.add(n)
                    else:
                        totals[n] += value * item["quantity"]
        complete = self.db.execute("SELECT value FROM settings WHERE key=?", ("complete:" + day,)).fetchone()
        return {"day": day, "known_totals": {n: round(v, 2) for n, v in totals.items()}, "unknown_nutrients": sorted(unknown), "estimated": estimated, "recorded_meals": sorted({e["meal"] for e in entries}), "all_consumption_so_far_recorded": bool(complete and complete[0] == "true"), "entries": entries}

    def mark_complete(self, day, complete):
        date.fromisoformat(day)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", ("complete:" + day, json.dumps(complete)))
            self.log("coverage", {"day": day, "complete": complete})
        return self.day_intake(day)

    def close_day(self, day, complete):
        """Explicit end-of-day coverage, separate from the intraday remainder flag."""
        current = self.day_intake(day)
        if complete and (day > today() or not any(i["quantity"] > 0 for e in current["entries"] for i in e["items"])):
            raise ValueError("仅能确认有实际饮食记录的当天或过去日期")
        payload = {"entries_hash": digest(current["entries"]),
                   "confirmed_at": datetime.now(CN_TIME).isoformat()}
        with self.db:
            if complete:
                self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", ("closed:" + day, json.dumps(payload)))
            else:
                self.db.execute("DELETE FROM settings WHERE key=?", ("closed:" + day,))
            self.log("close_day", {"day": day, "complete": complete})
        return {"day": day, "full_day_confirmed": complete}

    def target(self, day, meal):
        plan, intake = self.plan(day), self.day_intake(day)
        meal_config = plan["template"]["meals"].get(meal)
        if not meal_config:
            raise ValueError("该模板未设置此餐段")
        target = dict(meal_config["target"])
        warnings = []
        # Only apply a daily remainder as a hard limit when coverage is explicitly affirmed.
        if intake["all_consumption_so_far_recorded"]:
            for n in NUTRIENTS:
                maximum = plan["template"].get("daily", {}).get(n + "_max")
                if maximum is None:
                    continue
                if n in intake["unknown_nutrients"]:
                    warnings.append(f"{n} 有未知记录，无法核实当天剩余额度")
                    continue
                remainder = max(0, maximum - intake["known_totals"][n])
                target[n + "_max"] = min(target.get(n + "_max", remainder), remainder)
        else:
            warnings.append("其他餐食记录未确认完整，按预设当餐目标推荐；当天已知合计不代表全天总摄入")
        if intake["estimated"]:
            warnings.append("饮食记录包含估算，剩余额度也是估算")
        return {"plan": plan, "meal": meal, "target": target, "budget_cents": meal_config["budget_cents"], "intake": intake, "warnings": warnings}

    def bill(self, bill, commit=True):
        date.fromisoformat(bill["day"])
        if not bill.get("id") or bill["status"] not in ("pending", "paid", "cancelled", "refunded", "partial_refund"):
            raise ValueError("账单 id 或 status 无效")
        paid, refund = cents(bill["paid_cents"]), cents(bill.get("refund_cents", 0))
        if refund > paid:
            raise ValueError("退款不能大于付款")
        if bill["status"] == "refunded" and refund != paid:
            raise ValueError("全额退款状态要求退款金额等于付款")
        if bill["status"] == "partial_refund" and not 0 < refund < paid:
            raise ValueError("部分退款必须介于 0 和付款之间")
        if bill["status"] == "paid" and refund:
            raise ValueError("已有退款请使用退款状态")
        if bill["status"] in ("pending", "cancelled") and (paid or refund):
            raise ValueError("待付款/未支付取消记录金额应为 0；已付款退款请用退款状态")
        existing = self.db.execute("SELECT payload FROM bills WHERE id=?", (bill["id"],)).fetchone()
        previous = json.loads(existing[0]) if existing else None
        if previous:
            if previous["day"] != bill["day"]:
                raise ValueError("同一账单的原消费日期不能在同步时改变")
            previous_paid = previous["status"] in ("paid", "partial_refund", "refunded")
            if previous_paid and (bill["status"] in ("pending", "cancelled") or refund < previous["refund_cents"]):
                return {**previous, "sync_note": "忽略旧状态，保留已核实付款和累计退款"}
            if previous_paid and previous["paid_cents"] != paid:
                raise ValueError("已核实付款金额发生冲突，请检查原始订单，不能静默覆盖")
        # Whitelist persistent fields; payment links, address and token never enter the ledger.
        safe = {"id": bill["id"], "day": bill["day"], "status": bill["status"], "paid_cents": paid, "refund_cents": refund, "source": bill.get("source", "manual"), "items": [{"name": i["name"], "quantity": number(i.get("quantity", 1), "购买份数")} for i in bill.get("items", [])]}
        if previous and not safe["items"]:
            safe["items"] = previous["items"]
        if bill.get("discount_cents") is not None:
            safe["discount_cents"] = cents(bill["discount_cents"])
        elif previous and "discount_cents" in previous:
            safe["discount_cents"] = previous["discount_cents"]
        with (self.db if commit else nullcontext()):
            self.db.execute("INSERT OR REPLACE INTO bills VALUES (?,?,?,?,?,?)", (safe["id"], safe["day"], safe["status"], paid, refund, json.dumps(safe, ensure_ascii=False)))
            self.log("bill", {"id": safe["id"], "status": safe["status"]})
        return safe

    def import_bills(self, normalized):
        results = []
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            for bill in normalized["bills"]:
                results.append(self.bill(bill, commit=False))
        return {"merged": len(results), "bills": results, "unresolved": normalized.get("unresolved", []), "coverage": normalized.get("coverage", "仅本次导入记录")}

    def report(self, period):
        if not re.fullmatch(r"\d{4}(-\d{2})?", period):
            raise ValueError("period 格式应为 YYYY 或 YYYY-MM")
        parts = period.split("-")
        year = int(parts[0])
        if len(parts) == 2:
            month = int(parts[1])
            start = date(year, month, 1)
            end = date(year, month, calendar.monthrange(year, month)[1])
        else:
            start, end = date(year, 1, 1), date(year, 12, 31)
        rows = [dict(r) for r in self.db.execute("SELECT * FROM bills WHERE day BETWEEN ? AND ? ORDER BY day,id", (start.isoformat(), end.isoformat()))]
        paid = [r for r in rows if r["status"] in ("paid", "refunded", "partial_refund")]
        gross = sum(r["paid_cents"] for r in paid)
        refunds = sum(r["refund_cents"] for r in paid)
        monthly = {}
        for r in paid:
            key = r["day"][:7]
            monthly[key] = monthly.get(key, 0) + r["paid_cents"] - r["refund_cents"]
        all_range = self.db.execute("SELECT MIN(day), MAX(day) FROM bills").fetchone()
        discount_known = [json.loads(r["payload"])["discount_cents"] for r in paid if "discount_cents" in json.loads(r["payload"])]
        return {"period": period, "paid_order_count": len(paid), "gross_cents": gross, "refund_cents": refunds, "net_cents": gross - refunds, "average_net_cents": round((gross - refunds) / len(paid)) if paid else 0, "known_discount_cents": sum(discount_known), "discount_known_order_count": len(discount_known), "pending_count": sum(r["status"] == "pending" for r in rows), "monthly_net_cents": monthly, "recorded_range": list(all_range), "coverage": "仅包含本地已记录账单，不能据此认定历史完整；优惠仅累计有明细记录，退款不推算返还优惠"}

    def export(self, path):
        def cell(value):
            value = str(value)
            return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value
        with open(path, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["账单标识", "消费日期", "状态", "付款元", "退款元", "净支出元", "餐品"])
            for row in self.db.execute("SELECT * FROM bills ORDER BY day,id"):
                entry = json.loads(row["payload"])
                writer.writerow([cell(row["id"]), row["day"], row["status"], f"{row['paid_cents']/100:.2f}", f"{row['refund_cents']/100:.2f}", f"{(row['paid_cents']-row['refund_cents'])/100:.2f}", cell("; ".join(f"{i['name']} × {i['quantity']}" for i in entry["items"]))])
        return {"exported": str(Path(path).resolve())}

    def preview(self, day, meal, snapshot, quotes, key, review=None):
        if snapshot["kind"] != "official":
            raise ValueError("模拟数据不能用于创建真实订单")
        if not isinstance(snapshot.get("bundle_comparison"), dict):
            raise ValueError("缺少菜单套餐发现记录；先用当前菜单执行discover/requests/build，不能沿用仅单点的旧快照下单")
        result = recommend(self, day, meal, snapshot, quotes)
        choice = next((c for c in result["choices"] if c["key"] == key), None)
        if not choice:
            raise ValueError("只能预览当前符合目标和预算的推荐方案")
        if not isinstance(review, dict) or not review.get("can_finalize"):
            raise ValueError("套餐与优惠比较尚未完成，不能进入下单预览；先执行 review 并展示结果")
        if result.get("bundle_readiness", {}).get("status") == "incomplete":
            raise ValueError("相关套餐详情或计价未完成，不能只按单点进入订单预览")
        if review.get("snapshot_hash") != digest(snapshot) or review.get("target_hash") != digest(self.target(day, meal)["target"]):
            raise ValueError("套餐比较与当前菜单或营养目标不一致")
        if choice["total_cents"] not in review.get("reviewed_prices", {}).get(key, []):
            raise ValueError("所选方案未在套餐比较中核价")
        payload = choice["quote"].get("order_payload")
        if not isinstance(payload, dict) or not payload:
            raise ValueError("报价需要按 MCP 当前 schema 构造完整 order_payload")
        calculated = choice["quote"].get("calculate_payload")
        if calculated is not None:
            if choice["quote"].get("calculate_payload_hash") != digest(calculated):
                raise ValueError("计价参数哈希不一致")
            fields = ("storeCode", "beCode", "orderType", "beType", "reservationDate", "gmServiceCode", "withOrder", "needTableware", "items")
            if any(payload.get(k) != calculated.get(k) for k in fields):
                raise ValueError("下单与计价的餐品/优惠券/场景参数不同，请重新计价")
            def physical_items(items):
                counts = Counter()
                for item in items:
                    quantity = number(item["quantity"], "计价商品数量", 1)
                    if int(quantity) != quantity:
                        raise ValueError("计价商品数量必须为整数")
                    spec = {k: v for k, v in item.items() if k not in ("quantity", "couponId", "couponCode")}
                    counts[digest(spec)] += quantity
                return counts
            catalog = {i["id"]: i for i in snapshot["items"]}
            expected_items = [{**catalog[i["id"]]["mcp_item"], "quantity": i["quantity"]} for i in choice["items"]]
            if physical_items(expected_items) != physical_items(calculated.get("items", [])) or any(calculated.get(k) != snapshot.get("parameters", {}).get(k) for k in fields if k != "items"):
                raise ValueError("计价餐品/场景与营养候选快照不同")
        validate_coupon_items(payload.get("items", []))
        # The local engine never handles credentials or initiates a network mutation.
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        if any(word in serialized for word in ("authorization", "bearer ", "token", "payh5url")):
            raise ValueError("订单参数不能包含凭证或付款链接")
        now = datetime.now(CN_TIME)
        expiry = min(now + timedelta(minutes=10), datetime.fromisoformat(choice["quoted_at"]) + timedelta(minutes=15))
        manifest = {"day": day, "meal": meal, "context_id": result["context_id"], "target_hash": digest(self.target(day, meal)), "review_status": review["status"], "review_hash": digest(review), "key": key, "store_code": snapshot["store_code"], "context": snapshot["context"], "items": choice["items"], "nutrition": choice["nutrition"], "total_cents": choice["total_cents"], "order_payload": payload, "payload_hash": digest(payload), "expires_at": expiry.isoformat(), "state": "preview"}
        identifier = digest({"manifest": manifest, "created_at": now.isoformat()})[:24]
        manifest["confirmation_id"] = identifier
        with self.db:
            self.db.execute("INSERT INTO previews VALUES (?,?,?)", (identifier, manifest["expires_at"], json.dumps(manifest, ensure_ascii=False)))
            self.log("preview", {"id": identifier})
        return manifest

    def authorize(self, identifier, fresh_quotes):
        # Single use: the connector may create one order after explicit user confirmation.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT payload FROM previews WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ValueError("预览不存在")
            manifest = json.loads(row[0])
            if manifest["state"] != "preview":
                raise ValueError("确认标识已经使用；先查询订单，禁止重复下单")
            now = datetime.now(CN_TIME)
            if now >= datetime.fromisoformat(manifest["expires_at"]):
                raise ValueError("预览已过期，请重新报价和确认")
            if digest(self.target(manifest["day"], manifest["meal"])) != manifest["target_hash"]:
                raise ValueError("计划或摄入记录变化，请重新推荐并确认")
            if fresh_quotes.get("kind") != "official" or any(fresh_quotes.get(k) != manifest[k] for k in ("context_id", "store_code", "context")):
                raise ValueError("重新报价上下文不同")
            matches = [q for q in fresh_quotes.get("quotes", []) if q.get("key") == manifest["key"]]
            if len(matches) != 1:
                raise ValueError("缺少唯一的新报价")
            quote = matches[0]
            timestamp = datetime.fromisoformat(quote["quoted_at"])
            if timestamp.tzinfo is None or not 0 <= (now - timestamp).total_seconds() <= 120:
                raise ValueError("确认前报价必须在两分钟内")
            if cents(quote["total_cents"]) != manifest["total_cents"] or digest(quote.get("order_payload")) != manifest["payload_hash"]:
                raise ValueError("价格或订单参数发生变化，请重新预览并确认")
            if now.date().isoformat() != manifest["day"]:
                raise ValueError("已跨日，请重新推荐")
            manifest["state"] = "consumed"
            self.db.execute("UPDATE previews SET payload=? WHERE id=?", (json.dumps(manifest, ensure_ascii=False), identifier))
            self.log("authorize", {"id": identifier})
        return {"confirmation_id": identifier, "create_once_payload": manifest["order_payload"], "total_cents": manifest["total_cents"], "note": "本地没有调用下单接口。Agent 须已取得用户明确确认，并仅调用一次 create-order；超时后查询订单，不自动重试。"}


def nutrition(item):
    data = item.get("nutrition", {})
    values = {}
    for n in NUTRIENTS:
        value = data.get(n)
        if value is None and n == "kcal" and data.get("energy_kj") is not None:
            value = number(data["energy_kj"], "energy_kj") / 4.184
        values[n] = None if value is None else number(value, n)
    return values


def candidate_key(items):
    return digest(sorted((str(i["id"]), i["quantity"]) for i in items))[:20]


def physical_contents(items, catalog):
    result = Counter()
    for selected in items:
        item = catalog[selected["id"]]
        for identifier in item.get("components", [item["id"]]):
            result[identifier] += selected["quantity"]
    return result


def content_units(contents, identifier, catalog):
    needs = Counter(catalog[identifier].get("components", [identifier]))
    return min(contents[i] // count for i, count in needs.items())


def aggregate(items, catalog):
    totals = {n: 0.0 for n in NUTRIENTS}
    intervals = {n: {"min": 0.0, "max": 0.0} for n in NUTRIENTS}
    unknown = set()
    details = []
    sources = []
    estimated = False
    for selection in items:
        item = catalog[selection["id"]]
        quantity = number(selection["quantity"], "购买数量", 1)
        if int(quantity) != quantity:
            raise ValueError("点餐数量必须是整数")
        parts = [catalog[identifier] for identifier in item["components"]] if item.get("components") else [item]
        for part in parts:
            values, part_bounds = nutrition(part), ns.bounds(part)
            estimated |= part.get("nutrition_source") in ns.ESTIMATED_SOURCES or bool(part.get("nutrition_bounds")) or any(s["source"] in ns.ESTIMATED_SOURCES for s in part.get("nutrition_sources", []))
            sources.append({"id": part["id"], "name": part["name"], "quantity": quantity, "source": part.get("nutrition_source", "unknown"), "evidence": part.get("evidence", {}), "supplements": part.get("nutrition_sources", [])})
            for n, value in values.items():
                b = part_bounds[n]
                intervals[n]["min"] += b["min"] * quantity
                intervals[n]["max"] = None if intervals[n]["max"] is None or b["max"] is None else intervals[n]["max"] + b["max"] * quantity
                if value is None or n in part.get("nutrition_bounds", {}):
                    unknown.add(n)
                else:
                    totals[n] += value * quantity
        details.append({"id": item["id"], "name": item["name"], "quantity": quantity, "nutrition_source": item.get("nutrition_source", "unknown")})
    return {"key": candidate_key(items), "items": details, "ordering_mode": "bundle" if any(catalog[i["id"]]["category"] == "bundle" for i in items) else "a_la_carte", "nutrition": {n: None if n in unknown else round(totals[n], 2) for n in NUTRIENTS}, "known_subtotal": {n: round(totals[n], 2) for n in NUTRIENTS}, "unknown_nutrients": sorted(unknown), "nutrition_bounds": intervals, "estimated": estimated, "nutrition_sources": sources}


def violations(candidate, target):
    reasons = []
    if "nutrition_bounds" in candidate:
        assessment = ns.target_assessment(candidate, target)
        for nutrient, outcome in assessment["nutrients"].items():
            if outcome["status"] in ("above", "below", "uncertain"):
                reasons.append(f"{nutrient} {outcome['status']}，无法满足/核实目标范围")
        return reasons
    for key, limit in target.items():
        nutrient, side = key.rsplit("_", 1)
        value = candidate["nutrition"].get(nutrient)
        if value is None:
            reasons.append(f"{nutrient} 未知，无法确认 {side}={limit}")
        elif (side == "min" and value < limit) or (side == "max" and value > limit):
            reasons.append(f"{nutrient}={value} 不满足 {side}={limit}")
    return reasons


def validate_snapshot(snapshot, day):
    if snapshot.get("kind") not in ("demo", "official"):
        raise ValueError("snapshot kind 必须为 demo 或 official")
    if snapshot.get("day") != day or not snapshot.get("store_code") or not snapshot.get("context"):
        raise ValueError("菜单快照需要匹配当天日期、store_code 和 context")
    items = snapshot.get("items", [])
    if not items or len(items) > 250:
        raise ValueError("菜单需包含 1–250 个明确规格的可售选项")
    ids = []
    for item in items:
        if not item.get("id") or not item.get("name") or item.get("category") not in ("main", "side", "drink", "bundle"):
            raise ValueError("菜单项需要 id / name / category")
        ids.append(item["id"])
        nutrition(item)
        ns.bounds(item)
        if snapshot["kind"] == "official" and item["category"] != "bundle" and (any(v is not None for v in nutrition(item).values()) or item.get("nutrition_bounds")):
            evidence = item.get("evidence", {})
            source = item.get("nutrition_source")
            if source == "official":
                if not evidence.get("nutrition_name") or not evidence.get("serving"):
                    raise ValueError("官方营养匹配需记录来源餐品名和份量证据")
            elif source in {"official_web", "database", "estimated", "mixed"}:
                supplements = item.get("nutrition_sources", [])
                if not supplements:
                    raise ValueError("网页补充营养必须保留来源记录")
                covered = set()
                for supplement in supplements:
                    ns.validate_web_evidence(supplement["evidence"], supplement["source"])
                    if supplement["evidence"].get("matched_item_name") != item["name"] or supplement["evidence"].get("selected_serving") != (item.get("serving") or evidence.get("serving")):
                        raise ValueError("网页补充营养的匹配证据与菜单不符")
                    covered.update(supplement["nutrients"])
                    if supplement["source"] in ns.ESTIMATED_SOURCES and (not supplement["evidence"].get("uncertainty_reason") or set(supplement["nutrients"]) - set(item.get("nutrition_bounds", {}))):
                        raise ValueError("第三方或估算营养需保留区间与不确定性证据")
                uncovered = {n for n, v in nutrition(item).items() if v is not None and n not in covered}
                if source == "mixed" and uncovered and (not evidence.get("nutrition_name") or not evidence.get("serving")):
                    raise ValueError("混合来源需要保留原官方匹配证据")
                if source != "mixed" and uncovered:
                    raise ValueError("网页营养字段没有来源证据")
                if set(item.get("nutrition_bounds", {})) - covered:
                    raise ValueError("营养估算区间没有来源证据")
            else:
                raise ValueError("官方营养匹配需记录来源餐品名和份量证据")
        if item["category"] == "bundle" and not item.get("components"):
            raise ValueError("套餐必须明确 components；更换配餐后应生成新的套餐选项")
    if len(set(ids)) != len(ids):
        raise ValueError("菜单 id 重复")
    catalog = {i["id"]: i for i in items}
    for item in items:
        if item.get("components"):
            if not isinstance(item["components"], list) or any(not isinstance(c, str) or c not in catalog or catalog[c]["category"] == "bundle" or catalog[c].get("components") for c in item["components"]):
                raise ValueError("套餐组件必须引用已明确规格的单品 ID，不能嵌套套餐")
    return catalog


def expand_exclusions(catalog, selectors):
    """A root code excludes its family; a full variant ID names one recipe.

    Match the '~' boundary and explicit MCP/detail codes, never a raw prefix.
    This also supports aliased roots and non-purchasable component leaf IDs.
    """
    exact = set(selectors)
    roots = {s for s in exact if "~" not in s}
    return {identifier for identifier, item in catalog.items()
            if identifier in exact or identifier.split("~", 1)[0] in roots
            or str(item.get("mcp_item", {}).get("productCode", "")) in roots
            or (not item.get("mcp_item") and str(item.get("evidence", {}).get("detail_code", "")) in roots)}


def contains_excluded(selections, catalog, excluded, contents=None):
    if any(i["id"] in excluded for i in selections):
        return True
    contents = physical_contents(selections, catalog) if contents is None else contents
    # A bundle exclusion names a purchasable product family, not every basket
    # with coincidentally identical contents. Single-item exclusions propagate.
    return any(catalog[i]["category"] != "bundle" and content_units(contents, i, catalog) >= 1 for i in excluded)


def expand_candidates(snapshot, catalog, required, excluded, max_candidates=20000):
    excluded = expand_exclusions(catalog, excluded)
    if set(required) - set(catalog):
        raise ValueError("必选项不在当店菜单中")
    if set(required) & set(excluded):
        raise ValueError("同一餐品不能同时必选和排除")
    required_quantities = snapshot.get("required_quantities", {})
    if not isinstance(required_quantities, dict) or set(required_quantities) - set(catalog) or set(required_quantities) & set(excluded):
        raise ValueError("required_quantities 需要菜单中的非排除餐品 ID")
    for quantity in required_quantities.values():
        number(quantity, "必须保留的份数", 1)
        if int(quantity) != quantity:
            raise ValueError("必须份数需为整数")

    def allowed(selections):
        contents = physical_contents(selections, catalog)
        return all(content_units(contents, i, catalog) >= 1 for i in required) and not contains_excluded(selections, catalog, excluded, contents) and all(content_units(contents, i, catalog) >= q for i, q in required_quantities.items())
    candidates = []
    for item in catalog.values():
        if item["id"] in excluded or item.get("available", True) is False:
            continue
        if item["category"] in ("main", "bundle"):
            candidates.append([item])
    groups = [[i for i in catalog.values() if i["category"] == category and i["id"] not in excluded and i.get("available", True)] for category in ("main", "side", "drink")]
    main, sides, drinks = groups
    count = len(main) * (len(sides) + 1) * (len(drinks) + 1) + len(candidates)
    if count > max_candidates:
        raise ValueError("候选组合过多，请先按口味或类别缩小菜单")
    candidates.extend([m] + ([s] if s else []) + ([d] if d else []) for m, s, d in itertools.product(main, [None] + sides, [None] + drinks))
    requested = snapshot.get("requested_baskets", [])
    if not isinstance(requested, list) or len(requested) > 50:
        raise ValueError("requested_baskets 应为不超过50个用户指定组合")
    unique = {}
    for items in candidates:
        selections = [{"id": i["id"], "quantity": 1} for i in items]
        if not allowed(selections):
            continue
        result = aggregate(selections, catalog)
        unique[result["key"]] = result
    for basket in requested:
        selections = basket.get("items")
        if not isinstance(selections, list) or not selections:
            raise ValueError("用户指定组合需要非空 items")
        if any(i["id"] not in catalog or catalog[i["id"]].get("available", True) is False for i in selections):
            raise ValueError("用户指定组合包含不存在或不可直接购买的选项")
        if not allowed(selections):
            continue
        result = aggregate(selections, catalog)
        unique[result["key"]] = result
    return list(unique.values())


def fit_score(candidate, target):
    score = 0
    for n in NUTRIENTS:
        low, high = target.get(n + "_min"), target.get(n + "_max")
        if low is not None and high is not None and candidate["nutrition"][n] is not None:
            middle = (low + high) / 2
            score += abs(candidate["nutrition"][n] - middle) / max(middle, 1)
    return score


def bundle_codes(candidate, catalog):
    return {str(catalog[i["id"]].get("mcp_item", {}).get("productCode", i["id"].split("~", 1)[0])) for i in candidate["items"] if catalog[i["id"]]["category"] == "bundle"}


def bundle_readiness(snapshot, catalog, eligible, checked, excluded=None):
    plan = snapshot.get("bundle_comparison")
    if plan is None:
        return {"status": "incomplete" if snapshot["kind"] == "official" else "legacy_snapshot", "missing_discovery": True, "scope": "旧快照未携带菜单套餐发现记录；新菜单必须通过builder或adapter转换"}
    wanted = set(plan["required_codes"])
    feasible = set().union(*(bundle_codes(c, catalog) for c in eligible)) & wanted
    covered = set().union(*(bundle_codes(c, catalog) for c in checked)) & wanted
    missing = sorted(feasible - covered)
    details = [code for code in plan.get("missing_detail_codes", []) if code not in set(excluded or [])]
    return {"status": "incomplete" if details or missing else "complete", "policy": plan["policy"], "missing_detail_codes": details, "unquoted_feasible_bundle_codes": missing, "feasible_bundle_codes": sorted(feasible), "quoted_bundle_codes": sorted(covered), "unselected_menu_bundle_count": plan.get("unselected_count", 0), "scope": plan["scope"]}


def quote_shortlist(eligible, snapshot, catalog, prefer_bundles):
    result, seen, family_seen = [], set(), set()
    explicit = {candidate_key(b["items"]) for b in snapshot.get("requested_baskets", [])}
    def add(candidate):
        if candidate["key"] not in seen and len(result) < 12:
            result.append(candidate); seen.add(candidate["key"])
    for candidate in eligible:
        if candidate["key"] in explicit: add(candidate)
    if prefer_bundles:
        for candidate in eligible:
            codes = bundle_codes(candidate, catalog)
            if codes - family_seen:
                add(candidate); family_seen.update(codes)
        bundles = [c for c in eligible if c["ordering_mode"] == "bundle"]
        singles = [c for c in eligible if c["ordering_mode"] != "bundle"]
        for a, b in itertools.zip_longest(bundles, singles):
            if a is not None: add(a)
            if b is not None: add(b)
    else:
        for candidate in eligible: add(candidate)
    return result


def recommend(store, day, meal, snapshot, quotes=None, required=None, excluded=None):
    context = store.target(day, meal)
    catalog = validate_snapshot(snapshot, day)
    defaults = context["plan"]["defaults"]
    if snapshot.get("meal") != meal:
        raise ValueError("菜单快照餐段不同")
    if any(defaults.get(k) and defaults[k] != snapshot[k] for k in ("store_code", "context")):
        raise ValueError("菜单与已保存门店/方式不同；更换门店请先更新设置")
    required = required if required is not None else defaults.get("required_ids", [])
    excluded = excluded if excluded is not None else defaults.get("excluded_ids", [])
    all_candidates = expand_candidates(snapshot, catalog, required, excluded)
    eligible, rejected = [], []
    for candidate in all_candidates:
        candidate["violations"] = violations(candidate, context["target"])
        (rejected if candidate["violations"] else eligible).append(candidate)
    eligible.sort(key=lambda c: (fit_score(c, context["target"]), c["key"]))
    prefer_bundles = snapshot.get("bundle_comparison", {}).get("policy", "bundle_first") == "bundle_first"
    context_id = digest({"snapshot": snapshot, "day": day, "meal": meal, "target": context["target"], "budget": context["budget_cents"], "required": required, "excluded": excluded})
    common = {"day": day, "meal": meal, "kind": snapshot["kind"], "template": context["plan"]["active_template"], "target": context["target"], "budget_cents": context["budget_cents"], "context_id": context_id, "warnings": context["warnings"], "eligible_count": len(eligible), "rejected_count": len(rejected)}
    common["warnings"] += [f"{w['name']} 营养匹配未完成：{w['reason']}" for w in snapshot.get("nutrition_warnings", [])]
    if quotes is None:
        return {**common, "phase": "needs_quotes", "quote_requests": quote_shortlist(eligible, snapshot, catalog, prefer_bundles), "bundle_readiness": bundle_readiness(snapshot, catalog, eligible, [], excluded), "scope": "最多12个营养候选待计价，优先保留套餐及用户原单；最省仅在已报价候选中比较", "closest_rejected": sorted(rejected, key=lambda c: len(c["violations"]))[:3] if not eligible else []}
    if quotes.get("context_id") != context_id or quotes.get("kind") != snapshot["kind"]:
        raise ValueError("报价与当前菜单/目标上下文不一致，请重新报价")
    if quotes.get("store_code") != snapshot["store_code"] or quotes.get("context") != snapshot["context"]:
        raise ValueError("报价门店或取餐方式不一致")
    quote_map = {}
    common["quote_failure_count"] = len(quotes.get("quote_failures", []))
    if common["quote_failure_count"]:
        common["warnings"].append("部分计价失败或缺失；保留成功方案，但优惠比较不完整")
    if quotes.get("coupon_comparison", {}).get("truncated"):
        common["warnings"].append("优惠计价达到调用上限，最省结论仅限实际报价的变体")
    if quotes.get("coupon_comparison", {}).get("unresolved"):
        common["warnings"].append("存在规格/有效期未核实的券；尚不能判断这些券是否适用于本餐")
    for quote in quotes.get("quotes", []):
        if quote["key"] in quote_map:
            raise ValueError("同一候选出现重复报价；先选择该候选实际可用的最低报价")
        payable_cents(quote["total_cents"])
        timestamp = datetime.fromisoformat(quote["quoted_at"])
        if timestamp.tzinfo is None:
            raise ValueError("报价时间必须包含时区")
        if snapshot["kind"] == "official":
            age = (datetime.now(CN_TIME) - timestamp).total_seconds()
            if not 0 <= age <= 900 or timestamp.astimezone(CN_TIME).date().isoformat() != day:
                raise ValueError("官方报价超过 15 分钟或不是当天，请重新计价")
        quote_map[quote["key"]] = quote
    priced = []
    over_budget = []
    for candidate in eligible:
        quote = quote_map.get(candidate["key"])
        if not quote:
            continue
        candidate = {**candidate, "total_cents": quote["total_cents"], "quoted_at": quote["quoted_at"], "quote": quote}
        (over_budget if quote["total_cents"] > context["budget_cents"] else priced).append(candidate)
    best = sorted(priced, key=lambda c: (fit_score(c, context["target"]), c["total_cents"]))
    cheapest = sorted(priced, key=lambda c: (c["total_cents"], fit_score(c, context["target"])))
    choices = []
    preferred = sorted(priced, key=lambda c: (prefer_bundles and c["ordering_mode"] != "bundle", c["total_cents"], fit_score(c, context["target"])))
    for label, ranked in (("优先套餐" if preferred and preferred[0]["ordering_mode"] == "bundle" and prefer_bundles else "优先方案", preferred), ("最贴合目标", best), ("已报价候选中最省钱", cheapest)):
        if ranked:
            candidate = ranked[0]
            previous = next((c for c in choices if c["key"] == candidate["key"]), None)
            if previous:
                previous["labels"].append(label)
            else:
                choices.append({**candidate, "labels": [label]})
    for candidate in best:
        if len(choices) >= 3:
            break
        if candidate["key"] not in {c["key"] for c in choices}:
            choices.append({**candidate, "labels": ["备选"]})
    if len(priced) == 1:
        common["warnings"] = common["warnings"] + ["只有一个已报价合格候选，不能据此宣称已完成套餐或优惠比较"]
    closest = next((c for c in choices if best and c["key"] == best[0]["key"]), None)
    cheapest_choice = next((c for c in choices if cheapest and c["key"] == cheapest[0]["key"]), None)
    status = "no_feasible_priced_option" if not choices else "single_option_only" if len(priced) == 1 else "alternatives_ready"
    fallback = None
    if prefer_bundles and (not preferred or preferred[0]["ordering_mode"] != "bundle"):
        fallback = "没有符合营养目标及必选/排除条件的套餐" if not any(c["ordering_mode"] == "bundle" for c in eligible) else "套餐尚未核价或已核价套餐超出预算；仅展示单点参考，先完成比较"

    readiness = bundle_readiness(snapshot, catalog, eligible, priced + over_budget, excluded)
    if readiness["status"] == "incomplete":
        common["warnings"].append("相关套餐详情/计价未完成；不能只按单点下单，也不能宣称没有更省套餐")
    return {**common, "phase": "recommended", "recommendation_status": status, "bundle_readiness": readiness, "bundle_preference": "bundle_first" if prefer_bundles else "single_items_only", "bundle_fallback_reason": fallback, "preferred": next((c for c in choices if preferred and c["key"] == preferred[0]["key"]), None), "choices": choices, "closest": closest, "cheapest": cheapest_choice, "priced_count": len(priced) + len(over_budget), "unquoted_count": len(eligible) - len(priced) - len(over_budget), "over_budget_count": len(over_budget), "scope": "价格比较仅覆盖已计价候选，不能宣称全菜单最低价", "message": "没有已报价且同时满足营养与预算的方案；没有放宽限制" if not choices else "按用户目标筛选，热量较低不自动代表更优"}


def review_basket(snapshot, baseline, alternatives, checks, target=None, must_keep=None, budget_cents=None, exact_contents=False, excluded_ids=None):
    """Distinguish cheaper equivalent baskets, substitutions, none, and unchecked work."""
    catalog = validate_snapshot(snapshot, snapshot["day"])
    target, must_keep = target or {}, must_keep or []
    validate_target(target)
    if budget_cents is not None:
        cents(budget_cents)
    excluded_ids = excluded_ids or []
    excluded = expand_exclusions(catalog, excluded_ids)

    def contents(items):
        return physical_contents(items, catalog)

    def priced(selection):
        candidate = aggregate(selection["items"], catalog)
        quote = selection.get("quote")
        if not quote:
            return None
        if quote.get("key") != candidate["key"] or quote.get("kind") != snapshot["kind"] or any(quote.get(k) != snapshot[k] for k in ("store_code", "context")):
            raise ValueError("比较报价的餐品、门店或取餐方式不同")
        timestamp = datetime.fromisoformat(quote["quoted_at"])
        if timestamp.tzinfo is None:
            raise ValueError("比较报价时间需要时区")
        if snapshot["kind"] == "official":
            age = (datetime.now(CN_TIME) - timestamp).total_seconds()
            if not 0 <= age <= 900 or timestamp.astimezone(CN_TIME).date().isoformat() != snapshot["day"]:
                raise ValueError("比较报价已过期，请重新计价")
        candidate.update({"label": selection.get("label", "方案"), "total_cents": payable_cents(quote["total_cents"]), "quoted_at": quote["quoted_at"], "violations": violations(candidate, target)})
        if budget_cents is not None and candidate["total_cents"] > budget_cents:
            candidate["violations"].append("超过用户本餐预算")
        if any(catalog[i["id"]].get("available", True) is False for i in selection["items"]):
            candidate["violations"].append("所选套餐/单品不可直接购买")
        if contains_excluded(selection["items"], catalog, excluded):
            candidate["violations"].append("含用户排除的餐品")
        return candidate

    original = priced(baseline)
    if original is None:
        raise ValueError("原单需要实时报价才能比较")
    original_contents = contents(baseline["items"])
    if any(identifier not in catalog or content_units(original_contents, identifier, catalog) < 1 for identifier in must_keep):
        raise ValueError("must_keep 必须来自原单餐品")
    reviewed_prices = {} if original["violations"] else {original["key"]: [original["total_cents"]]}
    equivalent, replacements, rejected, nutrition_recommendations = [], [], [], []
    missing_quotes = 0
    for selection in alternatives:
        candidate = priced(selection)
        if candidate is None:
            missing_quotes += 1
            continue
        current = contents(selection["items"])
        if selection.get("requires_membership_purchase"):
            candidate["violations"].append("需要额外购买会员卡，未获用户授权")
        if any(content_units(current, i, catalog) < content_units(original_contents, i, catalog) for i in must_keep):
            candidate["violations"].append("未保留必须餐品及数量")
        if exact_contents and current != original_contents:
            candidate["violations"].append("用户要求原样餐品和数量，不接受增减替换")
        if not candidate["violations"]:
            reviewed_prices.setdefault(candidate["key"], []).append(candidate["total_cents"])
        candidate["price_delta_cents"] = candidate["total_cents"] - original["total_cents"]
        removed, added = original_contents - current, current - original_contents
        candidate["removed"] = [{"id": i, "name": catalog[i]["name"], "quantity": q} for i, q in removed.items()]
        candidate["added"] = [{"id": i, "name": catalog[i]["name"], "quantity": q} for i, q in added.items()]
        candidate["nutrition_delta"] = {n: None if candidate["nutrition"][n] is None or original["nutrition"][n] is None else round(candidate["nutrition"][n] - original["nutrition"][n], 2) for n in NUTRIENTS}
        candidate["decision"] = {"actual_savings_cents": original["total_cents"] - candidate["total_cents"], "additional_items": bool(added), "filler_cost_trap": bool(added and candidate["total_cents"] > original["total_cents"]), "goal_violations": list(candidate["violations"]), "note": "按原本这餐比较真实应付差额，不把券面值或即将过期当作必须消费的理由"}
        targeted_nutrients = {key.rsplit("_", 1)[0] for key in target}
        scored = all(original["nutrition"][n] is not None and candidate["nutrition"][n] is not None for n in targeted_nutrients)
        baseline_assessment = ns.target_assessment(original, target)
        improved = bool(target) and not candidate["violations"] and (baseline_assessment["status"] == "outside" or (scored and fit_score(candidate, target) < fit_score(original, target)))
        known_fit_alternative = bool(target) and not candidate["violations"] and baseline_assessment["status"] == "uncertain"
        if improved or known_fit_alternative:
            candidate["decision"]["nutrition_goal_improved"] = improved
            candidate["decision"]["baseline_nutrition_uncertain"] = baseline_assessment["status"] == "uncertain"
            nutrition_recommendations.append(candidate)
        if candidate["violations"] or candidate["total_cents"] >= original["total_cents"]:
            rejected.append(candidate)
        elif current == original_contents:
            equivalent.append(candidate)
        else:
            replacements.append(candidate)
    equivalent.sort(key=lambda c: c["total_cents"])
    replacements.sort(key=lambda c: c["total_cents"])
    nutrition_recommendations.sort(key=lambda c: (c["total_cents"], fit_score(c, target)))
    complete = checks.get("bundles_checked") is True and checks.get("coupons_checked") is True and missing_quotes == 0 and checks.get("quote_failure_count", 0) == 0 and checks.get("coupon_unresolved_count", 0) == 0 and checks.get("coupon_pages_complete", True) is True
    eligible_bundles = []
    if snapshot.get("bundle_comparison"):
        for c in expand_candidates(snapshot, catalog, must_keep, excluded_ids):
            if not violations(c, target) and (not exact_contents or contents(c["items"]) == original_contents):
                eligible_bundles.append(c)
    readiness = bundle_readiness(snapshot, catalog, eligible_bundles, [original] + equivalent + replacements + rejected, excluded_ids)
    complete = complete and readiness["status"] != "incomplete"
    if not complete:
        status, message = "incomplete", "套餐/优惠比较尚未完成，不能说没有推荐，也不能进入下单预览"
    elif equivalent:
        status, message = "better_equivalent_found", "找到保留原餐品和份量的更省方案，请先展示再确认"
    elif replacements:
        status, message = "changes_required", "已检查范围内没有保留原样更省的方案；有需替换或增减餐品的建议，必须说明变化并取得确认"
    else:
        status, message = "no_better_found", "在已检查的套餐和现有优惠中，没有找到满足要求的更省推荐；保留原单，不宣称全菜单最低价"
        if nutrition_recommendations:
            message = "已查范围没有更省方案，但有更贴合当前营养目标的方案；请展示价差、餐品变化并取得确认" if any(c["decision"]["nutrition_goal_improved"] for c in nutrition_recommendations) else "已查范围没有更省方案；原单营养尚无法核实，另有符合已录目标的参考方案，不能声称比原单热量或脂肪更低"
    nutrition_recommendation_status = "goal_improvement_available" if any(c["decision"]["nutrition_goal_improved"] for c in nutrition_recommendations) else "target_fit_alternative_available" if nutrition_recommendations else "no_verified_goal_improvement"
    return {"status": status, "message": message, "can_finalize": complete, "bundle_readiness": readiness, "snapshot_hash": digest(snapshot), "target_hash": digest(target), "budget_cents": budget_cents, "exact_contents": exact_contents, "excluded_ids": excluded_ids, "baseline": original, "equivalent_recommendations": equivalent, "replacement_recommendations": replacements, "nutrition_recommendations": nutrition_recommendations, "nutrition_recommendation_status": nutrition_recommendation_status, "rejected": rejected, "missing_quote_count": missing_quotes, "checked_alternative_count": len(alternatives) - missing_quotes, "checks": checks, "reviewed_prices": reviewed_prices, "nutrition_status": "target_not_configured" if not target else "unknown" if original["unknown_nutrients"] else "known", "scope": "仅覆盖列出的套餐和已核实优惠；其他组合未穷举；rejected包含价格未更省的方案，符合营养目标的改进会单列nutrition_recommendations"}


def compare(snapshot, before, after):
    catalog = validate_snapshot(snapshot, snapshot["day"])
    a, b = aggregate(before["items"], catalog), aggregate(after["items"], catalog)
    delta = {n: None if a["nutrition"][n] is None or b["nutrition"][n] is None else round(b["nutrition"][n] - a["nutrition"][n], 2) for n in NUTRIENTS}
    price_delta = None
    if before.get("total_cents") is not None and after.get("total_cents") is not None:
        price_delta = cents(after["total_cents"]) - cents(before["total_cents"])
    return {"before": a, "after": b, "nutrition_delta_after_minus_before": delta, "price_delta_cents": price_delta}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="麦当劳饮食计划与消费账本（本地计算）")
    parser.add_argument("--db", default=os.environ.get("MCD_FIT_DB", str(Path(__file__).resolve().parents[1] / "data" / "planner.sqlite3")))
    parser.add_argument("--output", dest="json_output", help="可选 UTF-8 JSON 输出文件，避免 shell 重定向改变编码（export 除外）")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("configure"); p.add_argument("--file", required=True)
    for command in ("plan", "intake-day"):
        p = sub.add_parser(command); p.add_argument("--date", default=today())
    p = sub.add_parser("schedule"); p.add_argument("--date", default=today()); p.add_argument("--days", type=int, default=7)
    p = sub.add_parser("override"); p.add_argument("--date", default=today()); p.add_argument("--mode", choices=["rest", "skip", "normal"], required=True)
    p = sub.add_parser("coverage"); p.add_argument("--date", default=today()); p.add_argument("--complete", choices=["yes", "no"], required=True)
    p = sub.add_parser("close-day"); p.add_argument("--date", default=today()); p.add_argument("--complete", choices=["yes", "no"], required=True)
    for command in ("record-intake", "record-bill", "import-bills"):
        p = sub.add_parser(command); p.add_argument("--file", required=True)
    p = sub.add_parser("recommend"); p.add_argument("--date", default=today()); p.add_argument("--meal", choices=["breakfast", "lunch", "dinner"], required=True); p.add_argument("--snapshot", required=True); p.add_argument("--quotes"); p.add_argument("--require", nargs="*"); p.add_argument("--exclude", nargs="*")
    p = sub.add_parser("compare"); p.add_argument("--snapshot", required=True); p.add_argument("--before", required=True); p.add_argument("--after", required=True)
    p = sub.add_parser("review"); p.add_argument("--snapshot", required=True); p.add_argument("--baseline", required=True); p.add_argument("--alternatives", required=True); p.add_argument("--checks", required=True); p.add_argument("--target"); p.add_argument("--must-keep", nargs="*", default=[])
    p.add_argument("--budget-cents", type=int); p.add_argument("--exact-contents", action="store_true"); p.add_argument("--exclude", nargs="*", default=[])
    p = sub.add_parser("report"); p.add_argument("--period", default=today()[:7])
    p = sub.add_parser("export"); p.add_argument("--csv", "--output", dest="csv_path", required=True, help="CSV 路径；--output 保留为旧用法兼容别名")
    p = sub.add_parser("preview"); p.add_argument("--date", default=today()); p.add_argument("--meal", choices=["breakfast", "lunch", "dinner"], required=True); p.add_argument("--snapshot", required=True); p.add_argument("--quotes", required=True); p.add_argument("--key", required=True); p.add_argument("--review", required=True)
    p = sub.add_parser("authorize"); p.add_argument("--confirmation-id", required=True); p.add_argument("--quotes", required=True)
    args = parser.parse_args()
    if args.command == "export" and args.json_output:
        parser.error("export 使用 --csv 指定 CSV 文件，不能同时使用全局 JSON --output")
    store = Store(args.db)
    try:
        if args.command == "configure": result = store.configure(read_json(args.file))
        elif args.command == "plan": result = store.plan(args.date)
        elif args.command == "schedule": result = store.schedule(args.date, args.days)
        elif args.command == "override": result = store.override(args.date, args.mode)
        elif args.command == "coverage": result = store.mark_complete(args.date, args.complete == "yes")
        elif args.command == "close-day": result = store.close_day(args.date, args.complete == "yes")
        elif args.command == "intake-day": result = store.day_intake(args.date)
        elif args.command == "record-intake": result = store.record_intake(read_json(args.file))
        elif args.command == "record-bill": result = store.bill(read_json(args.file))
        elif args.command == "import-bills": result = store.import_bills(read_json(args.file))
        elif args.command == "recommend": result = recommend(store, args.date, args.meal, read_json(args.snapshot), read_json(args.quotes) if args.quotes else None, args.require, args.exclude)
        elif args.command == "compare": result = compare(read_json(args.snapshot), read_json(args.before), read_json(args.after))
        elif args.command == "review": result = review_basket(read_json(args.snapshot), read_json(args.baseline), read_json(args.alternatives), read_json(args.checks), read_json(args.target) if args.target else None, args.must_keep, args.budget_cents, args.exact_contents, args.exclude)
        elif args.command == "report": result = store.report(args.period)
        elif args.command == "export": result = store.export(args.csv_path)
        elif args.command == "preview": result = store.preview(args.date, args.meal, read_json(args.snapshot), read_json(args.quotes), args.key, read_json(args.review))
        elif args.command == "authorize": result = store.authorize(args.confirmation_id, read_json(args.quotes))
        serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.command != "export" and args.json_output:
            Path(args.json_output).write_text(serialized, encoding="utf-8")
            print(json.dumps({"output": str(Path(args.json_output).resolve())}, ensure_ascii=False))
        else:
            print(serialized)
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        store.db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
