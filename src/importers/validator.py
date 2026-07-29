"""数据完整性校验工具."""

from datetime import datetime


def validate_imported_data(conn) -> dict:
    """检查已导入数据的完整性和异常.

    Returns:
        {
            "total_orders": int,
            "total_ad_rows": int,
            "anomalies": [
                {"type": "refund_gt_gmv", "order_id": str, "gmv": float, "refund": float},
                ...
            ],
            "missing_data": [str],   # 人类可读的缺失项描述
            "is_ready": bool,        # 数据是否足够开始算账
        }
    """
    anomalies: list[dict] = []
    missing: list[str] = []

    # 检查订单数据
    total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    if total_orders == 0:
        missing.append("尚未导入任何抖音订单数据")
    else:
        # 异常：退款 > GMV
        bad_rows = conn.execute("""
            SELECT id, order_id, gmv, refund_amount FROM orders
            WHERE refund_amount > gmv AND gmv > 0
        """).fetchall()
        for r in bad_rows:
            anomalies.append({
                "type": "refund_gt_gmv",
                "order_id": r["order_id"],
                "gmv": r["gmv"],
                "refund": r["refund_amount"],
            })

        # 检查是否有订单缺少 sku_name
        empty_sku = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE sku_name = '' OR sku_name IS NULL"
        ).fetchone()[0]
        if empty_sku > 0:
            missing.append(f"{empty_sku} 条订单缺少商品名称")

        # 检查是否有订单缺少确认收货日期
        empty_date = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE settle_date = '' OR settle_date IS NULL"
        ).fetchone()[0]
        if empty_date > 0:
            missing.append(f"{empty_date} 条订单缺少确认收货日期，汇总时将被归入「未知」期间")

    # 检查投放数据
    total_ad = conn.execute("SELECT COUNT(*) FROM ad_spend").fetchone()[0]
    if total_ad == 0:
        missing.append("尚未导入千川投放数据")

    # 检查成本配置
    skus_in_orders = set(
        r[0] for r in conn.execute(
            "SELECT DISTINCT sku_name FROM orders WHERE sku_name != ''"
        ).fetchall()
    )
    skus_in_costs = set(
        r[0] for r in conn.execute(
            "SELECT sku_name FROM cost_config"
        ).fetchall()
    )
    unconfigured = skus_in_orders - skus_in_costs
    if unconfigured:
        names = "、".join(sorted(unconfigured)[:5])
        suffix = "等" if len(unconfigured) > 5 else ""
        missing.append(f"以下商品无成本配置，将使用默认值: {names}{suffix}")

    is_ready = total_orders > 0 and total_ad > 0

    return {
        "total_orders": total_orders,
        "total_ad_rows": total_ad,
        "anomalies": anomalies,
        "missing_data": missing,
        "is_ready": is_ready,
    }
