import asyncio
from copy import deepcopy

from src.analysis import CONTRACT_VERSION
from src.analysis.mcp_server import create_server
from src.analysis.schemas import DEFAULT_LIMIT, DEFAULT_MODE, DEFAULT_SORT_BY
from src.analysis.service import AnalysisService


SCOPE = {'close_refs': [{'month': '2026-09', 'version': 1}]}


def without_clock(value):
    result = deepcopy(value)
    result.pop('as_of', None)
    return result


def test_shared_contract_supplies_defaults_and_envelope(closed_db):
    path, _conn = closed_db
    service = AnalysisService(path)
    result = service.query_performance(SCOPE)
    assert result['contract_version'] == CONTRACT_VERSION
    assert result['scope']['mode'] == DEFAULT_MODE
    assert result['data']['paging']['limit'] == DEFAULT_LIMIT
    assert result['data']['sort']['metric'] == DEFAULT_SORT_BY
    assert result['as_of']


def test_mcp_and_service_return_same_business_payload(closed_db):
    path, _conn = closed_db
    direct = AnalysisService(path).query_performance(
        SCOPE, group_by='session', metrics=['final_profit', 'final_margin'], limit=1)

    async def invoke():
        _content, structured = await create_server(path).call_tool(
            'workbench_query_performance', {
                'scope': SCOPE, 'group_by': 'session',
                'metrics': ['final_profit', 'final_margin'], 'limit': 1,
            })
        return structured

    through_mcp = asyncio.run(invoke())
    assert without_clock(through_mcp) == without_clock(direct)


def test_mcp_and_service_share_agent_scenario_trace_contract(closed_db):
    path, _conn = closed_db
    arguments = {
        'close_ref': {'month': '2026-09', 'version': 1},
        'session_ids': ['S1'],
        'assumptions': [{
            'type': 'session_fee', 'session_id': 'S1', 'field': 'slot_fee',
            'operation': 'delta', 'amount_cents': -20000,
        }],
        'assumption_source': 'agent_proposed',
        'authorization_scope': {
            'session_ids': ['S1'], 'assumption_types': ['session_fee'],
            'fee_fields': ['slot_fee'], 'operations': ['delta'],
            'max_assumptions': 2, 'fee_delta_abs_max_cents': 20000,
        },
    }
    direct = AnalysisService(path).simulate_scenario(**arguments)

    async def invoke():
        _content, structured = await create_server(path).call_tool(
            'workbench_simulate_scenario', arguments)
        return structured

    through_mcp = asyncio.run(invoke())
    assert without_clock(through_mcp) == without_clock(direct)
    assert through_mcp['assumption_trace']['source'] == 'agent_proposed'


def test_errors_use_same_contract_without_leaking_database_path(tmp_path):
    path = tmp_path / 'private-close.db'
    direct = AnalysisService(path).list_close_versions()

    async def invoke():
        _content, structured = await create_server(path).call_tool(
            'workbench_list_close_versions', {})
        return structured

    through_mcp = asyncio.run(invoke())
    assert direct['contract_version'] == CONTRACT_VERSION
    assert without_clock(through_mcp) == without_clock(direct)
    assert direct['error']['code'] == 'NO_CLOSED_DATA'
    assert 'scope' not in direct
    assert 'accounting_rule_version' not in direct
    assert 'analysis_rule_version' not in direct
    assert str(path) not in str(direct)
