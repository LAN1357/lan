"""M1 deterministic accounting. Monetary inputs are text or integer cents."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

MAX_CENTS = 999_999_999_999_99
AMOUNT_FIELDS = (
    'sales', 'platform_fee', 'other_deductions', 'commission', 'slot_fee',
    'talent_adjustment', 'ad_spend', 'product_cost', 'gift', 'insurance',
    'logistics', 'loss', 'warehouse', 'labor', 'management', 'tax', 'adjustment',
)


def integer(value, name='整数', *, nonnegative=False):
    if type(value) is not int or (nonnegative and value < 0):
        raise ValueError(f'{name}必须是{"非负" if nonnegative else ""}整数')
    return value


def decimal_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 64:
        raise ValueError('金额/单价必须是十进制文本')
    try:
        value = Decimal(text.strip())
    except InvalidOperation as exc:
        raise ValueError('非法金额/单价') from exc
    if not value.is_finite() or value.copy_abs() > Decimal(MAX_CENTS) / 100:
        raise ValueError('金额/单价非有限数或超范围')
    return value


def money_to_cents(text: str) -> int:
    value = decimal_text(text)
    with localcontext() as ctx:
        ctx.prec = 80
        cents = int((value * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    if abs(cents) > MAX_CENTS:
        raise ValueError('金额超范围')
    return cents


def calculate_product_cost(unit_cost: str, retained_units: int) -> int:
    units = integer(retained_units, '计成本数量', nonnegative=True)
    value = decimal_text(unit_cost)
    if value < 0:
        raise ValueError('单位成本不能为负')
    with localcontext() as ctx:
        ctx.prec = 80
        return money_to_cents(str(value * units))


def allocate_cents(total: int, weights: dict[str, int]) -> dict[str, int]:
    integer(total, '来源金额')
    if not weights or any(not isinstance(k, str) or not k for k in weights):
        raise ValueError('必须指定目标场次和权重')
    for value in weights.values():
        integer(value, '权重', nonnegative=True)
    denominator = sum(weights.values())
    if denominator == 0:
        if total:
            raise ValueError('非零费用不能使用零分母')
        return {k: 0 for k in sorted(weights)}
    magnitude = abs(total)
    result = {k: magnitude * weights[k] // denominator for k in sorted(weights)}
    remainder = magnitude - sum(result.values())
    priority = sorted(weights, key=lambda k: (-(magnitude * weights[k] % denominator), k))
    for key in priority[:remainder]:
        result[key] += 1
    return {k: v if total >= 0 else -v for k, v in result.items()}


def ratio(numerator, denominator):
    if denominator <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        return str((Decimal(numerator) / Decimal(denominator)).quantize(
            Decimal('0.000001'), rounding=ROUND_HALF_UP))


def calculate_session(amounts: dict[str, int]) -> dict:
    if set(amounts) != set(AMOUNT_FIELDS):
        raise ValueError('核算科目缺失或多余')
    for key, value in amounts.items():
        integer(value, key)
        if abs(value) > MAX_CENTS:
            raise ValueError('金额超范围')
    result = dict(amounts)
    result['net_revenue'] = amounts['sales'] - amounts['platform_fee'] - amounts['other_deductions']
    result['direct_cost'] = sum(amounts[k] for k in (
        'commission', 'slot_fee', 'talent_adjustment', 'ad_spend',
        'product_cost', 'gift', 'insurance', 'logistics', 'loss'))
    result['indirect_cost'] = sum(amounts[k] for k in ('warehouse', 'labor', 'management', 'tax'))
    result['operating_profit'] = result['net_revenue'] - result['direct_cost']
    result['final_profit'] = result['operating_profit'] - result['indirect_cost'] + amounts['adjustment']
    result['operating_margin'] = ratio(result['operating_profit'], result['net_revenue'])
    result['final_margin'] = ratio(result['final_profit'], result['net_revenue'])
    result['ad_roi'] = ratio(amounts['sales'], amounts['ad_spend'])
    return result
