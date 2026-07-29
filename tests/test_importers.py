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
