# 官方 MCP 与本地计算桥接

端点 https://mcp.mcd.cn ，Streamable HTTP，通过宿主连接器保存认证信息。以服务器实时返回的 tools/list、inputSchema 和实际 response 为准，本文不假定官方 JSON 字段。不要手写未经验证的请求参数，也不要索要或打印明文 Token。

官方能力来源：https://github.com/M-China/mcd-mcp-server/blob/main/README.md

0.6.0推荐入口优先用 [catalog-builder.md](catalog-builder.md) 自动组装根商品详情与套餐组件；以下menu转换命令保留兼容明确的旧selection。根商品详情工具的编码参数为code；价格/下单商品参数为productCode，不能混用。

官方响应解析：营养data可为紧凑TOON，菜单data.meals为商品编码映射，calculate-price的data.price为整数分。订单为空时data={}按空结果处理；非空订单realTotalAmount按元精确转分。实际字段与成功状态均以当次schema/返回核对。

## 原始返回转换（优先使用脚本）

所有命令从技能目录执行，输出到本地 data/；先创建该目录。原始返回可以是官方 JSON 包装，或宿主保存的唯一 JSON text content。只处理 success=true/code=200，无法识别的 schema 报错，不补造字段。

```powershell
python scripts/mcp_adapter.py --output data/nutrition.local.json nutrition --response data/nutrition-response.local.json
python scripts/mcp_adapter.py --output data/snapshot.local.json menu --menu-response data/menu-response.local.json --nutrition-response data/nutrition-response.local.json --selection data/selection.local.json
python scripts/fit_core.py --output data/candidates.local.json recommend --snapshot data/snapshot.local.json --meal lunch
python scripts/mcp_adapter.py --output data/requests.local.json price-requests --snapshot data/snapshot.local.json --recommendation data/candidates.local.json
# 调用 requests 中的 calculate-price 后，按 request_id 保存原始响应映射（不同券不能共用餐品key）
python scripts/mcp_adapter.py --output data/quotes.local.json quotes --requests data/requests.local.json --responses data/price-responses.local.json
python scripts/mcp_adapter.py --output data/bills.local.json orders --response data/orders-response.local.json --account-alias my-account
python scripts/fit_core.py import-bills --file data/bills.local.json
```

selection 只填写已选规格、分类与官方参数；脚本读取原始数值和名称。示例结构（代号均为占位，不能拿来真实调用）：

```json
{"day":"2026-10-14","meal":"lunch","context":"自取","parameters":{"storeCode":"用户门店","orderType":1,"beType":1},"items":[{"product_code":"实际编码","category":"main","nutrition_name":"营养表名称","serving":"已核实的规格"}]}
```

名称按NFKC全半角/括号/空格归一后匹配，不删除无糖、口味等语义。**selection 每项 serving 必填**，优先由详情确定；nutritionDescription 大多为null，不要把null写成字符串或依赖它提供份量。相同名称/描述且全部营养数值一致的重复行去重，值不同的同名行仍要求明确规格或补充来源。

例如菜单“薯条”在详情选中中份时可用 `"spec":{"size":"medium"},"spec_evidence":{"source":"query-meal-detail","option_code":"实际详情选中编码"},"serving":"中份薯条，标准单份"`；“麦乐鸡”可用 spec.pieces=5。size 为 small/medium/large/mini。营养别名与显式规格冲突会标 spec_conflict，不自动套用。可提供经核实的 nutrition_name 别名；nutrition_description 只有真实非空且需要消歧时才填写。snapshot.nutrition_warnings 明示未匹配项及下一步，不静默当零。

套餐需核对详情选项，提供 options.roundList 与 components。单品 options.modification 会使其营养未知，因为不能定量推断特制影响。当前转换器要求每个选项 product_code 出现在 data.meals，套餐内仅在详情可见的组件需按详情补入内部快照并保留来源；不能用不存在的菜单编码伪造营养。

price-responses 是 `{"request_id": <完整官方算价响应>}`；无优惠变体的原参数 request_id 与候选key相同，旧文件兼容。每个优惠变体有独立 request_id，但共用物理餐品 key。适配器保留 calculate_payload 和哈希，按同一key选择有效的最低 data.price；无效/缺失/过期响应记 quote_failures，不让一张券失败丢掉其他方案。不会默认构造完整 order_payload，因为 takeWayCode/配送地址需要核实。下单前按实时schema补全，preview 校验 items、券、配餐、门店和计价场景不变，并核对实际物理餐品与营养候选。不得擅自随单购会员卡。

