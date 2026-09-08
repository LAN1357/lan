import json
import hashlib
import html
import re
import threading
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from src.analysis.service import AnalysisService
from src.workbench.analysis_ui import render_analysis_page
from src.workbench.app import WorkbenchServer
from src.workbench.closing import close_month, reopen_month


@contextmanager
def analysis_app(path):
    server = WorkbenchServer(('127.0.0.1', 0), path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def post_json(base, path, payload):
    request = Request(base + path, data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
    with urlopen(request) as response:
        return response.status, json.loads(response.read())


def without_time(payload):
    result = dict(payload)
    result.pop('as_of', None)
    return result


def test_readonly_http_and_dashboard_share_service_contract(closed_db):
    path, conn = closed_db
    scope = {'close_refs': [{'month': '2026-09', 'version': 1}], 'mode': 'current'}
    expected = AnalysisService(path).query_performance(scope)
    before_changes = conn.execute('SELECT COUNT(*) FROM changes').fetchone()[0]
    before_closes = conn.execute('SELECT COUNT(*) FROM closes').fetchone()[0]

    with analysis_app(path) as base:
        status, actual = post_json(base, '/api/analysis/performance', {'scope': scope})
        assert status == 200
        assert without_time(actual) == without_time(expected)
        versions = json.loads(urlopen(base + '/api/analysis/versions?include_history=true').read())
        assert versions['data'][0]['versions'][0]['version'] == 1

        page = urlopen(base + '/analysis?month=2026-09&version=1&mode=current').read().decode()
        assert '全月经营分析' in page
        assert '当前有效版本 · 2026-09 V1' in page
        assert '复制分析引用' in page
        assert '保持以上版本和筛选范围，不自动切换最新版' in page
        assert '去重关注' in page
        assert '主要金额构成' in page

    assert conn.execute('SELECT COUNT(*) FROM changes').fetchone()[0] == before_changes
    assert conn.execute('SELECT COUNT(*) FROM closes').fetchone()[0] == before_closes


def test_ai_entry_preserves_historical_filtered_scope_and_precedes_overview(closed_db):
    path, conn = closed_db
    reopen_month(conn, '2026-09', reason='保留旧版测试')
    close_month(conn, '2026-09', confirmed=True)
    before = list(conn.iterdump())
    page = render_analysis_page(AnalysisService(path), {
        'month': '2026-09', 'version': '1', 'mode': 'history',
        'session_id': 'S1', 'talent': '甲', 'start_date': '2026-09-01',
        'end_date': '2026-09-01',
    })
    assert page.index('使用 Claude Code / Codex 分析本月') < page.index('<section id="overview">')
    assert page.count('id="analysis-ref"') == 1
    reference = html.unescape(re.search(r'<textarea id="analysis-ref" readonly>(.*?)</textarea>', page)[1])
    for expected in ('历史关账副本：2026-09 V1', '场次范围：S1', '达人原始名称：甲',
                     '开播日期：2026-09-01至2026-09-01', '不自动切换最新版',
                     'livecommerce-m3-readonly', '待验证假设'):
        assert expected in reference
    assert 'claude mcp get livecommerce-m3-readonly' in page
    assert 'codex mcp get livecommerce-m3-readonly' in page
    assert '这些命令不负责启动服务' in page
    assert list(conn.iterdump()) == before


def test_all_eight_http_routes_and_two_comparison_contracts(closed_db):
    path, conn = closed_db
    reopen_month(conn, '2026-09', reason='测试版本比较')
    close_month(conn, '2026-09', confirmed=True)
    historical = {'close_refs': [{'month': '2026-09', 'version': 1}], 'mode': 'history'}
    current = {'close_refs': [{'month': '2026-09', 'version': 2}], 'mode': 'current'}

    calls = [
        ('/api/analysis/performance', {'scope': current}),
        ('/api/analysis/compare-performance', {'base_scope': historical, 'current_scope': current}),
        ('/api/analysis/evidence', {'close_ref': {'month': '2026-09', 'version': 2},
                                   'session_id': 'S1', 'mode': 'current'}),
        ('/api/analysis/compare-versions', {'month': '2026-09', 'base_version': 1,
                                           'current_version': 2}),
        ('/api/analysis/rules', {'metric_keys': ['final_profit', 'final_margin']}),
        ('/api/analysis/diagnostics', {'scope': current}),
        ('/api/analysis/scenario', {'close_ref': {'month': '2026-09', 'version': 2},
                                    'session_ids': ['S1'], 'mode': 'current',
                                    'assumptions': [{'type': 'session_fee', 'session_id': 'S1',
                                                     'field': 'warehouse', 'operation': 'delta',
                                                     'amount_cents': 1000}]}),
    ]
    with analysis_app(path) as base:
        assert json.loads(urlopen(base + '/api/analysis/versions').read())['status'] == 'ok'
        for route, payload in calls:
            status, result = post_json(base, route, payload)
            assert status == 200, (route, result)
            assert result['status'] == 'ok'
            assert result['contract_version'] == 'm3-contract-v1'

        page = urlopen(base + '/analysis?month=2026-09&version=1&mode=history').read().decode()
        assert '历史版本 · 2026-09 V1' in page
        assert '当前有效版本为 V2' in page

        comparison = urlopen(base + '/analysis?month=2026-09&version=2&mode=current'
                             '&compare_version_base=1&compare_version_current=2').read().decode()
        assert '关账版本比较' in comparison
        assert '期间比较' in comparison


def test_analysis_routes_do_not_call_workbench_connect_or_create_missing_db(tmp_path, monkeypatch):
    missing = tmp_path / 'must-not-be-created.db'
    server = WorkbenchServer(('127.0.0.1', 0), missing)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    monkeypatch.setattr('src.workbench.app.connect',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('write connect called')))
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        page = urlopen(base + '/analysis').read().decode()
        assert '数据库不存在或不可读取' in page
        assert 'navigator.clipboard' in urlopen(base + '/analysis.js').read().decode()
        with pytest.raises(HTTPError) as error:
            urlopen(base + '/api/analysis/versions')
        assert error.value.code == 404
        payload = json.loads(error.value.read())
        assert payload['error']['code'] == 'NO_CLOSED_DATA'
        assert str(missing) not in json.dumps(payload, ensure_ascii=False)
        scenario = urlencode({'month':'2026-09','version':'1','mode':'current',
                              'session_id':'S1','assumption_type':'session_fee',
                              'fee_field':'warehouse','operation':'delta',
                              'amount_yuan':'10'}).encode()
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base + '/analysis/scenario',data=scenario,
                            headers={'Content-Type':'application/x-www-form-urlencoded'}))
        assert error.value.code == 400
        assert not missing.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_scenario_form_is_non_persistent_and_months_is_landing_page(closed_db):
    path, conn = closed_db
    before = conn.execute('SELECT COUNT(*) FROM changes').fetchone()[0]
    before_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    with analysis_app(path) as base:
        landing = urlopen(base + '/').read().decode()
        assert '先选月份，再继续当前任务' in landing
        assert '查看经营分析' in landing
        data = urlencode({'month': '2026-09', 'version': '1', 'mode': 'current',
                          'session_id': 'S1', 'assumption_type': 'session_fee',
                          'fee_field': 'warehouse', 'operation': 'delta',
                          'amount_yuan': '10'}).encode()
        request = Request(base + '/analysis/scenario', data=data,
                          headers={'Content-Type': 'application/x-www-form-urlencoded'})
        page = urlopen(request).read().decode()
        assert '假设测算，非实际结算结果' in page
        assert '不会保存到工作台' in page
        assert '假设来源：用户直接给定' in page
        assert '来源标记仅用于追溯，不能替代真正授权' in page
        sku_data = urlencode({'month': '2026-09', 'version': '1', 'mode': 'current',
                              'session_id': 'S1', 'assumption_type': 'sku_unit_cost',
                              'sku': '学习机', 'unit_cost': '1970'}).encode()
        sku_page = urlopen(Request(base + '/analysis/scenario', data=sku_data,
                                   headers={'Content-Type': 'application/x-www-form-urlencoded'})).read().decode()
        assert 'sku_unit_cost' in sku_page
        assert 'persisted=false' in sku_page
    assert conn.execute('SELECT COUNT(*) FROM changes').fetchone()[0] == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_hash


