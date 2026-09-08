"""Small server-rendered BP workflows; business mutations stay in domain functions."""
from collections import Counter
import csv
import io
import json
from urllib.parse import urlencode

from src.workbench.presentation import esc, currency, field, select, table, business_name
from src.workbench.workflow import order_queue, ORDER_STATES, FILTER_KEYS, fee_states
from src.workbench.db import records, session_map
from src.workbench.scope import resolve_scope, scoped_records
from src.workbench.costs import FLAGS, settings, standards
from src.workbench.attribution import assignments, needs_review
from src.workbench.recommendations import ad_links
from src.workbench.imports import SCHEMAS, recent_batches

PAGE_SIZE = 50


def context(q, exclude=()):
    return ''.join(field(k,'',q[k],kind='hidden') for k in FILTER_KEYS if k in q and k not in exclude)


def form(csrf,q,action,body,button='预览本次处理',anchor=None):
    anchor = anchor if anchor is not None else {
        'm2-preview':'batch-preview', 'm2-standard':'scope',
        'm2-ad-link':'ad-scope'}.get(action,'')
    target = f'/{action}' + (f'#{anchor}' if anchor else '')
    return f'<form method="post" action="{target}">' + field('csrf','',csrf,kind='hidden') + context(q) + body + f'<p><button>{esc(button)}</button></p></form>'


def selection():
    return select('selection','本次处理范围',[('selected','仅处理本页勾选记录'),('filtered','处理全部筛选结果（跨页）')])


def session_checks(sessions, name='scope_session', checked=False):
    return ''.join(f'<label class="check"><input type="checkbox" name="{name}" value="{esc(s)}" {"checked" if checked else ""}>{esc(s)} · {esc(d["talent"])} · {esc(d["start"])}</label>' for s,d in sessions.items())



def render_scope(conn, q):
    scope = {s:d for s,d in session_map(conn).items() if d['start'][:7]==q['month']}
    batches = [b for b in recent_batches(conn) if b['status']=='accepted']
    output = '<div class="notice scope-ribbon"><strong>当前处理范围：' + esc(resolve_scope(conn,q)['label']) + '</strong><p>切换步骤保留此范围。共享费用与正式关账按整月处理，并单独标明。</p>'
    output += '<form method="get" action="/workbench"><div class="grid">' + field('month','',q['month'],kind='hidden') + field('step','',q.get('step','2'),kind='hidden')
    output += select('work_scope','查看范围',[('batch','本次导入'),('session','所选场次全部'),('month','本月全部')],q.get('work_scope','month'))
    output += select('batch_id','本次导入文件',[(b['id'],f'批次 {b["id"]} · {b["filename"]}') for b in batches],q.get('batch_id',''))
    output += select('batch_action','批次成员', [('changed','本次新增与更新'),('unchanged','未变旧记录'),('all','全部批次成员')],q.get('batch_action','changed'))
    output += select('scope_session_id','所选场次',[(s,f'{s} · {d["talent"]} · {d["start"]}') for s,d in scope.items()],q.get('scope_session_id',''))
    output += '<button class="secondary">切换范围</button></div></form>'
    return output + '</div>'


