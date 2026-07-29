"""算账引擎指标公式 — 纯函数，无数据库依赖."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ROIMetrics:
    """全链路 ROI 指标结果."""
    period_type: str = ""
    period_value: str = ""
    gmv: float = 0.0
    refund_amount: float = 0.0
    net_gmv: float = 0.0
    platform_fee: float = 0.0
    commission: float = 0.0
    net_revenue: float = 0.0
    product_cost: float = 0.0
    ad_spend: float = 0.0
    shipping_cost: float = 0.0
    gift_cost: float = 0.0
    warehouse_cost: float = 0.0
    labor_cost: float = 0.0
    pre_tax_profit: float = 0.0
    tax: float = 0.0
    net_profit: float = 0.0
    marketing_roi: Optional[float] = None
    real_roi: Optional[float] = None
    net_margin: Optional[float] = None
    refund_rate: Optional[float] = None
    ad_spend_ratio: Optional[float] = None
    sku_breakdown: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转为字典，方便 JSON 序列化."""
        d = {}
        for k, v in self.__dict__.items():
            if v is None:
                d[k] = None
            elif isinstance(v, float):
                d[k] = round(v, 2)
            else:
                d[k] = v
        return d


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    """安全除法，分母为 0 返回 None."""
    if abs(denominator) < 1e-10:
        return None
    return numerator / denominator


def compute_metrics(
    orders: list[dict],
    ad_spends: list[dict],
    cost_configs: dict[str, dict],
    period_type: str = "day",
    period_value: str = "",
    default_cost: dict | None = None,
) -> ROIMetrics:
    """计算全链路 ROI 与净利润。

    Args:
        orders: 订单列表，每项含 sku_name, gmv, refund_amount, platform_fee,
                commission, shipping_fee, insurance
        ad_spends: 投放明细列表，每项含 spend
        cost_configs: {sku_name: cost_dict}，cost_dict 含 cost_per_unit,
                      gift_cost_pct, warehouse_cost_per_order, labor_pct, tax_rate
        period_type: "day" | "week" | "month"
        period_value: 期间标识
        default_cost: 当 SKU 不在 cost_configs 中时使用的默认值

    Returns:
        ROIMetrics 对象，含全部指标
    """
    if default_cost is None:
        default_cost = {}

    result = ROIMetrics(period_type=period_type, period_value=period_value)
    dc = default_cost

    # ── 收入端 ──
    result.gmv = sum(o["gmv"] for o in orders)
    result.refund_amount = sum(o.get("refund_amount", 0) for o in orders)
    result.net_gmv = result.gmv - result.refund_amount
    result.platform_fee = sum(o.get("platform_fee", 0) for o in orders)
    result.commission = sum(o.get("commission", 0) for o in orders)
    result.net_revenue = result.net_gmv - result.platform_fee - result.commission

    # ── 成本端（按 SKU 归集） ──
    result.shipping_cost = sum(
        o.get("shipping_fee", 0) + o.get("insurance", 0) for o in orders
    )
    result.ad_spend = sum(a.get("spend", 0) for a in ad_spends)

    # 按 SKU 分组计算
    sku_orders: dict[str, list[dict]] = {}
    for o in orders:
        sku = o.get("sku_name", "")
        sku_orders.setdefault(sku, []).append(o)

    result.product_cost = 0.0
    result.gift_cost = 0.0
    result.warehouse_cost = 0.0

    for sku, sku_ords in sku_orders.items():
        cfg = cost_configs.get(sku)
        if cfg is None:
            result.warnings.append(f"SKU「{sku}」无成本配置，使用默认值")
            cfg = dc

        order_count = len(sku_ords)
        sku_gmv = sum(o["gmv"] for o in sku_ords)

        result.product_cost += cfg.get("cost_per_unit", 0) * order_count
        result.gift_cost += sku_gmv * cfg.get("gift_cost_pct", 0)
        result.warehouse_cost += cfg.get("warehouse_cost_per_order", 0) * order_count

    # 人工分摊（按净收入比例）
    # 取第一个（或任意一个）SKU 的 labor_pct；若多 SKU 则用加权平均
    if cost_configs:
        avg_labor_pct = sum(c.get("labor_pct", 0) for c in cost_configs.values()) / len(cost_configs)
    else:
        avg_labor_pct = dc.get("labor_pct", 0)
    result.labor_cost = result.net_revenue * avg_labor_pct

    # ── 税前毛利 ──
    result.pre_tax_profit = (
        result.net_revenue
        - result.product_cost
        - result.ad_spend
        - result.shipping_cost
        - result.gift_cost
        - result.warehouse_cost
        - result.labor_cost
    )

    # ── 税费 ──
    if cost_configs:
        avg_tax_rate = sum(c.get("tax_rate", 0) for c in cost_configs.values()) / len(cost_configs)
    else:
        avg_tax_rate = dc.get("tax_rate", 0)
    result.tax = max(0.0, result.pre_tax_profit * avg_tax_rate)  # 亏损不交税
    result.net_profit = result.pre_tax_profit - result.tax

    # ── 比率指标 ──
    total_cost = (
        result.product_cost + result.ad_spend + result.shipping_cost
        + result.gift_cost + result.warehouse_cost + result.labor_cost + result.tax
    )
    non_ad_cost = total_cost - result.ad_spend

    result.marketing_roi = _safe_div(result.net_revenue - non_ad_cost, result.ad_spend)
    result.real_roi = _safe_div(result.net_revenue, total_cost)
    result.net_margin = _safe_div(result.net_profit, result.net_revenue)
    result.refund_rate = _safe_div(result.refund_amount, result.gmv)
    result.ad_spend_ratio = _safe_div(result.ad_spend, result.net_revenue)

    # ── 单品拆解 ──
    result.sku_breakdown = []
    total_gmv = result.gmv
    sku_count = len(sku_orders)

    for sku, sku_ords in sku_orders.items():
        cfg = cost_configs.get(sku, dc)
        sku_gmv = sum(o["gmv"] for o in sku_ords)
        sku_refund = sum(o.get("refund_amount", 0) for o in sku_ords)
        sku_net_gmv = sku_gmv - sku_refund
        sku_platform = sum(o.get("platform_fee", 0) for o in sku_ords)
        sku_comm = sum(o.get("commission", 0) for o in sku_ords)
        sku_net_rev = sku_net_gmv - sku_platform - sku_comm
        sku_prod_cost = cfg.get("cost_per_unit", 0) * len(sku_ords)
        sku_ship = sum(o.get("shipping_fee", 0) + o.get("insurance", 0) for o in sku_ords)
        sku_gift = sku_gmv * cfg.get("gift_cost_pct", 0)
        sku_wh = cfg.get("warehouse_cost_per_order", 0) * len(sku_ords)
        sku_labor = sku_net_rev * cfg.get("labor_pct", 0)
        sku_pre_tax = sku_net_rev - sku_prod_cost - sku_ship - sku_gift - sku_wh - sku_labor
        sku_tax = max(0.0, sku_pre_tax * cfg.get("tax_rate", 0))
        sku_profit = sku_pre_tax - sku_tax

        # 按 GMV 占比分摊投放费用；GMV 为 0 时均摊
        if total_gmv > 0:
            sku_ad_spend = result.ad_spend * (sku_gmv / total_gmv)
        else:
            sku_ad_spend = result.ad_spend / sku_count if sku_count else 0.0
        sku_profit_with_ad = sku_profit - sku_ad_spend

        sku_margin = _safe_div(sku_profit_with_ad, sku_net_rev)

        result.sku_breakdown.append({
            "sku_name": sku,
            "order_count": len(sku_ords),
            "gmv": round(sku_gmv, 2),
            "net_revenue": round(sku_net_rev, 2),
            "product_cost": round(sku_prod_cost, 2),
            "ad_spend": round(sku_ad_spend, 2),
            "net_profit": round(sku_profit_with_ad, 2),
            "net_margin": round(sku_margin, 4) if sku_margin is not None else None,
        })

    return result


