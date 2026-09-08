"""Single-user local workbench. Start: python3 -m src.workbench.app."""

import argparse
import html
import json
import secrets
import sqlite3
import threading
import time
from datetime import date
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, quote

from src.analysis.reader import AnalysisError
from src.analysis.service import AnalysisService
from src.analysis.web_api import ROUTES as ANALYSIS_ROUTES, dispatch as dispatch_analysis
from src.engine.session_profit import money_to_cents
from src.reports.management import LABELS, export_management
from src.workbench.attribution import assign, assign_orders, assignments, filter_sources
from src.workbench.closing import close_month, get_close, history, preview_close, preview_operating_profit, reopen_month
from src.workbench.costs import FLAGS, save_cost, set_applicability, settings
from src.workbench.db import DB_PATH, clear_all_data, connect, records, session_map
from src.workbench.imports import SCHEMAS, import_workbook, make_template, preview_workbook, recent_batches


from src.workbench.presentation import esc, currency, field, select, table, business_name
from src.workbench import analysis_ui, m2_ui
from src.workbench.workflow import order_queue, FILTER_KEYS
from src.workbench.scope import resolve_scope, scoped_records
from src.workbench.recommendations import generate, save_ad_link, latest
from src.workbench.costs import save_standard
from src.workbench.batches import (prepare_assignments, prepare_flags, prepare_cost_fill,
                                   prepare_multi_cost_fill, prepare_allocation_reuse,
                                   prepare_cost_edit, apply_batch)


def display_record(row):
    values = []
    for name, label, typ in SCHEMAS[row['kind']][1]:
        value = row['data'][name]
        if value is None:
            value = '未取得'
        elif typ in ('money?', 'optional') and type(value) is int:
            value = currency(value) + ' 元'
        values.append(f'{label}：{value}')
    return '；'.join(values)


def describe_change(raw):
    value = json.loads(raw) if raw else None
    if value is None:
        return '无（首次录入）'
    if isinstance(value, dict) and 'data' in value:
        value = value['data']
        if isinstance(value, str):
            value = json.loads(value)
    labels = {f: label for _, fields in SCHEMAS.values() for f, label, _ in fields}
    labels.update(FLAGS)
    labels.update(targets='目标场次及结果（订单为归属标记，费用为分）', weights='权重', mode='分配方式',
                  basis='依据', source_amount='来源金额（分）', tail_cents='尾差（分）', tail_rule='尾差规则')
    return '；'.join(f'{labels.get(k, k)}：{v}' for k, v in value.items()) if isinstance(value, dict) else str(value)


CSS = '''
:root{--ink:#153b3a;--muted:#526968;--primary:#0f766e;--primary-dark:#0b5b55;--accent:#c8490b;--paper:#fff;--wash:#f3f8f6;--line:#cfe0dc;--soft:#e4f2ee;--warn:#8a3b12;--warn-bg:#fff4e8;--danger:#b42318;--shadow:0 12px 35px rgba(21,59,58,.08);--r:14px}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:linear-gradient(135deg,#edf7f3 0,#f8faf8 42%,#f4f7f5 100%);color:var(--ink);font:16px/1.58 "Avenir Next","PingFang SC","Noto Sans CJK SC",sans-serif;min-height:100vh}
.skip-link{position:absolute;left:16px;top:-60px;background:#fff;padding:10px 14px;z-index:1000}.skip-link:focus{top:12px}
header{padding:18px max(24px,calc((100vw - 1440px)/2));background:#123e3b;color:#fff;border-bottom:4px solid #e96b2c;display:flex;justify-content:space-between;align-items:center;gap:20px}header h1{font-size:24px;margin:0;font-weight:700;letter-spacing:.01em}header p{margin:3px 0 0;color:#cfe7e2;font-size:14px}.global-nav{display:flex;gap:6px;flex-wrap:wrap}.global-nav a{color:#dff3ef;text-decoration:none;padding:9px 13px;border-radius:8px;font-weight:700}.global-nav a:hover,.global-nav a[aria-current=page]{background:#fff;color:var(--primary-dark)}
.app-shell{max-width:1440px;margin:auto;padding:28px 24px 72px;display:grid;grid-template-columns:260px minmax(0,1fr);gap:28px;align-items:start}
.workflow-rail{position:sticky;top:20px;background:#fff;border:1px solid var(--line);border-radius:var(--r);padding:18px;box-shadow:var(--shadow)}.rail-title{font-size:12px;color:var(--muted);font-weight:700;letter-spacing:.12em;text-transform:uppercase;margin:0 0 12px}
.step-link{display:grid;grid-template-columns:36px 1fr;gap:10px;padding:12px 8px;border-radius:10px;color:var(--ink);text-decoration:none;margin:4px 0;transition:background .18s ease,color .18s ease}.step-link:hover{background:var(--wash)}.step-link.active{background:var(--soft);color:var(--primary-dark)}.step-link:focus-visible{outline:3px solid #fdba74;outline-offset:2px}.step-number{width:32px;height:32px;border:1px solid #9fc3bb;border-radius:50%;display:grid;place-items:center;font-weight:700;font-variant-numeric:tabular-nums}.step-link.done .step-number{background:var(--primary);border-color:var(--primary);color:#fff}.step-link.active .step-number{box-shadow:0 0 0 4px rgba(15,118,110,.14)}.step-label{font-weight:700;display:block}.step-meta{font-size:12px;color:var(--muted);display:block;margin-top:1px}
main{min-width:0}.page-head{background:transparent;border:0;padding:2px 0 18px;margin:0}.eyebrow,.section-kicker{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--primary);font-weight:800}.page-head h2{font-size:30px;line-height:1.2;margin:6px 0 8px;letter-spacing:-.02em}.page-head p{max-width:720px;color:var(--muted);margin:0}
.period-bar{display:flex;justify-content:space-between;align-items:end;gap:20px;background:#fff;border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;margin-bottom:18px}.period-bar form{margin:0}.period-summary{font-size:14px;color:var(--muted)}.period-summary strong{display:block;color:var(--ink);font-size:17px}
.step-view{display:none}body.step-1 .step-1,body.step-2 .step-2,body.step-3 .step-3,body.step-4 .step-4{display:block}
section{background:var(--paper);border:1px solid var(--line);border-radius:var(--r);padding:24px;margin-bottom:20px;scroll-margin-top:24px;box-shadow:0 2px 0 rgba(21,59,58,.02)}
h2{font-size:22px;line-height:1.3;margin:0 0 10px}h3{font-size:17px;margin:20px 0 8px}.section-lead{color:var(--muted);max-width:760px;margin:0 0 18px}.muted,small{color:var(--muted)}
.grid{display:flex;gap:14px;flex-wrap:wrap;align-items:end}label{display:block;flex:1;min-width:170px;font-size:13px;font-weight:700}
input,select,textarea{font:inherit;font-size:16px;width:100%;min-height:44px;padding:9px 11px;border:1px solid #9dbbb4;border-radius:8px;margin-top:5px;background:#fff;color:var(--ink)}input[type=checkbox],input[type=radio]{width:20px;height:20px;min-height:0;padding:0;margin:0 9px 0 0;accent-color:var(--primary);flex:none}input:focus-visible,select:focus-visible,button:focus-visible,.button:focus-visible,summary:focus-visible,a:focus-visible{outline:3px solid #fdba74;outline-offset:2px}
button,.button{display:inline-flex;align-items:center;justify-content:center;min-height:44px;background:var(--accent);color:#fff;padding:10px 18px;border:0;border-radius:8px;font:700 15px/1.2 inherit;text-decoration:none;cursor:pointer;transition:background .18s ease,box-shadow .18s ease}button:hover,.button:hover{background:#a83b08;box-shadow:0 4px 12px rgba(200,73,11,.18)}button.secondary{background:var(--soft);color:var(--primary-dark)}button.secondary:hover{background:#d2e9e3;box-shadow:none}
.table-wrap{overflow:auto;margin:14px 0;border:1px solid var(--line);border-radius:10px}table{border-collapse:collapse;min-width:100%;font-size:13px;background:#fff}th{text-align:left;white-space:nowrap;background:#eaf4f1;color:#244d49;font-size:12px;letter-spacing:.02em}td,th{border-bottom:1px solid #dce9e5;padding:11px 12px;vertical-align:top}tbody tr:last-child td{border-bottom:0}tbody tr:hover{background:#f8fbfa}td{min-width:96px}td.long{max-width:500px;overflow-wrap:anywhere}
details{border-top:1px solid var(--line);padding:14px 0}details[id]{scroll-margin-top:24px}details:first-of-type{border-top:0}summary{cursor:pointer;font-weight:750;min-height:32px}.notice{padding:15px 18px;border:1px solid #9fc9c0;border-left:5px solid var(--primary);border-radius:10px;background:#eaf6f2;margin-bottom:18px;overflow-wrap:anywhere}.notice.issue{border-color:#edb88f;border-left-color:#c55b17;background:var(--warn-bg)}.notice[role=alert]{border-left-color:var(--danger)}.danger{color:var(--danger)}.check{display:flex;align-items:flex-start;font-size:14px;margin:14px 0;min-height:28px}.check input{flex:none;margin-top:2px}
.status{display:inline-flex;border-radius:999px;padding:3px 9px;font-size:12px;font-weight:750;background:#e7f4ef;color:#176458}.status.pending{background:#fff0df;color:#8a3b12}.status.done{background:#dff3ea;color:#0f654f}
.task-grid,.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:16px 0}.task-card,.metric{background:var(--wash);border:1px solid var(--line);border-radius:10px;padding:16px}.task-card strong,.metric strong{display:block;font-size:24px;line-height:1.2;margin-top:5px;font-variant-numeric:tabular-nums}.metric{min-width:0}.next-bar{display:flex;justify-content:space-between;align-items:center;gap:18px;border-left:5px solid var(--primary)}.next-bar p{margin:0}.empty-state{text-align:center;padding:46px 24px}.empty-state h2{margin-top:0}
.operation-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:18px 0}.operation-card{position:relative;border:1px solid var(--line);border-radius:12px;padding:18px;background:var(--wash)}.operation-card:has(input:checked){border-color:var(--primary);box-shadow:0 0 0 3px rgba(15,118,110,.12);background:#fff}.operation-card label{font-size:17px;color:var(--ink)}.operation-card p{color:var(--muted);margin:8px 0 0}.scope-ribbon{border-left:5px solid var(--accent);background:#fffaf5}.mapping{font-variant-numeric:tabular-nums;font-weight:700}
.action-strip,.split-head{display:flex;align-items:center;justify-content:space-between;gap:18px}.action-strip{padding:14px 16px;background:var(--wash);border:1px solid var(--line);border-radius:10px;margin:16px 0}.action-strip form{margin:0}.action-strip span{display:block;color:var(--muted);font-size:13px}.split-head{margin-top:26px}.split-head h3{margin:5px 0 0}.split-head p{margin:3px 0 0;color:var(--muted);font-size:14px}.split-head>strong{font-size:22px;white-space:nowrap}.empty-inline{padding:14px 16px;border:1px dashed #a9c8c1;border-radius:9px;background:#f9fcfb;color:var(--muted);margin:10px 0 18px}.closing-checks ul{margin-bottom:8px}.closing-checks .grid{margin-top:12px}
pre{white-space:pre-wrap;word-break:break-word}a{color:var(--primary-dark)}form{margin:12px 0}footer{color:var(--muted);text-align:center;padding:24px}.visually-hidden{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:900px){.app-shell{grid-template-columns:1fr;padding:18px 16px 56px}.workflow-rail{position:static;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px}.rail-title{display:none}.step-link{min-width:0}.page-head h2{font-size:26px}.task-grid,.metrics{grid-template-columns:1fr 1fr}}
@media(max-width:600px){body{font-size:16px}header{align-items:stretch;flex-direction:column}.global-nav a{flex:1;text-align:center}.app-shell{padding:14px 12px 48px}.workflow-rail{padding:10px;grid-template-columns:1fr 1fr}.step-meta{white-space:normal}.period-bar,.next-bar,.action-strip,.split-head{align-items:stretch;flex-direction:column}.grid{display:grid;grid-template-columns:1fr}.task-grid,.metrics,.operation-grid{grid-template-columns:1fr}section{padding:18px}.table-wrap{margin-left:-6px;margin-right:-6px}.page-head h2{font-size:24px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*,*::before,*::after{transition:none!important;animation:none!important}}
'''


