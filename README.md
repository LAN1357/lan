# 抖音直播带货全链路「真实 ROI 与净利」算账 Agent

品牌方直播带货的算账工具。从抖音/千川导出 CSV -> 自动导入 -> 全链路净利计算 -> Claude Agent 对话解读。

## 快速开始

### 安装

```bash
cd livecommerce-roi-agent
pip install -e ".[dev]"
```

### 启动 MCP Server

```bash
# 方式一：通过 MCP CLI
mcp run src/server.py

# 方式二：直接运行
python src/server.py
```

### 配置 Claude

在 Claude Code 中使用 Agent：

```bash
# Claude 会自动发现 .claude/agents/roi-analyst.md 中定义的 Agent
# 在对话中直接说"帮我分析直播ROI"即可触发
```

## 工作流程

1. **导出数据**：从抖音商家后台导出订单 CSV，从千川后台导出投放报表 CSV
2. **导入数据**：通过 Agent 对话导入两个 CSV 文件
3. **配置成本**：设置每个 SKU 的采购成本、赠品率、仓储费、人工占比、税率
4. **计算 ROI**：选择汇总粒度（日/周/月），Agent 自动计算并解读
5. **导出报表**：生成 Excel 或 HTML 报表

## 数据格式

### 订单 CSV（抖音导出）

必需列：订单号、商品名称、下单金额、退款金额、达人佣金

可选列：平台服务费、运费、运费险、退款状态、支付时间

系统会自动检测列名，支持抖音多种导出格式（详见 `src/importers/column_maps.py`）。

### 投放报表 CSV（千川导出）

必需列：计划名称、花费、日期

可选列：展示数、点击数、转化数

## 成本配置

| 参数 | 说明 | 示例 |
|------|------|------|
| cost_per_unit | 单位采购/生产成本（元） | 25.0 |
| gift_cost_pct | 赠品成本占 GMV 比例 | 0.03（3%） |
| warehouse_cost_per_order | 单均仓储费（元） | 1.5 |
| labor_pct | 人工分摊占净收入比例 | 0.05（5%） |
| tax_rate | 综合税率 | 0.13（13%） |

成本可通过 MCP 工具实时设置，也可通过 `config/default_costs.yaml` 配置默认值。

## 运行测试

```bash
python -m pytest tests/ -v
```

## 目录结构

```
livecommerce-roi-agent/
├── src/
│   ├── server.py              # MCP Server 入口（MCPServer）
│   ├── db.py                  # SQLite 数据库初始化与连接管理
│   ├── models.py              # Pydantic 数据模型
│   ├── engine/                # 算账引擎（纯函数，无数据库依赖）
│   │   ├── metrics.py         # 核心指标计算（ROIMetrics）
│   │   └── calculator.py      # 编排层：取数 -> 计算 -> 写 snapshot
│   ├── importers/             # CSV 导入器 + 成本管理
│   │   ├── orders.py          # 抖音订单 CSV 导入
│   │   ├── ad_spend.py        # 千川投放 CSV 导入
│   │   ├── costs.py           # 成本配置管理
│   │   ├── column_maps.py     # 列名映射（支持多种导出格式）
│   │   └── validator.py       # 数据校验
│   ├── adapters/              # ERP 适配器（预留接口）
│   │   ├── base.py            # 抽象基类
│   │   └── excel_adapter.py   # Excel ERP 成本导入
│   ├── reports/               # 报表生成
│   └── tools/                 # MCP 工具定义
│       ├── import_tools.py    # 导入工具注册
│       ├── calc_tools.py      # 计算工具注册
│       └── report_tools.py    # 报表工具注册
├── tests/                     # 测试
│   ├── fixtures/              # 测试 CSV 数据
│   ├── test_metrics.py        # 指标计算单元测试
│   ├── test_calculator.py     # Calculator 集成测试
│   ├── test_importers.py      # 导入器测试
│   └── test_integration.py    # 端到端集成测试
├── config/                    # 配置文件
├── data/                      # SQLite 数据库存储
├── .claude/agents/            # Claude Agent 定义
│   └── roi-analyst.md
└── pyproject.toml
```
