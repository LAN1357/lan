import re
import threading
import socket
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from src.db import init_db
from src.workbench.app import WorkbenchServer
from src.workbench.closing import get_close
from src.workbench.db import connect, records
from src.workbench.demo import demo_workbook


@contextmanager
def local_app(tmp_path):
    path = tmp_path / 'ui.db'
    conn = connect(path); conn.close()
    server = WorkbenchServer(('127.0.0.1', 0), path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, path
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_http_full_trial_import_assign_costs_close_reopen_export(tmp_path):
    with local_app(tmp_path) as (server, path):
        base = f'http://127.0.0.1:{server.server_port}'
        page = urlopen(base + '/workbench').read().decode()
        assert '直播经营复盘工作台' in page
        csrf = re.search('name="csrf"[^>]*value="([^"]+)"', page)[1]

        def post(action, data):
            payload = urlencode({'csrf': csrf, 'month': '2026-09', **data}, doseq=True).encode()
            return urlopen(Request(base + '/' + action, data=payload)).read()

        boundary = 'test-boundary-2026'
        payload = b''
        for key, value in [('csrf', csrf), ('month', '2026-09')]:
            payload += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        payload += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="demo.xlsx"\r\nContent-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n'.encode()
        payload += demo_workbook() + f'\r\n--{boundary}--\r\n'.encode()
        preview = urlopen(Request(base+'/preview', data=payload, headers={'Content-Type': 'multipart/form-data; boundary='+boundary})).read().decode()
        token = re.search('name="upload_token"[^>]*value="([^"]+)"', preview)[1]
        assert '待入账 13 行' in preview
        assert '入账 13 行' in post('import', {'upload_token': token}).decode()
        conn = connect(path)
        order_ids = [r['id'] for r in records(conn, 'orders')]
        for rid, sid in zip(order_ids, ['S1', 'S2']):
            post('assign-orders', {'record_id': [rid], 'session_id':sid, 'basis':'支付时间', 'reason':'首次确认'})
        # One manual attribution correction is auditable.
        post('assign-orders', {'record_id':[order_ids[1]],'session_id':'S1','basis':'待核对','reason':'暂定'})
        post('assign-orders', {'record_id':[order_ids[1]],'session_id':'S2','basis':'运营记录核实','reason':'纠正场次'})
        for sid, flag in [('S1','true'),('S2','false')]:
            post('flags', {'session_id':sid, 'talent':flag,'ads':flag,'slot':flag,'gift':flag,'reason':'确认业务范围'})
        for row in records(conn,'ads') + records(conn,'monthly'):
            post('allocate', {'record_id':row['id'],'mode':'weights','target:S1':'1','basis':'合成账例指定受益场次','reason':'确认'})
        result = post('close',{'confirmed':'yes'}).decode()
        assert '整月关账完成，版本 V1' in result
        assert get_close(conn,'2026-09')['results']['S1']['final_profit'] == 112000
        assert urlopen(base+'/export?month=2026-09').read().startswith(b'PK')
        post('reopen',{'reason':'仓储改为两场共享'})
        assert get_close(conn,'2026-09') is None
        warehouse = records(conn,'monthly')[0]
        post('allocate',{'record_id':warehouse['id'],'mode':'weights','target:S1':'1','target:S2':'1','basis':'共同受益','reason':'重开修订'})
        # Fee editor uses yuan text and maintains source corrections.
        post('cost',{'kind':'monthly','record_id':warehouse['id'],'source_id':'W','month':'2026-09',
                     'category':'仓储','amount':'100','basis':'两场直播承担部分','reason':'补充分摊范围'})
        assert '整月关账完成，版本 V2' in post('close',{'confirmed':'yes'}).decode()
        assert get_close(conn,'2026-09',1)['results']['S1']['final_profit'] == 112000
        assert get_close(conn,'2026-09')['results']['S1']['final_profit'] == 117000
        assert urlopen(base+'/export?month=2026-09&version=1').read().startswith(b'PK')
        conn.close()


def test_local_only_csrf_and_legacy_database_isolation(tmp_path):
    with pytest.raises(ValueError):
        WorkbenchServer(('0.0.0.0',0),tmp_path/'bad.db')
    with local_app(tmp_path) as (server, path):
        base = f'http://127.0.0.1:{server.server_port}'
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(base+'/close',data=b'month=2026-09&confirmed=yes'))
        assert exc.value.code == 403
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(base + '/workbench',headers={'Host':'attacker.example'}))
        assert exc.value.code == 403
    import sqlite3
    legacy_path = tmp_path/'legacy.db'
    legacy = sqlite3.connect(legacy_path)
    init_db(legacy); legacy.close()
    with pytest.raises(ValueError,match='旧原型'):
        connect(legacy_path)


def test_idle_browser_preconnection_does_not_block_other_requests(tmp_path):
    with local_app(tmp_path) as (server, path):
        # Establish the browser's speculative connection before the real GET.
        with socket.create_connection(server.server_address, timeout=2) as idle:
            with urlopen(f'http://127.0.0.1:{server.server_port}/workbench',timeout=3) as response:
                assert response.status == 200
                assert '直播经营复盘工作台' in response.read().decode()


