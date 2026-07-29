"""Pydantic 数据模型 — 与数据库表结构一一对应."""

from typing import Optional

from pydantic import BaseModel


class OrderRow(BaseModel):
    """抖音订单明细."""
    id: Optional[int] = None
    order_id: str
    sku_name: str
    gmv: float
    refund_amount: float = 0.0
    refund_status: str = ""
    platform_fee: float = 0.0
    commission: float = 0.0
    shipping_fee: float = 0.0
    insurance: float = 0.0
    settle_date: str = ""       # YYYY-MM-DD
    live_session_id: str = ""
    imported_at: str = ""       # ISO datetime


class AdSpendRow(BaseModel):
    """千川投放明细."""
    id: Optional[int] = None
    campaign_name: str
    spend: float
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    date: str = ""              # YYYY-MM-DD
    live_session_id: str = ""
    imported_at: str = ""


class CostConfigRow(BaseModel):
    """成本配置."""
    id: Optional[int] = None
    sku_name: str
    cost_per_unit: float = 0.0
    gift_cost_pct: float = 0.0
    warehouse_cost_per_order: float = 0.0
    labor_pct: float = 0.0
    tax_rate: float = 0.0
    effective_date: str = ""    # YYYY-MM-DD


class CalcSnapshot(BaseModel):
    """计算结果缓存."""
    id: Optional[int] = None
    period_type: str            # "day" | "week" | "month"
    period_value: str           # 期间值
    metrics_json: str           # JSON 字符串
    created_at: str = ""
