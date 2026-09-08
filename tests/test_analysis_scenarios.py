import sqlite3

import pytest

from src.analysis.reader import AnalysisError, CloseReader
from src.analysis.scenarios import simulate_scenario


REF = {'month': '2026-09', 'version': 1}


def test_fee_scenario_changes_both_profit_measures_without_writing(closed_db):
    path, conn = closed_db
    before = conn.execute('SELECT snapshot FROM closes').fetchone()[0]
    result = simulate_scenario(CloseReader(path), REF, ['S1'], [{
        'type': 'session_fee', 'session_id': 'S1', 'field': 'slot_fee',
        'operation': 'delta', 'amount_cents': -20000}])
    session = result['data']['sessions']['S1']
    assert session['baseline']['amounts']['operating_profit']['cents'] == 182000
    assert session['scenario']['amounts']['operating_profit']['cents'] == 202000
    assert session['scenario']['amounts']['final_profit']['cents'] == 132000
    assert result['persisted'] is False
    assert result['assumption_trace']['source'] == 'user_provided'
    assert result['assumption_trace']['authorization_scope'] is None
    assert conn.execute('SELECT snapshot FROM closes').fetchone()[0] == before


def test_sku_scenario_uses_retained_units_and_existing_rounding(closed_db):
    path, _conn = closed_db
    result = simulate_scenario(CloseReader(path), REF, ['S1'], [{
        'type': 'sku_unit_cost', 'session_id': 'S1', 'sku': '学习机',
        'operation': 'set', 'unit_cost': '1970'}])
    session = result['data']['sessions']['S1']
    assert session['scenario']['amounts']['product_cost']['cents'] == 394000
    assert session['scenario']['amounts']['operating_profit']['cents'] == 188000
    assert session['scenario']['amounts']['final_profit']['cents'] == 118000


def test_scenario_rejects_duplicates_unsupported_and_not_applicable(closed_db):
    path, _conn = closed_db
    reader = CloseReader(path)
    duplicate = {'type': 'session_fee', 'session_id': 'S1', 'field': 'ad_spend',
                 'operation': 'set', 'amount_cents': 1}
    with pytest.raises(AnalysisError) as exc:
        simulate_scenario(reader, REF, ['S1'], [duplicate, duplicate])
    assert exc.value.code == 'INVALID_ASSUMPTION'
    with pytest.raises(AnalysisError) as exc:
        simulate_scenario(reader, REF, ['S2'], [{**duplicate, 'session_id': 'S2'}])
    assert exc.value.code == 'INVALID_ASSUMPTION'
    with pytest.raises(AnalysisError) as exc:
        simulate_scenario(reader, REF, ['S1'], [{**duplicate, 'field': 'sales'}])
    assert exc.value.code == 'UNSUPPORTED_ASSUMPTION'


def test_agent_proposed_scenario_echoes_and_enforces_authorization_scope(closed_db):
    path, _conn = closed_db
    reader = CloseReader(path)
    assumption = {'type': 'session_fee', 'session_id': 'S1', 'field': 'slot_fee',
                  'operation': 'delta', 'amount_cents': -20000}
    scope = {'session_ids': ['S1'], 'assumption_types': ['session_fee'],
             'fee_fields': ['slot_fee'], 'operations': ['delta'],
             'max_assumptions': 3, 'fee_delta_abs_max_cents': 20000}
    result = simulate_scenario(
        reader, REF, ['S1'], [assumption], assumption_source='agent_proposed',
        authorization_scope=scope)
    trace = result['assumption_trace']
    assert trace['source'] == 'agent_proposed'
    assert trace['authorization_scope'] == scope
    assert trace['scope_validation'] == 'matched'
    assert '不能证明用户授权' in trace['authorization_notice']

    with pytest.raises(AnalysisError, match='超出用户声明'):
        simulate_scenario(
            reader, REF, ['S1'], [{**assumption, 'amount_cents': -20001}],
            assumption_source='agent_proposed', authorization_scope=scope)


def test_agent_proposed_scenario_requires_declared_scope_and_sku_bounds(closed_db):
    path, _conn = closed_db
    reader = CloseReader(path)
    sku_assumption = {'type': 'sku_unit_cost', 'session_id': 'S1', 'sku': '学习机',
                      'operation': 'set', 'unit_cost': '1970'}
    with pytest.raises(AnalysisError, match='authorization_scope'):
        simulate_scenario(
            reader, REF, ['S1'], [sku_assumption], assumption_source='agent_proposed')

    scope = {'session_ids': ['S1'], 'assumption_types': ['sku_unit_cost'],
             'skus': ['学习机'], 'operations': ['set'], 'max_assumptions': 1,
             'sku_unit_cost_min': '1950', 'sku_unit_cost_max': '2000'}
    result = simulate_scenario(
        reader, REF, ['S1'], [sku_assumption], assumption_source='agent_proposed',
        authorization_scope=scope)
    assert result['assumption_trace']['scope_validation'] == 'matched'

    with pytest.raises(AnalysisError, match='超出用户声明'):
        simulate_scenario(
            reader, REF, ['S1'], [{**sku_assumption, 'unit_cost': '1949'}],
            assumption_source='agent_proposed', authorization_scope=scope)
