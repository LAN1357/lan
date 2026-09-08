"""Human-readable metric definitions for the deterministic accounting rules."""

from __future__ import annotations

from src.analysis import ANALYSIS_RULE_VERSION
from src.analysis.reader import AnalysisError
from src.engine.session_profit import AMOUNT_FIELDS
from src.workbench.closing import RULE_VERSION


_NAMES = {
    'sales': '确认结算销售额', 'platform_fee': '平台费', 'other_deductions': '其他未扣款',
    'commission': '最终佣金', 'slot_fee': '坑位费', 'talent_adjustment': '达人调整',
    'ad_spend': '投流费', 'product_cost': '商品销售成本', 'gift': '赠品费',
    'insurance': '运费险', 'logistics': '物流费', 'loss': '退货损耗',
    'warehouse': '仓储费', 'labor': '人工费', 'management': '管理费',
    'tax': '损益税费', 'adjustment': '其他结算调整', 'net_revenue': '净经营收入',
    'direct_cost': '直接成本', 'indirect_cost': '间接费用及损益税费',
    'operating_profit': '经营复盘利润', 'final_profit': '最终结算利润',
    'operating_margin': '经营复盘利润率', 'final_margin': '最终结算利润率',
    'ad_roi': '投流比率', 'session_count': '场次数',
}

_FORMULAS = {key: f'关账科目 {key} 的整数分金额之和' for key in AMOUNT_FIELDS}
_FORMULAS.update({
    'net_revenue': 'sales - platform_fee - other_deductions',
    'direct_cost': 'commission + slot_fee + talent_adjustment + ad_spend + product_cost + gift + insurance + logistics + loss',
    'indirect_cost': 'warehouse + labor + management + tax',
    'operating_profit': 'net_revenue - direct_cost',
    'final_profit': 'operating_profit - indirect_cost + adjustment',
    'operating_margin': 'operating_profit / net_revenue',
    'final_margin': 'final_profit / net_revenue',
    'ad_roi': 'sales / ad_spend',
    'session_count': '筛选范围内不重复场次的数量',
})


def get_metric_rules(metric_keys: list[str] | None = None, *,
                     accounting_rule_version: str = RULE_VERSION,
                     analysis_rule_version: str = ANALYSIS_RULE_VERSION) -> dict:
    if accounting_rule_version != RULE_VERSION:
        raise AnalysisError('INCOMPATIBLE_RULES', f'不支持核算规则 {accounting_rule_version}')
    if analysis_rule_version != ANALYSIS_RULE_VERSION:
        raise AnalysisError('INCOMPATIBLE_RULES', f'不支持分析规则 {analysis_rule_version}')
    keys = list(_NAMES) if metric_keys is None else metric_keys
    if not isinstance(keys, list) or not keys or any(not isinstance(x, str) for x in keys):
        raise AnalysisError('INVALID_SCOPE', 'metric_keys 必须是非空指标列表')
    unknown = sorted(set(keys) - set(_NAMES))
    if unknown:
        raise AnalysisError('UNSUPPORTED_METRIC', f'不支持的指标：{", ".join(unknown)}')
    rules = []
    for key in keys:
        ratio = key in ('operating_margin', 'final_margin', 'ad_roi')
        rules.append({
            'key': key, 'name': _NAMES[key], 'formula': _FORMULAS[key],
            'unit': 'ratio' if ratio else ('count' if key == 'session_count' else 'CNY cents'),
            'source': 'immutable_close_snapshot',
            'scope': '筛选后的完整已关账场次范围',
            'denominator': ({'operating_margin': 'net_revenue', 'final_margin': 'net_revenue',
                             'ad_roi': 'ad_spend'}.get(key)),
            'missing_handling': '分母不大于0时比率为 null；金额未知不会补0',
            'comparability': '仅可合并或比较相同核算规则版本；开播日期为经营时间口径',
        })
    return {
        'status': 'ok', 'accounting_rule_version': accounting_rule_version,
        'analysis_rule_version': analysis_rule_version, 'data': rules,
        'summary_text': f'返回 {len(rules)} 个确定性指标口径。',
    }

