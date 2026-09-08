"""Concrete BP batch previews and atomic confirmation; no rule can call this itself."""

from uuid import uuid4

from src.workbench.attribution import _save_assignment, assignments, needs_review, source_amount
from src.engine.session_profit import allocate_cents
from src.workbench.costs import FLAGS, settings, standards
from src.workbench.db import audit, dumps, ensure_open, records, session_map, transaction
from src.workbench.recommendations import latest, ad_links, is_current, adopted
from src.workbench.imports import normalize, SCHEMAS
from decimal import Decimal


def _base(conn, kind):
    return {'kind':kind,'operation_id':uuid4().hex,'sessions':session_map(conn),
            'items':[],'count':0,'overwrites':0}


def _finish(preview):
    for item in preview['items']:
        if preview['kind']=='assignments' and item['source']['kind']!='orders':
            total = source_amount(item['source'])
            item['allocation'] = allocate_cents(total,item['targets']) if item['mode']=='weights' else item['targets']
    preview['count'] = len(preview['items'])
    if not preview['count']:
        raise ValueError('没有可处理记录；已有成本不会覆盖，缺少履约数据仍须补齐实际数量和费用')
    return preview


def _unique(ids):
    if not ids or len(set(ids)) != len(ids) or any(type(i) is not int for i in ids):
        raise ValueError('请选择不重复的来源记录')


def prepare_assignments(conn, record_ids, *, session_id=None, use_candidates=False, allow_overwrite=False):
    _unique(record_ids)
    preview = _base(conn,'assignments')
    sources, decisions, suggestions, links = {r['id']:r for r in records(conn)}, assignments(conn), latest(conn), ad_links(conn)
    for rid in record_ids:
        source = sources.get(rid)
        if not source or source['kind'] not in ('orders','ads'):
            raise ValueError('请选择订单或投流')
        old = decisions.get(rid)
        if old and not allow_overwrite:
            raise ValueError('包含已确认归属；如需更正，请明确勾选允许更正')
        if old:
            preview['overwrites'] += 1
        suggestion = None
        if use_candidates:
            suggestion = suggestions.get(rid)
            if not suggestion or suggestion['data']['issues'] or not suggestion['data']['targets']:
                issues = (suggestion or {}).get('data', {}).get('issues', [])
                if issues == ['尚未确认所选记录与场次的业务范围']:
                    raise ValueError(
                        f'{source["business_key"]}: 生成候选时未勾选业务范围确认；'
                        '请勾选后重新生成候选，再预览归属确认')
                detail = '；'.join(issues)
                raise ValueError(
                    f'{source["business_key"]}: 无可直接确认的候选'
                    + (f'（{detail}）' if detail else '')
                    + '，请重新生成或改用人工指定场次')
            if not is_current(suggestion,source,preview['sessions'],links):
                raise ValueError('推荐依据已变化，请重新生成候选')
            targets, mode = suggestion['data']['targets'], suggestion['data']['mode']
            basis = '；'.join(suggestion['data']['hits'])
        else:
            if source['kind'] != 'orders' or session_id not in preview['sessions']:
                raise ValueError('人工订单归属需指定有效场次')
            targets, mode, basis = {session_id:1}, 'order', 'BP人工指定场次'
        ensure_open(conn,[preview['sessions'][s]['start'][:7] for s in set(targets) | set((old or {}).get('targets',{}))])
        preview['items'].append({'source':source,'old':old,'targets':targets,'mode':mode,'basis':basis,'recommendation':suggestion})
    return _finish(preview)


def prepare_flags(conn, session_ids, flags):
    if not session_ids or len(set(session_ids)) != len(session_ids):
        raise ValueError('请选择不重复的场次')
    if set(flags) != set(FLAGS) or any(type(v) is not bool for v in flags.values()):
        raise ValueError('请明确选择四项业务范围')
    preview, existing = _base(conn,'flags'), settings(conn)
    for sid in session_ids:
        if sid not in preview['sessions']:
            raise ValueError('场次不存在')
        ensure_open(conn,[preview['sessions'][sid]['start'][:7]])
        preview['items'].append({'session_id':sid,'old':existing.get(sid),'new':dict(flags)})
    return _finish(preview)


