# 选餐比较页

输入是 `fit_core.py recommend --quotes ...` 的完整 JSON 结果，`phase` 必须为 `recommended`。该结果在本地保存，不把原始报价或含账户字段的 JSON 上传。只导出展示字段，页面不联网、不下单；它不是预览授权或报价校验器。

```powershell
python scripts/meal_report.py --input data/recommend.local.json --output data/meal-report.local.html
```

网页展示当前推荐与最多三份超预算参考。预算内方案包含 `budget_remaining_cents`，超预算参考另列 `over_budget_options` 和 `closest_over_budget`，包含 `over_budget_cents` 与 `requires_budget_confirmation=true`。这些参考满足当前营养、必选、排除等条件，只是不符合预算；它们不会加入 `choices`、`preferred` 或 `cheapest`。没有报价则返回空参考，不能把未核价菜单价当作兜底。

页面可改预算、恢复保存的预算，切换任意两份展示方案比较组成、保持项、整餐价差和四种营养差异，复制选中餐单。复制不支持时可手动选中文字。组成取聚合时的实际组件，避免将套餐外壳重复算成食物。差价不是新增单品的售价。未知营养、估算和上下界保留原有含义，缺失或区间不计算确定差值。

仅筛选本轮已核价结果，不进行全菜单搜索，也不为满足预算降低蛋白质等原目标。预算输入不修改账本；用户同意新预算后回到 Agent 更新预算并重新走推荐、报价和确认。展示范围不完整、计价失败或券信息未核实时按页面提示补查，网页本身不能绕过 `review`。官方报价超过 15 分钟、跨北京时间日期或时间异常标为需重新计价；打开旧文件仍是历史记录，不能直接下单。

公开示例见仓库 `examples/meals/compare.html`，全部为合成餐品、营养、目标和价格；无真实账户信息。设计取舍参考 `mcd-budget-meal` 的预算余量与组成比较，脚本和模板为独立实现，见第三方说明。
