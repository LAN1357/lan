"""Excel ERP 适配器 — MVP 阶段从 Excel 文件导入成本数据."""

from pathlib import Path

import pandas as pd

from .base import ERPAdapter


class ExcelERPAdapter(ERPAdapter):
    """从 Excel / CSV 文件导入成本数据。

    期望的文件格式：
        sku_name | cost_per_unit | gift_cost_pct | warehouse_cost_per_order | labor_pct | tax_rate
    """

    def __init__(self, file_path: str):
        self._file_path = Path(file_path)

    def name(self) -> str:
        return f"ExcelAdapter({self._file_path.name})"

    def fetch_costs(self, period: str) -> list[dict]:
        """从 Excel 读取成本数据.

        Args:
            period: 财务期间（当前实现未使用，预留参数）

        Returns:
            成本配置列表
        """
        if not self._file_path.exists():
            raise FileNotFoundError(f"成本文件不存在: {self._file_path}")

        if self._file_path.suffix.lower() == ".csv":
            df = pd.read_csv(self._file_path, encoding="utf-8-sig")
        else:
            df = pd.read_excel(self._file_path)

        # 标准化列名
        col_map = {
            "sku_name": ["sku_name", "商品名称", "SKU", "sku", "货品"],
            "cost_per_unit": ["cost_per_unit", "单位成本", "采购成本", "生产成本"],
            "gift_cost_pct": ["gift_cost_pct", "赠品成本占比", "赠品率"],
            "warehouse_cost_per_order": ["warehouse_cost_per_order", "单均仓储费", "仓储费"],
            "labor_pct": ["labor_pct", "人工分摊占比", "人工占比"],
            "tax_rate": ["tax_rate", "综合税率", "税率"],
        }

        df.columns = [c.strip().lower() for c in df.columns]
        renamed: dict[str, str] = {}
        for target, aliases in col_map.items():
            for col in df.columns:
                if col in [a.lower() for a in aliases]:
                    renamed[col] = target
                    break

        df = df.rename(columns=renamed)

        required = ["sku_name"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"成本文件缺少必填列: {missing}")

        # 填充默认值
        for col in col_map:
            if col not in df.columns:
                df[col] = 0.0

        df = df.fillna(0.0)
        return df.to_dict(orient="records")