def prepare_cost_fill(conn, standard_id):
    catalog = standards(conn)
    if standard_id not in catalog:
        raise ValueError('成本标准不存在')
    standard = catalog[standard_id]
    ensure_open(conn,[standard['month']])
    preview = _base(conn,'unit_cost')
    preview['standard'] = standard
    all_rows, decisions = records(conn), assignments(conn)
    originals, links = adopted(conn,decisions), ad_links(conn)
    fulfillment = {r['business_key']:r for r in all_rows if r['kind']=='fulfillment'}
    for order in (r for r in all_rows if r['kind']=='orders' and r['data']['sku']==standard['sku']):
        decision = decisions.get(order['id'])
        if not decision or needs_review(order,decision,preview['sessions']):
            continue
        original = originals.get(decision.get('recommendation_id'))
        if original and not is_current(original,order,preview['sessions'],links):
            continue
        sid = next(iter(decision['targets']))
        if preview['sessions'][sid]['start'][:7] != standard['month']:
            continue
        cost = fulfillment.get(order['business_key'])
        if cost and cost['data']['unit_cost'] is None:
            new = cost['data'] | {'unit_cost':standard['data']['unit_cost'],'_cost_source':standard}
            preview['items'].append({'source':cost,'order':order,'decision':decision,'new':new})
    return _finish(preview)


def prepare_multi_cost_fill(conn, order_ids, sku_values, *, month,
                            save_as_standard=False, basis='', sku_basis=None):
    """Freeze a scoped multi-SKU fill; never creates missing fulfillment rows."""
    _unique(order_ids)
    if not isinstance(sku_values, dict) or not sku_values:
        raise ValueError('请至少选择一个SKU并填写本次单位成本')
    from src.workbench.imports import parse_value
    normalized = {str(sku): parse_value(value, 'unit') for sku, value in sku_values.items()}
    sku_basis = {str(k):str(v).strip() for k,v in (sku_basis or {}).items()}
    if save_as_standard and any(not (sku_basis.get(sku) or str(basis).strip()) for sku in normalized):
        raise ValueError('另存本月通用成本标准时，请填写适用依据')
    ensure_open(conn, [month])
    preview = _base(conn, 'multi_unit_cost')
    preview.update(month=month, missing_fulfillment=[], existing_actual=[], standards=[])
    sources = {r['id']: r for r in records(conn)}
    fulfillment = {r['business_key']: r for r in sources.values() if r['kind'] == 'fulfillment'}
    decisions = assignments(conn)
    catalog = standards(conn)
    by_key = {(s['sku'], s['month']): s for s in catalog.values()}
    for sku, value in normalized.items():
        old = by_key.get((sku, month))
        row_basis = sku_basis.get(sku) or str(basis).strip()
        affected = sum(1 for row in sources.values()
                       if row['kind'] == 'fulfillment' and old
                       and row['data'].get('_cost_source',{}).get('id') == old['id'])
        preview['standards'].append({
            'sku': sku, 'month': month, 'old': old,
            'new': {'unit_cost': value, 'basis': row_basis},
            'save': bool(save_as_standard), 'affected_count': affected,
        })
    for rid in order_ids:
        order = sources.get(rid)
        if not order or order['kind'] != 'orders':
            raise ValueError('成本补齐范围只能包含订单行')
        if order['data']['sku'] not in normalized:
            continue
        decision = decisions.get(rid)
        if not decision or needs_review(order, decision, preview['sessions']):
            raise ValueError(f'{order["business_key"]}: 订单归属尚未确认或需要复核')
        sid = next(iter(decision['targets']))
        if preview['sessions'][sid]['start'][:7] != month:
            raise ValueError('所选订单不属于当前业务月份')
        cost = fulfillment.get(order['business_key'])
        if not cost:
            preview['missing_fulfillment'].append(order)
            continue
        if cost['data']['unit_cost'] is not None:
            preview['existing_actual'].append(cost)
            continue
        row_basis = sku_basis.get(order['data']['sku']) or str(basis).strip()
        new = cost['data'] | {'unit_cost': normalized[order['data']['sku']],
                              '_cost_basis': row_basis}
        preview['items'].append({'source': cost, 'order': order,
                                 'decision': decision, 'new': new,
                                 'sku': order['data']['sku']})
    preview['count'] = len(preview['items'])
    if not preview['count'] and not any(s['save'] for s in preview['standards']):
        raise ValueError('没有可补齐记录；已有实际成本不会覆盖，缺少履约数据仍须补齐实际数量')
    return preview


