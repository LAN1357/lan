import pytest

from src.engine.session_profit import (
    AMOUNT_FIELDS, allocate_cents, calculate_product_cost, calculate_session, money_to_cents,
)


def test_explicit_accounting_example():
    a = dict.fromkeys(AMOUNT_FIELDS, 0)
    a.update({k: v * 100 for k, v in dict(sales=9000, platform_fee=300,
        commission=900, slot_fee=500, ad_spend=1200, product_cost=4000,
        gift=100, insurance=30, logistics=100, loss=50, warehouse=100,
        labor=300, management=200, tax=150, adjustment=50).items()})
    r = calculate_session(a)
    assert (r['net_revenue'], r['operating_profit'], r['final_profit']) == (870000, 182000, 112000)


def test_precision_quantity_and_negative_profit():
    assert money_to_cents('0.10') + money_to_cents('0.20') == 30
    assert money_to_cents('1.005') == 101
    assert money_to_cents('-1.005') == -101
    assert calculate_product_cost('0.335', 3) == 101
    a = dict.fromkeys(AMOUNT_FIELDS, 0)
    a['product_cost'] = 500
    r = calculate_session(a)
    assert r['final_profit'] == -500
    assert r['final_margin'] is None and r['ad_roi'] is None


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-Infinity', '', 'abc', '1e999', '1e999999999', '9'*65, 0.1, True])
def test_invalid_money(value):
    with pytest.raises(ValueError):
        money_to_cents(value)


def test_allocation_conservation_and_ties():
    for total in range(-1001, 1002):
        result = allocate_cents(total, {'s1': 2, 's2': 3, 's3': 7})
        assert sum(result.values()) == total
    assert allocate_cents(1, {'s2': 1, 's1': 1}) == {'s1': 1, 's2': 0}
    assert allocate_cents(-1, {'s2': 1, 's1': 1}) == {'s1': -1, 's2': 0}


@pytest.mark.parametrize('total,weights', [(1, {}), (1, {'s': 0}), (1, {'s': -1}),
    (1, {'s': True}), (1.0, {'s': 1}), (True, {'s': 1})])
def test_invalid_allocation(total, weights):
    with pytest.raises(ValueError):
        allocate_cents(total, weights)


def test_missing_or_wrong_amount_types():
    with pytest.raises(ValueError):
        calculate_session({})
    for invalid in (None, 0.0, True):
        a = dict.fromkeys(AMOUNT_FIELDS, 0)
        a['sales'] = invalid
        with pytest.raises(ValueError):
            calculate_session(a)
