import json
from io import BytesIO

import pytest
from openpyxl import load_workbook

from src.workbench.app import render_page
from src.workbench.closing import close_month, preview_close, preview_operating_profit
from src.workbench.db import records
from src.workbench.imports import import_workbook
from tests.test_workbench_closing import seed
from tests.test_workbench_imports import db, workbook_bytes, SESSION


@pytest.mark.parametrize('monthly_state', ['absent', 'unassigned', 'unknown', 'changed'])
def test_monthly_state_does_not_block_operating_or_write_actuals(db, monthly_state):
    seed(db)
    monthly = records(db, 'monthly')
    if monthly_state in ('absent', 'unassigned'):
        for row in monthly:
            db.execute('DELETE FROM assignments WHERE record_id=?', (row['id'],))
        if monthly_state == 'absent':
            db.execute("DELETE FROM records WHERE kind='monthly'")
    else:
        row = monthly[0]
        data = row['data'] | {'amount': None if monthly_state == 'unknown' else 20000}
        db.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(data), row['id']))
    before = list(db.iterdump())
    preview = preview_operating_profit(db, '2026-09')
    assert not preview['issues']
    assert preview['results']['S1']['operating_profit'] == 182000
    assert preview['total']['operating_profit'] == 370000
    assert preview['persisted'] is False
    assert preview['monthly_fees_included'] is False
    assert 'final_profit' not in preview['total']
    assert all('final_profit' not in result for result in preview['results'].values())
    page = render_page(db, 'test', {'month': '2026-09', 'step': '4'})
    close_section = page.split('<section id="close">')[1].split('<section id="history">')[0]
    assert '经营利润预览 · 未关账' in close_section
    assert '1,820.00' in close_section
    assert '最终结算利润' not in close_section
    assert 'action="/close' not in close_section
    assert list(db.iterdump()) == before
    if monthly_state != 'absent':
        assert preview_close(db, '2026-09')['issues']
        with pytest.raises(ValueError):
            close_month(db, '2026-09', confirmed=True)


@pytest.mark.parametrize('kind,field,value', [
    ('orders', 'status', '待结算'),
    ('fulfillment', 'unit_cost', None),
    ('talent', 'commission', None),
    ('ads', 'amount', None),
])
def test_operating_preview_still_requires_direct_business_data(db, kind, field, value):
    seed(db)
    row = records(db, kind)[0]
    db.execute('UPDATE records SET data=? WHERE id=?',
               (json.dumps(row['data'] | {field: value}), row['id']))
    preview = preview_operating_profit(db, '2026-09')
    assert preview['issues'] and not preview['results'] and not preview['total']


def test_optional_monthly_sheet_can_be_imported_later_without_erasing_rows(db):
    wb = load_workbook(BytesIO(workbook_bytes({'场次': [SESSION]})))
    del wb['月度费用']
    stream = BytesIO()
    wb.save(stream)
    assert import_workbook(db, stream.getvalue(), '月中五表.xlsx')['accepted'] == 1
    later = workbook_bytes({'月度费用': [['W', '2026-09', '仓储', '100', '直播承担部分']]})
    assert import_workbook(db, later, '月末补充.xlsx')['accepted'] == 1
    assert len(records(db, 'sessions')) == 1
    assert records(db, 'monthly')[0]['data']['amount'] == 10000


def test_full_view_and_close_keep_original_dual_profit(db):
    seed(db)
    page = render_page(db, 'test', {'month': '2026-09', 'step': '4', 'profit_view': 'full'})
    close_section = page.split('<section id="close">')[1].split('<section id="history">')[0]
    assert '最终结算利润' in close_section and '1,120.00' in close_section
    assert 'name="confirmed"' in close_section
    with pytest.raises(ValueError):
        close_month(db, '2026-09', confirmed=False)
    closed = close_month(db, '2026-09', confirmed=True)
    assert closed['results']['S1']['final_profit'] == 112000
    assert closed['total']['final_profit'] == 300000
