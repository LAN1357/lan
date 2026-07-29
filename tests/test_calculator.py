"""Calculator 集成测试 — 写入 SQLite 后验证完整计算链路."""
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from src.engine.calculator import calculate_from_db


@pytest.fixture
def db_conn():
    """创建临时数据库并初始化表结构."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL, sku_name TEXT, gmv REAL DEFAULT 0,
            refund_amount REAL DEFAULT 0, refund_status TEXT DEFAULT '',
            platform_fee REAL DEFAULT 0, commission REAL DEFAULT 0,
            shipping_fee REAL DEFAULT 0, insurance REAL DEFAULT 0,
            settle_date TEXT DEFAULT '', live_session_id TEXT DEFAULT '',
            imported_at TEXT DEFAULT ''
        );
        CREATE TABLE ad_spend (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_name TEXT, spend REAL DEFAULT 0,
            impressions INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0,
            conversions INTEGER DEFAULT 0, date TEXT DEFAULT '',
            live_session_id TEXT DEFAULT '', imported_at TEXT DEFAULT ''
        );
        CREATE TABLE cost_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku_name TEXT NOT NULL UNIQUE, cost_per_unit REAL DEFAULT 0,
            gift_cost_pct REAL DEFAULT 0, warehouse_cost_per_order REAL DEFAULT 0,
            labor_pct REAL DEFAULT 0, tax_rate REAL DEFAULT 0,
            effective_date TEXT DEFAULT ''
        );
        CREATE TABLE calc_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_type TEXT NOT NULL, period_value TEXT NOT NULL,
            metrics_json TEXT DEFAULT '{}', created_at TEXT DEFAULT '',
            UNIQUE(period_type, period_value)
        );
    """)
    conn.commit()
    return conn


def test_calculate_from_db_basic(db_conn):
    """完整计算链路：插入数据 → 计算 → 验证结果."""
    # 插入订单
    db_conn.execute("""
        INSERT INTO orders (order_id, sku_name, gmv, refund_amount,
            platform_fee, commission, shipping_fee, insurance, settle_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("O001", "商品A", 1000.0, 100.0, 50.0, 100.0, 10.0, 2.0, "2026-07-01"))
    # 插入投放
    db_conn.execute("""
        INSERT INTO ad_spend (campaign_name, spend, date)
        VALUES (?, ?, ?)
    """, ("计划1", 200.0, "2026-07-01"))
    # 插入成本
    db_conn.execute("""
        INSERT INTO cost_config (sku_name, cost_per_unit, gift_cost_pct,
            warehouse_cost_per_order, labor_pct, tax_rate)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("商品A", 300.0, 0.02, 3.0, 0.05, 0.13))
    db_conn.commit()

    result = calculate_from_db(db_conn, period_type="month")

    assert result["period_type"] == "month"
    assert len(result["periods"]) == 1
    period = result["periods"][0]
    assert period["gmv"] == 1000.0
    assert period["net_gmv"] == 900.0
    assert period["net_revenue"] == 750.0
    assert period["net_profit"] == pytest.approx(154.43, rel=1e-2)

    # 验证 snapshot 已写入
    snap = db_conn.execute(
        "SELECT * FROM calc_snapshots WHERE period_type='month'"
    ).fetchone()
    assert snap is not None
