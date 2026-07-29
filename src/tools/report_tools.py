"""MCP 报表工具 — generate_report."""

from datetime import datetime
from pathlib import Path

from src.db import get_db, init_db
from src.engine.calculator import calculate_from_db

VALID_PERIOD_TYPES = {"day", "week", "month"}
REPORTS_DIR = Path(__file__).parent.parent.parent / "data" / "reports"


def register_report_tools(mcp):
    """向 MCP Server 注册报表相关工具."""

    @mcp.tool()
    async def generate_report(
        period_type: str,
        date_start: str,
        date_end: str,
        format: str = "excel",
    ) -> str:
        """生成周期汇总报表（Excel 或 HTML）。

        Args:
            period_type: "day" | "week" | "month"
            date_start: 起始日期 YYYY-MM-DD
            date_end: 结束日期 YYYY-MM-DD
            format: "excel" 或 "html"
        """
        if period_type not in VALID_PERIOD_TYPES:
            return f"无效的 period_type: {period_type}，可选值: {', '.join(sorted(VALID_PERIOD_TYPES))}"

        conn = get_db()
        init_db(conn)
        result = calculate_from_db(conn, period_type=period_type,
                                   date_start=date_start, date_end=date_end)
        conn.close()

        if not result["periods"]:
            return "无数据可生成报表"

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if format == "excel":
            try:
                import pandas as pd
            except ImportError:
                return "生成 Excel 报表需要安装 pandas。请运行: pip install pandas openpyxl"
            rows = []
            for p in result["periods"]:
                rows.append({
                    "期间": p.get("period_value"),
                    "GMV": p.get("gmv"),
                    "退款金额": p.get("refund_amount"),
                    "净GMV": p.get("net_gmv"),
                    "平台扣点": p.get("platform_fee"),
                    "达人佣金": p.get("commission"),
                    "净收入": p.get("net_revenue"),
                    "商品成本": p.get("product_cost"),
                    "投流花费": p.get("ad_spend"),
                    "运费险": p.get("shipping_cost"),
                    "赠品成本": p.get("gift_cost"),
                    "仓储费": p.get("warehouse_cost"),
                    "人工分摊": p.get("labor_cost"),
                    "税前毛利": p.get("pre_tax_profit"),
                    "税费": p.get("tax"),
                    "净利润": p.get("net_profit"),
                    "营销ROI": p.get("marketing_roi"),
                    "真实ROI": p.get("real_roi"),
                    "净利率": p.get("net_margin"),
                    "退货率": p.get("refund_rate"),
                    "投流占比": p.get("ad_spend_ratio"),
                })
            df = pd.DataFrame(rows)
            path = REPORTS_DIR / f"roi_report_{timestamp}.xlsx"
            df.to_excel(path, index=False)
            return f"报表已生成: {path}"

        elif format == "html":
            path = REPORTS_DIR / f"roi_report_{timestamp}.html"
            html = _build_html_report(result)
            path.write_text(html, encoding="utf-8")
            return f"HTML 报表已生成: {path}"

        else:
            return f"不支持的格式: {format}，可选 excel 或 html"


def _build_html_report(result: dict) -> str:
    """构建 HTML 汇总报表."""
    periods = result["periods"]
    if not periods:
        return "<html><body>无数据</body></html>"

    # 构建简单表格
    rows_html = ""
    for p in periods:
        profit_color = "green" if p.get("net_profit", 0) > 0 else "red"
        rows_html += f"""
        <tr>
            <td>{p.get('period_value')}</td>
            <td>¥{p.get('gmv', 0):,.0f}</td>
            <td>¥{p.get('net_revenue', 0):,.0f}</td>
            <td>¥{p.get('ad_spend', 0):,.0f}</td>
            <td style="color:{profit_color}">¥{p.get('net_profit', 0):,.2f}</td>
            <td>{p.get('net_margin'):.1% if p.get('net_margin') is not None else 'N/A'}</td>
            <td>{p.get('marketing_roi'):.2f if p.get('marketing_roi') is not None else 'N/A'}</td>
        </tr>"""

    return f"""
    <html><head><meta charset="utf-8"><title>ROI 汇总报表</title>
    <style>body{{font-family:sans-serif;padding:20px}}
    table{{border-collapse:collapse;width:100%}}
    th,td{{border:1px solid #ddd;padding:8px;text-align:right}}
    th{{background:#4CAF50;color:white}}</style></head>
    <body>
    <h1>直播带货 ROI 汇总报表</h1>
    <p>{result.get('period_type')} · 共 {len(periods)} 个期间</p>
    <table>
    <tr><th>期间</th><th>GMV</th><th>净收入</th><th>投流花费</th><th>净利润</th><th>净利率</th><th>营销ROI</th></tr>
    {rows_html}
    </table>
    </body></html>"""
