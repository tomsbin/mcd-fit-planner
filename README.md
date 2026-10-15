# 麦当劳饮食计划助手 · mcd-fit-planner

**把训练与饮食计划录入一次，之后直接问：今天吃哪份麦当劳套餐？**

基于麦当劳中国官方 MCP 的 Agent Skill。默认优先比较套餐，同时看实际应付、热量、蛋白质、碳水和脂肪；支持临时休息顺延，以及长期保存在本机的月度、年度消费账本。

当前版本 **0.7.0**。适合已有饮食目标、希望减少每天重复选择和记录的人。兼容 WorkBuddy，以及支持 MCP 和本地 Python 技能的 Agent 宿主。

## 能做什么

| 场景 | 行为 |
|---|---|
| 预先安排训练日、高碳日和休息日 | 循环长度自定义，每种日类型保存每日和早中晚目标、预算 |
| 临时休息一天 | 暂停循环并顺延后续安排；计划内休息正常推进 |
| 点汉堡、薯条和饮料 | 从当店菜单自动发现相关套餐，核对真实组件，再查询应付总价 |
| 已指定主食或份数 | 保留要求；替换主食、改变份量须展示差异并取得确认 |
| 换更省或更适合目标的搭配 | 同时展示价格、热量及三大营养素变化；同价改善也可推荐 |
| 官方营养缺失 | Agent 主动查对应中国规格的公开来源，保留证据或估算区间；未知不当零 |
| 薯条只吃半份 | 按实际摄入记录，订单购买份数与食用份数分开 |
| 查询吃麦支出 | 合并本地账单，区分待支付、已支付和退款，支持月年统计及 CSV |

套餐发现默认覆盖相关商品与限额内超值搭配。缺少相关套餐详情或可行套餐未核价时，不能进入下单预览；不能只勾“已检查”就放行。零元应付先列为待核实，不自动当成免费订单。

## 一分钟离线试玩

Python **3.11+**，仅使用标准库，不需要 Token 或安装依赖。

```bash
git clone https://github.com/tomsbin/mcd-fit-planner.git
cd mcd-fit-planner
python -X utf8 demo.py
```

演示使用合成餐品、价格、营养与目标，数据库在独立临时目录中，结束后清理；不会读取账户或产生真实订单。

## 安装到 WorkBuddy

1. 在 [Releases](https://github.com/tomsbin/mcd-fit-planner/releases) 下载 `mcd-fit-planner-workbuddy.zip`。
2. WorkBuddy「专家·技能·连接器」→「技能」→「添加技能」→「上传技能」，导入并启用。
3. 在 [麦当劳 MCP 平台](https://open.mcd.cn/mcp) 取得自己的 Token，在本机连接器接入 `https://mcp.mcd.cn`。
4. 先查询营养和菜单验证连接，再录入自己的已有计划、默认门店与预算。

[配置示例](mcp-config.example.json)只包含环境变量占位符 `${MCD_MCP_TOKEN}`。由支持变量展开的宿主在本机读取；宿主不支持时，在其本机连接器界面填写。不要提交包含真实 Token 的配置。

其他宿主可使用通用包 `mcd-fit-planner-skill.zip`，或直接读取 [SKILL.md](skill/mcd-fit-planner/SKILL.md)。详细步骤见 [WorkBuddy 安装说明](skill/mcd-fit-planner/references/workbuddy-setup.md)。

## 使用示例

以下是功能示例，不是个人饮食处方；实际目标由使用者已有计划提供。

> 保存我的训练 A、高碳训练 B、休息、训练 C 循环；每天和各餐按我提供的目标与预算执行。

> 推荐今天午餐，保留指定汉堡，优先比较套餐。这次先不下单。

> 今天临时休息，后续训练顺延。

> 这个搭配换饮料能省多少？热量、蛋白质、碳水和脂肪怎么变？

> 薯条只吃了一半，记录实际摄入。这个月和今年吃麦当劳花了多少？

整餐报告列出营养点值、估算区间或已知下限，注明来源；对照已录入的当日训练模板，说明符合、超出或不确定。成年男女群体参考会注明年龄、活动水平和标准版本，不替代个人目标。

账本保存在**技能目录之外的固定本机目录**，每次操作统一传 `--db`。订单未付款不记为已支出，生成订单不记为已经食用；近期订单接口不代表完整年度历史。

## 工作流程

```mermaid
flowchart LR
    A[读取当日计划] --> B[菜单与套餐发现]
    B --> C[详情组件与营养]
    C --> D[官方计价与优惠比较]
    D --> E[整餐报告与预览]
    E --> F[用户确认后重新计价]
    F --> G[单次创建订单]
    G --> H[用户付款与本地账本]
```

本地 Python 负责计算与校验，官方 MCP 调用由宿主 Agent 完成。付款由用户在官方渠道完成。完整工具和字段流程见 [MCP_INTEGRATION.md](MCP_INTEGRATION.md)。

## 验证与构建

```bash
python -X utf8 -m unittest discover -s tests
python -X utf8 build.py
```

当前 **206 项离线测试**覆盖循环顺延、营养区间、套餐自动发现及优先级、排除套餐变体、用券参数、零元报价隔离、预览确认、账本和导出。测试中的优惠价格与券码为合成数据；公开食品捕获样例不含账户信息，不能代表当前库存或价格。

已使用官方 MCP 完成真实营养、门店菜单、详情和计价查询。真实付款、券核销，以及外送、团餐和预约的线上交易未作为完成验证；相关参数链路有离线测试。价格结论只覆盖实际查询的候选，不保证全菜单、全渠道最低；小程序可见活动可能与 MCP 不同。

构建输出：

- `dist/mcd-fit-planner-workbuddy.zip`：WorkBuddy 导入包。
- `dist/mcd-fit-planner-skill.zip`：通用技能包。

## 文档与许可

- [本地数据和命令](skill/mcd-fit-planner/references/local-data.md)
- [菜单、套餐与组件](skill/mcd-fit-planner/references/catalog-builder.md)
- [营养来源与报告](skill/mcd-fit-planner/references/nutrition-report.md)
- [MCP 编排及订单确认](skill/mcd-fit-planner/references/mcp-flow.md)

本项目采用 [MIT 许可](LICENSE)。优惠门槛解析改编自 MIT 授权的 `mcd-coupon-strategist`，来源、修改说明及完整许可见 [第三方声明](skill/mcd-fit-planner/references/third-party-notices.md)。

本项目为非官方参赛作品，与麦当劳不存在官方产品或背书关系。规则见 [官方活动仓库](https://github.com/M-China/mcd-developer-innovation-challenge)，参赛声明使用官方原文。
