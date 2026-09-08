"""Shared application contract for MCP and browser analysis adapters."""

from __future__ import annotations

from pathlib import Path

from src.analysis import ANALYSIS_RULE_VERSION, CONTRACT_VERSION
from src.analysis.diagnostics import diagnose_performance
from src.analysis.evidence import compare_close_versions, get_session_evidence
from src.analysis.metrics import compare_performance, query_performance
from src.analysis.reader import AnalysisError, CloseReader
from src.analysis.rules import get_metric_rules
from src.analysis.scenarios import simulate_scenario
from src.analysis.schemas import (DEFAULT_GROUP_BY, DEFAULT_LIMIT, DEFAULT_MODE,
                                  DEFAULT_OFFSET, DEFAULT_PROFIT_METRIC,
                                  DEFAULT_SORT_BY, DEFAULT_SORT_ORDER)


class AnalysisService:
    """One JSON-facing contract shared by all transports."""

    def __init__(self, db_path: str | Path):
        self.reader = CloseReader(db_path)

    def _call(self, function, *args, **kwargs) -> dict:
        try:
            result = function(*args, **kwargs)
        except AnalysisError as exc:
            result = exc.payload()
        except Exception:
            result = AnalysisError(
                'SNAPSHOT_INVALID', '分析请求无法完成',
                '核对参数与关账副本；如仍失败请由开发人员查看本地日志',
            ).payload()
        result.setdefault('contract_version', CONTRACT_VERSION)
        result.setdefault('as_of', self.reader._as_of())
        return result

    def list_close_versions(self, start_month: str | None = None,
                            end_month: str | None = None,
                            include_history: bool = False) -> dict:
        return self._call(self.reader.list_versions, start_month, end_month,
                          include_history=include_history)

    def query_performance(self, scope: dict, group_by: str = DEFAULT_GROUP_BY,
                          metrics: list[str] | None = None,
                          sort_by: str = DEFAULT_SORT_BY,
                          sort_order: str = DEFAULT_SORT_ORDER,
                          offset: int = DEFAULT_OFFSET,
                          limit: int = DEFAULT_LIMIT) -> dict:
        return self._call(query_performance, self.reader, scope, group_by=group_by,
                          metrics=metrics, sort_by=sort_by, sort_order=sort_order,
                          offset=offset, limit=limit)

    def compare_performance(self, base_scope: dict, current_scope: dict,
                            profit_metric: str = DEFAULT_PROFIT_METRIC) -> dict:
        return self._call(compare_performance, self.reader, base_scope, current_scope,
                          profit_metric=profit_metric)

    def get_session_evidence(self, close_ref: dict, session_id: str,
                             topic: str = 'pnl', mode: str = DEFAULT_MODE,
                             offset: int = DEFAULT_OFFSET,
                             limit: int = DEFAULT_LIMIT) -> dict:
        return self._call(get_session_evidence, self.reader, close_ref, session_id,
                          topic=topic, mode=mode, offset=offset, limit=limit)

    def compare_close_versions(self, month: str, base_version: int,
                               current_version: int, offset: int = DEFAULT_OFFSET,
                               limit: int = DEFAULT_LIMIT) -> dict:
        return self._call(compare_close_versions, self.reader, month, base_version,
                          current_version, offset=offset, limit=limit)

    def get_metric_rules(self, metric_keys: list[str] | None = None,
                         accounting_rule_version: str = 'm1-decimal-half-up-v1',
                         analysis_rule_version: str = ANALYSIS_RULE_VERSION) -> dict:
        return self._call(get_metric_rules, metric_keys,
                          accounting_rule_version=accounting_rule_version,
                          analysis_rule_version=analysis_rule_version)

    def diagnose_performance(self, scope: dict, base_scope: dict | None = None,
                             rules: list[str] | None = None,
                             offset: int = DEFAULT_OFFSET,
                             limit: int = DEFAULT_LIMIT) -> dict:
        return self._call(diagnose_performance, self.reader, scope,
                          base_scope=base_scope, rules=rules, offset=offset,
                          limit=limit)

    def simulate_scenario(self, close_ref: dict, session_ids: list[str],
                          assumptions: list[dict], mode: str = DEFAULT_MODE,
                          assumption_source: str = 'user_provided',
                          authorization_scope: dict | None = None) -> dict:
        return self._call(simulate_scenario, self.reader, close_ref, session_ids,
                          assumptions, mode=mode,
                          assumption_source=assumption_source,
                          authorization_scope=authorization_scope)
