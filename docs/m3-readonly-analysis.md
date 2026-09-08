# M3 只读经营分析使用说明

M3 是关账后的分析入口，不是新的财务操作页面。导入、归属、费用确认、关账和正式 Excel 仍在 M2 工作台完成；M3 只读取已经发布的关账副本。

## 浏览器看板

启动一次工作台服务后，浏览器和MCP共用同一套确定性分析契约，无需再单独启动Web分析进程：

```bash
cd /absolute/path/to/lan
python3 -m src.workbench.app
```

打开 `http://127.0.0.1:8765` 后有三个入口：

1. **月份总览**：未关账月份继续结算；已关账月份进入分析。
2. **结算工作台**：完成数据导入、订单归属、费用确认、关账和Excel导出。
3. **经营分析**：查看概览、重点场次、证据、期间比较、版本比较和条件测算。

分析页顶部始终显示月份、明确版本、当前/历史模式、场次数、日期范围、达人构成、关账时间和版本校验时间。历史版本不会自动跳到新版；当前版本在页面读取期间发生重开或换版时，页面丢弃本轮金额并要求用户重新选择。

页首提供“使用 Claude Code / Codex 分析本月”：复制分析指令后，在已接入本项目只读MCP的客户端中粘贴。这里的“本月”是页面选择的业务月份；指令携带当前或历史版本、场次、达人及日期筛选，并要求先核验版本、查询双利润、追查证据和区分事实与假设。“连接说明”提供两个客户端的 `mcp get livecommerce-m3-readonly` 检查命令；它们用于查看配置与连接信息，不是启动命令。已配置服务由客户端按需启动，页面不会自动发送消息或启动客户端。

除页首分析指令外，首屏只展开“经营概览”和“重点场次”。其他区域按需展开：

- “场次与达人”支持精确场次、达人、开播日期和利润口径筛选。
- “场次证据”查看利润科目、SKU直接贡献、分配依据和人工修改；SKU直接贡献不是SKU全成本利润。
- “期间比较”接收两个完整业务范围；“关账版本比较”只比较同月两个明确版本。
- “条件测算”只接受白名单费用或SKU单位成本，显示基准、假设、差额及不变条件，始终为 `persisted=false`。
- “复制分析引用”可把已验证的版本和筛选范围粘贴到Codex或Claude Code继续对话。

页面JavaScript只负责复制和提交反馈；金额、比率、排名、诊断和测算均由后端生成。浏览分析和运行测算不会写入工作台数据库。

## 只读HTTP接口

同一服务提供八项固定接口：`GET /api/analysis/versions`，以及`performance`、`compare-performance`、`evidence`、`compare-versions`、`rules`、`diagnostics`、`scenario`七项JSON POST接口。POST仅接收`application/json`，调用参数不能指定数据库路径、SQL、文件或URL。

分析页面、脚本、页面测算和JSON API都在M2写连接建立前分派，只使用固定路径的`CloseReader`。缺库不会建库，旧结构不会迁移。

## 使用前提

1. 在工作台完成业务月份关账。
2. 先调用 `workbench_list_close_versions`，取得明确的 `{month, version}`。
3. 当前分析使用 `mode=current`。月份重开后，当前分析会返回 `MONTH_REOPENED`；确需查看旧版时明确使用 `mode=history`。

## MCP启动方式

```bash
cd /absolute/path/to/lan
python3 -m src.analysis.mcp_server --db /absolute/path/to/lan/data/workbench.db
```

这是 stdio MCP 进程，应由支持 MCP 的客户端作为本地子进程启动，不是在浏览器中打开的新端口。数据库路径只允许在进程启动时固定；八个工具都没有路径、SQL或URL参数。

## Codex 与 Claude Code 接入

在自己的电脑上用客户端CLI注册 MCP 服务 `livecommerce-m3-readonly`。下面的 `/absolute/path/to/lan` 需要替换为你的实际项目绝对路径；Python 指向安装了项目依赖的虚拟环境。`--db` 替换为工作台实际账本，桌面版可在“数据位置”菜单查看。配置固定项目与账本路径，因此从其他工作目录启动客户端也能找到服务。

Codex：

```bash
codex mcp add livecommerce-m3-readonly \
  --env PYTHONPATH=/absolute/path/to/lan \
  -- /absolute/path/to/lan/.venv/bin/python \
  -m src.analysis.mcp_server \
  --db /absolute/path/to/lan/data/workbench.db
```