def test_http_echoes_agent_proposed_assumption_scope(closed_db):
    path, _conn = closed_db
    authorization_scope = {
        'session_ids': ['S1'],
        'assumption_types': ['session_fee'],
        'fee_fields': ['slot_fee'],
        'operations': ['delta'],
        'max_assumptions': 3,
        'fee_delta_abs_max_cents': 20000,
    }
    payload = {
        'close_ref': {'month': '2026-09', 'version': 1},
        'session_ids': ['S1'],
        'mode': 'current',
        'assumptions': [{
            'type': 'session_fee', 'session_id': 'S1', 'field': 'slot_fee',
            'operation': 'delta', 'amount_cents': -20000,
        }],
        'assumption_source': 'agent_proposed',
        'authorization_scope': authorization_scope,
    }
    with analysis_app(path) as base:
        status, result = post_json(base, '/api/analysis/scenario', payload)
    assert status == 200
    assert result['assumption_trace'] == {
        'source': 'agent_proposed',
        'authorization_scope': authorization_scope,
        'scope_validation': 'matched',
        'authorization_notice': (
            '假设由Agent在用户声明范围内提出；系统只校验参数落在该范围内，'
            '来源标记本身不能证明用户授权。'),
    }

    page = render_analysis_page(
        AnalysisService(path),
        {'month': ['2026-09'], 'version': ['1'], 'mode': ['current']},
        result,
    )
    assert '假设来源：Agent在用户声明范围内提出' in page
    assert '查看声明的授权范围' in page
    assert '&quot;fee_delta_abs_max_cents&quot;: 20000' in page
    assert '来源标记仅用于追溯，不能替代真正授权' in page


