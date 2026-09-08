import json

import pytest

from src.workbench.attribution import assign, assign_orders, filter_sources
from src.workbench.closing import close_month, preview_close, reopen_month
from src.workbench.costs import save_cost, set_applicability
from src.workbench.db import records
from src.workbench.imports import import_workbook, normalize
from tests.test_workbench_closing import seed
from tests.test_workbench_imports import db, workbook_bytes, SESSION


def test_overlapping_export_no_double_booking_and_bad_batch_no_updates(db):
    first = workbook_bytes({'场次':[SESSION]})
    import_workbook(db,first,'first.xlsx')
    overlap = workbook_bytes({'场次':[SESSION,['S2','乙','2026-09-01 11:00','2026-09-01 12:00']]})
    result = import_workbook(db,overlap,'overlap.xlsx')
    assert result['accepted'] == 1 and result['unchanged'] == 1
    bad = workbook_bytes({'场次':[['S1','修订达人',*SESSION[2:]],['S3','丙','坏时间','2026-09-01 12:00']]})
    assert import_workbook(db,bad,'bad.xlsx',confirm_updates=True,reason='修订')['accepted'] == 0
    assert records(db,'sessions')[0]['data']['talent'] == SESSION[1]


@pytest.mark.parametrize('kind,field,value,phrase',[
    ('orders','status','待结算','最终结算'),
    ('orders','sales',None,'金额未取得'),
    ('fulfillment','logistics',None,'金额未取得'),
    ('fulfillment','loss','不适用','不能标为不适用'),
    ('talent','commission',None,'最终佣金'),
    ('talent','evidence',None,'结算依据'),
])
def test_required_business_inputs_block_close(db,kind,field,value,phrase):
    seed(db)
    # Damaged return is on O2; other missing examples use S1.
    row = records(db,kind)[1 if field=='loss' else 0]
    data = row['data'] | {field:value}
    db.execute('UPDATE records SET data=? WHERE id=?',(json.dumps(data),row['id']))
    assert any(phrase in issue for issue in preview_close(db,'2026-09')['issues'])
    with pytest.raises(ValueError):
        close_month(db,'2026-09',confirmed=True)


def test_penny_negative_adjustments_saved_and_conserved(db):
    seed(db)
    rid = save_cost(db,'monthly',{'source_id':'negative','month':'2026-09','category':'其他结算调整',
                                  'amount':'-0.01','basis':'直播结算追回'},reason='确认负调整')
    decision = assign(db,rid,{'S2':1,'S1':1},basis='等权受益',reason='确认')
    assert decision['targets'] == {'S1':-1,'S2':0}
    assert decision['tail_cents'] == {'S2':0,'S1':1}
    p = close_month(db,'2026-09',confirmed=True)
    assert p['results']['S1']['final_profit'] == 111999
    assert sum(p['assignments'][str(rid)]['targets'].values()) == -1


def test_batch_assignment_atomic_and_date_talent_filters(db):
    seed(db)
    first = records(db,'orders')[0]
    with pytest.raises(ValueError):
        assign_orders(db,[first['id'],9999],'S2',basis='测试批量原子性',reason='测试')
    assert preview_close(db,'2026-09')['results']['S1']['sales'] == 900000
    assert len(filter_sources(db,date_prefix='2026-09-01',talent='甲',time_from='2026-09-01 10:00')) == 1
    assert filter_sources(db,time_from='2026-09-02') == []


def test_closed_mutations_and_new_import_require_whole_month_reopen(db):
    seed(db)
    close_month(db,'2026-09',confirmed=True)
    with pytest.raises(ValueError,match='重开'):
        save_cost(db,'monthly',{'source_id':'new','month':'2026-09','category':'仓储','amount':'0','basis':'直播承担'},reason='新增')
    with pytest.raises(ValueError,match='重开'):
        set_applicability(db,'S1',dict(talent=True,ads=True,slot=True,gift=True),reason='修改')
    late = workbook_bytes({'场次':[['S3','丙','2026-09-02 10:00','2026-09-02 11:00']]})
    assert import_workbook(db,late,'late.xlsx')['accepted'] == 0
    reopen_month(db,'2026-09',reason='补场次')
    assert import_workbook(db,late,'late.xlsx')['accepted'] == 1


def test_unit_cost_na_and_return_quantity_cannot_bypass(db):
    seed(db)
    row = records(db,'fulfillment')[0]['data']
    with pytest.raises(ValueError):
        normalize('fulfillment', row | {'unit_cost':'不适用'})
    with pytest.raises(ValueError,match='退回数量'):
        normalize('fulfillment', row | {'restored':3})


def test_cross_session_ad_weights_and_explicit_amounts(db):
    seed(db)
    set_applicability(db,'S2',dict(talent=False,ads=True,slot=False,gift=False),reason='两场均承担投流')
    ad = records(db,'ads')[0]
    assign(db,ad['id'],{'S1':1,'S2':1},basis='人工审核跨场小时消耗等权建议',reason='确认分摊')
    weighted = preview_close(db,'2026-09')
    assert not weighted['issues']
    assert weighted['results']['S1']['ad_spend'] == weighted['results']['S2']['ad_spend'] == 60000
    assign(db,ad['id'],{'S1':80000,'S2':40000},mode='amounts',basis='根据账单明确金额',reason='更正分配')
    explicit = close_month(db,'2026-09',confirmed=True)
    assert explicit['results']['S1']['ad_spend'] == 80000
    assert explicit['results']['S2']['ad_spend'] == 40000
    assert explicit['total']['final_profit'] == weighted['total']['final_profit'] == 300000