订单转换只自动处理文档可核实的已完成与待支付状态；取消/退款或未知状态放入 unresolved，需查询详情核实，不猜付款或退款金额。导入事务中任一合法性错误会整体回滚。

## 推荐

1. 读取用户默认门店与方式；未配置时选一次门店。城市关键词检索用 query-nearby-stores 的 searchType=2 + city + keyword；默认收藏查询可能返回收藏为空，不等于附近没有门店。`query-meals` 查询可售项，`query-meal-detail` 查套餐具体组成及可替换规格，`list-nutrition-foods` 查营养。
2. 只从上述返回值转换以下标准化快照。此格式是本项目内部格式，不是官方原始 response。保存实际匹配证据；同一套餐的每一种明确组成作为独立选项。套餐营养由具体组件相加，不从套餐标题推断。缺任一组件的某营养字段则对应合计 null。不要把中国大陆餐品与其他地区营养表混合。
3. `recommend --snapshot snapshot.local.json --meal lunch` 输出 `context_id` 和 `quote_requests`；按其 item IDs 找回官方餐品参数并映射当前 schema，保留取餐方式、规格、特制项、优惠券与预约设置。
4. `query-store-coupons` 获取当店券，用适配器 coupons 转换（--parameters 指向与实际券查询一致的参数JSON）。随后 price-requests --coupons coupons.local.json；默认最多12个营养候选、每候选4种报价、总调用24次，以总上限为准。券绑定门店/方式/预约时间，查有效期和适用商品，每变体只试一张券；两份商品仅一份用一次券，另一份保留正常计价。不从券名推门槛、叠加或折扣；最终由官方calculate-price核实。coupon_comparison.truncated 表示达到限额，unresolved 是条件未明的券。明确范围，必要时缩小候选增加优惠覆盖。超时/429不无限重试；领券仍需用户指令。
5. 回填以下 quotes，第二次执行 `recommend --quotes quotes.local.json ...`。官方报价 15 分钟有效，需当天同门店/方式与同一 context_id；价格必须取实际应付总额，包含配送费等。不要用历史菜单价代替实价。模板/快照改变导致 context_id 改变，应重新计价。

```json
{
  "kind": "official", "day": "2026-10-14", "meal": "lunch", "store_code": "实际门店", "context": "实际取餐方式",
  "parameters": {"storeCode": "实际门店", "orderType": 1, "beType": 1},
  "items": [
    {"id":"本地明确规格ID","name":"官方餐品名及规格","category":"main","available":true,"nutrition_source":"official",
     "evidence":{"nutrition_name":"营养表匹配名称","serving":"明确每份规格"},
     "nutrition":{"kcal":null,"protein_g":null,"carb_g":null,"fat_g":null},
     "mcp_item":{"productCode":"真实商品编码","quantity":1}}
  ]
}
```

以上是字段结构说明，不是可直接调用的真实快照。真实流程必须优先用 `mcp_adapter menu` 转换，自动带入 `meal`、`parameters` 与每项 `mcp_item`；不要按示例手写数值。单纯本地营养比较可以不带网络参数，但进入 price-requests 必须有这两组官方参数。

category 支持 main / side / drink / bundle。bundle 需要 components 数组（选定组成的本地单品规格 IDs），引擎按该数组重新计算营养，不信任填写的套餐总量。同一单品两份时 ID 重复列两次，不支持嵌套套餐。仅作为套餐组件、不单独可售的单品也放入 items 并设 available=false。不同配餐版本使用不同 ID。kcal 与 energy_kj 二选一，只有 kJ 时引擎除以 4.184。kcal 优先；`energyKcal=0` 目前按显式零值保留，只有 null 才从 kJ 换算。如果 kJ 为正而 kcal 为零，须核实来源语义，不把零直接当作“缺失”并偷偷换值。不确定的名称匹配保留 null。候选覆盖单主食、主食配小食/饮料、明确套餐；第一版不穷举多个汉堡或多个套餐。

推荐第二阶段返回 `closest`（最贴合目标）、`cheapest`（已报价合格候选中最便宜）与 `choices`（展示列表，每项带 labels）。无合格报价时 closest/cheapest 都为 null；二者可能引用同一个 key。Agent 按字段和 labels 识别方案，不按数组顺序猜。`recommendation_status` 区分 no_feasible_priced_option / single_option_only / alternatives_ready；只有一个原单候选不能代表套餐优化。

