from src.analysis.metrics import compare_performance, query_performance
from src.analysis.reader import CloseReader


SCOPE = {'close_refs': [{'month': '2026-09', 'version': 1}]}


def test_query_recalculates_aggregate_ratios_and_returns_stable_ranks(closed_db):
    path, _conn = closed_db
    result = query_performance(CloseReader(path), SCOPE, group_by='session',
                               metrics=['sales', 'final_profit', 'final_margin'], limit=1)
    assert result['status'] == 'ok'
    assert result['scope']['session_count'] == 2
    assert result['data']['totals']['sales']['cents'] == 1_200_000
    assert result['data']['totals']['final_profit']['cents'] == 300_000
    assert result['data']['totals']['final_margin']['decimal'] == '0.258621'
    assert result['data']['paging'] == {
        'total_count': 2, 'returned_count': 1, 'offset': 0, 'limit': 1,
        'has_more': True, 'next_offset': 1}
    assert result['data']['groups'][0]['key'] == 'S2'
    assert result['data']['groups'][0]['rank'] == 1


def test_talent_and_date_filters_use_session_start(closed_db):
    path, _conn = closed_db
    scope = SCOPE | {'talents': ['乙'], 'start_date': '2026-09-01', 'end_date': '2026-09-01'}
    result = query_performance(CloseReader(path), scope, group_by='talent', metrics=['session_count'])
    assert result['data']['totals']['session_count'] == 1
    assert result['data']['groups'][0]['key'] == '乙'


def test_profit_bridge_is_exact(closed_db):
    path, _conn = closed_db
    reader = CloseReader(path)
    base = SCOPE | {'session_ids': ['S2']}
    current = SCOPE | {'session_ids': ['S1']}
    result = compare_performance(reader, base, current)
    bridge = result['data']['profit_bridge']
    assert sum(x['profit_contribution']['cents'] for x in bridge) == (
        result['data']['amounts']['final_profit']['delta']['cents'])
    assert result['data']['session_count']['base'] == 1
