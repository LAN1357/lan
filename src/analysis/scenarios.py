"""Non-persistent, deterministic scenarios fixed to one close version."""

from __future__ import annotations

from copy import deepcopy

from src.analysis import ANALYSIS_RULE_VERSION
from src.analysis.common import money, ratio_value
from src.analysis.reader import AnalysisError, CloseReader
from src.analysis.schemas import (ASSUMPTION_OPERATIONS, ASSUMPTION_SOURCES,
                                  ASSUMPTION_TYPES, DEFAULT_MODE,
                                  MAX_ASSUMPTIONS, SCENARIO_FEE_FIELDS)
from src.engine.session_profit import (AMOUNT_FIELDS, MAX_CENTS, calculate_product_cost,
                                       calculate_session, decimal_text)

FEE_FIELDS = set(SCENARIO_FEE_FIELDS)
FLAG_FIELDS = {'commission': 'talent', 'slot_fee': 'slot', 'ad_spend': 'ads', 'gift': 'gift'}


def _authorization_trace(session_ids: list[str], assumptions: list[dict],
                         assumption_source: str,
                         authorization_scope: dict | None) -> dict:
    if assumption_source not in ASSUMPTION_SOURCES:
        raise AnalysisError('INVALID_ASSUMPTION',
                            'assumption_source 只能是 user_provided 或 agent_proposed')
    if assumption_source == 'user_provided':
        if authorization_scope is not None:
            raise AnalysisError('INVALID_ASSUMPTION',
                                '用户直接给定的假设不应伪装为Agent授权范围')
        return {
            'source': assumption_source,
            'authorization_scope': None,
            'scope_validation': 'not_required',
            'authorization_notice': '假设由用户直接给定；来源标记仅用于追溯。',
        }
    if not isinstance(authorization_scope, dict):
        raise AnalysisError('INVALID_ASSUMPTION',
                            'Agent提出假设时必须提供结构化 authorization_scope')
    allowed_keys = {
        'session_ids', 'assumption_types', 'fee_fields', 'skus', 'operations',
        'max_assumptions', 'fee_delta_abs_max_cents', 'fee_set_min_cents',
        'fee_set_max_cents', 'sku_unit_cost_min', 'sku_unit_cost_max',
    }
    unexpected = sorted(set(authorization_scope) - allowed_keys)
    if unexpected:
        raise AnalysisError('INVALID_ASSUMPTION',
                            f'授权范围包含不支持的字段：{", ".join(unexpected)}')

    def string_list(key, allowed=None):
        values = authorization_scope.get(key)
        if not isinstance(values, list) or not values or any(
                not isinstance(value, str) or not value for value in values):
            raise AnalysisError('INVALID_ASSUMPTION', f'授权范围 {key} 必须是非空字符串列表')
        if len(set(values)) != len(values):
            raise AnalysisError('INVALID_ASSUMPTION', f'授权范围 {key} 不能重复')
        if allowed is not None and any(value not in allowed for value in values):
            raise AnalysisError('INVALID_ASSUMPTION', f'授权范围 {key} 包含不支持的值')
        return values

    allowed_sessions = string_list('session_ids')
    allowed_types = string_list('assumption_types', ASSUMPTION_TYPES)
    operations = string_list('operations', ASSUMPTION_OPERATIONS)
    maximum = authorization_scope.get('max_assumptions')
    if type(maximum) is not int or not 1 <= maximum <= MAX_ASSUMPTIONS:
        raise AnalysisError('INVALID_ASSUMPTION',
                            f'授权范围 max_assumptions 必须是1至{MAX_ASSUMPTIONS}的整数')
    if not set(session_ids) <= set(allowed_sessions) or len(assumptions) > maximum:
        raise AnalysisError('INVALID_ASSUMPTION', '测算目标或假设数量超出用户声明的授权范围')
    if any(assumption.get('type') not in allowed_types or
           assumption.get('operation') not in operations for assumption in assumptions):
        raise AnalysisError('INVALID_ASSUMPTION', '假设类型或操作超出用户声明的授权范围')

    fee_assumptions = [x for x in assumptions if x.get('type') == 'session_fee']
    if fee_assumptions:
        fee_fields = string_list('fee_fields', FEE_FIELDS)
        if any(x.get('field') not in fee_fields for x in fee_assumptions):
            raise AnalysisError('INVALID_ASSUMPTION', '费用科目超出用户声明的授权范围')
        delta_limit = authorization_scope.get('fee_delta_abs_max_cents')
        if any(x.get('operation') == 'delta' for x in fee_assumptions):
            if type(delta_limit) is not int or delta_limit < 0 or any(
                    type(x.get('amount_cents')) is not int or abs(x['amount_cents']) > delta_limit
                    for x in fee_assumptions if x.get('operation') == 'delta'):
                raise AnalysisError('INVALID_ASSUMPTION', '费用增减金额超出用户声明的授权范围')
        set_min = authorization_scope.get('fee_set_min_cents')
        set_max = authorization_scope.get('fee_set_max_cents')
        if any(x.get('operation') == 'set' for x in fee_assumptions):
            if (type(set_min) is not int or type(set_max) is not int or
                    not 0 <= set_min <= set_max or any(
                        type(x.get('amount_cents')) is not int or
                        not set_min <= x['amount_cents'] <= set_max
                        for x in fee_assumptions if x.get('operation') == 'set')):
                raise AnalysisError('INVALID_ASSUMPTION', '费用目标金额超出用户声明的授权范围')

    sku_assumptions = [x for x in assumptions if x.get('type') == 'sku_unit_cost']
    if sku_assumptions:
        skus = string_list('skus')
        if any(x.get('sku') not in skus for x in sku_assumptions):
            raise AnalysisError('INVALID_ASSUMPTION', 'SKU超出用户声明的授权范围')
        lower = authorization_scope.get('sku_unit_cost_min')
        upper = authorization_scope.get('sku_unit_cost_max')
        try:
            lower_value, upper_value = decimal_text(lower), decimal_text(upper)
            values = [decimal_text(x.get('unit_cost')) for x in sku_assumptions]
        except (TypeError,ValueError) as exc:
            raise AnalysisError('INVALID_ASSUMPTION', 'SKU单位成本授权边界必须是十进制文本') from exc
        if lower_value < 0 or lower_value > upper_value or any(
                not lower_value <= value <= upper_value for value in values):
            raise AnalysisError('INVALID_ASSUMPTION', 'SKU单位成本超出用户声明的授权范围')
    return {
        'source': assumption_source,
        'authorization_scope': deepcopy(authorization_scope),
        'scope_validation': 'matched',
        'authorization_notice': (
            '假设由Agent在用户声明范围内提出；系统只校验参数落在该范围内，'
            '来源标记本身不能证明用户授权。'),
    }


