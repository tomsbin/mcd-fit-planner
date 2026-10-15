# 从详情自动生成明确营养候选

适用于推荐/套餐/配餐变更。优先用 catalog_builder.py 取代手工填写每个品项的serving、营养别名和components；旧mcp_adapter menu仍兼容既有快照输入。

## 有限查询与组装

selection.local.json保留day/meal/context/parameters结构，items只要根商品编码、分类，以及用户明确选择/允许替换的范围。例如：

```json
{"day":"2026-10-15","meal":"lunch","context":"到店自取","parameters":{"storeCode":"实际门店","orderType":1,"beType":1},"items":[{"product_code":"实际套餐根编码","category":"bundle","expand":true,"expand_rounds":["2"]}]}
```

上例“2”必须取自真实详情，不是固定的小食轮次约定。expand_rounds限制只替换用户允许变化的轮次；不传时在限额内枚举所有单选轮次。明确的options.roundList与expand不能混用。单个饮料包装可用category=drink，选中杯型同样从详情轮次解析；普通单品无rounds时按详情的标准单份匹配。

```powershell
python scripts/catalog_builder.py --output detail-requests.local.json requests --selection selection.local.json --menu menu-response.local.json --limit 12
# 按requests调用query-meal-detail，按request_id保存原始响应。参数名是code，不是productCode。
python scripts/catalog_builder.py --output details.local.json collect --requests detail-requests.local.json --responses detail-responses.local.json
python scripts/catalog_builder.py --output snapshot.local.json build --selection selection.local.json --menu menu-response.local.json --nutrition nutrition-response.local.json --cache details.local.json --target meal-target.local.json
```

0.7.0默认从原始菜单中发现名称明确的套餐/三件套/随心配等相关商品：先匹配所选主食名称，再补限额内的超值套餐（默认至多6个新发现根商品，显式选中的套餐保留）。这只是候选发现，不能凭名称猜实际组成或价格。也可先执行 `catalog_builder.py --output expanded-selection.local.json discover --selection selection.local.json --menu menu-response.local.json`，后续使用扩展文件。

即使漏传requests的--menu，build仍会补发现套餐；缺详情进入bundle_comparison.missing_detail_codes，必须继续requests/collect才能完成。不能手改完成标志。推荐前12个核价位置优先保留每个可行套餐系列与用户指定原单，避免被大量单点组合挤掉。phase2的preferred默认先取预算内且目标合格的套餐，同时保留closest/cheapest；无可行套餐时bundle_fallback_reason解释原因。

review会对照菜单发现记录和实际参与比较的报价核查，遗漏可行套餐的报价仍为incomplete，不能仅靠--bundles-checked声明完成。preview也拒绝缺少套餐发现记录的旧官方快照；请用当前菜单重新转换。发现限额之外的套餐数量会显示，只说“已查范围”，不宣传全菜单或小程序最低。小程序可见活动不一定与MCP一致，不能用MCP未返回推断其他渠道没有优惠。

用户明确只要单点时selection增加 `"bundle_policy":"single_items_only","bundle_policy_reason":"用户明确说不要套餐"`；不能为了跳过套餐检查而虚构这项用户要求。只买一杯饮料/一份配餐时不自动加主食。

requests/collect可传--cache复用同一上下文30分钟内的详情。门店、日期、餐段、取餐方式和参数变化不复用；--limit外的根商品明确列为deferred，不能当成查询完毕。无需为套餐每个子项再查一遍详情，更不要扫描117项菜单才给一次推荐。

### 缓存过期或组装为0条时

先检查 `catalog_status.blocked`。若提示“详情缺失或超过30分钟”，执行 `requests --selection ... --cache details.local.json` 生成缺失/过期根商品的查询，再实际调用 query-meal-detail；`collect --requests ... --responses ... --cache details.local.json` 合并新返回并再次 build。`--limit` 外的 deferred 需要下一批继续查询；别把更新时间手改为当前时间。

**`--enriched` 仅复用营养证据，不延长详情的30分钟有效期。** 补充营养耗时或跨会话后仍要刷新详情；全被阻断时0条是明确的未完成状态，不能解释成当店无餐可点。详情选项变化后，只有名称/规格和上下文仍吻合的补充才可复用。