def prepare_allocation_reuse(conn, from_id, record_ids):
    _unique(record_ids)
    sources, decisions = {r['id']:r for r in records(conn)}, assignments(conn)
    original, decision = sources.get(from_id), decisions.get(from_id)
    preview = _base(conn,'assignments')
    if (not original or original['kind']!='monthly' or not decision or decision['mode']!='weights'
            or needs_review(original,decision,preview['sessions'])):
        raise ValueError('请选择已确认且有效的月度费用权重方案')
    preview['reuse'] = {'source':original,'decision':decision}
    for rid in record_ids:
        source = sources.get(rid)
        if not source or source['kind']!='monthly' or source['data']['month'] != original['data']['month'] or rid == from_id:
            raise ValueError('只能复用给同月其他费用，请逐笔核对承担范围')
        ensure_open(conn,[source['data']['month']])
        old = decisions.get(rid)
        preview['overwrites'] += int(old is not None)
        preview['items'].append({'source':source,'old':old,'targets':decision['weights'],'mode':'weights',
            'basis':f'复用费用 {original["business_key"]} 的已确认受益场次和权重；源额按本笔计算','recommendation':None})
    return _finish(preview)


def prepare_cost_edit(conn, record_ids, field, value, *, allow_overwrite=False):
    _unique(record_ids)
    preview = _base(conn,'cost_edit')
    sources = {r['id']:r for r in records(conn)}
    allowed = {'fulfillment':('gift','insurance','logistics','loss'), 'talent':('commission','slot_fee','talent_adjustment')}
    for rid in record_ids:
        row = sources.get(rid)
        if not row or field not in allowed.get(row['kind'],()):
            raise ValueError('所选费用不支持此字段；只允许批量填写实际履约费用或达人最终费用')
        if row['data'][field] is not None:
            if not allow_overwrite:
                raise ValueError('包含已有金额，请明确允许更正或只选择缺失项目')
            preview['overwrites'] += 1
        raw = dict(row['data'])
        for key,_,typ in SCHEMAS[row['kind']][1]:
            if typ in ('optional','money?') and type(raw[key]) is int:
                raw[key] = str(Decimal(raw[key])/100)
        raw[field] = value
        new = normalize(row['kind'],raw)
        for internal in ('_cost_source','_cost_basis'):
            if internal in row['data']:
                new[internal] = row['data'][internal]
        preview['items'].append({'source':row,'new':new})
    return _finish(preview)


