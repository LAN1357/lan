import pytest

from src.analysis.evidence import compare_close_versions, get_session_evidence
from src.analysis.reader import AnalysisError, CloseReader
from src.workbench.attribution import assign
from src.workbench.closing import close_month, reopen_month
from src.workbench.db import records


REF = {'month': '2026-09', 'version': 1}


def test_session_pnl_sku_allocation_and_changes_are_narrow_views(closed_db):
    path, _conn = closed_db
    reader = CloseReader(path)
    pnl = get_session_evidence(reader, REF, 'S1', topic='pnl')
    assert pnl['data']['items'][0]['amounts']['final_profit']['cents'] == 112000
    sku = get_session_evidence(reader, REF, 'S1', topic='sku')
    assert sum(x['contribution'] for x in sku['data']['items']) == 112000
    allocations = get_session_evidence(reader, REF, 'S1', topic='allocation', limit=2)
    assert allocations['data']['paging']['total_count'] > 2
    assert all(x['source_ref']['section'] == 'assignments' for x in allocations['data']['items'])
    changes = get_session_evidence(reader, REF, 'S1', topic='changes')
    assert changes['data']['items']
    assert all('reason' in x for x in changes['data']['items'])


def test_compare_two_close_versions_by_stable_keys(closed_db):
    path, conn = closed_db
    reopen_month(conn, '2026-09', reason='改共享费用范围')
    monthly = records(conn, 'monthly')[0]
    assign(conn, monthly['id'], {'S1': 1, 'S2': 1}, basis='两场共同受益', reason='重新分摊')
    close_month(conn, '2026-09', confirmed=True)
    reader = CloseReader(path)
    result = compare_close_versions(reader, '2026-09', 1, 2)
    changed = {x['session_id']: x for x in result['data']['session_changes']}
    assert changed['S1']['final_profit_delta']['cents'] == 5000
    assert changed['S2']['final_profit_delta']['cents'] == -5000
    assert '重新分摊' in result['data']['audit_reasons']
    with pytest.raises(AnalysisError) as exc:
        reader.load_ref({'month': '2026-09', 'version': 1})
    assert exc.value.code == 'VERSION_CHANGED'
