import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from src.workbench.db import connect, records, dumps
from src.workbench.attribution import assignments
from src.workbench.closing import get_close
from src.workbench.costs import set_applicability
from src.workbench.app import render_page
from src.workbench import m2_ui
from src.workbench.recommendations import generate, save_ad_link
from tests.test_workbench_app import local_app
from tests.test_workbench_closing import seed
from tests.test_workbench_m2 import unassign


def client(server):
    base = f'http://127.0.0.1:{server.server_port}'
    csrf = re.search('name="csrf"[^>]*value="([^"]+)"',urlopen(base+'/workbench').read().decode())[1]
    def post(action, values):
        raw = urlencode({'csrf':csrf,'month':'2026-09',**values},doseq=True).encode()
        return urlopen(Request(base+'/'+action,data=raw)).read().decode()
    return base,post


def token(page):
    return re.search('name="preview_token"[^>]*value="([^"]+)"',page)[1]


def test_http_rules_preview_confirm_close_and_candidate_history(tmp_path):
    with local_app(tmp_path) as (server,path):
        conn = connect(path)
        ids = unassign(conn)
        base,post = client(server)
        assert '2 条候选' in post('m2-orders',{'action':'generate','selection':'filtered','scope_session':['S1','S2'],'source_scope_confirmed':'yes'})
        assert all(i not in assignments(conn) for i in ids)
        page = post('m2-orders',{'action':'preview','selection':'filtered','assign_mode':'candidates'})
        assert 'href="?' not in page
        assert '<form method="get">' not in page
        assert '确认本次批量处理' in page and '共 2 条' in page
        assert len(urlopen(base+'/issues.csv?month=2026-09').read().decode('utf-8-sig').splitlines())==3
        t = token(page)
        csv = urlopen(base+'/m2-preview.csv?token='+t).read().decode('utf-8-sig')
        assert 'O1 / 1' in csv and 'O2 / 1' in csv and 'S1' in csv and 'S2' in csv
        assert '已确认处理 2 条' in post('m2-apply',{'preview_token':t,'reason':'BP核对原始订单范围'})
        with pytest.raises(HTTPError) as err:
            post('m2-apply',{'preview_token':t,'reason':'重复点击'})
        assert err.value.code==400
        assert '整月关账完成' in post('close',{'confirmed':'yes'})
        assert get_close(conn,'2026-09')['results']['S1']['final_profit']==112000
        history = urlopen(base+f'/m2-history?record_id={ids[0]}').read().decode()
        assert '当前决定采用' in history and '支付时间' in history and 'BP核对原始订单范围' in history
        script = urlopen(base+'/m2.js')
        assert script.headers['Content-Type'].startswith('text/javascript')
        assert 'revealHashTarget' in script.read().decode()
        conn.close()


def test_filtered_selection_crosses_pages_but_preview_freezes_ids(tmp_path):
    with local_app(tmp_path) as (server,path):
        conn = connect(path)
        unassign(conn)
        example = records(conn,'orders')[0]
        for n in range(53):
            data = example['data']|{'order_id':f'BATCH{n:03d}'}
            conn.execute('INSERT INTO records(kind,business_key,data) VALUES(?,?,?)',('orders',f'BATCH{n:03d}|1',dumps(data)))
        base,post = client(server)
        page = urlopen(base+'/?search=BATCH').read().decode()
        assert len(re.findall('aria-label="选择订单 BATCH',page))==50
        assert '本次筛选共 53 条' in page
        criteria = {'search':'BATCH','date_prefix':'2026-09-01','order_status':'pending','selection':'filtered'}
        post('m2-orders',criteria|{'action':'generate','scope_session':['S1'],'source_scope_confirmed':'yes'})
        page = post('m2-orders',criteria|{'action':'preview','assign_mode':'candidates'})
        t = token(page)
        assert '共 53 条' in page
        assert len(urlopen(base+'/m2-preview.csv?token='+t).read().decode('utf-8-sig').splitlines())==54
        conn.execute('INSERT INTO records(kind,business_key,data) VALUES(?,?,?)',('orders','BATCHNEW|1',dumps(example['data']|{'order_id':'BATCHNEW'})))
        result = post('m2-apply',criteria|{'preview_token':t,'reason':'跨页批量核对'})
        assert '已确认处理 53 条' in result
        assert '本次筛选共 1 条' in result
        selected = [r for r in records(conn,'orders') if r['data']['order_id'].startswith('BATCH')]
        decisions = assignments(conn)
        assert sum(r['id'] in decisions for r in selected)==53
        assert selected[-1]['id'] not in decisions
        conn.close()


