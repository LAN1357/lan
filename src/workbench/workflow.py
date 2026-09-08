"""Read-only BP queues: evidence and processing state remain distinct."""
import re
from datetime import datetime

from src.workbench.imports import parse_value

from src.workbench.attribution import assignments, needs_review
from src.workbench.costs import settings, standards, cost_source_current
from src.workbench.db import records, session_map
from src.workbench.recommendations import latest, ad_links, is_current, adopted
from src.workbench.scope import resolve_scope, source_sessions

ORDER_STATES = {'pending':'全部待处理','unassigned':'未生成候选','candidate':'有候选待确认',
                'exception':'异常待判断','review':'依据变化需复核','confirmed':'已确认','closed':'已关账','all':'全部记录'}
FILTER_KEYS = ('month','step','profit_view','order_status','search','date_prefix','talent',
               'time_from','time_to','page','fee_status','fee_page','allocation_page',
               'manual_open','work_scope','batch_id','batch_action','scope_session_id','cost_scope')


def _payment_time_filter(value, *, upper=False):
    """Accept a clock time for daily filtering or a full local timestamp."""
    text = str(value).strip()
    if re.fullmatch(r'\d{2}:\d{2}(?::\d{2})?', text):
        try:
            clock = datetime.strptime(text, '%H:%M:%S' if len(text) == 8 else '%H:%M')
        except ValueError as exc:
            raise ValueError('支付时间应为 HH:MM 或 YYYY-MM-DD HH:MM') from exc
        if upper and len(text) == 5:
            clock = clock.replace(second=59)
        return 'clock', clock.strftime('%H:%M:%S')
    try:
        if upper and len(text) == 16:
            text += ':59'
        return 'timestamp', parse_value(text, 'time')
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('支付时间应为 HH:MM 或 YYYY-MM-DD HH:MM') from exc


def order_queue(conn, filters=None):
    q = filters or {}
    scope = resolve_scope(conn, q)
    wanted = q.get('order_status','pending')
    if wanted not in ORDER_STATES:
        raise ValueError('未知订单状态')
    lower = _payment_time_filter(q['time_from']) if q.get('time_from') else None
    upper = _payment_time_filter(q['time_to'], upper=True) if q.get('time_to') else None
    if lower and upper and lower[0] == upper[0] and lower[1] > upper[1]:
        raise ValueError('支付时间截止不能早于开始')
    sessions, decisions, suggestions, links = session_map(conn), assignments(conn), latest(conn), ad_links(conn)
    closed = {r['month'] for r in conn.execute("SELECT month FROM months WHERE status='closed'")}
    originals = adopted(conn,decisions)
    batch_ids = scope['member_ids']
    if batch_ids is not None:
        cost_keys = {r['business_key'] for r in records(conn,'fulfillment') if r['id'] in batch_ids}
        batch_ids = batch_ids | {r['id'] for r in records(conn,'orders') if r['business_key'] in cost_keys}
    result, counts = [], dict.fromkeys(ORDER_STATES,0)
    for row in records(conn,'orders'):
        d = row['data']
        decision, suggestion = decisions.get(row['id']), suggestions.get(row['id'])
        timestamp = d['paid_at']
        if q.get('search') and q['search'] not in ' '.join((d['order_id'],d['line_id'],d['sku'])):
            continue
        if q.get('date_prefix') and not timestamp.startswith(q['date_prefix']):
            continue
        if lower and (timestamp[11:19] if lower[0] == 'clock' else timestamp) < lower[1]:
            continue
        if upper and (timestamp[11:19] if upper[0] == 'clock' else timestamp) > upper[1]:
            continue
        targets = (decision or {}).get('targets',{})
        if batch_ids is not None and row['id'] not in batch_ids:
            continue
        if q.get('work_scope') == 'session' and q.get('scope_session_id'):
            hints = source_sessions(conn, row) if not targets else set()
            if (targets and q['scope_session_id'] not in targets) or (not targets and hints and q['scope_session_id'] not in hints):
                continue
        if q.get('work_scope') == 'month' and q.get('month'):
            target_months = {sessions[s]['start'][:7] for s in targets if s in sessions}
            if target_months:
                if q['month'] not in target_months:
                    continue
            elif not timestamp.startswith(q['month']):
                continue
        if targets and q.get('month') and not any(sessions[s]['start'][:7]==q['month'] for s in targets):
            continue
        if q.get('talent') and not any(q['talent'] in sessions[s]['talent'] for s in targets):
            continue
        if decision:
            original = originals.get(decision.get('recommendation_id'))
            state = 'review' if needs_review(row,decision,sessions) or (original and not is_current(original,row,sessions,links)) else 'confirmed'
            if any(sessions.get(s,{}).get('start','')[:7] in closed for s in targets):
                state = 'closed'
        elif suggestion:
            state = 'candidate' if not suggestion['data']['issues'] and is_current(suggestion,row,sessions,links) else 'exception'
        else:
            state = 'unassigned'
        counts[state] += 1
        counts['all'] += 1
        if state in ('unassigned','candidate','exception','review'):
            counts['pending'] += 1
        if wanted != 'all' and state != wanted and not (wanted=='pending' and state in ('unassigned','candidate','exception','review')):
            continue
        result.append(row | {'decision':decision,'recommendation':suggestion,'state':state})
    return result, counts


