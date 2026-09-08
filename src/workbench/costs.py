"""Business applicability and manual corrections, sharing the import contract."""

import json
from decimal import Decimal

from src.workbench.db import audit, dumps, ensure_open, record, records, session_map, transaction
from src.workbench.imports import affected_months, business_key, normalize, validate_references, parse_value, flag_changed_assignments

FLAGS = {'talent': '达人合作', 'ads': '投流', 'slot': '坑位费', 'gift': '赠品'}


def settings(conn):
    return {r['session_id']: json.loads(r['data']) for r in conn.execute('SELECT * FROM settings')}


def set_applicability(conn, session_id, flags, *, reason):
    if set(flags) != set(FLAGS) or any(type(v) is not bool for v in flags.values()):
        raise ValueError('请明确选择达人合作、投流、坑位费和赠品是否适用')
    with transaction(conn):
        sessions = session_map(conn)
        if session_id not in sessions:
            raise ValueError('场次不存在')
        ensure_open(conn, [sessions[session_id]['start'][:7]])
        old = settings(conn).get(session_id)
        audit(conn, 'applicability', session_id, old, flags, reason)
        conn.execute('INSERT INTO settings(session_id,data) VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET data=excluded.data',
                     (session_id, dumps(flags)))


def save_cost(conn, kind, raw, *, reason, record_id=None, confirm_unit_cost=False):
    if kind not in ('fulfillment', 'talent', 'monthly'):
        raise ValueError('请选择商品与履约、达人费用或月度费用')
    data = normalize(kind, raw)
    key = business_key(kind, data)
    with transaction(conn):
        old = record(conn, record_id) if record_id is not None else None
        if old and (old['kind'] != kind or old['business_key'] != key):
            raise ValueError('更正不能修改业务键，请核对所选记录')
        if old and kind=='fulfillment' and old['data'].get('_cost_source') and data['unit_cost'] is not None and Decimal(old['data']['unit_cost'])==Decimal(data['unit_cost']) and confirm_unit_cost is not True:
            data['_cost_source'] = old['data']['_cost_source']
        if old and kind == 'fulfillment' and old['data'].get('_cost_basis'):
            data['_cost_basis'] = old['data']['_cost_basis']
        months = affected_months(conn, kind, key, data, record_id)
        if old:
            months |= affected_months(conn, kind, key, old['data'], record_id)
        ensure_open(conn, months)
        combined = {(r['kind'], r['business_key']): r['data'] for r in records(conn)}
        combined[(kind, key)] = data
        issues = validate_references(combined)
        if issues:
            raise ValueError('; '.join(issues))
        audit(conn, kind, key, old, {'data': data, 'source': '人工维护'}, reason)
        if old:
            conn.execute('UPDATE records SET data=? WHERE id=?', (dumps(data), record_id))
            flag_changed_assignments(conn,kind,key,old['data'],data,record_id)
            return record_id
        if conn.execute('SELECT 1 FROM records WHERE kind=? AND business_key=?', (kind, key)).fetchone():
            raise ValueError('业务键已存在，请选择已有记录更正')
        return conn.execute('INSERT INTO records(kind,business_key,data) VALUES(?,?,?)', (kind, key, dumps(data))).lastrowid


def standards(conn):
    return {r['id']: dict(r) | {'data':json.loads(r['data'])} for r in conn.execute('SELECT * FROM cost_standards')}


def save_standard(conn, sku, month, unit_cost, *, reason):
    sku, month = parse_value(sku,'text'), parse_value(month,'month')
    unit_cost = parse_value(unit_cost,'unit')
    with transaction(conn):
        ensure_open(conn,[month])
        old = next((s for s in standards(conn).values() if s['sku']==sku and s['month']==month),None)
        data = {'unit_cost':unit_cost,'basis':reason}
        audit(conn,'cost_standard',dumps([sku,month]),old,data,reason)
        conn.execute('INSERT INTO cost_standards(sku,month,data) VALUES(?,?,?) ON CONFLICT(sku,month) DO UPDATE SET data=excluded.data',
                     (sku,month,dumps(data)))
        return conn.execute('SELECT id FROM cost_standards WHERE sku=? AND month=?',(sku,month)).fetchone()[0]


def cost_source_current(fulfillment, order, decision, sessions, catalog):
    origin = fulfillment['data'].get('_cost_source')
    if origin is None:
        return True
    standard = catalog.get(origin['id'])
    target = next(iter(decision['targets']),None) if decision else None
    if not standard or target not in sessions:
        return False
    return (order['data']['sku'] == origin['sku'] == standard['sku'] and
            sessions[target]['start'][:7] == origin['month'] == standard['month'] and
            fulfillment['data']['unit_cost'] is not None and
            Decimal(fulfillment['data']['unit_cost']) == Decimal(origin['data']['unit_cost']) == Decimal(standard['data']['unit_cost']))
