import json

import pytest

from src.workbench.attribution import assign
from src.workbench.batches import prepare_multi_cost_fill, apply_batch
from src.workbench.db import records, dumps
from src.workbench.imports import (
    batch_member_ids,
    import_workbook,
    preview_workbook,
    recent_batches,
)
from src.workbench import m2_ui
from tests.test_workbench_closing import seed
from tests.test_workbench_imports import db, workbook_bytes


def test_new_session_mode_generates_internal_ids_and_keeps_source_mapping(db):
    raw = workbook_bytes({
        '场次': [['SOURCE-1', '达人甲', '2026-09-02 10:00', '2026-09-02 11:00']],
        '达人费用': [['SOURCE-1', '100', '不适用', '0', '结算单']],
    })
    preview = preview_workbook(db, raw, import_mode='new')
    internal = preview['session_mappings']['SOURCE-1']
    assert internal.startswith('LS-20260902-1000-')
    assert internal != 'SOURCE-1'

    result = import_workbook(db, raw, '新场.xlsx', import_mode='new')
    assert not result['issues']
    assert records(db, 'sessions')[0]['business_key'] == internal
    assert records(db, 'talent')[0]['data']['session_id'] == internal
    mapping = db.execute('SELECT * FROM session_source_mappings').fetchone()
    assert mapping['source_session_id'] == 'SOURCE-1'
    assert mapping['internal_session_id'] == internal


def test_new_mode_requires_explicit_basis_for_existing_source_id(db):
    import_workbook(db, workbook_bytes({
        '场次': [['S1', '原场次', '2026-09-01 10:00', '2026-09-01 11:00']],
    }), '旧场.xlsx')
    raw = workbook_bytes({
        '场次': [['S1', '并行新场', '2026-09-01 12:00', '2026-09-01 13:00']],
    })
    preview = preview_workbook(db, raw, import_mode='new')
    assert preview['warnings'] and not preview['issues']
    rejected = import_workbook(db, raw, '冲突新场.xlsx', import_mode='new')
    assert any('确认确为新场次' in issue for issue in rejected['issues'])
    accepted = import_workbook(
        db, raw, '冲突新场.xlsx', import_mode='new',
        confirm_new_session=True, duplicate_basis='运营排期确认是并行新场')
    assert not accepted['issues']
    assert len(records(db, 'sessions')) == 2


def test_new_mode_does_not_turn_existing_order_into_an_update(db):
    original = workbook_bytes({
        '场次': [['S1', '原场次', '2026-09-01 10:00', '2026-09-01 11:00']],
        '订单行': [['O1', '1', 'SKU-A', 1, '2026-09-01 10:20', '待结算', None,
                 None, None, None, None, None, None]],
    })
    import_workbook(db, original, '原场.xlsx')
    incoming = workbook_bytes({
        '场次': [['NEW', '新场次', '2026-09-02 10:00', '2026-09-02 11:00']],
        '订单行': [['O1', '1', 'SKU-A', 1, '2026-09-01 10:20', '已结算', '2026-09-03',
                 '1000', '10', '0', '1000', '0', '0']],
    })
    preview = preview_workbook(db, incoming, import_mode='new')
    assert any('已有订单' in issue and '补充或更正' in issue for issue in preview['issues'])
    assert preview['conflict_count'] == 1
    assert records(db, 'orders')[0]['data']['status'] == '待结算'


def test_batch_membership_survives_later_record_update(db):
    first = import_workbook(db, workbook_bytes({
        '场次': [['S1', '达人甲', '2026-09-01 10:00', '2026-09-01 11:00']],
        '订单行': [['O1', '1', 'SKU-A', 1, '2026-09-01 10:20', '待结算', None,
                 None, None, None, None, None, None]],
    }), '首次.xlsx', import_mode='legacy')
    order_id = records(db, 'orders')[0]['id']
    second = import_workbook(db, workbook_bytes({
        '订单行': [['O1', '1', 'SKU-A', 1, '2026-09-01 10:20', '已结算', '2026-09-03',
                 '1000', '10', '0', '1000', '0', '0']],
    }), '补充结算.xlsx', import_mode='supplement', selected_session_ids=['S1'],
        confirm_updates=True, reason='结算单补充')
    assert batch_member_ids(db, first['batch_id'], 'orders') == [order_id]
    assert batch_member_ids(db, second['batch_id'], 'orders') == [order_id]
    assert records(db, 'orders')[0]['batch_id'] == second['batch_id']
    batches = recent_batches(db)
    assert batches[0]['update_count'] == 1
    assert batches[1]['new_count'] == 2


def test_multi_sku_fill_is_scoped_and_saves_standards_atomically(db):
    seed(db)
    order_rows = records(db, 'orders')
    fulfillment = records(db, 'fulfillment')
    db.execute('UPDATE records SET data=? WHERE id=?',
               (dumps(order_rows[1]['data'] | {'sku':'SKU-B'}), order_rows[1]['id']))
    for row in fulfillment:
        db.execute('UPDATE records SET data=? WHERE id=?',
                   (dumps(row['data'] | {'unit_cost':None}), row['id']))
    preview = prepare_multi_cost_fill(
        db, [r['id'] for r in order_rows], {'学习机':'2100', 'SKU-B':'600'},
        month='2026-09', save_as_standard=True, basis='本月采购结算单')
    assert preview['count'] == 2
    apply_batch(db, preview, reason='核对两项SKU成本')
    costs = records(db, 'fulfillment')
    assert [r['data']['unit_cost'] for r in costs] == ['2100', '600']
    assert all(r['data'].get('_cost_source') for r in costs)
    standards = list(db.execute('SELECT sku,data FROM cost_standards ORDER BY sku'))
    assert len(standards) == 2


def test_monthly_reuse_entry_only_appears_with_valid_weight_source(db):
    assert '月度费用可后补' in m2_ui.render_monthly_reuse(db, 'csrf', {'month':'2026-09'})
    seed(db)
    monthly = records(db, 'monthly')
    db.execute('DELETE FROM assignments WHERE record_id IN '
               "(SELECT id FROM records WHERE kind='monthly')")
    assert '先完成一笔费用的权重分摊' in m2_ui.render_monthly_reuse(
        db, 'csrf', {'month':'2026-09'})
    assign(db, monthly[0]['id'], {'S1':1}, basis='直播受益权重', reason='确认')
    page = m2_ui.render_monthly_reuse(db, 'csrf', {'month':'2026-09'})
    assert '沿用已有分摊方式' in page
    assert '仅复用受益范围和权重' in page