def test_http_rejects_wrong_media_type_and_unknown_parameters(closed_db):
    path, _conn = closed_db
    with analysis_app(path) as base:
        bad = Request(base + '/api/analysis/performance', data=b'{}',
                      headers={'Content-Type': 'text/plain'})
        with pytest.raises(HTTPError) as error:
            urlopen(bad)
        assert error.value.code == 400
        assert json.loads(error.value.read())['error']['code'] == 'INVALID_SCOPE'

        with pytest.raises(HTTPError) as error:
            post_json(base, '/api/analysis/rules', {'database_path': '/tmp/other.db'})
        assert error.value.code == 400
        body = json.loads(error.value.read())
        assert body['error']['code'] == 'INVALID_SCOPE'
        assert '/tmp/other.db' not in json.dumps(body, ensure_ascii=False)


def test_analysis_javascript_only_handles_copy_and_feedback(closed_db):
    path, _conn = closed_db
    with analysis_app(path) as base:
        script = urlopen(base + '/analysis.js').read().decode()
    assert 'navigator.clipboard' in script
    assert 'aria-busy' in script
    for finance_logic in ('reduce(', 'cents', 'profit', 'margin', 'rank', 'percent'):
        assert finance_logic not in script


def test_dashboard_discards_results_when_current_version_changes_during_render(closed_db):
    path, conn = closed_db

    class RacingService(AnalysisService):
        calls = 0

        def list_close_versions(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2:
                reopen_month(conn, '2026-09', reason='模拟页面读取期间重开')
            return super().list_close_versions(*args, **kwargs)

    page = render_analysis_page(
        RacingService(path), {'month': ['2026-09'], 'version': ['1'], 'mode': ['current']})
    assert '版本已变化，未展示可能混版的金额' in page
    assert '原分析片段已全部丢弃' in page
    assert '11,970.00 元' not in page
    assert '保留查看历史 V1' in page


def test_dashboard_escapes_business_text_instead_of_treating_it_as_markup(closed_db):
    path, conn = closed_db
    snapshot = json.loads(conn.execute('SELECT snapshot FROM closes').fetchone()[0])
    snapshot['sessions']['S1']['talent'] = '<script>do-not-run()</script>'
    conn.execute('UPDATE closes SET snapshot=?', (json.dumps(snapshot),))
    page = render_analysis_page(
        AnalysisService(path), {'month': ['2026-09'], 'version': ['1'], 'mode': ['current']})
    assert '<script>do-not-run()</script>' not in page
    assert '&lt;script&gt;do-not-run()&lt;/script&gt;' in page