def fee_states(conn, rows=None):
    all_rows = rows if rows is not None else records(conn)
    orders = {r['business_key']:r for r in all_rows if r['kind']=='orders'}
    decisions, sessions, catalog, flags = assignments(conn), session_map(conn), standards(conn), settings(conn)
    originals, links = adopted(conn,decisions), ad_links(conn)
    result = {}
    for row in all_rows:
        kind, d = row['kind'], row['data']
        if kind not in ('fulfillment','talent','monthly','ads'):
            continue
        state, process = '已取得', '已确认'
        if kind in ('monthly','ads'):
            if d['amount'] is None:
                state,process = '未取得','待补齐金额'
            else:
                state = '不适用' if d['amount']=='不适用' else ('已取得（实际0元）' if d['amount']==0 else '已取得')
                decision = decisions.get(row['id'])
                process = '待分配' if not decision else ('需复核' if needs_review(row,decision,sessions) else '已确认分配')
                original = originals.get((decision or {}).get('recommendation_id'))
                if original and not is_current(original,row,sessions,links):
                    process = '需复核'
        elif kind == 'fulfillment':
            order = orders.get(row['business_key'])
            decision = decisions.get(order['id']) if order else None
            sid = next(iter(decision['targets'])) if decision else None
            required = ['unit_cost','insurance','logistics','loss'] + (['gift'] if flags.get(sid,{}).get('gift',True) else [])
            missing = [k for k in required if d.get(k) is None]
            if missing:
                state,process = '未取得','待补齐成本或费用'
            elif not decision:
                process = '待确认订单归属'
            elif not cost_source_current(row,order,decision,sessions,catalog):
                process = '需复核'
            elif sid not in flags:
                process = '待确认业务范围'
            elif needs_review(order,decision,sessions):
                process = '需复核订单归属'
            elif ((flags[sid]['gift'] and d['gift']=='不适用') or
                  (not flags[sid]['gift'] and d['gift'] not in (None,0,'不适用')) or
                  (d['damaged']>0 and d['loss']=='不适用')):
                process = '待处理适用范围或损耗冲突'
        else:
            f = flags.get(d['session_id'])
            required = (['commission'] if not f or f['talent'] else []) + (['slot_fee'] if not f or f['slot'] else []) + ['talent_adjustment']
            if any(d.get(k) is None for k in required):
                state,process = '未取得','待补齐结算费用'
            elif not f:
                process = '待确认业务范围'
            elif (f['talent'] or f['slot']) and not d.get('evidence'):
                process = '待补齐结算依据'
            elif any((f[flag] and d[key]=='不适用') or (not f[flag] and d[key] not in (None,0,'不适用'))
                     for flag,key in [('talent','commission'),('slot','slot_fee')]):
                process = '待处理适用范围冲突'
            elif type(d.get('talent_adjustment')) is int and d['talent_adjustment'] and not d.get('evidence'):
                process = '待补齐结算依据'
        result[row['id']] = (state,process)
    return result
