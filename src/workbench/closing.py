"""Monthly close validates and publishes in one SQLite transaction."""

import json
from datetime import date

from src.engine.session_profit import AMOUNT_FIELDS, calculate_product_cost, calculate_session
from src.workbench.attribution import assignments, source_amount, needs_review
from src.workbench.costs import settings, standards, cost_source_current
from src.workbench.recommendations import ad_links, is_current
from src.workbench.db import audit, dumps, now, records, session_map, transaction
from src.workbench.imports import MONTHLY_CATEGORIES, NA

RULE_VERSION = 'm1-decimal-half-up-v1'


def check_month(month):
    if not isinstance(month, str) or len(month) != 7:
        raise ValueError('月份应为YYYY-MM')
    date.fromisoformat(month + '-01')


def preview_close(conn, month):
    return _preview_snapshot(conn, month, include_monthly=True)


def preview_operating_profit(conn, month, session_ids=None):
    """Unclosed operating view; monthly omissions never become actual zero costs."""
    snapshot = _preview_snapshot(conn, month, include_monthly=False, session_ids=session_ids)
    fields = ('net_revenue', 'direct_cost', 'operating_profit', 'operating_margin')
    return {
        'month': month, 'issues': snapshot['issues'],
        'results': {sid: {k: result[k] for k in fields}
                    for sid, result in snapshot['results'].items()},
        'total': {k: snapshot['total'][k] for k in fields} if snapshot['total'] else {},
        'persisted': False, 'monthly_fees_included': False,
        'rule_version': RULE_VERSION,
        'scope': 'month' if session_ids is None else 'sessions',
        'session_ids': sorted(snapshot['sessions']),
        'unresolved': snapshot.get('unresolved', []),
    }