def render_orders(conn,csrf,q,notice=''):
    rows, counts = order_queue(conn,q)
    sessions = session_map(conn)
    scope = {s:d for s,d in sessions.items() if d['start'][:7]==q['month']}
    page = max(1,min(int(q.get('page',1)),max(1,(len(rows)+PAGE_SIZE-1)//PAGE_SIZE)))
    shown = rows[(page-1)*PAGE_SIZE:page*PAGE_SIZE]
    batches = [b for b in recent_batches(conn) if b['status'] == 'accepted']
    output = '<section id="attribution"><div class="section-kicker">步骤 2</div><h2>确认订单属于哪场</h2><p class="section-lead">决定订单销售额及对应商品履约成本计入哪场直播；确认前可以核对和修改。场次选择只是处理范围，不会把文件内全部订单自动改绑。</p>'
    if q.get('work_scope') == 'batch' and q.get('batch_id'):
        selected = next((b for b in batches if str(b['id']) == str(q['batch_id'])), None)
        if selected:
            output += f'<div class="notice"><strong>本次处理：{esc(selected["filename"])}</strong><p>新增 {selected["new_count"] or 0} · 更新 {selected["update_count"] or 0} · 未变 {selected["unchanged_count"] or 0} · 需处理 {selected["conflict_count"] or 0}。批次成员来自导入时保存的清单，不依赖记录当前 batch_id 反推。</p></div>'
    output += '<div class="task-grid"><div class="task-card">未生成候选<strong>'+str(counts['unassigned'])+'</strong><span class="muted">笔</span></div><div class="task-card">有候选待确认<strong>'+str(counts['candidate'])+'</strong><span class="muted">笔</span></div><div class="task-card">异常需判断<strong>'+str(counts['exception']+counts['review'])+'</strong><span class="muted">笔</span></div></div>'
    if notice:
        output += f'<div class="notice">{esc(notice)}</div>'
    output += '<form method="get" action="/workbench"><div class="grid">' + context(q, ('order_status','search','date_prefix','talent','time_from','time_to','page')) + select('order_status','处理状态',[(s,f'{label}（{counts[s]}）') for s,label in ORDER_STATES.items()],q.get('order_status','pending'))
    output += field('search','订单号或SKU',q.get('search','')) + field('date_prefix','支付日期',q.get('date_prefix',''),kind='date') + field('talent','已确认归属的达人',q.get('talent','')) + '</div><div class="grid">' + field('time_from','支付时段从（如 20:30；也可填完整日期时间）',q.get('time_from','')) + field('time_to','支付时段至（如 21:30；包含该分钟）',q.get('time_to','')) + '<button class="secondary">筛选</button></div></form>'
    output += f'<p>本次筛选共 {len(rows)} 条；当前范围第 {page} 页，每页最多 {PAGE_SIZE} 条。已确认记录可单独查看，不会进入待办全选。</p>'
    if not rows and q.get('order_status','pending') == 'pending':
        output += '<div class="empty-inline"><strong>本次已完成。</strong> 当前范围没有待确认订单；可切换到“已确认”核对结果。</div>'
    body = selection()
    body += '<p><label class="check"><input type="checkbox" data-select-page="record_id">选择/取消本页全部订单</label></p>'
    cells = []
    for row in shown:
        d,r = row['data'],row['recommendation']
        candidate_text = ', '.join(r['data']['candidates']) if r else '尚未生成'
        explanation = '；'.join(r['data']['issues'] or r['data']['hits']) if r else '可生成候选或人工指定场次'
        detail = f'<details><summary>处理依据</summary><p>{esc(explanation)}</p><a href="/m2-history?record_id={row["id"]}">查看全部候选与人工决定</a></details>'
        cells.append([f'<input type="checkbox" name="record_id" value="{row["id"]}" aria-label="选择订单 {esc(business_name(row))}">',esc(d['order_id']),esc(d['line_id']),esc(d['sku']),esc(d['quantity']),esc(d['paid_at']),esc(candidate_text),esc(', '.join((row['decision'] or {}).get('targets',{})) or '未确认'),esc(ORDER_STATES[row['state']])+detail])
    body += table(['选择','订单号','子订单/行号','SKU','数量','支付时间','候选场次','最终场次','状态与依据'],cells)
    body += '<details><summary>生成候选的场次范围</summary>' + session_checks(scope,checked=True) + '<label class="check"><input name="source_scope_confirmed" type="checkbox" value="yes">已核对所选订单和场次的业务范围，接受时间候选仍需人工审核（生成候选时必选）</label></details>'
    body += '<div class="grid">' + select('assign_mode','确认方式',[('manual','本文件订单属于同一场'),('candidates','多场文件：采用各订单明确候选')]) + select('session_id','本次确认到场次',[(s,f'{s} · {d["talent"]} · {d["start"]}') for s,d in sessions.items()]) + '</div>'
    body += '<label class="check"><input name="allow_overwrite" type="checkbox" value="yes">本次明确需要更正已确认记录（原决定和原因保留）</label>'
    output += '<form method="post" action="/m2-orders">' + field('csrf','',csrf,kind='hidden') + context(q) + body + '<p><button name="action" value="preview" formaction="/m2-orders#batch-preview">预览并确认到本场</button> <button name="action" value="generate" formaction="/m2-orders#attribution" class="secondary">多场文件：按直播时间匹配</button></p></form>'
    output += '<p>' + ' · '.join(f'<a href="/workbench?{esc(urlencode(q|{"page":str(p)}))}#attribution">第 {p} 页</a>' for p in sorted({1,max(1,page-1),page,min(max(1,(len(rows)+49)//50),page+1),max(1,(len(rows)+49)//50)})) + '</p></section>'
    return output


def render_reuse(conn,csrf,q):
    month_sessions = {s:d for s,d in session_map(conn).items() if d['start'][:7]==q['month']}
    scope = resolve_scope(conn,q)
    sessions = {s:d for s,d in month_sessions.items() if s in scope['session_ids']}
    confirmed_flags = settings(conn)
    imported_pairs = sorted({(r['data']['account'],r['data']['plan']) for r in records(conn,'ads')
                             if r['data']['start'][:7] == q['month']})
    links = ad_links(conn)
    flags_complete = bool(sessions) and all(sid in confirmed_flags for sid in sessions)
    links_complete = all((q['month'],account,plan) in links for account,plan in imported_pairs)
    output = '<section id="scope"><div class="section-kicker">任务 3A</div><h2>确认业务与投流范围</h2><p class="section-lead">先明确每场业务是否发生，再确认已导入账号/计划服务哪些场次。完成后系统才能生成可靠的费用分配建议。</p>'
    output += f'<details {"open" if not flags_complete else ""}><summary>场次业务范围 · {"已完成" if flags_complete else "待确认"}</summary>'
    flag_rows = []
    for sid, session in sessions.items():
        current = confirmed_flags.get(sid)
        flag_rows.append([
            esc(sid), esc(session['talent']),
            *[esc('未确认' if current is None else ('适用' if current[key] else '不适用'))
              for key in FLAGS],
        ])
    output += '<h3>当前确认结果</h3>'
    output += table(['场次', '达人/自播标识', *FLAGS.values()], flag_rows)
    body = field('operation','','flags',kind='hidden') + session_checks(sessions,'session_id') + '<div class="grid">'
    body += ''.join(select(k,label,[('','请选择'),('true','适用'),('false','不适用')]) for k,label in FLAGS.items()) + '</div>'
    output += form(csrf,q,'m2-preview',body) + '</details>'
    output += f'<details id="ad-scope" {"open" if not links_complete else ""}><summary>投流账号与计划的受益场次（整月共享配置） · {"已完成" if links_complete else "待确认"}</summary><p>直接选择导入数据中的账号/计划组合，无需重新输入。系统只在完整且不重叠覆盖时给出时间权重建议。</p>'
    pair_choices = [(json.dumps([account,plan],ensure_ascii=False),f'{account} / {plan}')
                    for account,plan in imported_pairs]
    body = '<div class="grid">'+select('ad_pair','选择已导入的账号 / 计划',pair_choices)+field('reason','关联及承担范围依据',required=True)+'</div>'+session_checks(month_sessions,'session_id')
    output += form(csrf,q,'m2-ad-link',body,'保存已确认关联范围')
    output += table(['账号','计划','当前受益场次','状态与依据'],[
        [esc(account),esc(plan),
         esc(', '.join(links[(q['month'],account,plan)]['data']['session_ids']))
             if (q['month'],account,plan) in links else '<span class="status pending">未确认</span>',
         esc(links[(q['month'],account,plan)]['data']['basis'])
             if (q['month'],account,plan) in links else '请选择场次并保存']
        for account,plan in imported_pairs])
    output += '</details></section>'
    return output


def render_monthly_reuse(conn, csrf, q):
    monthly = [r for r in records(conn,'monthly') if r['data']['month'] == q['month']]
    decisions, sessions = assignments(conn), session_map(conn)
    valid = [r for r in monthly if decisions.get(r['id'],{}).get('mode') == 'weights'
             and not needs_review(r, decisions[r['id']], sessions)]
    if not monthly:
        return '<div class="empty-inline"><strong>月度费用可后补。</strong> 当前未录入月度费用，因此不显示空的参考方案。</div>'
    if not valid:
        return '<div class="empty-inline">先完成一笔费用的权重分摊，再用于其他费用。</div>'
    if not any(r['id'] != valid[0]['id'] for r in monthly):
        return '<div class="empty-inline">已有有效权重方案；当前没有其他月度费用需要沿用。</div>'
    body = field('operation','','reuse',kind='hidden')
    body += select('from_id','参考费用',[(r['id'],r['business_key']+' · '+r['data']['category']) for r in valid])
    body += ''.join(f'<label class="check"><input type="checkbox" name="record_id" value="{r["id"]}" {"" if r["id"] in decisions else "checked"}>{esc(r["business_key"])} · {esc(r["data"]["category"])}（仅复用受益范围和权重）</label>' for r in monthly if r['id'] != valid[0]['id'])
    reference_rows = [[esc(r['business_key']),esc(r['data']['category']),
                       esc('、'.join(f'{sid}：{weight}' for sid,weight in decisions[r['id']]['weights'].items())),
                       esc(decisions[r['id']]['basis'])] for r in valid]
    return '<details><summary>沿用已有分摊方式</summary><p>目标费用仍按各自源额计算并单独处理尾差。已有分配默认不选中，承担范围须由BP确认。</p>' + table(['参考费用','类别','受益场次与权重','已确认依据'],reference_rows) + form(csrf,q,'m2-preview',body) + '</details>'


def render_multi_costs(conn, csrf, q):
    all_rows = records(conn)
    orders = {r['id']: r for r in all_rows if r['kind'] == 'orders'}
    scoped, _ = order_queue(conn, q | {'order_status':'all'})
    fulfillment = {r['business_key']:r for r in all_rows if r['kind']=='fulfillment'}
    catalog = {(s['sku'],s['month']):s for s in standards(conn).values()}
    grouped = {}
    for order in scoped:
        sku = order['data']['sku']
        group = grouped.setdefault(sku, {'orders':[], 'blank':0, 'missing':0, 'actual':set()})
        group['orders'].append(order)
        cost = fulfillment.get(order['business_key'])
        if not cost:
            group['missing'] += 1
        elif cost['data']['unit_cost'] is None:
            group['blank'] += 1
        else:
            group['actual'].add(str(cost['data']['unit_cost']))
    if not grouped:
        return '<div class="empty-inline">当前处理范围没有订单；请先选择本次导入、场次或本月范围。</div>'
    rows = []
    for sku, group in sorted(grouped.items()):
        standard = catalog.get((sku,q['month']))
        actual = '、'.join(sorted(group['actual'])) if group['actual'] else '暂无实际单价'
        if len(group['actual']) > 1:
            actual += '（多价，保留逐单记录）'
        rows.append([
            f'<input type="checkbox" name="sku" value="{esc(sku)}" {"checked" if group["blank"] else ""}>',
            esc(sku), esc(actual),
            esc((standard['data']['unit_cost']+' 元；'+standard['data']['basis']) if standard else '待维护'),
            f'<input name="unit_cost:{esc(sku)}" inputmode="decimal" aria-label="{esc(sku)} 本次单位成本">',
            f'<input name="basis:{esc(sku)}" aria-label="{esc(sku)} 行级适用依据" placeholder="可覆盖共同依据">',
            esc(f'空缺单价 {group["blank"]}；缺履约记录 {group["missing"]}；订单 {len(group["orders"])}')])
    body = field('operation','','multi_unit_cost',kind='hidden')
    body += ''.join(field('order_id','',r['id'],kind='hidden') for r in scoped)
    body += table(['选择','SKU','已有实际成本','本月参考标准','本次单位成本','行级例外依据','待补数量'],rows)
    body += '<label class="check"><input type="checkbox" name="save_as_standard" value="yes">另存为本月通用成本标准（不会自动扩大本次订单补齐范围）</label>'
    body += field('basis','本次订单适用范围与成本依据；另存标准时必填',required=True)
    return '<details open><summary>多 SKU 成本表 · 当前范围 '+str(len(scoped))+' 个订单行</summary><p>默认只补齐已有履约记录中的空缺单价；已有实际成本、出库数量、退回数量和其他费用保持原值。缺履约记录不会按订单数量猜测创建。</p>' + form(csrf,q,'m2-preview',body,'预览本次成本补齐') + '</details>'


def render_fees(conn,csrf,q):
    all_rows = records(conn)
    states = fee_states(conn,all_rows)
    sources = scoped_records(conn,q,('fulfillment','talent','monthly'))
    sessions, decisions = session_map(conn), assignments(conn)
    orders = {r['business_key']:r for r in all_rows if r['kind']=='orders'}
    rows = []
    label_map = {k:v for _,fields in SCHEMAS.values() for k,v,_ in fields}
    for r in sources:
        if r['kind']=='monthly' and r['data']['month'] != q['month']:
            continue
        target = r['data'].get('session_id')
        if r['kind']=='fulfillment':
            order = orders.get(r['business_key'])
            target = next(iter(decisions.get((order or {}).get('id'),{}).get('targets',{})),None)
        if target in sessions and sessions[target]['start'][:7] != q['month']:
            continue
        if q.get('fee_status','pending')=='pending' and states[r['id']][1].startswith('已确认'):
            continue
        values = []
        for key, label, typ in SCHEMAS[r['kind']][1]:
            if typ in ('optional','money?','unit?'):
                value = r['data'].get(key)
                text = currency(value)+' 元' if type(value) is int else ('未取得' if value is None else str(value))
                values.append(f'{label}：{text}')
        rows.append((r,values))
    page = max(1,min(int(q.get('fee_page',1)),max(1,(len(rows)+49)//50)))
    shown = rows[(page-1)*50:page*50]
    body = field('operation','','cost_edit',kind='hidden')
    body += '<label class="check"><input type="checkbox" data-select-page="record_id">选择/取消本页全部费用</label>'
    body += table(['选择','费用类型','订单/场次/费用编号','数据情况','处理情况','金额与操作'],[[f'<input name="record_id" type="checkbox" value="{r["id"]}">',esc(SCHEMAS[r['kind']][0] + ('（整月共享）' if r['kind']=='monthly' else '')),esc(business_name(r)),esc(states[r['id']][0]),esc(states[r['id']][1]),'<details><summary>查看实际金额</summary>'+esc('；'.join(values))+'</details>'+f'<a href="/workbench?{esc(urlencode(q|{"cost_kind":r["kind"],"cost_id":r["id"]}))}#costs">维护该笔费用</a>'] for r,values in shown])
    fields = ('gift','insurance','logistics','loss','commission','slot_fee','talent_adjustment')
    body += '<div class="grid">'+select('cost_field','批量填写同一字段',[(k,label_map[k]) for k in fields])+field('value','实际金额（元）或不适用',required=True)+'</div><label class="check"><input name="allow_overwrite" type="checkbox" value="yes">明确允许更正已有金额</label>'
    output = '<details open><summary>实际费用记录待办</summary><p>商品履约与达人费用跟随当前范围；月度费用标注“整月共享”。这里只统计实际费用记录，不等同于全部关账条件。完整阻断项请看本页顶部“距离关账还差什么”。</p>'
    output += f'<a href="/workbench?{esc(urlencode(q|{"fee_status":"pending","fee_page":"1"}))}#costs">仅看待处理费用记录</a> · <a href="/workbench?{esc(urlencode(q|{"fee_status":"all","fee_page":"1"}))}#costs">查看全部费用记录</a><p>当前显示 {len(rows)} 笔，第 {page} 页</p>'
    if not rows:
        output += '<div class="empty-inline">实际费用记录没有待办；如果仍不能关账，请按顶部列出的业务范围或分配问题处理。</div>'
    else:
        output += form(csrf,q,'m2-preview',body) + '<p>' + ' · '.join(f'<a href="/workbench?{esc(urlencode(q|{"fee_page":str(p)}))}#costs">费用第 {p} 页</a>' for p in sorted({1,max(1,page-1),min(max(1,(len(rows)+49)//50),page+1)}))+'</p>'
    output += '</details>'
    return output


def preview_rows(preview):
    rows = []
    for item in preview['items']:
        if preview['kind']=='flags':
            before = '；'.join(f'{FLAGS[k]}：{"适用" if v else "不适用"}' for k,v in (item['old'] or {}).items()) or '未确认'
            after = '；'.join(f'{FLAGS[k]}：{"适用" if v else "不适用"}' for k,v in item['new'].items())
            rows.append([item['session_id'],before,after,'统一确认场次业务范围'])
        elif preview['kind']=='assignments':
            previous = (item['old'] or {}).get('targets',{})
            before = ('；'.join(f'{s}：{currency(v)} 元' for s,v in previous.items()) if item['source']['kind']!='orders' else ', '.join(previous)) or '未确认'
            after = '；'.join(f'{s}：{currency(v)} 元' for s,v in item['allocation'].items()) if 'allocation' in item else ', '.join(item['targets'])
            rows.append([business_name(item['source']),before,after,item['basis']])
        else:
            before,after = [],[]
            for key,label,typ in SCHEMAS[item['source']['kind']][1]:
                old,new = item['source']['data'].get(key),item['new'].get(key)
                if old == new:
                    continue
                before.append(f'{label}：{currency(old) if typ=="optional" and type(old) is int else ("未取得" if old is None else old)}')
                after.append(f'{label}：{currency(new) if typ=="optional" and type(new) is int else new}')
            rows.append([business_name(item['source']),'；'.join(before),'；'.join(after),'单位：元；来源及依据在确认时记录'])
    return rows


def render_preview(csrf,q,token,preview):
    rows = preview_rows(preview)
    groups = Counter(r[2] for r in rows)
    result = f'<section id="batch-preview" class="notice"><h2>确认本次批量处理</h2><p>共 {preview["count"]} 条，涉及更正已有归属或金额 {preview["overwrites"]} 条。尚未写入业务数据。</p>'
    result += table(['处理结果分组','记录数'],[[esc(k),esc(v)] for k,v in groups.items()])
    result += table(['订单或场次','修改前','拟采用结果','依据说明'],[[esc(v) for v in r] for r in rows[:50]])
    if preview['kind'] == 'multi_unit_cost':
        result += '<h3>成本标准与既有来源影响</h3>' + table(
            ['SKU','原本月标准','拟采用单价','本次是否保存标准','引用原标准的历史来源'],
            [[esc(s['sku']),esc((s['old'] or {}).get('data',{}).get('unit_cost','无')),
              esc(s['new']['unit_cost']),esc('保存' if s['save'] else '仅本次订单'),
              esc(str(s['affected_count'])+' 条；标准变化后需按既有规则复核')]
             for s in preview['standards']])
        if preview['missing_fulfillment']:
            result += f'<div class="notice issue">另有 {len(preview["missing_fulfillment"])} 个订单行缺少履约记录，本次不会创建或猜测数量。</div>'
        if preview['existing_actual']:
            result += f'<p class="muted">{len(preview["existing_actual"])} 条已有实际单位成本保持不变。</p>'
    result += f'<p>上表展示前50条；<a href="/m2-preview.csv?token={esc(token)}">下载本次全部明细核对</a>。确认只处理此次预览中的记录，后续新增记录不会被带入。</p>'
    body = field('preview_token','',token,kind='hidden')+field('reason','本次确认/更正依据（逐条记录，共用本次说明）',required=True)
    if preview['overwrites']:
        body += '<label class="check"><input name="allow_overwrite" type="checkbox" value="yes" required>已核对以上更正，保留原决定和金额历史</label>'
    if preview['kind'] == 'flags':
        anchor = 'scope'
    elif preview['kind'] == 'assignments':
        anchor = 'attribution' if preview['items'][0]['source']['kind'] == 'orders' else 'allocation'
    else:
        anchor = 'costs'
    return result + form(csrf,q,'m2-apply',body,'确认采用本次结果',anchor=anchor)+'</section>'


def preview_csv(preview):
    return csv_bytes([['订单或场次','修改前','拟采用结果','依据说明']] + preview_rows(preview))


def csv_bytes(rows):
    stream = io.StringIO()
    writer = csv.writer(stream)
    for row in rows:
        writer.writerow(["'"+str(v) if str(v).lstrip().startswith(('=','+','-','@')) else v for v in row])
    return ('\ufeff'+stream.getvalue()).encode('utf-8')


def render_ads(conn,csrf,q):
    from src.workbench.recommendations import latest
    all_rows = scoped_records(conn,q,('ads',))
    suggestions,decisions = latest(conn),assignments(conn)
    page = max(1,int(q.get('allocation_page',1)))
    shown = all_rows[(page-1)*25:page*25]

    output = '<div class="action-strip"><div><strong>先刷新规则结果</strong><span>只生成建议，不会修改最终分配。</span></div>'
    if all_rows:
        output += '<form action="/m2-ads#allocation" method="post">'+field('csrf','',csrf,kind='hidden')+context(q)+field('selection','','filtered',kind='hidden')+'<button name="action" value="generate" formaction="/m2-ads#allocation" class="secondary">生成/刷新当前范围投流候选</button></form>'
    else:
        output += '<span class="status pending">当前范围没有投流记录</span>'
    output += '</div>'

    ready, exceptions, completed = [], [], []
    for row in shown:
        recommendation = suggestions.get(row['id'])
        data = (recommendation or {}).get('data', {})
        targets = data.get('targets', {})
        issues = data.get('issues', [])
        direct = bool(recommendation and targets and not issues)
        decision = decisions.get(row['id'])
        candidates = ', '.join(data.get('candidates', [])) or ('无' if recommendation else '尚未生成')
        suggested = '；'.join(f'{sid}：{weight // 60}分钟' for sid,weight in targets.items()) or '无自动建议'
        desc = '；'.join(issues or data.get('hits', [])) if recommendation else '尚未生成候选'
        common = [esc(row['business_key']),esc(row['data']['account']),esc(row['data']['plan']),esc(row['data']['start'])]
        history = f'<a href="/m2-history?record_id={row["id"]}">查看依据历史</a>'
        if decision:
            completed.append(common+[esc(', '.join(decision.get('targets',{}))),history])
        elif direct:
            ready.append([f'<input name="record_id" type="checkbox" value="{row["id"]}" aria-label="选择可确认投流 {esc(row["business_key"])}">']+common+[esc(candidates),esc(suggested),history])
        else:
            manual_url = '/?' + urlencode(q|{'manual_open':str(row['id'])}) + f'#manual-{row["id"]}'
            exceptions.append(common+[esc(desc or '没有可直接采用的建议'),f'<a href="{esc(manual_url)}">转到手工分配</a> · {history}'])

    output += '<div class="split-head"><div><span class="status">可直接确认</span><h3>明确候选</h3><p>这里只允许选择没有异常、尚未确认的记录。</p></div><strong>'+str(len(ready))+' 笔</strong></div>'
    if ready:
        body = field('selection','','selected',kind='hidden') + '<label class="check"><input type="checkbox" data-select-page="record_id">选择/取消本页全部可确认投流</label>'
        body += table(['选择','记录编号','账号','计划','小时起始','候选场次','建议权重','依据'],ready)
        output += '<form action="/m2-ads#batch-preview" method="post">'+field('csrf','',csrf,kind='hidden')+context(q)+body+'<p><button name="action" value="preview" formaction="/m2-ads#batch-preview">预览所选投流的最终分配</button></p></form>'
    else:
        output += '<div class="empty-inline">当前没有等待批量确认的明确候选。</div>'

    output += '<div class="split-head"><div><span class="status pending">需要判断</span><h3>异常记录</h3><p>异常记录不会进入上面的全选范围，请逐笔确认真实受益场次。</p></div><strong>'+str(len(exceptions))+' 笔</strong></div>'
    if exceptions:
        output += table(['记录编号','账号','计划','小时起始','不能自动确认的原因','下一步'],exceptions)
    else:
        output += '<div class="empty-inline">当前没有需要手工判断的投流。</div>'

    if completed:
        output += '<details><summary>已确认分配 · '+str(len(completed))+' 笔</summary>'+table(['记录编号','账号','计划','小时起始','最终场次','依据历史'],completed)+'</details>'
    return output


def render_history(conn,record_id):
    from src.workbench.db import record
    row=record(conn,record_id)
    all_suggestions=list(conn.execute('SELECT * FROM recommendations WHERE record_id=? ORDER BY id',(record_id,)))
    decision=assignments(conn).get(record_id)
    content=f'<h1>{esc(business_name(row))} 的候选与人工决定</h1><p>当前最终场次：{esc(", ".join((decision or {}).get("targets",{})) or "未确认")}</p>'
    adopted=(decision or {}).get('recommendation_id')
    labels = {k:label for k,label,_ in SCHEMAS[row['kind']][1]}
    for r in all_suggestions:
        d=json.loads(r['data'])
        content+=f'<h2>候选记录 {r["id"]} {"（当前决定采用）" if r["id"]==adopted else ""}</h2><p>{esc(r["created_at"])}</p>'
        source_text = '；'.join(f'{labels.get(k,k)}：{currency(v)+" 元" if k=="amount" and type(v) is int else v}' for k,v in d['evidence']['source'].items())
        link = d['evidence'].get('link')
        scope_basis = link['data']['basis'] if link else ('BP已确认所选订单业务范围' if d['parameters'].get('source_scope_confirmed') else '尚未确认所选订单业务范围')
        content+=table(['项目','当时记录'],[[esc(k),esc(v)] for k,v in [('规则版本',d['rule_version']),('命中规则','；'.join(d['hits'])),('候选场次',', '.join(d['candidates']) or '无候选'),('异常依据','；'.join(d['issues']) or '无规则异常，仍需人工确认'),('支付/投流来源依据',source_text),('范围及关联依据',scope_basis),('当时场次范围','；'.join(f'{s}: {v["talent"]} {v["start"]}—{v["end"]}' for s,v in d['evidence']['scope_sessions'].items() if v))]])
    changes=list(conn.execute("SELECT * FROM changes WHERE object_type IN ('assignment','assignment_review') AND object_key=? ORDER BY id",(str(record_id),)))
    def targets(raw):
        value=json.loads(raw) if raw else None
        if not value:
            return '未确认'
        result = '；'.join(s if row['kind']=='orders' else f'{s}：{currency(v)} 元' for s,v in value.get('targets',{}).items())
        return result + '；依据：' + value.get('basis','') + ('；需复核：'+value['review_reason'] if value.get('review_reason') else '')
    content+='<h2>人工决定与修改</h2>'+table(['原场次','新场次','原因','批量操作编号','时间'],[[esc(targets(r['old_value'])),esc(targets(r['new_value'])),esc(r['reason']),esc(r['operation_id'] or '单笔操作'),esc(r['created_at'])] for r in changes])
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>候选与人工决定</title><style>body{font:15px/1.6 sans-serif;padding:30px}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd}h2{margin-top:30px}</style><body>'+content+'<p><a href="/workbench">返回结算工作台</a></p></body></html>'
