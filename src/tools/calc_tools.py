"""MCP 计算工具 — calculate_roi, query_metrics, compare_periods."""

import json
from src.db import get_db, init_db
from src.engine.calculator import calculate_from_db


def register_calc_tools(mcp):
    """向 MCP Server 注册所有计算相关工具."""

    @mcp.tool()
    async def calculate_roi(
        period_type: str,
        date_start: str = "",
        date_end: str = "",
    ) -> str:
        """执行全链路 ROI 和净利润计算。

        按指定的期间类型（日/周/月）汇总所有已导入数据，
        计算每个期间的 GMV、净收入、各项成本、税前毛利、净利润，
        以及营销 ROI、真实 ROI、净利率、退货率、投流占比等指标。
        结果会缓存到本地，历史期间不会重复计算。

        Args:
            period_type: 汇总粒度，可选 "day"（按日）、"week"（按周）、"month"（按月）
            date_start: 起始日期（含），格式 YYYY-MM-DD，不传则从最早数据开始
            date_end: 结束日期（含），格式 YYYY-MM-DD，不传则到最晚数据结束
        """
        conn = get_db()
        init_db(conn)
        result = calculate_from_db(
            conn,
            period_type=period_type,
            date_start=date_start if date_start else None,
            date_end=date_end if date_end else None,
        )
        conn.close()

        if not result["periods"]:
            return result["warnings"][0] if result["warnings"] else "无数据可计算"

        output = f"共 {len(result['periods'])} 个期间:\n\n"
        for p in result["periods"]:
            output += _format_period_metrics(p)

        if result["warnings"]:
            output += "\n⚠️ 注意事项:\n"
            for w in result["warnings"]:
                output += f"  · {w}\n"

        return output

    @mcp.tool()
    async def query_metrics(
        period_type: str,
        date_start: str = "",
        date_end: str = "",
    ) -> str:
        """查询历史算账结果（从缓存中读取，不重新计算）。

        Args:
            period_type: "day" | "week" | "month"
            date_start: 起始日期 YYYY-MM-DD
            date_end: 结束日期 YYYY-MM-DD
        """
        conn = get_db()
        init_db(conn)

        query = "SELECT * FROM calc_snapshots WHERE period_type = ?"
        params = [period_type]

        if date_start:
            query += " AND period_value >= ?"
            params.append(date_start)
        if date_end:
            query += " AND period_value <= ?"
            params.append(date_end)

        query += " ORDER BY period_value"
        rows = conn.execute(query, params).fetchall()
        conn.close()

        if not rows:
            return f"暂无 {period_type} 粒度的历史计算结果。请先运行 calculate_roi。"

        output = ""
        for r in rows:
            metrics = json.loads(r["metrics_json"])
            output += _format_period_metrics(metrics)

        return output

    @mcp.tool()
    async def compare_periods(
        period_type: str,
        period_a_start: str,
        period_a_end: str,
        period_b_start: str,
        period_b_end: str,
    ) -> str:
        """环比/同比对比两个时间段的 ROI 指标。

        Args:
            period_type: "day" | "week" | "month"
            period_a_start: 当期起始日期 YYYY-MM-DD
            period_a_end: 当期结束日期 YYYY-MM-DD
            period_b_start: 基期起始日期 YYYY-MM-DD
            period_b_end: 基期结束日期 YYYY-MM-DD
        """
        conn = get_db()
        init_db(conn)

        # 分别计算两个期间
        result_a = calculate_from_db(conn, period_type=period_type,
                                     date_start=period_a_start, date_end=period_a_end)
        result_b = calculate_from_db(conn, period_type=period_type,
                                     date_start=period_b_start, date_end=period_b_end)
        conn.close()

        # 汇总每个时期的全部期间
        def summarize_periods(periods: list[dict]) -> dict:
            return {
                "gmv": sum(p.get("gmv", 0) for p in periods),
                "net_revenue": sum(p.get("net_revenue", 0) for p in periods),
                "ad_spend": sum(p.get("ad_spend", 0) for p in periods),
                "net_profit": sum(p.get("net_profit", 0) for p in periods),
                "net_margin": 0.0,
            }

        a = summarize_periods(result_a["periods"])
        b = summarize_periods(result_b["periods"])
        if a["net_revenue"] > 0:
            a["net_margin"] = a["net_profit"] / a["net_revenue"]
        if b["net_revenue"] > 0:
            b["net_margin"] = b["net_profit"] / b["net_revenue"]

        def change(cur, base):
            if base and abs(base) > 0.01:
                return (cur - base) / base
            return None

        output = f"对比: {period_a_start}~{period_a_end} vs {period_b_start}~{period_b_end}\n\n"
        output += f"| 指标 | 当期 | 基期 | 变化 |\n"
        output += f"|------|------|------|------|\n"

        for label, key in [("净收入", "net_revenue"), ("投流花费", "ad_spend"),
                           ("净利润", "net_profit"), ("净利率", "net_margin")]:
            cv = a[key]
            bv = b[key]
            ch = change(cv, bv)
            ch_str = f"{ch:+.1%}" if ch is not None else "N/A"
            if key == "net_margin":
                output += f"| {label} | {cv:.1%} | {bv:.1%} | {ch_str} |\n"
            else:
                output += f"| {label} | ¥{cv:,.0f} | ¥{bv:,.0f} | {ch_str} |\n"

        # 归因分析
        output += "\n## 利润率变动归因\n"
        margin_ch = change(a["net_margin"], b["net_margin"])
        if margin_ch is not None and abs(margin_ch) > 0.01:
            ad_ratio_a = a["ad_spend"] / a["net_revenue"] if a["net_revenue"] > 0 else 0
            ad_ratio_b = b["ad_spend"] / b["net_revenue"] if b["net_revenue"] > 0 else 0
            if ad_ratio_a > ad_ratio_b + 0.02:
                output += f"· 投流占比上升 (当期 {ad_ratio_a:.1%} vs 基期 {ad_ratio_b:.1%})，可能是利润率下降主因\n"
            elif ad_ratio_b > ad_ratio_a + 0.02:
                output += f"· 投流占比下降 (当期 {ad_ratio_a:.1%} vs 基期 {ad_ratio_b:.1%})，投放效率改善\n"
            else:
                output += "· 投流占比基本持平，利润率变化可能来自成本端或退货率变化\n"

        return output


def _format_period_metrics(metrics: dict) -> str:
    """格式化单个期间的指标为可读文本."""
    roi_str = f"{metrics.get('marketing_roi'):.2f}" if metrics.get("marketing_roi") is not None else "N/A"
    real_roi_str = f"{metrics.get('real_roi'):.2f}" if metrics.get("real_roi") is not None else "N/A"
    margin_str = f"{metrics.get('net_margin', 0):.1%}" if metrics.get("net_margin") is not None else "N/A"
    refund_str = f"{metrics.get('refund_rate', 0):.1%}" if metrics.get("refund_rate") is not None else "N/A"

    return (
        f"【{metrics.get('period_value', '')}】\n"
        f"  GMV: ¥{metrics.get('gmv', 0):,.0f}  "
        f"退货率: {refund_str}\n"
        f"  净收入: ¥{metrics.get('net_revenue', 0):,.0f}  "
        f"投流花费: ¥{metrics.get('ad_spend', 0):,.0f}\n"
        f"  净利润: ¥{metrics.get('net_profit', 0):,.2f}  "
        f"净利率: {margin_str}\n"
        f"  营销 ROI: {roi_str}  真实 ROI: {real_roi_str}\n"
        f"  投流占比: {metrics.get('ad_spend_ratio', 0):.1%}\n\n"
    )
