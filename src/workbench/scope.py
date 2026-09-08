"""Read-only work scope shared by queues and the operating preview.

An import is a set of records, not a new accounting boundary. Preview its
associated complete sessions; never calculate profit from only the changed rows.
"""
import json

from src.workbench.attribution import assignments
from src.workbench.db import records, session_map
from src.workbench.imports import batch_member_ids


def source_sessions(conn, row):
    """Explicit source scope for unresolved records; time alone proves no exclusion."""
    hints = set()
    if row['kind']=='ads':
        from src.workbench.recommendations import ad_links
        data = row['data']
        link = ad_links(conn).get((data['start'][:7], data['account'], data['plan']))
        if link:
            hints.update(link['data']['session_ids'])
    for batch in conn.execute(
        "SELECT DISTINCT b.id,b.context FROM batches b JOIN batch_members m ON m.batch_id=b.id "
        "WHERE m.record_id=? AND b.status='accepted'", (row['id'],)
    ):
        hints.update(json.loads(batch['context']).get('selected_session_ids', []))
        hints.update(r[0] for r in conn.execute(
            'SELECT internal_session_id FROM session_source_mappings WHERE batch_id=?', (batch['id'],)))
    return hints


def resolve_scope(conn, q):
    month, mode = q.get('month'), q.get('work_scope', 'month')
    sessions = {s:d for s,d in session_map(conn).items() if not month or d['start'][:7] == month}
    if mode == 'month':
        return {'mode':mode, 'session_ids':set(sessions), 'member_ids':None, 'label':'本月全部'}
    if mode == 'session':
        sid = q.get('scope_session_id')
        if sid not in sessions:
            raise ValueError('请选择当前月份的有效场次；不会自动扩大到本月全部')
        return {'mode':mode, 'session_ids':{sid}, 'member_ids':None, 'label':f'所选场次：{sid} · {sessions[sid]["talent"]}'}
    if mode != 'batch':
        raise ValueError('未知处理范围')
    try:
        bid = int(q.get('batch_id', ''))
    except (ValueError, TypeError) as exc:
        raise ValueError('请先选择有效导入批次；不会自动扩大到本月全部') from exc
    batch = conn.execute("SELECT * FROM batches WHERE id=? AND status='accepted'", (bid,)).fetchone()
    if batch is None:
        raise ValueError('导入批次不存在或未成功入账')
    action = q.get('batch_action', 'changed')
    if action not in ('changed','unchanged','all'):
        raise ValueError('未知导入成员范围')
    actions = {'changed':('new','update'), 'unchanged':('unchanged',), 'all':None}[action]
    ids = set(batch_member_ids(conn, bid, actions=actions))
    rows, decisions = records(conn), assignments(conn)
    orders = {r['business_key']:r for r in rows if r['kind']=='orders'}
    sids = set(json.loads(batch['context']).get('selected_session_ids', [])) if ids else set()
    for row in rows:
        if row['id'] not in ids:
            continue
        if row['kind'] in ('sessions','talent'):
            sids.add(row['data']['session_id'])
        source = orders.get(row['business_key'], row) if row['kind']=='fulfillment' else row
        sids.update(decisions.get(source['id'], {}).get('targets', {}))
    return {'mode':mode, 'session_ids':sids & sessions.keys(), 'member_ids':ids,
            'label':f'本次导入：{batch["filename"]} · ' + {'changed':'新增与更新','unchanged':'未变旧记录','all':'全部批次成员'}[action]}


def scoped_records(conn, q, kinds):
    """Direct work items follow scope; monthly and shared allocations stay explicit."""
    scope = resolve_scope(conn, q)
    rows, decisions = records(conn), assignments(conn)
    orders = {r['business_key']:r for r in rows if r['kind']=='orders'}
    selected = []
    for row in rows:
        if row['kind'] not in kinds:
            continue
        kind, data = row['kind'], row['data']
        if kind == 'monthly':
            if data['month'] == q.get('month'):
                selected.append(row)
            continue
        source = orders.get(row['business_key'], row) if kind=='fulfillment' else row
        targets = ({data['session_id']} if kind=='talent'
                   else set(decisions.get(source['id'], {}).get('targets', {})))
        if scope['mode']=='batch':
            include = row['id'] in scope['member_ids'] or source['id'] in scope['member_ids']
        elif targets:
            include = bool(targets & scope['session_ids'])
        elif scope['mode']=='month':
            include = data.get('start', source['data'].get('paid_at','')).startswith(q.get('month',''))
        else:
            hints = source_sessions(conn, source)
            include = not hints or bool(hints & scope['session_ids'])
        if include:
            selected.append(row)
    return selected
