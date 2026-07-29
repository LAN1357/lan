"""MCP 导入工具 — import_orders, import_ad_spend, configure_costs, list_cost_configs, validate_data."""

import json
from src.db import get_db, init_db
from src.importers.orders import import_orders_csv
from src.importers.ad_spend import import_ad_spend_csv
from src.importers.costs import set_cost_config, list_cost_configs
from src.importers.validator import validate_imported_data


def register_import_tools(mcp):
    """向 MCP Server 注册所有导入相关工具."""

    @mcp.tool()
    async def import_orders(file_path: str) -> str:
        """导入抖音订单导出 CSV 文件。

        Args:
            file_path: CSV 文件的完整路径，如 /Users/xxx/Downloads/orders.csv
        """
        conn = get_db()
        init_db(conn)
        result = import_orders_csv(conn, file_path)
        conn.close()

        if result["errors"]:
            error_text = "\n".join(result["errors"])
            return f"导入完成。成功 {result['imported_count']} 条。\n\n警告/错误:\n{error_text}"
        return f"成功导入 {result['imported_count']} 条订单。列名映射: {json.dumps(result['mapping_used'], ensure_ascii=False)}"

    @mcp.tool()
    async def import_ad_spend(file_path: str) -> str:
        """导入千川投放报表 CSV 文件。

        Args:
            file_path: CSV 文件的完整路径
        """
        conn = get_db()
        init_db(conn)
        result = import_ad_spend_csv(conn, file_path)
        conn.close()

        if result["errors"]:
            error_text = "\n".join(result["errors"])
            return f"导入完成。成功 {result['imported_count']} 条。\n\n警告/错误:\n{error_text}"
        return f"成功导入 {result['imported_count']} 条投放记录。列名映射: {json.dumps(result['mapping_used'], ensure_ascii=False)}"

    @mcp.tool()
    async def configure_costs(
        sku_name: str,
        cost_per_unit: float = 0.0,
        gift_cost_pct: float = 0.0,
        warehouse_cost_per_order: float = 0.0,
        labor_pct: float = 0.0,
        tax_rate: float = 0.0,
    ) -> str:
        """设置或更新某个商品的成本配置。

        只需提供需要修改的字段，未提供的字段保持原值不变。

        Args:
            sku_name: 商品名称/SKU
            cost_per_unit: 单位采购/生产成本（元）
            gift_cost_pct: 赠品成本占GMV比例，如 0.03 表示 3%
            warehouse_cost_per_order: 单均仓储费（元）
            labor_pct: 人工分摊占净收入比例，如 0.05 表示 5%
            tax_rate: 综合税率，如 0.13 表示 13%
        """
        conn = get_db()
        init_db(conn)
        kwargs = {
            "cost_per_unit": cost_per_unit,
            "gift_cost_pct": gift_cost_pct,
            "warehouse_cost_per_order": warehouse_cost_per_order,
            "labor_pct": labor_pct,
            "tax_rate": tax_rate,
        }
        result = set_cost_config(conn, sku_name, **kwargs)
        conn.close()

        if result.get("error"):
            return f"配置失败: {result['error']}"
        return f"已更新商品「{sku_name}」的成本配置: {json.dumps(result.get('fields', {}), ensure_ascii=False)}"

    @mcp.tool()
    async def list_cost_configs(sku_name: str = "") -> str:
        """查询当前所有成本配置，或按商品名过滤。

        Args:
            sku_name: 可选，指定商品名进行过滤
        """
        conn = get_db()
        init_db(conn)
        configs = list_cost_configs(conn, sku_name if sku_name else None)
        conn.close()

        if not configs:
            return "暂无成本配置。使用 configure_costs 添加。"
        return json.dumps(configs, ensure_ascii=False, indent=2)

    @mcp.tool()
    async def validate_data() -> str:
        """检查已导入数据的完整性和异常情况。

        返回订单总数、投放记录数、数据异常项、缺失数据项等。
        """
        conn = get_db()
        init_db(conn)
        result = validate_imported_data(conn)
        conn.close()
        return json.dumps(result, ensure_ascii=False, indent=2)