默认优先真实quantity>0的选择；没有实际数量时，只有唯一默认且数量明确才回退isDefault。所有轮次必须完整、数量满足min/max；模糊默认、错误选项和缺失详情进catalog_status.blocked，不猜选项。组件数量重复计入；根商品与组件的实际物理组成相同，才按同一份食物对比。diffPrice只是选项差价，不用于算最终价格。

expand最多枚举2000个明确单选组合，默认每根保留8个：实际默认项＋更贴合当前目标的候选。营养未知不当作零，也不因为未知而获得更好的匹配分。catalog_status.truncated明示未保留全部变体；可缩小替换范围或--max-variants调整（1–20），总快照上限250项。价格仍由calculate-price返回，组装阶段不以菜单展示价猜最终应付。

## 未知营养的闭环

snapshot.nutrition_warnings是当前保留变体的缺口，catalog_status.unresolved_detail_options另列已见到但可能未入选的未知组件。缺失一项不应抹掉已知下限，也不能只命中汉堡就称整套已知。

如果合格候选太少，优先查可能合适的未知配餐的中国官方营养页面，而不是直接宣布无解。使用nutrition_report.py supplement补入同一快照后，再运行：

```powershell
python scripts/catalog_builder.py --output reranked-snapshot.local.json build --selection selection.local.json --menu menu-response.local.json --nutrition nutrition-response.local.json --cache details.local.json --target meal-target.local.json --enriched enriched-snapshot.local.json
```

只复用同上下文、同名称和规格、保留来源证据的网页补充；新MCP明确值优先。特制不套用标准营养。完成后重新生成推荐/报价上下文与review，不能复用旧确认标识。

第三方数据必须为每个非空点值附对应区间，完整示例及 estimated_within 判定见 [营养来源补充](nutrition-report.md)。整餐区间完全满足所有目标时也能入选，不能因为nutrition.kcal为null就断定不能推荐；区间跨目标边界或其他受约束营养未知时仍不能确认符合。

## 券专属商品

query-store-coupons可能返回普通菜单中没有的专属商品码。不能把券码硬挂到名字相似的普通菜单码上：先把券专属productCode加入selection，附上 `"coupon":{"coupon_id":"实际ID","coupon_code":"实际券码"}`，查询其详情，再build时传 --coupons normalized-coupons.local.json。

builder校验券来自同店/同方式、ID/code与商品码明确对应且在取餐有效期内；根商品可因此不在普通菜单里。券套餐全部组成照常统计，买赠也计入营养。真实组成变化列为替换；仅同一份物理食物才是等价省钱。一单只构造一次券核销，不因为同ID有多个code就一起叠加；同ID不同code可分别核价。

## 直接衔接review和最终汇总

每条规范化quote已自动带kind/store_code/context，无需手工合并外层。实际执行后生成完整checks：

```powershell
python scripts/mcp_adapter.py --output checks.local.json review-checks --quotes quotes.local.json --bundles-checked --coupons-checked --coupon-pages-complete yes
```

只有实际查过才能加checked标志；quote_failure_count、coupon_unresolved_count由报价自动带入。分页不完整用no。达到枚举上限要明确已报价范围。

review的四种status描述省钱结果。另看nutrition_recommendations：同价或预算内更贴合已录目标的方案，即使没更便宜也会列出；同价标明0元价差，不说省钱。baseline营养未知时，nutrition_recommendation_status=target_fit_alternative_available只说明参考替代方案符合目标，不能证明比原单热量或脂肪少。decision.filler_cost_trap表示加东西后应付反而更多；券面值/过期不是净收益。最终还应运行nutrition_report report展示整餐数据及当前训练模板评价。


## 排除菜单根商品（0.6.2）

“不要三件套”可把已识别的菜单根编码传给recommend/review的--exclude，例如`9900005462 9900005466`；无需逐个枚举带~的内部选项。完整~变体ID仅排除该选项；单品的真实商品编码也会匹配组件，排除包含它的套餐。不能用不带边界的任意数字前缀匹配。
根商品nutrition={}由components聚合，单杯包装也可能如此；使用aggregate/recommend或最终nutrition_report读取整餐值，别直接索引根项kcal。
