"""Build explicit nutrition/price candidates from official menu + selected detail rounds. No network."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import itertools
import json
import re
from pathlib import Path
import sys

import fit_core as core
import mcp_adapter as adapter


def scope(selection):
    return core.digest({key: selection[key] for key in ("day", "meal", "context", "parameters")})


def timestamp(now=None):
    now = now or datetime.now(core.CN_TIME)
    if now.tzinfo is None:
        raise ValueError("时间必须带时区")
    return now


def fresh(raw, now):
    try:
        at = datetime.strptime(adapter.envelope(raw)["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=core.CN_TIME)
        return 0 <= (now - at).total_seconds() <= 1800
    except (ValueError, KeyError, TypeError):
        return False


BUNDLE_NAME = re.compile(r"套餐|[两二三四五六七八九十\d]+件套|随心配|随心选|[一二两三四五六\d]+人餐|分享餐|超值配")


def discover_bundles(menu_raw, selection, limit=6):
    """Find meal deals before detail queries, even when the caller selected singles.

    Name matching discovers candidates only; composition and price still require
    official details and calculate-price. The bounded scope is reported honestly.
    """
    if type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError("套餐根商品查询范围需为1–8")
    meals = adapter.envelope(menu_raw)["data"]["meals"]
    result = copy.deepcopy(selection)
    for item in result["items"]:
        if BUNDLE_NAME.search(meals.get(str(item["product_code"]), {}).get("name", "")):
            item["category"] = "bundle"
    policy = result.get("bundle_policy", "bundle_first")
    if policy not in ("bundle_first", "single_items_only"):
        raise ValueError("bundle_policy 需为bundle_first/single_items_only")
    reason = result.get("bundle_policy_reason", "")
    if policy == "single_items_only" and (not isinstance(reason, str) or not reason.strip()):
        raise ValueError("仅单点需记录用户明确要求的bundle_policy_reason")
    has_main = any(i.get("category") in ("main", "bundle") for i in result["items"])
    names = [meals.get(str(i["product_code"]), {}).get("name", "") for i in result["items"] if i.get("category") == "main"]
    candidates = [(str(code), row["name"]) for code, row in meals.items() if isinstance(row, dict) and BUNDLE_NAME.search(row.get("name", ""))]
    candidates.sort(key=lambda pair: (not any(name and name in pair[1] for name in names), not re.search(r"大堡口福|超值|随心配", pair[1]), pair[0]))
    selected = {str(i["product_code"]) for i in result["items"] if i.get("category") == "bundle"}
    relevant = [code for code, _ in candidates[:limit]] if has_main and policy == "bundle_first" else []
    required = sorted(selected | set(relevant))
    for code in relevant:
        if code not in selected:
            result["items"].append({"product_code": code, "category": "bundle", "expand": True})
            selected.add(code)
    result["bundle_plan"] = {"policy": policy, "required_codes": required, "menu_bundle_count": len(candidates), "unselected_count": max(0, len(candidates) - len(set(relevant) | {c for c, _ in candidates if c in selected})), "limit": limit, "scope": "按明确菜单名称发现的相关套餐及限额内超值搭配；并非小程序全部活动或全菜单最低价"}
    return result


def detail_requests(selection, cache=None, limit=12, now=None, menu=None):
    if menu is not None:
        selection = discover_bundles(menu, selection)
    now = timestamp(now)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
        raise ValueError("详情请求上限需为1–30")
    cached = (cache or {}).get("responses", {}) if (cache or {}).get("scope") == scope(selection) else {}
    requests, deferred, reused = [], [], []
    seen = set()
    for item in selection["items"]:
        code = str(item["product_code"])
        if code in seen:
            continue
        seen.add(code)
        if code in cached and fresh(cached[code], now):
            reused.append(code)
            continue
        if len(requests) >= limit:
            deferred.append(code)
            continue
        args = {k: copy.deepcopy(selection["parameters"][k]) for k in adapter.COUPON_SCOPE_FIELDS if k in selection["parameters"]}
        args["code"] = code  # query-meal-detail uses code, not productCode.
        requests.append({"request_id": code, "tool": "query-meal-detail", "arguments": args})
    return {"scope": scope(selection), "requests": requests, "reused": reused, "deferred": deferred, "bundle_plan": selection.get("bundle_plan"), "message": "仅查询候选根商品；套餐组件来自详情，不逐个查询所有菜单项"}


def collect_details(requests, responses, cache=None):
    expected = {r["request_id"] for r in requests["requests"]}
    if set(responses) - expected:
        raise ValueError("详情响应包含本轮未请求的编码")
    result = copy.deepcopy(cache) if cache and cache.get("scope") == requests["scope"] else {"scope": requests["scope"], "responses": {}}
    failed = []
    for code in expected:
        try:
            raw = responses[code]
            data = adapter.envelope(raw)["data"]
            if str(data["code"]) != code or not data.get("name"):
                raise ValueError("详情商品编码不同")
            result["responses"][code] = raw
        except (ValueError, KeyError, TypeError):
            failed.append(code)
    result["failed"] = sorted(failed)
    return result


def positive_int(value, minimum=0):
    core.number(value, "详情数量", minimum)
    if int(value) != value:
        raise ValueError("详情数量需为整数")
    return value


def round_selection(round_data, explicit=None):
    choices = {str(choice["code"]): choice for choice in round_data["choices"]}
    if len(choices) != len(round_data["choices"]):
        raise ValueError("同一轮次出现重复选项编码")
    if explicit is None:
        picked = [{"code": str(c["code"]), "quantity": positive_int(c.get("quantity", 0))} for c in choices.values() if c.get("quantity", 0) > 0]
        if not picked and round_data.get("minQuantity", 1) > 0:
            defaults = [c for c in choices.values() if c.get("isDefault") == 1]
            if len(defaults) != 1 or round_data.get("quantity") != 1:
                raise ValueError("默认选项不唯一或数量不明确，需要明确选择")
            picked = [{"code": str(defaults[0]["code"]), "quantity": 1}]
    else:
        picked = copy.deepcopy(explicit)
    total = 0
    seen = set()
    for choice in picked:
        if set(choice) - {"code", "quantity", "modification"}:
            raise ValueError("选配只能包含code/quantity/modification")
        code = str(choice["code"])
        if code not in choices or code in seen:
            raise ValueError("选项编码不在当前轮次或重复")
        seen.add(code)
        choice["code"] = code
        quantity = positive_int(choice["quantity"], 1)
        cap = choices[code].get("maxQuantity", -1)
        if quantity > 10 or (cap >= 0 and quantity > cap):
            raise ValueError("选项数量超限")
        total += quantity
    if total < round_data.get("minQuantity", 1) or (round_data.get("maxQuantity", 1) >= 0 and total > round_data.get("maxQuantity", 1)):
        raise ValueError("轮次选配数量不满足要求")
    return {"round": str(round_data["id"]), "comboItemList": picked}


def recipes(detail, selector, max_combinations=2000):
    rounds = detail.get("rounds", [])
    explicit = selector.get("options", {}).get("roundList")
    if not rounds:
        if explicit:
            raise ValueError("无选配轮次的单品不能附加roundList")
        return [[]]
    if len({str(r["id"]) for r in rounds}) != len(rounds):
        raise ValueError("详情轮次ID重复")
    selected = {str(r["round"]): r["comboItemList"] for r in explicit} if explicit is not None else {}
    if explicit is not None and (len(selected) != len(explicit) or set(selected) != {str(r["id"]) for r in rounds}):
        raise ValueError("显式roundList必须完整对应所有轮次")
    default = [round_selection(r, selected.get(str(r["id"]))) for r in rounds]
    if not selector.get("expand", False):
        return [default]
    if explicit is not None:
        raise ValueError("明确选配与自动expand不能同时使用；分别建立选择项")
    expand_rounds = selector.get("expand_rounds")
    if expand_rounds is not None and (not isinstance(expand_rounds, list) or set(expand_rounds) - {str(r["id"]) for r in rounds}):
        raise ValueError("expand_rounds 需为详情中允许替换的轮次字符串ID列表")
    groups = []
    for r, current in zip(rounds, default):
        if (expand_rounds is None or str(r["id"]) in expand_rounds) and r.get("minQuantity", 1) == r.get("maxQuantity", 1) == 1:
            options = [current]
            for choice in r["choices"]:
                candidate = round_selection(r, [{"code": str(choice["code"]), "quantity": 1}])
                if candidate not in options:
                    options.append(candidate)
        else:
            options = [current]  # Complex multi-select quantities are preserved, never guessed.
        groups.append(options)
    count = 1
    for group in groups:
        count *= len(group)
    if count > max_combinations:
        raise ValueError("选配组合超过2000；先按口味或明确选择缩小范围")
    return [list(combo) for combo in itertools.product(*groups)]


def build_catalog(menu_raw, nutrition_raw, selection, cache, coupons=None, target=None, max_variants=8, now=None, enriched=None):
    # Re-discover from the menu, rather than trusting a caller's completion flag.
    selection = discover_bundles(menu_raw, selection)
    now = timestamp(now)
    if cache.get("scope") != scope(selection):
        raise ValueError("详情缓存的门店/餐段/日期/方式上下文不符")
    if isinstance(max_variants, bool) or not isinstance(max_variants, int) or not 1 <= max_variants <= 20:
        raise ValueError("每根商品保留变体需为1–20")
    target = target or {}
    core.validate_target(target)
    meals = adapter.envelope(menu_raw)["data"]["meals"]
    rows = adapter.nutrition_rows(nutrition_raw)
    enriched_items = {}
    if enriched is not None:
        if enriched.get("detail_scope") != scope(selection):
            raise ValueError("补充营养快照与当前详情上下文不同")
        enriched_items = core.validate_snapshot(enriched, selection["day"])
    parameters = copy.deepcopy(selection["parameters"])
    if not parameters.get("storeCode") or parameters.get("orderType") not in (1, 2) or parameters.get("beType") not in (1, 2, 5, 6):
        raise ValueError("缺少门店/方式参数")
    coupon_rows = []
    if coupons is not None:
        if coupons.get("source") != "query-store-coupons" or coupons.get("parameters") != {k: parameters[k] for k in adapter.COUPON_SCOPE_FIELDS if k in parameters}:
            raise ValueError("券专属商品与当前门店/方式不符")
        coupon_rows = coupons["coupons"]
    catalog, roots, blocked = {}, [], []
    generated_count = 0

    def leaf(code, name, modification=None):
        identifier = "leaf:" + str(code) + ":" + core.digest({"name": adapter.canonical_name(name), "modification": modification})[:10]
        if identifier not in catalog:
            serving = "官方详情明确的标准单份：" + name
            values, evidence, source = adapter.match_nutrition(rows, {"serving": serving}, name)
            if modification:
                values, source = {n: None for n in core.NUTRIENTS}, "unknown"
                evidence["match"] = "customization_unquantified"
            evidence["detail_code"] = str(code)
            catalog[identifier] = {"id": identifier, "name": name, "category": "side", "available": False, "nutrition": values, "nutrition_source": source, "evidence": evidence, "serving": serving}
            if modification:
                catalog[identifier]["customization"] = modification
            previous = enriched_items.get(identifier)
            if previous and not modification and source == "unknown" and previous["name"] == name and previous.get("serving") == serving and previous.get("nutrition_sources"):
                for key in ("nutrition", "nutrition_bounds", "nutrition_sources", "nutrition_source", "evidence"):
                    if key in previous:
                        catalog[identifier][key] = copy.deepcopy(previous[key])
        return identifier

    for selector in selection["items"]:
        code = str(selector["product_code"])
        try:
            category = selector["category"]
            if category not in ("main", "side", "drink", "bundle"):
                raise ValueError("根商品需明确main/side/drink/bundle分类")
            allowed_options = {"roundList", "modification"}
            if set(selector.get("options", {})) - allowed_options:
                raise ValueError("未知点餐选项字段")
            coupon = None
            if selector.get("coupon"):
                matches = [c for c in coupon_rows if c["coupon_id"] == selector["coupon"]["coupon_id"] and c["coupon_code"] == selector["coupon"]["coupon_code"] and code in c["product_codes"]]
                if len(matches) != 1:
                    raise ValueError("券ID/code与券专属商品没有唯一对应证据")
                coupon = matches[0]
                service_at = datetime.strptime(parameters["reservationDate"], "%Y-%m-%d %H:%M").replace(tzinfo=core.CN_TIME) if parameters.get("reservationDate") else now
                if not datetime.fromisoformat(coupon["valid_from"]) <= service_at <= datetime.fromisoformat(coupon["valid_until"]):
                    raise ValueError("券在取餐时间不可用")
            if code not in meals and coupon is None:
                raise ValueError("根商品不在菜单中，也无当店券商品来源")
            raw = cache.get("responses", {}).get(code)
            if raw is None or not fresh(raw, now):
                raise ValueError("详情缺失或超过30分钟，先补查该根商品")
            detail = adapter.envelope(raw)["data"]
            if str(detail["code"]) != code:
                raise ValueError("详情返回了其他商品")
            variants = recipes(detail, selector)
            generated_count += len(variants)
            root_candidates = []
            for index, rounds in enumerate(variants):
                components = []
                root_mod = selector.get("options", {}).get("modification")
                for r in rounds:
                    original_round = next(x for x in detail["rounds"] if str(x["id"]) == r["round"])
                    for picked in r["comboItemList"]:
                        original_choice = next(x for x in original_round["choices"] if str(x["code"]) == str(picked["code"]))
                        identifier = leaf(picked["code"], original_choice["name"], root_mod or picked.get("modification"))
                        components.extend([identifier] * picked["quantity"])
                if not rounds:
                    components = [leaf(code, detail["name"], root_mod)]
                if not components or len(components) > 30:
                    raise ValueError("套餐组件为空或超过30份，请核实明确组成")
                root_id = str(selector.get("id", code)) + ("~" + core.digest(rounds)[:12] if index else "")
                payload = {"productCode": code, "quantity": 1}
                if rounds:
                    payload["roundList"] = rounds
                if root_mod:
                    payload["modification"] = root_mod
                if coupon:
                    payload.update(couponId=coupon["coupon_id"], couponCode=coupon["coupon_code"])
                root = {"id": root_id, "name": detail["name"] + ("：" + "＋".join(catalog[i]["name"] for i in components) if rounds else ""), "category": category, "available": True, "components": components, "nutrition": {}, "nutrition_source": "derived_from_official_components", "mcp_item": payload, "evidence": {"detail_code": code, "detail_time": adapter.envelope(raw)["datetime"], "coupon_product": bool(coupon)}}
                numeric = core.aggregate([{"id": i, "quantity": 1} for i in components], catalog)
                root_candidates.append((root, numeric))
            # Preserve the actual default first; choose other variants by nutrition fit, never headline price.
            target_nutrients = {key.rsplit("_", 1)[0] for key in target}
            best = root_candidates[:1] + sorted(root_candidates[1:], key=lambda pair: (len(core.violations(pair[1], target)), len(set(pair[1]["unknown_nutrients"]) & target_nutrients), core.fit_score(pair[1], target), len(pair[1]["unknown_nutrients"]), pair[0]["id"]))[:max_variants - 1]
            for root, _ in best:
                if root["id"] in catalog:
                    raise ValueError("根商品ID重复；不同用户选择请指定不同id")
                roots.append(root["id"])
                catalog[root["id"]] = root
        except (ValueError, KeyError, TypeError) as exc:
            blocked.append({"code": code, "reason": str(exc), "action": "补查详情或明确选配，不套用相似食品"})
    # Keep non-purchasable leaves for web supplementation, so an initially unknown side can
    # become eligible on a second build without manual recreation of its recipe.
    referenced = {i for r in roots for i in catalog[r]["components"]}
    leaves = sorted(i for i in catalog if i not in roots)
    items = [catalog[i] for i in roots] + [catalog[i] for i in leaves]
    snapshot = {"kind": "official", "day": selection["day"], "meal": selection["meal"], "context": selection["context"], "store_code": parameters["storeCode"], "parameters": parameters, "items": items, "nutrition_warnings": [{"id": i, "name": catalog[i]["name"], "reason": catalog[i]["evidence"].get("match"), "action": "联网查对应中国规格；不要漏掉酱料"} for i in sorted(referenced) if catalog[i]["nutrition_source"] == "unknown"], "catalog_status": {"selected_root_count": len(selection["items"]), "built_variant_count": len(roots), "enumerated_variant_count": generated_count, "truncated": generated_count > len(roots), "blocked": blocked, "scope": "仅涵盖选中的根商品与明确详情选项；营养完整性按全部组件计算，不用命中一项冒充整套"}}
    for key in ("requested_baskets", "required_quantities"):
        if key in selection:
            snapshot[key] = copy.deepcopy(selection[key])
    snapshot["detail_scope"] = scope(selection)
    built_codes = {catalog[r]["mcp_item"]["productCode"] for r in roots if catalog[r]["category"] == "bundle"}
    plan = selection["bundle_plan"]
    snapshot["bundle_comparison"] = {**plan, "built_codes": sorted(built_codes), "missing_detail_codes": sorted(set(plan["required_codes"]) - built_codes)}
    snapshot["catalog_status"]["unresolved_detail_options"] = [{"id": i, "name": catalog[i]["name"], "serving": catalog[i]["serving"], "code": catalog[i]["evidence"]["detail_code"]} for i in leaves if catalog[i]["nutrition_source"] == "unknown"]
    if items:
        core.validate_snapshot(snapshot, snapshot["day"])
    return snapshot


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="官方详情自动组装营养候选，无网络和账户写操作")
    parser.add_argument("--output", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("requests"); p.add_argument("--selection", required=True); p.add_argument("--cache"); p.add_argument("--limit", type=int, default=12); p.add_argument("--menu")
    p = sub.add_parser("discover"); p.add_argument("--selection", required=True); p.add_argument("--menu", required=True)
    p = sub.add_parser("collect"); p.add_argument("--requests", required=True); p.add_argument("--responses", required=True); p.add_argument("--cache")
    p = sub.add_parser("build"); p.add_argument("--selection", required=True); p.add_argument("--menu", required=True); p.add_argument("--nutrition", required=True); p.add_argument("--cache", required=True); p.add_argument("--coupons"); p.add_argument("--target"); p.add_argument("--max-variants", type=int, default=8)
    p.add_argument("--enriched", help="经网页补充后的同上下文快照；用于重排原先未知的选项")
    args = parser.parse_args()
    try:
        read = core.read_json
        if args.command == "requests":
            result = detail_requests(read(args.selection), read(args.cache) if args.cache else None, args.limit, menu=read(args.menu) if args.menu else None)
        elif args.command == "discover":
            result = discover_bundles(read(args.menu), read(args.selection))
        elif args.command == "collect":
            result = collect_details(read(args.requests), read(args.responses), read(args.cache) if args.cache else None)
        else:
            result = build_catalog(read(args.menu), read(args.nutrition), read(args.selection), read(args.cache), read(args.coupons) if args.coupons else None, read(args.target) if args.target else None, args.max_variants, enriched=read(args.enriched) if args.enriched else None)
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps({"output": str(Path(args.output).resolve()), "status": result.get("catalog_status")}, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
