"""BP decisions only. There is no automatic attribution or rule score in M1."""

import json

from src.engine.session_profit import allocate_cents, integer
from src.workbench.db import audit, dumps, ensure_open, record, records, session_map, transaction
from src.workbench.imports import NA, affected_months

TAIL_RULE = '按绝对金额整数商分配，余数从大到小补分；同余数按场次编号排序，负数恢复符号'


def source_amount(source):
    value = source['data'].get('amount')
    if value == NA:
        return 0
    if type(value) is not int:
        raise ValueError('来源金额尚未取得')
    return value


def assignments(conn):
    return {r['record_id']: json.loads(r['data']) for r in conn.execute('SELECT * FROM assignments')}


def _save_assignment(conn, source, targets, mode, basis, reason, *, context=None, operation_id=None, recommendation=None):
    if not isinstance(basis, str) or not basis.strip():
        raise ValueError('请填写归属/分配依据')
    if not targets:
        raise ValueError('必须指定目标场次')
    sessions = context['sessions'] if context is not None else session_map(conn)
    if any(sid not in sessions for sid in targets):
        raise ValueError('目标场次不存在')
    old = (context['decisions'] if context is not None else assignments(conn)).get(source['id'])
    months = {sessions[sid]['start'][:7] for sid in targets}
    if old:
        months |= {sessions[sid]['start'][:7] for sid in old['targets']}
    months |= affected_months(conn, source['kind'], source['business_key'], source['data'], source['id'])
    ensure_open(conn, months)
    if source['kind'] == 'orders':
        if mode != 'order' or len(targets) != 1:
            raise ValueError('订单行需归属一个场次')
        data = {'mode': mode, 'targets': {next(iter(targets)): 1}, 'basis': basis}
    elif source['kind'] in ('ads', 'monthly'):
        total = source_amount(source)
        if source['kind'] == 'monthly' and any(sessions[s]['start'][:7] != source['data']['month'] for s in targets):
            raise ValueError('月度费用只能分配给同业务月份场次')
        if mode == 'weights':
            amounts = allocate_cents(total, targets)
            denominator = sum(targets.values())
            tails = {s: abs(amounts[s]) - (abs(total) * targets[s] // denominator if denominator else 0) for s in targets}
        elif mode == 'amounts':
            for amount in targets.values():
                integer(amount, '分配金额')
                if (total >= 0 and amount < 0) or (total < 0 and amount > 0):
                    raise ValueError('分配金额符号必须与源额一致')
            if sum(targets.values()) != total:
                raise ValueError('分配金额合计不等于来源金额')
            amounts, tails = dict(targets), {}
        else:
            raise ValueError('请选择权重或明确金额分配')
        data = {'mode': mode, 'targets': amounts, 'weights': targets if mode == 'weights' else None,
                'source_amount': total, 'basis': basis, 'tail_cents': tails, 'tail_rule': TAIL_RULE}
    else:
        raise ValueError('该来源不需要人工场次分配')
    data['evidence'] = decision_evidence(source, sessions, targets)
    if recommendation is not None:
        data['recommendation_id'] = recommendation['id']
    audit(conn, 'assignment', source['id'], old, data, reason, operation_id=operation_id)
    conn.execute('INSERT INTO assignments(record_id,data) VALUES(?,?) ON CONFLICT(record_id) DO UPDATE SET data=excluded.data',
                 (source['id'], dumps(data)))
    if context is not None:
        context['decisions'][source['id']] = data
    return data


def assign(conn, record_id, targets, *, mode='weights', basis, reason):
    with transaction(conn):
        return _save_assignment(conn, record(conn, record_id), targets, mode, basis, reason)


def assign_orders(conn, record_ids, session_id, *, basis, reason):
    if not record_ids or len(set(record_ids)) != len(record_ids):
        raise ValueError('请选择不重复的订单行')
    with transaction(conn):
        context = {'sessions': session_map(conn), 'decisions': assignments(conn)}
        return [_save_assignment(conn, record(conn, rid), {session_id: 1}, 'order', basis, reason, context=context) for rid in record_ids]


def decision_evidence(source, sessions, targets):
    fields = {'orders': ('paid_at',), 'ads': ('account', 'plan', 'start', 'end', 'amount'),
              'monthly': ('month', 'category', 'amount')}[source['kind']]
    return {'source': {k: source['data'].get(k) for k in fields},
            'sessions': {sid: sessions.get(sid) for sid in sorted(targets)}}


def needs_review(source, decision, sessions):
    if not decision:
        return False
    if decision.get('review_reason'):
        return True
    if decision.get('evidence') is not None and decision['evidence'] != decision_evidence(source, sessions, decision['targets']):
        return True
    if source['kind'] in ('ads', 'monthly'):
        try:
            return decision.get('source_amount') != source_amount(source)
        except ValueError:
            return True
    return False


def filter_sources(conn, *, kind='orders', date_prefix='', talent='', time_from='', time_to=''):
    """Filter source payment/hour timestamps; talent refers to existing BP attribution."""
    if kind not in ('orders', 'ads'):
        raise ValueError('仅支持筛选订单或投流')
    decisions, sessions = assignments(conn), session_map(conn)
    result = []
    for row in records(conn, kind):
        timestamp = row['data']['paid_at' if kind == 'orders' else 'start']
        if date_prefix and not timestamp.startswith(date_prefix):
            continue
        if time_from and timestamp < time_from:
            continue
        if time_to and timestamp > time_to:
            continue
        target_ids = decisions.get(row['id'], {}).get('targets', {})
        if talent and not any(talent in sessions[s]['talent'] for s in target_ids):
            continue
        result.append(row)
    return result
