"""Narrow evidence views over one immutable close snapshot."""

from __future__ import annotations

import json

from src.analysis import ANALYSIS_RULE_VERSION
from src.analysis.common import money, paging, ratio_value
from src.analysis.reader import AnalysisError, CloseReader
from src.analysis.schemas import (DEFAULT_LIMIT, DEFAULT_MODE, DEFAULT_OFFSET,
                                  EVIDENCE_TOPICS)
from src.engine.session_profit import AMOUNT_FIELDS


def _snapshot(reader: CloseReader, close_ref: dict, session_id: str, mode: str):
    snapshot = reader.load_ref(close_ref, mode=mode)
    if not isinstance(session_id, str) or session_id not in snapshot['sessions']:
        raise AnalysisError('SESSION_NOT_FOUND', f'关账版本内找不到场次 {session_id}')
    return snapshot


def _json(value):
    if value in (None, ''):
        return None
    if isinstance(value, (dict, list, int, bool)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return str(value)[:2000]


def _record_index(snapshot):
    return {str(row['id']): row for row in snapshot.get('records', [])}


def _assignment_items(snapshot, session_id):
    records = _record_index(snapshot)
    items = []
    for record_id, decision in snapshot.get('assignments', {}).items():
        targets = decision.get('targets', {})
        if session_id not in targets:
            continue
        row = records.get(str(record_id), {})
        is_money_allocation = row.get('kind') in ('ads', 'monthly')
        items.append({
            'record_id': int(record_id), 'record_kind': row.get('kind'),
            'business_key': row.get('business_key'),
            'allocated_cents': targets[session_id] if is_money_allocation else None,
            'allocated_amount': money(targets[session_id]) if is_money_allocation and type(targets[session_id]) is int else None,
            'assignment_weight': targets[session_id] if not is_money_allocation else None,
            'all_targets': targets, 'mode': decision.get('mode'), 'basis': decision.get('basis'),
            'recommendation_id': decision.get('recommendation_id'), 'evidence': decision.get('evidence'),
            'source_ref': {'month': snapshot['month'], 'version': snapshot['version'],
                           'section': 'assignments', 'record_id': int(record_id)},
        })
    return sorted(items, key=lambda x: (x['record_kind'] or '', x['business_key'] or '', x['record_id']))


def _changes(snapshot, session_id):
    records = _record_index(snapshot)
    related_ids = {str(item['record_id']) for item in _assignment_items(snapshot, session_id)}
    for rid, row in records.items():
        if row.get('business_key') == session_id:
            related_ids.add(rid)
    result = []
    for row in snapshot.get('changes', []):
        object_type, object_key = row.get('object_type'), str(row.get('object_key'))
        related = (object_type == 'applicability' and object_key == session_id) or (
            object_type in ('assignment', 'assignment_review') and object_key in related_ids) or (
            object_key == session_id)
        if not related:
            continue
        result.append({
            'change_id': row.get('id'), 'object_type': object_type, 'object_key': object_key,
            'old_value': _json(row.get('old_value')), 'new_value': _json(row.get('new_value')),
            'reason': row.get('reason'), 'created_at': row.get('created_at'),
            'source_ref': {'month': snapshot['month'], 'version': snapshot['version'],
                           'section': 'changes', 'change_id': row.get('id')},
        })
    return sorted(result, key=lambda x: (x['created_at'] or '', x['change_id'] or 0))


def get_session_evidence(reader: CloseReader, close_ref: dict, session_id: str, *,
                         topic: str = 'pnl', mode: str = DEFAULT_MODE,
                         offset: int = DEFAULT_OFFSET, limit: int = DEFAULT_LIMIT) -> dict:
    if topic not in EVIDENCE_TOPICS:
        raise AnalysisError('INVALID_SCOPE', 'topic 只能是 pnl、sku、allocation 或 changes')
    snapshot = _snapshot(reader, close_ref, session_id, mode)
    result = snapshot['results'][session_id]
    if topic == 'pnl':
        items = [{
            'session': snapshot['sessions'][session_id],
            'amounts': {key: money(result[key]) for key in list(AMOUNT_FIELDS) + [
                'net_revenue', 'direct_cost', 'indirect_cost', 'operating_profit', 'final_profit']},
            'ratios': {key: ratio_value(result[key]) for key in ('operating_margin', 'final_margin', 'ad_roi')},
            'source_ref': {'month': snapshot['month'], 'version': snapshot['version'],
                           'section': 'results', 'session_id': session_id},
        }]
        limitations = []
    elif topic == 'sku':
        items = [{**row, 'contribution_amount': money(row['contribution']),
                  'source_ref': {'month': snapshot['month'], 'version': snapshot['version'],
                                 'section': 'sku', 'session_id': session_id, 'sku': row['sku']}}
                 for row in snapshot.get('sku', []) if row.get('session_id') == session_id]
        public = snapshot.get('public', {}).get(session_id)
        if public is not None:
            items.append({'session_id': session_id, 'sku': None, 'label': '公共及非SKU部分',
                          'contribution': public, 'contribution_amount': money(public),
                          'source_ref': {'month': snapshot['month'], 'version': snapshot['version'],
                                         'section': 'public', 'session_id': session_id}})
        limitations = ['SKU为直接贡献拆分，不是SKU全成本净利润；公共及共享费用未强行归到SKU']
    elif topic == 'allocation':
        items = _assignment_items(snapshot, session_id)
        limitations = ['展示关账版本内保存的归属和分配依据，不读取当前实时决定']
    else:
        items = _changes(snapshot, session_id)
        limitations = ['修改记录说明输入或决定发生过变化，不单独证明每项修改的利润因果金额']
    page_info = paging(len(items), offset, limit)
    page_items = items[offset:offset + limit]
    return {
        'status': 'ok', 'as_of': reader._as_of(),
        'scope': {'mode': mode, 'close_ref': snapshot['_close_ref'], 'session_id': session_id},
        'accounting_rule_version': snapshot['rule_version'],
        'analysis_rule_version': ANALYSIS_RULE_VERSION,
        'data': {'topic': topic, 'items': page_items, 'paging': page_info},
        'source_refs': [x['source_ref'] for x in page_items],
        'limitations': limitations,
        'summary_text': f'{session_id} 的 {topic} 证据共 {len(items)} 项。',
    }


def compare_close_versions(reader: CloseReader, month: str, base_version: int, current_version: int,
                           *, offset: int = DEFAULT_OFFSET, limit: int = DEFAULT_LIMIT) -> dict:
    if base_version == current_version:
        raise AnalysisError('INVALID_SCOPE', '版本比较需要两个不同版本')
    with reader.transaction() as conn:
        base = reader.load_ref({'month': month, 'version': base_version}, mode='history', conn=conn)
        current = reader.load_ref({'month': month, 'version': current_version}, mode='history', conn=conn)
        as_of = reader._as_of()
    if base['rule_version'] != current['rule_version']:
        raise AnalysisError('INCOMPATIBLE_RULES', '两个版本使用不同核算规则，不能比较')
    session_ids = sorted(set(base['sessions']) | set(current['sessions']))
    items = []
    for sid in session_ids:
        before, after = base['results'].get(sid), current['results'].get(sid)
        if before is None:
            items.append({'session_id': sid, 'change': 'added', 'current': {
                key: money(after[key]) for key in ('operating_profit', 'final_profit')}})
        elif after is None:
            items.append({'session_id': sid, 'change': 'removed', 'base': {
                key: money(before[key]) for key in ('operating_profit', 'final_profit')}})
        else:
            deltas = {key: after[key] - before[key] for key in AMOUNT_FIELDS
                      if after[key] != before[key]}
            if deltas:
                items.append({'session_id': sid, 'change': 'changed',
                              'amount_changes': {k: money(v) for k, v in deltas.items()},
                              'operating_profit_delta': money(after['operating_profit'] - before['operating_profit']),
                              'final_profit_delta': money(after['final_profit'] - before['final_profit'])})
    page_info = paging(len(items), offset, limit)
    records = lambda s: {(x.get('kind'), x.get('business_key')): x for x in s.get('records', [])}
    br, cr = records(base), records(current)
    changed_sources = []
    for key in sorted(set(br) | set(cr)):
        if br.get(key) != cr.get(key):
            changed_sources.append({'record_kind': key[0], 'business_key': key[1],
                                    'change': 'added' if key not in br else ('removed' if key not in cr else 'changed')})
    def decisions(snapshot):
        index = _record_index(snapshot)
        return {(index.get(str(rid), {}).get('kind'), index.get(str(rid), {}).get('business_key')): decision
                for rid, decision in snapshot.get('assignments', {}).items()}
    bd, cd = decisions(base), decisions(current)
    changed_assignments = []
    for key in sorted(set(bd) | set(cd), key=lambda x: (x[0] or '', x[1] or '')):
        if bd.get(key) != cd.get(key):
            changed_assignments.append({
                'record_kind': key[0], 'business_key': key[1],
                'change': 'added' if key not in bd else ('removed' if key not in cd else 'changed'),
                'base_targets': bd.get(key, {}).get('targets'),
                'current_targets': cd.get(key, {}).get('targets'),
                'current_basis': cd.get(key, {}).get('basis'),
            })
    changed_applicability = []
    for sid in sorted(set(base.get('applicability', {})) | set(current.get('applicability', {}))):
        before = base.get('applicability', {}).get(sid)
        after = current.get('applicability', {}).get(sid)
        if before != after:
            changed_applicability.append({'session_id': sid, 'base': before, 'current': after})
    return {
        'status': 'ok', 'as_of': as_of,
        'scope': {'month': month, 'base_version': base_version, 'current_version': current_version,
                  'current_version_is_active': current['_close_ref']['is_current']},
        'accounting_rule_version': base['rule_version'], 'analysis_rule_version': ANALYSIS_RULE_VERSION,
        'data': {'session_changes': items[offset:offset + limit], 'paging': page_info,
                 'changed_sources': changed_sources, 'changed_assignments': changed_assignments,
                 'changed_applicability': changed_applicability,
                 'audit_reasons': sorted({x.get('reason') for x in current.get('changes', [])
                                          if x.get('reason')} - {x.get('reason') for x in base.get('changes', [])
                                                                 if x.get('reason')})},
        'source_refs': [{'month': month, 'version': v, 'section': section}
                        for v in (base_version, current_version) for section in ('results', 'records', 'changes')],
        'limitations': ['版本差异列出发生变化的输入和结果，不把多项修改拆成未经验证的因果金额'],
        'summary_text': f'{month} V{base_version} 至 V{current_version} 有 {len(items)} 个场次结果发生增减或变化。',
    }
