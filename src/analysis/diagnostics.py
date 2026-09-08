"""Fixed, factual diagnostics over closed performance results."""

from __future__ import annotations

from src.analysis import ANALYSIS_RULE_VERSION
from src.analysis.common import money, paging
from src.analysis.metrics import PROFIT_SIGN, aggregate, select_sessions, source_refs
from src.analysis.reader import AnalysisError, CloseReader
from src.analysis.schemas import (DEFAULT_LIMIT, DEFAULT_OFFSET,
                                  DIAGNOSTIC_RULES)

RULES = DIAGNOSTIC_RULES
COMPONENTS = ('sales', 'platform_fee', 'other_deductions', 'commission', 'slot_fee',
              'talent_adjustment', 'ad_spend', 'product_cost', 'gift', 'insurance',
              'logistics', 'loss', 'warehouse', 'labor', 'management', 'tax', 'adjustment')


def diagnose_performance(reader: CloseReader, scope: dict, *, base_scope: dict | None = None,
                         rules: list[str] | None = None, offset: int = DEFAULT_OFFSET,
                         limit: int = DEFAULT_LIMIT) -> dict:
    selected_rules = list(RULES if rules is None else rules)
    if not selected_rules or len(set(selected_rules)) != len(selected_rules):
        raise AnalysisError('INVALID_SCOPE', '诊断规则必须为不重复的非空列表')
    unknown = sorted(set(selected_rules) - set(RULES))
    if unknown:
        raise AnalysisError('INVALID_SCOPE', f'不支持的诊断规则：{", ".join(unknown)}')
    paging(0, offset, limit)
    if base_scope is None:
        rows, scope_out, as_of = select_sessions(reader, scope)
        base_rows = base_out = None
    else:
        with reader.transaction() as conn:
            rows, scope_out, as_of = select_sessions(reader, scope, conn=conn)
            base_rows, base_out, base_as_of = select_sessions(reader, base_scope, conn=conn)
            as_of = max(as_of, base_as_of)
    items = []
    for snap, sid, session, result in rows:
        ref = [{'month': snap['month'], 'version': snap['version'], 'section': 'results',
                'session_id': sid}]
        amounts = {'operating_profit': money(result['operating_profit']),
                   'final_profit': money(result['final_profit'])}
        if 'final_loss' in selected_rules and result['final_profit'] < 0:
            costs = sorted(({'metric': k, 'amount': money(result[k])} for k in COMPONENTS
                            if k not in ('sales', 'adjustment')), key=lambda x: (-abs(x['amount']['cents']), x['metric']))[:5]
            items.append({'rule_id': 'final_loss', 'session_id': sid, 'session': session,
                          'facts': amounts | {'largest_cost_components': costs},
                          'rule_explanation': '最终结算利润小于0。',
                          'checks_for_bp': ['结合分配依据复核金额较大的费用，并判断是否需要业务讨论；这不表示数据必然有错。'],
                          'source_refs': ref})
        if ('operating_profit_final_loss' in selected_rules and result['operating_profit'] > 0
                and result['final_profit'] < 0):
            items.append({'rule_id': 'operating_profit_final_loss', 'session_id': sid,
                          'session': session,
                          'facts': amounts | {'indirect_cost': money(result['indirect_cost']),
                                              'adjustment': money(result['adjustment'])},
                          'rule_explanation': '经营复盘利润大于0，但扣除间接费用并计入结算调整后，最终结算利润小于0。',
                          'checks_for_bp': ['复核间接费用、损益税费和结算调整依据；不预设其中某项为业务原因。'],
                          'source_refs': ref})
        if 'major_components' in selected_rules:
            components = [{'metric': k, 'amount': money(result[k]),
                           'profit_direction': 'favorable' if result[k] * PROFIT_SIGN[k] >= 0 else 'unfavorable'}
                          for k in COMPONENTS]
            components.sort(key=lambda x: (-abs(x['amount']['cents']), x['metric']))
            items.append({'rule_id': 'major_components', 'session_id': sid, 'session': session,
                          'facts': amounts | {'components': components[:5]},
                          'rule_explanation': '按已核算科目绝对金额排序；金额大小不是经营重要性评分。',
                          'checks_for_bp': [], 'source_refs': ref})
    if 'period_change' in selected_rules and base_scope is not None:
        if len({r[0]['rule_version'] for r in rows + base_rows}) != 1:
            raise AnalysisError('INCOMPATIBLE_RULES', '基期与当期核算规则不同')
        base, current = aggregate(base_rows), aggregate(rows)
        bridge = [{'metric': key, 'amount_delta': money(current[key] - base[key]),
                   'profit_contribution': money((current[key] - base[key]) * PROFIT_SIGN[key])}
                  for key in COMPONENTS]
        bridge.sort(key=lambda x: (-abs(x['profit_contribution']['cents']), x['metric']))
        expected = current['final_profit'] - base['final_profit']
        if sum(x['profit_contribution']['cents'] for x in bridge) != expected:
            raise AnalysisError('SNAPSHOT_INVALID', '期间主要变化科目桥与最终利润差额不守恒')
        items.append({'rule_id': 'period_change', 'session_id': None,
                      'facts': {'base_scope': base_out,
                                'final_profit_delta': money(expected),
                                'largest_profit_contributions': bridge[:5]},
                      'rule_explanation': '按科目对最终利润变化的金额贡献排序，不推断经营因果。',
                      'checks_for_bp': ['比较两侧场次数、达人构成及公共费用分配范围是否可比。'],
                      'source_refs': source_refs(base_rows + rows, ['final_profit'])})
    result_by_session = {sid: result for _snap, sid, _session, result in rows}
    order = {rule: index for index, rule in enumerate(RULES)}
    attention_rules = {'final_loss', 'operating_profit_final_loss'}
    items.sort(key=lambda x: (
        order[x['rule_id']],
        result_by_session[x['session_id']]['final_profit']
        if x['rule_id'] in attention_rules and x['session_id'] else 0,
        x['session_id'] or '',
    ))
    loss_ids = sorted(
        (sid for sid, result in result_by_session.items() if result['final_profit'] < 0),
        key=lambda sid: (result_by_session[sid]['final_profit'], sid),
    ) if 'final_loss' in selected_rules else []
    direction_ids = sorted(
        (sid for sid, result in result_by_session.items()
         if result['operating_profit'] > 0 and result['final_profit'] < 0),
        key=lambda sid: (result_by_session[sid]['final_profit'], sid),
    ) if 'operating_profit_final_loss' in selected_rules else []
    both_attention_rules_ran = all(
        rule in selected_rules for rule in ('final_loss', 'operating_profit_final_loss'))
    attention_ids = (sorted(
        set(loss_ids) | set(direction_ids),
        key=lambda sid: (result_by_session[sid]['final_profit'], sid),
    ) if both_attention_rules_ran else None)

    def rule_summary(rule, ids, **extra):
        ran = rule in selected_rules
        result = {'status': 'executed' if ran else 'not_run',
                  'count': len(ids) if ran else None}
        if not ran:
            result['reason'] = '本次请求未选择该规则'
        result.update(extra)
        return result

    summary = {
        'final_loss': rule_summary('final_loss', loss_ids),
        'operating_profit_final_loss': rule_summary(
            'operating_profit_final_loss', direction_ids, subset_of='final_loss'),
        'attention_status': 'complete' if both_attention_rules_ran else 'incomplete',
        'attention_session_count': len(attention_ids) if attention_ids is not None else None,
        'major_components_counted_as_attention': False,
    }
    page_info = paging(len(items), offset, limit)
    shown = items[offset:offset + limit]
    return {
        'status': 'ok', 'as_of': as_of, 'scope': scope_out,
        'accounting_rule_version': rows[0][0]['rule_version'],
        'analysis_rule_version': ANALYSIS_RULE_VERSION,
        'data': {'items': shown, 'paging': page_info, 'rules_run': selected_rules,
                 'summary': summary},
        'source_refs': [ref for item in shown for ref in item['source_refs']],
        'limitations': (['规则命中表示需要关注的事实，不代表数据错误或自动经营结论；不生成评分。'] +
                        (['未提供明确基期，已跳过期间主要变化，不推测环比。']
                         if 'period_change' in selected_rules and base_scope is None else [])),
        'summary_text': f'在 {len(rows)} 个场次中返回 {len(items)} 个固定规则命中或构成项。',
    }
