"""Read immutable close snapshots without invoking workbench migrations or writes."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

from src.analysis import CONTRACT_VERSION
from src.analysis.schemas import MAX_MONTHS, MODES

EXPECTED_SCHEMA_VERSION = 3
SUPPORTED_SCHEMA_VERSIONS = {2, 3}
SUPPORTED_ACCOUNTING_RULES = {'m1-decimal-half-up-v1'}


class AnalysisError(ValueError):
    def __init__(self, code: str, message: str, next_action: str = ''):
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_action = next_action

    def payload(self) -> dict:
        result = {'status':'error','contract_version':CONTRACT_VERSION,
                  'error':{'code':self.code,'message':self.message}}
        if self.next_action:
            result['error']['next_action'] = self.next_action
        return result


def check_month(month: str) -> str:
    if not isinstance(month,str) or len(month) != 7:
        raise AnalysisError('INVALID_SCOPE','业务月份必须使用 YYYY-MM 格式')
    try:
        datetime.strptime(month,'%Y-%m')
    except ValueError as exc:
        raise AnalysisError('INVALID_SCOPE','业务月份必须使用有效的 YYYY-MM') from exc
    return month


class CloseReader:
    """A fixed-path, read-only reader. Tool inputs can never choose another file."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser().resolve()

    def _connect(self) -> sqlite3.Connection:
        uri = 'file:' + quote(str(self.db_path),safe='/') + '?mode=ro'
        try:
            conn = sqlite3.connect(uri,uri=True,timeout=5)
        except sqlite3.OperationalError as exc:
            raise AnalysisError('NO_CLOSED_DATA','工作台数据库不存在或不可读取','先在工作台完成关账，并检查M3配置的固定数据库路径') from exc
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('months','closes')")}
        if version not in SUPPORTED_SCHEMA_VERSIONS or tables != {'months','closes'}:
            conn.close()
            raise AnalysisError('SNAPSHOT_INVALID',f'数据库结构版本不受支持（需要 {EXPECTED_SCHEMA_VERSION}，实际 {version}）','使用当前工作台数据库；M3不会自动迁移或修复')
        return conn

    @contextmanager
    def transaction(self):
        conn = self._connect()
        try:
            conn.execute('BEGIN')
            yield conn
            conn.rollback()
        finally:
            conn.close()

    @staticmethod
    def _as_of() -> str:
        return datetime.now(timezone.utc).isoformat()

    def list_versions(self, start_month: str | None = None, end_month: str | None = None,
                      include_history: bool = False) -> dict:
        if start_month:
            check_month(start_month)
        if end_month:
            check_month(end_month)
        if start_month and end_month and start_month > end_month:
            raise AnalysisError('INVALID_SCOPE','起始月份不能晚于结束月份')
        with self.transaction() as conn:
            params: list[str] = []
            where = []
            if start_month:
                where.append('month>=?'); params.append(start_month)
            if end_month:
                where.append('month<=?'); params.append(end_month)
            clause = (' WHERE ' + ' AND '.join(where)) if where else ''
            months = {r['month']:r['status'] for r in conn.execute('SELECT month,status FROM months'+clause,params)}
            close_rows = list(conn.execute('SELECT month,version,closed_at FROM closes'+clause+' ORDER BY month,version',params))
            grouped: dict[str,list[dict]] = {}
            for row in close_rows:
                grouped.setdefault(row['month'],[]).append({'version':row['version'],'closed_at':row['closed_at']})
            result = []
            for month in sorted(set(months)|set(grouped)):
                versions = grouped.get(month,[])
                status = months.get(month,'unknown')
                current = versions[-1] if status == 'closed' and versions else None
                item = {'month':month,'status':status,'current_version':current['version'] if current else None,
                        'current_closed_at':current['closed_at'] if current else None,
                        'history_count':len(versions)}
                if include_history:
                    item['versions'] = versions
                result.append(item)
            return {'status':'ok','as_of':self._as_of(),'data':result,
                    'summary_text':f'共找到 {len(result)} 个业务月份的关账状态。'}

    @staticmethod
    def _decode(row: sqlite3.Row, month: str, version: int) -> dict:
        try:
            snapshot = json.loads(row['snapshot'])
        except (TypeError,json.JSONDecodeError) as exc:
            raise AnalysisError('SNAPSHOT_INVALID',f'{month} V{version} 关账副本无法解析') from exc
        required = {'month','version','closed_at','rule_version','sessions','results','total'}
        if not isinstance(snapshot,dict) or not required <= snapshot.keys() or snapshot['month'] != month or snapshot['version'] != version:
            raise AnalysisError('SNAPSHOT_INVALID',f'{month} V{version} 关账副本字段不完整或引用不一致')
        if snapshot['rule_version'] not in SUPPORTED_ACCOUNTING_RULES:
            raise AnalysisError('INCOMPATIBLE_RULES',
                                f'{month} V{version} 使用未知核算规则 {snapshot["rule_version"]}',
                                '仅选择M3明确支持的核算规则版本')
        return snapshot

    def load_ref(self, close_ref: dict, *, mode: str = 'current', conn: sqlite3.Connection | None = None) -> dict:
        if not isinstance(close_ref,dict):
            raise AnalysisError('INVALID_SCOPE','close_ref 必须包含 month 和 version')
        month = check_month(close_ref.get('month'))
        version = close_ref.get('version')
        if type(version) is not int or version <= 0:
            raise AnalysisError('INVALID_SCOPE','关账版本必须是正整数')
        if mode not in MODES:
            raise AnalysisError('INVALID_SCOPE','mode 只能是 current 或 history')
        if conn is None:
            with self.transaction() as owned:
                return self.load_ref(close_ref,mode=mode,conn=owned)
        state = conn.execute('SELECT status FROM months WHERE month=?',(month,)).fetchone()
        if mode == 'current':
            if state and state['status'] == 'open':
                raise AnalysisError('MONTH_REOPENED',f'{month} 已重开，当前没有有效关账结果','在工作台重新关账，或明确使用 history 查询旧版本')
            if not state or state['status'] != 'closed':
                raise AnalysisError('NO_CLOSED_DATA',f'{month} 尚无当前有效关账结果','先在工作台完成关账')
            latest = conn.execute('SELECT version FROM closes WHERE month=? ORDER BY version DESC LIMIT 1',(month,)).fetchone()
            if not latest:
                raise AnalysisError('SNAPSHOT_INVALID',f'{month} 标为已关账但找不到关账副本')
            if latest['version'] != version:
                raise AnalysisError('VERSION_CHANGED',f'{month} 当前有效版本已变为 V{latest["version"]}','重新获取关账版本目录后再分析')
        row = conn.execute('SELECT snapshot,closed_at FROM closes WHERE month=? AND version=?',(month,version)).fetchone()
        if not row:
            raise AnalysisError('VERSION_NOT_FOUND',f'找不到 {month} V{version}')
        snapshot = self._decode(row,month,version)
        snapshot['_close_ref'] = {'month':month,'version':version,'mode':mode,
                                  'is_current':bool(state and state['status']=='closed' and
                                      conn.execute('SELECT MAX(version) FROM closes WHERE month=?',(month,)).fetchone()[0] == version)}
        return snapshot

    def load_refs(self, close_refs: list[dict], *, mode: str = 'current',
                  conn: sqlite3.Connection | None = None) -> tuple[list[dict],str]:
        if not isinstance(close_refs,list) or not close_refs:
            raise AnalysisError('INVALID_SCOPE','至少提供一个明确关账版本')
        if len(close_refs) > MAX_MONTHS:
            raise AnalysisError('INVALID_SCOPE',f'一次最多分析{MAX_MONTHS}个业务月')
        keys = [(r.get('month'),r.get('version')) for r in close_refs if isinstance(r,dict)]
        if len(keys) != len(close_refs) or len(set(keys)) != len(keys) or len({m for m,_ in keys}) != len(keys):
            raise AnalysisError('INVALID_SCOPE','同一分析范围中每月只能包含一个明确版本')
        if conn is None:
            with self.transaction() as owned:
                return self.load_refs(close_refs, mode=mode, conn=owned)
        snapshots = [self.load_ref(ref, mode=mode, conn=conn) for ref in close_refs]
        rules = {s['rule_version'] for s in snapshots}
        if len(rules) != 1:
            raise AnalysisError('INCOMPATIBLE_RULES','所选关账版本使用不同核算规则，不能合并分析')
        return snapshots,self._as_of()