```json
{
  "kind":"official","context_id":"第一阶段返回值","store_code":"实际门店","context":"实际取餐方式",
  "quotes":[{"key":"第一阶段候选key","total_cents":3350,"quoted_at":"2026-10-14T12:00:00+08:00","order_payload":{"按当前官方create-order schema构造":"真实参数"}}]
}
```

同一候选只填写实际可用的最低报价，order_payload必须对应这次计价的餐品和券，不包含凭证。仅推荐时可暂不构造order_payload，进入预览前使用prepare-order完整复制参数。私人地址、手机号和付款链接只保留必要的本机临时参数，不写入演示数据或公开项目。

### 自动传递用券参数（0.6.1）

0.7.0：点一餐默认先做套餐发现与核价。requests传--menu；即使Agent仅选了单品，build也会标出相关套餐的缺失详情。review/preview按真实覆盖情况检查，旧官方快照缺少bundle_comparison需重新构建。全额应付0元统一进quote_failures，reason=zero_price_requires_verification；不能当最便宜免费餐，也不能进prepare-order/review/preview。核查真实券、可售及完整选配后另行处理，不以补字段猜测放行。本地pending账单0元支出、营养kcal=0均正常保留。

`fulfillment.local.json` 到店示例为 `{"takeWayCode":"从本次takeWayList选择的实际code"}`，外送为 `{"addressId":"用户选定的实际地址ID","remark":"用户备注"}`。只允许这三个字段；不能借此改券、加会员卡、改店或改单品。外送地址只作临时参数，不存入账本。

takeWayCode 类型为字符串，实际捕获包括 `eat-in`（堂食）、`locker-out`（外带取餐柜）；每次仍从本次报价takeWayList选择，不把文档例子当作始终可用的选项。套餐根项的营养字典可能为空，应通过组件聚合读取，不能直接索引根项nutrition.kcal。普通无糖可乐的官方kcal和kJ均可为0，不能当作缺失覆盖。

```powershell
python scripts/mcp_adapter.py --output prepared-quotes.local.json prepare-order --quotes quotes.local.json --key <候选key> --fulfillment fulfillment.local.json
```

将 prepared-quotes.local.json 传给 preview。用户确认后用相同计价参数重新 calculate-price→quotes→prepare-order，再传给 authorize。优惠拒绝/超时或新应付不同，不降级成原价自动下单。该命令仅准备参数，不代表已核销或已下单。完整流程用合成券离线验证，无需用户先持有券；模拟券码不得发送到真实MCP。

`promotion_hint.balance_cents` 为距门槛的差额（分）；`potential_discount_cents` 来自 enjoyable.realDiscount 的元转分，未知则null，`applied=false`。本单总应付只使用 data.price；满减提示不从总价再次扣除。凑单后的总价、增加食品和整餐营养仍须重新比较。该字段解析改编自参考项目，完整MIT许可随技能分发。

## 替换与确认下单

`compare --snapshot ... --before before.local.json --after after.local.json`，before/after 格式为 `{"items":[{"id":"明确规格ID","quantity":1}],"total_cents":3350}`。只在金额来自同上下文真实报价时展示价差；否则不填写 total_cents。

下单流程：

必须先审阅原单与套餐/优惠：

```powershell
python scripts/fit_core.py --output data/review.local.json review --snapshot data/snapshot.local.json --baseline data/baseline.local.json --alternatives data/alternatives.local.json --checks data/checks.local.json --target data/target.local.json --must-keep <必须保留的单品ID>
```

baseline 是 `{"label":"原单","items":[{"id":"本地规格ID","quantity":1}],"quote":{"key":"由候选返回的key","kind":"official","store_code":"门店编码","context":"与快照一致","total_cents":4790,"quoted_at":"带时区的实际报价时间"}}`；alternatives 是同格式数组。quote直接取规范化报价项，不能重新填价格。没有可售相关套餐/没有券也是已检查；接口未调用或失败不是已检查。target 必须是当前 Store.target 的 target 对象；没有配置个人目标时为 {}，仅比较营养与价格。完整checks生成方式如下。

