"""Independent stdio MCP server exposing exactly eight read-only M3 tools."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from src.analysis import ANALYSIS_RULE_VERSION
from src.analysis.schemas import (DEFAULT_GROUP_BY, DEFAULT_LIMIT, DEFAULT_MODE,
                                  DEFAULT_OFFSET, DEFAULT_PROFIT_METRIC,
                                  DEFAULT_SORT_BY, DEFAULT_SORT_ORDER)
from src.analysis.service import AnalysisService

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                            idempotentHint=True, openWorldHint=False)


def create_server(db_path: str | Path) -> FastMCP:
    """Create a fixed-database server; no tool accepts paths, SQL, or URLs."""
    service = AnalysisService(db_path)
    server = FastMCP('直播经营复盘与决策支持（只读）',
                     instructions='仅分析明确关账版本；所有工具只读取不可变关账副本。')

    @server.tool(name='workbench_list_close_versions', annotations=READ_ONLY, structured_output=True)
    def list_close_versions(start_month: str | None = None, end_month: str | None = None,
                            include_history: bool = False) -> dict[str, Any]:
        """列出业务月状态和可引用的关账版本；不返回利润。"""
        return service.list_close_versions(start_month, end_month, include_history)

    @server.tool(name='workbench_query_performance', annotations=READ_ONLY, structured_output=True)
    def performance(scope: dict[str, Any], group_by: str = DEFAULT_GROUP_BY,
                    metrics: list[str] | None = None, sort_by: str = DEFAULT_SORT_BY,
                    sort_order: str = DEFAULT_SORT_ORDER, offset: int = DEFAULT_OFFSET,
                    limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """按月份、场次或达人汇总明确关账范围的经营表现。"""
        return service.query_performance(scope, group_by, metrics, sort_by,
                                         sort_order, offset, limit)

    @server.tool(name='workbench_compare_performance', annotations=READ_ONLY, structured_output=True)
    def compare(base_scope: dict[str, Any], current_scope: dict[str, Any],
                profit_metric: str = DEFAULT_PROFIT_METRIC) -> dict[str, Any]:
        """比较两个明确关账范围，并返回守恒的利润科目桥。"""
        return service.compare_performance(base_scope, current_scope, profit_metric)

    @server.tool(name='workbench_get_session_evidence', annotations=READ_ONLY, structured_output=True)
    def session_evidence(close_ref: dict[str, Any], session_id: str, topic: str = 'pnl',
                         mode: str = DEFAULT_MODE, offset: int = DEFAULT_OFFSET,
                         limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """查看单场在指定关账版中的利润、SKU、分配或修改依据。"""
        return service.get_session_evidence(close_ref, session_id, topic, mode,
                                            offset, limit)

    @server.tool(name='workbench_compare_close_versions', annotations=READ_ONLY, structured_output=True)
    def close_versions(month: str, base_version: int, current_version: int,
                       offset: int = DEFAULT_OFFSET,
                       limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """比较同一业务月的两个历史关账版本。"""
        return service.compare_close_versions(month, base_version, current_version,
                                              offset, limit)

    @server.tool(name='workbench_get_metric_rules', annotations=READ_ONLY, structured_output=True)
    def metric_rules(metric_keys: list[str] | None = None,
                     accounting_rule_version: str = 'm1-decimal-half-up-v1',
                     analysis_rule_version: str = ANALYSIS_RULE_VERSION) -> dict[str, Any]:
        """返回指标公式、单位、来源、分母和可比性规则。"""
        return service.get_metric_rules(metric_keys, accounting_rule_version,
                                        analysis_rule_version)

    @server.tool(name='workbench_diagnose_performance', annotations=READ_ONLY, structured_output=True)
    def diagnostics(scope: dict[str, Any], base_scope: dict[str, Any] | None = None,
                    rules: list[str] | None = None, offset: int = DEFAULT_OFFSET,
                    limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """运行少量固定事实规则，列出需要BP关注和核查的场次。"""
        return service.diagnose_performance(scope, base_scope, rules, offset, limit)

    @server.tool(name='workbench_simulate_scenario', annotations=READ_ONLY, structured_output=True)
    def scenario(close_ref: dict[str, Any], session_ids: list[str],
                 assumptions: list[dict[str, Any]], mode: str = DEFAULT_MODE,
                 assumption_source: str = 'user_provided',
                 authorization_scope: dict[str, Any] | None = None) -> dict[str, Any]:
        """基于固定关账版做不落库测算，并追溯用户给定或Agent授权范围内提出的假设。"""
        return service.simulate_scenario(close_ref, session_ids, assumptions, mode,
                                         assumption_source, authorization_scope)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description='M3 read-only close analysis MCP server')
    parser.add_argument('--db', default='data/workbench.db',
                        help='启动时固定的工作台数据库路径（工具调用不能修改）')
    args = parser.parse_args()
    create_server(args.db).run(transport='stdio')


if __name__ == '__main__':
    main()
