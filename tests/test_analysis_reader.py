import json
import sqlite3

import pytest

from src.analysis.reader import AnalysisError, CloseReader
from src.workbench.closing import reopen_month


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / 'missing.db'
    with pytest.raises(AnalysisError, match='不存在') as exc:
        CloseReader(path).list_versions()
    assert exc.value.code == 'NO_CLOSED_DATA'
    assert not path.exists()


def test_list_current_history_and_read_connection_is_query_only(closed_db):
    path, _conn = closed_db
    reader = CloseReader(path)
    versions = reader.list_versions(include_history=True)
    item = versions['data'][0]
    assert {key: item[key] for key in ('month', 'status', 'current_version', 'history_count')} == {
        'month': '2026-09', 'status': 'closed', 'current_version': 1, 'history_count': 1}
    assert versions['data'][0]['versions'][0]['version'] == 1
    with reader.transaction() as readonly:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            readonly.execute("UPDATE months SET status='open'")


def test_reopened_month_blocks_current_but_keeps_explicit_history(closed_db):
    path, conn = closed_db
    reader = CloseReader(path)
    reopen_month(conn, '2026-09', reason='版本测试')
    with pytest.raises(AnalysisError) as exc:
        reader.load_ref({'month': '2026-09', 'version': 1})
    assert exc.value.code == 'MONTH_REOPENED'
    old = reader.load_ref({'month': '2026-09', 'version': 1}, mode='history')
    assert old['_close_ref']['is_current'] is False
    assert old['results']['S1']['final_profit'] == 112000


def test_old_schema_is_refused_without_migration(tmp_path):
    path = tmp_path / 'old.db'
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA user_version=1')
    conn.execute('CREATE TABLE months(month TEXT,status TEXT)')
    conn.execute('CREATE TABLE closes(month TEXT,version INTEGER,closed_at TEXT,snapshot TEXT)')
    conn.close()
    with pytest.raises(AnalysisError) as exc:
        CloseReader(path).list_versions()
    assert exc.value.code == 'SNAPSHOT_INVALID'
    assert sqlite3.connect(path).execute('PRAGMA user_version').fetchone()[0] == 1


def test_unknown_accounting_rule_is_blocked(closed_db):
    path, conn = closed_db
    snapshot = json.loads(conn.execute('SELECT snapshot FROM closes').fetchone()[0])
    snapshot['rule_version'] = 'future-unknown-rule'
    conn.execute('UPDATE closes SET snapshot=?', (json.dumps(snapshot),))
    with pytest.raises(AnalysisError) as exc:
        CloseReader(path).load_ref({'month': '2026-09', 'version': 1})
    assert exc.value.code == 'INCOMPATIBLE_RULES'
