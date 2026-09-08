import json
from copy import deepcopy

import pytest

from src.workbench.attribution import assignments, assign_orders
from src.workbench.closing import close_month, preview_close, reopen_month
from src.workbench.db import records
from src.workbench.recommendations import generate, latest, save_ad_link
from src.workbench.batches import prepare_assignments, apply_batch, prepare_flags, prepare_cost_fill, prepare_allocation_reuse
from src.workbench.costs import save_standard
from src.workbench.imports import import_workbook
from tests.test_workbench_imports import db, workbook_bytes
from tests.test_workbench_closing import seed


def unassign(db):
    seed(db)
    db.execute("DELETE FROM assignments WHERE record_id IN (SELECT id FROM records WHERE kind='orders')")
    return [r['id'] for r in records(db,'orders')]


def test_rules_are_candidates_only_and_batch_confirm_different_sessions(db):
    ids = unassign(db)
    recs = generate(db,ids,session_ids=['S1','S2'],source_scope_confirmed=True)
    assert [r['data']['targets'] for r in recs] == [{'S1':1},{'S2':1}]
    assert all('score' not in r['data'] for r in recs)
    assert all(i not in assignments(db) for i in ids)
    p = prepare_assignments(db,ids,use_candidates=True)
    assert p['count'] == 2
    apply_batch(db,p,reason='按支付时间及运营范围统一确认')
    assert preview_close(db,'2026-09')['results']['S1']['final_profit'] == 112000
    rows = list(db.execute('SELECT * FROM changes WHERE operation_id=?',(p['operation_id'],)))
    assert len(rows) == 2
    with pytest.raises(ValueError,match='已处理'):
        apply_batch(db,p,reason='重复点击')


def test_rerun_preserves_original_and_manual_decision(db):
    ids = unassign(db)
    first = generate(db,ids,session_ids=['S1','S2'],source_scope_confirmed=True)
    apply_batch(db,prepare_assignments(db,ids,use_candidates=True),reason='确认')
    before = deepcopy(assignments(db))
    generate(db,ids,session_ids=['S2'],source_scope_confirmed=True)
    assert assignments(db) == before
    assert json.loads(db.execute('SELECT data FROM recommendations WHERE id=?',(first[0]['id'],)).fetchone()[0]) == first[0]['data']
    with pytest.raises(ValueError,match='已确认'):
        prepare_assignments(db,ids,session_id='S2')


def test_stale_preview_rolls_back_all_and_source_change_needs_review(db):
    ids = unassign(db)
    generate(db,ids,session_ids=['S1','S2'],source_scope_confirmed=True)
    p = prepare_assignments(db,ids,use_candidates=True)
    changed = records(db,'orders')[1]['data'] | {'paid_at':'2026-09-01 10:20:00'}
    db.execute('UPDATE records SET data=? WHERE id=?',(json.dumps(changed),ids[1]))
    with pytest.raises(ValueError,match='变化'):
        apply_batch(db,p,reason='过期预览')
    assert all(i not in assignments(db) for i in ids)
    assign_orders(db,[ids[0]],'S1',basis='人工核对',reason='确认')
    changed = records(db,'orders')[0]['data'] | {'paid_at':'2026-09-01 11:20:00'}
    db.execute('UPDATE records SET data=? WHERE id=?',(json.dumps(changed),ids[0]))
    assert any('需复核' in x for x in preview_close(db,'2026-09')['issues'])


@pytest.mark.parametrize('paid,expected', [('2026-09-01 11:00:00','边界'),('2026-09-01 09:30:00','未落入')])
def test_boundaries_and_no_match_stay_exceptions(db,paid,expected):
    ids = unassign(db)
    row = records(db,'orders')[0]
    db.execute('UPDATE records SET data=? WHERE id=?',(json.dumps(row['data']|{'paid_at':paid}),ids[0]))
    r = generate(db,[ids[0]],session_ids=['S1','S2'],source_scope_confirmed=True)[0]
    assert any(expected in x for x in r['data']['issues'])
    assert not r['data']['targets']
    with pytest.raises(ValueError):
        prepare_assignments(db,[ids[0]],use_candidates=True)


def test_ad_link_full_coverage_and_gap(db):
    seed(db)
    ad = records(db,'ads')[0]
    assert generate(db,[ad['id']],month='2026-09')[0]['data']['issues']
    save_ad_link(db,'2026-09','账号1','计划1',['S1','S2'],reason='确认受益范围')
    r = generate(db,[ad['id']],month='2026-09')[0]
    assert r['data']['targets'] == {'S1':1800,'S2':1800}
    save_ad_link(db,'2026-09','账号1','计划1',['S1'],reason='缩小范围')
    r = generate(db,[ad['id']],month='2026-09')[0]
    assert any('未覆盖' in x for x in r['data']['issues'])
    assert not r['data']['targets']


def test_confirmed_cost_standard_fills_only_missing_and_preserves_m1(db):
    seed(db)
    close_month(db,'2026-09',confirmed=True)
    with pytest.raises(ValueError,match='重开'):
        save_standard(db,'学习机','2026-09','2000',reason='月度标准')
    reopen_month(db,'2026-09',reason='补成本来源')
    f = records(db,'fulfillment')[0]
    db.execute('UPDATE records SET data=? WHERE id=?',(json.dumps(f['data']|{'unit_cost':None}),f['id']))
    standard = save_standard(db,'学习机','2026-09','2000',reason='BP确认本月适用')
    p = prepare_cost_fill(db,standard)
    assert p['count'] == 1
    apply_batch(db,p,reason='补齐空缺成本')
    assert records(db,'fulfillment')[1]['data']['unit_cost'] == '500'
    assert close_month(db,'2026-09',confirmed=True)['results']['S1']['final_profit'] == 112000


def test_batch_flags_and_weight_reuse_are_auditable(db):
    seed(db)
    p = prepare_flags(db,['S1','S2'],dict(talent=False,ads=False,slot=False,gift=False))
    apply_batch(db,p,reason='统一变更，费用冲突仍由关账阻断')
    assert len(list(db.execute('SELECT * FROM changes WHERE operation_id=?',(p['operation_id'],)))) == 2
    monthly = records(db,'monthly')
    p = prepare_allocation_reuse(db,monthly[0]['id'],[monthly[1]['id'],monthly[2]['id']])
    apply_batch(db,p,reason='确认相同受益范围',allow_overwrite=True)
    decisions = assignments(db)
    assert decisions[monthly[1]['id']]['targets'] == {'S1':30000}
    assert decisions[monthly[2]['id']]['targets'] == {'S1':20000}
