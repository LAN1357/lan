"""算账引擎指标测试."""
import pytest
from src.engine.metrics import compute_metrics, compute_period_summary, ROIMetrics, _safe_div


def make_order(sku="测试商品", gmv=1000.0, refund=100.0,
               platform_fee=50.0, commission=100.0,
               shipping=10.0, insurance=2.0):
    return dict(sku_name=sku, gmv=gmv, refund_amount=refund,
                platform_fee=platform_fee, commission=commission,
                shipping_fee=shipping, insurance=insurance)


def make_cost(sku="测试商品", cost_per_unit=300.0, gift_pct=0.02,
              warehouse=3.0, labor_pct=0.05, tax_rate=0.13):
    return dict(sku_name=sku, cost_per_unit=cost_per_unit,
                gift_cost_pct=gift_pct, warehouse_cost_per_order=warehouse,
                labor_pct=labor_pct, tax_rate=tax_rate)


class TestSafeDiv:
    def test_normal_division(self):
        assert _safe_div(10.0, 2.0) == 5.0

    def test_zero_denominator_returns_none(self):
        assert _safe_div(10.0, 0.0) is None

    def test_near_zero_denominator_returns_none(self):
        assert _safe_div(10.0, 1e-11) is None


class TestComputeMetrics:
    def test_normal_case(self):
        """正常盈亏场景：收入 > 成本，应有正净利润."""
        orders = [make_order()]
        ad_spends = [{"spend": 200.0}]
        cost_configs = {"测试商品": make_cost()}

        result = compute_metrics(
            orders=orders,
            ad_spends=ad_spends,
            cost_configs=cost_configs,
            period_type="day",
            period_value="2026-07-01",
        )

        # 验证核心计算链
        assert result.gmv == 1000.0
        assert result.refund_amount == 100.0
        assert result.net_gmv == 900.0
        assert result.platform_fee == 50.0
        assert result.commission == 100.0
        assert result.net_revenue == 750.0
        assert result.product_cost == 300.0
        assert result.ad_spend == 200.0
        assert result.shipping_cost == 12.0
        assert result.gift_cost == 20.0   # 1000 * 0.02
        assert result.warehouse_cost == 3.0
        assert result.labor_cost == 37.5  # 750 * 0.05

        # 税前毛利 = 750 - 300 - 200 - 12 - 20 - 3 - 37.5 = 177.5
        assert result.pre_tax_profit == pytest.approx(177.5)
        assert result.tax == pytest.approx(23.075)   # 177.5 * 0.13
        assert result.net_profit == pytest.approx(154.425)

    def test_empty_orders(self):
        """空订单列表应返回全零指标."""
        result = compute_metrics(orders=[], ad_spends=[], cost_configs={})
        assert result.gmv == 0.0
        assert result.net_revenue == 0.0
        assert result.net_profit == 0.0
        assert result.marketing_roi is None
        assert result.real_roi is None
        assert result.net_margin is None
        assert result.refund_rate is None
        assert result.sku_breakdown == []

    def test_zero_ad_spend_roi_is_none(self):
        """投放费用为 0 时 marketing_roi 应为 None."""
        orders = [make_order()]
        ad_spends = [{"spend": 0.0}]
        cost_configs = {"测试商品": make_cost()}

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends, cost_configs=cost_configs,
        )
        assert result.ad_spend == 0.0
        assert result.marketing_roi is None

    def test_default_cost_fallback(self):
        """SKU 无成本配置时应使用 default_cost 并触发警告."""
        orders = [make_order(sku="未知SKU")]
        ad_spends = [{"spend": 100.0}]
        cost_configs = {}  # 无任何配置
        default = make_cost(cost_per_unit=200.0, gift_pct=0.01)

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends,
            cost_configs=cost_configs, default_cost=default,
        )
        assert "未知SKU" in result.warnings[0]
        assert result.product_cost == 200.0
        assert result.gift_cost == 10.0  # 1000 * 0.01

    def test_pre_tax_loss_zero_tax(self):
        """税前亏损时税费应为 0."""
        orders = [make_order(gmv=100.0, refund=90.0,
                             platform_fee=5.0, commission=10.0)]
        # net_revenue = 100 - 90 - 5 - 10 = -5
        ad_spends = [{"spend": 100.0}]
        cost_configs = {"测试商品": make_cost(cost_per_unit=500.0)}

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends, cost_configs=cost_configs,
        )
        assert result.pre_tax_profit < 0
        assert result.tax == 0.0

    def test_ratio_fields_returned(self):
        """比率字段应正确计算."""
        orders = [make_order()]
        ad_spends = [{"spend": 200.0}]
        cost_configs = {"测试商品": make_cost()}

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends, cost_configs=cost_configs,
        )
        assert result.refund_rate == 0.1  # 100 / 1000
        assert result.ad_spend_ratio == pytest.approx(200.0 / 750.0)
        assert result.net_margin is not None
        assert result.real_roi is not None

    def test_sku_breakdown_included(self):
        """应包含单品拆解明细."""
        orders = [make_order(sku="SKU-A"), make_order(sku="SKU-B", gmv=500.0)]
        ad_spends = [{"spend": 100.0}]
        cost_configs = {
            "SKU-A": make_cost(sku="SKU-A"),
            "SKU-B": make_cost(sku="SKU-B", cost_per_unit=150.0),
        }

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends, cost_configs=cost_configs,
        )
        assert len(result.sku_breakdown) == 2
        sku_names = [s["sku_name"] for s in result.sku_breakdown]
        assert "SKU-A" in sku_names
        assert "SKU-B" in sku_names

    def test_to_dict_rounds_floats(self):
        """to_dict 应将浮点数四舍五入到 2 位小数."""
        result = compute_metrics(orders=[], ad_spends=[], cost_configs={})
        d = result.to_dict()
        assert d["gmv"] == 0.0
        assert d["marketing_roi"] is None
        assert isinstance(d["period_type"], str)

    def test_multiple_sku_weighted_average(self):
        """多 SKU 时 labor_pct 和 tax_rate 使用简单平均."""
        orders = [
            make_order(sku="A", gmv=1000.0),
            make_order(sku="B", gmv=500.0),
        ]
        ad_spends = [{"spend": 200.0}]
        cost_configs = {
            "A": make_cost(sku="A", labor_pct=0.05, tax_rate=0.13),
            "B": make_cost(sku="B", labor_pct=0.10, tax_rate=0.06),
        }

        result = compute_metrics(
            orders=orders, ad_spends=ad_spends, cost_configs=cost_configs,
        )
        # avg_labor_pct = (0.05 + 0.10) / 2 = 0.075
        assert result.labor_cost == pytest.approx(result.net_revenue * 0.075)
        # avg_tax_rate = (0.13 + 0.06) / 2 = 0.095
        expected_tax = max(0.0, result.pre_tax_profit * 0.095)
        assert result.tax == pytest.approx(expected_tax)


