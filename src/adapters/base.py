"""ERP 适配器抽象基类."""

from abc import ABC, abstractmethod


class ERPAdapter(ABC):
    """ERP 成本数据适配器 — 所有 ERP 对接实现必须继承此类.

    每个具体实现负责从不同的 ERP 系统获取成本数据，
    返回统一格式的 dict 列表。
    """

    @abstractmethod
    def fetch_costs(self, period: str) -> list[dict]:
        """获取指定期间的成本数据.

        Args:
            period: 财务期间，格式 "YYYY-MM"

        Returns:
            [{"sku_name": str, "cost_per_unit": float,
              "gift_cost_pct": float, "warehouse_cost_per_order": float,
              "labor_pct": float, "tax_rate": float}, ...]
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """适配器名称."""
        ...
