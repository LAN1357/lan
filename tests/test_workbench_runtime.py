from pathlib import Path

import pytest

from src.workbench.runtime import (
    InvalidLedger,
    LedgerAlreadyOpen,
    inspect_existing_ledger,
    ledger_lock_path,
    start_workbench,
)


def test_runtime_uses_dynamic_port_and_releases_ledger_lock(tmp_path):
    ledger = tmp_path / '中文目录' / 'workbench.db'
    first = start_workbench(ledger, create_if_missing=True)
    try:
        assert first.server.server_address[0] == '127.0.0.1'
        assert first.server.server_port > 0
        assert first.base_url.endswith(str(first.server.server_port))
        assert ledger_lock_path(ledger).exists()
        assert inspect_existing_ledger(ledger) == 3
        with pytest.raises(LedgerAlreadyOpen, match='已在工作台中打开'):
            start_workbench(ledger)
    finally:
        first.stop()

    reopened = start_workbench(ledger)
    reopened.stop()


def test_runtime_does_not_create_missing_saved_ledger(tmp_path):
    parent = tmp_path / 'missing-directory'
    missing = parent / 'missing.db'
    with pytest.raises(InvalidLedger, match='不会自动创建替代库'):
        start_workbench(missing)
    assert not missing.exists()
    assert not parent.exists()


def test_existing_legacy_database_is_rejected_without_migration(tmp_path):
    import sqlite3

    legacy = tmp_path / 'roi_agent.db'
    conn = sqlite3.connect(legacy)
    conn.execute('CREATE TABLE orders(id INTEGER PRIMARY KEY)')
    conn.commit()
    conn.close()

    with pytest.raises(InvalidLedger, match='旧ROI原型库'):
        start_workbench(legacy)
    assert sqlite3.connect(legacy).execute('PRAGMA user_version').fetchone()[0] == 0


def test_close_is_cancelled_while_request_is_active(tmp_path):
    runtime = start_workbench(tmp_path / 'workbench.db', create_if_missing=True)
    try:
        with runtime.server._request_state:
            runtime.server._active_requests += 1
        assert runtime.prepare_close() is False
        with runtime.server._request_state:
            runtime.server._active_requests -= 1
            runtime.server._request_state.notify_all()
        assert runtime.prepare_close() is True
        runtime.stop(prepared=True)
    finally:
        runtime.stop()