def _view(result: dict) -> dict:
    return {
        'amounts': {key: money(result[key]) for key in (
            'sales', 'net_revenue', 'direct_cost', 'indirect_cost',
            'operating_profit', 'final_profit', 'ad_spend', 'product_cost')},
        'ratios': {key: ratio_value(result[key]) for key in (
            'operating_margin', 'final_margin', 'ad_roi')},
    }


def _delta(before: dict, after: dict) -> dict:
    return {key: money(after[key] - before[key]) for key in (
        'net_revenue', 'direct_cost', 'indirect_cost', 'operating_profit',
        'final_profit', 'ad_spend', 'product_cost')}


def _sku_rows(snapshot: dict, session_id: str, sku: str) -> tuple[list[dict], int]:
    records = snapshot.get('records', [])
    by_id = {str(row['id']): row for row in records}
    fulfillment = {row['business_key']: row for row in records if row['kind'] == 'fulfillment'}
    matched, all_cost = [], 0
    for record_id, decision in snapshot.get('assignments', {}).items():
        order = by_id.get(str(record_id))
        if not order or order.get('kind') != 'orders' or session_id not in decision.get('targets', {}):
            continue
        cost = fulfillment.get(order['business_key'])
        if not cost:
            raise AnalysisError('INSUFFICIENT_SCENARIO_BASIS', f'{order["business_key"]} 缺少关账成本明细')
        data = cost.get('data', {})
        if data.get('unit_cost') is None:
            raise AnalysisError('INSUFFICIENT_SCENARIO_BASIS', f'{order["business_key"]} 缺少原单位成本')
        try:
            units = data['shipped'] - data['restored'] - data['damaged']
            old_cost = calculate_product_cost(data['unit_cost'], units)
        except (KeyError, TypeError, ValueError) as exc:
            raise AnalysisError('INSUFFICIENT_SCENARIO_BASIS', f'{order["business_key"]} 成本数量或单价无法复核') from exc
        all_cost += old_cost
        if order.get('data', {}).get('sku') == sku:
            matched.append({'record_id': order['id'], 'business_key': order['business_key'],
                            'unit_cost': data['unit_cost'], 'retained_units': units,
                            'old_cost_cents': old_cost})
    return matched, all_cost


