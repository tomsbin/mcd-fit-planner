# WorkBuddy、Codex 与 DeepSeek Agent

技能由本地 Python 计算器、SKILL.md 和官方 MCP 组成，不绑定模型厂商。在线点餐需要宿主能读技能文件、执行 Python 3.11+、连接 Streamable HTTP MCP；缺失营养还需要宿主的联网查询能力。月年总结只读本地账本，可直接离线运行，不需要任何模型 API Key。

| 环境 | 安装方式 | 验证范围 |
|---|---|---|
| WorkBuddy | 导入 `mcd-fit-planner-workbuddy.zip`，添加官方 MCP | 官方营养、门店菜单、详情、计价查询已有真实验证 |
| Codex app / CLI / IDE | 将通用技能文件夹放入 `.agents/skills`，配置 HTTP MCP | 本地脚本与长图生成在 Codex 环境验证；新宿主的账户连接需自行只读验收 |
| 使用 DeepSeek 的 Agent 宿主 | 宿主读取 SKILL.md，桥接 MCP，并执行本地脚本 | 模型无关的工具流程与配置说明；尚未做 DeepSeek API 端到端联调 |
| 其他 Agent | 具备上述三个能力即可复用通用包 | 以宿主实际能力与逐项验收为准 |

## Codex

通用 ZIP 解压后应得到 `mcd-fit-planner/SKILL.md`，将整个文件夹放入：

- 个人技能：`$HOME/.agents/skills/mcd-fit-planner/`；Windows 通常是 `%USERPROFILE%\.agents\skills\mcd-fit-planner\`。
- 仅当前仓库：`<项目根目录>/.agents/skills/mcd-fit-planner/`。

可直接从克隆的仓库复制 `skill/mcd-fit-planner`。在 Codex 中查看 `/skills` 或用 `$mcd-fit-planner` 明确调用；若尚未发现，重启宿主。路径与发现方式依据 [OpenAI Docs 的技能文档](https://learn.chatgpt.com/docs/build-skills)。

将以下节合并到个人 `~/.codex/config.toml`，保留原有配置：

```toml
[mcp_servers.mcd]
url = "https://mcp.mcd.cn"
bearer_token_env_var = "MCD_MCP_TOKEN"
```

先在自己的系统／启动环境中设置 `MCD_MCP_TOKEN`，再启动 Codex，使宿主能读取变量。这里只写环境变量名，不写真实 Token。HTTP MCP 与 `bearer_token_env_var` 的含义见 [OpenAI Docs 的 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。仓库另有可复制的 `examples/codex-config.example.toml`。

初次只做只读验收：让 Agent 读取 SKILL.md、运行 `fit_core.py --help`、确认可列出官方工具，再查询食品营养。账本统一使用技能目录外的固定 `--db`，无需为不同模型各建一份。

## DeepSeek

DeepSeek 是模型提供方。需要一个同时支持 DeepSeek 工具调用、MCP 桥接、技能文件和本地 Python 的 Agent 宿主；普通网页聊天框没有这些能力时，不能直接安装或执行本技能。[DeepSeek 官方工具调用文档](https://api-docs.deepseek.com/guides/tool_calls/)说明：模型提出调用请求，具体函数由客户端执行。

宿主接入顺序：

1. 在宿主配置自己的 DeepSeek 模型提供方，不在本技能里填写模型 API Key。
2. 在宿主的 MCP 配置中接入 `https://mcp.mcd.cn`，认证为 `Authorization: Bearer <自己的麦当劳 Token>`。两个服务的凭证分别保存在宿主本机。
3. 将通用技能包加入宿主技能目录；没有技能发现机制时，将 SKILL.md 作为任务说明，并使其能按绝对路径读取脚本和 references。
4. 先读取现有计划、营养和菜单验证工具链，再使用推荐。模型输出函数名不等于函数已执行，订单仍以官方返回为准。

宿主若只支持模型聊天，不提供 MCP 或本地执行，可以单独运行 `period_recap.py` 查看本地总结；实时点餐需补齐宿主能力。
