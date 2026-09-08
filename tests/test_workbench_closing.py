import json

import pytest

from src.workbench.attribution import assign, assign_orders
from src.workbench.closing import close_month, get_close, preview_close, reopen_month
from src.workbench.costs import save_cost, set_applicability
from src.workbench.db import backup, connect, records
from src.workbench.imports import import_workbook
from tests.test_workbench_imports import db, workbook_bytes


def seed(conn):
    raw = workbook_bytes({
        '场次': [['S1', '甲', '2026-09-01 10:00', '2026-09-01 11:00'],
                 ['S2', '乙', '2026-09-01 11:00', '2026-09-01 12:00']],
        '订单行': [['O1', '1', '学习机', 2, '2026-09-01 10:30', '已结算', '2026-10-01', '9000', '300', '0', '10000', '1000', '0'],
                  ['O2', '1', '学习机', 3, '2026-09-01 11:30', '已结算', '2026-09-10', '3000', '100', '0', '3000', '0', '0']],
        '商品与履约': [['O1', '1', '2000', 2, 0, 0, '100', '30', '100', '50'],
                       ['O2', '1', '500', 3, 1, 1, '不适用', '0', '20', '500']],
        '达人费用': [['S1', '900', '500', '0', '最终结算单']],
        '投流': [['A1', '账号1', '计划1', '2026-09-01 10:30', '2026-09-01 11:30', '1200']],
        '月度费用': [['W', '2026-09', '仓储', '100', '直播承担部分'],
                      ['L', '2026-09', '人工', '300', '直播承担部分'],
                      ['M', '2026-09', '管理', '200', '直播承担部分'],
                      ['T', '2026-09', '损益税费', '150', 'BP损益确认'],
                      ['J', '2026-09', '其他结算调整', '50', '结算调整']],
    })
    result = import_workbook(conn, raw, '合成账例.xlsx')
    assert not result['issues'], result
    for row, sid in zip(records(conn, 'orders'), ['S1', 'S2']):
        assign_orders(conn, [row['id']], sid, basis='支付时间及运营记录', reason='人工确认')
    for sid, flags in [('S1', dict(talent=True, ads=True, slot=True, gift=True)),
                       ('S2', dict(talent=False, ads=False, slot=False, gift=False))]:
        set_applicability(conn, sid, flags, reason='本场业务范围')
    for row in records(conn, 'ads') + records(conn, 'monthly'):
        assign(conn, row['id'], {'S1': 1}, basis='BP指定本场承担', reason='确认分配')
    return raw


def test_full_close_reopen_history_and_costs(db):
    seed(db)
    p = preview_close(db, '2026-09')
    assert not p['issues'], p['issues']
    assert p['results']['S1']['operating_profit'] == 182000
    assert p['results']['S1']['final_profit'] == 112000
    # A partial refund without return retains all 2 units of cost.
    assert p['results']['S1']['product_cost'] == 400000
    # 3 shipped, 1 restored, 1 damaged: 1 sale unit + explicit damage expense.
    assert p['results']['S2']['product_cost'] == 50000
    assert p['results']['S2']['loss'] == 50000
    for sid in p['results']:
        assert sum(x['contribution'] for x in p['sku'] if x['session_id'] == sid) + p['public'][sid] == p['results'][sid]['final_profit']
    v1 = close_month(db, '2026-09', confirmed=True)
    assert v1['version'] == 1
    assert close_month(db, '2026-09', confirmed=True) == v1
    with pytest.raises(ValueError, match='重开'):
        assign_orders(db, [records(db, 'orders')[0]['id']], 'S2', basis='修订', reason='修订')
    with pytest.raises(ValueError):
        reopen_month(db, '2026-09', reason='')
    reopen_month(db, '2026-09', reason='按新受益范围调整')
    assert get_close(db, '2026-09') is None
    assert get_close(db, '2026-09', version=1) == v1
    monthly = records(db, 'monthly')[0]
    assign(db, monthly['id'], {'S1': 1, 'S2': 1}, basis='两场共同受益', reason='重新分摊')
    v2 = close_month(db, '2026-09', confirmed=True)
    assert v2['version'] == 2
    assert v2['results']['S1']['final_profit'] == 117000
    assert get_close(db, '2026-09', version=1) == v1


def test_unassigned_overallocation_and_stale_source(db):
    seed(db)
    ad = records(db, 'ads')[0]
    with pytest.raises(ValueError, match='合计'):
        assign(db, ad['id'], {'S1': 130000}, mode='amounts', basis='金额', reason='测试')
    # Source update retains old allocation for review, and blocks close until reconfirmed.
    raw = workbook_bytes({'投流': [['A1', '账号1', '计划1', '2026-09-01 10:30', '2026-09-01 11:30', '1201']]})
    assert import_workbook(db, raw, '更新.xlsx', confirm_updates=True, reason='账单更正')['accepted'] == 1
    assert any('来源金额变化' in x for x in preview_close(db, '2026-09')['issues'])
    with pytest.raises(ValueError):
        close_month(db, '2026-09', confirmed=True)
    assign(db, ad['id'], {'S1': 1}, basis='新账单', reason='确认新金额')
    db.execute('DELETE FROM assignments WHERE record_id=?', (records(db, 'orders')[0]['id'],))
    assert any('未归属' in x for x in preview_close(db, '2026-09')['issues'])


def test_missing_cost_unknown_na_and_confirmation(db):
    seed(db)
    fulfillment = records(db, 'fulfillment')[0]
    # Simulates a valid missing source field; unit cost is not allowed to be NA.
    data = fulfillment['data'] | {'unit_cost': None}
    db.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(data), fulfillment['id']))
    assert any('单位成本' in x for x in preview_close(db, '2026-09')['issues'])
    with pytest.raises(ValueError):
        close_month(db, '2026-09', confirmed=True)
    with pytest.raises(ValueError):
        close_month(db, '2026-09', confirmed=False)
    assert db.execute('SELECT COUNT(*) FROM closes').fetchone()[0] == 0


def test_failed_publish_rolls_back(db):
    seed(db)
    db.execute("CREATE TRIGGER fail_close BEFORE INSERT ON closes BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END")
    with pytest.raises(Exception, match='simulated'):
        close_month(db, '2026-09', confirmed=True)
    assert db.execute('SELECT COUNT(*) FROM closes').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM months').fetchone()[0] == 0


def test_backup_restores_committed_wal(tmp_path):
    path = tmp_path / 'source.db'
    conn = connect(path)
    conn.execute('PRAGMA journal_mode=WAL')
    seed(conn)
    close_month(conn, '2026-09', confirmed=True)
    dest = backup(conn, tmp_path / 'backup.db')
    restored = connect(dest)
    assert get_close(restored, '2026-09')['results']['S1']['final_profit'] == 112000
    restored.close(); conn.close()
