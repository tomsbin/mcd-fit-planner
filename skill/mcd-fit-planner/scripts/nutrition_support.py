"""Provenance and interval arithmetic for nutrition retrieved by the host Agent."""
from __future__ import annotations

import copy
import math
from datetime import date
from urllib.parse import urlsplit

NUTRIENTS = ("kcal", "protein_g", "carb_g", "fat_g")
ESTIMATED_SOURCES = {"database", "estimated", "manual"}


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("营养数值必须为有限非负数字")
    return value


def values(item):
    data = item.get("nutrition") or {}
    result = {n: None if data.get(n) is None else numeric(data[n]) for n in NUTRIENTS}
    if result["kcal"] is None and data.get("energy_kj") is not None:
        result["kcal"] = numeric(data["energy_kj"]) / 4.184
    return result


def bounds(item):
    points, ranges = values(item), item.get("nutrition_bounds") or {}
    if set(ranges) - set(NUTRIENTS):
        raise ValueError("未知营养区间字段")
    result = {}
    for n in NUTRIENTS:
        if n in ranges:
            low, high = ranges[n]["min"], ranges[n].get("max")
            numeric(low)
            if high is not None and numeric(high) < low:
                raise ValueError("营养区间下限不能大于上限")
            if points[n] is not None and (points[n] < low or (high is not None and points[n] > high)):
                raise ValueError("营养点值不在所给区间内")
            result[n] = {"min": low, "max": high}
        elif points[n] is not None:
            result[n] = {"min": points[n], "max": points[n]}
        else:
            result[n] = {"min": 0, "max": None}
    return result


def validate_web_evidence(evidence, source):
    if source not in {"official_web", "database", "estimated"}:
        raise ValueError("未知网页营养来源类型")
    required = ("url", "product_name", "serving", "region", "checked_at", "method")
    if any(not evidence.get(key) for key in required) or evidence["region"] != "CN":
        raise ValueError("网页营养需要 URL、产品名、中国地区、规格、核查日期与换算方法")
    url = urlsplit(evidence["url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("来源必须是无凭证的 HTTPS 网页")
    if source == "official_web" and not (url.hostname == "mcdonalds.com.cn" or url.hostname.endswith(".mcdonalds.com.cn") or url.hostname == "open.mcd.cn"):
        raise ValueError("official_web 只能使用麦当劳中国官方域名")
    date.fromisoformat(evidence["checked_at"])


def supplement(snapshot, records, as_of):
    """Only fill missing nutrients of an explicitly matched serving; never change MCP ordering data."""
    result = copy.deepcopy(snapshot)
    catalog = {item["id"]: item for item in result["items"]}
    seen = set()
    changes = []
    for record in records:
        identifier = record["item_id"]
        if identifier in seen or identifier not in catalog:
            raise ValueError("补充记录 ID 重复或不在菜单中")
        seen.add(identifier)
        item = catalog[identifier]
        selected_serving = item.get("serving") or item.get("evidence", {}).get("serving")
        if item["category"] == "bundle" or not selected_serving or record.get("match") != {"name": item["name"], "serving": selected_serving}:
            raise ValueError("补充营养必须明确匹配选定单品名称和规格；套餐按组件汇总")
        if item.get("customization") or item.get("mcp_item", {}).get("modification") or item.get("evidence", {}).get("match") == "customization_unquantified":
            raise ValueError("特制餐品不能套用标准配方数据")
        source = record["source"]
        if source not in {"official_web", "database", "estimated"}:
            raise ValueError("补充来源应为 official_web / database / estimated")
        evidence = copy.deepcopy(record["evidence"])
        validate_web_evidence(evidence, source)
        age = (date.fromisoformat(as_of) - date.fromisoformat(evidence["checked_at"])).days
        if not 0 <= age <= 30:
            raise ValueError("网页补充数据需在最近 30 天核查；过期请重新查阅")
        basis = record.get("basis", "per_serving")
        factor = 1
        if basis in {"per_100g", "per_100ml"}:
            field = "portion_g" if basis == "per_100g" else "portion_ml"
            factor = numeric(record.get(field)) / 100
            if factor <= 0 or not evidence.get("portion_evidence"):
                raise ValueError("每 100g/ml 换算需要正份量及份量证据")
        elif basis != "per_serving":
            raise ValueError("未知营养计量基准")
        incoming = values(record)
        intervals = bounds(record)
        if source != "official_web" and any(incoming[n] is not None or n in record.get("nutrition_bounds", {}) for n in NUTRIENTS):
            if not record.get("nutrition_bounds") or not evidence.get("uncertainty_reason"):
                raise ValueError("第三方或估算数据需要依据明确的区间和不确定性说明")
            for n in NUTRIENTS:
                if incoming[n] is not None and n not in record["nutrition_bounds"]:
                    raise ValueError("第三方点值必须附对应区间，不能伪装成官方值")
        existing = values(item)
        item.setdefault("nutrition", {})
        fields = []
        for n in NUTRIENTS:
            if existing[n] is not None or n in item.get("nutrition_bounds", {}):
                continue
            if source == "official_web" and incoming[n] is not None:
                item["nutrition"][n] = incoming[n] * factor
            elif n in record.get("nutrition_bounds", {}):
                b = intervals[n]
                item.setdefault("nutrition_bounds", {})[n] = {"min": b["min"] * factor, "max": None if b["max"] is None else b["max"] * factor}
            else:
                continue
            fields.append(n)
        if fields:
            evidence["matched_item_name"] = item["name"]
            evidence["selected_serving"] = selected_serving
            item.setdefault("nutrition_sources", []).append({"source": source, "nutrients": fields, "evidence": evidence})
            # Preserve partial MCP provenance in evidence; source refers to combined data.
            item["nutrition_source"] = source if not any(v is not None for v in existing.values()) else "mixed"
            item.setdefault("evidence", {})["web_supplements"] = item["nutrition_sources"]
            changes.append({"item_id": identifier, "filled_nutrients": fields, "source": source})
    if "nutrition_warnings" in result:
        result["nutrition_warnings"] = [warning for warning in result["nutrition_warnings"] if warning["id"] not in catalog or any(v is None for v in values(catalog[warning["id"]]).values())]
    return {"snapshot": result, "changes": changes}


def target_assessment(totals, target):
    """A range touching both sides of a limit cannot be called compliant."""
    outcomes = {}
    estimated = totals.get("estimated", False)
    for n in NUTRIENTS:
        low, high = target.get(n + "_min"), target.get(n + "_max")
        if low is None and high is None:
            continue
        b = totals["nutrition_bounds"][n]
        if high is not None and b["min"] > high:
            state = "above"
        elif low is not None and b["max"] is not None and b["max"] < low:
            state = "below"
        elif b["max"] is None or (low is not None and b["min"] < low) or (high is not None and b["max"] > high):
            state = "uncertain"
        else:
            state = "estimated_within" if estimated else "within"
        outcomes[n] = {"status": state, "actual": b, "target_min": low, "target_max": high}
    statuses = {v["status"] for v in outcomes.values()}
    overall = "not_configured" if not statuses else "outside" if statuses & {"above", "below"} else "uncertain" if "uncertain" in statuses else "estimated_within" if "estimated_within" in statuses else "within"
    return {"status": overall, "nutrients": outcomes}
