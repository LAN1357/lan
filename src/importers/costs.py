"""成本配置管理."""

import yaml
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


def load_default_costs() -> dict:
    """从 default_costs.yaml 加载默认成本参数."""
    config_path = CONFIG_DIR / "default_costs.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("default", {})
    return {}


def set_cost_config(conn, sku_name: str, **kwargs) -> dict:
    """设置 / 更新单个 SKU 的成本配置.

    Args:
        conn: sqlite3.Connection
        sku_name: 商品名称
        **kwargs: cost_per_unit, gift_cost_pct, warehouse_cost_per_order,
                  labor_pct, tax_rate, effective_date

    Returns:
        {"sku_name": str, "updated": bool}
    """
    allowed = ["cost_per_unit", "gift_cost_pct", "warehouse_cost_per_order",
               "labor_pct", "tax_rate", "effective_date"]
    fields = {k: v for k, v in kwargs.items() if k in allowed}

    if not fields:
        return {"sku_name": sku_name, "updated": False, "error": "未提供有效成本字段"}

    # 检查是否已存在
    existing = conn.execute(
        "SELECT id FROM cost_config WHERE sku_name = ?", (sku_name,)
    ).fetchone()

    if existing:
        set_clause = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [sku_name]
        conn.execute(
            f"UPDATE cost_config SET {set_clause} WHERE sku_name=?",
            values,
        )
    else:
        fields["sku_name"] = sku_name
        columns = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        conn.execute(
            f"INSERT INTO cost_config ({columns}) VALUES ({placeholders})",
            list(fields.values()),
        )

    conn.commit()
    return {"sku_name": sku_name, "updated": True, "fields": list(fields.keys())}


def list_cost_configs(conn, sku_name: Optional[str] = None) -> list[dict]:
    """查询成本配置.

    Args:
        conn: sqlite3.Connection
        sku_name: 可选，按商品名过滤

    Returns:
        [{"sku_name": str, "cost_per_unit": float, ...}]
    """
    if sku_name:
        rows = conn.execute(
            "SELECT * FROM cost_config WHERE sku_name = ?", (sku_name,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cost_config ORDER BY sku_name").fetchall()
    return [dict(r) for r in rows]
