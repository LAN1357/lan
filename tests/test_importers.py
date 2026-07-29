import sqlite3
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            sku_name TEXT NOT NULL DEFAULT '',
            gmv REAL NOT NULL DEFAULT 0.0,
            refund_amount REAL NOT NULL DEFAULT 0.0,
            refund_status TEXT NOT NULL DEFAULT '',
            platform_fee REAL NOT NULL DEFAULT 0.0,
            commission REAL NOT NULL DEFAULT 0.0,
            shipping_fee REAL NOT NULL DEFAULT 0.0,
            insurance REAL NOT NULL DEFAULT 0.0,
            settle_date TEXT NOT NULL DEFAULT '',
            live_session_id TEXT NOT NULL DEFAULT '',
            imported_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE ad_spend (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_name TEXT NOT NULL DEFAULT '',
            spend REAL NOT NULL DEFAULT 0.0,
            impressions INTEGER NOT NULL DEFAULT 0,
            clicks INTEGER NOT NULL DEFAULT 0,
            conversions INTEGER NOT NULL DEFAULT 0,
            date TEXT NOT NULL DEFAULT '',
            live_session_id TEXT NOT NULL DEFAULT '',
            imported_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE cost_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku_name TEXT NOT NULL UNIQUE,
            cost_per_unit REAL NOT NULL DEFAULT 0.0,
            gift_cost_pct REAL NOT NULL DEFAULT 0.0,
            warehouse_cost_per_order REAL NOT NULL DEFAULT 0.0,
            labor_pct REAL NOT NULL DEFAULT 0.0,
            tax_rate REAL NOT NULL DEFAULT 0.0,
            effective_date TEXT NOT NULL DEFAULT ''
        );
    """)
    conn.commit()
    return conn


def test_import_orders_csv(db_conn):
    from src.importers.orders import import_orders_csv

    result = import_orders_csv(db_conn, str(FIXTURES / "orders_sample.csv"))
    assert result["imported_count"] == 3
    assert len(result["errors"]) == 0

    # 验证数据
    rows = db_conn.execute("SELECT * FROM orders").fetchall()
    assert len(rows) == 3
    assert rows[0]["order_id"] == "O001"
    assert rows[0]["gmv"] == 1000.0


def test_import_orders_variant_format(db_conn):
    from src.importers.orders import import_orders_csv

    result = import_orders_csv(db_conn, str(FIXTURES / "orders_variant.csv"))
    assert result["imported_count"] == 1


def test_import_orders_file_not_found(db_conn):
    from src.importers.orders import import_orders_csv

    result = import_orders_csv(db_conn, "/nonexistent/file.csv")
    assert result["imported_count"] == 0
    assert len(result["errors"]) == 1


def test_import_ad_spend_csv(db_conn):
    from src.importers.ad_spend import import_ad_spend_csv

    result = import_ad_spend_csv(db_conn, str(FIXTURES / "ad_spend_sample.csv"))
    assert result["imported_count"] == 2
    assert len(result["errors"]) == 0

    rows = db_conn.execute("SELECT * FROM ad_spend").fetchall()
    assert len(rows) == 2
    assert rows[0]["spend"] == 500.0


def test_set_and_list_cost_configs(db_conn):
    from src.importers.costs import set_cost_config, list_cost_configs

    # 新增
    result = set_cost_config(db_conn, "测试商品",
                             cost_per_unit=50.0, gift_cost_pct=0.03)
    assert result["updated"] is True

    configs = list_cost_configs(db_conn)
    assert len(configs) == 1
    assert configs[0]["sku_name"] == "测试商品"
    assert configs[0]["cost_per_unit"] == 50.0

    # 更新
    set_cost_config(db_conn, "测试商品", cost_per_unit=45.0)
    configs = list_cost_configs(db_conn, sku_name="测试商品")
    assert configs[0]["cost_per_unit"] == 45.0


def test_load_default_costs():
    from src.importers.costs import load_default_costs
    defaults = load_default_costs()
    assert isinstance(defaults, dict)
    assert "cost_per_unit" in defaults


def test_excel_erp_adapter():
    import tempfile
    import pandas as pd
    from src.adapters.excel_adapter import ExcelERPAdapter

    # 创建测试用成本 Excel
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        tmp = f.name
    df = pd.DataFrame([
        {"商品名称": "商品A", "单位成本": 30.0, "赠品成本占比": 0.02,
         "单均仓储费": 2.0, "人工分摊占比": 0.05, "综合税率": 0.13},
    ])
    df.to_excel(tmp, index=False)

    adapter = ExcelERPAdapter(tmp)
    costs = adapter.fetch_costs("2026-07")
    assert len(costs) == 1
    assert costs[0]["sku_name"] == "商品A"
    assert costs[0]["cost_per_unit"] == 30.0

    import os
    os.unlink(tmp)
