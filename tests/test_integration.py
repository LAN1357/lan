"""端到端集成测试 — 完整算账链路."""
import sqlite3
from pathlib import Path

import pytest

from src.db import init_db
from src.importers.orders import import_orders_csv
from src.importers.ad_spend import import_ad_spend_csv
from src.importers.costs import set_cost_config
from src.engine.calculator import calculate_from_db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def db_conn():
    """创建内存数据库并使用 init_db 初始化全部表结构."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_full_pipeline(db_conn):
    """完整链路：导入订单 → 导入投放 → 配置成本 → 计算 ROI."""
    # Step 1: 导入订单
    r = import_orders_csv(db_conn, str(FIXTURES / "orders_sample.csv"))
    assert r["imported_count"] == 3

    # Step 2: 导入投放
    r = import_ad_spend_csv(db_conn, str(FIXTURES / "ad_spend_sample.csv"))
    assert r["imported_count"] == 2

    # Step 3: 设置成本
    set_cost_config(db_conn, "商品A", cost_per_unit=200.0, gift_cost_pct=0.02,
                     warehouse_cost_per_order=2.0, labor_pct=0.05, tax_rate=0.13)
    set_cost_config(db_conn, "商品B", cost_per_unit=150.0, gift_cost_pct=0.01,
                     warehouse_cost_per_order=1.0, labor_pct=0.05, tax_rate=0.13)

    # Step 4: 计算
    result = calculate_from_db(db_conn, period_type="day")
    assert len(result["periods"]) > 0

    # Step 5: 验证关键指标非空
    p = result["periods"][0]
    assert p["gmv"] > 0
    assert p["net_revenue"] > 0
    assert "net_profit" in p
    # 验证 ratio 字段存在（可为 None）
    assert "marketing_roi" in p
    assert "real_roi" in p
    assert "net_margin" in p
    assert "refund_rate" in p
    assert "ad_spend_ratio" in p
    # 验证 sku_breakdown 存在
    assert "sku_breakdown" in p
    assert isinstance(p["sku_breakdown"], list)

    # Step 6: 验证 snapshot 已写入
    snap = db_conn.execute(
        "SELECT COUNT(*) as cnt FROM calc_snapshots"
    ).fetchone()
    assert snap["cnt"] > 0


def test_empty_data_returns_warning(db_conn):
    """空数据库计算应返回警告."""
    result = calculate_from_db(db_conn, period_type="month")
    assert len(result["periods"]) == 0
    assert len(result["warnings"]) > 0


def test_period_aggregation(db_conn):
    """验证日/周/月三种汇总粒度均正常工作."""
    import_orders_csv(db_conn, str(FIXTURES / "orders_sample.csv"))
    import_ad_spend_csv(db_conn, str(FIXTURES / "ad_spend_sample.csv"))
    set_cost_config(db_conn, "商品A", cost_per_unit=200.0, tax_rate=0.13)
    set_cost_config(db_conn, "商品B", cost_per_unit=150.0, tax_rate=0.13)

    for pt in ["day", "week", "month"]:
        result = calculate_from_db(db_conn, period_type=pt)
        assert len(result["periods"]) > 0, f"{pt} 粒度应有结果"
        assert result["period_type"] == pt


def test_date_filter(db_conn):
    """验证 date_start / date_end 日期过滤功能."""
    import_orders_csv(db_conn, str(FIXTURES / "orders_sample.csv"))
    import_ad_spend_csv(db_conn, str(FIXTURES / "ad_spend_sample.csv"))
    set_cost_config(db_conn, "商品A", cost_per_unit=200.0, tax_rate=0.13)
    set_cost_config(db_conn, "商品B", cost_per_unit=150.0, tax_rate=0.13)

    # 仅查 2026-07-01 一天
    result = calculate_from_db(db_conn, period_type="day",
                               date_start="2026-07-01", date_end="2026-07-01")
    assert len(result["periods"]) == 1
    assert result["periods"][0]["period_value"] == "2026-07-01"


def test_variant_format_pipeline(db_conn):
    """验证变体格式 CSV 的完整导入链路."""
    r = import_orders_csv(db_conn, str(FIXTURES / "orders_variant.csv"))
    assert r["imported_count"] == 1

    set_cost_config(db_conn, "变体商品", cost_per_unit=100.0,
                     gift_cost_pct=0.02, tax_rate=0.13)

    result = calculate_from_db(db_conn, period_type="day")
    assert len(result["periods"]) > 0
    p = result["periods"][0]
    assert p["gmv"] > 0
    assert p["net_revenue"] > 0
    assert "net_profit" in p
