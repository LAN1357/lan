"""SQLite 数据库初始化和连接管理."""

import sqlite3
from pathlib import Path

DB_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DB_DIR / "roi_agent.db"


def get_db() -> sqlite3.Connection:
    """获取数据库连接（启用 WAL 模式和外键约束）."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """创建全部表（如不存在）."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
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

        CREATE TABLE IF NOT EXISTS ad_spend (
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

        CREATE TABLE IF NOT EXISTS cost_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku_name TEXT NOT NULL UNIQUE,
            cost_per_unit REAL NOT NULL DEFAULT 0.0,
            gift_cost_pct REAL NOT NULL DEFAULT 0.0,
            warehouse_cost_per_order REAL NOT NULL DEFAULT 0.0,
            labor_pct REAL NOT NULL DEFAULT 0.0,
            tax_rate REAL NOT NULL DEFAULT 0.0,
            effective_date TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS erp_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL DEFAULT '',
            period TEXT NOT NULL DEFAULT '',
            raw_data_json TEXT NOT NULL DEFAULT '{}',
            processed_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS calc_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_type TEXT NOT NULL,
            period_value TEXT NOT NULL,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT '',
            UNIQUE(period_type, period_value)
        );
    """)
    conn.commit()