Claude Code：

```bash
claude mcp add livecommerce-m3-readonly -s user \
  -e PYTHONPATH=/absolute/path/to/lan \
  -- /absolute/path/to/lan/.venv/bin/python \
  -m src.analysis.mcp_server \
  --db /absolute/path/to/lan/data/workbench.db
```

检查状态：

```bash
codex mcp get livecommerce-m3-readonly
claude mcp get livecommerce-m3-readonly
```

如果客户端在注册前已经打开，请新建Codex任务或重新启动Claude Code，使该会话重新加载工具目录。不要另外在终端手动启动同一个stdio服务；客户端会按需启动它。

## 推荐操作顺序

1. `workbench_list_close_versions`：确认月份状态和有效版本。
2. `workbench_query_performance`：按月、场次或达人看双利润和科目。
3. `workbench_diagnose_performance`：用固定规则找最终亏损、两套利润方向不同和主要金额构成。
4. `workbench_get_session_evidence`：针对具体场次追查利润、SKU直接贡献、分配依据或人工修改。
5. 需要比较时使用 `workbench_compare_performance`；需要核对重开前后时使用 `workbench_compare_close_versions`。
6. 对口径有疑问时使用 `workbench_get_metric_rules`。
7. 用户直接给出费用或SKU单价假设后，使用 `assumption_source=user_provided`；用户明确授权方案探索后，Agent可在结构化`authorization_scope`内选择少量假设并使用`agent_proposed`。结果不写入数据库，也不替代正式Excel。

## Agent主动分析示例

- **“复盘9月”**：Agent自行确认关账版本，查询概览与重点场次，并对关键费用、分配或修改依据下钻后组织复盘，不要求用户逐步批准每次查询。
- **“为什么这场最终亏损？”**：Agent先列双利润与已核算费用，再追查证据；证据不足时可以提出可能解释和核查方向，但必须标为待验证假设。
- **“在S1坑位费增减不超过200元内比较3个方案”**：这是明确的探索性测算授权。Agent可在场次、科目、数量和金额边界内选择少量方案，工具回显`agent_proposed`与完整授权范围。
- **“看看有什么降本空间”**：Agent可以主动查证并提出测算建议；由于没有数值授权范围，只提出候选假设，不直接执行自定数值测算。

条件测算结果中的`assumption_trace`区分用户直接给定和Agent提出的假设，并回显声明的授权范围。系统会校验请求是否落在该范围内，但这个标记不能证明用户真正授权；实际授权仍由客户端会话和角色行为保证。

`agent_proposed`的`authorization_scope`是结构化范围：必填`session_ids`、`assumption_types`、`operations`和`max_assumptions`；费用测算再指定`fee_fields`与增减额上限或目标金额上下界，SKU测算再指定`skus`与单位成本上下界。任一实际假设超出场次、类型、字段/SKU、操作、数量或数值边界时，确定性服务返回`INVALID_ASSUMPTION`，不执行部分测算。

## 八个只读工具

| 工具 | 用途 |
|---|---|
| `workbench_list_close_versions` | 列关账目录，不返回利润 |
| `workbench_query_performance` | 完整筛选范围汇总、排序和排名 |
| `workbench_compare_performance` | 两个明确范围比较及利润科目桥 |
| `workbench_get_session_evidence` | 单场利润、SKU、分配和修改证据 |
| `workbench_compare_close_versions` | 同月两个关账版本差异 |
| `workbench_get_metric_rules` | 指标公式、分母和可比性 |
| `workbench_diagnose_performance` | 固定事实规则的问题定位，不评分 |
| `workbench_simulate_scenario` | 明确费用或SKU单位成本的内存测算 |

## 边界

- 金额、比率、差额、排名和情景结果均由确定性后端计算；Agent可自主安排查询、解释证据、提出待验证假设和比较经营方案。
- SKU只展示关账副本保存的直接贡献，不宣称SKU全成本利润。
- 科目桥说明金额如何变化，不证明投流、达人或其他业务因素具有因果关系。
- 正式 Excel 仍从工作台下载。
- 当前角色文件用于约束回答方式；是否真正隔离客户端中的其他工具，仍取决于所用客户端能否配置工具白名单。只读安全的核心保证是独立MCP进程没有写工具，且SQLite使用 `mode=ro` 与 `query_only`。
