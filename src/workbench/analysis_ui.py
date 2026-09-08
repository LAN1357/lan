"""Server-rendered M3 dashboard. It presents service payloads and never recalculates them."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from src.analysis.schemas import SCENARIO_FEE_FIELDS
from src.analysis.service import AnalysisService
from src.reports.management import LABELS
from src.workbench.presentation import esc, field, select, table


ANALYSIS_CSS = '''
:root{--ink:#153b3a;--muted:#526968;--primary:#0f766e;--primary-dark:#0b5b55;--accent:#c8490b;--paper:#fff;--wash:#f3f8f6;--line:#cfe0dc;--soft:#e4f2ee;--warn:#8a3b12;--warn-bg:#fff4e8;--danger:#b42318;--shadow:0 12px 35px rgba(21,59,58,.08);--r:14px}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f3f7f5;color:var(--ink);font:16px/1.58 "Avenir Next","PingFang SC","Noto Sans CJK SC",sans-serif;min-height:100vh}
a{color:var(--primary-dark)}.skip-link{position:absolute;left:16px;top:-60px;background:#fff;padding:10px 14px;z-index:1000}.skip-link:focus{top:12px}
.top{background:#123e3b;color:#fff;border-bottom:4px solid #e96b2c}.top-inner{max-width:1440px;margin:auto;padding:18px 24px;display:flex;align-items:center;justify-content:space-between;gap:24px}.brand strong{font-size:21px;display:block}.brand span{color:#cfe7e2;font-size:13px}.global-nav{display:flex;gap:6px;flex-wrap:wrap}.global-nav a{color:#dff3ef;text-decoration:none;padding:9px 13px;border-radius:8px;font-weight:700}.global-nav a:hover,.global-nav a[aria-current=page]{background:#fff;color:var(--primary-dark)}
.shell{max-width:1440px;margin:auto;padding:24px;display:grid;grid-template-columns:260px minmax(0,1fr);gap:24px}.side{position:sticky;top:20px;background:var(--paper);border:1px solid var(--line);border-radius:var(--r);padding:16px;box-shadow:var(--shadow)}.side h2{font-size:13px;letter-spacing:.08em;text-transform:uppercase;margin:0 0 10px}.side a{display:block;padding:10px;border-radius:8px;text-decoration:none;font-weight:700}.side a:hover{background:var(--wash)}
main{min-width:0}.hero{background:linear-gradient(120deg,#143e3b,#1a524c);color:#fff;border-radius:18px;padding:24px 26px;margin-bottom:18px;box-shadow:var(--shadow)}.hero-row{display:flex;justify-content:space-between;gap:24px;align-items:start}.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#9fd7ca;font-weight:800}.hero h1{font-size:30px;line-height:1.2;margin:5px 0 8px}.hero p{margin:0;color:#d7ece7}.version{display:inline-flex;padding:6px 10px;border:1px solid #7eb8ad;border-radius:999px;white-space:nowrap;font-weight:800}.version.history{background:#fff4e8;border-color:#f2b27c;color:#8a3b12}.scope-line{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}.chip{display:inline-flex;padding:5px 9px;border-radius:999px;background:#ecf5f2;color:#28514d;font-size:12px;font-weight:700}.hero .chip{background:rgba(255,255,255,.12);color:#fff}
section{background:var(--paper);border:1px solid var(--line);border-radius:var(--r);padding:22px;margin-bottom:18px;box-shadow:0 2px 0 rgba(21,59,58,.02);scroll-margin-top:20px}h2{font-size:21px;margin:0 0 7px}h3{font-size:16px;margin:18px 0 8px}.lead,.muted,small{color:var(--muted)}.lead{margin:0 0 16px}.split{display:flex;justify-content:space-between;gap:16px;align-items:start}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:15px 0}.metric{border:1px solid var(--line);background:var(--wash);border-radius:11px;padding:14px;min-width:0}.metric span{font-size:12px;color:var(--muted)}.metric strong{font:700 23px/1.25 "Avenir Next","PingFang SC",sans-serif;display:block;margin-top:5px;font-variant-numeric:tabular-nums}.metric.primary{background:#e6f3ef;border-color:#9ecbbf}.metric.warn{background:var(--warn-bg);border-color:#edb88f}
.notice{padding:14px 16px;border:1px solid #9fc9c0;border-left:5px solid var(--primary);border-radius:10px;background:#eaf6f2;margin:12px 0}.notice.issue{border-color:#edb88f;border-left-color:#c55b17;background:var(--warn-bg)}.notice.danger{border-color:#efaaa5;border-left-color:var(--danger);background:#fff0ef}.notice p:last-child{margin-bottom:0}
.grid{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px;align-items:end}.grid.two{grid-template-columns:repeat(2,minmax(0,1fr))}label{font-size:13px;font-weight:750;display:block}input,select,textarea{font:inherit;font-size:16px;width:100%;min-height:44px;padding:9px 11px;border:1px solid #9dbbb4;border-radius:8px;margin-top:5px;background:#fff;color:var(--ink)}textarea{min-height:92px;resize:vertical}button,.button{display:inline-flex;align-items:center;justify-content:center;min-height:44px;background:var(--accent);color:#fff;padding:10px 16px;border:0;border-radius:8px;font:700 14px/1.2 inherit;text-decoration:none;cursor:pointer}button:hover,.button:hover{background:#a83b08}button:disabled{opacity:.55;cursor:wait}.secondary{background:var(--soft)!important;color:var(--primary-dark)!important}.actions{display:flex;gap:9px;flex-wrap:wrap;margin:14px 0}.copy-box{display:grid;grid-template-columns:1fr auto;gap:9px;align-items:end}.copy-box textarea{margin:0;font-size:13px;background:#fbfcfb}
.table-wrap{overflow:auto;margin:12px 0;border:1px solid var(--line);border-radius:10px}table{border-collapse:collapse;min-width:100%;font-size:13px;background:#fff}th{text-align:left;white-space:nowrap;background:#eaf4f1;color:#244d49;font-size:12px}td,th{border-bottom:1px solid #dce9e5;padding:10px 11px;vertical-align:top}tbody tr:last-child td{border-bottom:0}tbody tr:hover{background:#f8fbfa}td{min-width:90px}.money{font-variant-numeric:tabular-nums;white-space:nowrap;font-weight:700}.negative{color:var(--danger)}details{border-top:1px solid var(--line);padding:14px 0}details:first-of-type{border-top:0}summary{cursor:pointer;font-weight:800;min-height:32px}.section-nav{display:flex;gap:7px;flex-wrap:wrap;margin:10px 0}.section-nav a{padding:7px 10px;border-radius:7px;background:var(--wash);text-decoration:none;font-weight:700;font-size:13px}.empty{text-align:center;padding:34px;color:var(--muted)}
input:focus-visible,select:focus-visible,textarea:focus-visible,button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #fdba74;outline-offset:2px}[role=status]{min-height:20px}.footer{text-align:center;color:var(--muted);padding:20px}
@media(max-width:1000px){.shell{grid-template-columns:1fr}.side{position:static;display:flex;gap:5px;overflow:auto}.side h2{display:none}.side a{white-space:nowrap}.metrics{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:620px){.top-inner,.hero-row,.split{align-items:stretch;flex-direction:column}.shell{padding:14px 12px}.global-nav{width:100%}.global-nav a{flex:1;text-align:center}.metrics,.grid,.grid.two,.copy-box{grid-template-columns:1fr}.hero{padding:20px}.hero h1{font-size:25px}section{padding:17px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
'''


ANALYSIS_JS = '''
document.addEventListener('click',async e=>{
  const button=e.target.closest('[data-copy-target]');
  if(!button)return;
  const target=document.getElementById(button.dataset.copyTarget);
  const status=document.getElementById(button.dataset.statusTarget);
  try{
    await navigator.clipboard.writeText(target.value);
    status.textContent=target.id.endsWith('-mcp-check')?'检查命令已复制，请粘贴到终端运行。':'分析引用已复制，可粘贴到 Claude Code 或 Codex。';
  }catch(_error){
    target.focus();target.select();
    status.textContent='浏览器未允许自动复制，已选中文本，请手动复制。';
  }
});
document.addEventListener('submit',e=>{
  const button=e.submitter;if(!button)return;
  button.setAttribute('aria-busy','true');button.disabled=true;button.textContent='正在读取关账副本…';
});
'''


def _value(query: dict, key: str, default=''):
    value = query.get(key, default)
    return value[-1] if isinstance(value, list) and value else value


def _list(query: dict, key: str) -> list[str]:
    value = query.get(key, [])
    values = value if isinstance(value, list) else [value]
    return [str(x) for x in values if str(x)]


def _money(value: dict) -> str:
    return value['yuan_text'] + ' 元'


def _ratio(value: dict) -> str:
    return value['percent_text'] or ('不适用：' + value.get('not_applicable_reason', '分母不适用'))


def _qs(query: dict, **changes) -> str:
    data = {}
    for key, value in query.items():
        if key.startswith('compare_') or key.startswith('scenario_'):
            continue
        data[key] = value
    data.update({key: value for key, value in changes.items() if value not in (None, '')})
    return urlencode(data, doseq=True)


def _reference(scope: dict, *, session_id: str | None = None,
               profit_metric: str = 'final_profit') -> str:
    mode = scope['mode']
    identity = '当前有效关账副本' if mode == 'current' else '历史关账副本'
    refs = '、'.join(f'{r["month"]} V{r["version"]}' for r in scope['close_refs'])
    parts = [f'查询{identity}：{refs}。']
    if session_id:
        parts.append(f'场次：{session_id}。')
    elif scope.get('session_ids'):
        parts.append('场次范围：' + '、'.join(scope['session_ids']) + '。')
    if scope.get('talents'):
        parts.append('达人原始名称：' + '、'.join(scope['talents']) + '。')
    if scope.get('start_date') or scope.get('end_date'):
        parts.append(f'开播日期：{scope.get("start_date") or "不限"}至{scope.get("end_date") or "不限"}。')
    parts.append('同时解释经营复盘利润与最终结算利润的差异。' if profit_metric == 'final_profit'
                 else '以经营复盘利润为主要比较口径，并同时列示最终结算利润。')
    parts.append('保持以上版本和筛选范围，不自动切换最新版。')
    return ''.join(parts)


def _page_top(title: str, scope: dict | None = None, state: dict | None = None) -> list[str]:
    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>',
             esc(title), '</title><style>', ANALYSIS_CSS,
             '</style></head><body><a class="skip-link" href="#main">跳到主要内容</a><header class="top"><div class="top-inner"><div class="brand"><strong>直播经营复盘工作台</strong><span>确定性关账 · 只读经营分析</span></div><nav class="global-nav" aria-label="主要导航"><a href="/months">月份总览</a><a href="/workbench">结算工作台</a><a href="/analysis" aria-current="page">经营分析</a></nav></div></header><div class="shell"><nav class="side" aria-label="分析区域"><h2>本版分析</h2><a href="#overview">经营概览</a><a href="#attention">重点场次</a><a href="#sessions">场次与达人</a><a href="#evidence">场次证据</a><a href="#period-compare">期间比较</a><a href="#version-compare">版本比较</a><a href="#scenario">条件测算</a><a href="#rules">指标口径</a></nav><main id="main" tabindex="-1">']
    if scope:
        historical = scope['mode'] == 'history'
        refs = '、'.join(f'{r["month"]} V{r["version"]}' for r in scope['close_refs'])
        label = '历史版本' if historical else '当前有效版本'
        chips = [f'{scope["session_count"]} 个场次',
                 f'开播日期 {scope["actual_start_date"]}—{scope["actual_end_date"]}',
                 '达人 ' + '、'.join(scope.get('talent_composition', [])),
                 f'关账时间 {scope.get("close_refs",[{}])[0].get("closed_at","")}',
                 '版本状态校验于 __VERSION_CHECKED_AT__']
        if scope.get('session_ids'):
            chips.append('场次筛选 ' + '、'.join(scope['session_ids']))
        if scope.get('talents'):
            chips.append('达人筛选 ' + '、'.join(scope['talents']))
        parts.extend(['<div class="hero"><div class="hero-row"><div><div class="eyebrow">M3 · 已关账数据</div><h1>', esc(title), '</h1><p>范围和数字由后端关账契约提供，页面不重新计算。</p></div><span class="version ', 'history' if historical else '', '">', label, ' · ', esc(refs), '</span></div><div class="scope-line">',
                      ''.join(f'<span class="chip">{esc(chip)}</span>' for chip in chips),
                      '</div></div>'])
    elif state:
        parts.extend(['<div class="hero"><div class="eyebrow">M3 · 关账状态</div><h1>', esc(title), '</h1><p>', esc(state.get('message', '请选择已关账月份。')), '</p></div>'])
    return parts


def _end(parts: list[str]) -> str:
    parts.append('</main></div><div class="footer">M3只读经营分析 · 对话请复制引用到 Codex 或 Claude Code</div><script src="/analysis.js" defer></script></body></html>')
    return ''.join(parts)


def _error_block(result: dict, catalog_item: dict | None = None) -> str:
    error = result['error']
    links = ''
    if catalog_item and catalog_item.get('current_version'):
        links = f'<p><a class="button" href="/analysis?month={esc(catalog_item["month"])}&version={catalog_item["current_version"]}&mode=current">查看当前 V{catalog_item["current_version"]}</a></p>'
    return f'<section><div class="notice danger" role="alert"><strong>{esc(error["message"])}</strong><p>{esc(error.get("next_action", "请重新选择分析范围"))}</p>{links}</div></section>'


def render_analysis_page(service: AnalysisService, query: dict,
                         scenario_result: dict | None = None) -> str:
    catalog = service.list_close_versions(include_history=True)
    if catalog['status'] != 'ok':
        parts = _page_top('经营分析暂不可用', state={'message': catalog['error']['message']})
        parts.append(_error_block(catalog))
        return _end(parts)
    items = catalog['data']
    requested_month = str(_value(query, 'month', ''))
    item = next((x for x in items if x['month'] == requested_month), None)
    if item is None and items:
        item = items[-1]
    if item is None:
        parts = _page_top('还没有可分析的关账版本', state={'message': '请先在结算工作台完成至少一个业务月关账。'})
        parts.append('<section class="empty"><h2>暂无关账数据</h2><p>经营分析不会读取未关账草稿。</p><a class="button" href="/workbench">前往结算工作台</a></section>')
        return _end(parts)
    month = item['month']
    mode = str(_value(query, 'mode', 'current'))
    try:
        version = int(_value(query, 'version', item.get('current_version') or 0))
    except (TypeError, ValueError):
        version = 0
    if not version:
        parts = _page_top(f'{month} 当前无有效关账结果', state={
            'message': '月份已重开，当前无有效关账结果。旧版本仍可明确作为历史查看。'
                       if item['status'] == 'open' else '该月份尚未完成关账。'})
        if item.get('versions'):
            parts.append('<section><h2>历史版本</h2><div class="actions">' + ''.join(
                f'<a class="button secondary" href="/analysis?month={esc(month)}&version={v["version"]}&mode=history">查看历史 V{v["version"]}</a>'
                for v in reversed(item['versions'])) + '</div></section>')
        return _end(parts)

    session_ids = _list(query, 'session_id')
    talents = _list(query, 'talent')
    scope = {'close_refs': [{'month': month, 'version': version}], 'mode': mode}
    if session_ids:
        scope['session_ids'] = session_ids
    if talents:
        scope['talents'] = talents
    for key in ('start_date', 'end_date'):
        value = str(_value(query, key, ''))
        if value:
            scope[key] = value
    sort_by = str(_value(query, 'sort_by', 'final_profit'))
    performance = service.query_performance(scope, group_by='session', sort_by=sort_by,
                                            limit=100)
    try:
        loss_offset = max(0, int(_value(query, 'loss_offset', 0)))
        direction_offset = max(0, int(_value(query, 'direction_offset', 0)))
    except (TypeError, ValueError):
        loss_offset = direction_offset = 0
    diagnostics = service.diagnose_performance(
        scope, rules=['final_loss', 'operating_profit_final_loss'], limit=1)
    loss_details = service.diagnose_performance(
        scope, rules=['final_loss'], offset=loss_offset, limit=20)
    direction_details = service.diagnose_performance(
        scope, rules=['operating_profit_final_loss'], offset=direction_offset, limit=20)
    component_details = service.diagnose_performance(
        scope, rules=['major_components'], limit=100)
    results = [performance, diagnostics, loss_details, direction_details, component_details]
    if any(result['status'] != 'ok' for result in results):
        failed = next(result for result in results if result['status'] != 'ok')
        parts = _page_top(f'{month} V{version} 无法继续分析', state={'message': failed['error']['message']})
        parts.append(_error_block(failed, item))
        if failed['error']['code'] in ('VERSION_CHANGED','MONTH_REOPENED'):
            parts.append(f'<section><a class="button secondary" href="/analysis?month={esc(month)}&version={version}&mode=history">明确查看历史 V{version}</a></section>')
        return _end(parts)
    perf_refs = [(x['month'], x['version']) for x in performance['scope']['close_refs']]
    diagnostic_scopes = [
        ([(x['month'], x['version']) for x in result['scope']['close_refs']], result['scope']['mode'])
        for result in (diagnostics, loss_details, direction_details, component_details)]
    if any(perf_refs != refs or performance['scope']['mode'] != result_mode
           for refs, result_mode in diagnostic_scopes):
        failed = {'error': {'message': '本次页面请求返回了不一致的版本范围',
                            'next_action': '请刷新页面重新读取完整关账范围'}}
        parts = _page_top('分析范围发生变化', state={'message': failed['error']['message']})
        parts.append(_error_block(failed, item))
        return _end(parts)
    actual_scope = performance['scope']
    filtered = bool(actual_scope['session_ids'] or actual_scope['talents'] or
                    actual_scope['start_date'] or actual_scope['end_date'])
    title = f'{month} · {"筛选范围" if filtered else "全月"}经营分析'
    parts = _page_top(title, actual_scope)
    ref_text = ('请通过 livecommerce-m3-readonly 的只读工具复盘以下范围。先核验关账版本，再查询双利润、定位重点场次并追查费用与分摊依据。'
                + _reference(actual_scope, profit_metric=sort_by)
                + '区分已核算事实、证据支持的解释、待验证假设和经营建议。金额、比率和差额均使用工具结果；不修改正式数据，未获得明确测算授权时不执行自定数值测算。')
    parts.append('<section id="ai-analysis"><h2>使用 Claude Code / Codex 分析本月</h2><p class="lead">将分析指令复制到已连接本项目 MCP 的 Claude Code 或 Codex 对话中。“本月”指页面选定的业务月份；指令保留当前版本和筛选范围。</p><div class="copy-box"><label for="analysis-ref">本次分析指令<textarea id="analysis-ref" readonly>'
                 + esc(ref_text) + '</textarea></label><button type="button" data-copy-target="analysis-ref" data-status-target="copy-status">复制分析引用与指令</button></div><p id="copy-status" role="status" class="muted"></p>'
                 + '<details><summary>连接说明 · Claude Code / Codex</summary><p>在终端运行对应命令，查看已注册服务的配置与连接信息；这些命令不负责启动服务。</p><label for="claude-mcp-check">Claude Code<input id="claude-mcp-check" readonly value="claude mcp get livecommerce-m3-readonly"></label><button type="button" class="secondary" data-copy-target="claude-mcp-check" data-status-target="claude-check-status">复制 Claude Code 检查命令</button><p id="claude-check-status" role="status" class="muted"></p><label for="codex-mcp-check">Codex<input id="codex-mcp-check" readonly value="codex mcp get livecommerce-m3-readonly"></label><button type="button" class="secondary" data-copy-target="codex-mcp-check" data-status-target="codex-check-status">复制 Codex 检查命令</button><p id="codex-check-status" role="status" class="muted"></p><p>配置完成后，客户端按需启动本地服务。若提示服务不存在，请先按项目《M3 接入与使用说明》注册服务，并确认使用同一工作台账本；注册后重新打开客户端会话。复制指令不会自动启动客户端或发送消息。</p></details></section>')
    parts.append('<section aria-label="分析版本选择"><div class="split"><div><h2>分析范围</h2><p class="lead">月份与版本都由你明确选择；出现新版本时不会静默切换。</p></div></div><form method="get" action="/analysis"><div class="grid">' +
                 select('month','切换业务月份',[(x['month'],x['month']) for x in items],month) +
                 '<button class="secondary">查看该月当前版本</button></div></form><div class="actions">' +
                 ''.join(f'<a class="button {"" if v["version"]==version else "secondary"}" href="/analysis?month={esc(month)}&version={v["version"]}&mode={"current" if item.get("current_version")==v["version"] else "history"}">V{v["version"]} · {"当前" if item.get("current_version")==v["version"] else "历史"}</a>' for v in reversed(item.get('versions',[]))) +
                 '</div></section>')
    if mode == 'history':
        parts.append('<div class="notice issue"><strong>历史版本</strong><p>__HISTORY_STATUS__</p></div>')

    totals = performance['data']['totals']
    parts.append('<section id="overview"><div class="split"><div><h2>经营概览</h2><p class="lead">' + ('以下数字仅覆盖当前筛选范围。' if filtered else '以下数字覆盖该关账版本的全月场次。') + f'</p></div><span class="chip">按{esc(LABELS.get(sort_by,sort_by))}排序</span></div><div class="metrics">')
    for key, css in [('sales',''), ('net_revenue',''), ('operating_profit','primary'), ('final_profit','primary')]:
        parts.append(f'<div class="metric {css}"><span>{esc(LABELS.get(key,key))}</span><strong>{esc(_money(totals[key]))}</strong></div>')
    for key in ('operating_margin', 'final_margin', 'ad_roi'):
        parts.append(f'<div class="metric"><span>{esc(LABELS.get(key,key))}</span><strong>{esc(_ratio(totals[key]))}</strong></div>')
    parts.append(f'<div class="metric"><span>已关账场次数</span><strong>{actual_scope["session_count"]}</strong></div></div>')
    parts.append('</section>')

    summary = diagnostics['data']['summary']
    parts.append('<section id="attention"><div class="split"><div><h2>重点场次</h2><p class="lead">规则命中是需要关注的事实，不代表数据错误或自动经营结论。</p></div><span class="chip">去重关注 ' + str(summary['attention_session_count']) + ' 场</span></div><div class="metrics">')
    parts.append(f'<div class="metric warn"><span>最终结算亏损</span><strong>{summary["final_loss"]["count"]} 场</strong></div>')
    parts.append(f'<div class="metric warn"><span>其中：经营盈利但最终亏损</span><strong>{summary["operating_profit_final_loss"]["count"]} 场</strong></div></div>')
    if summary['attention_session_count'] == 0:
        parts.append('<div class="notice">当前范围没有命中两项亏损关注规则；主要金额构成仍可在场次列表查看。</div>')
    for label, detail, offset_key in [
        ('最终结算亏损场次', loss_details, 'loss_offset'),
        ('其中：经营盈利但最终亏损', direction_details, 'direction_offset'),
    ]:
        paging = detail['data']['paging']
        rows = []
        for finding in detail['data']['items']:
            rows.append([finding['session_id'], _money(finding['facts']['operating_profit']),
                         _money(finding['facts']['final_profit']),
                         f'<a href="#session-{esc(finding["session_id"])}">查看场次</a>'])
        parts.append(f'<h3>{esc(label)} · 共 {paging["total_count"]} 场</h3>' +
                     table(['场次','经营复盘利润','最终结算利润','操作'], rows))
        links = []
        if paging['offset'] > 0:
            links.append(f'<a href="/analysis?{esc(_qs(query,**{offset_key:max(0,paging["offset"]-20)}))}#attention">上一页</a>')
        if paging['has_more']:
            links.append(f'<a href="/analysis?{esc(_qs(query,**{offset_key:paging["offset"]+20}))}#attention">下一页</a>')
        if links:
            parts.append('<div class="actions">' + ' · '.join(links) + '</div>')
    parts.append('</section>')

    groups = performance['data']['groups']
    major = {x['session_id']: x for x in component_details['data']['items']}
    filter_options = [(g['key'], g['label']) for g in groups]
    parts.append('<section id="sessions"><details><summary>场次与达人</summary><p class="lead">排名由完整筛选范围计算，翻页或页面展示不会改变名次。</p><form method="get" action="/analysis"><div class="grid">' +
                 field('month','',month,kind='hidden') + field('version','',str(version),kind='hidden') + field('mode','',mode,kind='hidden') +
                 field('session_id','场次编号（精确）',session_ids[0] if len(session_ids)==1 else '') +
                 field('talent','达人原始名称（精确）',talents[0] if len(talents)==1 else '') +
                 field('start_date','开播开始日期',actual_scope['start_date'] or '',kind='date') +
                 field('end_date','开播结束日期',actual_scope['end_date'] or '',kind='date') +
                 select('sort_by','排序利润口径',[('final_profit','最终结算利润'),('operating_profit','经营复盘利润')],sort_by) +
                 '<button>应用筛选</button></div></form><div class="table-wrap"><table><thead><tr><th>名次</th><th>场次</th><th>销售额</th><th>经营复盘利润</th><th>最终结算利润</th><th>主要金额构成</th><th>查看</th></tr></thead><tbody>')
    for group in groups:
        sid = group['key']; metrics = group['metrics']; components = major.get(sid, {}).get('facts', {}).get('components', [])
        comp_text = '；'.join(f'{LABELS.get(x["metric"],x["metric"])} {_money(x["amount"])}' for x in components[:3])
        final = metrics['final_profit']; negative = ' negative' if final['cents'] < 0 else ''
        parts.append(f'<tr id="session-{esc(sid)}"><td>{esc(group["rank"] if group["rank"] is not None else "—")}</td><td><strong>{esc(sid)}</strong></td><td class="money">{esc(_money(metrics["sales"]))}</td><td class="money">{esc(_money(metrics["operating_profit"]))}</td><td class="money{negative}">{esc(_money(final))}</td><td>{esc(comp_text)}</td><td><a href="/analysis?{esc(_qs(query,month=month,version=version,mode=mode,evidence_session=sid,topic="pnl"))}#evidence">查看证据</a></td></tr>')
    parts.append('</tbody></table></div></details></section>')

    evidence_sid = str(_value(query, 'evidence_session', ''))
    topic = str(_value(query, 'topic', 'pnl'))
    try:
        evidence_offset = max(0,int(_value(query,'evidence_offset',0)))
    except (TypeError,ValueError):
        evidence_offset = 0
    parts.append(f'<section id="evidence"><details {"open" if evidence_sid else ""}><summary>场次证据</summary><p class="lead">只显示当前问题所需的关账证据，不倾倒整份副本。</p>')
    if evidence_sid:
        evidence = service.get_session_evidence({'month': month, 'version': version}, evidence_sid,
                                                topic, mode, offset=evidence_offset, limit=20)
        if evidence['status'] == 'ok':
            parts.append('<div class="section-nav">' + ''.join(f'<a href="/analysis?{esc(_qs(query,month=month,version=version,mode=mode,evidence_session=evidence_sid,topic=t))}#evidence">{label}</a>' for t,label in [('pnl','利润科目'),('sku','SKU直接贡献'),('allocation','归属与分配'),('changes','人工修改')]) + '</div>')
            parts.append(_render_evidence(evidence))
            paging = evidence['data']['paging']
            page_links = []
            if paging['offset'] > 0:
                page_links.append(f'<a href="/analysis?{esc(_qs(query,evidence_offset=max(0,paging["offset"]-20)))}#evidence">上一页证据</a>')
            if paging['has_more']:
                page_links.append(f'<a href="/analysis?{esc(_qs(query,evidence_offset=paging["offset"]+20))}#evidence">下一页证据</a>')
            if page_links:
                parts.append('<div class="actions">' + ' · '.join(page_links) + '</div>')
            parts.append('<details><summary>来源引用与使用限制</summary>' + table(
                ['月份','版本','副本区域','场次'], [[esc(r.get('month')),esc(r.get('version')),esc(r.get('section')),esc(r.get('session_id') or '—')] for r in evidence.get('source_refs',[])]) +
                '<ul>' + ''.join(f'<li>{esc(x)}</li>' for x in evidence.get('limitations',[])) + '</ul></details>')
            sid_ref = _reference(actual_scope, session_id=evidence_sid, profit_metric=sort_by)
            parts.append('<div class="copy-box"><label for="session-ref">场次分析引用<textarea id="session-ref" readonly>' + esc(sid_ref) + '</textarea></label><button type="button" data-copy-target="session-ref" data-status-target="session-copy-status">复制场次引用</button></div><p id="session-copy-status" role="status" class="muted"></p>')
        else:
            parts.append(_error_block(evidence, item))
    else:
        parts.append('<div class="empty">请从上方场次列表选择“查看证据”。</div>')
    parts.append('</details></section>')

    parts.append(_render_comparisons(service, query, items, month, version, mode))
    parts.append(_render_scenario(query, month, version, mode, groups, scenario_result))
    rules = service.get_metric_rules(['net_revenue','operating_profit','final_profit','operating_margin','final_margin','ad_roi'])
    parts.append('<section id="rules"><details><summary>指标口径与不适用规则</summary>' + table(
        ['指标','公式','单位','分母','限制'], [[esc(r['name']),esc(r['formula']),esc(r['unit']),esc(r['denominator'] or '无'),esc(r['comparability'])] for r in rules['data']]) + '</details></section>')
    # Recheck only after every page component has finished reading. If an active
    # version changed meanwhile, discard all assembled figures instead of mixing.
    final_catalog = service.list_close_versions(include_history=True)
    if final_catalog['status'] != 'ok':
        blocked = _page_top('版本状态复核失败', state={'message': final_catalog['error']['message']})
        blocked.append(_error_block(final_catalog))
        return _end(blocked)
    checked_item = next((x for x in final_catalog['data'] if x['month'] == month), None)
    current_refs_to_recheck = {(month,version)} if mode == 'current' else set()
    if _value(query,'compare_base_month',''):
        for month_key, version_key in [
            ('compare_base_month','compare_base_version'),
            ('compare_current_month','compare_current_version'),
        ]:
            try:
                compared_month = str(_value(query,month_key))
                compared_version = int(_value(query,version_key))
            except (TypeError,ValueError):
                continue
            initial = next((x for x in items if x['month'] == compared_month), None)
            if initial and initial.get('status') == 'closed' and initial.get('current_version') == compared_version:
                current_refs_to_recheck.add((compared_month,compared_version))
    final_items = {x['month']:x for x in final_catalog['data']}
    current_changed = any(
        not final_items.get(ref_month) or final_items[ref_month].get('status') != 'closed' or
        final_items[ref_month].get('current_version') != ref_version
        for ref_month,ref_version in current_refs_to_recheck)
    if current_changed:
        blocked = _page_top('分析期间状态已经变化', state={
            'message': '本页读取期间发生了重开或新版本发布，原分析片段已全部丢弃。'})
        current = checked_item.get('current_version') if checked_item else None
        actions = f'<a class="button secondary" href="/analysis?month={esc(month)}&version={version}&mode=history">保留查看历史 V{version}</a>'
        if current:
            actions = f'<a class="button" href="/analysis?month={esc(month)}&version={current}&mode=current">查看当前 V{current}</a>' + actions
        blocked.append('<section><div class="notice danger" role="alert"><strong>版本已变化，未展示可能混版的金额。</strong></div><div class="actions">' + actions + '</div></section>')
        return _end(blocked)
    rendered = _end(parts).replace('__VERSION_CHECKED_AT__',esc(final_catalog['as_of']))
    if mode == 'history':
        if checked_item and checked_item.get('status') == 'closed' and checked_item.get('current_version'):
            history_status = (f'正在查看历史 V{version}。当前有效版本为 V{checked_item["current_version"]}，'
                              '本页不会自动切换。')
        else:
            history_status = f'正在查看历史 V{version}。月份已重开，当前无有效关账结果；本页仍固定在该历史版本。'
        rendered = rendered.replace('__HISTORY_STATUS__',esc(history_status))
    if '__VERSION_COMPARE_STATUS__' in rendered:
        try:
            compared_version = int(_value(query,'compare_version_current'))
        except (TypeError,ValueError):
            compared_version = None
        version_status = ('新版本当前有效。' if checked_item and checked_item.get('status') == 'closed'
                          and checked_item.get('current_version') == compared_version
                          else '所选新版本目前也属于历史版本。')
        rendered = rendered.replace('__VERSION_COMPARE_STATUS__',version_status)
    return rendered


def _render_evidence(result: dict) -> str:
    topic = result['data']['topic']; items = result['data']['items']
    if not items:
        return '<div class="empty">该主题没有保存的关账证据。</div>'
    if topic == 'pnl':
        item = items[0]
        rows = [[esc(LABELS.get(key,key)), esc(_money(value))] for key,value in item['amounts'].items()]
        rows += [[esc(LABELS.get(key,key)), esc(_ratio(value))] for key,value in item['ratios'].items()]
        return table(['指标','关账结果'], rows)
    if topic == 'sku':
        return table(['SKU','类型','直接贡献'], [[esc(x.get('sku') or '—'),esc(x.get('label') or 'SKU直接贡献'),esc(_money(x['contribution_amount']))] for x in items]) + '<p class="muted">SKU为直接贡献拆分，不是SKU全成本净利润；公共及共享费用未强行归到SKU。</p>'
    if topic == 'allocation':
        rows=[]
        for x in items:
            amount = _money(x['allocated_amount']) if x.get('allocated_amount') else f'归属标记 {x.get("assignment_weight")}'
            rows.append([esc(x.get('record_kind')),esc(x.get('business_key')),esc(amount),esc(x.get('basis') or '未记录'),esc(x.get('recommendation_id') or '—')])
        return table(['来源类型','业务键','本场结果','确认依据','候选编号'],rows)
    return table(['对象','业务键','原因','时间'], [[esc(x.get('object_type')),esc(x.get('object_key')),esc(x.get('reason') or '未记录'),esc(x.get('created_at') or '—')] for x in items])


def _render_comparisons(service, query, catalog, month, version, mode) -> str:
    versions = next((x.get('versions', []) for x in catalog if x['month'] == month), [])
    version_options = [(str(x['version']), f'V{x["version"]}') for x in versions]
    month_options = [(x['month'], x['month']) for x in catalog if x.get('current_version')]
    out = ['<section id="period-compare"><details><summary>期间比较</summary><p class="lead">比较两个完整业务范围；不是关账版本修订比较。</p><form method="get" action="/analysis"><div class="grid">',
           field('month','',month,kind='hidden'),field('version','',str(version),kind='hidden'),field('mode','',mode,kind='hidden'),
           select('compare_base_month','基期业务月',month_options,str(_value(query,'compare_base_month',month))),
           field('compare_base_version','基期版本',str(_value(query,'compare_base_version',version)),kind='number',required=True),
           select('compare_current_month','当期业务月',month_options,str(_value(query,'compare_current_month',month))),
           field('compare_current_version','当期版本',str(_value(query,'compare_current_version',version)),kind='number',required=True),
           '<button>比较期间</button></div></form>']
    if _value(query,'compare_base_month',''):
        base_month=str(_value(query,'compare_base_month')); current_month=str(_value(query,'compare_current_month',month))
        try:
            base_version=int(_value(query,'compare_base_version')); current_version=int(_value(query,'compare_current_version',version))
            def catalog_mode(selected_month, selected_version):
                selected = next((x for x in catalog if x['month'] == selected_month), None)
                return 'current' if selected and selected.get('current_version') == selected_version else 'history'
            result=service.compare_performance(
                {'close_refs':[{'month':base_month,'version':base_version}],
                 'mode':catalog_mode(base_month,base_version)},
                {'close_refs':[{'month':current_month,'version':current_version}],
                 'mode':catalog_mode(current_month,current_version)})
            if result['status']=='ok':
                def scope_identity(scope):
                    refs = '、'.join(f'{x["month"]} V{x["version"]}' for x in scope['close_refs'])
                    return f'{refs} · {scope["mode"]} · {scope["session_count"]} 场'
                rows=[[esc(LABELS.get(k,k)),esc(_money(v['base'])),esc(_money(v['current'])),esc(_money(v['delta']))] for k,v in result['data']['amounts'].items() if k in ('sales','net_revenue','operating_profit','final_profit')]
                out.append('<div class="grid two"><div class="notice"><strong>基期</strong><p>'+esc(scope_identity(result['base_scope']))+'</p></div><div class="notice"><strong>当期</strong><p>'+esc(scope_identity(result['current_scope']))+'</p></div></div>')
                out.append(table(['指标','基期','当期','变化'],rows))
                bridge = [[esc(LABELS.get(x['metric'],x['metric'])),esc(_money(x['amount_delta'])),esc(_money(x['profit_contribution']))] for x in result['data']['profit_bridge']]
                out.append('<h3>利润变化科目桥</h3>'+table(['科目','金额变化','对利润的贡献'],bridge))
                reference = ('比较两个完整业务范围：基期 ' + scope_identity(result['base_scope']) +
                             '；当期 ' + scope_identity(result['current_scope']) +
                             f'。利润口径：{result["data"]["profit_metric"]}。保持两侧版本，不自动切换最新版。')
                out.append('<div class="copy-box"><label for="period-ref">期间比较引用<textarea id="period-ref" readonly>'+esc(reference)+'</textarea></label><button type="button" data-copy-target="period-ref" data-status-target="period-copy-status">复制期间比较引用</button></div><p id="period-copy-status" role="status" class="muted"></p>')
            else: out.append(_error_block(result))
        except (TypeError,ValueError): out.append('<div class="notice danger">比较版本必须是正整数。</div>')
    out.append('</details></section><section id="version-compare"><details><summary>关账版本比较</summary><p class="lead">仅比较同一业务月的两个不可变关账版本。</p><form method="get" action="/analysis"><div class="grid">')
    out += [field('month','',month,kind='hidden'),field('version','',str(version),kind='hidden'),field('mode','',mode,kind='hidden'),
            select('compare_version_base','原版本',version_options,str(_value(query,'compare_version_base',''))),
            select('compare_version_current','新版本',version_options,str(_value(query,'compare_version_current',''))),
            '<button>比较版本</button></div></form>']
    if _value(query,'compare_version_base','') and _value(query,'compare_version_current',''):
        result=service.compare_close_versions(month,int(_value(query,'compare_version_base')),int(_value(query,'compare_version_current')),limit=100)
        if result['status']=='ok':
            def displayed_change(item):
                if item.get('final_profit_delta'):
                    return item['final_profit_delta']
                side = item.get('current') or item.get('base') or {}
                return side.get('final_profit', {'yuan_text': '—'})
            rows=[[esc(x['session_id']),esc(x['change']),esc(_money(displayed_change(x)))] for x in result['data']['session_changes']]
            out.append('<div class="notice"><strong>'+esc(f'{month} V{result["scope"]["base_version"]} → V{result["scope"]["current_version"]}')+'</strong><p>__VERSION_COMPARE_STATUS__</p></div>')
            out.append(table(['场次','变化','最终利润/差额'],rows))
            out.append('<h3>来源记录变化</h3>'+table(['来源类型','业务键','变化'],[[esc(x.get('record_kind')),esc(x.get('business_key')),esc(x.get('change'))] for x in result['data']['changed_sources']]))
            out.append('<h3>归属与分配变化</h3>'+table(['来源类型','业务键','变化','当前依据'],[[esc(x.get('record_kind')),esc(x.get('business_key')),esc(x.get('change')),esc(x.get('current_basis') or '—')] for x in result['data']['changed_assignments']]))
            out.append('<h3>业务适用性变化</h3>'+table(['场次','原值','新值'],[[esc(x.get('session_id')),esc(json.dumps(x.get('base'),ensure_ascii=False)),esc(json.dumps(x.get('current'),ensure_ascii=False))] for x in result['data']['changed_applicability']]))
            reference = (f'比较同一业务月 {month} 的两个关账版本：V{result["scope"]["base_version"]} 与 V{result["scope"]["current_version"]}。'
                         '解释场次结果、来源记录、分配与适用性变化；保持这两个版本，不自动切换最新版。')
            out.append('<div class="copy-box"><label for="version-ref">版本比较引用<textarea id="version-ref" readonly>'+esc(reference)+'</textarea></label><button type="button" data-copy-target="version-ref" data-status-target="version-copy-status">复制版本比较引用</button></div><p id="version-copy-status" role="status" class="muted"></p>')
        else: out.append(_error_block(result))
    out.append('</details></section>')
    return ''.join(out)


def _render_scenario(query, month, version, mode, groups, result) -> str:
    session_options=[(g['key'],g['key']) for g in groups]
    fee_options=[(x,LABELS.get(x,x)) for x in SCENARIO_FEE_FIELDS]
    out=['<section id="scenario"><details ', 'open' if result else '', '><summary>条件测算</summary><p class="lead">每次从同一关账基准独立计算；测算不会修改实际数据。</p><form method="post" action="/analysis/scenario"><div class="grid">',
         field('month','',month,kind='hidden'),field('version','',str(version),kind='hidden'),field('mode','',mode,kind='hidden'),
         select('session_id','明确场次',session_options,''),
         select('assumption_type','假设类型',[('session_fee','指定场次费用'),('sku_unit_cost','SKU单位成本')],'session_fee'),
         select('fee_field','费用科目',fee_options,'slot_fee'),
         select('operation','费用操作',[('set','设为目标金额'),('delta','增加/减少')],'delta'),
         field('amount_yuan','费用金额（元；减少请输入负数）'),
         field('sku','SKU原始名称'),field('unit_cost','目标单位成本（元）'),
         '<button>运行非持久化测算</button></div></form>']
    if result:
        if result['status']=='ok':
            total=result['data']['total']
            rows=[]
            for key in ('operating_profit','final_profit','ad_spend','product_cost'):
                rows.append([LABELS.get(key,key),_money(total['baseline']['amounts'][key]),_money(total['scenario']['amounts'][key]),_money(total['delta'][key])])
            baseline=result['baseline_close_ref']
            trace=result['assumption_trace']
            source_label='用户直接给定' if trace['source']=='user_provided' else 'Agent在用户声明范围内提出'
            trace_html='<p><strong>假设来源：</strong>'+esc(source_label)+'。'+esc(trace['authorization_notice'])+'</p>'
            if trace['authorization_scope'] is not None:
                trace_html += '<details><summary>查看声明的授权范围</summary><pre>'+esc(json.dumps(trace['authorization_scope'],ensure_ascii=False,indent=2))+'</pre></details>'
            out.append('<div class="notice issue"><strong>假设测算，非实际结算结果</strong><p>基准 '+esc(f'{baseline["month"]} V{baseline["version"]} · {baseline["mode"]}')+'；persisted=false，不会保存到工作台，也不会生成新关账版本。</p>'+trace_html+'</div><h3>本次类型化假设</h3><pre>'+esc(json.dumps(result['assumptions'],ensure_ascii=False,indent=2))+'</pre>'+table(['指标','关账基准','假设结果','变化'],rows)+'<h3>保持不变的条件</h3><ul>'+''.join(f'<li>{esc(x)}</li>' for x in result['unchanged_conditions'])+'</ul>')
            assumption=json.dumps(result['assumptions'],ensure_ascii=False)
            authorization=json.dumps(trace['authorization_scope'],ensure_ascii=False) if trace['authorization_scope'] is not None else '不适用（用户直接给定）'
            reference=(f'基于{month} V{version}（{mode}模式）运行以下假设：{assumption}。'
                       f'假设来源：{source_label}；声明的授权范围：{authorization}。'
                       '来源标记仅用于追溯，不能替代真正授权。保持该版本，不自动切换最新版；这是条件测算，不是实际结算结果。')
            out.append('<div class="copy-box"><label for="scenario-ref">测算分析引用<textarea id="scenario-ref" readonly>'+esc(reference)+'</textarea></label><button type="button" data-copy-target="scenario-ref" data-status-target="scenario-copy-status">复制测算引用</button></div><p id="scenario-copy-status" role="status" class="muted"></p>')
        else: out.append(_error_block(result))
    out.append('</details></section>')
    return ''.join(out)