def _preview_snapshot(conn, month, *, include_monthly, session_ids=None):
    check_month(month)
    all_sessions = session_map(conn)
    sessions = {s: d for s, d in all_sessions.items() if d['start'][:7] == month}
    if session_ids is not None:
        if isinstance(session_ids, str) or any(s not in sessions for s in session_ids):
            raise ValueError('经营利润预览必须选择当前月份的有效场次')
        selected_ids = set(session_ids)
        sessions = {s:d for s,d in sessions.items() if s in selected_ids}
    all_rows, decisions, flags = records(conn), assignments(conn), settings(conn)
    if not include_monthly:
        all_rows = [row for row in all_rows if row['kind'] != 'monthly']
    catalog, links = standards(conn), ad_links(conn)
    recommendation_rows = [dict(r) | {'data':json.loads(r['data'])} for r in conn.execute('SELECT * FROM recommendations')]
    recommendation_map = {r['id']:r for r in recommendation_rows}
    issues, included, sku, unresolved = [], set(), [], []
    amounts = {s: dict.fromkeys(AMOUNT_FIELDS, 0) for s in sessions}
    if not sessions:
        issues.append('本月没有场次' if session_ids is None else '当前范围没有已明确关联的场次，请先确认订单归属或选择场次')
    for sid in sessions:
        if sid not in flags:
            issues.append(f'{sid}: 尚未确认达人、投流、坑位费和赠品适用性')
    fulfillment = {r['business_key']: r for r in all_rows if r['kind'] == 'fulfillment'}
    talent = {r['business_key']: r for r in all_rows if r['kind'] == 'talent'}

    def amount(value, label, *, applicable=True, allow_na=False):
        if not applicable:
            if value not in (None, NA, 0):
                issues.append(f'{label}: 声明不适用但存在费用')
            return 0
        if value == NA and allow_na:
            return 0
        if type(value) is not int:
            issues.append(f'{label}: 金额未取得或不能标为不适用')
            return 0
        return value

    for row in all_rows:
        kind, data, rid = row['kind'], row['data'], row['id']
        source_label = f'{data["order_id"]} / {data["line_id"]}' if kind=='orders' else row['business_key']
        if kind == 'sessions' and data['session_id'] in sessions:
            included.add(rid)
        if kind not in ('orders', 'ads', 'monthly'):
            continue
        decision = decisions.get(rid)
        if not decision:
            if session_ids is not None and kind in ('orders', 'ads'):
                from src.workbench.scope import source_sessions
                hints = source_sessions(conn, row)
                blocks = not hints or bool(hints & sessions.keys())
                unresolved.append({'record_id':rid, 'kind':kind, 'label':source_label,
                                   'blocks_preview':blocks, 'source_session_ids':sorted(hints)})
                if not blocks:
                    continue
            # Unattributed cross-period records cannot safely be excluded by payment month.
            if kind != 'monthly' or data['month'] == month:
                issues.append(f'{source_label}: {"未归属" if kind == "orders" else "未分配"}，请先确认来源记录')
            continue
        targets = decision['targets']
        relevant = set(targets) & sessions.keys()
        if kind == 'monthly' and data['month'] == month:
            if any(s not in sessions for s in targets):
                issues.append(f'{source_label}: 月度费用目标不在本业务月份')
            included.add(rid)
        if not relevant:
            continue
        included.add(rid)
        adopted = recommendation_map.get(decision.get('recommendation_id'))
        if needs_review(row,decision,all_sessions) or (adopted and not is_current(adopted,row,all_sessions,links)):
            issues.append(f'{source_label}: 归属或分配依据变化，需复核；原决定仍保留')
        if kind == 'orders':
            if len(targets) != 1:
                issues.append(f'{source_label}: 订单需归属单个场次')
                continue
            sid = next(iter(relevant))
            if data['status'] != '已结算' or not data['settled_at']:
                issues.append(f'{source_label}: 最终结算状态或日期缺失')
            contribution = 0
            for key in ('sales', 'platform_fee', 'other_deductions'):
                value = amount(data[key], f'{source_label} {key}')
                amounts[sid][key] += value
                contribution += value if key == 'sales' else -value
            cost = fulfillment.get(row['business_key'])
            if cost is None:
                issues.append(f'{source_label}: 缺少商品与履约成本（含SKU单位成本）')
                continue
            included.add(cost['id'])
            if not cost_source_current(cost,row,decision,all_sessions,catalog):
                issues.append(f'{source_label}: SKU成本标准或适用范围变化，需复核；请按实际成本确认更正')
            cd = cost['data']
            if cd['unit_cost'] is None:
                issues.append(f'{source_label}: SKU单位成本缺失')
                product_cost = 0
            else:
                try:
                    product_cost = calculate_product_cost(cd['unit_cost'], cd['shipped'] - cd['restored'] - cd['damaged'])
                except ValueError as exc:
                    issues.append(f'{source_label}: {exc}')
                    product_cost = 0
            amounts[sid]['product_cost'] += product_cost
            contribution -= product_cost
            for key in ('gift', 'insurance', 'logistics', 'loss'):
                value = amount(cd[key], f'{source_label} {key}',
                    applicable=flags.get(sid, {}).get('gift', True) if key == 'gift' else True,
                    allow_na=(key != 'gift' and not (key == 'loss' and cd['damaged'] > 0)))
                amounts[sid][key] += value
                contribution -= value
            existing_sku = next((x for x in sku if x['session_id'] == sid and x['sku'] == data['sku']), None)
            if existing_sku:
                existing_sku['contribution'] += contribution
            else:
                sku.append({'session_id': sid, 'sku': data['sku'], 'contribution': contribution})
        else:
            try:
                total = source_amount(row)
                if decision.get('source_amount') != total:
                    issues.append(f'{source_label}: 来源金额变化，需重新确认分配')
                if sum(targets.values()) != total:
                    issues.append(f'{source_label}: 分配金额不守恒')
            except ValueError as exc:
                issues.append(f'{source_label}: {exc}')
            key = 'ad_spend' if kind == 'ads' else next(k for k, v in MONTHLY_CATEGORIES.items() if v == data['category'])
            for sid in relevant:
                amounts[sid][key] += targets[sid]
                if kind == 'ads' and flags.get(sid, {}).get('ads') is False and targets[sid] != 0:
                    issues.append(f'{sid}: 声明无投流但存在投流费用')

    for sid in sessions:
        f = flags.get(sid, {})
        if session_ids is not None and not any(
            r['kind']=='orders' and sid in decisions.get(r['id'], {}).get('targets', {}) for r in all_rows
        ):
            issues.append(f'{sid}: 尚无已确认归属的订单，不能把缺少收入记录当作零收入')
        fee = talent.get(sid)
        if fee:
            included.add(fee['id'])
        td = fee['data'] if fee else {}
        amounts[sid]['commission'] = amount(td.get('commission'), f'{sid} 最终佣金', applicable=f.get('talent', True))
        amounts[sid]['slot_fee'] = amount(td.get('slot_fee'), f'{sid} 坑位费', applicable=f.get('slot', True))
        if fee:
            amounts[sid]['talent_adjustment'] = amount(td.get('talent_adjustment'), f'{sid} 达人调整', allow_na=True)
        if (f.get('talent') or f.get('slot') or amounts[sid]['talent_adjustment']) and not td.get('evidence'):
            issues.append(f'{sid}: 达人最终结算依据未到')
        if f.get('ads') and not any(r['kind'] == 'ads' and sid in decisions.get(r['id'], {}).get('targets', {}) for r in all_rows):
            issues.append(f'{sid}: 有投流但消耗记录未到')
    results = {sid: calculate_session(a) for sid, a in amounts.items()} if not issues else {}
    public = {sid: results[sid]['final_profit'] - sum(x['contribution'] for x in sku if x['session_id'] == sid) for sid in results}
    selected = [r for r in all_rows if r['id'] in included]
    batch_ids = {r['batch_id'] for r in selected if r['batch_id'] is not None}
    batches = [dict(r) for r in conn.execute('SELECT id,filename,file_hash,created_at,status FROM batches') if r['id'] in batch_ids]
    object_keys = {(r['kind'], r['business_key']) for r in selected}
    modifications = []
    for row in conn.execute('SELECT * FROM changes ORDER BY id'):
        match = ((row['object_type'], row['object_key']) in object_keys or
                 (row['object_type'] in ('assignment','assignment_review') and int(row['object_key']) in included) or
                 (row['object_type'] == 'applicability' and row['object_key'] in sessions) or
                 (row['object_type'] == 'cost_standard' and json.loads(row['object_key'])[1] == month) or
                 (row['object_type'] == 'ad_link' and json.loads(row['object_key'])[0] == month) or
                 (row['object_type'] == 'reopen' and row['object_key'] == month))
        if match:
            modifications.append(dict(row))
            for name in ('old_value', 'new_value'):
                before_after = json.loads(row[name]) if row[name] else None
                if isinstance(before_after, dict) and before_after.get('batch_id') is not None:
                    batch_ids.add(before_after['batch_id'])
    batches = [dict(r) for r in conn.execute('SELECT id,filename,file_hash,created_at,status FROM batches') if r['id'] in batch_ids]
    total_amounts = {k: sum(a[k] for a in amounts.values()) for k in AMOUNT_FIELDS}
    return ({'unresolved':unresolved} if session_ids is not None else {}) | {'month': month, 'issues': issues, 'sessions': sessions, 'results': results,
            'total': calculate_session(total_amounts) if not issues else {}, 'sku': sku if not issues else [],
            'public': public, 'records': selected, 'batches': batches, 'changes': modifications,
            'assignments': {str(k): v for k, v in decisions.items() if k in included},
            'applicability': {k: v for k, v in flags.items() if k in sessions}, 'rule_version': RULE_VERSION,
            'recommendations':[r for r in recommendation_rows if r['record_id'] in included],
            'cost_standards':[s for s in catalog.values() if s['month']==month],
            'ad_links':[s for s in links.values() if s['month']==month]}


