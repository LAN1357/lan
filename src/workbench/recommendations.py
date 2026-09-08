"""Small fixed rules. Candidates are evidence for a BP decision, never a decision."""

import json
from datetime import datetime

from src.workbench.db import audit, dumps, ensure_open, now, records, session_map, transaction
from src.workbench.imports import parse_value, affected_months

RULE_VERSION = 'm2-fixed-rules-v1'


def latest(conn):
    return {r['record_id']: dict(r) | {'data': json.loads(r['data'])} for r in conn.execute(
        'SELECT r.* FROM recommendations r JOIN (SELECT record_id,MAX(id) id FROM recommendations GROUP BY record_id) x ON r.id=x.id')}


def ad_links(conn):
    return {(r['month'],r['account'],r['plan']): dict(r) | {'data': json.loads(r['data'])}
            for r in conn.execute('SELECT * FROM ad_links')}


def adopted(conn, decisions):
    ids = {d['recommendation_id'] for d in decisions.values() if d.get('recommendation_id')}
    return {r['id']:dict(r)|{'data':json.loads(r['data'])} for r in conn.execute('SELECT * FROM recommendations') if r['id'] in ids}


def save_ad_link(conn, month, account, plan, session_ids, *, reason):
    month = parse_value(month, 'month')
    account, plan = parse_value(account, 'text'), parse_value(plan, 'text')
    if not session_ids or len(set(session_ids)) != len(session_ids):
        raise ValueError('请选择不重复的受益场次')
    with transaction(conn):
        sessions = session_map(conn)
        if any(s not in sessions or sessions[s]['start'][:7] != month for s in session_ids):
            raise ValueError('关联范围只能包含所选业务月份的场次')
        ensure_open(conn, [month])
        old = ad_links(conn).get((month,account,plan))
        data = {'session_ids': sorted(session_ids), 'basis': reason}
        audit(conn,'ad_link', dumps([month,account,plan]),old,data,reason)
        conn.execute('INSERT INTO ad_links(month,account,plan,data) VALUES(?,?,?,?) ON CONFLICT(month,account,plan) DO UPDATE SET data=excluded.data',
                     (month,account,plan,dumps(data)))


def inputs(source, sessions, parameters, links):
    kind, data = source['kind'], source['data']
    if kind == 'orders':
        selected = parameters['session_ids']
        source_fields = ('paid_at',)
        link = None
    elif kind == 'ads':
        link = links.get((parameters['month'],data['account'],data['plan']))
        selected = link['data']['session_ids'] if link else []
        source_fields = ('account','plan','start','end','amount')
    else:
        raise ValueError('规则候选仅适用于订单和投流')
    return {'kind':kind,'source':{k:data[k] for k in source_fields},
            'scope_sessions':{sid:sessions.get(sid) for sid in sorted(selected)},
            'link':link}


def candidate(evidence, parameters):
    scope, data = evidence['scope_sessions'], evidence['source']
    result = {'rule_version':RULE_VERSION,'parameters':parameters,'evidence':evidence,
              'hits':[],'candidates':[],'targets':{},'mode':'order','issues':[]}
    if evidence['kind'] == 'orders':
        result['hits'] = ['支付时间落在所选场次区间（左闭右开），仅时间候选']
        result['candidates'] = [s for s,d in scope.items() if d and d['start'] <= data['paid_at'] < d['end']]
        if not parameters.get('source_scope_confirmed'):
            result['issues'].append('尚未确认所选记录与场次的业务范围')
        if any(d is None for d in scope.values()):
            result['issues'].append('候选范围内场次已不存在')
        if len(result['candidates']) == 0:
            result['issues'].append('支付时间未落入所选直播区间，不推断延迟支付归属')
        elif len(result['candidates']) > 1:
            result['issues'].append('多个场次同时覆盖支付时间，请人工选择')
        if any(d and data['paid_at'] in (d['start'],d['end']) for d in scope.values()):
            result['issues'].append('支付时间位于场次边界，请人工复核')
        if not result['issues']:
            result['targets'] = {result['candidates'][0]:1}
    else:
        result['mode'] = 'weights'
        result['hits'] = ['账号及计划精确关联已确认范围', '直播区间与小时投流完整覆盖且不重叠时按时长建议权重']
        if not evidence['link']:
            result['issues'].append('账号及计划尚未关联本月受益场次')
            return result
        if type(data['amount']) is not int:
            result['issues'].append('投流金额未取得')
        segments = sorted((max(data['start'],d['start']), min(data['end'],d['end']),sid)
                          for sid,d in scope.items() if d and max(data['start'],d['start']) < min(data['end'],d['end']))
        result['candidates'] = [sid for _,_,sid in segments]
        cursor = data['start']
        weights = {}
        for start,end,sid in segments:
            if start > cursor:
                result['issues'].append('小时内存在未覆盖时间，不将整笔消耗强行摊满')
            elif start < cursor:
                result['issues'].append('受益场次区间重叠，无法仅按时间确定分配')
            cursor = max(cursor,end)
            weights[sid] = int((datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds())
        if cursor != data['end']:
            result['issues'].append('小时内存在未覆盖时间，不将整笔消耗强行摊满')
        result['issues'] = list(dict.fromkeys(result['issues']))
        if not result['issues']:
            result['targets'] = weights
    return result


def generate(conn, record_ids, *, session_ids=None, month=None, source_scope_confirmed=False):
    if not record_ids or len(set(record_ids)) != len(record_ids):
        raise ValueError('请选择不重复的来源记录')
    with transaction(conn):
        sessions, links = session_map(conn), ad_links(conn)
        rows = {r['id']:r for r in records(conn)}
        parameters = {'session_ids':sorted(set(session_ids or [])), 'month':month,
                      'source_scope_confirmed':source_scope_confirmed is True}
        if any(s not in sessions for s in parameters['session_ids']):
            raise ValueError('候选范围包含不存在的场次')
        generated = []
        for rid in record_ids:
            if rid not in rows or rows[rid]['kind'] not in ('orders','ads'):
                raise ValueError('请选择订单或投流记录')
            if rows[rid]['kind'] == 'orders' and not parameters['session_ids']:
                raise ValueError('请选择订单候选场次范围')
            if rows[rid]['kind'] == 'ads':
                parse_value(month,'month')
            ensure_open(conn,affected_months(conn,rows[rid]['kind'],rows[rid]['business_key'],rows[rid]['data'],rid))
            evidence = inputs(rows[rid], sessions, parameters, links)
            ensure_open(conn, [d['start'][:7] for d in evidence['scope_sessions'].values() if d])
            data = candidate(evidence,parameters)
            created = now()
            identifier = conn.execute('INSERT INTO recommendations(record_id,data,created_at) VALUES(?,?,?)',
                                      (rid,dumps(data),created)).lastrowid
            generated.append({'id':identifier,'record_id':rid,'data':data,'created_at':created})
        return generated


def is_current(recommendation, source, sessions, links):
    data = recommendation['data']
    return data['evidence'] == inputs(source,sessions,data['parameters'],links)