def compute_period_summary(
    orders: list[dict],
    ad_spends: list[dict],
    cost_configs: dict[str, dict],
    period_type: str,
    default_cost: dict | None = None,
) -> list[ROIMetrics]:
    """按期间类型汇总，返回每个期间的 ROIMetrics 列表。

    Args:
        orders: 全部订单，每项含 settle_date 字段
        ad_spends: 全部投放，每项含 date 字段
        cost_configs: SKU 成本配置
        period_type: "day" | "week" | "month"
        default_cost: 当 SKU 不在 cost_configs 中时使用的默认值
    """
    from datetime import datetime

    def get_period(date_str: str) -> str:
        if not date_str:
            return "未知"
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if period_type == "day":
            return date_str
        elif period_type == "week":
            iso = dt.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"
        elif period_type == "month":
            return f"{dt.year}-{dt.month:02d}"
        return date_str

    # 按期间分组订单
    order_groups: dict[str, list[dict]] = {}
    for o in orders:
        period = get_period(o.get("settle_date", ""))
        order_groups.setdefault(period, []).append(o)

    # 按期间分组投放
    ad_groups: dict[str, list[dict]] = {}
    for a in ad_spends:
        period = get_period(a.get("date", ""))
        ad_groups.setdefault(period, []).append(a)

    all_periods = sorted(set(list(order_groups.keys()) + list(ad_groups.keys())))

    return [
        compute_metrics(
            orders=order_groups.get(p, []),
            ad_spends=ad_groups.get(p, []),
            cost_configs=cost_configs,
            period_type=period_type,
            period_value=p,
            default_cost=default_cost,
        )
        for p in all_periods
    ]