def simulate_scenario(reader: CloseReader, close_ref: dict, session_ids: list[str],
                      assumptions: list[dict], *, mode: str = DEFAULT_MODE,
                      assumption_source: str = 'user_provided',
                      authorization_scope: dict | None = None) -> dict:
    if not isinstance(session_ids, list) or not session_ids or len(session_ids) > 100 or any(
            not isinstance(x, str) or not x for x in session_ids):
        raise AnalysisError('INVALID_SCOPE', 'session_ids 必须包含 1 至 100 个明确场次')
    if len(set(session_ids)) != len(session_ids):
        raise AnalysisError('AMBIGUOUS_TARGET', '场次不能重复')
    if not isinstance(assumptions, list) or not 1 <= len(assumptions) <= MAX_ASSUMPTIONS or any(
            not isinstance(x, dict) for x in assumptions):
        raise AnalysisError('INVALID_ASSUMPTION', f'一次必须提供 1 至 {MAX_ASSUMPTIONS} 项类型化假设')
    trace = _authorization_trace(session_ids, assumptions, assumption_source,
                                 authorization_scope)
    snapshot = reader.load_ref(close_ref, mode=mode)
    missing = sorted(set(session_ids) - set(snapshot['sessions']))
    if missing:
        raise AnalysisError('SESSION_NOT_FOUND', f'关账版本内找不到场次：{", ".join(missing)}')
    baseline, scenario = {}, {}
    for sid in session_ids:
        stored = snapshot['results'].get(sid)
        if not isinstance(stored, dict):
            raise AnalysisError('INSUFFICIENT_SCENARIO_BASIS', f'{sid} 缺少基准核算结果')
        try:
            rebuilt = calculate_session({key: stored[key] for key in AMOUNT_FIELDS})
        except (KeyError, ValueError) as exc:
            raise AnalysisError('INSUFFICIENT_SCENARIO_BASIS', f'{sid} 基准科目无法重算') from exc
        if rebuilt != stored:
            raise AnalysisError('INSUFFICIENT_SCENARIO_BASIS', f'{sid} 基准重算与关账结果不一致')
        baseline[sid] = rebuilt
        scenario[sid] = {key: rebuilt[key] for key in AMOUNT_FIELDS}
    seen = set()
    applied = []
    for assumption in assumptions:
        kind, sid = assumption.get('type'), assumption.get('session_id')
        if sid not in scenario:
            raise AnalysisError('AMBIGUOUS_TARGET', f'假设目标场次 {sid} 不在明确选择范围内')
        if kind == 'session_fee':
            field, operation = assumption.get('field'), assumption.get('operation')
            if field not in FEE_FIELDS:
                raise AnalysisError('UNSUPPORTED_ASSUMPTION', f'不支持测算费用字段 {field}')
            if operation not in ('set', 'delta') or type(assumption.get('amount_cents')) is not int:
                raise AnalysisError('INVALID_ASSUMPTION', '费用假设需要 set/delta 和整数分 amount_cents')
            target = (kind, sid, field)
            if target in seen:
                raise AnalysisError('INVALID_ASSUMPTION', f'{sid} 的 {field} 存在重复假设')
            seen.add(target)
            flag = FLAG_FIELDS.get(field)
            if flag and snapshot.get('applicability', {}).get(sid, {}).get(flag) is False:
                raise AnalysisError('INVALID_ASSUMPTION', f'{sid} 的 {field} 在关账版中明确不适用')
            old = scenario[sid][field]
            new = assumption['amount_cents'] if operation == 'set' else old + assumption['amount_cents']
            if not 0 <= new <= MAX_CENTS:
                raise AnalysisError('INVALID_ASSUMPTION', f'{sid} 的 {field} 假设后金额必须为有效非负整数分')
            scenario[sid][field] = new
            applied.append({'type': kind, 'session_id': sid, 'field': field,
                            'operation': operation, 'old_amount': money(old),
                            'new_amount': money(new), 'delta': money(new - old)})
        elif kind == 'sku_unit_cost':
            sku, operation, unit_cost = assumption.get('sku'), assumption.get('operation'), assumption.get('unit_cost')
            if not isinstance(sku, str) or not sku or operation != 'set' or not isinstance(unit_cost, str):
                raise AnalysisError('INVALID_ASSUMPTION', 'SKU假设需要明确 sku、operation=set 和十进制文本 unit_cost')
            target = (kind, sid, sku)
            if target in seen:
                raise AnalysisError('INVALID_ASSUMPTION', f'{sid} 的 SKU {sku} 存在重复假设')
            seen.add(target)
            try:
                if decimal_text(unit_cost) < 0:
                    raise ValueError
            except ValueError as exc:
                raise AnalysisError('INVALID_ASSUMPTION', 'SKU目标单位成本必须是有效非负十进制文本') from exc
            rows, all_cost = _sku_rows(snapshot, sid, sku)
            if all_cost != baseline[sid]['product_cost']:
                raise AnalysisError('INSUFFICIENT_SCENARIO_BASIS', f'{sid} 关账商品成本不能与明细逐笔对齐')
            if not rows:
                raise AnalysisError('AMBIGUOUS_TARGET', f'{sid} 中找不到 SKU {sku} 的已归属成本记录')
            try:
                new_cost = sum(calculate_product_cost(unit_cost, row['retained_units']) for row in rows)
            except ValueError as exc:
                raise AnalysisError('INVALID_ASSUMPTION', 'SKU假设后的逐笔商品成本超出支持范围') from exc
            old_cost = sum(row['old_cost_cents'] for row in rows)
            scenario[sid]['product_cost'] += new_cost - old_cost
            applied.append({'type': kind, 'session_id': sid, 'sku': sku, 'operation': 'set',
                            'new_unit_cost': unit_cost, 'affected_records': rows,
                            'old_product_cost': money(old_cost), 'new_product_cost': money(new_cost),
                            'delta': money(new_cost - old_cost)})
        else:
            raise AnalysisError('UNSUPPORTED_ASSUMPTION', f'不支持假设类型 {kind}')
    calculated = {sid: calculate_session(values) for sid, values in scenario.items()}
    total_base = calculate_session({k: sum(baseline[sid][k] for sid in session_ids) for k in AMOUNT_FIELDS})
    total_scenario = calculate_session({k: sum(calculated[sid][k] for sid in session_ids) for k in AMOUNT_FIELDS})
    return {
        'status': 'ok', 'result_type': 'scenario', 'as_of': reader._as_of(),
        'baseline_close_ref': snapshot['_close_ref'], 'assumptions': deepcopy(assumptions),
        'assumption_trace': trace,
        'scope': {'session_ids': session_ids, 'session_count': len(session_ids), 'mode': mode},
        'accounting_rule_version': snapshot['rule_version'], 'analysis_rule_version': ANALYSIS_RULE_VERSION,
        'data': {'applied_assumptions': applied,
                 'sessions': {sid: {'baseline': _view(baseline[sid]), 'scenario': _view(calculated[sid]),
                                    'delta': _delta(baseline[sid], calculated[sid])} for sid in session_ids},
                 'total': {'baseline': _view(total_base), 'scenario': _view(total_scenario),
                           'delta': _delta(total_base, total_scenario)}},
        'unchanged_conditions': ['结算销售额与销量不变', '退回、损耗及计成本数量不变',
                                 '未指定费用与损益税费不变', '来源归属和分配决定不变',
                                 '不推算投流变化对销售的影响'],
        'source_refs': [{'month': snapshot['month'], 'version': snapshot['version'],
                         'section': 'results', 'session_id': sid} for sid in session_ids],
        'persisted': False,
        'limitations': ['假设测算，非实际结算结果；不会修改工作台数据或生成新关账版本。'],
        'summary_text': f'已基于 {snapshot["month"]} V{snapshot["version"]} 对 {len(session_ids)} 个场次完成非持久化假设测算。',
    }