class TestComputePeriodSummary:
    def test_daily_summary(self):
        orders = [
            {"sku_name": "商品A", "gmv": 1000.0, "refund_amount": 0.0,
             "platform_fee": 50.0, "commission": 100.0,
             "shipping_fee": 10.0, "insurance": 2.0,
             "settle_date": "2026-07-01"},
            {"sku_name": "商品A", "gmv": 500.0, "refund_amount": 0.0,
             "platform_fee": 25.0, "commission": 50.0,
             "shipping_fee": 5.0, "insurance": 1.0,
             "settle_date": "2026-07-02"},
        ]
        ad_spends = [
            {"spend": 100.0, "date": "2026-07-01"},
            {"spend": 50.0, "date": "2026-07-02"},
        ]
        cost_configs = {"商品A": make_cost(sku="商品A")}

        results = compute_period_summary(
            orders=orders, ad_spends=ad_spends,
            cost_configs=cost_configs, period_type="day",
        )
        assert len(results) == 2
        assert results[0].period_value == "2026-07-01"
        assert results[1].period_value == "2026-07-02"
        assert results[0].gmv == 1000.0
        assert results[1].gmv == 500.0

    def test_weekly_summary(self):
        orders = [
            {"sku_name": "商品A", "gmv": 1000.0, "refund_amount": 0.0,
             "platform_fee": 0.0, "commission": 0.0,
             "shipping_fee": 0.0, "insurance": 0.0,
             "settle_date": "2026-07-06"},  # Monday of W28 2026
        ]
        ad_spends = []
        cost_configs = {"商品A": make_cost(sku="商品A")}

        results = compute_period_summary(
            orders=orders, ad_spends=ad_spends,
            cost_configs=cost_configs, period_type="week",
        )
        assert len(results) == 1
        # 2026-07-06 is a Monday, ISO week 28 of 2026
        assert results[0].period_value == "2026-W28"

    def test_monthly_summary(self):
        orders = [
            {"sku_name": "商品A", "gmv": 1000.0, "refund_amount": 0.0,
             "platform_fee": 0.0, "commission": 0.0,
             "shipping_fee": 0.0, "insurance": 0.0,
             "settle_date": "2026-07-15"},
        ]
        ad_spends = []
        cost_configs = {"商品A": make_cost(sku="商品A")}

        results = compute_period_summary(
            orders=orders, ad_spends=ad_spends,
            cost_configs=cost_configs, period_type="month",
        )
        assert len(results) == 1
        assert results[0].period_value == "2026-07"