def apply_batch(conn, preview, *, reason, allow_overwrite=False):
    if not reason or not reason.strip():
        raise ValueError('请填写本次确认或更正依据')
    with transaction(conn):
        op = preview['operation_id']
        if conn.execute('SELECT 1 FROM changes WHERE operation_id=?',(op,)).fetchone():
            raise ValueError('该批次已处理，请刷新页面')
        if preview['overwrites'] and allow_overwrite is not True:
            raise ValueError('本次包含已确认记录，请明确确认更正')
        context = {'sessions':session_map(conn),'decisions':assignments(conn)}
        if context['sessions'] != preview['sessions']:
            raise ValueError('场次依据已变化，请重新预览')
        sources = {r['id']:r for r in records(conn)}
        links = ad_links(conn)
        kind = preview['kind']
        if preview.get('reuse'):
            r = preview['reuse']
            if sources.get(r['source']['id']) != r['source'] or context['decisions'].get(r['source']['id']) != r['decision']:
                raise ValueError('复用方案已变化，请重新预览')
        if kind == 'unit_cost' and standards(conn).get(preview['standard']['id']) != preview['standard']:
            raise ValueError('成本标准已变化，请重新预览')
        if kind == 'multi_unit_cost':
            catalog = standards(conn)
            by_key = {(s['sku'], s['month']): s for s in catalog.values()}
            if any(by_key.get((s['sku'], s['month'])) != s['old'] for s in preview['standards']):
                raise ValueError('成本标准已变化，请重新预览')
        current_flags = settings(conn)
        # Validate every selected record before mutating any of them.
        for item in preview['items']:
            if kind == 'flags':
                if current_flags.get(item['session_id']) != item['old']:
                    raise ValueError('业务范围已变化，请重新预览')
                ensure_open(conn,[context['sessions'][item['session_id']]['start'][:7]])
                continue
            rid = item['source']['id']
            if sources.get(rid) != item['source']:
                raise ValueError('来源记录已变化，请重新预览')
            if kind == 'assignments':
                if context['decisions'].get(rid) != item['old']:
                    raise ValueError('人工决定已变化，请重新预览')
                if item.get('recommendation') and not is_current(item['recommendation'],sources[rid],context['sessions'],links):
                    raise ValueError('推荐依据已变化，请重新预览')
            elif kind in ('unit_cost', 'multi_unit_cost'):
                order_id = item['order']['id']
                if sources.get(order_id) != item['order'] or context['decisions'].get(order_id) != item['decision']:
                    raise ValueError('成本关联订单或归属已变化，请重新预览')
                ensure_open(conn,[preview['standard']['month'] if kind == 'unit_cost' else preview['month']])
            elif kind == 'cost_edit':
                from src.workbench.imports import affected_months
                ensure_open(conn,affected_months(conn,item['source']['kind'],item['source']['business_key'],item['source']['data'],rid))
            else:
                raise ValueError('未知批量操作')
        saved_standards = {}
        if kind == 'multi_unit_cost':
            for standard in preview['standards']:
                if not standard['save']:
                    continue
                key = dumps([standard['sku'], standard['month']])
                audit(conn, 'cost_standard', key, standard['old'], standard['new'],
                      reason, operation_id=op)
                conn.execute(
                    'INSERT INTO cost_standards(sku,month,data) VALUES(?,?,?) '
                    'ON CONFLICT(sku,month) DO UPDATE SET data=excluded.data',
                    (standard['sku'], standard['month'], dumps(standard['new'])))
                row = conn.execute('SELECT * FROM cost_standards WHERE sku=? AND month=?',
                                   (standard['sku'], standard['month'])).fetchone()
                saved_standards[standard['sku']] = dict(row) | {'data': standard['new']}
        for item in preview['items']:
            if kind == 'assignments':
                _save_assignment(conn,item['source'],item['targets'],item['mode'],item['basis'],reason,
                                 context=context,operation_id=op,recommendation=item.get('recommendation'))
            elif kind == 'flags':
                audit(conn,'applicability',item['session_id'],item['old'],item['new'],reason,operation_id=op)
                conn.execute('INSERT INTO settings(session_id,data) VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET data=excluded.data',
                             (item['session_id'],dumps(item['new'])))
            elif kind in ('unit_cost','multi_unit_cost','cost_edit'):
                if kind == 'multi_unit_cost' and item['sku'] in saved_standards:
                    item = item | {'new': item['new'] | {
                        '_cost_source': saved_standards[item['sku']]}}
                audit(conn,item['source']['kind'],item['source']['business_key'],item['source'],{'data':item['new']},reason,operation_id=op)
                conn.execute('UPDATE records SET data=? WHERE id=?',(dumps(item['new']),item['source']['id']))
        return {'count':preview['count'],'operation_id':op}