def test_candidate_generation_returns_to_attribution_with_local_feedback(tmp_path):
    with local_app(tmp_path) as (server, path):
        base = f'http://127.0.0.1:{server.server_port}'
        page = urlopen(base + '/workbench').read().decode()
        csrf = re.search('name="csrf"[^>]*value="([^"]+)"', page)[1]
        assert 'formaction="/m2-orders#attribution"' in page
        assert 'formaction="/m2-orders#batch-preview"' in page
        assert '生成候选时必选' in page

        # Import the demo through the domain function so this test stays focused
        # on the candidate-generation response and its user-visible feedback.
        from src.workbench.imports import import_workbook
        conn = connect(path)
        import_workbook(conn, demo_workbook(), 'demo.xlsx')
        order_id = records(conn, 'orders')[0]['id']
        conn.close()

        payload = urlencode({
            'csrf': csrf, 'month': '2026-09', 'record_id': order_id,
            'selection': 'selected', 'scope_session': ['S1', 'S2'],
            'source_scope_confirmed': 'yes', 'action': 'generate',
        }, doseq=True).encode()
        result = urlopen(Request(base + '/m2-orders#attribution', data=payload)).read().decode()
        assert result.count('已保存 1 条候选记录') == 2
        assert '<td>S1</td>' in result
        assert '生成/刷新当前范围投流候选' in result
        assert 'formaction="/m2-ads#allocation"' in result
        assert '异常记录' in result

        missing_confirmation = urlencode({
            'csrf': csrf, 'month': '2026-09', 'record_id': order_id,
            'selection': 'selected', 'scope_session': ['S1', 'S2'],
            'action': 'generate',
        }, doseq=True).encode()
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(base + '/m2-orders', data=missing_confirmation))
        assert exc.value.code == 400
        assert '生成订单候选前' in exc.value.read().decode()


def test_time_only_filter_and_invalid_filter_return_operable_page(tmp_path):
    with local_app(tmp_path) as (server, path):
        from src.workbench.imports import import_workbook
        conn = connect(path)
        import_workbook(conn, demo_workbook(), 'demo.xlsx')
        conn.close()
        base = f'http://127.0.0.1:{server.server_port}'

        page = urlopen(base + '/workbench?month=2026-09&step=2&time_to=20%3A30').read().decode()
        assert '支付时段至（如 21:30；包含该分钟）' in page
        assert 'Invalid isoformat string' not in page

        with pytest.raises(HTTPError) as exc:
            urlopen(base + '/workbench?month=2026-09&step=2&time_to=not-a-time')
        assert exc.value.code == 400
        error_page = exc.value.read().decode()
        assert '筛选条件无效，已清除本次筛选' in error_page
        assert '确认订单属于哪场' in error_page
        assert 'href="/workbench?month=2026-09&amp;step=2' in error_page


def test_reset_all_requires_phrase_and_clears_imported_workspace(tmp_path):
    with local_app(tmp_path) as (server, path):
        base = f'http://127.0.0.1:{server.server_port}'
        page = urlopen(base + '/workbench').read().decode()
        csrf = re.search('name="csrf"[^>]*value="([^"]+)"', page)[1]
        from src.workbench.imports import import_workbook
        conn = connect(path)
        import_workbook(conn, demo_workbook(), 'demo.xlsx')
        conn.close()

        def post(confirmation):
            payload = urlencode({'csrf':csrf, 'month':'2026-09',
                                 'confirmation':confirmation}).encode()
            return urlopen(Request(base + '/reset-all', data=payload)).read().decode()

        with pytest.raises(HTTPError) as exc:
            post('清空')
        assert exc.value.code == 400
        result = post('清空全部数据')
        assert '已清空全部业务数据' in result
        conn = connect(path)
        assert records(conn) == []
        assert conn.execute('SELECT COUNT(*) FROM batches').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM changes').fetchone()[0] == 0
        conn.close()


def test_page_shows_current_session_applicability(tmp_path):
    with local_app(tmp_path) as (server, path):
        from src.workbench.costs import set_applicability
        from src.workbench.imports import import_workbook
        conn = connect(path)
        import_workbook(conn, demo_workbook(), 'demo.xlsx')
        set_applicability(conn, 'S1', {
            'talent': True, 'ads': False, 'slot': True, 'gift': False,
        }, reason='测试确认')
        conn.close()

        page = urlopen(f'http://127.0.0.1:{server.server_port}/?month=2026-09').read().decode()
        assert '当前确认结果' in page
        assert re.search(
            r'<td>S1</td><td>合成达人甲</td><td>适用</td><td>不适用</td>'
            r'<td>适用</td><td>不适用</td>', page)
        assert re.search(
            r'<td>S2</td><td>合成自播乙</td><td>未确认</td><td>未确认</td>'
            r'<td>未确认</td><td>未确认</td>', page)


def test_guided_workflow_defaults_to_next_incomplete_step(tmp_path):
    with local_app(tmp_path) as (server, path):
        base = f'http://127.0.0.1:{server.server_port}'
        empty = urlopen(base + '/workbench').read().decode()
        assert '<body class="step-1">' in empty
        assert '步骤 1 / 4' in empty
        assert '本月待办' not in empty

        from src.workbench.imports import import_workbook
        conn = connect(path)
        import_workbook(conn, demo_workbook(), 'demo.xlsx')
        conn.close()
        imported = urlopen(base + '/?month=2026-09').read().decode()
        assert '<body class="step-2">' in imported
        assert '步骤 2 / 4' in imported

        fees = urlopen(base + '/?month=2026-09&step=3').read().decode()
        assert '<body class="step-3">' in fees
        assert '确认业务与投流范围' in fees
        assert '账号A / 计划001' not in fees
        assert '合成账号 / 合成计划' in fees
        assert 'name="account"' not in fees
        assert 'name="plan"' not in fees