def render_page(conn, csrf, query=None, notice='', import_preview=None, batch_preview=None):
    q = query or {}
    month = q.get('month', date.today().strftime('%Y-%m'))
    q = dict(q) | {'month': month}
    q.setdefault('work_scope', 'month')
    scope = resolve_scope(conn,q)
    def step_url(target, **updates):
        return esc('/workbench?' + urlencode({k:v for k,v in (q | {'step':str(target)} | updates).items() if k in FILTER_KEYS}))
    sessions = session_map(conn)
    scoped_sessions = {s:d for s,d in sessions.items() if s in scope['session_ids']}
    month_choices = sorted({s['start'][:7] for s in sessions.values()} | {month})
    current_sessions = {sid: s for sid, s in sessions.items() if s['start'][:7] == month}
    session_choices = [(sid, f'{sid} · {s["talent"]} · {s["start"]}—{s["end"][11:]}') for sid, s in sessions.items()]
    snapshot = preview_close(conn, month)
    active = get_close(conn, month)
    version = int(q['version']) if q.get('version') else None
    operating_view = not active and not version and q.get('profit_view') != 'full'
    operating_preview = preview_operating_profit(conn, month, None if scope['mode']=='month' else sorted(scope['session_ids']))
    local_ready = not operating_preview['issues']
    displayed = get_close(conn, month, version) if version else (active or snapshot)
    state_text = f'已关账 V{active["version"]}' if active else '未关账 / 待复核'
    all_records = records(conn)
    order_progress, _ = order_queue(conn, {k:q[k] for k in ('month','work_scope','batch_id','batch_action','scope_session_id') if k in q} | {'order_status':'all'})
    order_total = len(order_progress)
    order_done = sum(r['state'] in ('confirmed','closed') for r in order_progress)
    imported = bool(all_records)
    attribution_done = imported and order_total > 0 and order_done == order_total
    close_ready = attribution_done and not snapshot['issues']
    recommended_step = '1' if not imported else ('2' if not attribution_done else ('3' if not local_ready else '4'))
    step = q.get('step') if q.get('step') in ('1','2','3','4') else recommended_step
    q['step'] = step
    step_titles = {
        '1':('数据导入','上传、核对并确认本月经营数据。入账完成后再进入订单归属。'),
        '2':('确认订单属于哪场','系统按支付时间生成候选；你只需确认明确候选并处理少量异常。'),
        '3':('业务与费用确认','确认收入与直接成本；月度间接费用可后补，先预览经营利润。'),
        '4':('关账与导出','月中预览经营利润，月末核对完整费用并关账导出。'),
    }
    nav_steps = [
        ('1','数据导入',f'已入账 {len(all_records)} 条' if imported else '等待导入',imported),
        ('2','确认订单属于哪场',f'{order_done}/{order_total} 已确认' if order_total else '等待订单',attribution_done),
        ('3','费用确认','当前范围可预览' if local_ready else f'当前范围待核对 {len(operating_preview["issues"])} 项',local_ready),
        ('4','关账导出',f'已关账 V{active["version"]}' if active else ('可以整月关账' if close_ready else ('可预览当前范围' if local_ready else '当前范围待核对')),bool(active)),
    ]

    def form(action, body, label='保存', *, upload=False):
        anchor = {'preview':'import','import':'import','reset-all':'reset','allocate':'allocation',
                  'cost':'costs','close':'close','reopen':'history'}.get(action,'')
        target = f'/{action}' + (f'#{anchor}' if anchor else '')
        return f'<form method="post" action="{target}" {"enctype=multipart/form-data" if upload else ""}>' + field('csrf', '', csrf, kind='hidden') + m2_ui.context(q) + body + f'<p><button>{esc(label)}</button></p></form>'

    title, subtitle = step_titles[step]
    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>直播经营复盘工作台</title><style>', CSS,
        '</style></head><body class="step-',step,'"><a class="skip-link" href="#main">跳到主要内容</a><header><div><h1>直播经营复盘工作台</h1><p>数据与计算可信 · 人工决定可追溯</p></div><nav class="global-nav" aria-label="全局导航"><a href="/months">月份总览</a><a href="',step_url(step),'" aria-current="page">结算工作台</a><a href="/analysis">经营分析</a></nav></header><div class="app-shell"><nav class="workflow-rail" aria-label="关账流程"><p class="rail-title">本月关账流程</p>',
        ''.join(f'<a class="step-link {"active" if sid==step else ""} {"done" if done else ""}" href="{step_url(sid)}" {"aria-current=step" if sid==step else ""}><span class="step-number">{sid}</span><span><span class="step-label">{label}</span><span class="step-meta">{meta}</span></span></a>' for sid,label,meta,done in nav_steps),
        '</nav><main id="main" tabindex="-1"><div class="period-bar"><div class="period-summary"><span>当前业务期间</span><strong>',esc(month),' · ',str(len(current_sessions)),' 场 · ',esc(state_text),'</strong></div><form method="get" action="/workbench"><div class="grid">',field('step','',step,kind='hidden'),select('month','切换月份（查看该月全部）',[(m,m) for m in month_choices],month),'<button class="secondary">切换</button></div></form></div><div class="page-head"><div class="eyebrow">步骤 ',step,' / 4</div><h2>',esc(title),'</h2><p>',esc(subtitle),'</p></div>']
    parts.append(m2_ui.render_scope(conn,q))
    if batch_preview:
        parts.append(m2_ui.render_preview(csrf,q,*batch_preview))
    if notice:
        role = 'alert' if notice.startswith('操作未完成') else 'status'
        parts.append(f'<div class="notice" role="{role}" tabindex="-1">{esc(notice)}</div>')
    parts.append('<div class="step-view step-1"><section id="import"><div class="section-kicker">步骤 1</div><h2>新增或补充场次数据</h2><p class="section-lead">先说明本次操作，再上传标准文件。导入批次不等于直播场次；文件上传不会自动确认订单归属或业务适用性。</p><p><a class="button secondary" href="/template">下载六表标准模板</a></p>')
    operation = '<div class="operation-grid"><div class="operation-card"><label class="check"><input type="radio" name="import_mode" value="new" required>新增直播场次</label><p>为文件中的每场直播生成新的内部编号；来源编号只作为本批次映射保留。</p></div><div class="operation-card"><label class="check"><input type="radio" name="import_mode" value="supplement" required>补充或更正已有场次</label><p>保留订单业务键，逐项区分新增、未变、更新与冲突；不会按所选场次强制改绑订单。</p></div></div>'
    operation += '<details><summary>补充模式：选择本次处理的已有场次（可多选）</summary>' + m2_ui.session_checks(current_sessions,'selected_session_id') + '</details>'
    operation += '<div class="grid">' + field('file', '本次导入文件（.xlsx，最大20MB）', kind='file', required=True) + '</div>'
    parts.append(form('preview', operation, '预览本次导入', upload=True))
    if import_preview:
        token, filename, result = import_preview
        parts.append(f'<h3>{esc(filename)} · 待入账 {len(result["rows"])} 行 · 本次新增 {result["new_count"]} · 更新 {result["update_count"]} · 未变 {result["unchanged"]} · 需处理 {result["conflict_count"]}</h3>')
        if result['session_mappings']:
            parts.append('<details open><summary>新场次来源编号映射</summary>' + table(['文件来源编号','新内部场次编号'],[[esc(k),f'<span class="mapping">{esc(v)}</span>'] for k,v in result['session_mappings'].items()]) + '</details>')
        if result['warnings']:
            parts.append('<div class="notice issue"><strong>需要确认的场次冲突或疑似重复</strong><ul>' + ''.join(f'<li>{esc(i)}</li>' for i in result['warnings']) + '</ul></div>')
        parts.append('<ul>' + ''.join(f'<li class="danger">{esc(i)}</li>' for i in result['issues']) + '</ul>')
        for update in result['updates']:
            def preview_value(value, typ):
                if type(value) is int and typ in ('money?', 'optional'):
                    return currency(value) + ' 元'
                return '未取得' if value is None else value
            differences = [[esc(label), esc(preview_value(update['old'][f], typ)), esc(preview_value(update['new'][f], typ))] for f,label,typ in SCHEMAS[update['kind']][1] if update['old'][f] != update['new'][f]]
            parts.append(f'<details open><summary>{esc(SCHEMAS[update["kind"]][0])} {esc(update["key"])} 更新差异</summary>' + table(['字段','原值','新值'], differences) + '</details>')
        if not result['issues']:
            body = field('upload_token','',token,kind='hidden') + field('import_mode','',result['context']['import_mode'],kind='hidden')
            body += ''.join(field('selected_session_id','',sid,kind='hidden') for sid in result['context']['selected_session_ids'])
            body += '<label class="check"><input type="checkbox" name="confirm_updates" value="yes">已核对以上同键更新差异</label>'
            if result['warnings']:
                body += '<label class="check"><input type="checkbox" name="confirm_new_session" value="yes" required>已核对冲突，确认这些记录应创建为新场次</label>' + field('duplicate_basis','编号冲突、疑似重复或并行直播的核对依据',required=True)
            body += field('reason','本次新增/更新依据（有更新时必填）')
            parts.append(form('import', body, '确认本次入账'))
    batches = recent_batches(conn,10)
    parts.append('<details><summary>最近导入批次与问题</summary>' + table(['批次','文件','操作','新增/更新/未变/需处理','状态','问题'], [[esc(r['id']),esc(r['filename']),esc('新增场次' if r['import_mode']=='new' else ('补充更正' if r['import_mode']=='supplement' else '旧流程')),esc(f'{r["new_count"] or 0}/{r["update_count"] or 0}/{r["unchanged_count"] or 0}/{r["conflict_count"] or 0}'),esc('已入账' if r['status']=='accepted' else '未入账'),esc('；'.join(json.loads(r['preview'])['issues']))] for r in batches]) + '</details></section>')
    if batches or records(conn):
        parts.append('<section id="reset"><details><summary>清空当前测试数据</summary><p class="danger">这会删除全部导入批次、场次、订单、费用、归属、候选、关账版本和修改记录，不可撤销。</p>'
                     + form('reset-all', field('confirmation','输入“清空全部数据”以确认',required=True), '清空并重新开始') + '</details></section>')
    if imported:
        parts.append('<section class="next-bar"><p><strong>数据已入账。</strong><br><span class="muted">下一步确认订单属于哪场直播。</span></p><a class="button" href="'+step_url(2)+'">进入订单归属</a></section>')
    parts.append('</div><div class="step-view step-2">')

    decisions = assignments(conn)
    attribution_notice = notice if notice.startswith('已保存') and '候选记录' in notice else ''
    if attribution_done:
        parts.append('<section class="next-bar"><p><strong>订单归属已完成：'+str(order_done)+'/'+str(order_total)+'。</strong><br><span class="muted">无需继续处理时，可直接进入下一步。</span></p><a class="button" href="'+step_url(3)+'">进入费用确认</a></section>')
    parts.append(m2_ui.render_orders(conn,csrf,q,attribution_notice))
    if not imported:
        parts.append('<section class="empty-state"><h2>请先导入数据</h2><p class="muted">完成数据入账后，这里会显示订单候选和异常队列。</p><a class="button" href="'+step_url(1)+'">返回数据导入</a></section>')
    parts.append('</div><div class="step-view step-3">')
    month_ads = scoped_records(conn,q,('ads',))
    month_monthly = [r for r in records(conn,'monthly') if r['data']['month'] == month]
    flag_done = sum(sid in settings(conn) for sid in scoped_sessions)
    ad_done = sum(r['id'] in decisions for r in month_ads)
    monthly_done = sum(r['id'] in decisions for r in month_monthly)
    parts.append('<div class="notice"><strong>月度费用可后补，先看经营利润</strong><p>仓储、人工、管理、损益税费及其他结算调整不纳入经营利润预览。留空表示待补充，不代表实际零元；已录入的月度费用会保留。</p><a class="button secondary" href="'+step_url(4,profit_view="operating")+'#close">先看经营利润</a></div>')
    parts.append('<div class="task-grid"><div class="task-card">当前范围 · 场次业务范围<strong>'+str(flag_done)+' / '+str(len(scoped_sessions))+'</strong><span class="muted">场已确认</span></div><div class="task-card">当前范围 · 投流分配<strong>'+str(ad_done)+' / '+str(len(month_ads))+'</strong><span class="muted">笔已确认</span></div><div class="task-card">整月共享 · 月度费用<strong>'+str(monthly_done)+' / '+str(len(month_monthly))+'</strong><span class="muted">笔已分配</span></div></div>')
    if snapshot['issues']:
        if scope['mode']!='month':
            parts.append('<details><summary>查看整月关账待办（含其他场次）</summary>')
        parts.append('<section id="closing-checks" class="notice issue closing-checks"><div class="section-kicker">整月关账检查</div><h2>距离关账还差 '+str(len(snapshot['issues']))+' 项</h2><p>以下按整月检查，不代表所选场次无法预览经营利润。这些条件包含业务范围、订单归属、投流和共享费用分配，不等同于下面的“实际费用记录待办”。</p><ul>'+''.join('<li>'+esc(issue)+'</li>' for issue in snapshot['issues'][:10])+'</ul>')
        if len(snapshot['issues']) > 10:
            parts.append('<p><a href="/issues.csv?month='+esc(month)+'">下载全部 '+str(len(snapshot['issues']))+' 项</a></p>')
        parts.append('<div class="grid"><a href="#scope">处理业务范围</a><a href="#allocation">处理投流与共享费用分配</a><a href="#costs">处理实际费用记录</a></div></section>')
        if scope['mode']!='month':
            parts.append('</details>')
    if not attribution_done:
        parts.append('<div class="notice issue"><strong>建议先完成订单归属。</strong> 未归属订单会让商品履约费用无法确定到场次。你仍可查看本页，但应先返回步骤2。</div>')
    parts.append('<div class="notice"><strong>当前范围经营利润：' + ('数据齐备，可进入步骤4预览' if local_ready else '仍需核对') + '</strong><ul>' + ''.join('<li>'+esc(i)+'</li>' for i in operating_preview['issues']) + '</ul></div>')
    parts.append(m2_ui.render_reuse(conn,csrf,q))
    parts.append('<section id="allocation"><div class="section-kicker">任务 3B</div><h2>确认投流与共享费用分配</h2><p class="section-lead">先处理有明确建议的投流，再展开异常记录人工分配；每笔来源金额都必须完整分配。</p>')
    parts.append(m2_ui.render_ads(conn,csrf,q))
    parts.append(m2_ui.render_monthly_reuse(conn,csrf,q))
    allocation_rows = scoped_records(conn,q,('ads','monthly'))
    allocation_suggestions = latest(conn)
    allocation_page = max(1,int(q.get('allocation_page',1)))
    parts.append(f'<p>手工分配明细共 {len(allocation_rows)} 笔，每页25笔。' + ' · '.join(f'<a href="/workbench?{esc(urlencode(q|{"allocation_page":str(n)}))}#allocation">第 {n} 页</a>' for n in range(1,(len(allocation_rows)+24)//25+1)) + '</p>')
    for row in allocation_rows[(allocation_page-1)*25:allocation_page*25]:
        if row['kind'] == 'monthly' and row['data']['month'] != month:
            continue
        choices = current_sessions if row['kind'] == 'monthly' else sessions
        previous = decisions.get(row['id'],{})
        recommendation = allocation_suggestions.get(row['id']) if row['kind'] == 'ads' else None
        recommendation_issues = (recommendation or {}).get('data',{}).get('issues',[])
        manual_state = '已确认分配' if previous else ('待人工分配' if recommendation_issues else '待分配')
        parts.append(f'<details id="manual-{row["id"]}" {"open" if q.get("manual_open")==str(row["id"]) else ""}><summary>{esc(SCHEMAS[row["kind"]][0])}（{"整月共享" if row["kind"]=="monthly" else "完整来源分配，保留跨场受益范围"}） · {esc(row["business_key"])} · {esc(row["data"].get("amount") if type(row["data"].get("amount")) is not int else currency(row["data"]["amount"]))} 元 · {manual_state}</summary><p>{esc(display_record(row))}</p>')
        if previous:
            parts.append('<p class="muted">当前分配已经确认。只有重新导入并修改来源金额时，才需要重新确认。</p>')
        elif recommendation_issues:
            parts.append('<div class="notice issue"><strong>需要人工判断：</strong> '+esc('；'.join(recommendation_issues))+'</div>')
        body = field('record_id','',row['id'],kind='hidden') + select('mode','分配方式',[('weights','非负整数权重'),('amounts','明确金额（元）')], previous.get('mode','weights'))
        body += '<p>只填写受益场次；留空代表不参与。明确金额合计必须等于源额。</p><div class="grid">'
        for sid in choices:
            old_value = (previous.get('weights') or {}).get(sid,'') if previous.get('mode') != 'amounts' else (currency(previous['targets'][sid]).replace(',','') if sid in previous.get('targets',{}) else '')
            body += field('target:'+sid,f'{sid} · {choices[sid]["talent"]}',old_value)
        body += '</div><div class="grid">' + field('basis','分配依据及承担范围',previous.get('basis',''),required=True) + field('reason','确认/更正原因',required=True) + '</div>'
        parts.append(form('allocate',body,'确认分配') + '</details>')
    parts.append('</section><section id="costs"><div class="section-kicker">任务 3C</div><h2>核对商品成本与实际费用</h2><p class="section-lead">先按当前处理范围批量补齐多个SKU单位成本，再处理达人、履约和月度费用字段。留空表示未取得，0表示实际零元，“不适用”表示业务不发生。</p>')
    cost_rows = scoped_records(conn,q,('talent','fulfillment','monthly'))
    parts.append(m2_ui.render_multi_costs(conn,csrf,q))
    parts.append(m2_ui.render_fees(conn,csrf,q))
    kind = q.get('cost_kind','talent')
    if kind not in ('talent','fulfillment','monthly'):
        kind = 'talent'
    edit = next((r for r in cost_rows if str(r['id']) == q.get('cost_id') and r['kind'] == kind),None)
    parts.append('<form method="get" action="/workbench"><div class="grid">' + m2_ui.context(q) + select('cost_kind','新增费用类型',[(k,SCHEMAS[k][0]) for k in ('talent','fulfillment','monthly')],kind) + '<button class="secondary">选择新增类型</button></div></form>')
    body = field('kind','',kind,kind='hidden') + field('record_id','',edit['id'] if edit else '',kind='hidden') + '<div class="grid">'
    for name,label,typ in SCHEMAS[kind][1]:
        value = edit['data'].get(name,'') if edit else (month if name == 'month' else (next(iter(scope['session_ids'])) if name=='session_id' and len(scope['session_ids'])==1 else ''))
        if typ in ('money?','optional') and type(value) is int:
            value = str(Decimal(value)/100)
        body += field(name,label + ('（元）' if typ in ('money?','optional','unit?') else ''),value,required=not (typ.endswith('?') or typ=='optional'))
    body += field('reason','录入/更正原因',required=True) + '</div>'
    if edit and kind=='fulfillment' and edit['data'].get('_cost_source'):
        body += '<label class="check"><input type="checkbox" name="confirm_unit_cost" value="yes">已按实际依据重新确认当前单位成本（转为人工实际成本，保留原标准来源历史）</label>'
    parts.append('<details><summary>' + ('更正已有费用' if edit else '新增费用') + '</summary>' + form('cost',body) + '</details></section>')
    if close_ready:
        parts.append('<section class="next-bar"><p><strong>本月已满足关账条件。</strong><br><span class="muted">进入最后一步核对利润并生成正式版本。</span></p><a class="button" href="'+step_url(4)+'">进入关账与导出</a></section>')
    else:
        parts.append('<section class="notice issue"><strong>仍有 '+str(len(snapshot["issues"]))+' 项关账条件。</strong> <a href="#closing-checks">查看具体原因和对应入口</a>。</section>')
    parts.append('</div><div class="step-view step-4">')

    parts.append('<section id="close"><div class="section-kicker">步骤 4</div><h2>经营利润预览与整月关账</h2>')
    if not active and not version:
        parts.append('<form method="get" action="/workbench#close"><div class="grid">'+m2_ui.context(q, ('profit_view',))+select('profit_view','查看口径',[('operating','月中经营利润（不含月度费用）'),('full','完整费用核对与关账')],'operating' if operating_view else 'full')+'<button class="secondary">切换查看口径</button></div></form>')
    if not operating_view:
        parts.append('<div class="notice"><strong>以下结果与正式关账范围：本月全部场次</strong><p>月度共享费用与关账统一按整月核对；返回其他步骤仍保留当前处理范围。</p></div>')
    if version:
        parts.append(f'<div class="notice">正在查看历史 V{version}，金额来自该版关账副本。<a href="{step_url(4)}#close">返回当前</a></div>')
    elif operating_view:
        parts.append('<div class="notice"><strong>经营利润预览 · 未关账</strong><p>不含月度仓储、人工、管理、损益税费及其他结算调整；月度费用待补充或待确认，不按实际零元处理。本预览不生成正式版本，也不进入 M3 关账分析。</p><p>仍需取得结算销售额、最终佣金、投流和商品履约成本；不使用支付 GMV 或估计值替代。</p></div>')
        parts.append('<p><strong>本次纳入：' + esc('本月全部场次' if scope['mode']=='month' else '所选场次完整数据（局部结果，非全月利润）') + '</strong> · ' + esc('、'.join(operating_preview['session_ids']) or '尚未确定场次') + '</p><p>导入范围用于确定涉及场次；核算包含这些场次历次导入并已确认的全部收入、直接成本和投流分配，不受订单搜索或分页影响。</p>')
        if operating_preview['unresolved']:
            parts.append('<details open><summary>仍待核对的未归属订单与未分配投流</summary>' + table(['来源记录','对本次预览的影响'], [[esc(r['label']), '可能涉及所选场次，补核前不出金额' if r['blocks_preview'] else '来源范围属于其他场次，未纳入本次结果；仍需另行处理'] for r in operating_preview['unresolved']]) + '</details>')
        if operating_preview['issues']:
            parts.append('<div class="notice issue"><strong>经营利润所需数据尚未齐备</strong><ul>'+''.join('<li>'+esc(issue)+'</li>' for issue in operating_preview['issues'])+'</ul><p>请返回订单或业务与费用确认补齐以上直接数据。</p></div>')
        else:
            parts.append('<div class="metrics">'+''.join(f'<div class="metric">{LABELS[k]}（元）<strong>{currency(operating_preview["total"][k])}</strong></div>' for k in ('net_revenue','operating_profit'))+'</div>')
            parts.append(table(['场次','净经营收入（元）','经营复盘利润（元）'],[[esc(sid),currency(r['net_revenue']),currency(r['operating_profit'])] for sid,r in operating_preview['results'].items()]))
        parts.append('<p><a class="button secondary" href="'+step_url(4,profit_view="full")+'#close">月度费用齐备后，核对完整利润并关账</a></p>')
    elif not active:
        parts.append('<p class="muted">以下为待关账预览，尚非正式报表。</p>')
        parts.append('<p class="notice">请先确认月度费用已全部取得，或实际无该项费用。未录入费用不会自动产生缺失记录，不能据此认定清单完整。</p>')
        if snapshot['issues']:
            parts.append(f'<div class="notice issue"><strong>还不能关账：剩余 {len(snapshot["issues"])} 项。</strong><p>请返回费用确认处理；这里仅展示前20项，避免一次性淹没当前任务。</p><p><a class="button" href="{step_url(3)}">返回费用确认</a> <a href="/issues.csv?month={esc(month)}">下载完整清单</a></p><ul>' + ''.join(f'<li>{esc(i)}</li>' for i in snapshot['issues'][:20]) + '</ul></div>')
    if not operating_view and displayed and displayed['results']:
        parts.append('<div class="metrics">' + ''.join(f'<div class="metric">{LABELS[k]}（元）<strong>{currency(displayed["total"][k])}</strong></div>' for k in ('net_revenue','operating_profit','final_profit')) + '</div>')
        parts.append(table(['场次','净经营收入（元）','经营复盘利润（元）','最终结算利润（元）'], [[esc(sid)] + [currency(r[k]) for k in ('net_revenue','operating_profit','final_profit')] for sid,r in displayed['results'].items()]))
    if not active and not operating_view and not version and not snapshot['issues']:
        parts.append(form('close','<label class="check"><input type="checkbox" name="confirmed" value="yes" required>已核对本月全部场次费用及共享费用清单完整；月度费用仅含直播承担部分，税费为已确认损益口径。</label>','确认整月关账'))
    elif active:
        parts.append(f'<div class="next-bar"><p><strong>正式关账结果已就绪。</strong><br><span class="muted">下载留档，或进入只读经营分析查看重点场次与证据。</span></p><div class="actions"><a class="button" href="/export?month={esc(month)}">下载正式 Excel · V{active["version"]}</a> <a class="button secondary" href="/analysis?month={esc(month)}&version={active["version"]}&mode=current">查看经营分析</a></div></div>')
    parts.append('</section><section id="history"><h2>历史版本与人工修改</h2>')
    versions = history(conn,month)
    parts.append(table(['版本','关账时间（UTC）','查看','分析','导出'], [[f'V{r["version"]}',esc(r['closed_at']),f'<a href="{step_url(4)}&amp;version={r["version"]}#close">查看关账结果</a>',f'<a href="/analysis?month={esc(month)}&version={r["version"]}&mode=history">分析历史 V{r["version"]}</a>',f'<a href="/export?month={esc(month)}&version={r["version"]}">下载历史 Excel</a>'] for r in versions]))
    if active:
        parts.append('<details><summary>重新开账（整个月度批次）</summary><p>重开后本月退出默认正式结果。旧版保留；重新关账生成下一版本。</p>' + form('reopen',field('reason','重开原因',required=True),'整月重开') + '</details>')
    changes = list(conn.execute('SELECT * FROM changes ORDER BY id DESC LIMIT 100'))
    parts.append('<details><summary>最近100条人工修改记录</summary>' + table(['对象','修改前','修改后','原因','时间（UTC）'], [[esc(r['object_type']+' / '+r['object_key']),esc(describe_change(r['old_value'])),esc(describe_change(r['new_value'])),esc(r['reason']),esc(r['created_at'])] for r in changes]) + '</details></section></div><footer>本地标准模板工作台 · 确定性核算 · 人工确认可追溯</footer></main></div><script src="/m2.js" defer></script></body></html>')
    return ''.join(parts)


def render_months(conn):
    """Small operational landing page; analysis amounts remain in M3 only."""
    sessions = session_map(conn)
    session_counts = {}
    for session in sessions.values():
        month = session['start'][:7]
        session_counts[month] = session_counts.get(month, 0) + 1
    month_states = {row['month']: row['status'] for row in conn.execute(
        'SELECT month,status FROM months')}
    versions = {}
    for row in conn.execute('SELECT month,MAX(version) AS version FROM closes GROUP BY month'):
        versions[row['month']] = row['version']
    months = sorted(set(session_counts) | set(month_states) | set(versions), reverse=True)
    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>月份总览 · 直播经营复盘</title><style>',
             analysis_ui.ANALYSIS_CSS,
             '.month-shell{max-width:1180px;margin:auto;padding:28px 24px 70px}.month-head{margin-bottom:20px}.month-head h1{font-size:30px;margin:4px 0 8px}.month-head p{color:var(--muted);margin:0}.month-card{display:grid;grid-template-columns:minmax(130px,.7fr) repeat(3,minmax(120px,1fr)) auto;gap:18px;align-items:center;padding:18px;border-top:1px solid var(--line)}.month-card:first-of-type{border-top:0}.month-card h2{margin:0}.month-card p{margin:2px 0;color:var(--muted);font-size:13px}.state{display:inline-flex;padding:4px 9px;border-radius:999px;background:var(--soft);font-weight:800;font-size:12px}.state.open{background:var(--warn-bg);color:var(--warn)}@media(max-width:780px){.month-card{grid-template-columns:1fr 1fr}.month-card .actions{grid-column:1/-1}}',
             '</style></head><body><a class="skip-link" href="#main">跳到主要内容</a><div class="top"><div class="top-inner"><div class="brand"><strong>直播经营复盘</strong><span>月份总览 · 从结算到分析</span></div><nav class="global-nav" aria-label="全局导航"><a href="/months" aria-current="page">月份总览</a><a href="/workbench">结算工作台</a><a href="/analysis">经营分析</a></nav></div></div><main id="main" class="month-shell"><div class="month-head"><div class="eyebrow">业务期间</div><h1>先选月份，再继续当前任务</h1><p>未关账月份进入结算流程；已关账月份可直接查看经营分析或历史版本。</p></div>']
    if not months:
        parts.append('<section class="empty"><h2>还没有业务月份</h2><p>先进入结算工作台下载模板并导入数据。</p><a class="button" href="/workbench">开始导入</a></section>')
    else:
        parts.append('<section aria-label="月份列表">')
        for month in months:
            state = month_states.get(month, 'open')
            version = versions.get(month)
            closed = state == 'closed' and version
            if closed:
                status = f'已关账 V{version}'
                next_text = '正式结果可分析'
                actions = (f'<a class="button" href="/analysis?month={esc(month)}&version={version}&mode=current">查看经营分析</a>'
                           f'<a class="button secondary" href="/workbench?month={esc(month)}&step=4">关账与导出</a>')
            else:
                status = '已重开' if version else '未关账'
                try:
                    issue_count = len(preview_close(conn,month)['issues'])
                    next_text = f'还有 {issue_count} 项关账条件' if issue_count else '已满足关账条件'
                except (ValueError,KeyError):
                    next_text = '继续核对本月数据'
                actions = f'<a class="button" href="/workbench?month={esc(month)}">继续结算</a>'
                if version:
                    actions += f'<a class="button secondary" href="/analysis?month={esc(month)}&version={version}&mode=history">查看历史 V{version}</a>'
            parts.append(f'<article class="month-card"><div><h2>{esc(month)}</h2><p>开播业务月</p></div><div><span class="state {"" if closed else "open"}">{esc(status)}</span></div><div><strong>{session_counts.get(month,0)} 场</strong><p>已导入直播场次</p></div><div><strong>{esc(next_text)}</strong><p>建议下一步</p></div><div class="actions">{actions}</div></article>')
        parts.append('</section>')
    parts.append('</main><div class="footer">本地单用户工作台 · 关账结果只读分析</div></body></html>')
    return ''.join(parts)


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, address, db_path):
        if address[0] not in ('127.0.0.1', 'localhost'):
            raise ValueError('工作台仅允许监听本机127.0.0.1')
        self.db_path = db_path
        # Construction fixes the database path but deliberately performs no I/O.
        # Every M3 request opens its own SQLite mode=ro transaction.
        self.analysis = AnalysisService(db_path)
        self.csrf = secrets.token_urlsafe(32)
        self.uploads = {}
        self.previews = {}
        self._request_state = threading.Condition()
        self._active_requests = 0
        self._accepting_requests = True
        super().__init__(address, Handler)

    @property
    def active_requests(self):
        with self._request_state:
            return self._active_requests

    def process_request(self, request, client_address):
        with self._request_state:
            if not self._accepting_requests:
                self.shutdown_request(request)
                return
            self._active_requests += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._request_state:
                self._active_requests -= 1
                self._request_state.notify_all()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._request_state:
                self._active_requests -= 1
                self._request_state.notify_all()

    def begin_shutdown_if_idle(self):
        with self._request_state:
            if self._active_requests:
                return False
            self._accepting_requests = False
            return True

    def stop_accepting(self):
        with self._request_state:
            self._accepting_requests = False

    def wait_until_idle(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._request_state:
            while self._active_requests:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._request_state.wait(remaining)
            return True


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        # Browsers may preconnect without sending a request. Such connections
        # must neither monopolize the server nor keep a worker indefinitely.
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        pass

    def respond(self, body, status=200, content_type='text/html; charset=utf-8', filename=None):
        raw = body.encode('utf-8') if isinstance(body,str) else body
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; form-action 'self'; frame-ancestors 'none'")
        if filename:
            self.send_header('Content-Disposition',"attachment; filename*=UTF-8''" + quote(filename))
        self.end_headers()
        self.wfile.write(raw)

    def respond_json(self, payload, status=200):
        self.respond(json.dumps(payload,ensure_ascii=False,separators=(',',':')),status,
                     'application/json; charset=utf-8')

    def valid_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')

    def do_GET(self):
        if not self.valid_host():
            self.respond('仅允许本机访问',403); return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query,keep_blank_values=True)
        q = {k:v[-1] for k,v in query.items()}
        # Analysis routes must be dispatched before the write-capable workbench
        # connection. This preserves the read-only boundary even for old/empty DBs.
        if parsed.path == '/analysis.js':
            self.respond(analysis_ui.ANALYSIS_JS,content_type='text/javascript; charset=utf-8')
            return
        if parsed.path == '/analysis':
            self.respond(analysis_ui.render_analysis_page(self.server.analysis,query))
            return
        if parsed.path in ANALYSIS_ROUTES:
            status, result = dispatch_analysis(self.server.analysis,'GET',parsed.path,q)
            self.respond_json(result,status)
            return
        conn = connect(self.server.db_path)
        try:
            if parsed.path == '/m2.js':
                self.respond("""
document.addEventListener('change',e=>{
  if(e.target.matches('[data-select-page]')){
    const name=e.target.dataset.selectPage;
    e.target.closest('form').querySelectorAll('input[type=checkbox][name="'+name+'"]').forEach(x=>{x.checked=e.target.checked;});
  }
});
document.addEventListener('submit',e=>{
  const button=e.submitter;
  if(button){
    button.setAttribute('aria-busy','true');
    button.dataset.originalText=button.textContent;
    button.textContent='正在处理…';
  }
});
document.addEventListener('DOMContentLoaded',()=>{
  const alert=document.querySelector('[role="alert"]');
  if(alert) alert.focus();
  revealHashTarget();
});
function revealHashTarget(){
  if(!location.hash) return;
  const target=document.getElementById(decodeURIComponent(location.hash.slice(1)));
  for(let parent=target?.parentElement;parent;parent=parent.parentElement){
    if(parent.tagName==='DETAILS') parent.open=true;
  }
  if(target && target.tagName==='DETAILS'){
    target.open=true;
    const summary=target.querySelector('summary');
    if(summary) summary.focus({preventScroll:true});
  }
}
window.addEventListener('hashchange',revealHashTarget);
""",content_type='text/javascript; charset=utf-8')
            elif parsed.path == '/m2-preview.csv':
                preview = self.server.previews.get(q.get('token'))
                if preview is None:
                    raise ValueError('预览已过期，请重新生成')
                self.respond(m2_ui.preview_csv(preview),content_type='text/csv; charset=utf-8',filename='批量处理核对明细.csv')
            elif parsed.path == '/m2-history':
                self.respond(m2_ui.render_history(conn,int(q.get('record_id',''))))
            elif parsed.path == '/issues.csv':
                issues = preview_close(conn,q.get('month',''))['issues']
                self.respond(m2_ui.csv_bytes([['待处理事项']]+[[x] for x in issues]),content_type='text/csv; charset=utf-8',filename='关账待处理事项.csv')
            elif parsed.path == '/template':
                self.respond(make_template(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename='经营复盘标准模板.xlsx')
            elif parsed.path == '/export':
                filename, raw = export_management(conn,q.get('month',''),version=int(q['version']) if q.get('version') else None)
                self.respond(raw,content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename=filename)
            elif parsed.path == '/months' or (parsed.path == '/' and not parsed.query):
                self.respond(render_months(conn))
            elif parsed.path in ('/','/workbench'):
                self.respond(render_page(conn,self.server.csrf,q))
            else:
                self.respond('页面不存在',404)
        except (ValueError,KeyError) as exc:
            if parsed.path in ('/', '/workbench'):
                # A malformed filter must never strand the desktop window on a
                # raw error page. Keep only known-safe navigation context and
                # return the user to an operable workbench.
                fallback = {'month': date.today().strftime('%Y-%m')}
                try:
                    candidate_month = q.get('month', '')
                    if len(candidate_month) == 7:
                        date.fromisoformat(candidate_month + '-01')
                        fallback['month'] = candidate_month
                except ValueError:
                    pass
                if q.get('step') in ('1', '2', '3', '4'):
                    fallback['step'] = q['step']
                fallback.update({k:q[k] for k in ('work_scope','batch_id','batch_action','scope_session_id','profit_view') if k in q})
                try:
                    resolve_scope(conn,fallback)
                except (ValueError, TypeError):
                    recovery = m2_ui.render_scope(conn, {'month':fallback['month'], 'step':fallback.get('step','2')})
                    self.respond('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>'+CSS+'</style><main><h1>请重新选择处理范围</h1><p role="alert">'+esc(exc)+'</p><p>当前未展示业务数据，也未自动切换到整月。请明确选择下方范围。</p>'+recovery+'</main></html>',400)
                else:
                    self.respond(render_page(
                        conn, self.server.csrf, fallback,
                        '操作未完成：筛选条件无效，已清除本次筛选，保留处理范围。' + str(exc)), 400)
            else:
                self.respond(esc(exc),400)
        finally:
            conn.close()

    def do_POST(self):
        if not self.valid_host():
            self.respond('仅允许本机访问',403); return
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):
            self.respond('请求来源无效',403); return
        action = urlparse(self.path).path
        if action in ANALYSIS_ROUTES:
            try:
                length = int(self.headers.get('Content-Length','0'))
                if length <= 0 or length > 1024 * 1024:
                    raise AnalysisError('INVALID_SCOPE','分析请求为空或超过1MB')
                if not self.headers.get('Content-Type','').lower().startswith('application/json'):
                    raise AnalysisError('INVALID_SCOPE','分析接口只接受 application/json')
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
                status, result = dispatch_analysis(self.server.analysis,'POST',action,payload)
            except (AnalysisError,json.JSONDecodeError,UnicodeError,ValueError) as exc:
                error = exc if isinstance(exc,AnalysisError) else AnalysisError('INVALID_SCOPE','请求JSON无法解析')
                result = self.server.analysis._call(lambda: (_ for _ in ()).throw(error))
                status = 400
            self.respond_json(result,status)
            return
        if action == '/analysis/scenario':
            try:
                length = int(self.headers.get('Content-Length','0'))
                if length <= 0 or length > 64 * 1024:
                    raise ValueError('测算请求为空或超过64KB')
                if not self.headers.get('Content-Type','').lower().startswith('application/x-www-form-urlencoded'):
                    raise ValueError('测算表单格式无效')
                values = parse_qs(self.rfile.read(length).decode(),keep_blank_values=True)
                data = {k:v[-1] for k,v in values.items()}
                close_ref = {'month':data['month'],'version':int(data['version'])}
                if data.get('assumption_type') == 'sku_unit_cost':
                    assumption = {'type':'sku_unit_cost','session_id':data['session_id'],
                                  'sku':data.get('sku',''),'operation':'set',
                                  'unit_cost':data.get('unit_cost','')}
                else:
                    assumption = {'type':'session_fee','session_id':data['session_id'],
                                  'field':data.get('fee_field',''),
                                  'operation':data.get('operation','delta'),
                                  'amount_cents':money_to_cents(data.get('amount_yuan',''))}
                result = self.server.analysis.simulate_scenario(
                    close_ref,[data['session_id']],[assumption],mode=data.get('mode','current'),
                    assumption_source='user_provided')
                query = {key:[data[key]] for key in ('month','version','mode') if data.get(key)}
                query['session_id'] = [data['session_id']]
                self.respond(analysis_ui.render_analysis_page(self.server.analysis,query,result),
                             200 if result['status']=='ok' else 400)
            except (KeyError,TypeError,ValueError,UnicodeError) as exc:
                result = self.server.analysis._call(lambda: (_ for _ in ()).throw(
                    AnalysisError('INVALID_ASSUMPTION','测算参数无效：'+str(exc))))
                fallback = parse_qs(urlparse(self.headers.get('Referer','')).query,
                                    keep_blank_values=True)
                self.respond(analysis_ui.render_analysis_page(self.server.analysis,fallback,result),400)
            return
        conn = connect(self.server.db_path)
        q = {}
        try:
            length = int(self.headers.get('Content-Length','0'))
            if length <= 0 or length > 20 * 1024 * 1024:
                raise ValueError('请求为空或超过20MB')
            body = self.rfile.read(length)
            files = {}
            if self.headers.get('Content-Type','').startswith('multipart/form-data'):
                message = BytesParser(policy=policy.default).parsebytes(('Content-Type: '+self.headers['Content-Type']+'\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
                values = {}
                for part in message.iter_parts():
                    name = part.get_param('name',header='content-disposition')
                    if part.get_filename():
                        files[name] = (part.get_filename(),part.get_payload(decode=True))
                    else:
                        values.setdefault(name,[]).append(part.get_content())
            else:
                values = parse_qs(body.decode(),keep_blank_values=True)
            data = {k:v[-1] for k,v in values.items()}
            if not secrets.compare_digest(data.get('csrf',''),self.server.csrf):
                self.respond('页面已过期，请刷新后重试',403); return
            q = {k:data[k] for k in FILTER_KEYS if k in data}
            q.setdefault('month',date.today().strftime('%Y-%m'))
            work_scope = resolve_scope(conn,q)
            if action.startswith('/m2-'):
                prepared = None
                allow = data.get('allow_overwrite')=='yes'
                if action in ('/m2-orders','/m2-ads'):
                    rows = order_queue(conn,q)[0] if action=='/m2-orders' else scoped_records(conn,q,('ads',))
                    available = {r['id'] for r in rows}
                    ids = [r['id'] for r in rows] if data.get('selection')=='filtered' else [int(v) for v in values.get('record_id',[])]
                    if any(rid not in available for rid in ids):
                        raise ValueError('筛选范围已变化，请刷新后重新选择')
                    if data.get('action')=='generate':
                        if action == '/m2-orders' and data.get('source_scope_confirmed') != 'yes':
                            raise ValueError('生成订单候选前，请展开场次范围并勾选业务范围确认')
                        generated = generate(conn,ids,session_ids=values.get('scope_session',[]),month=q['month'],source_scope_confirmed=data.get('source_scope_confirmed')=='yes')
                        exceptions = sum(bool(r['data']['issues']) for r in generated)
                        notice = f'已保存 {len(generated)} 条候选记录，其中 {exceptions} 条存在规则异常。全部候选均需人工确认，最终归属和费用未改变。'
                    else:
                        prepared = prepare_assignments(conn,ids,session_id=data.get('session_id'),use_candidates=action=='/m2-ads' or data.get('assign_mode')=='candidates',allow_overwrite=allow)
                elif action=='/m2-preview':
                    op = data.get('operation')
                    if op=='flags':
                        if not set(values.get('session_id',[])) <= resolve_scope(conn,q)['session_ids']:
                            raise ValueError('所选场次超出当前处理范围，请刷新后重新选择')
                        if any(data.get(k) not in ('true','false') for k in FLAGS):
                            raise ValueError('请明确选择四项业务范围')
                        prepared = prepare_flags(conn,values.get('session_id',[]),{k:data[k]=='true' for k in FLAGS})
                    elif op=='unit_cost':
                        if work_scope['mode']!='month':
                            raise ValueError('请使用当前范围的多 SKU 成本表；月度批量标准不能扩大本次范围')
                        prepared = prepare_cost_fill(conn,int(data['standard_id']))
                    elif op=='multi_unit_cost':
                        available_orders = {r['id'] for r in order_queue(conn,q | {'order_status':'all'})[0]}
                        if not {int(v) for v in values.get('order_id',[])} <= available_orders:
                            raise ValueError('所选订单超出当前处理范围，请刷新后重新选择')
                        selected_skus = values.get('sku',[])
                        sku_values = {sku:data.get('unit_cost:'+sku,'') for sku in selected_skus}
                        sku_basis = {sku:data.get('basis:'+sku,'') for sku in selected_skus}
                        if any(not str(value).strip() for value in sku_values.values()):
                            raise ValueError('所选SKU必须分别填写本次单位成本')
                        prepared = prepare_multi_cost_fill(
                            conn, [int(v) for v in values.get('order_id',[])], sku_values,
                            month=q['month'],
                            save_as_standard=data.get('save_as_standard')=='yes',
                            basis=data.get('basis',''), sku_basis=sku_basis)
                    elif op=='reuse':
                        prepared = prepare_allocation_reuse(conn,int(data['from_id']),[int(v) for v in values.get('record_id',[])])
                    elif op=='cost_edit':
                        available_costs = {r['id'] for r in scoped_records(conn,q,('fulfillment','talent','monthly'))}
                        if not {int(v) for v in values.get('record_id',[])} <= available_costs:
                            raise ValueError('所选费用超出当前处理范围，请刷新后重新选择')
                        prepared = prepare_cost_edit(conn,[int(v) for v in values.get('record_id',[])],data['cost_field'],data['value'],allow_overwrite=allow)
                    else:
                        raise ValueError('未知批量处理类型')
                elif action=='/m2-apply':
                    prepared_data = self.server.previews.get(data.get('preview_token'))
                    if prepared_data is None:
                        raise ValueError('预览已过期或已处理，请重新生成')
                    result = apply_batch(conn,prepared_data,reason=data.get('reason',''),allow_overwrite=allow)
                    self.server.previews.pop(data['preview_token'],None)
                    notice = f'已确认处理 {result["count"]} 条，每条修改和依据均已保留。'
                elif action=='/m2-standard':
                    save_standard(conn,data['sku'],q['month'],data['unit_cost'],reason=data.get('reason',''))
                    notice = '本月单位成本标准已保存；尚未改动订单成本，请预览后确认补齐。'
                elif action=='/m2-ad-link':
                    if data.get('ad_pair'):
                        pair = json.loads(data['ad_pair'])
                        if not isinstance(pair,list) or len(pair) != 2:
                            raise ValueError('请选择已导入的账号和计划')
                        account, plan = pair
                    else:
                        account, plan = data['account'], data['plan']
                    save_ad_link(conn,q['month'],account,plan,values.get('session_id',[]),reason=data.get('reason',''))
                    notice = '本月账号及计划的受益范围已保存；最终分配未改变，请生成候选并审核。'
                else:
                    raise ValueError('未知操作')
                if prepared is not None:
                    token = secrets.token_urlsafe(24)
                    if len(self.server.previews)>=10:
                        self.server.previews.pop(next(iter(self.server.previews)))
                    self.server.previews[token] = prepared
                    self.respond(render_page(conn,self.server.csrf,q,batch_preview=(token,prepared)))
                else:
                    self.respond(render_page(conn,self.server.csrf,q,notice))
                return
            if action == '/reset-all':
                if data.get('confirmation') != '清空全部数据':
                    raise ValueError('请完整输入“清空全部数据”')
                clear_all_data(conn)
                self.server.uploads.clear()
                self.server.previews.clear()
                self.respond(render_page(conn,self.server.csrf,{'month':q['month']},'已清空全部业务数据，可以导入新的测试文件。'))
                return
            if action == '/preview':
                filename, raw = files['file']
                import_mode = data.get('import_mode','supplement')
                result = preview_workbook(
                    conn, raw, import_mode=import_mode,
                    selected_session_ids=values.get('selected_session_id',[]))
                if result['issues']:
                    # Retain rejected preview issues with no business rows booked.
                    import_workbook(
                        conn,raw,filename,import_mode=import_mode,
                        selected_session_ids=values.get('selected_session_id',[]))
                token = secrets.token_urlsafe(24)
                if len(self.server.uploads) >= 5:
                    self.server.uploads.pop(next(iter(self.server.uploads)))
                self.server.uploads[token] = (filename,raw)
                self.respond(render_page(conn,self.server.csrf,q,import_preview=(token,filename,result))); return
            if action == '/import':
                filename, raw = self.server.uploads[data['upload_token']]
                result = import_workbook(
                    conn,raw,filename,
                    confirm_updates=data.get('confirm_updates')=='yes',
                    reason=data.get('reason',''),
                    import_mode=data.get('import_mode','supplement'),
                    selected_session_ids=values.get('selected_session_id',[]),
                    confirm_new_session=data.get('confirm_new_session')=='yes',
                    duplicate_basis=data.get('duplicate_basis',''))
                notice = (f'入账 {result["accepted"]} 行。' if result['accepted'] else '没有新增数据。') + '；'.join(result['issues'])
                if not result['issues']:
                    self.server.uploads.pop(data['upload_token'],None)
                    q.update(step='2',work_scope='batch',batch_id=str(result['batch_id']),
                             batch_action='changed',order_status='pending',page='1')
            elif action == '/assign-orders':
                if not {int(v) for v in values.get('record_id',[])} <= {r['id'] for r in order_queue(conn,q | {'order_status':'all'})[0]}:
                    raise ValueError('所选订单超出当前处理范围')
                assign_orders(conn,[int(v) for v in values.get('record_id',[])],data['session_id'],basis=data['basis'],reason=data['reason'])
                notice = '订单归属已保存，原决定及修改原因已保留。'
            elif action == '/allocate':
                if int(data['record_id']) not in {r['id'] for r in scoped_records(conn,q,('ads','monthly'))}:
                    raise ValueError('所选费用超出当前处理范围')
                mode = data['mode']
                targets = {k[7:]: (money_to_cents(v) if mode=='amounts' else int(v)) for k,v in data.items() if k.startswith('target:') and v.strip()}
                assign(conn,int(data['record_id']),targets,mode=mode,basis=data['basis'],reason=data['reason'])
                notice = '分配已保存，分配合计与来源金额一致。'
            elif action == '/flags':
                if data['session_id'] not in work_scope['session_ids']:
                    raise ValueError('所选场次超出当前处理范围')
                if any(data.get(k) not in ('true','false') for k in FLAGS):
                    raise ValueError('请明确选择四项业务是否适用')
                set_applicability(conn,data['session_id'],{k:data[k]=='true' for k in FLAGS},reason=data['reason'])
                notice = '场次业务范围已保存。'
            elif action == '/cost':
                if data.get('record_id') and int(data['record_id']) not in {r['id'] for r in scoped_records(conn,q,('talent','fulfillment','monthly'))}:
                    raise ValueError('所选费用超出当前处理范围')
                if data['kind']=='monthly' and data.get('month')!=q['month']:
                    raise ValueError('月度共享费用应属于当前业务月份')
                if work_scope['mode']!='month':
                    if data['kind']=='talent' and data.get('session_id') not in work_scope['session_ids']:
                        raise ValueError('费用所属场次超出当前处理范围')
                    if data['kind']=='fulfillment' and (data.get('order_id'),data.get('line_id')) not in {
                        (r['data']['order_id'],r['data']['line_id']) for r in order_queue(conn,q | {'order_status':'all'})[0]
                    }:
                        raise ValueError('费用所属订单超出当前处理范围')
                save_cost(conn,data['kind'],data,reason=data['reason'],record_id=int(data['record_id']) if data.get('record_id') else None,confirm_unit_cost=data.get('confirm_unit_cost')=='yes')
                notice = '费用已保存；月度费用金额变化后请重新确认分配。'
            elif action == '/close':
                snapshot = close_month(conn,q['month'],confirmed=data.get('confirmed')=='yes')
                notice = f'整月关账完成，版本 V{snapshot["version"]}，可下载正式 Excel。'
            elif action == '/reopen':
                reopen_month(conn,q['month'],reason=data['reason'])
                notice = '整月已重开，旧版仍可查；重新关账前本月退出默认正式结果。'
            else:
                raise ValueError('未知操作')
            self.respond(render_page(conn,self.server.csrf,q,notice))
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            try:
                self.respond(render_page(conn,self.server.csrf,q,'操作未完成：'+str(exc)),400)
            except ValueError:
                self.respond(esc(exc),400)
        except sqlite3.Error:
            self.respond('数据库操作失败，本次事务已回滚。请检查磁盘空间及数据库是否可写后重试。',500)
        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description='本地直播经营复盘工作台')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--db',type=Path,default=DB_PATH)
    args = parser.parse_args()
    from src.workbench.runtime import start_workbench

    runtime = start_workbench(args.db, port=args.port, create_if_missing=True)
    print(f'工作台已启动：{runtime.base_url} （Ctrl+C停止）',flush=True)
    try:
        while runtime.thread.is_alive():
            runtime.thread.join(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()


if __name__ == '__main__':
    main()
