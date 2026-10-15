"""Offline adapters for documented McDonald's MCP responses; no network or tokens.

Accepts official success envelopes or their single JSON MCP text-content wrapper.
Field contracts: https://open.mcd.cn/mcp/doc (checked 2026-10-14).
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import re
import sys
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fit_core import CN_TIME, NUTRIENTS, aggregate, cents, digest, number, read_json, validate_snapshot, validate_coupon_items, payable_cents

FIELDS = {"kcal": "energyKcal", "protein_g": "protein", "carb_g": "carbohydrate", "fat_g": "fat"}
CONTEXT_FIELDS = ("storeCode", "beCode", "orderType", "beType", "reservationDate", "gmServiceCode", "withOrder", "needTableware")
ITEM_FIELDS = ("productCode", "quantity", "couponId", "couponCode", "modification", "roundList")


def envelope(raw):
    if not isinstance(raw, dict) or raw.get("isError"):
        raise ValueError("MCP 返回不是成功对象")
    if "result" in raw and "jsonrpc" in raw:
        return envelope(raw["result"])
    if "structuredContent" in raw:
        return envelope(raw["structuredContent"])
    if "content" in raw:
        blocks = [b for b in raw["content"] if b.get("type") == "text"]
        if len(blocks) != 1:
            raise ValueError("需要唯一 JSON 文本响应，不能猜测多段内容")
        return envelope(json.loads(blocks[0]["text"]))
    if raw.get("success") is not True or raw.get("code") != 200 or "data" not in raw:
        raise ValueError("官方响应未成功或 schema 改变，请检查连接器返回")
    return raw


def numeric(value, label):
    if value is None or value == "null" or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} 不能是布尔值")
    try:
        parsed = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label} 数字格式无效") from None
    return number(parsed, label)


def yuan_to_cents(value):
    if isinstance(value, bool):
        raise ValueError("金额不能是布尔值")
    try:
        amount = Decimal(str(value))
        scaled = amount * 100
        if not amount.is_finite() or amount < 0 or scaled != scaled.to_integral_value():
            raise ValueError("金额必须非负且精确到分")
        return int(scaled)
    except InvalidOperation:
        raise ValueError("元金额格式无效") from None


def nutrition_rows(raw):
    data = envelope(raw)["data"]
    if isinstance(data, str):
        # Official flat TOON table subset: [N]{field1,field2}: then CSV rows.
        lines = data.strip().splitlines()
        if not lines:
            raise ValueError("营养表为空")
        match = re.fullmatch(r"\[(\d+)\]\{([^{}]+)\}:\s*", lines[0].strip())
        if not match:
            raise ValueError("不支持的 TOON 表头，请保留原始返回并更新解析器")
        fields = [f.strip() for f in match[2].split(",")]
        if len(set(fields)) != len(fields):
            raise ValueError("TOON 字段重复")
        rows = []
        for line in lines[1:]:
            if not line.strip():
                continue
            values = next(csv.reader([line.strip()], strict=True, skipinitialspace=True))
            if len(values) != len(fields):
                raise ValueError("TOON 行字段数量不匹配")
            rows.append(dict(zip(fields, (None if v == "null" else v for v in values))))
        if len(rows) != int(match[1]):
            raise ValueError("TOON 声明行数与实际行数不符")
    elif isinstance(data, list):
        rows = data
    else:
        raise ValueError("营养 data 必须为 TOON 字符串或数组")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("productName"), str) or not row["productName"].strip():
            raise ValueError("营养记录缺少 productName")
        if not set(FIELDS.values()) <= set(row):
            raise ValueError("营养字段缺失，不能把 schema 改变当作零值")
        for key in (*FIELDS.values(), "energyKj", "sodium", "calcium"):
            if key in row:
                numeric(row[key], key)
    return rows


def canonical_name(value):
    name = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()
    # Formatting-only wrappers; never strip recipe/sugar/flavour annotations.
    name = re.sub(r"\(((?:小|中|大)(?:杯|份)|迷你份)\)", r"\1", name)
    name = re.sub(r"\((?:盒装|瓶装|罐装)\)", "", name)
    return name


def spec_names(menu_name, spec):
    """Only explicit, verified sizes/counts; never fuzzy-match away sugar or recipe differences."""
    name = canonical_name(menu_name)
    size = spec.get("size")
    labels = {"small": "小", "medium": "中", "large": "大", "mini": "迷你"}
    if size is not None and size not in labels:
        raise ValueError("spec.size 应为 small / medium / large / mini")
    if name == "薯条" and size:
        return [labels[size] + "薯条"]
    if name == "玉米杯" and size in ("small", "large"):
        return [labels[size] + "杯玉米杯", "玉米杯" + labels[size] + "杯"]
    if name in ("可乐", "可口可乐", "无糖可口可乐") and size in ("small", "medium", "large"):
        base = "可乐" if name == "可口可乐" else menu_name
        return [base + labels[size] + "杯"]
    if name == "麦乐鸡" and spec.get("pieces") is not None:
        pieces = spec["pieces"]
        if isinstance(pieces, bool) or not isinstance(pieces, int) or pieces < 1:
            raise ValueError("spec.pieces 需为正整数")
        return ["麦乐鸡" + str(pieces) + "块"]
    return []


def match_nutrition(rows, selector, menu_name):
    expected = selector.get("nutrition_name", menu_name)
    names = spec_names(menu_name, selector.get("spec", {}))
    if names and (not isinstance(selector.get("spec_evidence"), dict) or selector["spec_evidence"].get("source") not in ("query-meal-detail", "official_menu", "user_confirmed")):
        return {n: None for n in NUTRIENTS}, {"menu_name": menu_name, "match": "spec_unverified", "requested_nutrition_name": expected}, "unknown"
    if names and "nutrition_name" in selector and canonical_name(expected) not in {canonical_name(n) for n in names}:
        return {n: None for n in NUTRIENTS}, {"menu_name": menu_name, "match": "spec_conflict", "requested_nutrition_name": expected}, "unknown"
    expected_names = names or [expected]
    matches = [r for r in rows if canonical_name(r["productName"]) in {canonical_name(n) for n in expected_names}]
    if "nutrition_description" in selector:
        matches = [r for r in matches if r.get("nutritionDescription") == selector["nutrition_description"]]
    # Real table repeats identical milk records. Deduplicate only complete equivalent records;
    # two servings or genuinely different values still remain ambiguous.
    unique = {}
    for row in matches:
        signature = digest({"name": canonical_name(row["productName"]), "description": row.get("nutritionDescription"), "values": {field: numeric(row.get(field), field) for field in (*FIELDS.values(), "energyKj", "sodium", "calcium")}})
        unique.setdefault(signature, row)
    duplicate_count = len(matches) - len(unique)
    matches = list(unique.values())
    if len(matches) != 1:
        return {n: None for n in NUTRIENTS}, {"menu_name": menu_name, "match": "ambiguous" if len(matches) > 1 else "not_found", "requested_nutrition_name": expected}, "unknown"
    row = matches[0]
    serving = selector.get("serving") or row.get("nutritionDescription")
    # A unique name alone is insufficient to attest the selected menu size.
    if not isinstance(serving, str) or not serving.strip():
        return {n: None for n in NUTRIENTS}, {"menu_name": menu_name, "match": "serving_unverified", "requested_nutrition_name": expected}, "unknown"
    values = {n: numeric(row[field], field) for n, field in FIELDS.items()}
    if values["kcal"] is None and row.get("energyKj") is not None:
        values["kcal"] = numeric(row["energyKj"], "energyKj") / 4.184
    evidence = {"menu_name": menu_name, "nutrition_name": row["productName"], "serving": serving, "nutrition_description": row.get("nutritionDescription"), "match": "verified_spec" if names else "normalized_name" if row["productName"] != expected else "exact_explicit_spec", "identical_duplicates_removed": duplicate_count, "spec": selector.get("spec", {}), "spec_evidence": selector.get("spec_evidence")}
    return values, evidence, "official"


def normalize_menu(menu_raw, nutrition_raw, selection):
    menu_data = envelope(menu_raw)["data"]
    if not isinstance(menu_data, dict):
        raise ValueError("菜单 data 必须是对象，请核对 schema")
    meals = menu_data.get("meals")
    if not isinstance(meals, dict):
        raise ValueError("菜单缺少 data.meals 映射，需核对运行时 schema")
    rows = nutrition_rows(nutrition_raw)
    parameters = selection["parameters"]
    if not parameters.get("storeCode") or parameters.get("orderType") not in (1, 2) or parameters.get("beType") not in (1, 2, 5, 6):
        raise ValueError("参数需要 storeCode / orderType / beType")
    selected = selection.get("items", [])
    if not selected:
        raise ValueError("先明确分类、规格和套餐选项，再生成菜单快照")
    items = []
    for selector in selected:
        if not isinstance(selector.get("serving"), str) or not selector["serving"].strip():
            raise ValueError("每项 selection 必填 serving；先查详情确认杯型/块数/单份规格，不能依赖 nutritionDescription")
        code = str(selector["product_code"])
        if code not in meals or not isinstance(meals[code], dict):
            raise ValueError(f"所选商品 {code} 不在当前菜单中")
        entry = meals[code]
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("菜单商品 name 字段缺失")
        category = selector["category"]
        values, evidence, source = match_nutrition(rows, selector, name)
        mcp_item = {"productCode": code, "quantity": 1}
        options = selector.get("options", {})
        if set(options) - {"roundList", "modification"}:
            raise ValueError("选项仅支持已核实 roundList / modification")
        mcp_item.update(copy.deepcopy(options))
        if options.get("modification"):
            # Official per-serving table cannot quantify custom removal/addition.
            values, source = {n: None for n in NUTRIENTS}, "unknown"
            evidence["match"] = "customization_unquantified"
        item = {"id": selector.get("id", code), "name": selector.get("display_name", name), "category": category, "available": True, "nutrition": values, "nutrition_source": source, "evidence": evidence, "mcp_item": mcp_item}
        if selector.get("serving"):
            item["serving"] = selector["serving"]
        if options.get("modification"):
            item["customization"] = copy.deepcopy(options["modification"])
        if category == "bundle":
            if not options.get("roundList"):
                raise ValueError("套餐必须根据详情提供确定的 roundList，不能猜默认配餐")
            item["components"] = selector["components"]
        items.append(item)
    snapshot = {"kind": "official", "day": selection["day"], "meal": selection["meal"], "store_code": parameters["storeCode"], "context": selection["context"], "parameters": {k: copy.deepcopy(parameters[k]) for k in CONTEXT_FIELDS if k in parameters}, "items": items, "source": "official MCP documented response, offline conversion"}
    from catalog_builder import discover_bundles  # Lazy import: both adapters share the same discovery contract.
    plan = discover_bundles(menu_raw, selection)["bundle_plan"]
    built = {str(i["mcp_item"]["productCode"]) for i in items if i["category"] == "bundle"}
    snapshot["bundle_comparison"] = {**plan, "built_codes": sorted(built), "missing_detail_codes": sorted(set(plan["required_codes"]) - built)}
    snapshot["nutrition_warnings"] = [{"id": i["id"], "name": i["name"], "reason": i["evidence"].get("match"), "action": "确认详情规格与对应营养名；仍未收录则联网查来源"} for i in items if i["nutrition_source"] == "unknown" and i["category"] != "bundle"]
    validate_snapshot(snapshot, snapshot["day"])
    return snapshot


COUPON_SCOPE_FIELDS = ("storeCode", "beCode", "orderType", "beType", "reservationDate")


def normalize_store_coupons(raw, parameters):
    data = envelope(raw)["data"]
    if not isinstance(data, list):
        raise ValueError("query-store-coupons data 应为数组；其他券列表不能当作门店可用券")
    scope = {key: copy.deepcopy(parameters[key]) for key in COUPON_SCOPE_FIELDS if key in parameters}
    if not scope.get("storeCode") or not scope.get("orderType") or not scope.get("beType"):
        raise ValueError("优惠券查询需绑定门店与取餐方式")
    coupons, unresolved, seen = [], [], {}
    for row in data:
        if not isinstance(row, dict):
            raise ValueError("优惠券记录需为对象")
        identifier, code = row.get("couponId"), row.get("couponCode")
        if not isinstance(identifier, str) or not identifier or not isinstance(code, str) or not code:
            unresolved.append({"title": row.get("title", "未知券"), "reason": "缺少可计价券 ID/code；不从券名推算折扣"})
            continue
        identity = (identifier, code)
        period = row.get("tradeDateTime", "")
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+-\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", period) if isinstance(period, str) else None
        products = row.get("products")
        if not match or not isinstance(products, list) or not products or any(not isinstance(p, dict) or not p.get("productCode") for p in products):
            unresolved.append({"title": row.get("title", "未知券"), "reason": "有效期或适用商品未明确；需核实后报价"})
            continue
        try:
            start, end = [datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN_TIME) for s in match.groups()]
        except ValueError:
            unresolved.append({"title": row.get("title", "未知券"), "reason": "有效期格式无效"})
            continue
        if end < start:
            raise ValueError("优惠券有效期起止倒置")
        normalized = {"coupon_id": identifier, "coupon_code": code, "title": row.get("title", "优惠券"), "product_codes": sorted({str(p["productCode"]) for p in products}), "valid_from": start.isoformat(), "valid_until": end.isoformat()}
        if identity in seen:
            previous = seen[identity]
            if previous is not None and any(previous[k] != normalized[k] for k in ("product_codes", "valid_from", "valid_until")):
                coupons = [c for c in coupons if (c["coupon_id"], c["coupon_code"]) != identity]
                seen[identity] = None
                unresolved.append({"title": row.get("title", "未知券"), "reason": "同一券码的有效期/适用商品冲突，需核实"})
            continue
        seen[identity] = normalized
        coupons.append(normalized)
    return {"source": "query-store-coupons", "parameters": scope, "coupons": coupons, "unresolved": unresolved, "scope": "门店返回的券仅用于构造计价请求；实际折扣、门槛、互斥与使用次数由 calculate-price 核实"}


def price_requests(snapshot, recommendation, coupons=None, max_per_candidate=4, max_requests=24):
    catalog = validate_snapshot(snapshot, snapshot["day"])
    if recommendation["kind"] != "official" or recommendation["day"] != snapshot["day"] or recommendation["meal"] != snapshot["meal"]:
        raise ValueError("候选与快照不同")
    parameters = snapshot.get("parameters")
    if not parameters or parameters.get("storeCode") != snapshot["store_code"]:
        raise ValueError("快照缺少官方计价参数")
    requests = []
    blocked_candidates = []
    for candidate in recommendation["quote_requests"]:
        actual = aggregate(candidate["items"], catalog)
        if actual["key"] != candidate["key"]:
            raise ValueError("候选 ID 与内容不一致")
        payload = copy.deepcopy(parameters)
        payload["items"] = []
        for choice in candidate["items"]:
            item = copy.deepcopy(catalog[choice["id"]].get("mcp_item"))
            if not item:
                raise ValueError("菜单项缺少已验证 mcp_item")
            item["quantity"] = choice["quantity"]
            payload["items"].append(item)
        try:
            validate_coupon_items(payload["items"])
        except ValueError as exc:
            blocked_candidates.append({"key": candidate["key"], "reason": str(exc)})
            continue
        requests.append({"key": candidate["key"], "request_id": candidate["key"], "tool": "calculate-price", "arguments": payload, "variant": "原参数"})
    if isinstance(max_per_candidate, bool) or not isinstance(max_per_candidate, int) or not 1 <= max_per_candidate <= 8 or isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 1 <= max_requests <= 96:
        raise ValueError("每候选报价数应为1–8，总请求数应为1–96")
    if len(requests) > max_requests:
        raise ValueError("总请求预算不足以覆盖原参数报价，请减少营养候选")
    variants = []
    blocked = []
    if coupons is not None:
        scope = {key: parameters[key] for key in COUPON_SCOPE_FIELDS if key in parameters}
        if coupons.get("source") != "query-store-coupons" or coupons.get("parameters") != scope:
            raise ValueError("优惠券与门店/取餐方式/预约时间上下文不一致")
        at = datetime.strptime(parameters["reservationDate"], "%Y-%m-%d %H:%M").replace(tzinfo=CN_TIME) if parameters.get("reservationDate") else datetime.now(CN_TIME)
        for coupon in coupons["coupons"]:
            if not datetime.fromisoformat(coupon["valid_from"]) <= at <= datetime.fromisoformat(coupon["valid_until"]):
                blocked.append({"coupon_id": coupon["coupon_id"], "reason": "不在本次取餐有效期内"})
                continue
            for original in requests:
                if any(i.get("couponId") or i.get("couponCode") for i in original["arguments"]["items"]):
                    continue  # Current official flow permits one coupon per order, not per item.
                for index, item in enumerate(original["arguments"]["items"]):
                    if str(item["productCode"]) not in coupon["product_codes"] or item.get("couponId") or item.get("couponCode"):
                        continue
                    payload = copy.deepcopy(original["arguments"])
                    chosen = payload["items"][index]
                    # Only one serving uses this coupon; remaining units remain full-price.
                    if chosen["quantity"] > 1:
                        remainder = copy.deepcopy(chosen)
                        remainder["quantity"] -= 1
                        payload["items"].insert(index + 1, remainder)
                        chosen["quantity"] = 1
                    chosen.update(couponId=coupon["coupon_id"], couponCode=coupon["coupon_code"])
                    variants.append({"key": original["key"], "request_id": digest({"key": original["key"], "arguments": payload})[:24], "tool": "calculate-price", "arguments": payload, "variant": coupon["title"]})
    selected = list(requests)
    counts = {request["key"]: 1 for request in requests}
    seen = {r["request_id"] for r in requests}
    for variant in variants:
        if len(selected) >= max_requests or counts[variant["key"]] >= max_per_candidate or variant["request_id"] in seen:
            continue
        selected.append(variant)
        counts[variant["key"]] += 1
        seen.add(variant["request_id"])
    return {"kind": "official", "context_id": recommendation["context_id"], "day": snapshot["day"], "store_code": snapshot["store_code"], "context": snapshot["context"], "requests": selected, "blocked_candidates": blocked_candidates, "coupon_comparison": {"generated_variant_count": len(variants), "quoted_variant_count": len(selected) - len(requests), "truncated": len(selected) - len(requests) < len(variants), "blocked": blocked, "unresolved": coupons.get("unresolved", []) if coupons else [], "scope": "仅比较选中的单券计价变体；不推断多券叠加，凑单/赠品改变餐品须另建营养候选"}}


def extract_threshold_gap(price_response):
    """Adapted from mcd-coupon-strategist; MIT notice in references/third-party-notices.md.

    Preserve the upstream distinction: balance is cents, realDiscount is yuan.
    This is an unmet promotion hint, never a reduction of the payable price.
    """
    data = price_response.get("data") or {}
    enjoyable = data.get("enjoyable")
    if not enjoyable:
        return None
    if not isinstance(enjoyable, dict):
        raise ValueError("优惠门槛提示格式错误")
    return {
        "balance_cents": cents(enjoyable["balance"]),
        "amount_type": str(enjoyable.get("amountType", "")),
        "potential_discount_cents": yuan_to_cents(enjoyable["realDiscount"]) if enjoyable.get("realDiscount") is not None else None,
        "applied": False,
        "note": "未满足的优惠提示；加购后的应付、餐品和营养须重新计价评估，不能直接扣减本单金额。",
    }


def prepare_order(quotes, key, fulfillment):
    """Copy the priced coupon/SKU/recipe intact; local preparation, no redemption.

    Adding a coupon, changing items or buying membership requires a new quote.
    Only create-order's fulfillment-only fields may be supplied separately.
    """
    if quotes.get("kind") != "official":
        raise ValueError("仅支持官方上下文报价")
    matches = [q for q in quotes.get("quotes", []) if q.get("key") == key]
    if len(matches) != 1:
        raise ValueError("需要唯一的所选报价")
    quote = matches[0]
    payable_cents(quote["total_cents"])
    calculated = quote.get("calculate_payload")
    if not isinstance(calculated, dict) or digest(calculated) != quote.get("calculate_payload_hash"):
        raise ValueError("计价参数缺失或哈希不一致")
    stamp = datetime.fromisoformat(quote["quoted_at"])
    now = datetime.now(CN_TIME)
    if stamp.tzinfo is None or not 0 <= (now - stamp).total_seconds() <= 900 or stamp.astimezone(CN_TIME).date() != now.date():
        raise ValueError("过期、未来或跨日报价，请重新计价")
    if calculated.get("storeCode") != quotes.get("store_code"):
        raise ValueError("计价门店不一致")
    if not isinstance(fulfillment, dict) or set(fulfillment) - {"takeWayCode", "addressId", "remark"}:
        raise ValueError("只能补充 takeWayCode/addressId/remark，不得修改计价餐品、券或其他参数")
    if any(not isinstance(v, str) or not v.strip() for v in fulfillment.values()):
        raise ValueError("取餐/配送字段须为非空字符串")
    if len(fulfillment.get("remark", "")) > 50:
        raise ValueError("备注最多50字")
    order_type, be_type = calculated.get("orderType"), calculated.get("beType")
    if type(order_type) is not int or type(be_type) is not int or (order_type, be_type) not in ((1, 1), (1, 5), (2, 2), (2, 6)):
        raise ValueError("订单类型与业务场景不匹配")
    if be_type in (2, 5, 6) and not calculated.get("beCode"):
        raise ValueError("当前场景缺少已计价的 beCode")
    if be_type == 6 and not calculated.get("gmServiceCode"):
        raise ValueError("团餐缺少已计价的 gmServiceCode")
    if order_type == 1:
        allowed = {v.get("code") for v in quote.get("take_way_options", []) if isinstance(v, dict) and isinstance(v.get("code"), str)}
        if fulfillment.get("takeWayCode") not in allowed or "addressId" in fulfillment:
            raise ValueError("取餐方式必须来自本次报价 takeWayList，且到店不传配送地址")
    elif not fulfillment.get("addressId") or "takeWayCode" in fulfillment:
        raise ValueError("外送/团餐需用户选定 addressId，且不传 takeWayCode")
    items = calculated.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("计价缺少餐品")
    validate_coupon_items(items)
    result = copy.deepcopy(quotes)
    chosen = next(q for q in result["quotes"] if q["key"] == key)
    chosen["order_payload"] = {**copy.deepcopy(calculated), **copy.deepcopy(fulfillment)}
    return result


def normalize_quotes(requests, responses):
    if requests.get("kind") != "official":
        raise ValueError("只处理真实 MCP 请求上下文")
    quotes = []
    failures = []
    known_keys = {r.get("request_id", r["key"]) for r in requests["requests"]}
    if len(known_keys) != len(requests["requests"]):
        raise ValueError("报价请求 ID 重复；不同优惠变体必须使用独立 request_id")
    if set(responses) - known_keys:
        raise ValueError("响应包含不在请求中的候选")
    for request in requests["requests"]:
        identifier = request.get("request_id", request["key"])
        if identifier not in responses:
            failures.append({"request_id": identifier, "key": request["key"], "reason": "missing_response"})
            continue
        try:
            response = envelope(responses[identifier])
            data = response["data"]
            total = cents(data["price"])
            if total == 0:
                failures.append({"request_id": identifier, "key": request["key"], "reason": "zero_price_requires_verification"})
                continue
            #  # Full payable cents; never recompute from coupon headline or productPrice.
            timestamp = datetime.strptime(response["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN_TIME)
            if not 0 <= (datetime.now(CN_TIME) - timestamp).total_seconds() <= 900 or (requests.get("day") and timestamp.date().isoformat() != requests["day"]):
                raise ValueError("过期、未来或跨日报价")
            quote = {"key": request["key"], "request_id": identifier, "variant": request.get("variant"), "kind": requests["kind"], "store_code": requests["store_code"], "context": requests["context"], "total_cents": total, "quoted_at": timestamp.isoformat(), "calculate_payload": copy.deepcopy(request["arguments"]), "calculate_payload_hash": digest(request["arguments"])}
            if "discount" in data:
                quote["discount_cents"] = cents(data["discount"])
            quote["price_breakdown"] = {key: cents(data[key]) for key in ("productPrice", "deliveryPrice", "packingPrice", "tablewarePrice") if key in data}
            quote["take_way_options"] = data.get("takeWayList", [])
            try:
                quote["promotion_hint"] = extract_threshold_gap(response)
            except (ValueError, KeyError, TypeError):
                # A broken optional hint must not discard a valid payable price.
                quote["promotion_hint"] = None
                quote["promotion_hint_warning"] = "优惠门槛提示无法核实，不据此推荐凑单"
            quotes.append(quote)
        except (ValueError, KeyError, TypeError):
            failures.append({"request_id": identifier, "key": request["key"], "reason": "price_response_unusable"})
    grouped = {}
    for quote in quotes:
        if quote["key"] not in grouped or (quote["total_cents"], quote["request_id"]) < (grouped[quote["key"]]["total_cents"], grouped[quote["key"]]["request_id"]):
            grouped[quote["key"]] = quote
    coupon_comparison = requests.get("coupon_comparison", {})
    return {"kind": "official", "context_id": requests["context_id"], "store_code": requests["store_code"], "context": requests["context"], "quotes": list(grouped.values()), "quote_failures": failures, "successful_variant_count": len(quotes), "comparison_complete": not failures and not coupon_comparison.get("unresolved") and not coupon_comparison.get("truncated"), "coupon_comparison": coupon_comparison}


def review_checks(quotes, bundles_checked=False, coupons_checked=False, coupon_pages_complete=True):
    if any(not isinstance(v, bool) for v in (bundles_checked, coupons_checked, coupon_pages_complete)):
        raise ValueError("实际查询完成标志必须为布尔值")
    return {"bundles_checked": bundles_checked, "coupons_checked": coupons_checked, "quote_failure_count": len(quotes.get("quote_failures", [])), "coupon_unresolved_count": len(quotes.get("coupon_comparison", {}).get("unresolved", [])), "coupon_pages_complete": coupon_pages_complete, "coupon_variants_truncated": bool(quotes.get("coupon_comparison", {}).get("truncated"))}


def normalize_orders(raw, account_alias):
    if not account_alias or not isinstance(account_alias, str):
        raise ValueError("需要稳定的非敏感账号别名")
    data = envelope(raw)["data"]
    # Live service returns {} when this account has no recent food orders.
    if data == {}:
        orders = []
    elif isinstance(data, dict) and "list" in data:
        orders = data["list"]
    elif isinstance(data, dict) and ("orderId" in data or "orderDetail" in data):
        orders = [data]
    else:
        raise ValueError("历史订单结构不受支持，请核对 data.list")
    if not isinstance(orders, list):
        raise ValueError("历史订单缺少 data.list")
    bills, unresolved = [], []
    for outer in orders:
        order = outer.get("orderDetail", outer)
        identifier = digest({"account": account_alias, "order": str(outer.get("orderId", order.get("orderId", "")))})
        if not outer.get("orderId", order.get("orderId")):
            raise ValueError("订单缺少 orderId")
        status = order.get("orderStatus")
        # Completed is documented. Ambiguous cancellation/refund is not evidence of no payment.
        if status in ("订单已完成", "已完成"):
            local_status = "paid"
        elif status in ("待支付", "待付款"):
            local_status = "pending"
        else:
            unresolved.append({"id": identifier, "status": status, "reason": "状态无法证明付款/退款金额，请查询详情；不自动写入消费"})
            continue
        timestamp = datetime.strptime(order["createTime"], "%Y-%m-%d %H:%M:%S")
        bill = {"id": identifier, "day": timestamp.date().isoformat(), "status": local_status, "paid_cents": yuan_to_cents(order["realTotalAmount"]) if local_status == "paid" else 0, "refund_cents": 0, "source": "official", "items": [{"name": i["productName"], "quantity": number(i["quantity"], "购买数量", 1)} for i in order.get("orderProductList", [])]}
        if local_status == "paid" and order.get("totalDiscountAmount") is not None:
            bill["discount_cents"] = yuan_to_cents(order["totalDiscountAmount"])
        bills.append(bill)
    return {"bills": bills, "unresolved": unresolved, "coverage": "仅本次接口返回的近期记录，通常最多 10 笔；不代表全年完整消费"}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="官方 MCP 原始返回 → 本地计划数据（离线）")
    parser.add_argument("--output", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("nutrition"); p.add_argument("--response", required=True)
    p = sub.add_parser("menu"); p.add_argument("--menu-response", required=True); p.add_argument("--nutrition-response", required=True); p.add_argument("--selection", required=True)
    p = sub.add_parser("price-requests"); p.add_argument("--snapshot", required=True); p.add_argument("--recommendation", required=True)
    p.add_argument("--coupons"); p.add_argument("--max-per-candidate", type=int, default=4); p.add_argument("--max-requests", type=int, default=24)
    p = sub.add_parser("coupons"); p.add_argument("--response", required=True); p.add_argument("--parameters", required=True)
    p = sub.add_parser("review-checks"); p.add_argument("--quotes", required=True); p.add_argument("--bundles-checked", action="store_true"); p.add_argument("--coupons-checked", action="store_true"); p.add_argument("--coupon-pages-complete", choices=("yes", "no"), default="yes")
    p = sub.add_parser("quotes"); p.add_argument("--requests", required=True); p.add_argument("--responses", required=True)
    p = sub.add_parser("prepare-order"); p.add_argument("--quotes", required=True); p.add_argument("--key", required=True); p.add_argument("--fulfillment", required=True)
    p = sub.add_parser("orders"); p.add_argument("--response", required=True); p.add_argument("--account-alias", required=True)
    args = parser.parse_args()
    try:
        if args.command == "nutrition": result = nutrition_rows(read_json(args.response))
        elif args.command == "menu": result = normalize_menu(read_json(args.menu_response), read_json(args.nutrition_response), read_json(args.selection))
        elif args.command == "price-requests": result = price_requests(read_json(args.snapshot), read_json(args.recommendation), read_json(args.coupons) if args.coupons else None, args.max_per_candidate, args.max_requests)
        elif args.command == "coupons": result = normalize_store_coupons(read_json(args.response), read_json(args.parameters))
        elif args.command == "review-checks": result = review_checks(read_json(args.quotes), args.bundles_checked, args.coupons_checked, args.coupon_pages_complete == "yes")
        elif args.command == "quotes": result = normalize_quotes(read_json(args.requests), read_json(args.responses))
        elif args.command == "prepare-order": result = prepare_order(read_json(args.quotes), args.key, read_json(args.fulfillment))
        elif args.command == "orders": result = normalize_orders(read_json(args.response), args.account_alias)
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps({"output": str(Path(args.output).resolve())}, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError, csv.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
