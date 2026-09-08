"""Independent local database; no legacy MCP imports or migrations."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / 'data' / 'workbench.db'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def connect(path=DB_PATH):
    path = Path(path) if str(path) != ':memory:' else path
    if isinstance(path, Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, 1, 2, 3):
        conn.close()
        raise ValueError('不支持的数据库版本；升级前请先备份')
    if version == 0:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone():
            conn.close()
            raise ValueError('此文件不是空库或新版工作台库，不能使用旧原型数据库')
        conn.executescript('''
        BEGIN IMMEDIATE;
        CREATE TABLE batches (
          id INTEGER PRIMARY KEY, filename TEXT NOT NULL, file_hash TEXT NOT NULL,
          created_at TEXT NOT NULL, status TEXT NOT NULL, preview TEXT NOT NULL);
        CREATE UNIQUE INDEX accepted_file ON batches(file_hash) WHERE status='accepted';
        CREATE TABLE records (
          id INTEGER PRIMARY KEY, kind TEXT NOT NULL, business_key TEXT NOT NULL,
          data TEXT NOT NULL, batch_id INTEGER REFERENCES batches(id), source_row INTEGER,
          UNIQUE(kind, business_key));
        CREATE TABLE assignments (
          record_id INTEGER PRIMARY KEY REFERENCES records(id), data TEXT NOT NULL);
        CREATE TABLE settings (session_id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE changes (
          id INTEGER PRIMARY KEY, object_type TEXT NOT NULL, object_key TEXT NOT NULL,
          old_value TEXT, new_value TEXT, reason TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE months (month TEXT PRIMARY KEY, status TEXT NOT NULL);
        CREATE TABLE closes (
          month TEXT NOT NULL, version INTEGER NOT NULL, closed_at TEXT NOT NULL,
          snapshot TEXT NOT NULL, PRIMARY KEY(month, version));
        PRAGMA user_version=1;
        COMMIT;
        ''')
    original_version = version
    if version in (0, 1):
        if version == 1 and str(path) != ':memory:':
            # One concrete upgrade, using SQLite backup so committed WAL is included.
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
            backup(conn, Path(path).with_name(Path(path).name + f'.pre-m2-{stamp}.bak'))
        try:
            conn.executescript('''
            BEGIN IMMEDIATE;
            CREATE TABLE recommendations (
              id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL REFERENCES records(id),
              data TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX recommendations_source ON recommendations(record_id,id);
            CREATE TABLE cost_standards (
              id INTEGER PRIMARY KEY, sku TEXT NOT NULL, month TEXT NOT NULL,
              data TEXT NOT NULL, UNIQUE(sku,month));
            CREATE TABLE ad_links (
              id INTEGER PRIMARY KEY, month TEXT NOT NULL, account TEXT NOT NULL,
              plan TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(month,account,plan));
            ALTER TABLE changes ADD COLUMN operation_id TEXT;
            CREATE INDEX changes_operation ON changes(operation_id);
            PRAGMA user_version=2;
            COMMIT;
            ''')
        except BaseException:
            conn.rollback()
            conn.close()
            raise
        version = 2
    if version == 2:
        if original_version == 2 and str(path) != ':memory:':
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
            backup(conn, Path(path).with_name(Path(path).name + f'.pre-daily-{stamp}.bak'))
        try:
            batch_columns = {row['name'] for row in conn.execute('PRAGMA table_info(batches)')}
            tables = {row['name'] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            conn.execute('BEGIN IMMEDIATE')
            if 'import_mode' not in batch_columns:
                conn.execute("ALTER TABLE batches ADD COLUMN import_mode TEXT NOT NULL DEFAULT 'legacy'")
            if 'context' not in batch_columns:
                conn.execute("ALTER TABLE batches ADD COLUMN context TEXT NOT NULL DEFAULT '{}'")
            if 'batch_members' not in tables:
                conn.execute('''CREATE TABLE batch_members (
              batch_id INTEGER NOT NULL REFERENCES batches(id),
              member_order INTEGER NOT NULL,
              kind TEXT NOT NULL,
              business_key TEXT NOT NULL,
              action TEXT NOT NULL,
              record_id INTEGER REFERENCES records(id) ON DELETE SET NULL,
              source_row INTEGER,
              snapshot TEXT NOT NULL,
              PRIMARY KEY(batch_id,member_order))''')
                conn.execute('CREATE INDEX batch_members_record ON batch_members(record_id,batch_id)')
            if 'session_source_mappings' not in tables:
                conn.execute('''CREATE TABLE session_source_mappings (
              batch_id INTEGER NOT NULL REFERENCES batches(id),
              source_session_id TEXT NOT NULL,
              internal_session_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(batch_id,source_session_id))''')
            conn.execute('PRAGMA user_version=3')
            conn.commit()
        except BaseException:
            conn.rollback()
            conn.close()
            raise
    return conn


@contextmanager
def transaction(conn):
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def backup(conn, destination):
    """SQLite backup API includes committed WAL contents."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError('备份目标已存在')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(destination) as target:
        conn.backup(target)
    return destination


def clear_all_data(conn):
    """Remove one trial workspace as a single transaction, preserving its schema."""
    with transaction(conn):
        # Delete dependants before their source records because foreign keys are
        # intentionally restrictive: a partial reset must never be possible.
        for table in ('recommendations', 'assignments', 'closes', 'changes',
                      'settings', 'cost_standards', 'ad_links', 'months',
                      'session_source_mappings', 'batch_members', 'records', 'batches'):
            conn.execute(f'DELETE FROM {table}')


def records(conn, kind=None):
    rows = conn.execute('SELECT * FROM records' + (' WHERE kind=?' if kind else '') + ' ORDER BY id',
                        (kind,) if kind else ())
    return [dict(r) | {'data': json.loads(r['data'])} for r in rows]


def record(conn, record_id):
    row = conn.execute('SELECT * FROM records WHERE id=?', (record_id,)).fetchone()
    if row is None:
        raise ValueError('来源记录不存在')
    return dict(row) | {'data': json.loads(row['data'])}


def session_map(conn):
    return {r['data']['session_id']: r['data'] for r in records(conn, 'sessions')}


def ensure_open(conn, months):
    for month in set(months):
        row = conn.execute('SELECT status FROM months WHERE month=?', (month,)).fetchone()
        if row and row['status'] == 'closed':
            raise ValueError(f'{month}已关账，请先整月重开')


def audit(conn, kind, key, old, new, reason, *, operation_id=None):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('请填写修改/确认原因')
    conn.execute('INSERT INTO changes(object_type,object_key,old_value,new_value,reason,created_at,operation_id) VALUES(?,?,?,?,?,?,?)',
                 (kind, str(key), dumps(old), dumps(new), reason.strip(), now(), operation_id))
