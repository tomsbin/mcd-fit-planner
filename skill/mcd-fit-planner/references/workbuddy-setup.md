# WorkBuddy 安装与使用

## 导入技能

下载GitHub Release中的mcd-fit-planner-workbuddy.zip，在WorkBuddy「专家·技能·连接器」→「技能」→「添加技能」→「上传技能」导入并启用。Python需3.11或更新版本，本地脚本只使用标准库。

## 接入官方MCP

在 https://open.mcd.cn/mcp 登录并激活自己的MCP Token。在WorkBuddy「连接器」→「自定义连接器」→「配置MCP」添加官方Streamable HTTP服务，端点 https://mcp.mcd.cn 。配置结构见仓库根目录mcp-config.example.json。

公开示例仅使用环境变量占位符${MCD_MCP_TOKEN}。由支持变量展开的宿主在本机读取；宿主不支持时，在本机连接器界面填入实际值。不要把填过Token的配置提交GitHub或发到对话中。本地Python脚本不保存认证信息。

## 首次使用

先说“使用麦当劳饮食计划助手，检查Python和连接器，查询营养表并解析；这次只查询。”连接成功后，一次录入自己的循环计划、已有营养目标、预算、默认门店和取餐方式。

例如：“按我的训练日目标推荐午餐，保留指定汉堡，优先比较套餐；这次不下单。”缺失营养由Agent主动查询对应中国规格的公开来源，查询不到时说明未知。

账本保存在技能目录之外的固定本机目录，每次命令显式传--db，避免重装后丢失。路径、账户凭证、地址和付款链接不进入公开项目。更新技能后重新读取SKILL.md；不因升级覆盖用户已有MCP设置。

订单必须展示完整菜单/套餐与优惠比较、营养和取餐方式，再取得用户明确确认；确认前重新计价。付款由用户完成，不把本地授权参数当作实际下单成功。

WorkBuddy技能说明：https://open.workbuddy.cn/docs/skill
麦当劳官方接入说明：https://github.com/M-China/mcd-developer-innovation-challenge
