# 月末／年末吃麦手账

生成自包含网页、1080×1780 分享长图 SVG 和 PNG。网页可筛选餐食结果、查看全部四项营养和每日目标明细，提供下载 SVG／PNG 按钮；无外部字体、CDN 或网络请求。Python 3.11+ 标准库；命令行 PNG 渲染需本机 Edge／Chrome（可用 `MCD_RECAP_BROWSER` 指定浏览器可执行文件）。没有浏览器时仍生成 HTML／SVG，并明确返回 `png_note`；可打开 HTML 下载 PNG。

## 调用

从技能目录运行，路径按实际安装位置替换；`--db` 必须是现有账本，脚本以只读模式打开：

```powershell
python scripts/period_recap.py --db "D:/my-mcd-data/planner.sqlite3" --period 2026-09 --output "D:/my-mcd-data/2026-09.html" --svg "D:/my-mcd-data/2026-09.svg" --png "D:/my-mcd-data/2026-09.png"
python scripts/period_recap.py --db "D:/my-mcd-data/planner.sqlite3" --period 2025 --output "D:/my-mcd-data/2025.html" --png "D:/my-mcd-data/2025.png"
```

`--json <路径>` 可额外输出统计数据，不含账单／摄入 ID、Token、支付链接、地址或手机号。网页仍含消费、日期与个人饮食目标，分享前由用户确认。脚本不上传或发送文件。

不读取真实账本的演示：

```powershell
python scripts/period_recap.py --demo --period 2026-09 --output month.html --svg month.svg --png month.png
python scripts/period_recap.py --demo --period 2025 --output year.html --png year.png
```

全部演示数据为合成，长图与网页明确标注；演示库在临时目录，结束后清理。上面示例年月不限制实际统计范围。

## 统计口径

- 账单统计已付款／已退款订单，净支出为已付减已退，待支付不计；退款沿用原消费月份。常点榜来自已付款且未全额退款的购买份数，部分退款无法推断退掉哪道菜。
- 吃麦营养来自 `venue: "mcdonalds"` 的实际食用记录；`other` 为其他餐食，`unspecified`／旧记录未填来源不擅自算作麦当劳。订单不会自动生成摄入。全部餐食可用于当天目标核对。
- 汇总 kcal、蛋白质、碳水和脂肪；按实际份数累加区间。未知显示已知下限，不当零；零热量饮料保留真实 0。范围或估算均作标记。
- 餐食评估合并同日同餐段全部已记录食物，且至少有一条实际吃麦记录；判断针对“已记录餐食”，不推断未记录的加餐。
- 每条新摄入自动保存录入时的当餐／每日目标与模板。更改计划不追溯改判；同 ID、同日同餐段纠错保留原目标。旧记录缺历史目标不拿今天目标套用；同餐或同日记录目标冲突时单列待核实。
- 达标、估算达标、超出、不足、营养待核实、缺目标与历史不一致分开。一个营养素超出而另一个不足，可同时出现。可核实达标率分母只含有明确结论且非估算的餐食；缺失和估算均不算达标。

## 全天确认

原 `coverage --complete yes` 只代表“截至目前都记了”，用于当天剩余额度，不能拿来判全天达标。用户明确说全天吃喝都记完后，执行：

```powershell
python scripts/fit_core.py --db "D:/my-mcd-data/planner.sqlite3" close-day --date 2026-09-30 --complete yes
```

`--complete no` 撤销。新增、纠错、移动摄入记录会自动撤销受影响日期的全天确认，需用户重新确认。只有已确认且记录指纹一致的日期才能做全天目标评价；未记录日期不计为达标。一天只有一餐且用户未确认完整时不作全天判断。

健身评价对应使用者已经保存的个人目标，不用男性／女性通用参考值替代处方；高碳日检查碳水目标，休息日用自己的休息模板。营养达标不等于训练完成、体脂降低或整体饮食健康达标。

## Agent 美化

默认生成暖米色、黄色、绿色的手账风格，有原创汉堡插画、消费节奏图、常点榜和目标进度环。用户想换风格时，可以让宿主 AI 基于 `--json` 的统计重排 HTML／SVG；保留统计数值、单位、估算标记、缺失说明和分母口径，不让模型编造“省了多少”“连续达标几天”或体重变化。只需要网页长图时，使用代码渲染可保证数字和文字准确；无需调用付费图片模型。
