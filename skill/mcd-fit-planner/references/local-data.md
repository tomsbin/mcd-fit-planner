# 本地命令与数据

以下命令从技能目录运行。脚本路径与输入文件在其他 cwd 下使用绝对路径。真实库默认 `data/planner.sqlite3`；演示使用独立临时库。

```powershell
python scripts/fit_core.py configure --file profile.local.json
python scripts/fit_core.py plan
python scripts/fit_core.py schedule --days 14
python scripts/fit_core.py override --date 2026-10-15 --mode rest
python scripts/fit_core.py override --date 2026-10-15 --mode normal
python scripts/fit_core.py record-intake --file intake.local.json
python scripts/fit_core.py coverage --complete yes
python scripts/fit_core.py intake-day
python scripts/fit_core.py record-bill --file bill.local.json
python scripts/fit_core.py report --period 2026-10
python scripts/fit_core.py report --period 2026
python scripts/fit_core.py export --csv bills.csv
```

`coverage --complete yes` 表示用户明确确认截至目前的摄入已记录完整，才能用每日上限扣减剩余额度。新增其他餐食后需保持记录完整；用户表示还有未记录的食物时设为 no。日内剩余量是辅助约束，不把剩余的全天最低目标强塞进当前餐。

## Profile

所有目标为用户指定；下列结构里的数字仅为软件测试示例。

```json
{
  "start_date": "2026-10-14",
  "cycle": ["train", "high-carb", "rest"],
  "rest_template": "rest",
  "defaults": {"store_code": "用户选定门店", "context": "用户选定取餐方式", "excluded_ids": [], "required_ids": []},
  "templates": {
    "train": {"daily": {"kcal_max": 2200}, "meals": {"lunch": {"budget_cents": 3500, "target": {"kcal_min": 500, "kcal_max": 750, "protein_g_min": 30, "fat_g_max": 25}}}},
    "high-carb": {"daily": {"kcal_max": 2400}, "meals": {"lunch": {"budget_cents": 3500, "target": {"kcal_min": 600, "kcal_max": 850, "carb_g_min": 80}}}},
    "rest": {"daily": {"kcal_max": 2000}, "meals": {"lunch": {"budget_cents": 3500, "target": {"kcal_min": 450, "kcal_max": 650}}}}
  }
}
```

支持 breakfast / lunch / dinner。营养键为 kcal、protein_g、carb_g、fat_g，加 `_min` / `_max` 指定上下限；模板 `daily` 与 `meals` 使用同一结构。预算和账单金额是整数分，避免金额浮点累积。

Profile 可加 `"nutrition_reference":{"age":35,"activity":"moderate"}` 保存群体参考的年龄和活动水平（此数字仅为示例）。activity 为 light/moderate/heavy，不把“有健身”直接等同于重活动；个人每日/当餐目标仍来自用户已有计划。

全局 `--output` 写 JSON；CSV 只用子命令 `export --csv`，不能同时传全局 JSON 输出。旧 `export --output` 是兼容别名，输出仍为 CSV。持久账本应固定放在技能目录外，每次传 `--db`，不要把安装/重装技能当作账本迁移。

## Intake

```json
{"id":"2026-10-14-lunch-1","day":"2026-10-14","meal":"lunch","venue":"mcdonalds","items":[{"name":"中薯条","quantity":0.5,"source":"official","nutrition":{"kcal":null,"protein_g":null,"carb_g":null,"fat_g":null}}]}
```

nutrition 是一整份的营养，quantity 是实际吃掉份数，支持半份；null 为未知，不能默认为零。zero quantity 不增加摄入。source 可为 official、official_web、estimated 或 manual；manual 不意味着已获官方验证。网页补充保留对应 evidence；估算区间不转成点值，保持未知标记与来源。已记录同 ID 再次写入会覆盖原记录，补一餐必须用新 ID。

`venue` 为 `mcdonalds`／`other`／`unspecified`，不填为 unspecified；月年吃麦营养只计明确 mcdonalds。`assessment_context` 由代码自动保存录入时的模板和目标，不接受外部伪造；同 ID 同日同餐段纠错保留原目标。月年长图、全天确认与历史口径见 [period-recap.md](period-recap.md)。

## Bill

```json
{"id":"账户与订单号的稳定哈希","day":"2026-10-14","status":"paid","paid_cents":3350,"refund_cents":0,"source":"official","items":[{"name":"套餐（实际规格）","quantity":1}]}
```

状态：pending、paid、cancelled、partial_refund、refunded。paid_cents 是原始累计付款，refund_cents 是累计退款，两者不是本次增量。已付款退款必须保持原消费日期与稳定 ID。月年净支出按原消费月份扣减退款，当前版本不是现金流水发生月报表。source 可标 manual 用于补记，账本不自动宣称年度完整。

可选 discount_cents 为实际订单已核实优惠明细；缺少时保持未知，不从菜单标价推算。再次同步旧的 pending/paid 状态不会冲掉已有付款或累计退款。同一 ID 的消费日期和已核实付款金额冲突会报错，不能自动覆盖；当前没有账单纠错命令，录入手工付款前核对金额。

账本 SQLite 有事务与操作日志，不存原始订单号/地址/手机号；本地用户仍可直接读取数据库，当前无加密。数据库在同一设备长期保留。备份时关闭所有调用后复制库，或使用 sqlite backup API；不要只复制活跃 WAL 库的主文件。CSV 支持 Excel 打开。
