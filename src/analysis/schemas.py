"""JSON-facing type aliases used by the M3 MCP tool signatures."""

from __future__ import annotations

from typing import Any, TypedDict

from src.analysis import CONTRACT_VERSION

DEFAULT_MODE = 'current'
DEFAULT_GROUP_BY = 'month'
DEFAULT_PROFIT_METRIC = 'final_profit'
DEFAULT_SORT_BY = 'final_profit'
DEFAULT_SORT_ORDER = 'desc'
DEFAULT_OFFSET = 0
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_MONTHS = 12
MAX_ASSUMPTIONS = 20
ASSUMPTION_SOURCES = ('user_provided', 'agent_proposed')
ASSUMPTION_TYPES = ('session_fee', 'sku_unit_cost')
ASSUMPTION_OPERATIONS = ('set', 'delta')

DEFAULT_METRICS = (
    'sales', 'net_revenue', 'operating_profit', 'final_profit',
    'operating_margin', 'final_margin', 'ad_spend', 'ad_roi',
)
MODES = ('current', 'history')
GROUP_BY_VALUES = ('month', 'session', 'talent')
SORT_ORDERS = ('asc', 'desc')
EVIDENCE_TOPICS = ('pnl', 'sku', 'allocation', 'changes')
DIAGNOSTIC_RULES = ('final_loss', 'operating_profit_final_loss', 'major_components', 'period_change')
SCENARIO_FEE_FIELDS = (
    'commission', 'slot_fee', 'ad_spend', 'gift', 'insurance',
    'logistics', 'loss', 'warehouse', 'labor', 'management',
)


class CloseRef(TypedDict):
    month: str
    version: int


class AnalysisScope(TypedDict, total=False):
    close_refs: list[CloseRef]
    mode: str
    session_ids: list[str]
    talents: list[str]
    start_date: str
    end_date: str


JsonObject = dict[str, Any]


__all__ = [
    'AnalysisScope', 'CloseRef', 'JsonObject', 'CONTRACT_VERSION',
    'DEFAULT_MODE', 'DEFAULT_GROUP_BY', 'DEFAULT_PROFIT_METRIC',
    'DEFAULT_SORT_BY', 'DEFAULT_SORT_ORDER', 'DEFAULT_OFFSET', 'DEFAULT_LIMIT',
    'MAX_LIMIT', 'MAX_MONTHS', 'MAX_ASSUMPTIONS', 'DEFAULT_METRICS', 'MODES',
    'GROUP_BY_VALUES', 'SORT_ORDERS', 'EVIDENCE_TOPICS', 'DIAGNOSTIC_RULES',
    'SCENARIO_FEE_FIELDS', 'ASSUMPTION_SOURCES', 'ASSUMPTION_TYPES',
    'ASSUMPTION_OPERATIONS',
]