新版quote直接取规范化quotes.quotes中的一项，每行已包含kind/store_code/context，不再手工合并上下文。推荐用 `mcp_adapter.py review-checks --quotes ... --bundles-checked --coupons-checked --coupon-pages-complete yes` 生成完整文件。完整示例：`{"bundles_checked":true,"coupons_checked":true,"quote_failure_count":0,"coupon_unresolved_count":0,"coupon_pages_complete":true,"coupon_variants_truncated":false}`；只能根据实际执行设置。返回no_better_found是没有更省方案，还要看nutrition_recommendations是否有同价的目标改善。

query-nearby-stores的门店记录字段是storeCode/storeName（不是code），例如 `{"storeCode":"实际返回编码","storeName":"实际返回门店名"}`；到店自取不补造beCode。不要把这个code字段约定误套到query-meal-detail的请求参数。

review 按当前需求加 --budget-cents、--exact-contents、--exclude，禁止用“比原单便宜”替代“符合预算/不改餐品”。checks 增加 quote_failure_count（quotes.quote_failures长度）、coupon_unresolved_count（coupon_comparison.unresolved长度）、coupon_pages_complete（实际需要分页的持有券查询是否查完）；非零失败/未核实或分页false返回 incomplete。限额只试部分券时，报告写明未覆盖的数量和范围，不把 comparison_complete=false 说成全部查完。必须保持需要人类确认的替换说明。

0.6.2：`--exclude 9900005462 9900005466` 按菜单根商品排除全部变体；`--exclude 9900005462~具体摘要` 仅排除该选项。仅在 `~` 边界扩展，不按任意数字前缀匹配。实际productCode可映射自定义内部ID；单品/组件编码继续传播到包含它的套餐。用户指定组合也必须通过排除检查；review中的原单若含排除项只作比较基准，不进入可下单核价列表。

用户指定数量可在快照增加 `"requested_baskets":[{"items":[{"id":"用户指定单品ID","quantity":2}]}],"required_quantities":{"用户指定单品ID":2}`；前者补充候选，后者保证不因预算或营养冲突悄悄减量。每个候选代表正在评价的一餐；多人合单的总营养不能当作一个人的实际摄入。

套餐内组件也放入快照，按具体规格 ID 引用并设 available=false；从详情核实组成后求和。review 输出 removed / added、price_delta_cents、nutrition_delta，营养缺失的差值是 null。保留原样、需要改餐品、没有更省方案、尚未完成比较是四种不同状态。不能因失败就声称没有推荐。按已核价范围判断，不穷举或宣称全局最低价。

营养表未列出的单品/规格应主动联网补充，详见 [nutrition-report.md](nutrition-report.md)。补充后重建推荐/报价上下文与 review。最终选择附上整餐 kcal 与三大营养素、来源和完整性、成年男女对照占比以及当前保存的训练模板评价；报告不能替代原单确认，也不能将建议餐写为已吃。

```powershell
python scripts/fit_core.py preview --snapshot snapshot.local.json --quotes quotes.local.json --meal lunch --key <推荐候选key> --review data/review.local.json
# 将输出摘要展示给用户，等待明确确认
# 使用同一订单参数重新 calculate-price，将返回值填入 fresh-quotes.local.json
python scripts/fit_core.py authorize --confirmation-id <预览标识> --quotes fresh-quotes.local.json
```

authorize 要求新报价在两分钟内、金额和完整 order_payload 与预览相同、确认标识未用且未过期。成功即消费标识，Agent 使用 create_once_payload 调一次 `create-order`。价格变化必须重新预览确认。代码不会判定用户是否点头，也不直接调用下单接口；Agent 负责确认与核对 MCP 参数/菜品对应关系。

preview 校验比较报告 can_finalize、菜单哈希、营养目标哈希与所选方案的已核价格；没有完成 review 会拒绝。代码无法证明 Agent 确实调用了全部工具，也无法穷举全部优惠，报告的 checks 必须来自实际执行，不能编造。

支付入口使用当次官方返回的payH5Url。它可能需要扫码或唤起官方App，不能承诺在聊天内付款，也不推断其他渠道可支付该MCP订单。付款结果以查询订单核实为准。

支付由用户完成。`query-order` 核对付款后写入账本。create-order 超时可能已成功，先通过订单查询消除不确定性，不自动重试。`order-list` 仅近期历史，逐次用稳定 ID 合并记录，不能承诺一键获取完整一年账单。未确认退款金额不能当作已退款。
