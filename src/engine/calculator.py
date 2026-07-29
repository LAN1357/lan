"""算账引擎编排层 — 从数据库取数，调用 metrics 计算，写回 snapshot."""

import json
from datetime import datetime
from typing import Optional

from .metrics import compute_period_summary, compute_metrics


def calculate_from_db(
    conn,
    period_type: str,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
) -> dict:
    """从数据库取数 → 按期间汇总 → 计算 → 写 snapshot → 返回结果.

    Args:
        conn: sqlite3.Connection
        period_type: "day" | "week" | "month"
        date_start: 起始日期 (YYYY-MM-DD)，不传则无下限
        date_end: 结束日期 (YYYY-MM-DD)，不传则无上限

    Returns:
        {"period_type": str, "periods": [dict], "warnings": [str]}
        其中每个 period dict 是 ROIMetrics.to_dict() 的结果
    """
    # 构建 WHERE 条件
    order_where = "WHERE 1=1"
    ad_where = "WHERE 1=1"
    params_order = []
    params_ad = []

    if date_start:
        order_where += " AND settle_date >= ?"
        ad_where += " AND date >= ?"
        params_order.append(date_start)
        params_ad.append(date_start)
    if date_end:
        order_where += " AND settle_date <= ?"
        ad_where += " AND date <= ?"
        params_order.append(date_end)
        params_ad.append(date_end)

    # 查询订单
    orders = [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM orders {order_where}", params_order
        ).fetchall()
    ]
    # 查询投放
    ad_spends = [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM ad_spend {ad_where}", params_ad
        ).fetchall()
    ]
    # 查询成本配置
    cost_rows = conn.execute("SELECT * FROM cost_config").fetchall()
    cost_configs = {row["sku_name"]: dict(row) for row in cost_rows}

    if not orders and not ad_spends:
        return {
            "period_type": period_type,
            "periods": [],
            "warnings": ["该时间段暂无数据，请先导入订单或投放报表"],
        }

    # 加载默认成本
    default_cost = {
        "cost_per_unit": 0.0,
        "gift_cost_pct": 0.0,
        "warehouse_cost_per_order": 0.0,
        "labor_pct": 0.0,
        "tax_rate": 0.0,
    }

    # 按期间汇总计算
    periods = compute_period_summary(
        orders=orders,
        ad_spends=ad_spends,
        cost_configs=cost_configs,
        period_type=period_type,
    )

    # 写 snapshot（UPSERT）
    now = datetime.now().isoformat()
    for p in periods:
        conn.execute("""
            INSERT INTO calc_snapshots (period_type, period_value, metrics_json, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(period_type, period_value)
            DO UPDATE SET metrics_json=excluded.metrics_json, created_at=excluded.created_at
        """, (p.period_type, p.period_value, json.dumps(p.to_dict(), ensure_ascii=False), now))
    conn.commit()

    all_warnings: list[str] = []
    for p in periods:
        all_warnings.extend(p.warnings)

    return {
        "period_type": period_type,
        "periods": [p.to_dict() for p in periods],
        "warnings": all_warnings,
    }
