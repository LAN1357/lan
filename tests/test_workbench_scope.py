"""Scope must survive navigation and must never turn missing costs into profit."""
import json
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit, urlencode
from urllib.request import urlopen

import pytest

from src.workbench import m2_ui
from src.workbench.app import render_page
from src.workbench.attribution import assign
from src.workbench.closing import preview_operating_profit, preview_close, close_month
from src.workbench.db import records, dumps, connect
from src.workbench.imports import import_workbook
from src.workbench.scope import resolve_scope, scoped_records
from src.workbench.workflow import order_queue
from src.workbench.recommendations import save_ad_link
from tests.test_workbench_imports import db, workbook_bytes
from tests.test_workbench_closing import seed
from tests.test_workbench_app import local_app
from tests.test_workbench_m2_app import client

Q = {'month':'2026-09', 'work_scope':'session', 'scope_session_id':'S1'}


class Controls(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links, self.forms = [], []
        self.current = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag=='a':
            self.links.append(attrs)
        if tag=='form':
            self.current = {'attrs':attrs, 'inputs':[]}
            self.forms.append(self.current)
        if tag in ('input','select') and self.current is not None:
            self.current['inputs'].append(attrs)

    def handle_endtag(self, tag):
        if tag=='form':
            self.current = None


def invalidate_other_cost(db):
    row = records(db,'fulfillment')[1]
    db.execute('UPDATE records SET data=? WHERE id=?', (dumps(row['data'] | {'unit_cost':None}), row['id']))


def test_complete_session_is_not_blocked_by_other_session_but_full_close_is(db):
    seed(db)
    invalidate_other_cost(db)
    before = list(db.iterdump())
    result = preview_operating_profit(db,'2026-09',['S1'])
    assert not result['issues']
    assert result['scope']=='sessions' and result['session_ids']==['S1']
    assert result['total']['operating_profit']==182000
    assert set(result['results'])=={'S1'} and 'final_profit' not in result['total']
    assert preview_operating_profit(db,'2026-09',['S2'])['issues']
    assert preview_close(db,'2026-09')['issues']
    with pytest.raises(ValueError):
        close_month(db,'2026-09',confirmed=True)
    assert list(db.iterdump())==before
    page = render_page(db,'csrf',Q|{'step':'4'})
    preview = page.split('<section id="close">')[1].split('<section id="history">')[0]
    assert '局部结果，非全月利润' in preview and '1,820.00' in preview
    assert 'action="/close' not in preview and 'O2' not in preview


@pytest.mark.parametrize('ids', [[], ['missing'], ['S1','missing'], 'S1', ['OCT']])
def test_invalid_or_empty_selection_never_falls_back_to_whole_month(db, ids):
    seed(db)
    import_workbook(db,workbook_bytes({'场次':[['OCT','丙','2026-10-01 10:00','2026-10-01 11:00']]}),'十月.xlsx')
    if ids==[]:
        result = preview_operating_profit(db,'2026-09',ids)
        assert result['issues'] and not result['results'] and not result['total']
    else:
        with pytest.raises(ValueError):
            preview_operating_profit(db,'2026-09',ids)


def test_unassigned_record_of_uncertain_scope_still_blocks_even_with_different_payment_time(db):
    seed(db)
    row = records(db,'orders')[1]
    db.execute('DELETE FROM assignments WHERE record_id=?',(row['id'],))
    result = preview_operating_profit(db,'2026-09',['S1'])
    assert result['issues'] and not result['total']
    assert result['unresolved'][0]['blocks_preview'] is True
    assert row['id'] in {r['id'] for r in order_queue(db,Q|{'order_status':'all'})[0]}


def test_unassigned_known_other_scope_is_disclosed_but_does_not_block(db):
    seed(db)
    raw = workbook_bytes({'订单行':[['OTHER','1','SKU',1,'2026-10-01 10:00','待结算',None,None,None,None,None,None,None]]})
    imported = import_workbook(db,raw,'其他场订单.xlsx',import_mode='supplement',selected_session_ids=['S2'])
    assert not imported['issues']
    result = preview_operating_profit(db,'2026-09',['S1'])
    assert not result['issues'] and result['total']['operating_profit']==182000
    assert result['unresolved'][0]['blocks_preview'] is False
    assert 'OTHER' not in [r['data']['order_id'] for r in order_queue(db,Q|{'order_status':'all'})[0]]
    assert preview_close(db,'2026-09')['issues']


def test_ad_explicit_scope_can_exclude_unrelated_unallocated_record(db):
    seed(db)
    import_workbook(db,workbook_bytes({'投流':[['OTHER-AD','账号2','计划2','2026-09-01 11:00','2026-09-01 12:00','200']]}),'其他投流.xlsx')
    save_ad_link(db,'2026-09','账号2','计划2',['S2'],reason='运营确认来源范围')
    result = preview_operating_profit(db,'2026-09',['S1'])
    assert not result['issues'] and result['unresolved'][0]['blocks_preview'] is False
    assert [r['business_key'] for r in scoped_records(db,Q,('ads',))]==['A1']


def test_selected_preview_validates_entire_shared_ad_allocation(db):
    seed(db)
    ad = records(db,'ads')[0]
    assign(db,ad['id'],{'S1':1,'S2':1},basis='两场共用',reason='核对完整分配')
    assert not preview_operating_profit(db,'2026-09',['S1'])['issues']
    data = json.loads(db.execute('SELECT data FROM assignments WHERE record_id=?',(ad['id'],)).fetchone()[0])
    data['targets']['S2'] += 1
    db.execute('UPDATE assignments SET data=? WHERE record_id=?',(dumps(data),ad['id']))
    result = preview_operating_profit(db,'2026-09',['S1'])
    assert any('不守恒' in issue for issue in result['issues']) and not result['total']


def cost_only_batch(db):
    raw = workbook_bytes({'商品与履约':[['O1','1','2001',2,0,0,'100','30','100','50']]})
    result = import_workbook(db,raw,'补成本.xlsx',import_mode='supplement',confirm_updates=True,reason='更新采购成本')
    assert not result['issues']
    return Q | {'work_scope':'batch','batch_id':str(result['batch_id']),'batch_action':'changed'}


def test_cost_only_batch_resolves_orders_and_previews_complete_session(db):
    seed(db)
    q = cost_only_batch(db)
    assert resolve_scope(db,q)['session_ids']=={'S1'}
    assert [r['data']['order_id'] for r in order_queue(db,q|{'order_status':'all'})[0]]==['O1']
    assert len(scoped_records(db,q,('fulfillment',)))==1
    invalidate_other_cost(db)
    page = render_page(db,'csrf',q|{'step':'4','search':'does-not-match'})
    preview = page.split('<section id="close">')[1].split('<section id="history">')[0]
    assert '1,818.00' in preview and 'S2' not in preview
    assert '当前范围 1 个订单行' in m2_ui.render_multi_costs(db,'csrf',q)


@pytest.mark.parametrize('mode',['session','batch'])
def test_all_steps_links_and_get_forms_keep_scope(db,mode):
    seed(db)
    q = Q if mode=='session' else cost_only_batch(db)
    for step in ('1','2','3','4'):
        page = render_page(db,'csrf',q|{'step':step})
        controls = Controls(page)
        links = [l for l in controls.links if 'step-link' in l.get('class','') or l.get('class')=='button' and l.get('href','').startswith('/workbench?')]
        assert links
        for link in links:
            query = parse_qs(urlsplit(link['href']).query)
            assert query['work_scope']==[mode]
            assert query['scope_session_id']==['S1']
            if mode=='batch':
                assert query['batch_id']==[q['batch_id']] and query['batch_action']==['changed']
        for form in controls.forms:
            if form['attrs'].get('method')!='get':
                continue
            inputs = form['inputs']
            names = [i.get('name') for i in inputs]
            # Changing month or scope is intentional; other GET forms must carry scope.
            if any(i.get('name') in ('month','work_scope') and 'type' not in i for i in inputs):
                continue
            assert 'work_scope' in names and len(names)==len(set(names))
            assert next(i for i in inputs if i.get('name')=='work_scope')['value']==mode


def test_direct_fee_worklist_and_flags_are_scoped_but_monthly_is_labelled(db):
    seed(db)
    sources = scoped_records(db,Q,('fulfillment','talent','monthly'))
    assert not any(r['kind']=='fulfillment' and r['data']['order_id']=='O2' for r in sources)
    assert len([r for r in sources if r['kind']=='monthly'])==5
    fees = m2_ui.render_fees(db,'csrf',Q|{'fee_status':'all'})
    assert 'O2' not in fees and '整月共享' in fees
    flags = m2_ui.render_reuse(db,'csrf',Q).split('<details id="ad-scope"')[0]
    assert 'S2' not in flags and 'S1' in flags


def test_http_scope_survives_bad_filter_and_rejects_outside_batch_edits(tmp_path):
    with local_app(tmp_path) as (server,path):
        conn = connect(path)
        seed(conn)
        base,post = client(server)
        with pytest.raises(HTTPError) as err:
            urlopen(base+'/workbench?'+urlencode(Q|{'step':'3','time_from':'bad'}))
        page = err.value.read().decode()
        assert '保留处理范围' in page and '当前处理范围：所选场次：S1' in page
        with pytest.raises(HTTPError) as err:
            urlopen(base+'/workbench?'+urlencode(Q|{'scope_session_id':'missing'}))
        page = err.value.read().decode()
        assert '当前未展示业务数据' in page and '<section id="close">' not in page
        other_order = records(conn,'orders')[1]
        other_cost = records(conn,'fulfillment')[1]
        before = list(conn.iterdump())
        attempts = [
            {'operation':'flags','session_id':['S2'],'talent':'false','ads':'false','slot':'false','gift':'false'},
            {'operation':'cost_edit','record_id':[other_cost['id']],'cost_field':'logistics','value':'10'},
            {'operation':'multi_unit_cost','order_id':[other_order['id']],'sku':['学习机'],'unit_cost:学习机':'10'},
        ]
        for attempt in attempts:
            with pytest.raises(HTTPError) as err:
                post('m2-preview',Q|attempt)
            assert err.value.code==400
            assert '超出当前处理范围' in err.value.read().decode()
        assert list(conn.iterdump())==before
        conn.close()
