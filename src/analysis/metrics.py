"""Deterministic performance queries over immutable close snapshots."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from src.analysis import ANALYSIS_RULE_VERSION
from src.analysis.common import money, paging, ratio_value
from src.analysis.reader import AnalysisError, CloseReader
from src.analysis.schemas import (DEFAULT_GROUP_BY, DEFAULT_LIMIT, DEFAULT_METRICS,
                                  DEFAULT_OFFSET, DEFAULT_PROFIT_METRIC,
                                  DEFAULT_SORT_BY, DEFAULT_SORT_ORDER,
                                  GROUP_BY_VALUES, SORT_ORDERS)
from src.engine.session_profit import AMOUNT_FIELDS, calculate_session

MONEY_METRICS = set(AMOUNT_FIELDS) | {
    'net_revenue', 'direct_cost', 'indirect_cost', 'operating_profit', 'final_profit'
}
RATIO_METRICS = {'operating_margin', 'final_margin', 'ad_roi'}
COUNT_METRICS = {'session_count'}
SUPPORTED_METRICS = MONEY_METRICS | RATIO_METRICS | COUNT_METRICS


def _date(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AnalysisError('INVALID_SCOPE', f'{name}必须是 YYYY-MM-DD')
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise AnalysisError('INVALID_SCOPE', f'{name}必须是有效的 YYYY-MM-DD') from exc
    return value


def validate_metrics(metrics: list[str] | None) -> list[str]:
    result = list(DEFAULT_METRICS if metrics is None else metrics)
    if not result or len(result) > 20 or any(not isinstance(x, str) for x in result):
        raise AnalysisError('INVALID_SCOPE', '指标必须为 1 至 20 个白名单指标')
    unknown = sorted(set(result) - SUPPORTED_METRICS)
    if unknown:
        raise AnalysisError('UNSUPPORTED_METRIC', f'不支持的指标：{", ".join(unknown)}',
                            '调用 workbench_get_metric_rules 查看指标口径')
    if len(set(result)) != len(result):
        raise AnalysisError('INVALID_SCOPE', '指标不能重复')
    return result


def _scope(scope: dict) -> tuple[list[dict], str, dict]:
    if not isinstance(scope, dict):
        raise AnalysisError('INVALID_SCOPE', 'scope 必须是对象')
    refs = scope.get('close_refs')
    mode = scope.get('mode', 'current')
    session_ids = scope.get('session_ids')
    talents = scope.get('talents')
    if session_ids is not None and (not isinstance(session_ids, list) or
                                    any(not isinstance(x, str) or not x for x in session_ids)):
        raise AnalysisError('INVALID_SCOPE', 'session_ids 必须是非空字符串列表')
    if talents is not None and (not isinstance(talents, list) or
                                any(not isinstance(x, str) or not x for x in talents)):
        raise AnalysisError('INVALID_SCOPE', 'talents 必须是达人原始名称列表')
    start = _date(scope.get('start_date'), 'start_date')
    end = _date(scope.get('end_date'), 'end_date')
    if start and end and start > end:
        raise AnalysisError('INVALID_SCOPE', 'start_date 不能晚于 end_date')
    return refs, mode, {
        'session_ids': list(dict.fromkeys(session_ids or [])),
        'talents': list(dict.fromkeys(talents or [])),
        'start_date': start,
        'end_date': end,
    }


def select_sessions(reader: CloseReader, scope: dict, *, conn=None) -> tuple[list[tuple[dict, str, dict, dict]], dict, str]:
    refs, mode, filters = _scope(scope)
    snapshots, as_of = reader.load_refs(refs, mode=mode, conn=conn)
    requested = set(filters['session_ids'])
    known = {sid for snapshot in snapshots for sid in snapshot['sessions']}
    missing = sorted(requested - known)
    if missing:
        raise AnalysisError('SESSION_NOT_FOUND', f'关账范围内找不到场次：{", ".join(missing)}')
    selected = []
    for snapshot in snapshots:
        for sid, session in snapshot['sessions'].items():
            start_day = str(session.get('start', ''))[:10]
            if requested and sid not in requested:
                continue
            if filters['talents'] and session.get('talent') not in filters['talents']:
                continue
            if filters['start_date'] and start_day < filters['start_date']:
                continue
            if filters['end_date'] and start_day > filters['end_date']:
                continue
            result = snapshot['results'].get(sid)
            if not isinstance(result, dict):
                raise AnalysisError('SNAPSHOT_INVALID', f'{snapshot["month"]} V{snapshot["version"]} 缺少 {sid} 核算结果')
            selected.append((snapshot, sid, session, result))
    if not selected:
        raise AnalysisError('SESSION_NOT_FOUND', '筛选后没有已关账场次', '核对场次、达人原始名称或开播日期范围')
    close_refs = [{
        'month': s['month'], 'version': s['version'], 'closed_at': s['closed_at'],
        'is_current': s['_close_ref']['is_current'],
    } for s in snapshots]
    dates = sorted(str(row[2].get('start', ''))[:10] for row in selected)
    talents = sorted({row[2].get('talent') or '（未命名达人）' for row in selected})
    applicability = {name: {'true': 0, 'false': 0, 'unknown': 0}
                     for name in ('talent', 'ads', 'slot', 'gift')}
    for snapshot, sid, _session, _result in selected:
        flags = snapshot.get('applicability', {}).get(sid, {})
        for name in applicability:
            state = 'true' if flags.get(name) is True else ('false' if flags.get(name) is False else 'unknown')
            applicability[name][state] += 1
    scope_out = {
        'mode': mode,
        'close_refs': close_refs,
        **filters,
        'time_basis': 'session_start',
        'timezone': 'Asia/Shanghai',
        'session_count': len(selected),
        'actual_start_date': dates[0],
        'actual_end_date': dates[-1],
        'talent_composition': talents,
        'applicability_summary': applicability,
    }
    return selected, scope_out, as_of


def aggregate(rows: list[tuple[dict, str, dict, dict]]) -> dict:
    amounts = {key: sum(row[3][key] for row in rows) for key in AMOUNT_FIELDS}
    try:
        return calculate_session(amounts) | {'session_count': len(rows)}
    except ValueError as exc:
        raise AnalysisError('INVALID_SCOPE', '筛选范围金额合计超出支持范围') from exc


def present_metric(key: str, value) -> dict | int:
    if key in MONEY_METRICS:
        return money(value)
    if key in RATIO_METRICS:
        return ratio_value(value)
    return value


def source_refs(rows, metrics: list[str]) -> list[dict]:
    return [
        {'month': snap['month'], 'version': snap['version'], 'section': 'results',
         'session_id': sid, 'metric': metric}
        for snap, sid, _session, _result in rows for metric in metrics
        if metric != 'session_count'
    ]


def _group(rows, group_by: str):
    groups = defaultdict(list)
    for row in rows:
        snap, sid, session, _result = row
        if group_by == 'month':
            key, label = snap['month'], snap['month']
        elif group_by == 'session':
            key, label = sid, sid
        elif group_by == 'talent':
            key = session.get('talent') or '（未命名达人）'
            label = key
        else:
            raise AnalysisError('INVALID_SCOPE', f'group_by 只能是 {"、".join(GROUP_BY_VALUES)}')
        groups[(str(key), str(label))].append(row)
    return groups


def query_performance(reader: CloseReader, scope: dict, *, group_by: str = DEFAULT_GROUP_BY,
                      metrics: list[str] | None = None, sort_by: str = DEFAULT_SORT_BY,
                      sort_order: str = DEFAULT_SORT_ORDER, offset: int = DEFAULT_OFFSET,
                      limit: int = DEFAULT_LIMIT) -> dict:
    metric_keys = validate_metrics(metrics)
    if sort_by not in SUPPORTED_METRICS:
        raise AnalysisError('UNSUPPORTED_METRIC', f'不支持按 {sort_by} 排序')
    if sort_order not in SORT_ORDERS:
        raise AnalysisError('INVALID_SCOPE', 'sort_order 只能是 asc 或 desc')
    paging(0, offset, limit)
    rows, scope_out, as_of = select_sessions(reader, scope)
    items = []
    for (key, label), group_rows in _group(rows, group_by).items():
        values = aggregate(group_rows)
        items.append({'key': key, 'label': label, '_raw': values,
                      'session_count': len(group_rows),
                      'metrics': {m: present_metric(m, values[m]) for m in metric_keys},
                      'source_refs': source_refs(group_rows, metric_keys)})

    def sort_key(item):
        value = item['_raw'][sort_by]
        missing = value is None
        numeric = Decimal(value) if isinstance(value, str) else Decimal(value or 0)
        return (missing, -numeric if sort_order == 'desc' else numeric, item['key'])

    items.sort(key=sort_key)
    previous = object(); rank = 0
    for index, item in enumerate(items, 1):
        value = item['_raw'][sort_by]
        if value is None:
            item['rank'] = None
        else:
            normalized = Decimal(value) if isinstance(value, str) else value
            if normalized != previous:
                rank = index
                previous = normalized
            item['rank'] = rank
        del item['_raw']
    page_info = paging(len(items), offset, limit)
    displayed = items[offset:offset + limit]
    total = aggregate(rows)
    return {
        'status': 'ok', 'as_of': as_of, 'scope': scope_out,
        'accounting_rule_version': rows[0][0]['rule_version'],
        'analysis_rule_version': ANALYSIS_RULE_VERSION,
        'data': {
            'group_by': group_by,
            'totals': {m: present_metric(m, total[m]) for m in metric_keys},
            'groups': displayed,
            'paging': page_info,
            'sort': {'metric': sort_by, 'order': sort_order, 'ranking_basis': 'full_filtered_scope'},
        },
        'source_refs': source_refs(rows, metric_keys),
        'limitations': ['汇总比率由完整筛选范围的金额重新计算，不是场次比率平均值'],
        'summary_text': f'已按{group_by}汇总 {len(rows)} 个已关账场次，共 {len(items)} 组。',
    }


PROFIT_SIGN = {
    'sales': 1, 'platform_fee': -1, 'other_deductions': -1,
    'commission': -1, 'slot_fee': -1, 'talent_adjustment': -1, 'ad_spend': -1,
    'product_cost': -1, 'gift': -1, 'insurance': -1, 'logistics': -1, 'loss': -1,
    'warehouse': -1, 'labor': -1, 'management': -1, 'tax': -1, 'adjustment': 1,
}


def compare_performance(reader: CloseReader, base_scope: dict, current_scope: dict,
                        *, profit_metric: str = DEFAULT_PROFIT_METRIC) -> dict:
    if profit_metric not in ('operating_profit', 'final_profit'):
        raise AnalysisError('UNSUPPORTED_METRIC', 'profit_metric 只能是 operating_profit 或 final_profit')
    with reader.transaction() as conn:
        base_rows, base_out, base_as_of = select_sessions(reader, base_scope, conn=conn)
        current_rows, current_out, current_as_of = select_sessions(reader, current_scope, conn=conn)
    rules = {row[0]['rule_version'] for row in base_rows + current_rows}
    if len(rules) != 1:
        raise AnalysisError('INCOMPATIBLE_RULES', '基期与当期使用不同核算规则，不能比较')
    base, current = aggregate(base_rows), aggregate(current_rows)
    keys = list(AMOUNT_FIELDS) + ['net_revenue', 'direct_cost', 'indirect_cost',
                                  'operating_profit', 'final_profit']
    values = {}
    for key in keys:
        delta = current[key] - base[key]
        growth = None if base[key] <= 0 else str((Decimal(delta) / Decimal(base[key])).quantize(
            Decimal('0.000001'), rounding=ROUND_HALF_UP))
        values[key] = {'base': money(base[key]), 'current': money(current[key]),
                       'delta': money(delta), 'growth': ratio_value(growth,
                       reason='基期金额不大于0，不计算增长率')}
    margin_keys = ('operating_margin', 'final_margin', 'ad_roi')
    ratios = {}
    for key in margin_keys:
        bv, cv = base[key], current[key]
        pp = None if bv is None or cv is None else str((Decimal(cv) - Decimal(bv)).quantize(
            Decimal('0.000001'), rounding=ROUND_HALF_UP))
        ratios[key] = {'base': ratio_value(bv), 'current': ratio_value(cv),
                       'percentage_point_delta': ratio_value(pp, reason='任一侧分母不大于0')}
    bridge_fields = list(PROFIT_SIGN)
    if profit_metric == 'operating_profit':
        bridge_fields = [k for k in bridge_fields if k not in ('warehouse','labor','management','tax','adjustment')]
    bridge = [{'metric': key, 'amount_delta': money(current[key] - base[key]),
               'profit_contribution': money((current[key] - base[key]) * PROFIT_SIGN[key])}
              for key in bridge_fields]
    bridge.sort(key=lambda x: (-abs(x['profit_contribution']['cents']), x['metric']))
    expected = current[profit_metric] - base[profit_metric]
    if sum(x['profit_contribution']['cents'] for x in bridge) != expected:
        raise AnalysisError('SNAPSHOT_INVALID', '科目差异桥与利润差额不守恒')
    return {
        'status': 'ok', 'as_of': max(base_as_of, current_as_of),
        'base_scope': base_out, 'current_scope': current_out,
        'accounting_rule_version': next(iter(rules)),
        'analysis_rule_version': ANALYSIS_RULE_VERSION,
        'data': {'amounts': values, 'ratios': ratios, 'profit_bridge': bridge,
                 'profit_metric': profit_metric, 'session_count': {
                     'base': len(base_rows), 'current': len(current_rows),
                     'delta': len(current_rows) - len(base_rows)}},
        'source_refs': source_refs(base_rows + current_rows, [profit_metric]),
        'limitations': ['科目桥解释金额变化，不证明经营因果；场次和达人构成变化需人工结合业务判断'],
        'summary_text': f'{profit_metric} 较基期变化 {money(expected)["yuan_text"]} 元。',
    }
