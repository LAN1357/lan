"""Shared lifecycle for browser and desktop workbench processes."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
from pathlib import Path
import threading
import time
from urllib.request import urlopen

from src.workbench.db import connect


class WorkbenchRuntimeError(RuntimeError):
    """A startup or lifecycle error safe to show to the local user."""


class LedgerAlreadyOpen(WorkbenchRuntimeError):
    pass


class InvalidLedger(WorkbenchRuntimeError):
    pass


def canonical_ledger_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def ledger_lock_path(path: str | Path) -> Path:
    ledger = canonical_ledger_path(path)
    return ledger.with_name(f'.{ledger.name}.workbench.lock')


def inspect_existing_ledger(path: str | Path) -> int:
    """Validate an existing workbench ledger without modifying or migrating it."""
    import sqlite3
    from urllib.parse import quote

    ledger = canonical_ledger_path(path)
    if not ledger.is_file():
        raise InvalidLedger('账本路径不存在或不是文件；请选择已有的 workbench.db')
    uri = 'file:' + quote(str(ledger), safe='/') + '?mode=ro'
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise InvalidLedger('所选文件无法作为SQLite账本读取') from exc
    try:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('batches','records','months','closes')"
            )
        }
    except sqlite3.Error as exc:
        raise InvalidLedger('所选文件不是可读取的工作台账本') from exc
    finally:
        conn.close()
    if version not in (1, 2, 3) or tables != {'batches', 'records', 'months', 'closes'}:
        raise InvalidLedger('所选文件不是受支持的新版工作台账本；旧ROI原型库不能打开')
    return version


class LedgerLock:
    """One-writer-process lock held for the whole workbench lifetime."""

    def __init__(self, ledger_path: str | Path):
        self.ledger_path = canonical_ledger_path(ledger_path)
        self.path = ledger_lock_path(self.ledger_path)
        self._file = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+', encoding='utf-8')
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise LedgerAlreadyOpen(
                '该账本已在工作台中打开，请使用已有窗口或先退出原工作台'
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({'ledger': str(self.ledger_path)}, ensure_ascii=False))
        handle.flush()
        self._file = handle

    def release(self) -> None:
        if self._file is None:
            return
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_exc):
        self.release()


@dataclass
class WorkbenchRuntime:
    ledger_path: Path
    lock: LedgerLock
    server: object
    thread: threading.Thread

    @property
    def base_url(self) -> str:
        return f'http://127.0.0.1:{self.server.server_port}'

    @property
    def active_requests(self) -> int:
        return self.server.active_requests

    def prepare_close(self) -> bool:
        """Atomically stop future requests only when no request is running."""
        return self.server.begin_shutdown_if_idle()

    def stop(self, *, prepared: bool = False) -> None:
        if self.thread is None:
            self.lock.release()
            return
        try:
            if not prepared:
                self.server.stop_accepting()
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise WorkbenchRuntimeError('工作台服务未能在预期时间内退出')
        finally:
            self.thread = None
            self.lock.release()


def _wait_until_ready(base_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            with urlopen(base_url + '/months', timeout=min(1.0, timeout)) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # readiness probe is retried until the deadline
            last_error = exc
        time.sleep(0.03)
    raise WorkbenchRuntimeError('本地工作台服务启动超时') from last_error


def start_workbench(
    ledger_path: str | Path,
    *,
    port: int = 0,
    create_if_missing: bool = False,
    ready_timeout: float = 5.0,
) -> WorkbenchRuntime:
    """Lock, initialize and start one local WorkbenchServer with cleanup on failure."""
    from src.workbench.app import WorkbenchServer

    ledger = canonical_ledger_path(ledger_path)
    if not ledger.exists() and not create_if_missing:
        raise InvalidLedger('已保存的账本路径不存在；请重新选择，不会自动创建替代库')
    if create_if_missing:
        ledger.parent.mkdir(parents=True, exist_ok=True)
    lock = LedgerLock(ledger)
    server = None
    thread = None
    try:
        lock.acquire()
        if ledger.exists():
            inspect_existing_ledger(ledger)
        conn = connect(ledger)
        conn.close()
        server = WorkbenchServer(('127.0.0.1', port), ledger)
        thread = threading.Thread(
            target=server.serve_forever,
            name='workbench-http',
            daemon=False,
        )
        thread.start()
        runtime = WorkbenchRuntime(ledger, lock, server, thread)
        _wait_until_ready(runtime.base_url, ready_timeout)
        if not server.wait_until_idle(ready_timeout):
            raise WorkbenchRuntimeError('本地工作台就绪检查未能正常结束')
        return runtime
    except BaseException:
        if server is not None:
            server.stop_accepting()
            if thread is not None and thread.is_alive():
                server.shutdown()
            server.server_close()
            if thread is not None:
                thread.join(timeout=5)
        lock.release()
        raise