def get_close(conn, month, version=None):
    check_month(month)
    if version is None:
        state = conn.execute('SELECT status FROM months WHERE month=?', (month,)).fetchone()
        if not state or state['status'] != 'closed':
            return None
        row = conn.execute('SELECT snapshot FROM closes WHERE month=? ORDER BY version DESC LIMIT 1', (month,)).fetchone()
    else:
        row = conn.execute('SELECT snapshot FROM closes WHERE month=? AND version=?', (month, version)).fetchone()
    return json.loads(row['snapshot']) if row else None


def close_month(conn, month, *, confirmed):
    if confirmed is not True:
        raise ValueError('请一次确认费用清单完整，含月度共享费用及损益税费范围')
    with transaction(conn):
        existing = get_close(conn, month)
        if existing:
            return existing
        snapshot = preview_close(conn, month)
        if snapshot['issues']:
            raise ValueError('; '.join(snapshot['issues']))
        version = conn.execute('SELECT COALESCE(MAX(version),0)+1 FROM closes WHERE month=?', (month,)).fetchone()[0]
        closed_at = now()
        snapshot.update(version=version, closed_at=closed_at, fees_confirmed=True,
                        confirmation='BP已确认全部场次费用及本月直播承担的共享费用和损益税费清单完整')
        conn.execute("INSERT INTO months(month,status) VALUES(?,'closed') ON CONFLICT(month) DO UPDATE SET status='closed'", (month,))
        conn.execute('INSERT INTO closes(month,version,closed_at,snapshot) VALUES(?,?,?,?)',
                     (month, version, closed_at, dumps(snapshot)))
        return snapshot


def reopen_month(conn, month, *, reason):
    with transaction(conn):
        current = get_close(conn, month)
        if current is None:
            raise ValueError('本月没有有效已关账版本')
        audit(conn, 'reopen', month, {'version': current['version'], 'status': 'closed'}, {'status': 'open'}, reason)
        conn.execute("UPDATE months SET status='open' WHERE month=?", (month,))


def history(conn, month):
    return [dict(r) for r in conn.execute('SELECT month,version,closed_at FROM closes WHERE month=? ORDER BY version DESC', (month,))]