def test_http_cost_reuse_and_stale_preview_rejection(tmp_path):
    with local_app(tmp_path) as (server,path):
        conn = connect(path)
        ids = unassign(conn)
        base,post = client(server)
        page = post('m2-orders',{'action':'preview','record_id':ids,'assign_mode':'manual','session_id':'S1'})
        t = token(page)
        row = records(conn,'orders')[1]
        conn.execute('UPDATE records SET data=? WHERE id=?',(dumps(row['data']|{'sku':'修正SKU'}),row['id']))
        with pytest.raises(HTTPError) as err:
            post('m2-apply',{'preview_token':t,'reason':'过期预览'})
        assert '来源记录已变化' in err.value.read().decode()
        assert all(i not in assignments(conn) for i in ids)
        for rid,sid in zip(ids,['S1','S2']):
            post('assign-orders',{'record_id':rid,'session_id':sid,'basis':'运营确认','reason':'确认'})
        cost = records(conn,'fulfillment')[0]
        conn.execute('UPDATE records SET data=? WHERE id=?',(dumps(cost['data']|{'unit_cost':None}),cost['id']))
        post('m2-standard',{'sku':'学习机','unit_cost':'2000','reason':'BP确认适用'})
        sid = conn.execute('SELECT id FROM cost_standards').fetchone()[0]
        page = post('m2-preview',{'operation':'unit_cost','standard_id':sid})
        post('m2-apply',{'preview_token':token(page),'reason':'核对空缺成本来源'})
        assert records(conn,'fulfillment')[0]['data']['_cost_source']['id']==sid
        post('m2-ad-link',{'account':'账号1','plan':'计划1','session_id':['S1','S2'],'reason':'确定本月受益范围'})
        assert conn.execute('SELECT COUNT(*) FROM ad_links').fetchone()[0]==1
        conn.close()


def test_ad_candidates_and_exceptions_are_separate_selection_groups(tmp_path):
    conn = connect(tmp_path/'ui-groups.db')
    seed(conn)
    ad = records(conn,'ads')[0]
    conn.execute('DELETE FROM assignments WHERE record_id=?',(ad['id'],))
    save_ad_link(conn,'2026-09','账号1','计划1',['S1'],reason='仅第一场受益')
    generate(conn,[ad['id']],month='2026-09')
    page = m2_ui.render_ads(conn,'csrf',{'month':'2026-09','step':'3'})
    assert '异常记录' in page and '转到手工分配' in page
    assert 'manual_open=' in page
    assert '选择可确认投流 A1' not in page
    save_ad_link(conn,'2026-09','账号1','计划1',['S1','S2'],reason='两场共同受益')
    generate(conn,[ad['id']],month='2026-09')
    page = m2_ui.render_ads(conn,'csrf',{'month':'2026-09','step':'3'})
    assert '选择可确认投流 A1' in page
    assert '只允许选择没有异常' in page
    conn.close()


def test_step_three_shows_non_fee_closing_issue_even_when_fee_queue_empty(tmp_path):
    conn = connect(tmp_path/'ui-closing-check.db')
    seed(conn)
    set_applicability(conn,'S2',dict(talent=False,ads=True,slot=False,gift=False),reason='确认第二场有投流')
    page = render_page(conn,'csrf',{'month':'2026-09','step':'3'})
    assert '距离关账还差 1 项' in page
    assert 'S2: 有投流但消耗记录未到' in page
    assert '实际费用记录没有待办' in page
    conn.close()
